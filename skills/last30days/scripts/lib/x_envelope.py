"""Host-fetched X envelope: the ``--x-posts`` lane.

The hosting model fetches posts through its own X connector and writes them
to a ``.json`` file of flat rows. The engine ingests that file as its X
source for one run: strict at the envelope level (a malformed, stale, or
off-topic file fails closed with an exit-2 contract error), lenient per row
(a bad row is dropped and counted), and trusting nothing a row asserts about
itself beyond a numeric id and a grammar-valid handle.

Shape (``schema: "last30days-x-posts/1"``)::

    {
      "schema": "last30days-x-posts/1",
      "generated_at": "<ISO-8601>",
      "topic": "<the run topic>",
      "window": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
      "provider": "<free text>",
      "status": "ok" | "partial" | "error",
      "error": "<short category: credits | not-connected | unavailable | window-unsupported>",
      "calls": [{"lane": "topic|from|mention|related", "handles": [...], "posts": [ROW, ...]}]
    }

A ROW is a flat object with exactly ``id``, ``author_handle``, ``created_at``,
``text``, ``likes``, ``reposts``, ``replies``, ``quotes``. Any other key is
ignored (and counted once per row as ``extra-fields``); in particular a
row-supplied ``url`` is never used for the citation, only cross-checked.

The envelope is single-serve: the first X subquery takes the topic-lane rows
and the handle-lane section takes the lane calls; later X subqueries,
judge-retry, and thin-retry get nothing (``take_topic`` / ``take_lanes``
return ``None`` once consumed).

Security contract: contract errors name the path and the field, never a
field value; the envelope's raw ``error`` text reaches stderr only under
``LAST30DAYS_DEBUG``; every outcome detail is one of the fixed strings below.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import env, health, log, schema
from .relevance import token_overlap_relevance as _compute_relevance
from .query import leading_mentions
from .x_api import _clean_handle, _decode_snowflake, _looks_generated, is_own_post

SCHEMA = "last30days-x-posts/1"
ID_PREFIX = "XHOST"

# Input bounds. Named so the recipe and the receipts can quote them.
MAX_BYTES = 8 * 1024 * 1024
MAX_CALLS = 20
MAX_ROWS_PER_CALL = 500
MAX_ROWS_TOTAL = 1000
MAX_TEXT_CHARS = 10_000
MAX_AGE_SECONDS = 6 * 60 * 60
MAX_ID_DIGITS = 20

LANES = ("topic", "from", "mention", "related")
STATUSES = ("ok", "partial", "error")
ROW_FIELDS = frozenset(
    {"id", "author_handle", "created_at", "text", "likes", "reposts", "replies", "quotes"}
)
COUNTERS = (
    "out-of-window",
    "missing-id-text",
    "uncitable",
    "date-mismatch",
    "handle-mismatch",
    "duplicate",
    "lane-mismatch",
    "truncated",
    "extra-fields",
)

# Engine-authored fixed outcome details. The envelope's own error text
# never becomes a detail string.
DETAIL_CREDITS = "X connector reported no credits"
DETAIL_NOT_CONNECTED = "X connector not connected"
DETAIL_UNAVAILABLE = "X connector unavailable"
DETAIL_ERROR = "X connector error"
DETAIL_GENERATED = "X connector rows rejected (id sequence looks generated)"
DETAIL_PARTIAL = "X connector returned partial results"
DETAIL_NOT_PASSED = "connector result not passed"

CATEGORY_CREDITS = "credits"
CATEGORY_NOT_CONNECTED = "not-connected"
CATEGORY_UNAVAILABLE = "unavailable"
CATEGORY_WINDOW_UNSUPPORTED = "window-unsupported"
_CATEGORY_GENERATED = "generated"

_REMEDY = (
    "Rewrite the file to the last30days-x-posts/1 shape, or re-run without --x-posts."
)

# Directories the envelope must never be read from: the engine's own config
# dir (which holds .env) and the credential stores of the X tooling and the
# usual shell neighbours. Checked after realpath so a symlink cannot reach in.
_CREDENTIAL_STORES = (
    "~/.xurl",
    "~/.grok",
    "~/.config/last30days",
    "~/.config/gh",
    "~/.aws",
    "~/.ssh",
    "~/.netrc",
)

# Snowflake ids predate nothing before X's epoch; a decode outside
# [2010-11-04, now + 1 day] is not a real post id.
_SNOWFLAKE_FLOOR = datetime(2010, 11, 4, tzinfo=timezone.utc)

# C0 and C1 control characters. Text keeps newlines and tabs; handles and the
# error field keep nothing.
_CONTROL_ANY_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_CONTROL_TEXT_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_PLACEHOLDER_HANDLES = frozenset(
    {"unknown", "n/a", "na", "none", "null", "user", "anonymous", "placeholder", "handle"}
)
_STATUS_URL_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/(?P<handle>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id>\d+)",
    re.IGNORECASE,
)
_TOPIC_STRIP = "\"'“”‘’ \t"


class EnvelopeContractError(Exception):
    """The envelope failed its contract: unreadable, out of bounds, malformed,
    stale, off-topic, or outside the research window. The CLI maps this to
    exit code 2. The message names the path and the offending field only."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass
