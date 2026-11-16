"""Host-found Reddit threads: the ``--reddit-threads`` lane.

A host with its own web search collects ``reddit.com/r/<sub>/comments/<id>``
URLs during its pre-research search and writes them to a ``.json`` file. The
engine reads only the thread identity from each entry and rehydrates every
thread itself (session thread JSON when a consented session is open, else
Arctic Shift), so a title, score or text the host wrote is never used.

Shape (``schema: "last30days-reddit-threads/1"``)::

    {
      "schema": "last30days-reddit-threads/1",
      "topic": "<the run topic>",
      "threads": [{"url": "https://www.reddit.com/r/<sub>/comments/<id>/..."}, {"id": "<base36>"}]
    }

Every other field, at the top level or in an entry, is ignored; an entry with
extra fields is counted once as ``extra-fields``. The reader is strict at the
envelope level (an unreadable, oversized, malformed or off-topic file fails
closed with an exit-2 contract error) and lenient per entry (a bad entry is
dropped with a named counter). At most ``MAX_THREADS`` entries are used.

Unlike the single-serve X envelope, the refs serve the whole run: the first
Reddit stream hydrates them once, the window and the topic bar drop what does
not belong, and each kept thread then goes to the planner subquery whose terms
its hydrated title matches best.

Security contract: contract errors name the path and the field, never a
field value, and nothing the host wrote reaches an outcome detail.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import hashlib
import json
import os
import re
import stat
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from . import env, log, reddit_discover, reddit_hydrate
from .reddit_refs import UNKNOWN, ThreadRef, canonical_thread

SCHEMA = "last30days-reddit-threads/1"
STDIN = "-"

# Input bounds. Named so the runbook and the receipts can quote them.
MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 1000
MAX_THREADS = 50
MAX_FIELD_CHARS = 2048

ENTRY_FIELDS = frozenset({"url", "id"})
# Counters, in receipt order. The first group is judged when the file is
# read; the last three after the engine hydrated the threads.
COUNTERS = (
    "non-reddit",
    "redirect",
    "share-link",
    "not-a-thread",
    "invalid",
    "duplicate",
    "truncated",
    "extra-fields",
    "unhydrated",
    "out-of-window",
    "off-topic",
)

_REMEDY = (
    "Rewrite the file to the last30days-reddit-threads/1 shape, or re-run "
    "without --reddit-threads."
)

# Directories the envelope must never be read from: the engine's own config
# dir (which holds .env) and the usual credential stores. Checked after
# realpath so a symlink cannot reach in.
_CREDENTIAL_STORES = (
    "~/.config/last30days",
    "~/.config/gh",
    "~/.aws",
    "~/.ssh",
    "~/.netrc",
    "~/.xurl",
    "~/.grok",
)

_REDDIT_SUFFIXES = ("reddit.com", "redd.it")
_REDIRECT_HOST = re.compile(
    r"(^|\.)(google\.[a-z.]+|vertexaisearch\.cloud\.google\.com|bing\.com|"
    r"duckduckgo\.com|search\.brave\.com|t\.co|out\.reddit\.com)$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_TOPIC_STRIP = "\"'“”‘’ \t"

SessionHydratorFactory = Callable[[], Optional[reddit_hydrate.SessionHydrator]]


class EnvelopeContractError(Exception):
    """The envelope failed its contract. The CLI maps this to exit code 2.
    The message names the path and the offending field only."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class Served:
    """One subquery's share of the host threads.

    ``arctic_state`` and ``note`` are set only on the call that hydrated the
    refs, so the lane records the archive's health and its detail note once.
    """

    items: list[dict]
    arctic_state: str = ""
    note: str = ""


