"""Xquik X search source for the v3.0.0 last30days pipeline.

Uses the Xquik REST API (https://xquik.com/api/v1) to search X/Twitter
with full engagement metrics (likes, retweets, replies, quotes, views,
bookmarks). Requires an API key from xquik.com.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from . import http, log
from .relevance import token_overlap_relevance as _compute_relevance

# Per-process probe cache: (state, reason). state is "unset" until probed, then
# True (funded/working) | False (auth/payment failure) | None (inconclusive).
_probe_cache: tuple = ("unset", "")

# Depth configurations: number of results to request per query
DEPTH_CONFIG = {
    "quick": {"limit": 10, "queries": 1},
    "default": {"limit": 20, "queries": 2},
    "deep": {"limit": 40, "queries": 3},
}

_BASE_URL = "https://xquik.com/api/v1"


def _log(msg: str):
    log.source_log("Xquik", msg, tty_only=False)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject for X search queries."""
    from .query import extract_core_subject
    return extract_core_subject(topic, max_words=5, strip_suffixes=True)


def expand_xquik_queries(topic: str, depth: str) -> List[str]:
    """Generate query variants based on depth.

    Args:
        topic: Research topic
        depth: "quick", "default", or "deep"

    Returns:
        List of query strings (1 for quick, 2 for default, 3 for deep).
    """
    core = _extract_core_subject(topic)
    # Anti-bare-generic guard (#607): never let the core collapse to a single
    # bare token when the topic carries more — a lone generic word floods X with
    # off-topic collisions. Fall back to the full multi-word topic as the anchor.
    topic_clean = topic.strip()
    if len(core.split()) <= 1 and len(topic_clean.split()) > 1 and core.lower() != topic_clean.lower():
        core = topic_clean
    queries = [core]

    # Add original topic if meaningfully different
    if topic.lower().strip() != core.lower().strip():
        queries.append(topic.strip())

    # Add compound term variant for deep searches
    if len(queries) < 3:
        from .query import extract_compound_terms
        compounds = extract_compound_terms(topic)
        if compounds:
            or_parts = " OR ".join(f'"{t}"' for t in compounds[:3])
            queries.append(f"({or_parts})")

    cap = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])["queries"]
    return queries[:cap]


def search_xquik(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
) -> Dict[str, Any]:
    """Search X via Xquik REST API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        token: Xquik API key

    Returns:
        Dict with "items" list and optional "error" string.
    """
    if not token:
        return {"items": [], "error": "No XQUIK_API_KEY configured"}

    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    queries = expand_xquik_queries(topic, depth)
    all_items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for query_text in queries:
        q = f"{query_text} since:{from_date} until:{to_date}"
        items, auth_error = _execute_search(
            q, cfg["limit"], token,
            label=query_text, id_prefix="XQ",
            seen_ids=seen_ids, relevance_query=query_text,
            index_offset=len(all_items),
        )
        if auth_error:
            # Auth/payment failure is fatal for the whole source (e.g. 401/403,
            # and 402-unpaid surfaced via U5 diagnose) — return it so the caller
            # settles honestly instead of silently empty.
            return {"items": [], "error": auth_error}
        all_items.extend(items)

    return {"items": all_items}


