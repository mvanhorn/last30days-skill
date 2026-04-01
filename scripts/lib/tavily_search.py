"""Tavily web search for last30days skill.

Uses the Tavily Search API as a web search backend.
Tavily is optimized for LLM consumption and returns relevance-scored results.

API docs: https://docs.tavily.com/
"""

import sys
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from tavily import TavilyClient

# Domains to exclude (handled by Reddit/X search)
EXCLUDED_DOMAINS = {
    "reddit.com", "www.reddit.com", "old.reddit.com",
    "twitter.com", "www.twitter.com", "x.com", "www.x.com",
}

# Map date range (days) to Tavily time_range parameter
# Tavily supports: "day", "week", "month", "year", or None (all time)
_TIME_RANGE_MAP = [
    (1, "day"),
    (7, "week"),
    (31, "month"),
    (365, "year"),
]


def _compute_time_range(from_date: str, to_date: str) -> Optional[str]:
    """Convert from_date/to_date to Tavily time_range parameter."""
    try:
        d1 = datetime.strptime(from_date, "%Y-%m-%d")
        d2 = datetime.strptime(to_date, "%Y-%m-%d")
        days = max(1, (d2 - d1).days)
    except (ValueError, TypeError):
        return "month"

    for threshold, code in _TIME_RANGE_MAP:
        if days <= threshold:
            return code
    return "year"


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
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        api_key: Tavily API key
        depth: 'quick', 'default', or 'deep'

    Returns:
        List of result dicts with keys: id, title, url, source_domain,
        snippet, date, date_confidence, relevance, why_relevant
    """
    max_results = {"quick": 8, "default": 15, "deep": 25}.get(depth, 15)
    search_depth = {"quick": "basic", "default": "basic", "deep": "advanced"}.get(depth, "basic")
    time_range = _compute_time_range(from_date, to_date)

    sys.stderr.write(f"[Web] Searching Tavily for: {topic}\n")
    sys.stderr.flush()

    client = TavilyClient(api_key=api_key)

    response = client.search(
        query=topic,
        max_results=max_results,
        search_depth=search_depth,
        topic="news",
        time_range=time_range,
        exclude_domains=list(EXCLUDED_DOMAINS),
    )

    return _normalize_results(response)


def _normalize_results(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert Tavily response to the shared websearch item schema."""
    items = []
    results = response.get("results", [])

    for i, result in enumerate(results):
        if not isinstance(result, dict):
            continue

        url = result.get("url", "")
        if not url:
            continue

        # Skip excluded domains (belt-and-suspenders; also filtered via API param)
        try:
            domain = urlparse(url).netloc.lower()
            if domain in EXCLUDED_DOMAINS:
                continue
            if domain.startswith("www."):
                domain = domain[4:]
        except (ValueError, TypeError):
            domain = ""

        title = str(result.get("title", "")).strip()
        snippet = str(result.get("content", "")).strip()

        if not title and not snippet:
            continue

        # Tavily provides a relevance score (0-1)
        score = result.get("score", 0.5)

        # Tavily does not return per-result dates
        date = result.get("published_date")
        date_confidence = "med" if date else "low"

        items.append({
            "id": f"W{i+1}",
            "title": title[:200],
            "url": url,
            "source_domain": domain,
            "snippet": snippet[:500],
            "date": date,
            "date_confidence": date_confidence,
            "relevance": round(score, 3),
            "why_relevant": "",
        })

    sys.stderr.write(f"[Web] Tavily: {len(items)} results\n")
    sys.stderr.flush()

    return items
