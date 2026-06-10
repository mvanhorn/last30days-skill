"""TikTok search via ScrapeCreators API for /last30days.

Uses ScrapeCreators REST API to search TikTok by keyword, extract engagement
metrics (views, likes, comments, shares), and fetch video transcripts.

Requires SCRAPECREATORS_API_KEY in config. 100 free API calls, then PAYG.
API docs: https://scrapecreators.com/docs
"""

import re
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from . import dates, http, log
from . import apify_runner

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/tiktok"
APIFY_TIKTOK_ACTOR = "clockworks~tiktok-scraper"

# Depth configurations: how many results to fetch / captions to extract
DEPTH_CONFIG = {
    "quick":   {"results_per_page": 10, "max_captions": 3},
    "default": {"results_per_page": 20, "max_captions": 5},
    "deep":    {"results_per_page": 40, "max_captions": 8},
}

# Max words to keep from each caption
CAPTION_MAX_WORDS = 500

from .relevance import token_overlap_relevance as _compute_relevance


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for TikTok search."""
    from .query import extract_core_subject
    _TIKTOK_NOISE = frozenset({
        'best', 'top', 'good', 'great', 'awesome', 'killer',
        'latest', 'new', 'news', 'update', 'updates',
        'trending', 'hottest', 'popular', 'viral',
        'practices', 'features',
        'recommendations', 'advice',
        'prompt', 'prompts', 'prompting',
        'methods', 'strategies', 'approaches',
    })
    return extract_core_subject(topic, noise=_TIKTOK_NOISE)


def _infer_query_intent(topic: str) -> str:
    """Tiny local intent classifier for TikTok query expansion."""
    text = topic.lower().strip()
    if re.search(r"\b(vs|versus|compare|difference between)\b", text):
        return "comparison"
    if re.search(r"\b(how to|tutorial|guide|setup|step by step|deploy|install)\b", text):
        return "how_to"
    if re.search(r"\b(thoughts on|worth it|should i|opinion|review)\b", text):
        return "opinion"
    if re.search(r"\b(pricing|feature|features|best .* for)\b", text):
        return "product"
    return "breaking_news"


def expand_tiktok_queries(topic: str, depth: str) -> List[str]:
    """Generate multiple TikTok search queries from a topic.

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

    qtype = _infer_query_intent(topic)

    # Intent-specific TikTok content-type variants
    if qtype in ("breaking_news", "opinion"):
        queries.append(f"{core} edit OR reaction OR trend")
    elif qtype == "product":
        queries.append(f"{core} review OR haul OR unboxing")
    elif qtype == "comparison":
        queries.append(f"{core} vs OR compared OR which is better")
    elif qtype == "how_to":
        queries.append(f"{core} tutorial OR hack OR tip")
    else:
        queries.append(f"{core} edit OR reaction OR trend")

    # Deep depth: add viral content variant
    if depth == "deep":
        queries.append(f"{core} viral OR fyp OR trending")

    # Cap by depth budget
    caps = {"quick": 1, "default": 2, "deep": 3}
    cap = caps.get(depth, 2)
    return queries[:cap]


def _log(msg: str):
    log.source_log("TikTok", msg)


def _looks_like_apify_token(token: str | None) -> bool:
    """Heuristic to distinguish Apify tokens from ScrapeCreators keys."""
    return bool(token and token.startswith("apify_api_"))


def _apify_date_filter(from_date: str, to_date: str) -> str:
    """Map exact date range to Apify's coarse TikTok search filters."""
    try:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
        span_days = max(1, (end - start).days + 1)
    except ValueError:
        return "PAST_MONTH"

    if span_days <= 1:
        return "PAST_24_HOURS"
    if span_days <= 7:
        return "PAST_WEEK"
    if span_days <= 31:
        return "PAST_MONTH"
    if span_days <= 92:
        return "PAST_3_MONTHS"
    if span_days <= 183:
        return "PAST_6_MONTHS"
    return "ALL_TIME"


