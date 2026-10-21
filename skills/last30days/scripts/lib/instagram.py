"""Instagram Reels search via ScrapeCreators API for /last30days.

Uses ScrapeCreators REST API to search Instagram Reels by keyword, extract
engagement metrics (views, likes, comments), and fetch video transcripts.

Requires SCRAPECREATORS_API_KEY in config. 100 free API calls, then PAYG.
API docs: https://scrapecreators.com/docs
"""

import os
import re
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

from . import dates, http, log
from .query import infer_query_intent
from .relevance import token_overlap_relevance as _compute_relevance

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"

# Depth configurations: how many results to fetch / captions to extract
DEPTH_CONFIG = {
    "quick":   {"results_per_page": 10, "max_captions": 3},
    "default": {"results_per_page": 20, "max_captions": 5},
    "deep":    {"results_per_page": 40, "max_captions": 8},
}

# Keep the Google discovery fallback bounded because each post detail request
# costs one credit. The page/post totals match the fallback's observed depth
# budgets: 1 + 9, 2 + 16, and 3 + 27 requests.
GOOGLE_FALLBACK_BUDGETS = {
    "quick": (1, 9),
    "default": (2, 16),
    "deep": (3, 27),
}

# Max words to keep from each caption
CAPTION_MAX_WORDS = 500
RESEARCH_TIMEOUT = "Instagram research timed out or was cancelled; returning collected reels"

# Default transcript fetch timeout (seconds). SC's
# /v2/instagram/media/transcript regularly takes >15s on real workloads,
# so the default is generous; override via LAST30DAYS_TRANSCRIPT_TIMEOUT.
DEFAULT_TRANSCRIPT_TIMEOUT = 30


