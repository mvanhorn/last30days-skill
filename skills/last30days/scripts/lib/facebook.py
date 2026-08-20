"""Facebook source via Apify keyword-search actor.

Actor: scraper_one/facebook-posts-search (pay-per-event, ~$0.0025/item FREE tier).
Keyword search over public posts with date filters — no cookies, no login.

Dataset row shape (verified 2026-08-16):
  postId, postText, url, timestamp (epoch ms), reactionsCount,
  commentsCount, sharesCount, reactions{like,love,...}, author{name}.

Env: APIFY_API_TOKEN. Opt-in via INCLUDE_SOURCES=facebook (paid-source consent
pattern, same as linkedin/perplexity).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import apify_client

ACTOR = "scraper_one/facebook-posts-search"

# Paid per item — keep caps tight.
_DEPTH_CAPS = {"quick": 5, "default": 10, "deep": 15}


def search_facebook(
    query: str,
    from_date: str,
    to_date: str,
    token: str = "",
    depth: str = "default",
) -> list[dict[str, Any]]:
    """Search Facebook posts by keyword. Returns grounding-shape web items."""
    cap = _DEPTH_CAPS.get(depth, 10)
    run_input: dict[str, Any] = {
        "query": query,
        "resultsCount": cap,
        "searchType": "top",
    }
    if from_date:
        run_input["startDate"] = from_date[:10]
    if to_date:
        run_input["endDate"] = to_date[:10]
    raw = apify_client.run_sync(ACTOR, run_input, token, item_cap=cap, timeout=150)
    return parse_facebook_response(raw, query=query)


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


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
    # ISO strings: take the date part.
    return text[:10] if len(text) >= 10 else None


def parse_facebook_response(raw: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("postText") or entry.get("text") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not text and not url:
            continue
        reactions = _to_int(entry.get("reactionsCount"))
        comments = _to_int(entry.get("commentsCount"))
        shares = _to_int(entry.get("sharesCount"))
        timestamp = entry.get("timestamp")
        date_str = _to_date_str(timestamp)
        author = entry.get("author") or {}
        author_name = str(author.get("name") or "") if isinstance(author, dict) else ""
        engagement_total = reactions + comments + shares
        why = f"Facebook engagement: reactions={reactions}, comments={comments}, shares={shares}"
        items.append({
            "id": str(entry.get("postId") or f"FB{index + 1}"),
            "title": (text[:120].replace("\n", " ") if text else f"Facebook post {index + 1}"),
            "url": url,
            "source_domain": "facebook.com",
            "snippet": text[:500],
            "date": date_str,
            "relevance": min(0.85, 0.4 + engagement_total / 5000) if engagement_total else 0.5,
            "why_relevant": why,
            "engagement": {
                "reactions": reactions,
                "comments": comments,
                "shares": shares,
            },
            "metadata": {"author": author_name} if author_name else {},
        })
    return items
