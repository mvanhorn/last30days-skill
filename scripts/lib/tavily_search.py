"""Tavily web search for last30days skill.

Uses the Tavily Search API as a web search backend.
Returns structured search results well-suited for LLM pipelines.

API docs: https://docs.tavily.com/
"""

import sys
from datetime import datetime
from typing import Any, Dict, List
from urllib.parse import urlparse

from . import http

ENDPOINT = "https://api.tavily.com/search"

# Domains to exclude (handled by Reddit/X search)
EXCLUDED_DOMAINS = [
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
]


def search_web(
    topic: str,
    from_date: str,
    to_date: str,
    api_key: str,
    depth: str = "default",
) -> List[Dict[str, Any]]:
    """Search the web via Tavily Search API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD) — used to compute recency window
        to_date: End date (YYYY-MM-DD) — used to compute recency window
        api_key: Tavily API key
        depth: 'quick', 'default', or 'deep'

    Returns:
        List of result dicts with keys: id, url, title, snippet, source_domain, date,
        date_confidence, relevance, why_relevant

    Raises:
        http.HTTPError: On API errors
    """
    max_results = {"quick": 8, "default": 15, "deep": 25}.get(depth, 15)
    search_depth = "basic" if depth == "quick" else "advanced"

    # Compute recency window from date range
    try:
        days = (datetime.strptime(to_date, "%Y-%m-%d") - datetime.strptime(from_date, "%Y-%m-%d")).days
        days = max(1, days)
    except (ValueError, TypeError):
        days = 30  # fallback to 30-day window

    payload = {
        "api_key": api_key,
        "query": topic,
        "max_results": max_results,
        "search_depth": search_depth,
        "days": days,
        "exclude_domains": EXCLUDED_DOMAINS,
    }

    sys.stderr.write(f"[Web] Searching Tavily for: {topic}\n")
    sys.stderr.flush()

    response = http.request(
        "POST",
        ENDPOINT,
        json_data=payload,
        timeout=15,
    )

    return _normalize_results(response)


def _normalize_results(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert Tavily response to websearch item schema."""
    items = []

    for i, result in enumerate(response.get("results", [])):
        if not isinstance(result, dict):
            continue

        url = result.get("url", "")
        if not url:
            continue

        try:
            domain = urlparse(url).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
        except (ValueError, TypeError):
            domain = ""

        title = str(result.get("title", "")).strip()
        snippet = str(result.get("content", "")).strip()

        if not title and not snippet:
            continue

        # Tavily provides a relevance score (0-1)
        score = result.get("score", 0.6)

        items.append({
            "id": f"W{i+1}",
            "title": title[:200],
            "url": url,
            "source_domain": domain,
            "snippet": snippet[:500],
            "date": None,
            "date_confidence": "low",
            "relevance": round(score, 3),
            "why_relevant": "",
        })

    sys.stderr.write(f"[Web] Tavily: {len(items)} results\n")
    sys.stderr.flush()

    return items
