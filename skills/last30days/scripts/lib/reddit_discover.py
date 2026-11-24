"""The keyless Reddit web floor: thread refs from web search, and the topic bar.

:func:`web_refs` finds recent Reddit threads with no key and no consent. It
runs ``site:reddit.com <topic>`` through the keyless web ladder
(:mod:`web_search_keyless`: Brave HTML, then DuckDuckGo, then Startpage),
filtered to the run's recent period, and turns every thread link into a
canonical :class:`reddit_refs.ThreadRef`. Profile pages, wiki pages,
subreddit listings, share links and other hosts are not threads and are
dropped. Each ref keeps the result's title and date, which stand in only when
no hydrator can fill the thread.

An entity gets at most :data:`MAX_QUERIES_PER_ENTITY` distinct web queries per run:
the topic, and the topic with the top resolved subreddit as a hint. Repeat
calls for the same topic (one per subquery, or per comparison entity) reuse
the answers already fetched instead of asking again.

:func:`apply_bar` is the topic bar for threads that web search, a host, or a
listing found: a thread stays when its title names the topic, or when it sits
in a subreddit the plan resolved or the user named as dedicated and its body
names the topic. Never raises.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import partial
from typing import Any, Callable, Iterable, Mapping, Optional

from . import env, health, http, web_search_keyless
from .reddit_refs import ThreadRef, canonical_thread

MAX_QUERIES_PER_ENTITY = 2
RESULTS_PER_QUERY = 20

# Brave titles a Reddit hit "r/<sub> on Reddit: <title>"; DuckDuckGo and
# Startpage show "<title> : r/<sub>" with an optional " - Reddit".
_TITLE_PREFIX = re.compile(r"^r/[A-Za-z0-9_]+ on Reddit:\s*", re.IGNORECASE)
_TITLE_SUFFIX = re.compile(r"(?:\s*:\s*r/[A-Za-z0-9_]+)?(?:\s+-\s+Reddit)?\s*$", re.IGNORECASE)

SearchFn = Callable[..., tuple[list[dict], dict]]


# The only SearXNG states that become a note. Anything else is ignored.
_SEARXNG_FAILURE_STATES = (
    web_search_keyless.BLOCKED, web_search_keyless.THROTTLED,
    web_search_keyless.ERROR, health.TIMEOUT,
)


@dataclass
class WebFloor:
    """What :func:`web_refs` found: refs, the queries behind them, and the
    state each web rung reached this run."""

    refs: list[ThreadRef] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    rungs: dict[str, str] = field(default_factory=dict)
    timed_out: bool = False
    # Failures the optional SearXNG fallback showed on any query, in order, once each. The
    # rung map keeps only its latest state, so a later clean query would otherwise hide them.
    searxng_failures: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False, compare=False,
    )

    def record_query(
        self,
        query: str,
        rungs: Optional[dict[str, str]] = None,
        refs: Optional[Iterable[ThreadRef]] = None,
        *,
        timed_out: bool = False,
        searxng: Optional[str] = None,
    ) -> None:
        """Atomically record a completed web query, its rung states, and refs."""
        with self._lock:
            self.queries.append(query)
            self.timed_out = self.timed_out or timed_out
            if searxng in _SEARXNG_FAILURE_STATES and searxng not in self.searxng_failures:
                self.searxng_failures.append(searxng)
            for rung, state in (rungs or {}).items():
                # A rung skipped on a later query keeps the state that took it out.
                if state != web_search_keyless.SKIPPED or rung not in self.rungs:
                    self.rungs[rung] = state
            if refs is not None:
                self.refs = list(refs)

    def mark_timed_out(self) -> None:
        with self._lock:
            self.timed_out = True

    def record_rung(self, rung: str, state: str) -> None:
        """Record one rung state under the floor lock."""
        with self._lock:
            if state != web_search_keyless.SKIPPED or rung not in self.rungs:
                self.rungs[rung] = state

    def add_refs(self, refs: Iterable[ThreadRef]) -> None:
        """Merge thread refs under the floor lock, preserving first-seen order."""
        with self._lock:
            seen = {ref.post_id for ref in self.refs}
            for ref in refs:
                if ref.post_id not in seen:
                    seen.add(ref.post_id)
                    self.refs.append(ref)

    def snapshot(self) -> WebFloor:
        """Return a detached, thread-safe copy of this floor's current state."""
        with self._lock:
            return WebFloor(
                refs=list(self.refs),
                queries=list(self.queries),
                rungs=dict(self.rungs),
                timed_out=self.timed_out,
                searxng_failures=list(self.searxng_failures),
            )