@dataclass
class Envelope:
    """A validated ``--reddit-threads`` file, ready to serve the run."""

    path: str
    sha256: str
    topic: str
    refs: list[ThreadRef]
    counters: dict[str, int]
    total: int
    notes: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _subqueries: list[tuple[str, tuple[str, ...]]] = field(default_factory=list, repr=False)
    _kept: Optional[list[dict]] = field(default=None, repr=False)
    _routes: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    _served: set[str] = field(default_factory=set, repr=False)

    @property
    def accepted(self) -> int:
        return len(self.refs)

    def bind_subqueries(self, subqueries: Iterable[tuple[str, Iterable[str]]]) -> None:
        """Name the planner subqueries the Reddit stream runs for, with the
        query strings (search and ranking) each one is judged by."""
        with self._lock:
            self._subqueries = [
                (str(label), tuple(q for q in queries if q)) for label, queries in subqueries
            ]

    def serve(
        self,
        label: str,
        *,
        depth: str = "default",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        subreddits: Optional[list[str]] = None,
        dedicated: Optional[list[str]] = None,
        session_hydrator: SessionHydratorFactory = lambda: None,
        deadline: Optional[float] = None,
    ) -> Served:
        """The host threads routed to subquery ``label``.

        The first call hydrates every ref (through the session when the
        caller passes one, else Arctic Shift), drops threads outside the
        window or off the topic bar, and routes the rest (see ``_route``).
        Each label is served once; an unbound run serves everything to the
        first caller.
        Never raises.
        """
        with self._lock:
            served = Served(items=[])
            if self._kept is None:
                served.arctic_state, served.note = self._hydrate(
                    depth=depth, from_date=from_date, to_date=to_date,
                    subreddits=subreddits, dedicated=dedicated,
                    session_hydrator=session_hydrator, deadline=deadline,
                )
                self._route()
            key = label if self._subqueries else ""
            if key in self._served:
                return served
            self._served.add(key)
            served.items = copy.deepcopy(self._routes.get(key) or [])
            return served

    def receipt(self) -> str:
        dropped = ", ".join(
            f"{name} {count}" for name, count in self.counters.items() if count
        ) or "none"
        line = f"host Reddit threads: accepted {self.accepted} of {self.total} (dropped: {dropped})"
        if self._kept is not None:
            line += f"; kept {len(self._kept)} after hydration"
        if self.notes:
            line += "; notes: " + "; ".join(self.notes)
        return line

    # -- internals --------------------------------------------------------

    def _hydrate(
        self,
        *,
        depth: str,
        from_date: Optional[str],
        to_date: Optional[str],
        subreddits: Optional[list[str]],
        dedicated: Optional[list[str]],
        session_hydrator: SessionHydratorFactory,
        deadline: Optional[float],
    ) -> tuple[str, str]:
        self._kept = []
        if not self.refs:
            return "", ""
        try:
            hydrator = session_hydrator()
            extra = {"session_hydrator": hydrator} if hydrator is not None else {}
            # No window and no comments yet: the window is judged here so the
            # out-of-window count is honest, and comment slots go only to
            # threads that stay.
            hydrated = reddit_hydrate.hydrate(
                self.refs, topic=self.topic, depth=depth, comments=False,
                deadline=deadline, **extra,
            )
        except Exception as exc:  # the host lane must never break the Reddit stream
            _log(f"host thread hydration failed: {type(exc).__name__}")
            return "", ""
        in_window: list[dict] = []
        for item in hydrated.items:
            if item.get("engagement_source") == UNKNOWN and not item.get("title"):
                self.counters["unhydrated"] += 1
            elif not reddit_hydrate.in_window(item, from_date, to_date):
                self.counters["out-of-window"] += 1
            else:
                in_window.append(item)
        bar = reddit_discover.apply_bar(
            self.topic, in_window, subreddits=subreddits, dedicated=dedicated,
        )
        self.counters["off-topic"] += len(bar.dropped)
        kept = bar.kept
        arctic_state = hydrated.arctic_state
        if kept and arctic_state not in ("down", "throttled"):
            try:
                reddit_hydrate.attach_comments(kept, depth, deadline)
            except Exception as exc:  # comments are a bonus, never a failure
                _log(f"host thread comments failed: {type(exc).__name__}")
        for item in kept:
            item["topic_bar"] = True
        self._kept = kept
        _log(self.receipt())
        note = f"host kept {len(kept)} of {self.total}" if self.total else ""
        return arctic_state, note

    def _route(self) -> None:
        """Send each kept thread to the subquery whose terms its title matches best.

        A subquery's match is how many of its informative query terms the
        title contains, so a thread about one angle reaches that angle's
        stream rather than only the broad primary one. A tie, or a title
        that matches no terms, goes to the earliest such subquery, so every
        kept thread is served exactly once.
        """
        from .relevance import PreparedQuery, tokenize

        self._routes = {}
        kept = self._kept or []
        if not self._subqueries:
            self._routes[""] = list(kept)
            return
        prepared = [
            [PreparedQuery(q).informative_q_tokens for q in queries]
            for _, queries in self._subqueries
        ]
        for item in kept:
            title_tokens = tokenize(str(item.get("title") or ""))
            scores = [
                max((len(terms & title_tokens) for terms in terms_list), default=0)
                for terms_list in prepared
            ]
            best = scores.index(max(scores))
            self._routes.setdefault(self._subqueries[best][0], []).append(item)


# ---------------------------------------------------------------------------
# Lane context: the pipeline opens it around one Reddit stream call
# ---------------------------------------------------------------------------

_current: contextvars.ContextVar[Optional[tuple[Envelope, str]]] = contextvars.ContextVar(
    "last30days_reddit_envelope", default=None,
)


@contextlib.contextmanager
def lane(envelope: Optional[Envelope], label: str = ""):
    """Make ``envelope`` the Reddit lane's host threads for subquery ``label``."""
    token = _current.set((envelope, label) if envelope is not None else None)
    try:
        yield envelope
    finally:
        _current.reset(token)


def current() -> Optional[tuple[Envelope, str]]:
    return _current.get()


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _log(msg: str) -> None:
    log.source_log("reddit", msg, tty_only=False)


def _stat(path: Path) -> os.stat_result:
    """Seam for tests: the size and type check runs before any read."""
    return os.stat(path)


def _protected_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in (env.CONFIG_DIR, getattr(env, "CONFIG_FILE", None)):
        if candidate:
            roots.append(Path(candidate))
    for store in _CREDENTIAL_STORES:
        roots.append(Path(store).expanduser())
    return [Path(os.path.realpath(root)) for root in roots]


