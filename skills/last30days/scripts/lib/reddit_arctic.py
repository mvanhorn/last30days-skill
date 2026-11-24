"""Arctic-shift score resolver — post upvote counts by id, keyless and free.

``search.json`` and ``/comments/{id}.json`` are 403 keyless. Site search and the
shreddit listing partials usually carry a score, but some posts still arrive
unscored. For a thread that reaches the merge with score 0, the free score
comes from arctic-shift
(https://arctic-shift.photon-reddit.com), a public Reddit archive whose
``/api/posts/ids`` returns the post object (score, num_comments, title) for a
batch of base36 post ids. Scores are point-in-time snapshots — slightly stale vs
live, which is fine for ranking and display.

Best-effort, never raises. On rate-limit (HTTP 422 "slow down"), error, or an
unreachable host it returns ``{}`` so the caller shows the thread without a point
count rather than failing the Reddit source.
"""

import inspect
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from . import http, reddit_refs

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")

API = "https://arctic-shift.photon-reddit.com/api/posts/ids"
SEARCH_API = "https://arctic-shift.photon-reddit.com/api/posts/search"
COMMENTS_TREE_API = "https://arctic-shift.photon-reddit.com/api/comments/tree"
BATCH = 50          # ids per request
IDS_BATCH = 100     # ids per hydration request; the archive documents a 500-id limit
TIMEOUT = 15
MAX_BATCHES = 3     # cap total requests per run (bounds latency + rate-limit risk)
PACE_SECONDS = 0.4  # gap between batches; arctic-shift answers 422 "slow down"
CACHE_MAX = 4096    # hard size bound so the in-run memo can never grow unbounded
# Listing-lane knobs. Base limits mirror reddit_listing's DEPTH_LIMITS so callers
# get the same per-depth volume. The supplement multiplier is applied when the
# caller requested multiple sorts (top/hot/new) — arctic-shift has no sort lanes
# (only asc/desc), so we fetch more posts to increase the chance of covering
# what the failed shreddit lanes would have returned. Windowed runs
# (from_date/to_date) instead slice the window and keep the highest-scored
# posts, which recovers window-top engagement without sort lanes.
#
_LISTING_DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}
_LISTING_SUPPLEMENT_MULTIPLIER = 2  # fetch 2x posts when supplementing multi-sort requests
# Total deadline for listing fetches to prevent unbounded stalls when many
# subreddits are requested and arctic is slow/unreachable.
_LISTING_DEADLINE_SECONDS = 45  # ~3 subs at 15s timeout each
# Comment tree rows read by hydration (fetch_comment_tree).
MAX_COMMENT_ROWS = 100
# Comment fallback (fetch_post_comments) rows. The archive's comment search
# sorts only by created_utc, so a capped search slice of a busy thread held only
# its newest replies and missed its most upvoted ones. The fallback reads the
# comment tree instead and ranks by score client-side; on a 628-reply thread a
# 500-row tree kept the same top replies as an uncapped one (#1467).
FALLBACK_COMMENT_ROWS = 500
# In-run memo: base36 id -> {score, num_comments}. Module-level so repeated
# fetch_scores calls within one `/last30days` run (e.g. across subqueries) reuse
# results, but capped at CACHE_MAX entries (never reached in a normal CLI run).
# Tests clear it via reddit_arctic._cache.clear().
_cache: Dict[str, Dict[str, int]] = {}


# Run memo for listing and comment searches. The Reddit lane runs once per
# subquery with the same subreddits and window, so a four-subquery run sent
# each search four times at once, and the archive answers all but one of a
# set of identical concurrent requests with HTTP 422 "Timeout. Maybe slow
# down a bit" (measured 2026-11-10: 4 identical requests -> 3 x 422). One
# request per URL per command: later callers wait for the first and reuse it.
# The archive also sheds load with the same 422 on requests that are neither
# duplicated nor fast (measured: 2 to 4 of 10 sequential searches, at 0.4s and
# at 1.5s spacing alike, with or without a ``fields`` selection), and a retry
# right after one tends to be refused again. A comment lookup retries once
# after a pause; a listing walk asks again for its refused slices at the end.
@dataclass
class _ScoreSlot:
    """In-flight score batch shared between the owner and waiting callers."""

    done: threading.Event = field(default_factory=threading.Event)
    scores: Dict[str, Dict[str, int]] = field(default_factory=dict)
    attempted: bool = False