def _execute_search(
    q: str,
    limit: int,
    token: str,
    *,
    label: str,
    id_prefix: str,
    seen_ids: set[str],
    relevance_query: str,
    index_offset: int = 0,
) -> tuple[List[Dict[str, Any]], str | None]:
    """Run one Xquik search call and parse its tweets.

    Returns ``(items, auth_error)``. ``auth_error`` is a non-empty string only
    on a fatal auth/payment failure (401/403); transient/HTTP errors log and
    return ``([], None)`` so one bad lane never discards another's results.
    ``relevance_query`` (the topic) is what items are scored against — for the
    handle lanes that differs from the search query (``from:handle``).
    ``index_offset`` keeps item ids unique across multiple calls that share an
    accumulator (multi-query topic search, per-handle lanes).
    """
    full_url = f"{_BASE_URL}/x/tweets/search?q={_url_encode(q)}&queryType=Top&limit={limit}"
    _log(f"Searching: {label}")
    try:
        request_headers = {"X-Api-Key": token}
        response = http.get(full_url, headers=request_headers, timeout=30, retries=2)
    except http.HTTPError as exc:
        status = getattr(exc, "status_code", None)
        if status == 402:
            # Unpaid key — fatal for the source, and surfaced on the real search
            # path (not just --diagnose) so a live run reports it instead of
            # settling silently empty.
            return [], "Xquik key unpaid (402)"
        if status in (401, 403):
            return [], f"Xquik auth failed ({status})"
        _log(f"HTTP error for '{label}': {exc}")
        return [], None
    except Exception as exc:
        _log(f"Error for '{label}': {exc}")
        return [], None

    tweets = response.get("tweets", [])
    if not isinstance(tweets, list):
        return [], None
    items: List[Dict[str, Any]] = []
    for tweet in tweets:
        if not isinstance(tweet, dict):
            continue
        tweet_id = str(tweet.get("id", ""))
        if tweet_id in seen_ids:
            continue
        seen_ids.add(tweet_id)
        item = _parse_tweet(tweet, index_offset + len(items), relevance_query, id_prefix=id_prefix)
        if item:
            items.append(item)
    return items, None


def _is_own(url: str, handle: str) -> bool:
    """True when a tweet URL is authored by ``handle`` (their own post).

    Used by the ABOUT lane to drop the subject's own tweets so only mentions
    *by others* remain. Handles both x.com and twitter.com permalinks.
    """
    u = (url or "").lower()
    h = handle.lower().lstrip("@").strip()
    return bool(h) and (f"x.com/{h}/status" in u or f"twitter.com/{h}/status" in u)


def search_handles(
    handles: List[str],
    topic: str,
    from_date: str,
    to_date: str,
    *,
    count_per: int = 8,
    token: str = "",
) -> List[Dict[str, Any]]:
    """FROM lane: tweets authored BY each handle (their own timeline).

    The topic is NOT AND'd into the query (that was the from:-AND bug, #610) —
    we pull the raw timeline and use ``topic`` for relevance ranking only.
    Returns a flat list of item dicts (mirrors ``bird_x.search_handles``).
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in handles:
        handle = str(raw).lstrip("@").strip()
        if not handle:
            continue
        q = f"from:{handle} since:{from_date} until:{to_date}"
        got, auth_error = _execute_search(
            q, count_per, token,
            label=f"from:{handle}", id_prefix="XF",
            seen_ids=seen_ids, relevance_query=topic,
            index_offset=len(items),
        )
        if auth_error:
            break  # fatal auth/payment failure — stop, keep what we have
        items.extend(got)
    return items


def search_mentions(
    handles: List[str],
    from_date: str,
    to_date: str,
    *,
    topic: str = "",
    count_per: int = 5,
    token: str = "",
) -> List[Dict[str, Any]]:
    """ABOUT lane: tweets mentioning each handle, authored by OTHERS.

    Queries ``@handle`` then drops the handle's own tweets (``_is_own``) so only
    third-party mentions remain. Returns a flat list of item dicts.
    """
    if not token or not handles:
        return []
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in handles:
        handle = str(raw).lstrip("@").strip()
        if not handle:
            continue
        q = f"@{handle} since:{from_date} until:{to_date}"
        got, auth_error = _execute_search(
            q, count_per, token,
            label=f"@{handle}", id_prefix="XA",
            seen_ids=seen_ids, relevance_query=topic,
            index_offset=len(items),
        )
        if auth_error:
            break
        items.extend(it for it in got if not _is_own(it.get("url", ""), handle))
    return items


def probe_works(token: str, timeout: int = 8) -> Optional[bool]:
    """Cheap runtime check that the xquik key actually returns data.

    Mirrors ``bird_x.probe_works`` for the key-based X path so ``--diagnose``
    reflects reality instead of static key presence. Returns True
    (funded/working), False (a clear auth/payment failure — 401/403, or 402
    when the key is configured but unpaid), or None (inconclusive: timeout /
    transient HTTP) so callers fail open.
    The human-readable reason is available via ``probe_reason()``. Cached per
    process so repeated diagnose calls don't re-probe.
    """
    global _probe_cache
    if _probe_cache[0] != "unset":
        return _probe_cache[0]
    if not token:
        _probe_cache = (False, "no XQUIK_API_KEY configured")
        return False
    from datetime import timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    # @x (the platform's own account) posts frequently, so a no-error response
    # means the key works even if this particular window is quiet.
    q = f"from:x since:{since}"
    full_url = f"{_BASE_URL}/x/tweets/search?q={_url_encode(q)}&queryType=Top&limit=1"
    try:
        request_headers = {"X-Api-Key": token}
        http.get(full_url, headers=request_headers, timeout=timeout, retries=0)
    except http.HTTPError as exc:
        status = getattr(exc, "status_code", None)
        if status == 402:
            _probe_cache = (False, "xquik key unpaid (402)")
        elif status in (401, 403):
            _probe_cache = (False, f"xquik auth failed ({status})")
        else:
            # 5xx / unexpected status — inconclusive, don't report a false-down.
            _probe_cache = (None, f"xquik probe inconclusive ({status})")
        return _probe_cache[0]
    except Exception as exc:
        _probe_cache = (None, f"xquik probe inconclusive ({type(exc).__name__})")
        return None
    _probe_cache = (True, "ok")
    return True


def probe_reason() -> str:
    """Human-readable reason for the last ``probe_works`` result (or '')."""
    return _probe_cache[1]


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
) -> Dict[str, Any]:
    """Search X via Xquik and return results.

    Xquik API returns full engagement data by default, so no separate
    enrichment step is needed.
    """
    return search_xquik(topic, from_date, to_date, depth=depth, token=token)


def parse_xquik_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract items from search response.

    Args:
        response: Response dict from search_xquik()

    Returns:
        List of normalized item dicts.
    """
    return response.get("items", [])


