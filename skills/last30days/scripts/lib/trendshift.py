"""Trendshift source for last30days.

Fetches trending GitHub repositories from Trendshift's public API
(https://trendshift.io) — no API key required.

Trendshift tracks daily/weekly GitHub trending and enriches each repo with
star history, contributor counts, and language. The source is opt-in (off by
default), activated via ``INCLUDE_SOURCES=trendshift`` or ``--search
trendshift``, or per-run via ``--search trendshift``.

Query strategy: the public ``/api/trending-repositories`` endpoint does not
accept freetext queries; it returns a ranked list of currently trending repos.
We post-filter by token-overlap against the topic to keep relevant results.
"""

from __future__ import annotations

import datetime
from typing import Any

from . import http, log
from .relevance import token_overlap_relevance

TRENDSHIFT_BASE = "https://trendshift.io"
TRENDING_URL = f"{TRENDSHIFT_BASE}/api/trending-repositories"

# How many repos to fetch from the API; the public endpoint returns up to 25
# per page. We fetch one page — sufficient for relevance filtering.
FETCH_LIMIT = {"quick": 10, "default": 20, "deep": 25}
# Token-overlap floor; repos below this are dropped as off-topic.
RELEVANCE_FLOOR = 0.10
REQUEST_TIMEOUT = 15


def _log(msg: str) -> None:
    log.source_log("Trendshift", msg, tty_only=False)


def _parse_repo(raw: dict[str, Any], topic: str) -> dict[str, Any] | None:
    """Normalize a raw Trendshift repo dict into a last30days item."""
    full_name = (
        raw.get("full_name")
        or raw.get("nameWithOwner")
        or raw.get("name")
        or ""
    )
    description = raw.get("description") or ""
    url = raw.get("url") or raw.get("html_url") or ""
    if full_name and not url:
        url = f"https://github.com/{full_name}"

    # Engagement
    stars = raw.get("stargazers_count") or raw.get("star_count") or raw.get("stars") or 0
    forks = raw.get("forks_count") or raw.get("forks") or 0

    # Language
    language = raw.get("language") or raw.get("primaryLanguage") or ""

    # Date — Trendshift returns created_at/pushed_at in ISO 8601
    date_str = ""
    for date_field in ("pushed_at", "updated_at", "created_at"):
        raw_date = raw.get(date_field) or ""
        if raw_date and len(raw_date) >= 10:
            date_str = raw_date[:10]
            break

    # Relevance check: combine name + description for matching
    text_for_relevance = f"{full_name.replace('/', ' ')} {description}"
    relevance = token_overlap_relevance(topic, text_for_relevance)

    title = full_name or description[:60]
    snippet = description[:300] if description else full_name

    return {
        "id": full_name,
        "title": title,
        "url": url,
        "text": snippet,
        "date": date_str,
        "engagement": {"stars": int(stars), "forks": int(forks)},
        "language": language,
        "relevance": relevance,
        "source": "trendshift",
        "why_relevant": f"Trendshift: {description[:80]}" if description else f"Trendshift trending: {full_name}",
    }


def search_trendshift(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> dict[str, Any]:
    """Fetch trending GitHub repos from Trendshift and filter by topic.

    Args:
        topic: The research topic to filter repos against.
        from_date: Start of the search window (YYYY-MM-DD). Used to
            optionally filter repos with known push dates.
        to_date: End of the search window (YYYY-MM-DD).
        depth: 'quick', 'default', or 'deep'.

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    limit = FETCH_LIMIT.get(depth, FETCH_LIMIT["default"])
    _log(f"Fetching trending repos for '{topic}' (depth={depth}, limit={limit})")

    try:
        data = http.get(
            TRENDING_URL,
            params={"limit": limit},
            timeout=REQUEST_TIMEOUT,
            retries=2,
        )
    except Exception as exc:
        _log(f"Trendshift fetch error: {exc}")
        return {"items": [], "error": f"{type(exc).__name__}: {exc}"}

    # The API returns a list directly or wraps it in a key
    raw_list = data if isinstance(data, list) else (
        data.get("repositories") or data.get("repos") or data.get("data") or []
    )
    _log(f"  -> {len(raw_list)} repos from Trendshift")

    items = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        parsed = _parse_repo(raw, topic)
        if parsed is None:
            continue
        # Apply relevance floor
        if parsed["relevance"] < RELEVANCE_FLOOR:
            continue
        items.append(parsed)

    # Sort by stars descending
    items.sort(key=lambda x: x["engagement"]["stars"], reverse=True)
    _log(f"Kept {len(items)} relevant repos after filtering")
    return {"items": items}