def _parse_date(item: Dict[str, Any]) -> Optional[str]:
    """Parse date from TikTok item to YYYY-MM-DD."""
    ts = item.get("create_time")
    if ts is None:
        ts = item.get("createTime")
    if ts:
        try:
            return dates.timestamp_to_date(int(ts))
        except (ValueError, TypeError):
            pass
    return None


def _clean_webvtt(text: str) -> str:
    """Strip WebVTT timestamps and headers from transcript text."""
    if not text:
        return ""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('WEBVTT'):
            continue
        if re.match(r'^\d{2}:\d{2}', line):
            continue
        if '-->' in line:
            continue
        cleaned.append(line)
    return ' '.join(cleaned)


def _parse_items(raw_items: List[Dict[str, Any]], core_topic: str) -> List[Dict[str, Any]]:
    """Parse ScrapeCreators or Apify TikTok items into normalized dicts."""
    items = []
    for raw in raw_items:
        video_id = str(raw.get("aweme_id") or raw.get("id") or "")
        text = raw.get("desc") or raw.get("text") or ""

        stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
        play_count = (
            stats.get("play_count")
            if stats.get("play_count") is not None
            else (raw.get("playCount") or 0)
        )
        digg_count = (
            stats.get("digg_count")
            if stats.get("digg_count") is not None
            else (raw.get("diggCount") or 0)
        )
        comment_count = (
            stats.get("comment_count")
            if stats.get("comment_count") is not None
            else (raw.get("commentCount") or 0)
        )
        share_count = (
            stats.get("share_count")
            if stats.get("share_count") is not None
            else (raw.get("shareCount") or 0)
        )

        author_raw = raw.get("author")
        if isinstance(author_raw, dict):
            author_name = author_raw.get("unique_id", "")
        elif isinstance(author_raw, str):
            author_name = author_raw
        else:
            author_meta = raw.get("authorMeta") if isinstance(raw.get("authorMeta"), dict) else {}
            author_name = (
                author_meta.get("name")
                or raw.get("authorName")
                or ""
            )

        share_url = raw.get("share_url") or raw.get("shareUrl") or raw.get("webVideoUrl") or ""
        text_extra = raw.get("text_extra") or []
        hashtag_names = [
            t.get("hashtag_name", "")
            for t in text_extra
            if isinstance(t, dict) and t.get("hashtag_name")
        ]
        if not hashtag_names:
            hashtags = raw.get("hashtags") or []
            hashtag_names = [
                t.get("name", "")
                for t in hashtags
                if isinstance(t, dict) and t.get("name")
            ]

        video_raw = raw.get("video")
        if not isinstance(video_raw, dict):
            video_raw = raw.get("videoMeta")
        duration = video_raw.get("duration") if isinstance(video_raw, dict) else None

        date_str = _parse_date(raw)

        # Compute relevance with hashtag boost
        relevance = _compute_relevance(core_topic, text, hashtag_names)

        # Build URL: prefer share_url, fallback to constructed URL
        url = share_url.split("?")[0] if share_url else ""
        if not url and author_name and video_id:
            url = f"https://www.tiktok.com/@{author_name}/video/{video_id}"

        items.append({
            "video_id": video_id,
            "text": text,
            "url": url,
            "author_name": author_name,
            "date": date_str,
            "engagement": {
                "views": play_count,
                "likes": digg_count,
                "comments": comment_count,
                "shares": share_count,
            },
            "hashtags": hashtag_names,
            "duration": duration,
            "relevance": relevance,
            "why_relevant": f"TikTok: {text[:60]}" if text else f"TikTok: {core_topic}",
            "caption_snippet": "",  # populated by fetch_captions
        })
    return items