@dataclass
class BarResult:
    kept: list[dict]
    dropped: list[dict]

    def note(self, label: str) -> Optional[str]:
        """``"N of M threads dropped for not naming the topic (<label>)"``, or None."""
        if not self.dropped:
            return None
        total = len(self.kept) + len(self.dropped)
        return f"{len(self.dropped)} of {total} threads dropped for not naming the topic ({label})"


@dataclass
class _QueryAttempt:
    done: threading.Event = field(default_factory=threading.Event)
    items: list[dict] = field(default_factory=list)
    timed_out: bool = False


# Per-run memo: topic key -> {query: attempt or None for a retryable miss}.
# Waiters keep the attempt they joined, even if the slot becomes retryable.
# Direct callers that omit ``config`` read the approved SearXNG URL once per run.
_LOCK = threading.Lock()
# Held while the standalone config is read. Each run gets its own, so a read that is still
# blocked when a run is reset can neither stall the next run nor seed it.
_CONFIG_LOAD_LOCK = threading.Lock()
_ASKED: dict[str, dict[str, Optional[_QueryAttempt]]] = {}
_SEARXNG_URL: Optional[str] = None
_RUN_GENERATION = 0
_WAIT_SECONDS = 30.0


def reset_run_state() -> None:
    """Forget the queries and SearXNG setting this run asked (call between runs in one process)."""
    global _SEARXNG_URL, _RUN_GENERATION, _CONFIG_LOAD_LOCK
    with _LOCK:
        _ASKED.clear()
        _SEARXNG_URL = None
        _CONFIG_LOAD_LOCK = threading.Lock()
        _RUN_GENERATION += 1


def _normalize_searxng_url(config: Optional[Mapping[str, Any]]) -> str:
    """Extract trimmed LAST30DAYS_SEARXNG_URL or '' when absent/unsubstituted."""
    if not isinstance(config, Mapping):
        return ""
    raw = config.get("LAST30DAYS_SEARXNG_URL")
    if not isinstance(raw, str) or env.is_unsubstituted_template(raw):
        return ""
    return raw.strip()


def _standalone_searxng_url(generation: int, deadline: Optional[float] = None) -> Optional[str]:
    """Load the approved URL. The answer is '' if absent and None if unavailable.

    ``deadline`` (``time.monotonic()``) bounds the wait for another caller's load of the same
    run. Out of time the answer is None, exactly as for a config that could not be read, and
    nothing is cached, so the caller's query stays retryable.
    """
    global _SEARXNG_URL
    with _LOCK:
        if generation != _RUN_GENERATION:
            return None
        if _SEARXNG_URL is not None:
            return _SEARXNG_URL
        load_lock = _CONFIG_LOAD_LOCK
    wait = -1 if deadline is None else max(0.0, deadline - time.monotonic())
    if not load_lock.acquire(timeout=wait):
        _log("config load wait ran out")
        return None
    try:
        with _LOCK:
            if generation != _RUN_GENERATION:
                return None
            if _SEARXNG_URL is not None:
                return _SEARXNG_URL
        try:
            url = (env.resolve_searxng_url() or "").strip()
        except Exception as exc:
            _log(f"config load failed: {type(exc).__name__}")
            return None
        with _LOCK:
            if generation != _RUN_GENERATION:
                return None
            _SEARXNG_URL = url
            return url
    finally:
        load_lock.release()


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditWeb] {msg}\n")
    sys.stderr.flush()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _freshness(from_date: Optional[str], to_date: Optional[str]) -> Optional[str]:
    """The search engines' recency filter that covers the window, or None.

    A window that ends before today cannot be expressed as "past month", so
    it runs unfiltered and the hydrated post dates do the windowing.
    """
    if not from_date or not to_date or to_date < _today():
        return None
    try:
        days = (date.fromisoformat(_today()) - date.fromisoformat(from_date)).days
    except ValueError:
        return None
    if days <= 7:
        return "week"
    if days <= 31:
        return "month"
    if days <= 366:
        return "year"
    return None