def _inside_protected(real: Path) -> bool:
    return any(real == root or root in real.parents for root in _protected_roots())


def _normalize_topic(value: Any) -> str:
    cleaned = _CONTROL_RE.sub("", str(value or ""))
    cleaned = cleaned.strip(_TOPIC_STRIP).casefold()
    return " ".join(cleaned.split())


def looks_inline_json(value: str) -> bool:
    """True when a ``--reddit-threads`` argument is JSON text rather than a path."""
    stripped = value.strip()
    return stripped.startswith(("{", "[")) or "\n" in value


def _rejection(text: str) -> str:
    """The counter for a value ``canonical_thread`` refused."""
    candidate = text if "://" in text else "https://" + text
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "invalid"
    if "/" not in text and "." not in text:
        return "invalid"
    if not host:
        return "invalid"
    if _REDIRECT_HOST.search(host):
        return "redirect"
    if not any(host == s or host.endswith("." + s) for s in _REDDIT_SUFFIXES):
        return "non-reddit"
    segments = [s for s in parts.path.split("/") if s]
    if "s" in segments[:3] and len(segments) >= 3 and segments[0].lower() == "r":
        return "share-link"
    return "not-a-thread"


def _entry_ref(entry: Any, counters: dict[str, int]) -> Optional[ThreadRef]:
    if not isinstance(entry, dict):
        counters["invalid"] += 1
        return None
    if set(entry) - ENTRY_FIELDS:
        counters["extra-fields"] += 1
    raw = entry.get("url")
    if not (isinstance(raw, str) and raw.strip()):
        raw = entry.get("id")
    if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_FIELD_CHARS:
        counters["invalid"] += 1
        return None
    text = _CONTROL_RE.sub("", raw).strip()
    ref = canonical_thread(text)
    if ref is None:
        counters[_rejection(text)] += 1
        return None
    ref.provenance = {"host"}
    return ref


def _read_bytes(raw_path: str, fail: Callable[[str], EnvelopeContractError]) -> bytes:
    if raw_path == STDIN:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise fail(f"exceeds the {MAX_BYTES // 1024} KiB limit")
        return data
    real = Path(os.path.realpath(raw_path))
    try:
        info = _stat(real)
    except OSError as exc:
        raise fail(f"cannot be read ({type(exc).__name__})") from None
    if not stat.S_ISREG(info.st_mode):
        raise fail("is not a regular file")
    if real.suffix.lower() != ".json":
        raise fail("must be a .json file")
    if _inside_protected(real):
        raise fail("is inside a configuration or credential directory")
    if info.st_size > MAX_BYTES:
        raise fail(f"exceeds the {MAX_BYTES // 1024} KiB limit")
    try:
        return real.read_bytes()
    except OSError as exc:
        raise fail(f"cannot be read ({type(exc).__name__})") from None


def read(path: str | os.PathLike[str], topic: str) -> Envelope:
    """Validate a ``--reddit-threads`` file (or ``-`` for stdin) for this run.

    ``topic`` is the run topic. Raises ``EnvelopeContractError`` (exit 2) for
    every fail-closed case; a bad entry is only counted.
    """
    raw_path = os.fspath(path)

    def fail(what: str) -> EnvelopeContractError:
        where = "on stdin" if raw_path == STDIN else raw_path
        return EnvelopeContractError(f"--reddit-threads file {where} {what}. {_REMEDY}")

    data = _read_bytes(raw_path, fail)
    digest = hashlib.sha256(data).hexdigest()
    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise fail("is not valid UTF-8") from None
    except (RecursionError, MemoryError):
        raise fail("is too deeply nested or too large to parse") from None
    except ValueError:
        raise fail("is not valid JSON") from None

    if not isinstance(payload, dict):
        raise fail("must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise fail('has a missing or unsupported "schema" field')
    if not _normalize_topic(topic) or _normalize_topic(payload.get("topic")) != _normalize_topic(topic):
        raise fail('has a "topic" field that does not match this run\'s topic')
    entries = payload.get("threads")
    if not isinstance(entries, list):
        raise fail('has a "threads" field that is not a list')
    if not entries:
        raise fail('has an empty "threads" list (omit --reddit-threads when no thread was found)')
    if len(entries) > MAX_ENTRIES:
        raise fail(f'has more than {MAX_ENTRIES} entries in "threads"')

    counters = {name: 0 for name in COUNTERS}
    notes: list[str] = []
    refs: list[ThreadRef] = []
    seen: set[str] = set()
    for entry in entries:
        ref = _entry_ref(entry, counters)
        if ref is None:
            continue
        if ref.post_id in seen:
            counters["duplicate"] += 1
            continue
        if len(refs) >= MAX_THREADS:
            counters["truncated"] += 1
            continue
        seen.add(ref.post_id)
        refs.append(ref)
    if counters["truncated"]:
        notes.append(f"only the first {MAX_THREADS} threads are used")

    envelope = Envelope(
        path=raw_path, sha256=digest, topic=topic, refs=refs,
        counters=counters, total=len(entries), notes=notes,
    )
    _log(envelope.receipt())
    return envelope
