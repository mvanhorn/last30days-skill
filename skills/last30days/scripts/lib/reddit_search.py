"""Keyless Reddit discovery via Reddit's own site search fragment.

``/svc/shreddit/search/?q=...&type=posts`` is the server-rendered HTML the
reddit.com search page loads. It serves HTTP 200 with no API key, and
``/svc/shreddit/r/{sub}/search/`` restricts the same search to one subreddit.
Each post result unit carries:

- a ``<search-telemetry-tracker>`` whose JSON tracking context has
  ``action_info.type == "post"``, the post id, title, and subreddit,
- the post-title link (the canonical permalink),
- a ``<faceplate-timeago ts=...>`` post date,
- a counter row of ``<faceplate-number>`` votes and comments.

So every discovered post arrives dated and scored, with no extra listing fetch.
The next page is a lazy ``<faceplate-partial>`` whose ``src`` carries a
``cursor``. Output dicts match ``reddit_listing.parse_cards`` so downstream
code is unaffected.
"""

import copy
import html as _html
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from . import http, reddit_listing
from .relevance import token_overlap_relevance

BASE = "https://www.reddit.com"

# Post caps per run by depth.
DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}
# Global search pages per depth (about 7 posts per page).
GLOBAL_PAGE_CAPS = {"quick": 2, "default": 4, "deep": 8}
# Targeted subreddits get one page each.
SUB_PAGE_CAP = 1
MAX_WORKERS = 4
SEARCH_TIMEOUT = 15

# A recognizable page has post result units or Reddit's explicit no-results
# unit. Anything else (challenge page, changed markup) is a lane failure.
RESULTS_MARKER = 'view-events="search/view/post"'
NO_RESULTS_MARKER = 'view-events="search/view/no_results"'

_TRACKER = re.compile(
    r'<search-telemetry-tracker\b[^>]*?\bdata-faceplate-tracking-context="([^"]*)"'
)
_TITLE_HREF = re.compile(
    r'<a\b[^>]*?data-testid="post-title"[^>]*?\bhref="([^"]*)"'
    r'|<a\b[^>]*?\bhref="([^"]*)"[^>]*?data-testid="post-title"'
)
_TIMEAGO = re.compile(r'<faceplate-timeago\b[^>]*?\bts="([^"]+)"')
_COUNTER = re.compile(
    r'<faceplate-number\b[^>]*?\bnumber="(\d+)"[^>]*>\s*</faceplate-number>\s*(vote|comment)'
)
_NEXT_PAGE = re.compile(
    r'<faceplate-partial\b[^>]*?\bsrc="([^"]*/svc/shreddit/[^"]*search/[^"]*)"'
)