MEMO_MAX = 512
RETRY_422_PAUSE = 1.5
_memo: Dict[str, Any] = {}
_inflight: Dict[str, threading.Event] = {}
_score_inflight: Dict[str, _ScoreSlot] = {}
_memo_lock = threading.Lock()
_tree_state = threading.local()


def _log(msg: str) -> None:
    sys.stderr.write(f"[ArcticShift] {msg}\n")
    sys.stderr.flush()


def _set_thread_tree_throttled(value: bool) -> None:
    _tree_state.throttled = bool(value)


def last_tree_was_throttled() -> bool:
    """Whether this thread's last ``fetch_comment_tree`` call was throttled (422/429)."""
    return bool(getattr(_tree_state, "throttled", False))


def __getattr__(name: str) -> Any:
    if name == "last_tree_throttled":
        return last_tree_was_throttled()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def reset_memo() -> None:
    """Forget memoized archive responses. Called once per command, and by tests."""
    with _memo_lock:
        _memo.clear()
        _inflight.clear()
        _cache.clear()
        _score_inflight.clear()
    _set_thread_tree_throttled(False)


def _is_slow_down(error: BaseException) -> bool:
    return getattr(error, "status_code", None) == 422


def _fetch_search(url: str, deadline: float, retry_422: bool = True) -> Any:
    """One archive search, retried once after a pause when it answers 422.

    With ``retry_422`` off a 422 is raised at once and kept out of the
    pipeline's failure sink: the caller means to ask again later, and reports
    it with ``_report_failures`` if it never does.
    """
    for attempt in range(2):
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            raise http.DeadlineExceeded()
        # A 422 that the retry recovers is not a lost request, so it is kept
        # out of the pipeline's failure sink unless the retry fails too.
        with http.capture_failures() as failures:
            try:
                # DNS recovery may retry even with retries=1. Bound the whole
                # request so those retries cannot consume other callers' time.
                return http.get(
                    url,
                    headers={"User-Agent": http.BROWSER_USER_AGENT},
                    timeout=min(TIMEOUT, remaining),
                    retries=1,
                    deadline_monotonic=min(deadline, now + TIMEOUT),
                )
            except Exception as exc:
                error = exc
        if _is_slow_down(error) and not retry_422:
            error.unreported_failures = list(failures)  # type: ignore[attr-defined]
            raise error
        retryable = (
            attempt == 0
            and _is_slow_down(error)
            and deadline - time.monotonic() > RETRY_422_PAUSE + 1
        )
        if not retryable:
            for failure in failures:
                http._record_failure(failure)
            raise error
        time.sleep(RETRY_422_PAUSE)
    raise http.DeadlineExceeded()  # pragma: no cover - the loop always returns or raises


def _report_failures(error: BaseException) -> None:
    """Record a deferred 422 that was never asked for again."""
    for failure in getattr(error, "unreported_failures", ()):
        http._record_failure(failure)


def _search_once(url: str, deadline: float, retry_422: bool = True) -> Any:
    """Archive search shared across the run: memoized, one fetch in flight per URL.

    Returns the decoded response, or raises what the fetch raised. Only a
    response that carries a ``data`` list is memoized, so a rate-limit body is
    asked for again by the next caller.
    """
    for _round in range(3):
        with _memo_lock:
            if url in _memo:
                return _memo[url]
            gate = _inflight.get(url)
            owner = gate is None
            if owner:
                gate = threading.Event()
                _inflight[url] = gate
        if owner:
            break
        gate.wait(timeout=max(0.0, deadline - time.monotonic()))
        if time.monotonic() >= deadline:
            raise http.DeadlineExceeded()
    else:
        raise http.HTTPError(f"Shared archive request kept failing: {url}")
    try:
        data = _fetch_search(url, deadline, retry_422)
        if isinstance((data or {}).get("data"), list):
            with _memo_lock:
                if len(_memo) < MEMO_MAX:
                    _memo[url] = data
        return data
    finally:
        with _memo_lock:
            _inflight.pop(url, None)
        gate.set()