def _queries(topic: str, subreddits: Optional[list[str]]) -> list[str]:
    queries = [f"site:reddit.com {topic}"]
    hint = next((s.strip().removeprefix("r/") for s in subreddits or [] if s and s.strip()), "")
    if hint:
        queries.append(f"site:reddit.com r/{hint} {topic}")
    return queries[:MAX_QUERIES_PER_ENTITY]


def _clean_title(title: str) -> str:
    return _TITLE_SUFFIX.sub("", _TITLE_PREFIX.sub("", (title or "").strip())).strip()


def _ask(
    generation: int, topic_key: str, query: str, search: SearchFn, date_range: tuple[str, str],
    freshness: Optional[str], config: Optional[Mapping[str, Any]] = None,
    deadline: Optional[float] = None,
) -> tuple[Optional[list[dict]], dict, bool]:
    """One web query, memoized per run and capped per entity.

    An absent item list means the entity's query slots were spent or the run ended.
    """
    with _LOCK:
        if generation != _RUN_GENERATION:
            return None, {}, False
        asked = _ASKED.setdefault(topic_key, {})
        attempt = asked.get(query)
        owner = attempt is None
        if owner:
            if query not in asked and len(asked) >= MAX_QUERIES_PER_ENTITY:
                return None, {}, False
            attempt = asked[query] = _QueryAttempt()
    if not owner:
        wait = _WAIT_SECONDS
        if deadline is not None:
            wait = min(wait, max(0.0, deadline - time.monotonic()))
        completed = attempt.done.wait(wait)
        with _LOCK:
            if generation != _RUN_GENERATION:
                return None, {}, False
            if not completed:
                return [], {}, True
            return list(attempt.items), {}, attempt.timed_out
    items: list[dict] = []
    artifact: dict = {}
    url: Optional[str] = ""
    try:
        url = (
            _standalone_searxng_url(generation, deadline)
            if config is None else _normalize_searxng_url(config)
        )
        with _LOCK:
            if generation != _RUN_GENERATION:
                return None, {}, False
        search_config = {"LAST30DAYS_SEARXNG_URL": url} if url else {}
        extra = {"deadline_monotonic": deadline} if deadline is not None else {}
        # The web rungs' failures are the floor's own health, not the Reddit
        # source's: keep them out of the stream's failure sink.
        with http.capture_failures():
            items, artifact = search(
                query, date_range, search_config,
                count=RESULTS_PER_QUERY, freshness=freshness, **extra,
            )
    except Exception as exc:  # the floor must never break the lane
        _log(f"web search failed: {type(exc).__name__}: {exc}")
    finally:
        timed_out = ((artifact or {}).get("_source_outcome") or {}).get("state") == health.TIMEOUT
        with _LOCK:
            if generation == _RUN_GENERATION:
                attempt.items = list(items or [])
                attempt.timed_out = timed_out
                if not items and (url is None or timed_out):
                    # Let this query try again without freeing its slot for a new hint.
                    asked[query] = None
            attempt.done.set()
    return list(items or []), artifact or {}, timed_out