def _search_tiktok_apify(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
    tokens: List[str] | None,
) -> Dict[str, Any]:
    """Search TikTok videos via Apify actor."""
    if not apify_runner.dedupe_tokens(tokens):
        return {"items": [], "error": "No APIFY_API_TOKEN configured", "provider": "apify"}

    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)
    _log(
        f"Searching TikTok via Apify for '{core_topic}' "
        f"(depth={depth}, count={config['results_per_page']})"
    )

    payload = {
        "searchQueries": [core_topic],
        "searchSection": "/video",
        "resultsPerPage": config["results_per_page"],
        "videoSearchSorting": "MOST_RELEVANT",
        "videoSearchDateFilter": _apify_date_filter(from_date, to_date),
    }

    try:
        raw_items = apify_runner.run_actor_dataset_items(
            APIFY_TIKTOK_ACTOR,
            payload,
            tokens,
            timeout=60,
        )
    except Exception as e:
        _log(f"Apify error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "apify"}

    items = _parse_items(raw_items[:config["results_per_page"]], core_topic)
    in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
    if in_range:
        items = in_range
    else:
        _log(f"No Apify videos within date range, keeping all {len(items)}")

    items.sort(key=lambda x: x["engagement"]["views"], reverse=True)
    _log(f"Found {len(items)} TikTok videos via Apify")
    return {"items": items, "provider": "apify"}


def _hashtag_search(
    hashtag: str,
    token: str,
) -> List[Dict[str, Any]]:
    """Search TikTok by hashtag via ScrapeCreators.

    Args:
        hashtag: Hashtag name (without #)
        token: ScrapeCreators API key

    Returns:
        List of raw TikTok item dicts (aweme_info format).
    """
    _log(f"Hashtag search: #{hashtag}")
    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/search/hashtag",
            params={"hashtag": hashtag},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as e:
        _log(f"Hashtag search error for #{hashtag}: {e}")
        return []

    raw_items = data.get("aweme_list") or data.get("data") or []
    _log(f"  -> {len(raw_items)} results for #{hashtag}")
    return raw_items


def _profile_videos(
    handle: str,
    token: str,
    count: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch a TikTok creator's recent videos via ScrapeCreators.

    Args:
        handle: TikTok username (without @)
        token: ScrapeCreators API key
        count: Max videos to return

    Returns:
        List of raw TikTok item dicts (aweme_info format).
    """
    _log(f"Profile videos: @{handle}")
    profile_url = "https://api.scrapecreators.com/v3/tiktok/profile/videos"
    try:
        data = http.get(
            profile_url,
            params={"handle": handle, "sort_by": "latest"},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as e:
        _log(f"Profile videos error for @{handle}: {e}")
        return []

    raw_items = data.get("aweme_list") or data.get("data") or []
    _log(f"  -> {len(raw_items)} videos from @{handle}")
    return raw_items[:count]


def search_tiktok(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    apify_tokens: List[str] | None = None,
) -> Dict[str, Any]:
    """Search TikTok via ScrapeCreators, falling back to Apify when needed.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    if _looks_like_apify_token(token):
        return _search_tiktok_apify(topic, from_date, to_date, depth, [token, *(apify_tokens or [])])

    if not token and apify_tokens:
        return _search_tiktok_apify(topic, from_date, to_date, depth, apify_tokens)
    if not token:
        return {"items": [], "error": "No TikTok provider token configured"}

    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching TikTok for '{core_topic}' (depth={depth}, count={config['results_per_page']})")

    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/search/keyword",
            params={"query": core_topic, "sort_by": "relevance"},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except http.HTTPError as e:
        if e.status_code == 402 and apify_tokens:
            _log("ScrapeCreators returned 402, falling back to Apify")
            return _search_tiktok_apify(topic, from_date, to_date, depth, apify_tokens)
        _log(f"ScrapeCreators error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "scrapecreators"}
    except Exception as e:
        _log(f"ScrapeCreators error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "scrapecreators"}

    # Items are nested under aweme_info
    raw_entries = data.get("search_item_list") or data.get("data") or []
    raw_items = []
    for entry in raw_entries:
        if isinstance(entry, dict):
            info = entry.get("aweme_info", entry)
            raw_items.append(info)

    # Limit to configured count
    raw_items = raw_items[:config["results_per_page"]]

    # Parse items
    items = _parse_items(raw_items, core_topic)

    # Hard date filter
    in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
    out_of_range = len(items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} videos outside date range")
    else:
        _log(f"No videos within date range, keeping all {len(items)}")

    # Sort by views descending
    items.sort(key=lambda x: x["engagement"]["views"], reverse=True)

    _log(f"Found {len(items)} TikTok videos")
    return {"items": items, "provider": "scrapecreators"}


def fetch_captions(
    video_items: List[Dict[str, Any]],
    token: str,
    depth: str = "default",
) -> Dict[str, str]:
    """Fetch transcripts for top N TikTok videos via ScrapeCreators.

    Strategy:
    1. Use the 'text' field (video description) as baseline caption
    2. For top N, call /video/transcript for spoken-word captions

    Args:
        video_items: Items from search_tiktok()
        token: ScrapeCreators API key
        depth: Depth level for caption limit

    Returns:
        Dict mapping video_id -> caption text (truncated to 500 words)
    """
    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    max_captions = config["max_captions"]

    if not video_items or not token:
        return {}

    top_items = video_items[:max_captions]
    _log(f"Enriching captions for {len(top_items)} videos")

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
        vid = item["video_id"]
        url = item.get("url", "")
        if not url:
            continue
        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/video/transcript",
                params={"url": url},
                headers=http.scrapecreators_headers(token),
                timeout=15,
                retries=1,
            )
            transcript = data.get("transcript")
            if transcript:
                if isinstance(transcript, list):
                    transcript = " ".join(str(s) for s in transcript)
                transcript = _clean_webvtt(transcript)
                if transcript:
                    words = transcript.split()
                    if len(words) > CAPTION_MAX_WORDS:
                        transcript = ' '.join(words[:CAPTION_MAX_WORDS]) + '...'
                    captions[vid] = transcript
        except Exception as e:
            _log(f"Transcript fetch failed for {vid}: {e}")

    got = sum(1 for v in captions.values() if v)
    _log(f"Got captions for {got}/{len(top_items)} videos")
    return captions


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    apify_tokens: List[str] | None = None,
    hashtags: List[str] | None = None,
    creators: List[str] | None = None,
) -> Dict[str, Any]:
    """Full TikTok search: find videos, then fetch captions for top results.

    Uses expand_tiktok_queries() to generate multiple search queries,
    runs ScrapeCreators for each, and merges/deduplicates results by video ID.

    Args:
        topic: Search topic (raw topic, not planner's narrowed query)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        hashtags: Optional list of TikTok hashtags to search (without #)
        creators: Optional list of TikTok creator handles to fetch videos from

    Returns:
        Dict with 'items' list. Each item has a 'caption_snippet' field.
    """
    core_topic = _extract_core_subject(topic)
    seen_ids: Set[str] = set()
    items: List[Dict[str, Any]] = []
    last_error = None

    sc_enabled = bool(token and not _looks_like_apify_token(token))

    # Step 0a: Hashtag search (high-signal, ScrapeCreators only)
    if hashtags and sc_enabled:
        for hashtag in hashtags:
            raw_items = _hashtag_search(hashtag, token)
            parsed = _parse_items(raw_items, core_topic)
            for item in parsed:
                vid = item.get("video_id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    items.append(item)

    # Step 0b: Creator profile videos (high-signal, ScrapeCreators only)
    if creators and sc_enabled:
        for creator in creators:
            raw_items = _profile_videos(creator, token)
            parsed = _parse_items(raw_items, core_topic)
            for item in parsed:
                vid = item.get("video_id", "")
                if vid and vid not in seen_ids:
                    seen_ids.add(vid)
                    items.append(item)

    # Step 1: Multi-query keyword search — run ScrapeCreators for each expanded query
    queries = expand_tiktok_queries(topic, depth)
    used_provider = "scrapecreators" if sc_enabled else "apify"
    for q in queries:
        search_result = search_tiktok(q, from_date, to_date, depth, token, apify_tokens=apify_tokens)
        if search_result.get("error"):
            last_error = search_result["error"]
        used_provider = search_result.get("provider", used_provider)
        for item in search_result.get("items", []):
            vid = item.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                items.append(item)

    # Sort merged results by views descending
    items.sort(key=lambda x: x.get("engagement", {}).get("views", 0), reverse=True)

    if not items:
        return {"items": [], "error": last_error}

    # Step 2: Fetch captions for top N
    captions = fetch_captions(items, token, depth) if used_provider == "scrapecreators" else {}

    # Step 3: Attach captions to items
    for item in items:
        vid = item["video_id"]
        caption = captions.get(vid)
        if caption:
            item["caption_snippet"] = caption

    return {"items": items, "error": last_error, "provider": used_provider}


def parse_tiktok_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse TikTok search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])


def _tiktok_total_engagement(item: Dict[str, Any]) -> int:
    """Total engagement for ranking which posts deserve comment enrichment."""
    eng = item.get("engagement", {})
    return (eng.get("views", 0) or 0) + (eng.get("likes", 0) or 0) + (eng.get("comments", 0) or 0)


def enrich_with_comments(
    items: List[Dict[str, Any]],
    token: str,
    max_posts: int = 3,
    max_comments: int = 5,
) -> List[Dict[str, Any]]:
    """Enrich top TikTok posts with comment data from ScrapeCreators.

    For the top N posts by engagement, fetches comments via the SC API
    and attaches them as a ``top_comments`` field on each item. Mirrors
    youtube_yt.enrich_with_comments.

    Args:
        items: TikTok items from search_tiktok()
        token: ScrapeCreators API key
        max_posts: How many posts to enrich with comments
        max_comments: Max comments to keep per post

    Returns:
        Items list (mutated in place) with top_comments added to enriched items.
    """
    if not items or not token or max_posts <= 0:
        return items

    ranked = sorted(items, key=_tiktok_total_engagement, reverse=True)
    top_items = ranked[:max_posts]
    _log(f"Enriching comments for {len(top_items)} TikTok posts")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _enrich_one(item: dict) -> bool:
        post_url = item.get("url", "")
        if not post_url:
            return False
        try:
            comments = _fetch_post_comments(post_url, token, max_comments)
            if comments:
                item["top_comments"] = comments
                return True
        except Exception as exc:
            _log(f"Comment enrichment failed for {post_url}: {exc}")
        return False

    enriched_count = 0
    with ThreadPoolExecutor(max_workers=min(4, len(top_items))) as executor:
        futures = {executor.submit(_enrich_one, item): item for item in top_items}
        for future in as_completed(futures):
            if future.result():
                enriched_count += 1

    _log(f"Enriched {enriched_count}/{len(top_items)} posts with comments")
    return items


def _fetch_post_comments(
    post_url: str,
    token: str,
    max_comments: int = 5,
) -> List[Dict[str, Any]]:
    """Fetch comments for a single TikTok post via ScrapeCreators.

    SC endpoint: GET /v1/tiktok/video/comments?url=<video_url>
    Response shape: { comments: [{text, user.nickname, digg_count, create_time, ...}], cursor, total }

    Args:
        post_url: Canonical TikTok post URL (share_url form works)
        token: ScrapeCreators API key
        max_comments: Maximum comments to return

    Returns:
        List of comment dicts with author, text, digg_count (likes), date.
        Empty list on any error — comment failures never crash the pipeline.
    """
    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/video/comments",
            params={"url": post_url, "trim": "true"},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except Exception as exc:
        _log(f"Comment fetch error for {post_url}: {exc}")
        return []

    raw_comments = data.get("comments") or data.get("data") or []
    # Sort by digg_count desc so normalize sees the highest-signal first.
    raw_comments = sorted(
        raw_comments,
        key=lambda c: c.get("digg_count", 0) or 0,
        reverse=True,
    )
    out: List[Dict[str, Any]] = []
    for c in raw_comments[:max_comments]:
        text = c.get("text") or ""
        if not text:
            continue
        user = c.get("user") if isinstance(c.get("user"), dict) else {}
        # Prefer unique_id (the @handle) over nickname (display name) so
        # downstream render can cite @handle consistently across platforms.
        author = user.get("unique_id") or user.get("nickname") or ""
        create_time = c.get("create_time")
        date_str = ""
        if create_time:
            try:
                date_str = dates.timestamp_to_date(int(create_time)) or ""
            except (ValueError, TypeError):
                date_str = ""
        out.append({
            "author": author,
            "text": text[:400],
            "digg_count": c.get("digg_count", 0) or 0,
            "date": date_str,
        })
    return out