def fetch_scores(
    post_ids: List[str], deadline: Optional[float] = None,
) -> Dict[str, Dict[str, int]]:
    """Return ``{base36_post_id: {"score", "num_comments"}}`` for the given ids.

    ``deadline`` is a ``time.monotonic()`` bound: no batch starts after it and
    a running one is cut off at it, and the scores already found are returned.

    Batched, paced, in-run cached, and never raises. Ids that fail or are absent
    from the archive are simply missing from the result (caller degrades to no
    point count for those threads).
    """
    out: Dict[str, Dict[str, int]] = {}
    clean_ids = list(dict.fromkeys(
        clean for pid in post_ids if (clean := reddit_refs.normalize_post_id(pid))
    ))
    if not clean_ids:
        return out

    pending: List[str] = list(clean_ids)
    batches_fetched = 0
    stop_fetching = False
    wait_retries = 0
    wait_end = (
        deadline
        if deadline is not None
        else time.monotonic() + TIMEOUT * MAX_BATCHES + PACE_SECONDS * MAX_BATCHES
    )

    for _round in range(MAX_BATCHES + 3):
        if batches_fetched > 0 and not stop_fetching:
            if deadline is not None:
                pace = min(PACE_SECONDS, max(0.0, deadline - time.monotonic()))
                if pace > 0:
                    time.sleep(pace)
            else:
                time.sleep(PACE_SECONDS)

        todo: List[str] = []
        waiting: List[tuple[str, _ScoreSlot]] = []
        batch: List[str] = []
        slot: Optional[_ScoreSlot] = None

        with _memo_lock:
            for clean in pending:
                if clean in out:
                    continue
                if clean in _cache:
                    out[clean] = dict(_cache[clean])
                elif clean in _score_inflight:
                    waiting.append((clean, _score_inflight[clean]))
                else:
                    todo.append(clean)
            if todo and not stop_fetching and batches_fetched < MAX_BATCHES:
                if deadline is not None and time.monotonic() >= deadline:
                    _log(f"lookup stopped at its deadline; {len(todo)} ids left unscored")
                    stop_fetching = True
                else:
                    # Claim one batch at a time so an owner that expires after
                    # batch 1 never blocks waiting callers from fetching batch 2.
                    batch = todo[:BATCH]
                    slot = _ScoreSlot()
                    for pid in batch:
                        _score_inflight[pid] = slot

        remaining_todo = todo[len(batch):]

        if slot is not None:
            try:
                bound: Dict[str, Any] = {"timeout": TIMEOUT}
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _log(f"lookup stopped at its deadline; {len(batch)} ids left unscored")
                        stop_fetching = True
                    else:
                        bound = {
                            "timeout": min(TIMEOUT, remaining),
                            "retries": 1,
                            "deadline_monotonic": deadline,
                        }
                if not stop_fetching:
                    batches_fetched += 1
                    try:
                        query = urlencode({"ids": ",".join(batch)}, safe=",")
                        data = http.get(
                            f"{API}?{query}",
                            headers={"User-Agent": http.BROWSER_USER_AGENT},
                            **bound,
                        )
                    except Exception as e:  # network error / non-200 — degrade, never raise
                        _log(f"lookup failed ({e}); {len(batch)} ids left unscored")
                        stop_fetching = True
                    else:
                        rows = (data or {}).get("data")
                        if not isinstance(rows, list):
                            # arctic-shift returns {"error": "..."} on rate-limit / bad request.
                            _log(f"unexpected response (rate-limited?): {str(data)[:80]}")
                            stop_fetching = True
                        else:
                            entries: Dict[str, Dict[str, int]] = {}
                            for row in rows:
                                if not isinstance(row, dict):
                                    continue
                                rid = str(row.get("id") or "").removeprefix("t3_")
                                if not rid:
                                    continue
                                try:
                                    entries[rid] = {
                                        "score": int(row.get("score") or 0),
                                        "num_comments": int(row.get("num_comments") or 0),
                                    }
                                except (TypeError, ValueError):
                                    continue
                            with _memo_lock:
                                for rid, entry in entries.items():
                                    if rid in _cache or len(_cache) < CACHE_MAX:
                                        _cache[rid] = dict(entry)
                                    slot.scores[rid] = dict(entry)
                                    out[rid] = dict(entry)
                                slot.attempted = True
                                for pid in batch:
                                    if _score_inflight.get(pid) is slot:
                                        _score_inflight.pop(pid, None)
            finally:
                with _memo_lock:
                    for pid in batch:
                        if _score_inflight.get(pid) is slot:
                            _score_inflight.pop(pid, None)
                slot.done.set()

            if remaining_todo and not stop_fetching and batches_fetched < MAX_BATCHES:
                pending = remaining_todo + [pid for pid, _ in waiting]
                continue

        if not waiting:
            break

        unique_slots: List[_ScoreSlot] = []
        seen_slot_ids: set[int] = set()
        for _pid, wslot in waiting:
            sid = id(wslot)
            if sid not in seen_slot_ids:
                seen_slot_ids.add(sid)
                unique_slots.append(wslot)
        timed_out = False
        for wslot in unique_slots:
            remaining = wait_end - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            if not wslot.done.wait(timeout=remaining):
                timed_out = True
                break

        retry_ids: List[str] = []
        with _memo_lock:
            for pid, wslot in waiting:
                if pid in wslot.scores:
                    out[pid] = dict(wslot.scores[pid])
                elif pid in _cache:
                    out[pid] = dict(_cache[pid])
                elif wslot.done.is_set() and not wslot.attempted:
                    retry_ids.append(pid)

        if (
            timed_out
            or not retry_ids
            or stop_fetching
            or batches_fetched >= MAX_BATCHES
            or wait_retries >= 1
            or (deadline is not None and time.monotonic() >= deadline)
        ):
            break
        wait_retries += 1
        pending = retry_ids

    return out