# Run state. Reddit answers this endpoint with HTTP 403 "blocked by network
# security" (or a challenge page) for some networks, for every query alike, so
# the first such answer stops the remaining searches of the command instead of
# spending a rate-limit token on each subreddit. The lane is dispatched with
# the raw topic for every subquery, so one search also serves them all.
class _StreamCollector:
    """Thread-safe per-stream buffer so earlier pages survive a timeout."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._posts: List[Dict[str, Any]] = []
        self._incomplete = False

    def add(self, posts: List[Dict[str, Any]]) -> None:
        if not posts:
            return
        with self._lock:
            self._posts.extend(posts)

    def mark_incomplete(self) -> None:
        with self._lock:
            self._incomplete = True

    @property
    def incomplete(self) -> bool:
        with self._lock:
            return self._incomplete

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._posts)


@dataclass
class _InflightSearch:
    """In-flight search coordination slot shared between owner and waiting callers."""

    done: threading.Event = field(default_factory=threading.Event)
    partial: Optional[List[Dict[str, Any]]] = None
    live_posts: _StreamCollector = field(default_factory=_StreamCollector)

    def add_live(self, posts: List[Dict[str, Any]]) -> None:
        self.live_posts.add(posts)

    def live_snapshot(self, limit: int) -> List[Dict[str, Any]]:
        seen_urls: set = set()
        seen_ids: set = set()
        unique: List[Dict[str, Any]] = []
        for post in self.live_posts.snapshot():
            pid = (post.get("metadata") or {}).get("post_id")
            url = post.get("url")
            if (pid and pid in seen_ids) or (url and url in seen_urls):
                continue
            if pid:
                seen_ids.add(pid)
            if url:
                seen_urls.add(url)
            copied = copy.deepcopy(post)
            copied["id"] = f"R{len(unique) + 1}"
            unique.append(copied)
            if len(unique) >= limit:
                break
        return unique


_run_lock = threading.Lock()
_blocked_reason: Optional[str] = None
_failed_reason: Optional[str] = None
_run_results: Dict[tuple, List[Dict[str, Any]]] = {}
_run_inflight: Dict[tuple, _InflightSearch] = {}
_BLOCK_SIGNALS = ("HTTP 403: Blocked", "not a Reddit search results page")


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditSearch] {msg}\n")
    sys.stderr.flush()


def reset_run_state() -> None:
    """Forget the block flag and shared results. Called once per command, and by tests."""
    global _blocked_reason, _failed_reason
    with _run_lock:
        _blocked_reason = None
        _failed_reason = None
        _run_results.clear()
        _run_inflight.clear()


def failed_reason() -> Optional[str]:
    """Why site search found nothing this command, when Reddit refused it; else None."""
    return _failed_reason


def _note_block(error: Optional[str]) -> None:
    global _blocked_reason
    if not error or not any(signal in error for signal in _BLOCK_SIGNALS):
        return
    reason = "HTTP 403: Blocked" if "403" in error else "challenge page instead of results"
    with _run_lock:
        first = _blocked_reason is None
        if first:
            _blocked_reason = reason
    if first:
        _log(f"site search is blocked for this network ({reason}); skipping the remaining searches")


def unrecognized_body(text: str) -> Optional[str]:
    """Validator for the fetch hook: None for a search page, else a reason."""
    if NO_RESULTS_MARKER in text:
        return None
    if RESULTS_MARKER in text:
        # Marker kept but tracking context changed shape: a clean zero-result
        # parse would hide the drift, so treat it as a lane failure.
        if _post_units(text):
            return None
        return "Reddit search result units no longer parse"
    return "not a Reddit search results page"


def search_url(
    query: str,
    time_filter: str = "month",
    subreddit: Optional[str] = None,
    cursor: Optional[str] = None,
    kind: str = "posts",
    sort: str = "relevance",
) -> str:
    """Build a deterministic search URL so concurrent streams share the memo.

    ``kind`` is Reddit's result type: ``posts`` (the default), or
    ``comments`` and ``communities`` for the logged-in session layer.
    A communities search takes no time filter or sort. Reddit applies the
    time filter to comment search only with ``sort=new``.
    """
    if subreddit:
        sub = subreddit.strip().removeprefix("r/").strip("/")
        path = f"/svc/shreddit/r/{quote(sub, safe='')}/search/"
    else:
        path = "/svc/shreddit/search/"
    if kind == "communities":
        params = [("q", query), ("type", kind)]
    else:
        params = [("q", query), ("type", kind), ("t", time_filter), ("sort", sort)]
    if cursor:
        params.append(("cursor", cursor))
    return f"{BASE}{path}?{urlencode(params)}"


def _post_units(html_text: str) -> List[Tuple[int, Dict[str, Any]]]:
    """(offset, tracking context) for each post-title tracker, in page order."""
    units = []
    for m in _TRACKER.finditer(html_text):
        try:
            ctx = json.loads(_html.unescape(m.group(1)))
        except (ValueError, TypeError):
            continue
        if not isinstance(ctx, dict):
            continue
        action = ctx.get("action_info")
        post = ctx.get("post")
        # A field that is no longer an object is drift: skip the unit, and a
        # page left with none is flagged by unrecognized_body.
        if not isinstance(action, dict) or not isinstance(post, dict):
            continue
        if action.get("type") == "post" and post.get("id"):
            units.append((m.start(), ctx))
    return units


def _next_cursor(html_text: str) -> Optional[str]:
    for m in _NEXT_PAGE.finditer(html_text):
        query = urlsplit(_html.unescape(m.group(1))).query
        cursor = (parse_qs(query).get("cursor") or [None])[0]
        if cursor:
            return cursor
    return None


def next_cursor(html_text: str) -> Optional[str]:
    """The next page's cursor in a search fragment, or None (no run state)."""
    return _next_cursor(html_text)


