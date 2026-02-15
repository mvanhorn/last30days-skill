"""YouTube Data API v3 client for video discovery."""

import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from . import dates, http


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[YT ERROR] {msg}\n")
    sys.stderr.flush()


def _log_info(msg: str):
    """Log info to stderr."""
    sys.stderr.write(f"[YT] {msg}\n")
    sys.stderr.flush()


# YouTube Data API v3
YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 10,
    "default": 25,
    "deep": 50,
}


def search_youtube(
    api_key: str,
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search YouTube for relevant videos.

    Args:
        api_key: YouTube Data API v3 key
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response with search results and video details
    """
    if mock_response is not None:
        return mock_response

    max_results = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    # Convert dates to RFC 3339 format for YouTube API
    published_after = f"{from_date}T00:00:00Z"
    published_before = f"{to_date}T23:59:59Z"

    # Search for videos
    url = (
        f"{YT_SEARCH_URL}"
        f"?part=snippet"
        f"&q={quote_plus(topic)}"
        f"&type=video"
        f"&order=relevance"
        f"&publishedAfter={published_after}"
        f"&publishedBefore={published_before}"
        f"&maxResults={max_results}"
        f"&key={api_key}"
    )

    try:
        search_response = http.get(url, timeout=30)
    except http.HTTPError as e:
        _log_error(f"YouTube search API error: {e}")
        return {"error": str(e)}

    # Extract video IDs for stats lookup
    video_ids = []
    for item in search_response.get("items", []):
        vid = item.get("id", {}).get("videoId")
        if vid:
            video_ids.append(vid)

    # Fetch video statistics (views, likes, comments)
    stats = {}
    if video_ids:
        stats = _fetch_video_stats(api_key, video_ids)

    # Merge stats into response
    search_response["_video_stats"] = stats

    return search_response


def _fetch_video_stats(
    api_key: str,
    video_ids: List[str],
) -> Dict[str, Dict]:
    """Fetch video statistics for a list of video IDs.

    Args:
        api_key: YouTube Data API key
        video_ids: List of video IDs

    Returns:
        Dict mapping video_id to stats dict
    """
    stats = {}

    # YouTube API allows up to 50 IDs per request
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        ids_param = ",".join(batch)

        url = (
            f"{YT_VIDEOS_URL}"
            f"?part=statistics"
            f"&id={ids_param}"
            f"&key={api_key}"
        )

        try:
            response = http.get(url, timeout=15)
            for item in response.get("items", []):
                vid = item.get("id")
                stat = item.get("statistics", {})
                if vid:
                    stats[vid] = {
                        "views": _safe_int(stat.get("viewCount")),
                        "likes": _safe_int(stat.get("likeCount")),
                        "comments": _safe_int(stat.get("commentCount")),
                    }
        except http.HTTPError as e:
            _log_error(f"YouTube stats API error: {e}")
        except Exception as e:
            _log_error(f"YouTube stats error: {e}")

    return stats


def _safe_int(value) -> Optional[int]:
    """Safely convert to int, handling None and non-numeric strings."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_youtube_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse YouTube API response to extract video items.

    Args:
        response: Raw API response from YouTube

    Returns:
        List of item dicts in normalized format
    """
    items = []

    if "error" in response and response["error"]:
        error = response["error"]
        if isinstance(error, dict):
            _log_error(f"YouTube API error: {error.get('message', str(error))}")
        else:
            _log_error(f"YouTube API error: {error}")
        return items

    stats = response.get("_video_stats", {})

    for i, item in enumerate(response.get("items", [])):
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")

        title = snippet.get("title", "")
        if not title:
            continue

        # Parse date
        published_at = snippet.get("publishedAt", "")
        date_str = None
        if published_at:
            try:
                dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                date_str = dt.date().isoformat()
            except (ValueError, TypeError):
                pass

        # Get channel info
        channel = snippet.get("channelTitle", "")

        # Get video stats
        video_stats = stats.get(video_id, {})
        views = video_stats.get("views")
        likes = video_stats.get("likes")
        comments = video_stats.get("comments")

        # Build engagement
        engagement = None
        if views is not None or likes is not None:
            engagement = {
                "views": views,
                "likes": likes,
                "comments": comments,
            }

        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

        result_item = {
            "id": f"YT{i+1}",
            "title": _clean_html_entities(title),
            "url": url,
            "channel": channel,
            "date": date_str,
            "engagement": engagement,
            "why_relevant": _build_relevance_reason(views, likes, comments, channel),
            "relevance": _estimate_relevance(i, video_stats),
        }

        items.append(result_item)

    return items


def _clean_html_entities(text: str) -> str:
    """Clean common HTML entities from YouTube titles."""
    import html
    return html.unescape(text)


def _build_relevance_reason(
    views: Optional[int],
    likes: Optional[int],
    comments: Optional[int],
    channel: str,
) -> str:
    """Build a human-readable relevance reason."""
    parts = []
    if channel:
        parts.append(f"by {channel}")
    if views is not None:
        parts.append(f"{_format_count(views)} views")
    if likes is not None:
        parts.append(f"{_format_count(likes)} likes")
    if comments is not None:
        parts.append(f"{_format_count(comments)} comments")

    return ", ".join(parts) if parts else "YouTube video result"


def _format_count(n: int) -> str:
    """Format large numbers for display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _estimate_relevance(rank: int, stats: Dict) -> float:
    """Estimate relevance from search rank and engagement.

    YouTube search returns results sorted by relevance, so rank matters.
    """
    # Base relevance from rank
    base = max(0.3, 1.0 - (rank * 0.02))

    # Engagement boost
    views = stats.get("views", 0) or 0
    likes = stats.get("likes", 0) or 0

    if views > 100_000 or likes > 5_000:
        base = min(1.0, base + 0.1)
    elif views > 10_000 or likes > 500:
        base = min(1.0, base + 0.05)

    return round(min(1.0, base), 2)
