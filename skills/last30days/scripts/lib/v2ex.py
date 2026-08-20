"""V2EX source via the free public topics API.

Zero auth, zero cost: GET /api/topics/hot.json (10 hot topics) and
/api/topics/latest.json (~50 latest). Keyword filtering happens engine-side
by the shared relevance machinery — the API has no search endpoint.

Default-on like digg/arxiv (free + public), but quiet off-topic: if no topic
matches the query terms, zero items are returned.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from . import http, log

BASE = "https://www.v2ex.com"

_DEPTH_SOURCES = {
    "quick": ("hot",),
    "default": ("hot", "latest"),
    "deep": ("hot", "latest"),
}


def search_v2ex(
    query: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> list[dict[str, Any]]:
    feeds = _DEPTH_SOURCES.get(depth, ("hot", "latest"))
    raw: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feed in feeds:
        try:
            batch = http.get(f"{BASE}/api/topics/{feed}.json", timeout=15, retries=1)
        except (OSError, http.HTTPError) as exc:
            log.source_log("v2ex", f"fetch failed: {exc}")
            continue
        if not isinstance(batch, list):
            continue
        for topic in batch:
            if isinstance(topic, dict) and str(topic.get("id")) not in seen:
                seen.add(str(topic.get("id")))
                raw.append(topic)
    return parse_v2ex_response(raw, query=query, from_date=from_date, to_date=to_date)


_TOKEN_RE = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)


def _query_terms(query: str) -> list[str]:
    # Keep CJK runs and latin words of length >= 2 (skip bare noise like 'vs').
    terms = [
        token.group(0).lower()
        for token in _TOKEN_RE.finditer(query or "")
        if len(token.group(0)) >= 2
    ]
    return terms


def _to_date_str(value: Any) -> str | None:
    """Normalize any timestamp/ISO form to YYYY-MM-DD (engine contract)."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        seconds = value / 1000.0 if value >= 10**12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10] if len(text) >= 10 else None


def parse_v2ex_response(
    raw: list[dict[str, Any]],
    query: str = "",
    from_date: str = "",
    to_date: str = "",
) -> list[dict[str, Any]]:
    terms = _query_terms(query)
    items: list[dict[str, Any]] = []
    for index, topic in enumerate(raw):
        if not isinstance(topic, dict):
            continue
        title = str(topic.get("title") or "").strip()
        url = str(topic.get("url") or "").strip()
        if not title and not url:
            continue
        content = str(topic.get("content") or "").strip()
        haystack = f"{title}\n{content}".lower()
        matched = [term for term in terms if term in haystack]
        if terms and not matched:
            continue  # keep V2EX quiet on off-topic queries
        replies = topic.get("replies") or 0
        try:
            replies = int(replies)
        except (TypeError, ValueError):
            replies = 0
        created = topic.get("created")  # epoch seconds
        date_str = _to_date_str(created)
        node = str((topic.get("node") or {}).get("name") or "") if isinstance(topic.get("node"), dict) else ""
        member = str((topic.get("member") or {}).get("username") or "") if isinstance(topic.get("member"), dict) else ""
        why = f"V2EX discussion: replies={replies}"
        if matched:
            why += f", matched {', '.join(matched[:3])}"
        items.append({
            "id": str(topic.get("id") or f"V2EX{index + 1}"),
            "title": title[:200] if title else f"V2EX topic {index + 1}",
            "url": url,
            "source_domain": "v2ex.com",
            "snippet": (content or title)[:500],
            "date": date_str,
            "relevance": min(0.85, 0.35 + replies / 500) if replies else 0.5,
            "why_relevant": why,
            "engagement": {"comments": replies},
            "metadata": {"node": node, "author": member},
        })
    return items
