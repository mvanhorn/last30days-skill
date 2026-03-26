"""Mastodon search via public instance APIs (no authentication required).

Searches across popular public Mastodon instances using their v2 search API.
No API key needed - uses public endpoints that allow anonymous search.

Supported instances:
- mastodon.social (largest general instance)
- mas.to (popular general instance)
- fosstodon.org (tech-focused)
- mstdn.social (general)
- techhub.social (tech-focused)
"""

import math
import re
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from . import http

# Popular Mastodon instances with public search APIs
# Ordered by user count and search availability
MASTODON_INSTANCES = [
    "mastodon.social",
    "mas.to",
    "fosstodon.org",
    "mstdn.social",
    "techhub.social",
]

MASTODON_SEARCH_PATH = "/api/v2/search"

DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# Instance-specific result limits (per instance)
INSTANCE_LIMITS = {
    "quick": 10,
    "default": 20,
    "deep": 40,
}


def _log(msg: str):
    """Log to stderr (only in TTY mode to avoid cluttering output)."""
    if sys.stderr.isatty():
        sys.stderr.write(f"[Mastodon] {msg}\n")
        sys.stderr.flush()


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for Mastodon search."""
    from .query import extract_core_subject
    _MASTODON_NOISE = frozenset({
        'best', 'top', 'good', 'great', 'awesome',
        'latest', 'new', 'news', 'update', 'updates',
        'trending', 'hottest', 'popular', 'viral',
        'practices', 'features', 'recommendations', 'advice',
    })
    return extract_core_subject(topic, noise=_MASTODON_NOISE)


def _strip_html(html: str) -> str:
    """Strip HTML tags from Mastodon post content."""
    # Convert <br> and </p> to newlines
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</p>', '\n', text)
    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    return text.strip()


def _parse_date(status: Dict[str, Any]) -> Optional[str]:
    """Parse date from Mastodon status to YYYY-MM-DD.

    Mastodon uses ISO 8601 format in created_at field.
    """
    val = status.get("created_at")
    if val and isinstance(val, str) and len(val) >= 10:
        return val[:10]
    return None


def _is_date_in_range(date_str: str, from_date: str, to_date: str) -> bool:
    """Check if date is within the research range."""
    if not date_str:
        return True  # Keep items with unknown dates
    return from_date <= date_str <= to_date


def _search_instance(
    instance: str,
    topic: str,
    from_date: str,
    to_date: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Search a single Mastodon instance for statuses.

    Args:
        instance: Mastodon instance hostname (e.g., "mastodon.social")
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        limit: Maximum results to return

    Returns:
        List of status dicts from the Mastodon API.
    """
    base_url = f"https://{instance}"
    url = f"{base_url}{MASTODON_SEARCH_PATH}"

    from urllib.parse import urlencode
    params = {
        "q": topic,
        "type": "statuses",
        "limit": str(min(limit, 40)),
    }
    full_url = f"{url}?{urlencode(params)}"

    try:
        response = http.request(
            "GET", full_url,
            timeout=15,
        )
    except http.HTTPError as e:
        if e.status_code in (401, 403, 404):
            _log(f"{instance}: search not available ({e.status_code})")
            return []
        elif e.status_code == 429:
            _log(f"{instance}: rate limited, skipping")
            return []
        _log(f"{instance}: search failed ({e.status_code})")
        return []
    except Exception as e:
        _log(f"{instance}: {type(e).__name__}: {e}")
        return []

    statuses = response.get("statuses", [])
    
    # Filter by date range
    filtered = []
    for status in statuses:
        date_str = _parse_date(status)
        if _is_date_in_range(date_str, from_date, to_date):
            # Add instance info to status for URL construction
            status["_instance"] = instance
            filtered.append(status)
    
    return filtered


def search_mastodon(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search Mastodon via public instance APIs.

    Searches multiple Mastodon instances in parallel for public posts
    matching the topic. No authentication required.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        config: Config dict (optional, for future instance customization)

    Returns:
        Dict with 'statuses' list from Mastodon API responses.
    """
    config = config or {}
    
    # Allow custom instances via config
    instances = config.get("MASTODON_INSTANCES", MASTODON_INSTANCES)
    
    # For quick depth, search fewer instances
    if depth == "quick":
        instances = instances[:3]
    
    limit = INSTANCE_LIMITS.get(depth, INSTANCE_LIMITS["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching for '{core_topic}' across {len(instances)} instances (depth={depth})")

    all_statuses = []
    seen_urls: Set[str] = set()

    # Search instances sequentially to avoid overwhelming them
    # (Mastodon instances often have rate limits)
    for instance in instances:
        try:
            statuses = _search_instance(
                instance, core_topic, from_date, to_date, limit
            )
            
            # Dedupe by URL
            for status in statuses:
                url = status.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_statuses.append(status)
            
            if statuses:
                _log(f"{instance}: {len(statuses)} results")
                
        except Exception as e:
            _log(f"{instance}: error: {e}")
            continue

    _log(f"Total: {len(all_statuses)} unique statuses from {len(seen_urls)} URLs")
    
    return {"statuses": all_statuses}


def parse_mastodon_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Mastodon API response into normalized item dicts.

    Returns:
        List of item dicts ready for normalization.
    """
    statuses = response.get("statuses", [])
    items = []

    for i, status in enumerate(statuses):
        content_html = status.get("content") or ""
        text = _strip_html(content_html)

        account = status.get("account") or {}
        handle = account.get("acct") or account.get("username") or ""
        display_name = account.get("display_name") or handle
        
        # URL construction
        # Full URL is provided by the API
        url = status.get("url") or ""
        
        # If URL is missing, construct it from instance and ID
        if not url:
            instance = status.get("_instance", "mastodon.social")
            status_id = status.get("id", "")
            username = account.get("username", "")
            if username and status_id:
                url = f"https://{instance}/@{username}/{status_id}"

        likes = status.get("favourites_count") or 0
        reposts = status.get("reblogs_count") or 0
        replies = status.get("replies_count") or 0

        date_str = _parse_date(status)

        # Relevance: position-based with engagement boost
        rank_score = max(0.3, 1.0 - (i * 0.015))
        engagement_boost = min(0.2, math.log1p(likes + reposts) / 40)
        relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

        # Extract instance for context
        instance = status.get("_instance", "")
        
        items.append({
            "handle": handle,
            "display_name": display_name,
            "text": text,
            "url": url,
            "date": date_str,
            "instance": instance,
            "engagement": {
                "likes": likes,
                "reposts": reposts,
                "replies": replies,
            },
            "relevance": round(relevance, 2),
            "why_relevant": f"Mastodon: @{handle}: {text[:60]}" if text else f"Mastodon: @{handle}",
        })

    return items