def _parse_tweet(
    tweet: Dict[str, Any], index: int, query: str, id_prefix: str = "XQ"
) -> Dict[str, Any] | None:
    """Parse a single tweet from the API response into the standard item format."""
    author = tweet.get("author") or {}
    username = str(author.get("username", "")).lstrip("@")
    tweet_id = str(tweet.get("id", ""))

    # Build URL
    url = ""
    if username and tweet_id:
        url = f"https://x.com/{username}/status/{tweet_id}"
    if not url:
        return None

    # Parse date
    date = None
    created_at = tweet.get("createdAt") or ""
    if created_at:
        try:
            if len(created_at) > 10 and created_at[10] == "T":
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
            date = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass

    text = str(tweet.get("text", "")).strip()[:500]

    # Leading-run @mentions = who the post is directed at (reply target). Shared
    # parser with bird so the first-party interaction signal fires for xquik too.
    from .query import leading_mentions
    mentioned_handles = leading_mentions(text)

    # Build engagement dict with full metrics
    engagement = {
        "likes": _safe_int(tweet.get("likeCount")),
        "reposts": _safe_int(tweet.get("retweetCount")),
        "replies": _safe_int(tweet.get("replyCount")),
        "quotes": _safe_int(tweet.get("quoteCount")),
        "views": _safe_int(tweet.get("viewCount")),
        "bookmarks": _safe_int(tweet.get("bookmarkCount")),
    }

    return {
        "id": f"{id_prefix}{index + 1}",
        "text": text,
        "url": url,
        "author_handle": username,
        "date": date,
        "engagement": engagement,
        "mentioned_handles": mentioned_handles,
        "relevance": _compute_relevance(query, text) if query else 0.7,
        "why_relevant": "",
    }


def _safe_int(value: Any) -> int | None:
    """Convert value to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _url_encode(text: str) -> str:
    """URL-encode a string using stdlib."""
    from urllib.parse import quote
    return quote(text, safe="")
