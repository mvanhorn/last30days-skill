"""Product Hunt API v2 (GraphQL) client for product discovery."""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import http


def _log_error(msg: str):
    """Log error to stderr."""
    sys.stderr.write(f"[PH ERROR] {msg}\n")
    sys.stderr.flush()


def _log_info(msg: str):
    """Log info to stderr."""
    sys.stderr.write(f"[PH] {msg}\n")
    sys.stderr.flush()


# Product Hunt API v2 (GraphQL)
PH_API_URL = "https://api.producthunt.com/v2/api/graphql"

# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 10,
    "default": 20,
    "deep": 50,
}

# Cache settings
CACHE_MAX_AGE = 86400  # 24 hours in seconds
_CACHE_DIR_OVERRIDE = os.environ.get('LAST30DAYS_CONFIG_DIR')
if _CACHE_DIR_OVERRIDE == "":
    _CACHE_DIR = None
elif _CACHE_DIR_OVERRIDE:
    _CACHE_DIR = Path(_CACHE_DIR_OVERRIDE)
else:
    _CACHE_DIR = Path.home() / ".config" / "last30days"
CACHE_FILE = _CACHE_DIR / "ph_topics_cache.json" if _CACHE_DIR else None

# GraphQL query to fetch all topics (paginated)
ALL_TOPICS_QUERY = """
query($first: Int!, $after: String) {
  topics(first: $first, after: $after, order: FOLLOWERS_COUNT) {
    edges {
      node {
        slug
        name
        postsCount
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""

# GraphQL query for posts within a topic slug
POSTS_QUERY = """
query SearchPosts($topic: String!, $postedAfter: DateTime!, $postedBefore: DateTime!, $first: Int!) {
  posts(
    topic: $topic
    postedAfter: $postedAfter
    postedBefore: $postedBefore
    first: $first
    order: VOTES
  ) {
    edges {
      node {
        id
        name
        tagline
        url
        votesCount
        commentsCount
        website
        createdAt
        topics {
          edges {
            node {
              name
            }
          }
        }
        makers {
          name
          username
        }
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Topic cache: fetch all PH topics, cache locally, refresh daily
# ---------------------------------------------------------------------------

def _fetch_all_topics(access_token: str) -> List[Dict[str, Any]]:
    """Fetch all Product Hunt topics via paginated GraphQL queries."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    all_topics = []
    cursor = None

    while True:
        variables = {"first": 50}
        if cursor:
            variables["after"] = cursor

        try:
            response = http.request(
                "POST",
                PH_API_URL,
                headers=headers,
                json_data={"query": ALL_TOPICS_QUERY, "variables": variables},
                timeout=30,
            )
        except http.HTTPError as e:
            _log_error(f"Failed to fetch topics: {e}")
            break

        if "errors" in response:
            for err in response.get("errors", []):
                _log_error(f"Topics fetch error: {err.get('message', str(err))}")
            break

        data = response.get("data", {}).get("topics", {})
        for edge in data.get("edges", []):
            node = edge.get("node", {})
            if node.get("slug"):
                all_topics.append({
                    "slug": node["slug"],
                    "name": node.get("name", ""),
                    "posts": node.get("postsCount", 0),
                })

        page_info = data.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return all_topics


def _load_cache() -> Optional[Dict[str, Any]]:
    """Load topic cache from disk if it exists and is fresh."""
    if not CACHE_FILE or not CACHE_FILE.exists():
        return None

    try:
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        if time.time() - cache.get("timestamp", 0) < CACHE_MAX_AGE:
            return cache
    except (json.JSONDecodeError, OSError, KeyError):
        pass

    return None


def _save_cache(topics: List[Dict[str, Any]]):
    """Save topic list to disk cache."""
    if not CACHE_FILE:
        return

    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump({"timestamp": time.time(), "topics": topics}, f)
    except OSError as e:
        _log_error(f"Failed to write topic cache: {e}")


def get_topics(access_token: str) -> List[Dict[str, Any]]:
    """Get all PH topics, using cache when fresh."""
    cache = _load_cache()
    if cache:
        return cache["topics"]

    _log_info("Refreshing topic cache...")
    topics = _fetch_all_topics(access_token)
    if topics:
        _save_cache(topics)
    return topics


def list_topic_slugs(access_token: str) -> str:
    """Return all topic slugs as a newline-separated string for display."""
    topics = get_topics(access_token)
    return "\n".join(t["slug"] for t in topics)


# ---------------------------------------------------------------------------
# Post search
# ---------------------------------------------------------------------------

def search_producthunt(
    access_token: str,
    slugs: List[str],
    from_date: str,
    to_date: str,
    depth: str = "default",
    mock_response: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Search Product Hunt for products in the given topic slugs.

    Args:
        access_token: Product Hunt API v2 access token
        slugs: Topic slugs to query (pre-selected by the caller)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: Research depth - "quick", "default", or "deep"
        mock_response: Mock response for testing

    Returns:
        Combined API response with product data from all matching topics
    """
    if mock_response is not None:
        return mock_response

    if not slugs:
        return {"data": {"posts": {"edges": []}}}

    first = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    posted_after = f"{from_date}T00:00:00Z"
    posted_before = f"{to_date}T23:59:59Z"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    all_edges = []
    seen_ids = set()

    for slug in slugs:
        _log_info(f"Querying topic: {slug}")
        try:
            response = http.request(
                "POST",
                PH_API_URL,
                headers=headers,
                json_data={
                    "query": POSTS_QUERY,
                    "variables": {
                        "topic": slug,
                        "postedAfter": posted_after,
                        "postedBefore": posted_before,
                        "first": first,
                    },
                },
                timeout=30,
            )
        except http.HTTPError as e:
            _log_error(f"Posts query error for topic '{slug}': {e}")
            continue

        if "errors" in response:
            for err in response.get("errors", []):
                _log_error(f"Posts query error: {err.get('message', str(err))}")
            continue

        edges = response.get("data", {}).get("posts", {}).get("edges", [])
        for edge in edges:
            post_id = edge.get("node", {}).get("id")
            if post_id and post_id not in seen_ids:
                seen_ids.add(post_id)
                all_edges.append(edge)

    # Sort merged results by votes descending
    all_edges.sort(
        key=lambda e: e.get("node", {}).get("votesCount", 0),
        reverse=True,
    )

    # Trim to requested depth
    all_edges = all_edges[:first]

    return {"data": {"posts": {"edges": all_edges}}}


def parse_ph_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Product Hunt GraphQL response to extract product items.

    Args:
        response: Raw API response from Product Hunt

    Returns:
        List of item dicts in normalized format
    """
    items = []

    # Handle error responses
    if "error" in response and response["error"]:
        error = response["error"]
        if isinstance(error, dict):
            _log_error(f"Product Hunt API error: {error.get('message', str(error))}")
        else:
            _log_error(f"Product Hunt API error: {error}")
        return items

    # Handle GraphQL errors
    if "errors" in response:
        for err in response["errors"]:
            _log_error(f"GraphQL error: {err.get('message', str(err))}")
        return items

    # Extract posts from GraphQL response
    posts_data = response.get("data", {}).get("posts", {})
    edges = posts_data.get("edges", [])

    for i, edge in enumerate(edges):
        node = edge.get("node", {})
        if not node:
            continue

        name = node.get("name", "")
        if not name:
            continue

        # Parse date
        created_at = node.get("createdAt", "")
        date_str = None
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                date_str = dt.date().isoformat()
            except (ValueError, TypeError):
                pass

        # Get engagement metrics
        votes = node.get("votesCount", 0)
        comments = node.get("commentsCount", 0)

        # Build engagement
        engagement = {
            "votes": votes,
            "comments": comments,
        }

        # Extract topics
        topics = []
        for topic_edge in node.get("topics", {}).get("edges", []):
            topic_name = topic_edge.get("node", {}).get("name", "")
            if topic_name:
                topics.append(topic_name)

        # Extract makers
        makers = []
        for maker in node.get("makers", []):
            maker_name = maker.get("name", "")
            if maker_name:
                makers.append(maker_name)

        # Product Hunt URL (ph post page)
        ph_url = node.get("url", "")
        # Website URL (the actual product)
        website = node.get("website", "")

        result_item = {
            "id": f"PH{i+1}",
            "name": name,
            "tagline": node.get("tagline", ""),
            "url": ph_url,
            "website": website,
            "date": date_str,
            "engagement": engagement,
            "topics": topics,
            "makers": makers,
            "why_relevant": _build_relevance_reason(votes, comments, topics, makers),
            "relevance": _estimate_relevance(i, votes, comments),
        }

        items.append(result_item)

    return items


def _build_relevance_reason(
    votes: int,
    comments: int,
    topics: List[str],
    makers: List[str],
) -> str:
    """Build a human-readable relevance reason."""
    parts = []
    if votes:
        parts.append(f"{votes} upvotes")
    if comments:
        parts.append(f"{comments} comments")
    if topics:
        parts.append(f"topics: {', '.join(topics[:3])}")
    if makers:
        parts.append(f"by {makers[0]}")

    return ", ".join(parts) if parts else "Product Hunt launch"


def _estimate_relevance(rank: int, votes: int, comments: int) -> float:
    """Estimate relevance from rank and engagement.

    Product Hunt returns results ordered by votes, so rank matters.
    """
    # Base relevance from rank (ordered by votes)
    base = max(0.3, 1.0 - (rank * 0.03))

    # Engagement boost
    if votes > 500 or comments > 50:
        base = min(1.0, base + 0.1)
    elif votes > 100 or comments > 20:
        base = min(1.0, base + 0.05)

    return round(min(1.0, base), 2)
