"""The keyless Reddit web floor: thread refs from web search, and the topic bar.

:func:`web_refs` finds recent Reddit threads with no key and no consent. It
runs ``site:reddit.com <topic>`` through the keyless web ladder
(:mod:`web_search_keyless`: Brave HTML, then DuckDuckGo, then Startpage),
filtered to the run's recent period, and turns every thread link into a
canonical :class:`reddit_refs.ThreadRef`. Profile pages, wiki pages,
subreddit listings, share links and other hosts are not threads and are
dropped. Each ref keeps the result's title and date, which stand in only when
no hydrator can fill the thread.

An entity gets at most :data:`MAX_QUERIES_PER_ENTITY` web queries per run:
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
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Optional

from . import http, web_search_keyless
from .reddit_refs import ThreadRef, canonical_thread

MAX_QUERIES_PER_ENTITY = 2
RESULTS_PER_QUERY = 20

# Brave titles a Reddit hit "r/<sub> on Reddit: <title>"; DuckDuckGo and
# Startpage show "<title> : r/<sub>" with an optional " - Reddit".
_TITLE_PREFIX = re.compile(r"^r/[A-Za-z0-9_]+ on Reddit:\s*", re.IGNORECASE)
_TITLE_SUFFIX = re.compile(r"(?:\s*:\s*r/[A-Za-z0-9_]+)?(?:\s+-\s+Reddit)?\s*$", re.IGNORECASE)

SearchFn = Callable[..., tuple[list[dict], dict]]


@dataclass
class WebFloor:
    """What :func:`web_refs` found: refs, the queries behind them, and the
    state each web rung reached this run."""

    refs: list[ThreadRef] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    rungs: dict[str, str] = field(default_factory=dict)


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


# Per-run memo: topic key -> {query: event set once the answer is in}, and
# topic key -> {query: results}. A second caller asking a query in flight
# waits for the first one's answer instead of asking again.
_LOCK = threading.Lock()
_ASKED: dict[str, dict[str, threading.Event]] = {}
_ANSWERS: dict[str, dict[str, list[dict]]] = {}
_WAIT_SECONDS = 30.0


def reset_run_state() -> None:
    """Forget the queries this run asked (call between runs in one process)."""
    with _LOCK:
        _ASKED.clear()
        _ANSWERS.clear()


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
    topic_key: str, query: str, search: SearchFn, date_range: tuple[str, str],
    freshness: Optional[str],
) -> tuple[Optional[list[dict]], dict]:
    """One web query, memoized per run and capped per entity.

    Returns ``(None, {})`` when the entity's query budget is spent.
    """
    with _LOCK:
        asked = _ASKED.setdefault(topic_key, {})
        done = asked.get(query)
        owner = done is None
        if owner:
            if len(asked) >= MAX_QUERIES_PER_ENTITY:
                return None, {}
            done = asked[query] = threading.Event()
    if not owner:
        done.wait(_WAIT_SECONDS)
        with _LOCK:
            return list(_ANSWERS.get(topic_key, {}).get(query, [])), {}
    items: list[dict] = []
    artifact: dict = {}
    try:
        # The web rungs' failures are the floor's own health, not the Reddit
        # source's: keep them out of the stream's failure sink.
        with http.capture_failures():
            items, artifact = search(
                query, date_range, {}, count=RESULTS_PER_QUERY, freshness=freshness,
            )
    except Exception as exc:  # the floor must never break the lane
        _log(f"web search failed: {type(exc).__name__}: {exc}")
    finally:
        with _LOCK:
            _ANSWERS.setdefault(topic_key, {})[query] = list(items or [])
        done.set()
    return list(items or []), artifact or {}


def web_refs(
    topic: str,
    *,
    subreddits: Optional[list[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    search: Optional[SearchFn] = None,
) -> WebFloor:
    """Thread refs for ``topic`` from keyless web search. Never raises.

    ``subreddits`` are the plan's resolved subreddits, best first; the first
    one is the second query's hint. ``search`` replaces
    :func:`web_search_keyless.keyless_search` (tests).
    """
    floor = WebFloor()
    topic = " ".join(str(topic or "").split())
    if not topic:
        return floor
    search = search or web_search_keyless.keyless_search
    date_range = (from_date or "", to_date or _today())
    freshness = _freshness(from_date, to_date)
    by_id: dict[str, ThreadRef] = {}
    skipped_links = 0
    for query in _queries(topic, subreddits):
        items, artifact = _ask(topic.lower(), query, search, date_range, freshness)
        if items is None:
            continue
        floor.queries.append(query)
        for rung, state in (artifact.get("keyless_rungs") or {}).items():
            # A rung skipped on a later query keeps the state that took it out.
            if state != web_search_keyless.SKIPPED or rung not in floor.rungs:
                floor.rungs[rung] = state
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
    floor.refs = list(by_id.values())
    if floor.queries:
        rungs = ", ".join(f"{rung} {state}" for rung, state in floor.rungs.items()) or "no rung"
        _log(
            f"{len(floor.refs)} threads from {len(floor.queries)} web queries "
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
