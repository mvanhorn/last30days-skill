"""GitHub search via public REST API (no auth required for public repos).

Uses api.github.com/search/issues for issue/PR/discussion discovery.
Unauthenticated: 10 req/min. Authenticated via GITHUB_TOKEN: 30 req/min.
"""

import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from . import http
from .query import extract_core_subject
from .relevance import token_overlap_relevance

GITHUB_SEARCH_ISSUES_URL = "https://api.github.com/search/issues"
GITHUB_SEARCH_REPOS_URL = "https://api.github.com/search/repositories"

DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

ENRICH_LIMITS = {
    "quick": 3,
    "default": 5,
    "deep": 10,
}


def _log(msg: str):
    """Log to stderr (only in TTY mode to avoid cluttering Claude Code output)."""
    if sys.stderr.isatty():
        sys.stderr.write(f"[GitHub] {msg}\n")
        sys.stderr.flush()


def _github_headers(token: Optional[str] = None) -> Dict[str, str]:
    """Build headers for GitHub API requests."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search GitHub Issues and PRs via the Search API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: Optional GitHub token for higher rate limits

    Returns:
        Dict with 'items' list from GitHub API response.
    """
    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core = extract_core_subject(topic)

    _log(f"Searching for '{core}' (raw: '{topic}', since {from_date}, count={count})")

    # GitHub search query: filter by creation date range
    query = f"{core} created:{from_date}..{to_date}"

    params = {
        "q": query,
        "per_page": min(count, 100),
        "sort": "reactions",
        "order": "desc",
    }

    url = f"{GITHUB_SEARCH_ISSUES_URL}?{urlencode(params)}"
    headers = _github_headers(token)

    try:
        response = http.request("GET", url, headers=headers, timeout=30)
    except http.HTTPError as e:
        _log(f"Search failed: {e}")
        return {"items": [], "error": str(e)}
    except Exception as e:
        _log(f"Search failed: {e}")
        return {"items": [], "error": str(e)}

    items = response.get("items", [])
    _log(f"Found {len(items)} issues/PRs")
    return response


def parse_github_response(response: Dict[str, Any], query: str = "") -> List[Dict[str, Any]]:
    """Parse GitHub search API response into normalized item dicts.

    Args:
        response: GitHub search API response
        query: Original search query for token-overlap relevance scoring

    Returns:
        List of item dicts ready for normalization.
    """
    items = response.get("items", [])
    parsed = []

    for i, hit in enumerate(items):
        number = hit.get("number", 0)
        reactions = hit.get("reactions", {})
        total_reactions = reactions.get("total_count", 0) if isinstance(reactions, dict) else 0
        num_comments = hit.get("comments", 0)
        is_pr = hit.get("pull_request") is not None

        # Extract date (YYYY-MM-DD from ISO timestamp)
        created_at = hit.get("created_at", "")
        date_str = created_at[:10] if created_at else None

        # Extract repository from repository_url or html_url
        repo_url = hit.get("repository_url", "")
        repository = ""
        if repo_url:
            # https://api.github.com/repos/owner/repo -> owner/repo
            parts = repo_url.split("/repos/")
            if len(parts) == 2:
                repository = parts[1]

        # Relevance: blend rank position + token-overlap content matching
        rank_score = max(0.3, 1.0 - (i * 0.02))  # 1.0 -> 0.3 over 35 items
        engagement_boost = min(0.2, math.log1p(total_reactions) / 40)
        if query:
            content_score = token_overlap_relevance(query, hit.get("title", ""))
            relevance = min(1.0, 0.6 * rank_score + 0.4 * content_score + engagement_boost)
        else:
            relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

        # Truncate body for storage
        body = hit.get("body") or ""
        body_snippet = body[:500] + "..." if len(body) > 500 else body

        parsed.append({
            "number": number,
            "title": hit.get("title", ""),
            "url": hit.get("html_url", ""),
            "repository": repository,
            "author": (hit.get("user") or {}).get("login", ""),
            "item_type": "pull_request" if is_pr else "issue",
            "date": date_str,
            "body_snippet": body_snippet,
            "labels": [l.get("name", "") for l in hit.get("labels", [])],
            "engagement": {
                "reactions": total_reactions,
                "num_comments": num_comments,
            },
            "relevance": round(relevance, 2),
            "why_relevant": f"GitHub {'PR' if is_pr else 'issue'}: {hit.get('title', '')[:60]}",
        })

    return parsed


def _fetch_issue_comments(
    url: str,
    max_comments: int = 5,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch top comments for an issue/PR from the GitHub API.

    Args:
        url: Issue comments API URL
        max_comments: Max comments to return
        token: Optional GitHub token

    Returns:
        Dict with 'comments' list and 'comment_insights' list.
    """
    headers = _github_headers(token)
    params = {
        "per_page": str(max_comments),
        "sort": "created",
        "direction": "desc",
    }
    api_url = f"{url}?{urlencode(params)}"

    try:
        data = http.request("GET", api_url, headers=headers, timeout=15)
    except Exception as e:
        _log(f"Failed to fetch comments: {e}")
        return {"comments": [], "comment_insights": []}

    if not isinstance(data, list):
        return {"comments": [], "comment_insights": []}

    comments = []
    insights = []
    for c in data[:max_comments]:
        body = c.get("body") or ""
        excerpt = body[:300] + "..." if len(body) > 300 else body
        reactions = c.get("reactions", {})
        reaction_count = reactions.get("total_count", 0) if isinstance(reactions, dict) else 0

        comments.append({
            "author": (c.get("user") or {}).get("login", ""),
            "text": excerpt,
            "reactions": reaction_count,
        })
        # First sentence as insight
        first_sentence = body.split(". ")[0].split("\n")[0][:200]
        if first_sentence:
            insights.append(first_sentence)

    return {"comments": comments, "comment_insights": insights}


def enrich_top_issues(
    items: List[Dict[str, Any]],
    depth: str = "default",
    token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch comments for top N issues by reactions.

    Args:
        items: Parsed GitHub items
        depth: Research depth (controls how many to enrich)
        token: Optional GitHub token

    Returns:
        Items with top_comments and comment_insights added.
    """
    if not items:
        return items

    limit = ENRICH_LIMITS.get(depth, ENRICH_LIMITS["default"])

    # Sort by reactions to enrich the most popular issues
    by_reactions = sorted(
        range(len(items)),
        key=lambda i: items[i].get("engagement", {}).get("reactions", 0),
        reverse=True,
    )
    to_enrich = by_reactions[:limit]

    _log(f"Enriching top {len(to_enrich)} issues with comments")

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for idx in to_enrich:
            # Build comments API URL from html_url
            html_url = items[idx].get("url", "")
            # https://github.com/owner/repo/issues/123 -> https://api.github.com/repos/owner/repo/issues/123/comments
            if "/issues/" in html_url or "/pull/" in html_url:
                api_url = html_url.replace("https://github.com/", "https://api.github.com/repos/")
                api_url = api_url.replace("/pull/", "/issues/")
                api_url += "/comments"
                futures[executor.submit(_fetch_issue_comments, api_url, 5, token)] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result(timeout=15)
                items[idx]["top_comments"] = result["comments"]
                items[idx]["comment_insights"] = result["comment_insights"]
            except Exception:
                items[idx]["top_comments"] = []
                items[idx]["comment_insights"] = []

    return items
