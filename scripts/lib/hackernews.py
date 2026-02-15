"""Hacker News search via Algolia API (free, no auth required)."""

import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from . import dates, http


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[HN ERROR] {msg}\n")
    sys.stderr.flush()


def _log_info(msg: str):
    """Log info to stderr."""
    sys.stderr.write(f"[HN] {msg}\n")
    sys.stderr.flush()


# Algolia HN Search API (free, no auth, no rate limits for reasonable use)
HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"

# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}


def _date_to_unix(date_str: str) -> int:
    """Convert YYYY-MM-DD to Unix timestamp."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def search_hn(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search Hacker News for relevant stories using Algolia API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response
    """
    if mock_response is not None:
        return mock_response

    hits_per_page = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    from_ts = _date_to_unix(from_date)
    to_ts = _date_to_unix(to_date) + 86400  # Include full end day

    # Search by relevance first
    url = (
        f"{HN_SEARCH_URL}"
        f"?query={quote_plus(topic)}"
        f"&tags=story"
        f"&numericFilters=created_at_i>{from_ts},created_at_i<{to_ts}"
        f"&hitsPerPage={hits_per_page}"
    )

    try:
        return http.get(url, timeout=30)
    except http.HTTPError as e:
        _log_error(f"Algolia API error: {e}")
        return {"error": str(e)}


def search_hn_by_date(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search HN sorted by date (newest first) for supplemental results.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth

    Returns:
        Raw API response
    """
    hits_per_page = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    from_ts = _date_to_unix(from_date)
    to_ts = _date_to_unix(to_date) + 86400

    url = (
        f"https://hn.algolia.com/api/v1/search_by_date"
        f"?query={quote_plus(topic)}"
        f"&tags=story"
        f"&numericFilters=created_at_i>{from_ts},created_at_i<{to_ts}"
        f"&hitsPerPage={hits_per_page}"
    )

    try:
        return http.get(url, timeout=30)
    except http.HTTPError as e:
        _log_error(f"Algolia API error (by_date): {e}")
        return {"error": str(e)}


def parse_hn_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Algolia HN response to extract story items.

    Args:
        response: Raw API response from Algolia

    Returns:
        List of item dicts in normalized format
    """
    items = []

    if "error" in response and response["error"]:
        _log_error(f"HN API error: {response['error']}")
        return items

    hits = response.get("hits", [])

    for i, hit in enumerate(hits):
        title = hit.get("title", "")
        if not title:
            continue

        # Build HN URL
        object_id = hit.get("objectID", "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
        hn_url = f"https://news.ycombinator.com/item?id={object_id}"

        # Parse date
        created_at = hit.get("created_at_i")
        date_str = dates.timestamp_to_date(created_at) if created_at else None

        # Engagement data
        points = hit.get("points")
        num_comments = hit.get("num_comments")

        item = {
            "id": f"HN{i+1}",
            "title": title.strip(),
            "url": url,
            "hn_url": hn_url,
            "author": hit.get("author", ""),
            "date": date_str,
            "engagement": {
                "points": points,
                "num_comments": num_comments,
            } if points is not None or num_comments is not None else None,
            "why_relevant": f"Hacker News story with {points or 0} points and {num_comments or 0} comments",
            "relevance": _estimate_relevance(hit, i),
        }

        items.append(item)

    return items


def _estimate_relevance(hit: Dict, rank: int) -> float:
    """Estimate relevance score from Algolia ranking and engagement.

    Algolia returns results sorted by relevance, so rank matters.
    High-engagement stories also get a boost.
    """
    # Base relevance from rank (Algolia's relevance ordering)
    base = max(0.3, 1.0 - (rank * 0.02))

    # Engagement boost
    points = hit.get("points", 0) or 0
    comments = hit.get("num_comments", 0) or 0

    if points > 500 or comments > 200:
        base = min(1.0, base + 0.1)
    elif points > 100 or comments > 50:
        base = min(1.0, base + 0.05)

    return round(min(1.0, base), 2)