class EnvelopeCall:
    """One validated handle-lane call (``from``, ``mention``, or ``related``)."""

    index: int
    lane: str
    handles: list[str]
    posts: list[dict[str, Any]]


@dataclass
class Envelope:
    """A validated ``--x-posts`` file, ready to be served once."""

    path: str
    sha256: str
    status: str
    error_category: str
    provider: str
    window: tuple[str, str]
    topic_items: list[dict[str, Any]]
    lane_calls: list[EnvelopeCall]
    counters: dict[str, int]
    accepted: int
    total: int
    lane_counts: dict[str, int]
    call_lanes: list[str]
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _topic_served: bool = field(default=False, repr=False, compare=False)
    _lanes_served: bool = field(default=False, repr=False, compare=False)

    def take_topic(self) -> list[dict[str, Any]] | None:
        """Topic-lane rows on the first call; ``None`` once consumed."""
        with self._lock:
            if self._topic_served:
                return None
            self._topic_served = True
            return list(self.topic_items)

    def take_lanes(self) -> list[EnvelopeCall] | None:
        """Handle-lane calls on the first call; ``None`` once consumed."""
        with self._lock:
            if self._lanes_served:
                return None
            self._lanes_served = True
            return list(self.lane_calls)

    def outcome(self) -> tuple[schema.RunOutcomeState, str] | None:
        """The fixed source outcome for a non-ok envelope, else ``None``."""
        if self.status == "error":
            return _error_outcome(self.error_category)
        if self.status == "partial":
            detail = DETAIL_PARTIAL
            if self.error_category:
                detail += f": {self.error_category}"
            if self.call_lanes:
                detail += f" (calls: {', '.join(self.call_lanes)})"
            return schema.PARTIAL, detail
        return None

    def receipt(self) -> str:
        dropped = ", ".join(
            f"{name} {count}" for name, count in self.counters.items() if count
        ) or "none"
        lanes = ", ".join(f"{lane} {self.lane_counts.get(lane, 0)}" for lane in LANES)
        line = (
            f"host-fetched X: accepted {self.accepted} of {self.total} "
            f"(dropped: {dropped}); lanes: {lanes}"
        )
        if self.status != "ok":
            line += f"; status {self.status}"
            if self.error_category:
                line += f" ({self.error_category})"
        if self.notes:
            line += "; notes: " + "; ".join(self.notes)
        return line


def _error_outcome(category: str) -> tuple[schema.RunOutcomeState, str]:
    if category == CATEGORY_CREDITS:
        return schema.PAYMENT_REQUIRED, DETAIL_CREDITS
    if category == CATEGORY_NOT_CONNECTED:
        return health.ERROR, DETAIL_NOT_CONNECTED
    if category == CATEGORY_UNAVAILABLE:
        return health.ERROR, DETAIL_UNAVAILABLE
    if category == _CATEGORY_GENERATED:
        return health.ERROR, DETAIL_GENERATED
    return health.ERROR, DETAIL_ERROR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stat(path: Path) -> os.stat_result:
    """Seam for tests: the size and type check runs before any read."""
    return os.stat(path)


def _log(msg: str) -> None:
    log.source_log("x", msg, tty_only=False)


def _protected_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in (env.CONFIG_DIR, getattr(env, "CONFIG_FILE", None)):
        if candidate:
            roots.append(Path(candidate))
    for store in _CREDENTIAL_STORES:
        roots.append(Path(store).expanduser())
    return [Path(os.path.realpath(root)) for root in roots]


def _inside_protected(real: Path) -> bool:
    for root in _protected_roots():
        if real == root or root in real.parents:
            return True
    return False


def _normalize_topic(value: str) -> str:
    cleaned = _CONTROL_ANY_RE.sub("", str(value or ""))
    cleaned = cleaned.strip(_TOPIC_STRIP).casefold()
    return " ".join(cleaned.split())