def _parse_iso_date(value: Optional[str]) -> Optional[date]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO_DATE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _epoch_to_date(value: Any) -> Optional[str]:
    """Epoch seconds -> YYYY-MM-DD (UTC), or None on garbage."""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _normalize_listing_row(row: Dict[str, Any], query: str = "") -> Dict[str, Any]:
    """Normalize an arctic-shift post row to reddit_listing.parse_cards shape.

    Mirrors the shreddit card schema (title/url/score/num_comments/subreddit/
    created_utc/author/selftext/date/engagement/relevance/metadata.post_id) so
    reddit_keyless can consume either backend interchangeably.
    """
    from .relevance import token_overlap_relevance

    pid = str(row.get("id") or "").removeprefix("t3_")
    permalink = row.get("permalink") or ""
    title = row.get("title") or ""
    try:
        score = int(row.get("score") or 0)
    except (TypeError, ValueError):
        score = 0
    try:
        num_comments = int(row.get("num_comments") or 0)
    except (TypeError, ValueError):
        num_comments = 0
    author = row.get("author") or "[deleted]"
    if author in ("[deleted]", "[removed]"):
        author = "[deleted]"
    url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else (permalink or "")
    return {
        "id": "",
        "title": title,
        "url": url,
        "score": score,
        "num_comments": num_comments,
        "subreddit": row.get("subreddit") or "",
        "created_utc": row.get("created_utc"),
        "author": author,
        "selftext": row.get("selftext") or "",
        "date": _epoch_to_date(row.get("created_utc")),
        "engagement": {"score": score, "num_comments": num_comments, "upvote_ratio": None},
        "relevance": round(token_overlap_relevance(query, title), 3) if query else 0.0,
        "why_relevant": "Reddit listing (arctic-shift)",
        "metadata": {"post_id": pid},
    }