def web_refs(
    topic: str,
    *,
    subreddits: Optional[list[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    search: Optional[SearchFn] = None,
    deadline: Optional[float] = None,
    floor: Optional[WebFloor] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> WebFloor:
    """Thread refs for ``topic`` from keyless web search. Never raises.

    ``subreddits`` are the plan's resolved subreddits, best first; the first
    one is the second query's hint. ``search`` replaces
    :func:`web_search_keyless.keyless_search` (tests). No query starts after
    ``deadline`` (``time.monotonic()``). ``floor`` is filled in place after
    each query, so a caller that stops waiting keeps the threads found so far.
    """
    with _LOCK:
        generation = _RUN_GENERATION
    floor = floor if floor is not None else WebFloor()
    topic = " ".join(str(topic or "").split())
    if not topic:
        return floor
    search = search or partial(
        web_search_keyless.keyless_search,
        accept_result=lambda item: canonical_thread(str(item.get("url") or "")) is not None,
    )
    date_range = (from_date or "", to_date or _today())
    freshness = _freshness(from_date, to_date)
    by_id: dict[str, ThreadRef] = {}
    skipped_links = 0
    for query in _queries(topic, subreddits):
        if deadline is not None and time.monotonic() >= deadline:
            floor.mark_timed_out()
            _log("web floor stopped at its deadline; later queries skipped")
            break
        items, artifact, timed_out = _ask(
            generation, topic.lower(), query, search, date_range, freshness,
            config=config, deadline=deadline,
        )
        if items is None:
            continue
        for item in items:
            ref = canonical_thread(str(item.get("url") or ""))
            if ref is None:
                skipped_links += 1
                continue
            if ref.post_id in by_id:
                continue
            ref.provenance.add("web")
            ref.title = _clean_title(str(item.get("title") or ""))
            ref.date = item.get("date") or None
            by_id[ref.post_id] = ref
        query_rungs = dict(artifact.get("keyless_rungs") or {})
        if artifact.get("keyless_searxng"):
            query_rungs["searxng"] = artifact["keyless_searxng"]
        floor.record_query(
            query, query_rungs, by_id.values(), timed_out=timed_out,
            searxng=artifact.get("keyless_searxng"),
        )
    snap = floor.snapshot()
    if snap.queries:
        rungs = ", ".join(f"{rung} {state}" for rung, state in snap.rungs.items()) or "no rung"
        _log(
            f"{len(snap.refs)} threads from {len(snap.queries)} web queries "
            f"({rungs}; {skipped_links} non-thread links skipped)"
        )
    return floor


def names_topic(text: str, topic: str) -> bool:
    """Whether ``text`` names ``topic``: the entity check rerank uses, or token
    overlap for a topic with no usable entity."""
    from . import relevance, rerank

    entity = rerank._primary_entity(topic).lower()
    if entity:
        return rerank._entity_grounded(text or "", entity)
    return relevance.token_overlap_relevance(topic, text or "") >= relevance.RELEVANCE_FLOOR


def _sub_key(name: Any) -> str:
    return str(name or "").strip().removeprefix("r/").lower()


def apply_bar(
    topic: str,
    items: Iterable[dict],
    *,
    subreddits: Optional[Iterable[str]] = None,
    dedicated: Optional[Iterable[str]] = None,
) -> BarResult:
    """Split threads into those that name ``topic`` and those that do not.

    A title that names the topic passes. A thread in one of ``subreddits``
    (resolved by the plan) or ``dedicated`` (named by the user) also passes
    when its body names the topic. Order is kept.
    """
    items = [item for item in items or [] if isinstance(item, dict)]
    if not topic:
        return BarResult(kept=items, dropped=[])
    home = {_sub_key(s) for s in [*(subreddits or []), *(dedicated or [])]} - {""}
    result = BarResult(kept=[], dropped=[])
    for item in items:
        passed = names_topic(str(item.get("title") or ""), topic) or (
            _sub_key(item.get("subreddit")) in home
            and names_topic(str(item.get("selftext") or ""), topic)
        )
        (result.kept if passed else result.dropped).append(item)
    return result
