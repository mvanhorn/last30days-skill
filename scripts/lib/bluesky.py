"""Bluesky AT Protocol client for Bluesky discovery."""

import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from . import http


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[BLUESKY ERROR] {msg}\n")
    sys.stderr.flush()


# Bluesky public API (no auth required)
BLUESKY_PUBLIC_API = "https://public.api.bsky.app"
SEARCH_POSTS_ENDPOINT = "/xrpc/app.bsky.feed.searchPosts"

# Depth configurations: number of posts to request
DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}


def search_bluesky(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search Bluesky for relevant posts using AT Protocol API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD) - maps to 'since' param
        to_date: End date (YYYY-MM-DD) - maps to 'until' param
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Raw API response
    """
    if mock_response is not None:
        return mock_response

    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])

    # Build query parameters
    # Convert YYYY-MM-DD to ISO datetime for API
    params = {
        "q": topic,
        "limit": limit,
        "since": f"{from_date}T00:00:00Z",
        "until": f"{to_date}T23:59:59Z",
        "sort": "top",  # Sort by engagement
    }

    url = f"{BLUESKY_PUBLIC_API}{SEARCH_POSTS_ENDPOINT}?{urlencode(params)}"

    # Timeout based on depth
    timeout = 30 if depth == "quick" else 45 if depth == "default" else 60

    # Add headers for Bluesky API
    headers = {
        "Accept": "application/json",
    }

    return http.get(url, headers=headers, timeout=timeout)


def parse_bluesky_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Bluesky API response to extract post items.

    Args:
        response: Raw API response

    Returns:
        List of item dicts
    """
    items = []

    # Check for errors
    if "error" in response:
        _log_error(f"API error: {response.get('message', response['error'])}")
        return items

    posts = response.get("posts", [])

    for i, post_view in enumerate(posts):
        if not isinstance(post_view, dict):
            continue

        # Post data can be nested or flat depending on response format
        author = post_view.get("author", {})
        record = post_view.get("record", {})

        # Extract post URI and convert to URL
        uri = post_view.get("uri", "")
        # AT URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
        # Convert to: https://bsky.app/profile/handle/post/rkey
        url = _uri_to_url(uri, author.get("handle", ""))

        if not url:
            continue

        # Extract engagement metrics
        engagement = {
            "likes": post_view.get("likeCount", 0),
            "reposts": post_view.get("repostCount", 0),
            "replies": post_view.get("replyCount", 0),
            "quotes": post_view.get("quoteCount", 0),
        }

        # Extract date from record.createdAt
        created_at = record.get("createdAt", "")
        date = None
        if created_at:
            # Format: 2026-01-15T12:34:56.789Z
            match = re.match(r'(\d{4}-\d{2}-\d{2})', created_at)
            if match:
                date = match.group(1)

        # Get post text
        text = str(record.get("text", "")).strip()[:500]

        clean_item = {
            "id": f"B{i+1}",
            "text": text,
            "url": url,
            "author_handle": str(author.get("handle", "")).strip(),
            "author_display_name": str(author.get("displayName", "")).strip(),
            "date": date,
            "engagement": engagement,
            "relevance": 0.7,  # Default relevance - Bluesky API doesn't provide this
            "why_relevant": "",  # Could be enhanced with keyword matching
        }

        items.append(clean_item)

    return items


def _uri_to_url(uri: str, handle: str) -> str:
    """Convert AT Protocol URI to Bluesky web URL.

    Args:
        uri: AT URI (at://did:plc:xxx/app.bsky.feed.post/rkey)
        handle: Author's handle

    Returns:
        Web URL (https://bsky.app/profile/handle/post/rkey)
    """
    if not uri or not handle:
        return ""

    # Extract rkey from URI
    match = re.search(r'/app\.bsky\.feed\.post/([^/]+)$', uri)
    if match:
        rkey = match.group(1)
        return f"https://bsky.app/profile/{handle}/post/{rkey}"

    return ""