def fetch_listings(
    subreddits: List[str],
    depth: str = "default",
    query: str = "",
    sorts: Optional[List[str]] = None,
    timeframe: str = "month",
    limit: Optional[int] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    lane_deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Scored subreddit listings from the arctic-shift archive, keyless.

    Drop-in fallback/supplement for ``reddit_listing.fetch_listings`` (shreddit
    partials), which datacenter IPs get HTTP 403 on. Arctic-shift serves recent
    posts with real score/num_comments from any IP.

    Arctic-shift has no top/hot/new lanes (only asc/desc). Without a date
    window the fetch is recency-only, and the archive freezes score at ingest,
    so the freshest ~2-3 days read as 1-point stubs. When the caller supplies
    ``from_date``/``to_date``, the window is sliced into ~7-day chunks queried
    with ``after``/``before`` and the highest-scored sampled posts are kept per
    subreddit. Slice requests rotate across subreddits under a shared deadline;
    successful partial results survive failures. The 100-row cap per slice
    means the sample can miss popular posts in busy subreddits. Best-effort,
    never raises: returns ``[]`` when no requests succeed. ``lane_deadline``
    is the Reddit lane's own ``time.monotonic()`` bound; the walk stops at
    whichever deadline comes first.
    """
    if not subreddits:
        return []
    base = limit or _LISTING_DEPTH_LIMITS.get(depth, _LISTING_DEPTH_LIMITS["default"])
    # When multiple sorts were requested, fetch more posts to compensate for
    # arctic-shift's lack of sort lanes.
    n = base * _LISTING_SUPPLEMENT_MULTIPLIER if sorts and len(sorts) > 1 else base
    # Window + engagement sort: the archive freezes score at ingest time, so
    # posts from the last ~2-3 days read as 1-point stubs and `before` ending
    # today can return null data. Scope the query with after/before, slice the
    # window into ~7-day chunks (API caps 100 rows/request, newest-first, so
    # one request only covers the last day of a month window), and keep the
    # highest-scored n per subreddit (sort has no top lane — only asc/desc).
    start = _parse_iso_date(from_date)
    parsed_to = _parse_iso_date(to_date)
    clean_from = start.isoformat() if start else None
    clean_to = parsed_to.isoformat() if parsed_to else None
    window = bool(clean_from or clean_to)
    slices: Optional[List[tuple]] = None
    if window:
        from datetime import timedelta as _timedelta
        end = parsed_to if parsed_to is not None else date.today()
        win_start = start if start is not None else end - _timedelta(days=30)
        if win_start < end:
            span = (end - win_start).days
            n_slices = min(4, max(1, span // 7))
            step = max(1, -(-span // n_slices))
            slices = []
            cursor = win_start
            while cursor < end:
                slices.append((cursor, min(cursor + _timedelta(days=step), end)))
                cursor += _timedelta(days=step)
    req_limit = 100 if window else n
    posts_by_sub: Dict[str, List[Dict[str, Any]]] = {}
    for raw_sub in subreddits:
        sub = reddit_refs.normalize_subreddit(raw_sub, allow_all=False)
        if sub:
            posts_by_sub.setdefault(sub, [])
    if not posts_by_sub:
        return []
    seen_by_sub = {sub: set() for sub in posts_by_sub}
    # Give every subreddit a slice before spending the shared budget on more
    # slices for any one subreddit.
    # Newest slice first: when the shared deadline cuts the walk short, the
    # week that is dropped is the oldest one, not the one the run is about.
    requests = (
        (sub, span_pair)
        for span_pair in (list(reversed(slices)) if slices else [None])
        for sub in posts_by_sub
    )
    deadline = time.monotonic() + _LISTING_DEADLINE_SECONDS
    if lane_deadline is not None:
        deadline = min(deadline, lane_deadline)

    def url_for(sub: str, span_pair: Optional[tuple]) -> str:
        params: List[tuple[str, Any]] = [
            ("subreddit", sub),
            ("limit", req_limit),
            ("sort", "desc"),
        ]
        if span_pair:
            params.append(("after", span_pair[0].isoformat()))
            params.append(("before", span_pair[1].isoformat()))
        else:
            if clean_from:
                params.append(("after", clean_from))
            if clean_to:
                params.append(("before", clean_to))
        return f"{SEARCH_API}?{urlencode(params)}"

    def keep(sub: str, data: Any) -> None:
        rows = (data or {}).get("data")
        if not isinstance(rows, list):
            _log(f"unexpected listing response for r/{sub}: {str(data)[:80]}")
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            post = _normalize_listing_row(row, query)
            if post["url"] and post["url"] not in seen_by_sub[sub]:
                seen_by_sub[sub].add(post["url"])
                posts_by_sub[sub].append(post)

    # Two passes under the one deadline. The first asks for every (subreddit,
    # slice) once and sets aside what the archive refuses with 422; the second
    # asks for those again, so a refusal neither stalls the walk nor loses the
    # slice while budget remains.
    first_pass = [(sub, url_for(sub, span_pair), None) for sub, span_pair in requests]
    refused: List[tuple] = []
    paced = False
    fetched_count = 0
    out_of_time = False
    for retrying, batch in ((False, first_pass), (True, refused)):
        for sub, url, earlier_error in batch:
            if out_of_time:
                if earlier_error is not None:
                    _report_failures(earlier_error)
                    _log(f"listing search failed r/{sub}: {earlier_error}")
                continue
            with _memo_lock:
                memoized = url in _memo
            # Pace requests that reach the archive; a memoized answer costs nothing.
            if paced and not memoized:
                time.sleep(min(PACE_SECONDS, max(0, deadline - time.monotonic())))
            if deadline - time.monotonic() <= 0:
                _log(f"listing deadline reached after {fetched_count} requests; keeping partial results")
                out_of_time = True
                if earlier_error is not None:
                    _report_failures(earlier_error)
                    _log(f"listing search failed r/{sub}: {earlier_error}")
                continue
            paced = paced or not memoized
            fetched_count += 1
            try:
                data = _search_once(url, deadline, retry_422=False)
            except Exception as e:  # network error / non-200 — degrade, never raise
                if not retrying and _is_slow_down(e):
                    refused.append((sub, url, e))
                    continue
                _report_failures(e)
                _log(f"listing search failed r/{sub}: {e}")
                continue
            keep(sub, data)

    out: List[Dict[str, Any]] = []
    for sub_posts in posts_by_sub.values():
        if window:
            sub_posts.sort(key=lambda p: -(p["score"] or 0))
            sub_posts = sub_posts[:n]
        out.extend(sub_posts)

    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for p in out:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)
    return unique


def _acquire_keyless_limiter(deadline: float) -> None:
    limiter = http.REDDIT_KEYLESS_LIMITER
    if isinstance(limiter, http.RateLimiter):
        limiter.acquire(deadline_monotonic=deadline)
        return
    try:
        params = inspect.signature(limiter.acquire).parameters
    except (TypeError, ValueError):
        params = {}
    if "deadline_monotonic" in params or any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
    ):
        limiter.acquire(deadline_monotonic=deadline)
    else:
        limiter.acquire()


def _archive_get(url: str, deadline: float) -> Any:
    """One serial archive request through the shared keyless Reddit limiter,
    retried once after a pause when the archive answers 422."""
    _acquire_keyless_limiter(deadline)
    return _fetch_search(url, deadline, retry_422=True)


def fetch_posts_by_ids(
    post_ids: List[str], deadline: Optional[float] = None,
) -> tuple:
    """Hydrate posts by base36 id: ``({id: listing-shaped post}, state)``.

    Batches of ``IDS_BATCH`` ids, one request at a time through the shared
    keyless limiter. Each post is normalized with ``_normalize_listing_row``
    and keeps the archive's raw ``created_utc``. ``state`` is ``"ok"``,
    ``"throttled"`` when the archive refused a batch with 422 twice (no later
    batch is sent), or ``"down"`` when a batch failed any other way (later
    batches are still tried). Never raises.
    """
    todo = list(dict.fromkeys(
        clean for p in post_ids if (clean := reddit_refs.normalize_post_id(p))
    ))
    end = deadline if deadline is not None else time.monotonic() + _LISTING_DEADLINE_SECONDS
    out: Dict[str, Dict[str, Any]] = {}
    state = "ok"
    for start in range(0, len(todo), IDS_BATCH):
        batch = todo[start:start + IDS_BATCH]
        if end - time.monotonic() <= 0:
            _log(f"post lookup stopped at its deadline; {len(todo) - start} ids left")
            # Unasked ids are not an archive answer: never report them as ok.
            if state == "ok":
                state = "down"
            break
        try:
            _acquire_keyless_limiter(end)
        except http.DeadlineExceeded:
            _log(f"post lookup stopped at its deadline; {len(todo) - start} ids left")
            if state == "ok":
                state = "down"
            break
        try:
            query = urlencode({"ids": ",".join(batch)}, safe=",")
            data = _fetch_search(f"{API}?{query}", end, retry_422=True)
        except http.DeadlineExceeded as exc:
            if time.monotonic() >= end:
                _log(f"post lookup stopped at its deadline; {len(todo) - start} ids left")
                if state == "ok":
                    state = "down"
                break
            _log(f"post lookup batch timed out ({exc}); {len(batch)} ids left")
            state = "down"
            continue
        except Exception as exc:  # network error / non-200 — degrade, never raise
            if _is_slow_down(exc):
                _log(f"post lookup throttled (422 twice); {len(todo) - start} ids left")
                return out, "throttled"
            _log(f"post lookup failed ({exc}); {len(batch)} ids left")
            state = "down"
            continue
        rows = (data or {}).get("data")
        if not isinstance(rows, list):
            _log(f"unexpected post lookup response: {str(data)[:80]}")
            if "slow down" in str((data or {}).get("error") or "").lower():
                return out, "throttled"
            state = "down"
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            post = _normalize_listing_row(row)
            pid = post["metadata"]["post_id"]
            if pid and post["url"]:
                post["why_relevant"] = "Reddit thread (arctic-shift)"
                out[pid] = post
    return out, state


def _tree_rows(nodes: Any) -> List[Dict[str, Any]]:
    """Every comment in an archive comment tree, parents before their replies."""
    rows: List[Dict[str, Any]] = []
    stack = list(reversed(nodes)) if isinstance(nodes, list) else []
    while stack:
        node = stack.pop()
        if not isinstance(node, dict) or node.get("kind") != "t1":
            continue
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        rows.append(data)
        replies = data.get("replies")
        children = ((replies or {}).get("data") or {}).get("children") if isinstance(replies, dict) else None
        if isinstance(children, list):
            stack.extend(reversed(children))
    return rows


def _fetch_comment_tree_with_status(
    post_id: str, deadline: Optional[float] = None,
) -> tuple[Optional[List[Dict[str, Any]]], bool]:
    """Fetch comments of a post from ``/comments/tree``, returning ``(rows, throttled)``.

    Also updates the calling thread's ``last_tree_throttled`` state so concurrent
    streams never overwrite each other's throttle classification.
    """
    _set_thread_tree_throttled(False)
    pid = reddit_refs.normalize_post_id(post_id)
    if not pid:
        return None, False
    end = deadline if deadline is not None else time.monotonic() + TIMEOUT
    if end - time.monotonic() < 1:
        return None, False
    try:
        query = urlencode({"link_id": f"t3_{pid}", "limit": MAX_COMMENT_ROWS})
        data = _archive_get(f"{COMMENTS_TREE_API}?{query}", end)
    except Exception as exc:  # network error / non-200 — degrade, never raise
        throttled = _is_slow_down(exc) or getattr(exc, "status_code", None) == 429
        _set_thread_tree_throttled(throttled)
        _log(f"comment tree failed t3_{pid}: {exc}")
        return None, throttled
    nodes = (data or {}).get("data")
    if not isinstance(nodes, list):
        _log(f"unexpected comment tree response for t3_{pid}: {str(data)[:80]}")
        return None, False

    from .snippet import cut_quote

    comments: List[Dict[str, Any]] = []
    for row in _tree_rows(nodes):
        author = row.get("author") or "[deleted]"
        body = row.get("body") or ""
        if author in ("[deleted]", "[removed]") or body in ("[deleted]", "[removed]"):
            continue
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        try:
            depth = int(row.get("depth") or 0)
        except (TypeError, ValueError):
            depth = 0
        permalink = row.get("permalink") or ""
        entry: Dict[str, Any] = {
            "id": str(row.get("id") or "").removeprefix("t1_"),
            "score": score,
            "author": author,
            "body": body[:300],
            "excerpt": cut_quote(body),
            "permalink": permalink,
            "date": _epoch_to_date(row.get("created_utc")),
            "created_utc": row.get("created_utc"),
            "url": f"https://reddit.com{permalink}" if permalink else "",
            "depth": depth,
        }
        if isinstance(row.get("is_submitter"), bool):
            entry["is_uploader"] = row["is_submitter"]
        comments.append(entry)
    return comments, False


def fetch_comment_tree(
    post_id: str, deadline: Optional[float] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Comments of a post from the archive's ``/comments/tree``, flattened.

    Each comment carries id, score, author, body, excerpt, permalink, url,
    date, ``created_utc`` and ``depth`` (0 for a top-level comment), plus
    ``is_uploader`` when the archive supplies ``is_submitter``. Deleted
    and removed comments are left out. Returns ``None`` when the request
    failed, so a caller can tell a failure from a thread with no comments.
    """
    comments, _throttled = _fetch_comment_tree_with_status(post_id, deadline=deadline)
    return comments


_ORIG_FETCH_COMMENT_TREE = fetch_comment_tree


def _row_depth(row: Dict[str, Any]) -> int:
    """A tree row's depth; without one, a reply to the post itself is top-level."""
    try:
        return int(row["depth"])
    except (KeyError, TypeError, ValueError):
        return 0 if str(row.get("parent_id") or "t3_").startswith("t3_") else 1


def fetch_post_comments(
    post_id: str,
    limit: int = 10,
    skip_author: Optional[Callable[[str], bool]] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Top comments for a post from the arctic-shift archive, keyless.

    Drop-in fallback for ``reddit_shreddit.parse_comments`` output on hosts
    where the shreddit comment partials 403 (datacenter egress). Rows carry
    the same dict shape (score/author/body/excerpt/permalink/date/url), are
    ranked by score desc, and capped at ``limit``. ``skip_author`` drops rows
    before ranking (the caller's bot filter). ``deadline`` is an absolute
    ``time.monotonic()`` bound shared with the caller's budget. Best-effort, never raises:
    returns ``[]`` on any failure so the caller keeps whatever it had.
    """
    pid = reddit_refs.normalize_post_id(post_id)
    if not pid:
        return []
    now = time.monotonic()
    end = now + TIMEOUT if deadline is None else min(deadline, now + TIMEOUT)
    if end - now < 1:
        _log(f"comments lookup skipped t3_{pid}: enrichment budget spent")
        return []
    try:
        query = urlencode({"link_id": f"t3_{pid}", "limit": FALLBACK_COMMENT_ROWS})
        data = _search_once(f"{COMMENTS_TREE_API}?{query}", end)
    except Exception as e:  # network error / non-200 — degrade, never raise
        _log(f"comments lookup failed t3_{pid}: {e}")
        return []
    rows = (data or {}).get("data")
    if not isinstance(rows, list):
        _log(f"unexpected comments response for t3_{pid}: {str(data)[:80]}")
        return []

    from .snippet import cut_quote

    comments: List[Dict[str, Any]] = []
    for row in _tree_rows(rows):
        author = row.get("author") or "[deleted]"
        body = row.get("body") or ""
        if author in ("[deleted]", "[removed]") or body in ("[deleted]", "[removed]"):
            continue
        if skip_author and skip_author(author):
            continue
        try:
            score = int(row.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        permalink = row.get("permalink") or ""
        entry: Dict[str, Any] = {
            "score": score,
            "author": author,
            "body": body[:300],
            "excerpt": cut_quote(body),
            "permalink": permalink,
            "date": _epoch_to_date(row.get("created_utc")),
            "url": f"https://reddit.com{permalink}" if permalink else "",
            "depth": _row_depth(row),
        }
        if isinstance(row.get("is_submitter"), bool):
            entry["is_uploader"] = row["is_submitter"]
        comments.append(entry)
    comments.sort(key=lambda c: c.get("score", 0), reverse=True)
    return comments[:limit]
