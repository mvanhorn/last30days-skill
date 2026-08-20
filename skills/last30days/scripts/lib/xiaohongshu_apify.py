"""Xiaohongshu source via Apify Pro Scraper.

Actor: habit.zhou/xiaohongshu-pro-scraper (pay-per-event: $0.005/search row).
Keyword search — no cookies, no login, 7 modes.

Dataset row shape (verified 2026-08-16):
  noteId, title, bodyText, noteUrl, likes, collects, commentsCount, shares,
  publishedAt, author, hashtags, isVideo.

Env: APIFY_API_TOKEN. Opt-in via INCLUDE_SOURCES=xiaohongshu.

This module is the default backend when APIFY_API_TOKEN is present; the
cookie-gated local HTTP API (xiaohongshu_api.py) remains as fallback.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import apify_client

ACTOR = "habit.zhou/xiaohongshu-pro-scraper"

_DEPTH_CAPS = {"quick": 8, "default": 15, "deep": 25}
_TIME_FILTERS = {"quick": "一周内", "default": "一周内", "deep": "半年内"}


def search_xiaohongshu(
    query: str,
    from_date: str,
    to_date: str,
    token: str = "",
    depth: str = "default",
) -> list[dict[str, Any]]:
    cap = _DEPTH_CAPS.get(depth, 15)
    run_input: dict[str, Any] = {
        "mode": "search",
        "keywords": [query],
        "maxItemsPerInput": cap,
        "sortType": "general",
        "noteType": "不限",
        "timeFilter": _TIME_FILTERS.get(depth, "一周内"),
        "fetchComments": False,
    }
    raw = apify_client.run_sync(ACTOR, run_input, token, item_cap=cap, timeout=180)
    return parse_xiaohongshu_response(raw, query=query)


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
    return text[:10] if len(text) >= 10 else None


def parse_xiaohongshu_response(raw: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        note_id = str(entry.get("noteId") or entry.get("id") or "").strip()
        url = str(entry.get("noteUrl") or entry.get("url") or "").strip()
        if not url and note_id:
            url = f"https://www.xiaohongshu.com/explore/{note_id}"
        desc = str(entry.get("bodyText") or entry.get("desc") or "").strip()
        if not title and not desc and not url:
            continue
        likes = _to_int(entry.get("likes") or entry.get("likedCount"))
        comments = _to_int(entry.get("commentsCount") or entry.get("commentCount"))
        collects = _to_int(entry.get("collects") or entry.get("collectedCount"))
        shares = _to_int(entry.get("shares"))
        date_str = _to_date_str(entry.get("publishedAt") or entry.get("_createTime"))
        author = str(entry.get("author") or "").strip()
        engagement_total = likes + comments + collects + shares
        why = (
            f"Xiaohongshu engagement: likes={likes}, comments={comments}, "
            f"collections={collects}, shares={shares}"
        )
        items.append({
            "id": note_id or f"XHS{index + 1}",
            "title": title[:200] if title else f"Xiaohongshu note {note_id or index + 1}",
            "url": url,
            "source_domain": "xiaohongshu.com",
            "snippet": (desc or title)[:500],
            "date": date_str,
            "relevance": min(0.9, 0.35 + engagement_total / 100000) if engagement_total else 0.5,
            "why_relevant": why,
            "engagement": {
                "likes": likes,
                "comments": comments,
                "collections": collects,
                "shares": shares,
            },
            "metadata": {"author": author} if author else {},
        })
    return items
