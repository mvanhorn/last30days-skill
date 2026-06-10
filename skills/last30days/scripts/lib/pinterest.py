"""Pinterest search via ScrapeCreators API for /last30days.

Uses ScrapeCreators REST API to search Pinterest by keyword, extract
engagement metrics (saves, comments), and return pin descriptions.

Requires SCRAPECREATORS_API_KEY in config. 100 free API calls, then PAYG.
API docs: https://scrapecreators.com/docs
"""

import re
import sys
from typing import Any, Dict, List, Optional, Set

from . import apify_runner, dates, http, log

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/pinterest"
APIFY_PINTEREST_ACTOR = "automation-lab~pinterest-scraper"

# Depth configurations: how many results to fetch
DEPTH_CONFIG = {
    "quick":   {"results_per_page": 10},
    "default": {"results_per_page": 20},
    "deep":    {"results_per_page": 40},
}

from .relevance import token_overlap_relevance as _compute_relevance


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for Pinterest search."""
    from .query import extract_core_subject
    _PINTEREST_NOISE = frozenset({
        'best', 'top', 'good', 'great', 'awesome', 'killer',
        'latest', 'new', 'news', 'update', 'updates',
        'trending', 'hottest', 'popular', 'viral',
        'practices', 'features',
        'recommendations', 'advice',
        'prompt', 'prompts', 'prompting',
        'methods', 'strategies', 'approaches',
    })
    return extract_core_subject(topic, noise=_PINTEREST_NOISE)


def _log(msg: str):
    log.source_log("Pinterest", msg)


def _parse_items(raw_items: List[Dict[str, Any]], core_topic: str) -> List[Dict[str, Any]]:
    """Parse raw Pinterest items into normalized dicts.

    Pinterest pins are visual content with descriptions. Saves are the
    primary engagement signal (analogous to upvotes/likes on other platforms).
    """
    items = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        pin_id = str(raw.get("id", raw.get("pin_id", "")))
        description = str(raw.get("description") or raw.get("title") or "")

        # Engagement metrics - saves are the primary signal
        save_count = raw.get("save_count") or raw.get("saves") or raw.get("repin_count") or 0
        comment_count = raw.get("comment_count") or raw.get("comments") or 0

        # Author info
        pinner = raw.get("pinner") or raw.get("creator") or raw.get("user") or {}
        if isinstance(pinner, dict):
            author_name = pinner.get("username") or pinner.get("full_name") or ""
        elif isinstance(pinner, str):
            author_name = pinner
        else:
            author_name = raw.get("pinnerUsername") or raw.get("pinnerName") or ""

        # URL
        url = raw.get("link") or raw.get("url") or ""
        if not url and pin_id:
            url = f"https://www.pinterest.com/pin/{pin_id}/"

        # Board info (container for pins)
        board = raw.get("board") or {}
        board_name = board.get("name", "") if isinstance(board, dict) else ""
        if not board_name:
            board_name = raw.get("boardName", "")

        date_str = None
        created_at = raw.get("createdAt")
        if created_at:
            parsed = dates.parse_date(str(created_at))
            if parsed:
                date_str = parsed.strftime("%Y-%m-%d")

        # Compute relevance
        relevance = _compute_relevance(core_topic, description, [])

        items.append({
            "pin_id": pin_id,
            "description": description,
            "url": url,
            "author": author_name,
            "board": board_name,
            "date": date_str,
            "engagement": {
                "saves": save_count,
                "comments": comment_count,
            },
            "relevance": relevance,
            "why_relevant": f"Pinterest: {description[:60]}" if description else f"Pinterest: {core_topic}",
        })
    return items


def _search_pinterest_apify(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str,
    tokens: List[str] | None,
) -> Dict[str, Any]:
    """Search Pinterest pins via Apify actor."""
    if not apify_runner.dedupe_tokens(tokens):
        return {"items": [], "error": "No APIFY_API_TOKEN configured", "provider": "apify"}

    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)
    payload = {
        "searchQueries": [core_topic],
        "maxPins": config["results_per_page"],
    }
    _log(
        f"Searching Pinterest via Apify for '{core_topic}' "
        f"(depth={depth}, count={config['results_per_page']})"
    )

    try:
        raw_items = apify_runner.run_actor_dataset_items(
            APIFY_PINTEREST_ACTOR,
            payload,
            tokens,
            timeout=60,
        )
    except Exception as e:
        _log(f"Apify error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "apify"}

    items = _parse_items(raw_items[:config["results_per_page"]], core_topic)
    in_range = [i for i in items if i.get("date") and from_date <= i["date"] <= to_date]
    if in_range:
        items = in_range

    items.sort(key=lambda x: x["engagement"]["saves"], reverse=True)
    _log(f"Found {len(items)} Pinterest pins via Apify")
    return {"items": items, "provider": "apify"}


def parse_pinterest_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse Pinterest search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])


def search_pinterest(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = None,
    apify_tokens: List[str] | None = None,
) -> Dict[str, Any]:
    """Search Pinterest via ScrapeCreators API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    if not token and apify_tokens:
        return _search_pinterest_apify(topic, from_date, to_date, depth, apify_tokens)
    if not token:
        return {"items": [], "error": "No Pinterest provider token configured"}

    config = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching Pinterest for '{core_topic}' (depth={depth}, count={config['results_per_page']})")

    try:
        data = http.get(
            f"{SCRAPECREATORS_BASE}/search",
            params={"keyword": core_topic},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except http.HTTPError as e:
        if e.status_code == 402 and apify_tokens:
            _log("ScrapeCreators returned 402, falling back to Apify")
            return _search_pinterest_apify(topic, from_date, to_date, depth, apify_tokens)
        _log(f"ScrapeCreators error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "scrapecreators"}
    except Exception as e:
        _log(f"ScrapeCreators error: {e}")
        return {"items": [], "error": f"{type(e).__name__}: {e}", "provider": "scrapecreators"}

    # Extract items from response - try common SC response shapes
    raw_items = data.get("pins") or data.get("results") or data.get("data") or data.get("items") or []

    # Limit to configured count
    raw_items = raw_items[:config["results_per_page"]]

    # Parse items
    items = _parse_items(raw_items, core_topic)

    # Sort by saves descending (primary engagement signal)
    items.sort(key=lambda x: x["engagement"]["saves"], reverse=True)

    _log(f"Found {len(items)} Pinterest pins")
    return {"items": items, "provider": "scrapecreators"}
