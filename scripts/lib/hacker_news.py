"""Hacker News search via Algolia HN API."""

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from . import http

HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1"


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[HN ERROR] {msg}\n")
    sys.stderr.flush()


def _log_info(msg: str):
    """Log info to stderr."""
    sys.stderr.write(f"[HN] {msg}\n")
    sys.stderr.flush()


def search_hacker_news(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search Hacker News via Algolia API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'

    Returns:
        Raw Algolia response dict
    """
    # Build date filter for Algolia
    # Algolia uses Unix timestamps
    from_ts = int(datetime.fromisoformat(from_date).timestamp())
    to_ts = int(datetime.fromisoformat(to_date).timestamp()) + 86400  # Include full end day

    # Map depth to hits per page
    hits_map = {
        "quick": 15,
        "default": 30,
        "deep": 60,
    }
    hits_per_page = hits_map.get(depth, 30)

    # Algolia search endpoint
    # Use story type filter to exclude comments
    params = f"query={quote(topic)}&tags=story&hitsPerPage={hits_per_page}&numericFilters=created_at_i>={from_ts},created_at_i<={to_ts}"
    url = f"{HN_ALGOLIA_URL}/search?{params}"

    _log_info(f"Searching HN Algolia: {topic}")

    try:
        response = http.get(url, timeout=30)
        return response  # http.get already returns parsed JSON
    except http.HTTPError as e:
        _log_error(f"HTTP error: {e}")
        return {"error": str(e)}
    except Exception as e:
        _log_error(f"Unexpected error: {e}")
        return {"error": str(e)}


def parse_hn_response(raw_response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Algolia HN response into normalized item dicts.

    Args:
        raw_response: Raw response from search_hacker_news

    Returns:
        List of normalized HN item dicts ready for HackerNewsItem creation
    """
    if raw_response.get("error"):
        return []

    hits = raw_response.get("hits", [])
    items = []

    for i, hit in enumerate(hits):
        # Skip、脱出 posts (Ask HN, Show HN without external URL)
        # Only include stories with external URLs
        url = hit.get("url", "")
        if not url:
            # This is a Ask HN or self-post without external link
            # Use the HN discussion link instead
            object_id = hit.get("objectID", "")
            url = f"https://news.ycombinator.com/item?id={object_id}"

        # Parse created_at timestamp
        created_at = hit.get("created_at", "")
        date = ""
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                date = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                date = ""

        # Extract points (upvotes) and comments
        points = hit.get("points", 0)
        num_comments = hit.get("num_comments", 0)

        item = {
            "id": hit.get("objectID", f"HN{i+1}"),
            "title": hit.get("title", ""),
            "url": url,
            "author": hit.get("author", ""),
            "date": date,
            "date_confidence": "high",
            "points": points,
            "num_comments": num_comments,
            "relevance": 0.5,
            "why_relevant": "",
        }

        items.append(item)

    return items