def _parse_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _parse_iso(value: str) -> datetime | None:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _category(raw: str) -> str:
    """The short error class: first token, lowercased, [a-z0-9-] only."""
    cleaned = _CONTROL_ANY_RE.sub("", raw or "").strip().lower()
    if not cleaned:
        return ""
    token = cleaned.split()[0]
    token = re.sub(r"[^a-z0-9-]", "", token)
    return token[:40]


def _clean_text(value: str) -> str:
    """Strip C0/C1 controls (newlines and tabs survive) and defang markup.

    Envelope text is model-written and post-authored, so the two Markdown
    constructs that stay active in the saved report are neutralised without
    hiding the characters: a link tail ``](`` becomes ``]\\(`` (renders as
    the same text, never as a link) and an HTML comment opener ``<!--``
    becomes ``<! --`` (never a comment, so a pasted META marker cannot be
    promoted). Raw tags are left to the HTML renderer's escaping.
    """
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_TEXT_RE.sub("", text)
    return text.replace("](", "]\\(").replace("<!--", "<! --")


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _post_id(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        return ""
    if not candidate.isdigit() or len(candidate) > MAX_ID_DIGITS:
        return ""
    return candidate


def _handle(value: Any) -> str:
    if not isinstance(value, str) or _CONTROL_ANY_RE.search(value):
        return ""
    handle = _clean_handle(value)
    if handle.lower() in _PLACEHOLDER_HANDLES:
        return ""
    return handle


def _clean_handles(value: Any) -> list[str] | None:
    """Grammar-valid, lowercased handles; ``None`` when any entry fails."""
    if not isinstance(value, list):
        return None
    cleaned: list[str] = []
    for raw in value:
        handle = _handle(raw)
        if not handle:
            return None
        if handle.lower() not in cleaned:
            cleaned.append(handle.lower())
    return cleaned


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


# Clock skew a host may legitimately show; anything further ahead is a
# stamp chosen to outlive the six-hour freshness gate.
FUTURE_SKEW_SECONDS = 5 * 60


def _generated_in_future(value: Any) -> bool:
    """True when ``generated_at`` is more than ``FUTURE_SKEW_SECONDS`` ahead of now."""
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    ahead = stamp.astimezone(timezone.utc) - datetime.now(timezone.utc)
    return ahead.total_seconds() > FUTURE_SKEW_SECONDS


def read(
    path: str | os.PathLike[str],
    window: tuple[str, str],
    topic: str,
    handles: list[str] | None = None,
    related: list[str] | None = None,
) -> Envelope:
    """Validate and ingest a ``--x-posts`` file for this run.

    ``window`` is the engine's ``(from_date, to_date)``; ``topic`` is the run
    topic (or comparison entity); ``handles`` are the run's ``--x-handle``
    handles and ``related`` its ``--x-related`` handles, which together bound
    what a call's ``handles`` may claim.

    Raises ``EnvelopeContractError`` (exit 2) for every fail-closed case.
    """
    raw_path = os.fspath(path)

    def fail(what: str) -> EnvelopeContractError:
        return EnvelopeContractError(f"--x-posts file {raw_path} {what}. {_REMEDY}")

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
        raise fail(f"exceeds the {MAX_BYTES // (1024 * 1024)} MiB limit")

    try:
        data = real.read_bytes()
    except OSError as exc:
        raise fail(f"cannot be read ({type(exc).__name__})") from None
    digest = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise fail("is not valid UTF-8") from None
    try:
        payload = json.loads(text)
    except (RecursionError, MemoryError):
        raise fail("is too deeply nested or too large to parse") from None
    except ValueError:
        raise fail("is not valid JSON") from None

    if not isinstance(payload, dict):
        raise fail("must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise fail('has a missing or unsupported "schema" field')
    status = payload.get("status")
    if status not in STATUSES:
        raise fail('has a "status" field that is not ok, partial, or error')
    if not env.is_timestamp_fresh(payload.get("generated_at"), MAX_AGE_SECONDS):
        raise fail(
            'has a "generated_at" field that is missing, malformed, or older than '
            f"{MAX_AGE_SECONDS // 3600} hours"
        )
    if _generated_in_future(payload.get("generated_at")):
        raise fail('has a "generated_at" field in the future')
    if _normalize_topic(payload.get("topic")) != _normalize_topic(topic) or not _normalize_topic(topic):
        raise fail('has a "topic" field that does not match this run\'s topic')

    from_date, to_date = window
    window_raw = payload.get("window")
    if not isinstance(window_raw, dict):
        raise fail('has a missing or malformed "window" field')
    window_from = _parse_day(window_raw.get("from"))
    window_to = _parse_day(window_raw.get("to"))
    if window_from is None or window_to is None:
        raise fail('has a "window" field without valid from/to dates')
    engine_from = _parse_day(from_date) or date.min
    engine_to = _parse_day(to_date) or date.max
    if window_to < engine_from:
        raise fail('has a "window" that ends before the research window starts')
    warnings: list[str] = []
    if window_from > engine_from or window_to < engine_to:
        warnings.append(
            f"host-fetched X window {window_from.isoformat()}..{window_to.isoformat()} "
            f"is narrower than the research window {from_date}..{to_date}; "
            "the research window is authoritative"
        )

    provider = _CONTROL_ANY_RE.sub("", str(payload.get("provider") or ""))[:64].strip()

    error_raw = payload.get("error")
    if error_raw is not None and not isinstance(error_raw, str):
        raise fail('has an "error" field that is not a string')
    if error_raw and ("\n" in error_raw or "\r" in error_raw):
        raise fail('has an "error" field that is not a single line')
    error_category = _category(error_raw or "")
    if error_raw and log.is_debug():
        _log(f"envelope error text (debug only): {_CONTROL_ANY_RE.sub('', error_raw)[:500]}")

    calls_raw = payload.get("calls")
    if not isinstance(calls_raw, list):
        raise fail('has a "calls" field that is not a list')
    if len(calls_raw) > MAX_CALLS:
        raise fail(f'has more than {MAX_CALLS} entries in "calls"')
    if not calls_raw and status == "ok":
        raise fail('has an empty "calls" list with status ok')

    allowed = {h.lower() for h in (_clean_handles(list(handles or [])) or [])}
    related_set = {h.lower() for h in (_clean_handles(list(related or [])) or [])}
    allowed |= related_set

    counters: dict[str, int] = {name: 0 for name in COUNTERS}
    lane_counts: dict[str, int] = {lane: 0 for lane in LANES}
    notes: list[str] = []
    call_lanes: list[str] = []
    topic_items: list[dict[str, Any]] = []
    lane_calls: list[EnvelopeCall] = []
    seen_ids: set[str] = set()
    kept_ids: list[str] = []
    total = 0
    now_plus = datetime.now(timezone.utc) + timedelta(days=1)

    for index, call in enumerate(calls_raw, start=1):
        if not isinstance(call, dict):
            raise fail(f"has a call {index} that is not an object")
        posts = call.get("posts")
        if not isinstance(posts, list) or any(not isinstance(row, dict) for row in posts):
            raise fail(f'has a call {index} whose "posts" is not a list of objects')
        if len(posts) > MAX_ROWS_PER_CALL:
            raise fail(f"has a call {index} with more than {MAX_ROWS_PER_CALL} rows")
        total += len(posts)
        if total > MAX_ROWS_TOTAL:
            raise fail(f"has more than {MAX_ROWS_TOTAL} rows in total")

        lane_raw = call.get("lane")
        lane = lane_raw.strip().lower() if isinstance(lane_raw, str) else ""
        call_handles = _clean_handles(call.get("handles"))
        if lane not in LANES:
            if lane_raw is not None:
                counters["lane-mismatch"] += 1
                notes.append(f"call {index}: unknown lane served as topic")
            lane = "topic"
            call_handles = []
        elif lane != "topic":
            valid = bool(call_handles) and set(call_handles) <= allowed
            if valid and lane == "related":
                valid = set(call_handles) <= related_set
            if not valid:
                counters["lane-mismatch"] += 1
                notes.append(
                    f"call {index}: {lane} lane served as topic "
                    "(handles are not in --x-handle/--x-related)"
                )
                lane = "topic"
                call_handles = []
        else:
            call_handles = []
        call_lanes.append(lane)

        kept: list[dict[str, Any]] = []
        for row in posts:
            item = _ingest_row(
                row, topic=topic, from_date=from_date, to_date=to_date,
                now_plus=now_plus, seen_ids=seen_ids, counters=counters,
            )
            if item is None:
                continue
            author = item["author_handle"].lower()
            if lane == "from" or lane == "related":
                if author not in call_handles:
                    counters["lane-mismatch"] += 1
                    seen_ids.discard(item["post_id"])
                    continue
            elif lane == "mention":
                if author in call_handles or any(
                    is_own_post(item["url"], handle) for handle in call_handles
                ):
                    counters["lane-mismatch"] += 1
                    seen_ids.discard(item["post_id"])
                    continue
            kept.append(item)
            kept_ids.append(item["post_id"])

        lane_counts[lane] += len(kept)
        if lane == "topic":
            topic_items.extend(kept)
        else:
            lane_calls.append(EnvelopeCall(index=index, lane=lane, handles=call_handles, posts=kept))

    accepted = len(kept_ids)
    if status == "error":
        # An error envelope carries no evidence: the outcome is the story.
        topic_items, lane_calls = [], []
        lane_counts = {lane: 0 for lane in LANES}
        accepted = 0
    elif kept_ids and _looks_generated(kept_ids):
        status = "error"
        error_category = _CATEGORY_GENERATED
        topic_items, lane_calls = [], []
        lane_counts = {lane: 0 for lane in LANES}
        accepted = 0
        notes.append("id sequence looks generated; envelope rejected")

    for position, item in enumerate(
        [*topic_items, *(post for call in lane_calls for post in call.posts)], start=1
    ):
        item["id"] = f"{ID_PREFIX}{position}"

    envelope = Envelope(
        path=raw_path,
        sha256=digest,
        status=status,
        error_category=error_category,
        provider=provider,
        window=(window_from.isoformat(), window_to.isoformat()),
        topic_items=topic_items,
        lane_calls=lane_calls,
        counters=counters,
        accepted=accepted,
        total=total,
        lane_counts=lane_counts,
        call_lanes=call_lanes,
        warnings=warnings,
        notes=notes,
    )
    _log(envelope.receipt())
    for warning in warnings:
        _log(warning)
    return envelope


def _ingest_row(
    row: dict[str, Any],
    *,
    topic: str,
    from_date: str,
    to_date: str,
    now_plus: datetime,
    seen_ids: set[str],
    counters: dict[str, int],
) -> dict[str, Any] | None:
    """Rebuild one flat row into the engine's X item shape, or drop it.

    Returns ``None`` after incrementing the matching counter. The citation
    URL is always engine-built from the id and the validated handle.
    """
    if set(row) - ROW_FIELDS:
        counters["extra-fields"] += 1

    post_id = _post_id(row.get("id"))
    text_raw = row.get("text")
    text = _clean_text(text_raw) if isinstance(text_raw, str) else ""
    if not post_id or not text.strip():
        counters["missing-id-text"] += 1
        return None
    text = text.strip()
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        counters["truncated"] += 1

    decoded = _decode_snowflake(post_id)
    if decoded is None or decoded < _SNOWFLAKE_FLOOR or decoded > now_plus:
        counters["out-of-window"] += 1
        return None
    snowflake_day = decoded.date()

    created_raw = row.get("created_at")
    if isinstance(created_raw, str) and created_raw.strip():
        created = _parse_iso(created_raw)
        if created is not None and abs((created.date() - snowflake_day).days) > 1:
            counters["date-mismatch"] += 1
            return None

    day = snowflake_day.isoformat()
    if (from_date and day < from_date) or (to_date and day > to_date):
        counters["out-of-window"] += 1
        return None

    if post_id in seen_ids:
        counters["duplicate"] += 1
        return None
    seen_ids.add(post_id)

    handle = _handle(row.get("author_handle"))
    url_raw = row.get("url")
    if isinstance(url_raw, str) and url_raw.strip():
        match = _STATUS_URL_RE.match(url_raw.strip())
        url_handle = match.group("handle").lower() if match else ""
        consistent = bool(match) and match.group("id") == post_id and (
            url_handle == "i" or not handle or url_handle == handle.lower()
        )
        if not consistent:
            counters["handle-mismatch"] += 1
            handle = ""

    if handle:
        url = f"https://x.com/{handle}/status/{post_id}"
    else:
        counters["uncitable"] += 1
        url = f"https://x.com/i/status/{post_id}"

    return {
        "id": "",
        "text": text,
        "url": url,
        "author_handle": handle,
        "date": day,
        "engagement": {
            "likes": _int(row.get("likes")),
            "reposts": _int(row.get("reposts")),
            "replies": _int(row.get("replies")),
            "quotes": _int(row.get("quotes")),
        },
        "mentioned_handles": leading_mentions(text),
        "why_relevant": "",
        "relevance": _compute_relevance(topic, text) if topic else 0.5,
        "post_id": post_id,
    }