def parse_page(html_text: str, query: str = "") -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Parse one search fragment into (normalized posts, next-page cursor)."""
    html_text = html_text or ""
    units = _post_units(html_text)
    posts: List[Dict[str, Any]] = []
    seen: set = set()
    for i, (start, ctx) in enumerate(units):
        post_id = str(ctx["post"]["id"]).removeprefix("t3_")
        if post_id in seen:
            continue
        seen.add(post_id)
        end = units[i + 1][0] if i + 1 < len(units) else len(html_text)
        chunk = html_text[start:end]

        sub_ctx = ctx.get("subreddit")
        subreddit = str(sub_ctx.get("name") or "") if isinstance(sub_ctx, dict) else ""
        profile_ctx = ctx.get("profile")
        raw_author = profile_ctx.get("name") if isinstance(profile_ctx, dict) else None
        author = raw_author.strip() if isinstance(raw_author, str) else ""
        title = str(ctx["post"].get("title") or "")
        href_match = _TITLE_HREF.search(chunk)
        href = _html.unescape((href_match.group(1) or href_match.group(2))) if href_match else ""
        href = href.split("?", 1)[0]
        if f"/comments/{post_id}/" in href and href.startswith("/r/"):
            url = f"{BASE}{href}"
        else:
            url = f"{BASE}/r/{subreddit}/comments/{post_id}/"

        ts_match = _TIMEAGO.search(chunk)
        ts = ts_match.group(1) if ts_match else None
        # First counter of each kind wins: this post's own counters precede any
        # that belong to a skipped (malformed) neighbour sharing the chunk.
        counts: Dict[str, int] = {}
        for number, label in _COUNTER.findall(chunk):
            counts.setdefault(label, int(number))
        score, num_comments = counts.get("vote", 0), counts.get("comment", 0)

        posts.append({
            "id": "",
            "title": title,
            "url": url,
            "score": score,
            "num_comments": num_comments,
            "subreddit": subreddit,
            "created_utc": reddit_listing._to_epoch(ts),
            "author": author,
            "selftext": "",
            "date": reddit_listing._to_date(ts),
            "engagement": {
                "score": score,
                "num_comments": num_comments,
                "upvote_ratio": None,
            },
            "relevance": round(token_overlap_relevance(query, title), 3) if query else 0.0,
            "why_relevant": "Reddit search",
            "metadata": {"post_id": post_id},
        })
    return posts, _next_cursor(html_text)


def _fetch_page(
    url: str, deadline: Optional[float] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """(body, error). Shared limiter, run memo, one 429 retry, failure sink."""
    req_timeout: float = SEARCH_TIMEOUT
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, "deadline exceeded"
        req_timeout = min(float(SEARCH_TIMEOUT), remaining)
    kwargs: Dict[str, Any] = {
        "timeout": req_timeout,
        "accept": "text/html",
        "validate": unrecognized_body,
    }
    if deadline is not None:
        kwargs["deadline_monotonic"] = deadline
    try:
        return http.reddit_keyless_get_text_retry_429(url, **kwargs)
    except Exception as e:  # defensive: one bad page must not sink the run
        _log(f"search fetch failed for {url}: {e}")
        return None, str(e)


def _search_stream(
    query: str,
    time_filter: str,
    max_pages: int,
    subreddit: Optional[str] = None,
    deadline: Optional[float] = None,
    collector: Optional[_StreamCollector] = None,
    inflight: Optional[_InflightSearch] = None,
) -> List[Dict[str, Any]]:
    """Page one search sequentially. Keeps earlier pages when a later one fails."""
    label = f"r/{subreddit}" if subreddit else "global"
    posts: List[Dict[str, Any]] = []
    seen_ids: set = set()
    used_cursors: set = set()
    cursor: Optional[str] = None
    for page in range(1, max_pages + 1):
        if _blocked_reason:
            break
        if deadline is not None and time.monotonic() >= deadline:
            if collector is not None:
                collector.mark_incomplete()
            _log(f"{label} stopped before page {page}: deadline reached")
            break
        text, error = _fetch_page(
            search_url(query, time_filter, subreddit, cursor),
            deadline=deadline,
        )
        if text is None:
            _log(f"{label} page {page} failed: {error or 'no response'}")
            _note_block(error)
            if collector is not None and not _blocked_reason:
                collector.mark_incomplete()
            break
        page_posts, next_cursor = parse_page(text, query)
        new = [p for p in page_posts if p["metadata"]["post_id"] not in seen_ids]
        for p in new:
            seen_ids.add(p["metadata"]["post_id"])
        posts.extend(new)
        if collector is not None:
            collector.add(new)
        if inflight is not None:
            inflight.add_live(new)
        if not new or not next_cursor or next_cursor in used_cursors:
            break
        used_cursors.add(next_cursor)
        cursor = next_cursor
    return posts


def _result_timeout(batch_size: int, pages: int = 1) -> float:
    """Per-future wait: each sequential page's own timeout plus the bucket's queue."""
    return pages * (SEARCH_TIMEOUT + 5) + http.reddit_keyless_wait_allowance(batch_size)


def _time_filter(from_date: Optional[str], to_date: Optional[str]) -> str:
    # Same mapping as the ScrapeCreators path; falls back to "month".
    from .reddit import _window_to_time_filter
    return _window_to_time_filter(from_date or "", to_date or "")


def search(
    query: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Discover Reddit posts for a query via the site search fragment.

    ``deadline`` is a ``time.monotonic()`` bound: streams still running then
    are left behind and the posts of the streams that answered are returned.

    Runs a paged global search plus one page of per-subreddit search for each
    targeted subreddit. Returns normalized, dated, scored posts, deduped by URL
    and capped by depth. Streams are interleaved round-robin before the cap so
    neither the targeted subreddits nor the global search crowds out the other. Returns ``[]`` on total failure and never raises; failures
    land in the pipeline's failure sink. Callers of one command that ask for
    the same search share one run of it.
    """
    try:
        global_pages = GLOBAL_PAGE_CAPS.get(depth, GLOBAL_PAGE_CAPS["default"])
        time_filter = _time_filter(from_date, to_date)
        subs = []
        for raw in subreddits or []:
            sub = (raw or "").strip().removeprefix("r/").strip("/")
            if sub and sub.lower() not in {s.lower() for s in subs}:
                subs.append(sub)
        limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
        key = (query, depth, time_filter, tuple(sub.lower() for sub in subs))
        owner = False
        slot: Optional[_InflightSearch] = None
        partial_fallback: Optional[List[Dict[str, Any]]] = None
        for _round in range(2):
            with _run_lock:
                if key in _run_results:
                    return copy.deepcopy(_run_results[key])
                if deadline is not None and time.monotonic() >= deadline:
                    return copy.deepcopy(partial_fallback or [])
                slot = _run_inflight.get(key)
                owner = slot is None
                if owner:
                    slot = _InflightSearch()
                    _run_inflight[key] = slot
            if owner:
                break
            wait = _result_timeout(len(subs) + global_pages, global_pages)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return copy.deepcopy(partial_fallback or [])
                wait = min(wait, remaining)
            slot.done.wait(timeout=wait)
            if slot.partial:
                partial_fallback = slot.partial
            else:
                live = slot.live_snapshot(limit)
                if live:
                    partial_fallback = live
            if deadline is not None and time.monotonic() >= deadline:
                with _run_lock:
                    if key in _run_results:
                        return copy.deepcopy(_run_results[key])
                return copy.deepcopy(partial_fallback or [])
        else:
            with _run_lock:
                if key in _run_results:
                    return copy.deepcopy(_run_results[key])
            return copy.deepcopy(partial_fallback or [])
        try:
            unique, complete = _search_uncached_with_status(
                query, depth, subs, time_filter, global_pages, deadline, inflight=slot,
            )
            # Only cache complete non-empty runs or network-wide blocks;
            # partial runs from a timeout stay uncached so later callers with
            # more time can fetch all pages, while concurrent waiters can still
            # fall back to the salvaged posts if they have no time left.
            if (complete and unique) or (unique is not None and _blocked_reason):
                with _run_lock:
                    _run_results[key] = unique
            elif unique:
                slot.partial = unique
            return copy.deepcopy(unique if unique else (partial_fallback or []))
        finally:
            if owner:
                with _run_lock:
                    if _run_inflight.get(key) is slot:
                        _run_inflight.pop(key, None)
                if slot is not None:
                    slot.done.set()
    except Exception as e:  # defensive: discovery must never raise into the pipeline
        _log(f"search failed: {e}")
        return []