def _resolve_transcript_timeout(
    timeout: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    """Resolve the IG transcript-fetch timeout.

    Priority (highest wins):
      1. Explicit ``timeout`` kwarg
      2. ``LAST30DAYS_TRANSCRIPT_TIMEOUT`` in os.environ
      3. ``LAST30DAYS_TRANSCRIPT_TIMEOUT`` in caller-supplied config dict
      4. ``DEFAULT_TRANSCRIPT_TIMEOUT`` (30s)

    Mirrors the ``os.environ.get(X) or config.get(X)`` pattern used for
    LAST30DAYS_STORE in last30days.py so the env var works whether it's
    shell-exported or set in ~/.config/last30days/.env.
    """
    if timeout is not None:
        try:
            return float(timeout)
        except (TypeError, ValueError):
            pass
    raw = os.environ.get("LAST30DAYS_TRANSCRIPT_TIMEOUT")
    if not raw and config:
        raw = config.get("LAST30DAYS_TRANSCRIPT_TIMEOUT")
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return float(DEFAULT_TRANSCRIPT_TIMEOUT)


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for Instagram search."""
    from .query import VIRAL_NOISE, extract_core_subject
    return extract_core_subject(topic, noise=VIRAL_NOISE)


def _to_hashtag_form(query: str) -> str:
    """Collapse a multi-word query to hashtag form (no spaces, lowercase).

    SC's /v2/instagram/reels/search wraps Google Search and is documented
    to be flaky on multi-token queries. Single-token queries map to a
    hashtag page lookup which is the stable path. Used as a 500-retry
    fallback before the request bubbles up as a silent failure.
    """
    return ''.join(query.split()).lower()


def expand_instagram_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple Instagram search queries from a topic.

    Mirrors reddit.py's expand_reddit_queries() pattern:
    1. Extract core subject (strip noise words)
    2. Include original topic if different from core
    3. Add intent-specific OR-joined content-type variants
    4. Cap by depth: 1 for quick, 2 for default, 3 for deep

    Returns 1-3 query strings depending on depth.
    """
    core = _extract_core_subject(topic)
    queries = [core]

    # Include cleaned original topic as variant if different from core
    original_clean = topic.strip().rstrip('?!.')
    if core.lower() != original_clean.lower() and len(original_clean.split()) <= 8:
        queries.append(original_clean)

    qtype = infer_query_intent(topic)

    # Intent-specific Instagram content-type variants
    if qtype == "breaking_news":
        queries.append(f"{core} reaction OR edit")
    elif qtype == "opinion":
        queries.append(f"{core} reaction OR edit")
    elif qtype == "product":
        queries.append(f"{core} review OR haul")
    elif qtype == "comparison":
        queries.append(f"{core} vs OR compared")
    elif qtype == "how_to":
        queries.append(f"{core} tutorial OR hack")
    else:
        queries.append(f"{core} reaction OR edit")

    # Deep depth: add viral content variant
    if depth == "deep":
        queries.append(f"{core} viral OR trending OR reel")

    # Cap by depth budget
    caps = {"quick": 1, "default": 2, "deep": 3}
    cap = caps.get(depth, 2)
    return queries[:cap]


def _log(msg: str):
    log.source_log("Instagram", msg, tty_only=False)


def _budget_expired(deadline: float | None, cancel: threading.Event | None) -> bool:
    return bool(
        (cancel is not None and cancel.is_set())
        or (deadline is not None and time.monotonic() >= deadline)
    )


def _request_options(timeout: float, deadline: float | None, cancel: threading.Event | None) -> dict:
    options = {"timeout": timeout}
    if deadline is not None:
        options["timeout"] = min(timeout, max(0.001, deadline - time.monotonic()))
        options["deadline_monotonic"] = deadline
    if cancel is not None:
        options["cancel"] = cancel
    return options


def _parse_date(item: Dict[str, Any]) -> Optional[str]:
    """Parse date from ScrapeCreators Instagram item to YYYY-MM-DD.

    Handles taken_at as ISO string (e.g. "2026-02-26T16:00:00.000Z")
    or unix timestamp.
    """
    ts = item.get("taken_at")
    if not ts:
        return None

    # Try ISO string first (ScrapeCreators reels/search returns this)
    if isinstance(ts, str):
        try:
            # Handle "2026-02-26T16:00:00.000Z" format
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
        # Try just the date portion
        if len(ts) >= 10:
            return ts[:10]

    # Fall back to unix timestamp
    try:
        return dates.timestamp_to_date(int(ts))
    except (ValueError, TypeError):
        pass

    return None


def _extract_hashtags(caption_text: str) -> List[str]:
    """Extract hashtags from Instagram caption text."""
    if not caption_text:
        return []
    return re.findall(r'#(\w+)', caption_text)


def _parse_items(raw_items: List[Dict[str, Any]], core_topic: str) -> List[Dict[str, Any]]:
    """Parse raw Instagram items into normalized dicts."""
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        # Extract reel ID and shortcode
        reel_pk = str(raw.get("id", raw.get("pk", "")))
        shortcode = raw.get("shortcode", raw.get("code", ""))

        # Caption text -- can be a string or dict depending on endpoint
        caption_obj = raw.get("caption", "")
        if isinstance(caption_obj, dict):
            text = caption_obj.get("text", "")
        elif isinstance(caption_obj, str):
            text = caption_obj
        else:
            text = raw.get("desc", raw.get("text", ""))

        # Engagement metrics
        play_count = raw.get("video_play_count") or raw.get("video_view_count") or raw.get("play_count") or 0
        like_count = raw.get("like_count") or 0
        comment_count = raw.get("comment_count") or 0

        # Author info -- 'owner' in reels/search, 'user' in user/reels
        owner_raw = raw.get("owner") or raw.get("user")
        if isinstance(owner_raw, dict):
            author_name = owner_raw.get("username", "")
        elif isinstance(owner_raw, str):
            author_name = owner_raw
        else:
            author_name = ""

        # Duration
        duration = raw.get("video_duration")

        # Date
        date_str = _parse_date(raw)

        # Hashtags from caption text
        hashtags = _extract_hashtags(text)

        # Compute relevance with hashtag boost
        relevance = _compute_relevance(core_topic, text, hashtags)

        # Build URL -- prefer API-provided url, fallback to shortcode
        url = raw.get("url", "")
        if not url and shortcode:
            url = f"https://www.instagram.com/reel/{shortcode}"

        items.append({
            "video_id": reel_pk,
            "text": text,
            "url": url,
            "author_name": author_name,
            "date": date_str,
            "engagement": {
                "views": play_count,
                "likes": like_count,
                "comments": comment_count,
            },
            "hashtags": hashtags,
            "duration": duration,
            "relevance": relevance,
            "why_relevant": f"Instagram: {text[:60]}" if text else f"Instagram: {core_topic}",
            "caption_snippet": "",  # populated by fetch_captions
        })
    return items


def _user_reels(
    handle: str,
    token: str,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Fetch an Instagram user's recent reels via ScrapeCreators.

    Args:
        handle: Instagram username (without @)
        token: ScrapeCreators API key

    Returns:
        List of raw Instagram reel dicts.
    """
    if _budget_expired(deadline, cancel):
        return []
    _log(f"User reels: @{handle}")
    reels_url = f"{SCRAPECREATORS_BASE}/v1/instagram/user/reels"
    try:
        data = http.get(
            reels_url,
            params={"handle": handle},
            headers=http.scrapecreators_headers(token),
            retries=2,
            **_request_options(30, deadline, cancel),
        )
    except Exception as e:
        _log(f"User reels error for @{handle}: {e}")
        return []

    raw_items = data.get("items") or data.get("reels") or data.get("data") or []
    _log(f"  -> {len(raw_items)} reels from @{handle}")

    # User-reels responses wrap each reel in a ``media`` envelope, while
    # other Instagram endpoints already return flat reel dictionaries.
    return [
        item["media"]
        if isinstance(item, dict) and isinstance(item.get("media"), dict)
        else item
        for item in raw_items
    ]


def _normalize_google_post(
    payload: dict[str, Any], url: str
) -> dict[str, Any] | None:
    """Convert a ScrapeCreators post detail response to the reels/search shape."""
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    media = data.get("xdt_shortcode_media")
    if not isinstance(media, dict):
        return None

    caption = media.get("caption")
    if not caption:
        edges = (media.get("edge_media_to_caption") or {}).get("edges") or []
        if edges and isinstance(edges[0], dict):
            caption = (edges[0].get("node") or {}).get("text", "")

    likes = (media.get("edge_media_preview_like") or {}).get("count")
    comments = (media.get("edge_media_to_parent_comment") or {}).get("count")
    if comments is None:
        comments = (media.get("edge_media_to_comment") or {}).get("count")

    return {
        "id": media.get("id", ""),
        "shortcode": media.get("shortcode", ""),
        "url": url,
        "caption": caption or "",
        "taken_at": media.get("taken_at_timestamp") or media.get("created_at"),
        "video_play_count": media.get("video_play_count") or 0,
        "like_count": likes or 0,
        "comment_count": comments or 0,
        "owner": media.get("owner") or media.get("user") or {},
    }


def _google_reel_fallback(
    topic: str,
    depth: str,
    token: str,
    from_date: str,
    to_date: str,
    budget: dict[str, int] | None = None,
    detail_cache: dict[str, dict[str, Any]] | None = None,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """Discover Instagram reels through Google and fetch their post details."""
    if budget is None:
        pages, max_posts = GOOGLE_FALLBACK_BUDGETS.get(
            depth, GOOGLE_FALLBACK_BUDGETS["default"]
        )
        budget = {"pages": pages, "posts": max_posts}
    pages = max(0, budget.get("pages", 0))
    max_posts = max(0, budget.get("posts", 0))
    if not pages or not max_posts:
        return []
    headers = http.scrapecreators_headers(token)
    detail_cache = detail_cache if detail_cache is not None else {}
    shortcode_urls: dict[str, str] = {}
    ordered_shortcodes: list[str] = []
    end_exclusive = (
        datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    for page in range(1, pages + 1):
        if _budget_expired(deadline, cancel):
            break
        budget["pages"] -= 1
        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/v1/google/search",
                params={
                    "query": (
                        f"site:instagram.com/reel {topic} "
                        f"after:{from_date} before:{end_exclusive}"
                    ),
                    "page": page,
                },
                headers=headers,
                retries=1,
                **_request_options(30, deadline, cancel),
            )
        except Exception as e:
            _log(f"Google Instagram fallback search failed on page {page}: {e}")
            break
        if not isinstance(data, dict):
            _log(f"Google Instagram fallback returned invalid data on page {page}")
            break

        results = data.get("results") or data.get("items") or []
        if not isinstance(results, list):
            _log(f"Google Instagram fallback returned invalid results on page {page}")
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            url = result.get("url", "")
            try:
                parsed_url = urlsplit(url) if isinstance(url, str) else None
            except ValueError:
                continue
            hostname = parsed_url.hostname.lower() if parsed_url and parsed_url.hostname else ""
            is_instagram = hostname == "instagram.com" or hostname.endswith(".instagram.com")
            is_reel_path = bool(
                parsed_url
                and re.fullmatch(r"/reel/[A-Za-z0-9_-]+/?", parsed_url.path)
            )
            shortcode_match = (
                re.fullmatch(r"/reel/([A-Za-z0-9_-]+)/?", parsed_url.path)
                if parsed_url
                else None
            )
            if (
                parsed_url
                and parsed_url.scheme == "https"
                and is_instagram
                and is_reel_path
            ):
                shortcode = shortcode_match.group(1)
                if shortcode not in shortcode_urls and shortcode not in detail_cache:
                    if len(shortcode_urls) >= max_posts:
                        continue
                    shortcode_urls[shortcode] = url
                if shortcode not in ordered_shortcodes:
                    ordered_shortcodes.append(shortcode)
                if len(shortcode_urls) >= max_posts:
                    break
        if len(shortcode_urls) >= max_posts:
            break

    budget["posts"] -= len(shortcode_urls)

    def fetch_post(url: str) -> dict[str, Any] | None:
        if _budget_expired(deadline, cancel):
            return None
        try:
            payload = http.get(
                f"{SCRAPECREATORS_BASE}/v1/instagram/post",
                params={"url": url},
                headers=headers,
                retries=1,
                **_request_options(30, deadline, cancel),
            )
        except Exception as e:
            _log(f"Instagram post detail failed for {url}: {e}")
            return None
        try:
            return _normalize_google_post(payload, url)
        except (AttributeError, TypeError, ValueError) as e:
            _log(f"Instagram post detail had an invalid response for {url}: {e}")
            return None

    if shortcode_urls:
        executor = ThreadPoolExecutor(max_workers=min(4, len(shortcode_urls)))
        try:
            futures = {
                http.submit_with_context(executor, fetch_post, url): shortcode
                for shortcode, url in shortcode_urls.items()
            }
            pending = set(futures)
            while pending:
                remaining = 0.05 if deadline is None else max(0, min(0.05, deadline - time.monotonic()))
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                for future in done:
                    reel = future.result()
                    if reel:
                        detail_cache[futures[future]] = reel
                if _budget_expired(deadline, cancel):
                    break
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    reels = []
    for shortcode in ordered_shortcodes:
        reel = detail_cache.get(shortcode)
        if reel:
            reels.append(reel)
    return reels


def search_instagram(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    _fallback_budget: dict[str, int] | None = None,
    _fallback_cache: dict[str, dict[str, Any]] | None = None,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Search Instagram Reels via ScrapeCreators API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    if not token:
        return {"items": [], "error": "No SCRAPECREATORS_API_KEY configured"}
    if _budget_expired(deadline, cancel):
        return {"items": [], "error": RESEARCH_TIMEOUT}

    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching Instagram for '{core_topic}' (depth={depth}, count={config['results_per_page']})")

    primary_error = None
    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/v2/instagram/reels/search",
            params={"query": core_topic},
            headers=http.scrapecreators_headers(token),
            retries=2,
            **_request_options(30, deadline, cancel),
        )
    except http.HTTPError as e:
        # SC's v2 reels search wraps Google Search and 500s frequently on
        # multi-token queries. Single tokens hit the stable hashtag-page
        # path. Retry once with hashtag form before bubbling up.
        if (getattr(e, "status_code", None) == 500 and ' ' in core_topic
                and not _budget_expired(deadline, cancel)):
            _log(f"IG search 500 on '{core_topic}', retrying with hashtag form")
            try:
                data = http.get(
                    f"{SCRAPECREATORS_BASE}/v2/instagram/reels/search",
                    params={"query": _to_hashtag_form(core_topic)},
                    headers=http.scrapecreators_headers(token),
                    retries=2,
                    **_request_options(30, deadline, cancel),
                )
            except Exception as retry_e:
                if (
                    isinstance(retry_e, http.HTTPError)
                    and getattr(retry_e, "status_code", None) == 404
                ):
                    primary_error = retry_e
                    data = {"reels": []}
                else:
                    _log(f"IG search retry failed: {retry_e}")
                    return {"items": [], "error": f"{type(retry_e).__name__}: {retry_e}"}
        elif getattr(e, "status_code", None) == 404:
            # A 404 is ScrapeCreators' current response for both empty searches
            # and the reported reels-search outage. Try the independent Google
            # discovery + post-detail lane before treating Instagram as failed.
            primary_error = e
            data = {"reels": []}
        else:
            _log(f"ScrapeCreators error: {e}")
            return {"items": [], "error": f"{type(e).__name__}: {e}"}
    except Exception as e:
        _log(f"ScrapeCreators error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}"}

    # Items are in the 'reels' array (ScrapeCreators v2 response)
    raw_items = data.get("reels") or data.get("items") or data.get("data") or []

    used_google_fallback = not raw_items
    if used_google_fallback:
        if primary_error:
            _log("IG reels search returned 404; trying Google Search fallback")
        else:
            _log("IG reels search returned no results; trying Google Search fallback")
        raw_items = _google_reel_fallback(
            core_topic, depth, token, from_date, to_date,
            _fallback_budget, _fallback_cache,
            deadline=deadline, cancel=cancel,
        )

    if not raw_items and primary_error and not _budget_expired(deadline, cancel):
        _log(f"Google Instagram fallback returned no reels: {primary_error}")
        return {"items": [], "error": f"{type(primary_error).__name__}: {primary_error}"}

    # Limit to configured count
    raw_items = raw_items[:config["results_per_page"]]

    # Parse items
    items = _parse_items(raw_items, core_topic)

    # Hard date filter
    in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
    out_of_range = len(items) - len(in_range)
    if used_google_fallback:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} Google reels outside date range")
    elif in_range:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} reels outside date range")
    else:
        _log(f"No reels within date range, keeping all {len(items)}")

    # Sort by views descending
    items.sort(key=lambda x: x["engagement"]["views"], reverse=True)

    _log(f"Found {len(items)} Instagram reels")
    if _budget_expired(deadline, cancel):
        return {"items": items, "error": RESEARCH_TIMEOUT}
    return {"items": items}


def fetch_captions(
    video_items: List[Dict[str, Any]],
    token: str,
    depth: str = "default",
    timeout: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, str]:
    """Fetch transcripts for top N Instagram reels via ScrapeCreators.

    Strategy:
    1. Use the 'text' field (caption) as baseline
    2. For top N, call /v2/instagram/media/transcript for spoken-word captions

    Args:
        video_items: Items from search_instagram()
        token: ScrapeCreators API key
        depth: Depth level for caption limit
        timeout: Optional per-request transcript timeout in seconds. When
            None, resolves from LAST30DAYS_TRANSCRIPT_TIMEOUT (env or
            config), defaulting to DEFAULT_TRANSCRIPT_TIMEOUT (30s).
        config: Optional config dict (from env.get_config()) used as a
            fallback source for LAST30DAYS_TRANSCRIPT_TIMEOUT when the
            value is not exported in os.environ.

    Returns:
        Dict mapping video_id -> caption text (truncated to 500 words)
    """
    depth_cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_captions = depth_cfg["max_captions"]
    transcript_timeout = _resolve_transcript_timeout(timeout, config)

    if not video_items or not token:
        return {}

    top_items = video_items[:max_captions]
    _log(f"Enriching captions for {len(top_items)} reels")

    captions = {}

    # First pass: use text field as caption (always available, free)
    for item in top_items:
        vid = item["video_id"]
        text = item.get("text", "")
        if text:
            words = text.split()
            if len(words) > CAPTION_MAX_WORDS:
                text = ' '.join(words[:CAPTION_MAX_WORDS]) + '...'
            captions[vid] = text

    # Second pass: try to get spoken-word transcripts (1 credit each)
    for item in top_items:
        if _budget_expired(deadline, cancel):
            break
        vid = item["video_id"]
        url = item.get("url", "")
        if not url:
            continue
        try:
            # Isolate transcript fetch errors from the pipeline-level
            # capture_failures() context so an individual reel's 400 doesn't
            # poison the entire source outcome (#829).
            with http.capture_failures() as _tf:
                data = http.get(
                    f"{SCRAPECREATORS_BASE}/v2/instagram/media/transcript",
                    params={"url": url},
                    headers=http.scrapecreators_headers(token),
                    retries=1,
                    **_request_options(transcript_timeout, deadline, cancel),
                )
            transcripts = data.get("transcripts") or []
            if transcripts and isinstance(transcripts, list):
                transcript_text = " ".join(
                    t.get("text", "") for t in transcripts
                    if isinstance(t, dict) and t.get("text")
                )
                if transcript_text:
                    words = transcript_text.split()
                    if len(words) > CAPTION_MAX_WORDS:
                        transcript_text = ' '.join(words[:CAPTION_MAX_WORDS]) + '...'
                    captions[vid] = transcript_text
        except Exception as e:
            _log(f"Transcript fetch failed for {vid}: {e}")

    got = sum(1 for v in captions.values() if v)
    _log(f"Got captions for {got}/{len(top_items)} reels")
    return captions


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    ig_creators: List[str] | None = None,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """Full Instagram search: find reels, then fetch captions for top results.

    Uses expand_instagram_queries() to generate multiple search queries,
    runs ScrapeCreators for each, and merges/deduplicates results by video ID.

    Args:
        topic: Search topic (raw topic, not planner's narrowed query)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        ig_creators: Optional list of Instagram creator handles to fetch reels from

    Returns:
        Dict with 'items' list. Each item has a 'caption_snippet' field.
    """
    core_topic = _extract_core_subject(topic)
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    last_error = None

    # Step 0: Creator reels (high-signal, runs first)
    if ig_creators and token:
        for creator in ig_creators:
            if _budget_expired(deadline, cancel):
                break
            raw_items = _user_reels(creator, token, deadline=deadline, cancel=cancel)
            parsed = _parse_items(raw_items, core_topic)
            for item in parsed:
                vid = item.get("video_id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    items.append(item)

    # Step 1: Multi-query keyword search — run ScrapeCreators for each expanded query
    queries = expand_instagram_queries(topic, depth)
    fallback_pages, fallback_posts = GOOGLE_FALLBACK_BUDGETS.get(
        depth, GOOGLE_FALLBACK_BUDGETS["default"]
    )
    fallback_cache: dict[str, dict[str, Any]] = {}
    for index, q in enumerate(queries):
        if _budget_expired(deadline, cancel):
            break
        query_count = len(queries)
        query_budget = {
            "pages": fallback_pages // query_count
            + (1 if index < fallback_pages % query_count else 0),
            "posts": fallback_posts // query_count
            + (1 if index < fallback_posts % query_count else 0),
        }
        search_result = search_instagram(
            q, from_date, to_date, depth, token,
            _fallback_budget=query_budget, _fallback_cache=fallback_cache,
            deadline=deadline, cancel=cancel,
        )
        if search_result.get("error"):
            last_error = search_result["error"]
        for item in search_result.get("items", []):
            vid = item.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                items.append(item)

    # Sort merged results by views descending
    items.sort(key=lambda x: x.get("engagement", {}).get("views") or 0, reverse=True)

    if _budget_expired(deadline, cancel):
        last_error = RESEARCH_TIMEOUT
    if not items:
        return {"items": [], "error": last_error}

    # Step 2: Fetch captions for top N
    captions = fetch_captions(items, token, depth, deadline=deadline, cancel=cancel)

    # Step 3: Attach captions to items
    for item in items:
        vid = item["video_id"]
        caption = captions.get(vid)
        if caption:
            item["caption_snippet"] = caption

    if _budget_expired(deadline, cancel):
        last_error = RESEARCH_TIMEOUT
    return {"items": items, "error": last_error}


def parse_instagram_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Instagram search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])


# ---------------------------------------------------------------------------
# Comments (ScrapeCreators, opt-in via INCLUDE_SOURCES=instagram_comments)
# ---------------------------------------------------------------------------


def _ig_total_engagement(item: Dict[str, Any]) -> int:
    """Sum an Instagram item's engagement for picking which posts to enrich."""
    eng = item.get("engagement", {}) or {}
    return (eng.get("views") or 0) + (eng.get("likes") or 0) + (eng.get("comments") or 0)


def enrich_with_comments(
    items: List[Dict[str, Any]],
    token: str,
    max_posts: int = 3,
    max_comments: int = 5,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Enrich top Instagram posts with comment data from ScrapeCreators.

    Mirrors ``tiktok.enrich_with_comments`` / ``youtube_yt.enrich_with_comments``:
    for the top N posts by engagement, fetch comments and attach them as a
    ``top_comments`` field (highest-liked first). Failures never crash the run.
    """
    if not items or not token or max_posts <= 0 or _budget_expired(deadline, cancel):
        return items

    ranked = sorted(items, key=_ig_total_engagement, reverse=True)
    top_items = ranked[:max_posts]
    _log(f"Enriching comments for {len(top_items)} Instagram posts")

    def _enrich_one(item: dict) -> list[dict]:
        post_url = item.get("url", "")
        if not post_url:
            return []
        try:
            return _fetch_post_comments(
                post_url, token, max_comments, deadline=deadline, cancel=cancel,
            )
        except Exception as exc:
            _log(f"Comment enrichment failed for {post_url}: {exc}")
        return []

    enriched_count = 0
    executor = ThreadPoolExecutor(max_workers=min(4, len(top_items)))
    try:
        futures = {http.submit_with_context(executor, _enrich_one, item): item for item in top_items}
        pending = set(futures)
        while pending:
            remaining = 0.05 if deadline is None else max(0, min(0.05, deadline - time.monotonic()))
            done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            for future in done:
                comments = future.result()
                if comments:
                    futures[future]["top_comments"] = comments
                    enriched_count += 1
            if _budget_expired(deadline, cancel):
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    _log(f"Enriched {enriched_count}/{len(top_items)} posts with comments")
    return items


def _fetch_post_comments(
    post_url: str,
    token: str,
    max_comments: int = 5,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Fetch comments for a single Instagram post/reel via ScrapeCreators.

    SC endpoint: GET /v2/instagram/post/comments?url=<post_or_reel_url>
    Response shape: { comments: [{text, comment_like_count, child_comment_count,
    created_at, user{username, ...}}], cursor }

    Returns:
        List of comment dicts with author, text, comment_like_count (likes), date,
        highest-liked first. Empty list on any error — never crashes the pipeline.
    """
    if _budget_expired(deadline, cancel):
        return []
    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/v2/instagram/post/comments",
            params={"url": post_url},
            headers=http.scrapecreators_headers(token),
            retries=2,
            **_request_options(30, deadline, cancel),
        )
    except Exception as exc:
        _log(f"Comment fetch error for {post_url}: {exc}")
        return []

    raw_comments = data.get("comments") or data.get("data") or []
    # Sort by like count desc so normalize sees the highest-signal first.
    raw_comments = sorted(
        raw_comments,
        key=lambda c: c.get("comment_like_count", 0) or 0,
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for c in raw_comments[:max_comments]:
        if not isinstance(c, dict):
            continue
        text = c.get("text") or ""
        if not text:
            continue
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        author = user.get("username") or ""
        created_at = c.get("created_at") or ""
        # created_at is ISO 8601 (e.g. "2026-07-04T14:27:58.000Z"); take the date.
        date_str = created_at[:10] if isinstance(created_at, str) and len(created_at) >= 10 else ""
        out.append({
            "author": author,
            "text": text[:400],
            "comment_like_count": c.get("comment_like_count", 0) or 0,
            "date": date_str,
        })
    return out
