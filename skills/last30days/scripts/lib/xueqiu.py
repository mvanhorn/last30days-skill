"""Xueqiu source via Apify.

Actor: zhorex/xueqiu-scraper (pay-per-event: $0.005/post).
Trending mode ONLY — anonymous access, no cookie. Post-search/ticker-post
modes need a logged-in xueqiu.com cookie which we deliberately do not use
(cookie/ban-risk policy). Trending surfaces what Chinese retail investors
are discussing right now; relevance filtering happens engine-side.

Dataset row shape (verified 2026-08-16):
  postId, postText, postUrl, publishedAt (ISO), author{screenName, userId},
  metrics{likeCount, commentCount, retweetCount, viewCount}, tickersInPost[].

Env: APIFY_API_TOKEN. Opt-in via INCLUDE_SOURCES=xueqiu.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import apify_client

ACTOR = "zhorex/xueqiu-scraper"

_DEPTH_CAPS = {"quick": 20, "default": 30, "deep": 50}


def search_xueqiu(
    query: str,
    from_date: str,
    to_date: str,
    token: str = "",
    depth: str = "default",
) -> list[dict[str, Any]]:
    """Trending Xueqiu posts (anonymous). ``query`` is used for engine-side
    relevance, not sent to Xueqiu — the actor has no anonymous keyword mode."""
    cap = _DEPTH_CAPS.get(depth, 30)
    run_input: dict[str, Any] = {
        "mode": "trending",
        "maxResults": cap,
        "includeRetweets": False,
    }
    raw = apify_client.run_sync(ACTOR, run_input, token, item_cap=cap, timeout=150)
    return parse_xueqiu_response(raw, query=query)


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


def parse_xueqiu_response(raw: list[dict[str, Any]], query: str = "") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("postText") or entry.get("text") or "").strip()
        url = str(entry.get("postUrl") or entry.get("url") or "").strip()
        if not text and not url:
            continue
        post_id = str(entry.get("postId") or "")
        if not url and post_id:
            url = f"https://xueqiu.com/status/{post_id}"
        metrics = entry.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        likes = _to_int(metrics.get("likeCount"))
        replies = _to_int(metrics.get("commentCount"))
        reposts = _to_int(metrics.get("retweetCount"))
        reads = _to_int(metrics.get("viewCount"))
        author = entry.get("author") or {}
        author_name = str(author.get("screenName") or "") if isinstance(author, dict) else ""
        tickers = entry.get("tickersInPost") or []
        ticker_note = f", tickers={','.join(str(t) for t in tickers[:3])}" if isinstance(tickers, list) and tickers else ""
        date_str = _to_date_str(entry.get("publishedAt"))
        engagement_total = likes + replies + reposts
        why = f"Xueqiu trending: likes={likes}, replies={replies}, reposts={reposts}, reads={reads}{ticker_note}"
        items.append({
            "id": post_id or f"XQ{index + 1}",
            "title": (text[:120] if text else f"Xueqiu post {index + 1}"),
            "url": url,
            "source_domain": "xueqiu.com",
            "snippet": text[:500],
            "date": date_str or None,
            "relevance": min(0.85, 0.4 + engagement_total / 10000) if engagement_total else 0.5,
            "why_relevant": why,
            "engagement": {
                "likes": likes,
                "comments": replies,
                "reposts": reposts,
                "reads": reads,
            },
            "metadata": {"author": author_name} if author_name else {},
        })
    return items