def _search_uncached_with_status(
    query: str,
    depth: str,
    subs: List[str],
    time_filter: str,
    global_pages: int,
    deadline: Optional[float] = None,
    inflight: Optional[_InflightSearch] = None,
) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    """One run of the search streams: ``(posts_or_none, complete)``."""
    global _failed_reason
    try:
        if deadline is not None and time.monotonic() >= deadline:
            return [], False
        limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
        jobs = [(sub, SUB_PAGE_CAP) for sub in subs] + [(None, global_pages)]
        batch = sum(pages for _sub, pages in jobs)
        streams: List[List[Dict[str, Any]]] = []
        complete = True
        executor = ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(jobs)))
        try:
            # submit_with_context, not executor.submit: a plain submit starts the
            # worker with an empty context, dropping the pipeline's
            # capture_failures() sink so a 429/403 or a challenge page is
            # silently discarded (issue #899).
            futures = []
            for sub, pages in jobs:
                collector = _StreamCollector()
                futures.append((
                    http.submit_with_context(
                        executor, _search_stream, query, time_filter, pages, sub,
                        deadline=deadline, collector=collector, inflight=inflight,
                    ),
                    sub,
                    pages,
                    collector,
                ))
            for future, sub, pages, collector in futures:
                wait = _result_timeout(batch, pages)
                if deadline is not None:
                    wait = max(0.0, min(wait, deadline - time.monotonic()))
                try:
                    streams.append(future.result(timeout=wait))
                    if collector.incomplete:
                        complete = False
                except (Exception, FuturesTimeoutError) as e:
                    complete = False
                    salvaged = collector.snapshot()
                    if salvaged:
                        streams.append(salvaged)
                    _log(
                        f"{'r/' + sub if sub else 'global'} search future failed: "
                        f"{e or 'still running at the deadline'}"
                        + (f" (kept {len(salvaged)} posts from earlier pages)" if salvaged else "")
                    )
        finally:
            # Under a deadline a stream still queued or running is not waited for.
            executor.shutdown(wait=deadline is None, cancel_futures=deadline is not None)

        # Round-robin across streams, each in its own relevance order, so the
        # depth cap keeps a share of every stream.
        results = [
            stream[i]
            for i in range(max((len(s) for s in streams), default=0))
            for stream in streams
            if i < len(stream)
        ]
        seen: set = set()
        unique: List[Dict[str, Any]] = []
        for post in results:
            if post["url"] not in seen:
                seen.add(post["url"])
                unique.append({**post, "id": f"R{len(unique) + 1}"})
                if len(unique) >= limit:
                    break
        _log(f"{len(unique)} posts (t={time_filter}, {len(subs)} targeted subs)")
        if not unique and _blocked_reason:
            _failed_reason = _blocked_reason
        return unique, complete
    except Exception as e:  # defensive: discovery must never raise into the pipeline
        _log(f"search failed: {e}")
        return None, False


def _search_uncached(
    query: str,
    depth: str,
    subs: List[str],
    time_filter: str,
    global_pages: int,
    deadline: Optional[float] = None,
) -> Optional[List[Dict[str, Any]]]:
    """One run of the search streams; None when it raised."""
    unique, _complete = _search_uncached_with_status(
        query, depth, subs, time_filter, global_pages, deadline,
    )
    return unique
