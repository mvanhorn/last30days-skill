"""Arctic-shift score resolver — post upvote counts by id, keyless and free.

``search.json`` and ``/comments/{id}.json`` are 403 keyless, and ``search.rss``
(used for discovery) carries titles but NO score; the shreddit listing partials
score only posts that appear in a pulled listing. For a thread found only via
global RSS search in a broad sub, the free score comes from arctic-shift
(https://arctic-shift.photon-reddit.com), a public Reddit archive whose
``/api/posts/ids`` returns the post object (score, num_comments, title) for a
batch of base36 post ids. Scores are point-in-time snapshots — slightly stale vs
live, which is fine for ranking and display.

Best-effort, never raises. On rate-limit (HTTP 422 "slow down"), error, or an
unreachable host it returns ``{}`` so the caller shows the thread without a point
count rather than failing the Reddit source.
"""

import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import http

API = "https://arctic-shift.photon-reddit.com/api/posts/ids"
SEARCH_API = "https://arctic-shift.photon-reddit.com/api/posts/search"
BATCH = 50          # ids per request
TIMEOUT = 15
MAX_BATCHES = 3     # cap total requests per run (bounds latency + rate-limit risk)
PACE_SECONDS = 0.4  # gap between batches; arctic-shift answers 422 "slow down"
CACHE_MAX = 4096    # hard size bound so the in-run memo can never grow unbounded
# Listing-lane knobs. Base limits mirror reddit_listing's DEPTH_LIMITS so callers
# get the same per-depth volume. The supplement multiplier is applied when the
# caller requested multiple sorts (top/hot/new) — arctic-shift has no sort lanes,
# so we fetch more posts to increase the chance of covering what the failed
# shreddit lanes would have returned.
#
# KNOWN LIMITATION: Arctic-shift is recency-only (sort=desc). It has no top/hot/
# new/rising lanes — failed shreddit sort lanes are supplemented with recent
# posts, not lane-specific results. This is a fundamental backend constraint.
_LISTING_DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}
_LISTING_SUPPLEMENT_MULTIPLIER = 2  # fetch 2x posts when supplementing multi-sort requests
# Total deadline for listing fetches to prevent unbounded stalls when many
# subreddits are requested and arctic is slow/unreachable.
_LISTING_DEADLINE_SECONDS = 45  # ~3 subs at 15s timeout each
# In-run memo: base36 id -> {score, num_comments}. Module-level so repeated
# fetch_scores calls within one `/last30days` run (e.g. across subqueries) reuse
# results, but capped at CACHE_MAX entries (never reached in a normal CLI run).
# Tests clear it via reddit_arctic._cache.clear().
_cache: Dict[str, Dict[str, int]] = {}


def _log(msg: str) -> None:
    sys.stderr.write(f"[ArcticShift] {msg}\n")
    sys.stderr.flush()


def fetch_scores(post_ids: List[str]) -> Dict[str, Dict[str, int]]:
    """Return ``{base36_post_id: {"score", "num_comments"}}`` for the given ids.

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
        try:
            data = http.get(
                f"{API}?ids={','.join(batch)}",
                headers={"User-Agent": http.BROWSER_USER_AGENT},
                timeout=TIMEOUT,
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
) -> List[Dict[str, Any]]:
    """Scored subreddit listings from the arctic-shift archive, keyless.

    Drop-in fallback/supplement for ``reddit_listing.fetch_listings`` (shreddit
    partials), which datacenter IPs get HTTP 403 on. Arctic-shift serves recent
    posts with real score/num_comments from any IP.

    Arctic-shift has no top/hot/new lanes, only recency. When ``sorts`` contains
    multiple entries (e.g., dedicated lanes requesting top+hot+new), we fetch
    more posts per subreddit to partially compensate for the missing lane
    coverage — the caller's engagement ranking does the final sorting.

    Best-effort, never raises: returns ``[]`` on any failure.
    """
    if not subreddits:
        return []
    base = limit or _LISTING_DEPTH_LIMITS.get(depth, _LISTING_DEPTH_LIMITS["default"])
    # When multiple sorts were requested, fetch more posts to compensate for
    # arctic-shift's lack of sort lanes.
    n = base * _LISTING_SUPPLEMENT_MULTIPLIER if sorts and len(sorts) > 1 else base
    out: List[Dict[str, Any]] = []
    # Process all requested subreddits with pacing and a total deadline to
    # prevent unbounded stalls when arctic is slow or unreachable.
    deadline = time.time() + _LISTING_DEADLINE_SECONDS
    fetched_count = 0
    for sub in subreddits:
        if time.time() >= deadline:
            _log(f"listing deadline reached after {fetched_count} subs; skipping remaining")
            break
        sub = sub.removeprefix("r/").strip()
        if not sub or sub.lower() == "all":
            continue
        if fetched_count:
            time.sleep(PACE_SECONDS)
        fetched_count += 1
        try:
            # Use retries=1 (single attempt) so retries don't exceed our deadline.
            # The deadline handles overall timing; per-request retries would
            # multiply the delay unpredictably.
            data = http.get(
                f"{SEARCH_API}?subreddit={sub}&limit={n}&sort=desc",
                headers={"User-Agent": http.BROWSER_USER_AGENT},
                timeout=TIMEOUT,
                retries=1,
            )
        except Exception as e:  # network error / non-200 — degrade, never raise
            _log(f"listing search failed r/{sub}: {e}")
            continue
        rows = (data or {}).get("data")
        if not isinstance(rows, list):
            _log(f"unexpected listing response for r/{sub}: {str(data)[:80]}")
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            post = _normalize_listing_row(row, query)
            if post["url"]:
                out.append(post)

    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for p in out:
        if p["url"] not in seen:
            seen.add(p["url"])
            unique.append(p)
    return unique
