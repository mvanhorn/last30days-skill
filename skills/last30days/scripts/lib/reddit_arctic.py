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

import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from . import http

API = "https://arctic-shift.photon-reddit.com/api/posts/ids"
SEARCH_API = "https://arctic-shift.photon-reddit.com/api/posts/search"
COMMENTS_API = "https://arctic-shift.photon-reddit.com/api/comments/search"
BATCH = 50          # ids per request
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
# Comment fallback: fetch a wide recency slice, then rank by score client-side
# (the archive's sort_type only supports created_utc).
MAX_COMMENT_ROWS = 100
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
MEMO_MAX = 512
RETRY_422_PAUSE = 1.5
_memo: Dict[str, Any] = {}
_inflight: Dict[str, threading.Event] = {}
_memo_lock = threading.Lock()


def _log(msg: str) -> None:
    sys.stderr.write(f"[ArcticShift] {msg}\n")
    sys.stderr.flush()


def reset_memo() -> None:
    """Forget memoized archive responses. Called once per command, and by tests."""
    with _memo_lock:
        _memo.clear()
        _inflight.clear()
    _cache.clear()


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
    todo: List[str] = []
    for pid in post_ids:
        if not pid:
            continue
        if pid in _cache:
            out[pid] = _cache[pid]
        elif pid not in todo:
            todo.append(pid)

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)][:MAX_BATCHES]
    for n, batch in enumerate(batches):
        if n:
            time.sleep(PACE_SECONDS)
        bound: Dict[str, Any] = {"timeout": TIMEOUT}
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _log(f"lookup stopped at its deadline; {len(batch)} ids left unscored")
                break
            bound = {"timeout": min(TIMEOUT, remaining), "retries": 1, "deadline_monotonic": deadline}
        try:
            data = http.get(
                f"{API}?ids={','.join(batch)}",
                headers={"User-Agent": http.BROWSER_USER_AGENT},
                **bound,
            )
        except Exception as e:  # network error / non-200 — degrade, never raise
            _log(f"lookup failed ({e}); {len(batch)} ids left unscored")
            break
        rows = (data or {}).get("data")
        if not isinstance(rows, list):
            # arctic-shift returns {"error": "..."} on rate-limit / bad request.
            _log(f"unexpected response (rate-limited?): {str(data)[:80]}")
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "").removeprefix("t3_")
            if not rid:
                continue
            try:
                entry = {
                    "score": int(row.get("score") or 0),
                    "num_comments": int(row.get("num_comments") or 0),
                }
            except (TypeError, ValueError):
                continue
            if len(_cache) < CACHE_MAX:
                _cache[rid] = entry
            out[rid] = entry
    return out


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
    window = bool(from_date or to_date)
    slices: Optional[List[tuple]] = None
    if window:
        from datetime import date as _date, timedelta as _timedelta
        try:
            start = _date.fromisoformat(from_date) if from_date else None
            end = _date.fromisoformat(to_date) if to_date else _date.today()
        except ValueError:
            start, end = None, None
        if end and not start:
            start = end - _timedelta(days=30)
        if start and end and start < end:
            span = (end - start).days
            n_slices = min(4, max(1, span // 7))
            step = max(1, -(-span // n_slices))
            slices = []
            cursor = start
            while cursor < end:
                slices.append((cursor, min(cursor + _timedelta(days=step), end)))
                cursor += _timedelta(days=step)
    req_limit = 100 if window else n
    posts_by_sub: Dict[str, List[Dict[str, Any]]] = {}
    for sub in subreddits:
        sub = sub.removeprefix("r/").strip()
        if sub and sub.lower() != "all":
            posts_by_sub.setdefault(sub, [])
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
        if span_pair:
            win = f"&after={span_pair[0].isoformat()}&before={span_pair[1].isoformat()}"
        else:
            win = f"&after={from_date}" if from_date else ""
            win += f"&before={to_date}" if to_date else ""
        return f"{SEARCH_API}?subreddit={sub}&limit={req_limit}&sort=desc{win}"

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
    pid = str(post_id or "").removeprefix("t3_")
    if not pid:
        return []
    now = time.monotonic()
    end = now + TIMEOUT if deadline is None else min(deadline, now + TIMEOUT)
    if end - now < 1:
        _log(f"comments lookup skipped t3_{pid}: enrichment budget spent")
        return []
    try:
        data = _search_once(
            f"{COMMENTS_API}?link_id=t3_{pid}&limit={MAX_COMMENT_ROWS}&sort=desc", end,
        )
    except Exception as e:  # network error / non-200 — degrade, never raise
        _log(f"comments lookup failed t3_{pid}: {e}")
        return []
    rows = (data or {}).get("data")
    if not isinstance(rows, list):
        _log(f"unexpected comments response for t3_{pid}: {str(data)[:80]}")
        return []

    from .snippet import cut_quote

    comments: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
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
        comments.append({
            "score": score,
            "author": author,
            "body": body[:300],
            "excerpt": cut_quote(body),
            "permalink": permalink,
            "date": _epoch_to_date(row.get("created_utc")),
            "url": f"https://reddit.com{permalink}" if permalink else "",
        })
    comments.sort(key=lambda c: c.get("score", 0), reverse=True)
    return comments[:limit]
