"""Bilibili source via Apify.

Actor: zhorex/bilibili-scraper (pay-per-event: $0.02/item, $0.05/danmaku).
Search mode with date bounds — no cookies, no login.

Dataset row shape (verified 2026-08-16):
  type=video, bvid, title, description, url, viewCount, likeCount, coinCount,
  favoriteCount, shareCount, danmakuCount, replyCount, publishTimestamp (epoch s).

Env: APIFY_API_TOKEN. Opt-in via INCLUDE_SOURCES=bilibili (paid-source consent
pattern). Comments/danmaku/replies stay OFF (cost multipliers).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import apify_client

ACTOR = "zhorex/bilibili-scraper"

# $0.02/item — the priciest actor in the stack; caps stay tight.
_DEPTH_CAPS = {"quick": 5, "default": 10, "deep": 15}


def search_bilibili(
    query: str,
    from_date: str,
    to_date: str,
    token: str = "",
    depth: str = "default",
) -> list[dict[str, Any]]:
    cap = _DEPTH_CAPS.get(depth, 10)
    run_input: dict[str, Any] = {
        "mode": "search",
        "searchQuery": query,
        "maxResults": cap,
        "sortOrder": "totalrank",  # relevance-ranked, not just newest
        "autoLocalize": True,  # Latin brand names also search their Chinese name
        "includeComments": False,
        "includeReplies": False,
        "includeDanmaku": False,
    }
    if from_date:
        run_input["pubtimeBegin"] = from_date[:10]
    if to_date:
        run_input["pubtimeEnd"] = to_date[:10]
    raw = apify_client.run_sync(ACTOR, run_input, token, item_cap=cap, timeout=180)
    return parse_bilibili_response(raw, query=query)


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    # Bilibili UI stats sometimes arrive as strings like "1.2万" (12,000)
    mult = 1
    if "万" in text:
        mult = 10000
        text = text.replace("万", "").replace("+", "")
    elif "亿" in text:
        mult = 100000000
        text = text.replace("亿", "").replace("+", "")
    try:
        return int(float(text) * mult)
    except ValueError:
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


def parse_bilibili_response(raw: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "video") != "video":
            continue
        title = str(entry.get("title") or "").strip()
        bvid = str(entry.get("bvid") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not url and bvid:
            url = f"https://www.bilibili.com/video/{bvid}"
        if not title and not url:
            continue
        views = _to_int(entry.get("viewCount"))
        likes = _to_int(entry.get("likeCount"))
        coins = _to_int(entry.get("coinCount"))
        favs = _to_int(entry.get("favoriteCount"))
        shares = _to_int(entry.get("shareCount"))
        danmaku = _to_int(entry.get("danmakuCount"))
        replies = _to_int(entry.get("replyCount"))
        date_str = _to_date_str(entry.get("publishTimestamp"))
        desc = str(entry.get("description") or "").strip()
        snippet = (desc or title)[:500]
        engagement_total = views + likes + coins + favs + danmaku + replies
        why = (
            f"Bilibili engagement: views={views}, likes={likes}, coins={coins}, "
            f"favorites={favs}, danmaku={danmaku}, replies={replies}"
        )
        items.append({
            "id": str(entry.get("aid") or bvid or f"BILI{index + 1}"),
            "title": title[:200] if title else f"Bilibili video {bvid or index + 1}",
            "url": url,
            "source_domain": "bilibili.com",
            "snippet": snippet,
            "date": date_str,
            "relevance": min(0.9, 0.3 + engagement_total / 1_000_000) if engagement_total else 0.5,
            "why_relevant": why,
            "engagement": {
                "views": views,
                "likes": likes,
                "coins": coins,
                "favorites": favs,
                "danmaku": danmaku,
                "comments": replies,
                "shares": shares,
            },
        })
    return items
