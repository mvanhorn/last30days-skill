"""Telegram public channel posts via ScrapeCreators API for /last30days.

Uses ScrapeCreators REST API to fetch recent posts from named public Telegram
channels.  No keyword search - channel handles only.

Requires SCRAPECREATORS_API_KEY in config plus a channel list (TELEGRAM_SOURCES
env var or --telegram-sources CLI flag).

API docs: https://docs.scrapecreators.com/v1/telegram/channel/posts
"""

import math
import os
import re
from typing import Any

from . import dates, http, log
from .relevance import token_overlap_relevance as _compute_relevance

SCRAPECREATORS_BASE = "https://api.scrapecreators.com/v1/telegram"

DEPTH_PAGE_CAPS = {
    "quick": 1,
    "default": 3,
    "deep": 6,
}


def _log(msg: str):
    log.source_log("Telegram", msg, tty_only=False)


class InvalidChannelHandle(ValueError):
    """Raised when a channel handle is rejected (joinchat, numeric -100 ID)."""


def parse_channel_handle(raw: str) -> str:
    """Normalize a Telegram channel identifier to a bare handle.

    Accepts:
        - bare username: aipost
        - @handle: @aipost
        - t.me URL: https://t.me/aipost
        - t.me/s preview URL: https://t.me/s/aipost

    Rejects (raises InvalidChannelHandle):
        - joinchat links: https://t.me/joinchat/xxxxx
        - numeric -100 supergroup IDs: -1001234567890

    Returns:
        Bare handle string (no @ prefix).
    """
    handle = raw.strip()
    if not handle:
        raise InvalidChannelHandle("Empty channel handle")

    if handle.lstrip("-").isdigit() and handle.startswith("-100"):
        raise InvalidChannelHandle(
            f"Numeric supergroup IDs are not supported: {handle}"
        )

    if handle.startswith("@"):
        handle = handle[1:]
        if not handle:
            raise InvalidChannelHandle("Empty handle after @ prefix")
        return handle

    url_match = re.match(
        r"(?:https?://)?(?:www\.)?t\.me/(?:s/)?([^/?#]+)",
        handle,
        re.IGNORECASE,
    )
    if url_match:
        extracted = url_match.group(1)
        if extracted.lower() == "joinchat":
            raise InvalidChannelHandle(
                f"Private joinchat links are not supported: {raw}"
            )
        return extracted

    if "joinchat" in handle.lower():
        raise InvalidChannelHandle(
            f"Private joinchat links are not supported: {raw}"
        )

    return handle


def parse_channel_sources(raw: str) -> list[str]:
    """Parse a comma-separated list of channel handles.

    Filters out invalid handles (logs a warning) and returns valid ones.
    """
    handles: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            handle = parse_channel_handle(part)
            if handle.lower() not in {h.lower() for h in handles}:
                handles.append(handle)
        except InvalidChannelHandle as exc:
            _log(f"Skipping invalid channel: {exc}")
    return handles


def _get_channel_sources(config: dict[str, Any]) -> list[str]:
    """Get configured Telegram channel sources from config or env."""
    raw = config.get("TELEGRAM_SOURCES") or os.environ.get("TELEGRAM_SOURCES") or ""
    return parse_channel_sources(raw)


def is_telegram_configured(config: dict[str, Any]) -> bool:
    """True when Telegram has both API key and at least one channel."""
    return bool(
        config.get("SCRAPECREATORS_API_KEY")
        and _get_channel_sources(config)
    )


def _parse_date(item: dict[str, Any]) -> str | None:
    """Parse date from Telegram post to YYYY-MM-DD."""
    for key in ("published_at", "date", "created_at"):
        val = item.get(key)
        if val is None:
            continue
        dt = dates.parse_date(str(val))
        if dt:
            return dt.strftime("%Y-%m-%d")
    return None


def _parse_post(
    raw: dict[str, Any],
    channel: dict[str, Any],
    topic: str,
    index: int,
) -> dict[str, Any]:
    """Parse a single Telegram post into normalized dict."""
    post_id = str(raw.get("id") or f"TG{index + 1}")
    text = str(raw.get("text") or "").strip()
    url = str(raw.get("url") or "")
    date_str = _parse_date(raw)

    handle = str(raw.get("channel_handle") or channel.get("handle") or "")
    author_name = str(raw.get("author_name") or channel.get("name") or handle)

    view_count = raw.get("view_count") or 0
    reaction_count = raw.get("reaction_count") or 0
    subscriber_count = channel.get("subscriber_count") or 0

    text_relevance = _compute_relevance(topic, text)
    rank_score = max(0.3, 1.0 - (index * 0.02))
    engagement_boost = min(0.2, math.log1p(view_count + reaction_count * 10) / 50)
    relevance = min(1.0, text_relevance * 0.5 + rank_score * 0.3 + engagement_boost + 0.1)

    return {
        "id": post_id,
        "handle": handle,
        "display_name": author_name,
        "text": text,
        "url": url,
        "date": date_str,
        "engagement": {
            "views": view_count,
            "reactions": reaction_count,
            "subscribers": subscriber_count,
        },
        "relevance": round(relevance, 2),
        "why_relevant": f"Telegram @{handle}: {text[:60]}" if text else f"Telegram: @{handle}",
    }


def _fetch_channel_posts(
    handle: str,
    token: str,
    *,
    from_date: str,
    topic: str,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Fetch posts from a single channel, paginating until date cutoff."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    pages_fetched = 0

    while pages_fetched < max_pages:
        _log(f"Fetching @{handle} (page {pages_fetched + 1}/{max_pages})")

        params: dict[str, Any] = {"handle": handle}
        if cursor:
            params["cursor"] = cursor

        try:
            data = http.get(
                f"{SCRAPECREATORS_BASE}/channel/posts",
                params=params,
                headers=http.scrapecreators_headers(token),
                timeout=30,
                retries=2,
            )
        except http.HTTPError as exc:
            _log(f"HTTP error fetching @{handle}: {exc}")
            break

        if not data.get("success"):
            error_msg = data.get("error") or data.get("message") or "Unknown error"
            _log(f"API error for @{handle}: {error_msg}")
            break

        channel = data.get("channel") or {}
        posts = data.get("posts") or []

        if not posts:
            _log(f"No posts returned for @{handle}")
            break

        page_all_old = True
        for idx, raw_post in enumerate(posts):
            parsed = _parse_post(raw_post, channel, topic, len(items) + idx)
            items.append(parsed)
            if parsed["date"] and parsed["date"] >= from_date:
                page_all_old = False

        pages_fetched += 1

        if page_all_old:
            _log(f"All posts on page older than {from_date}, stopping pagination")
            break

        cursor = data.get("cursor")
        if not data.get("has_more") or not cursor:
            break

    return items


def search_telegram(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch recent posts from configured Telegram channels.

    Args:
        topic: Search topic (for relevance scoring)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        token: ScrapeCreators API key
        config: Config dict (for TELEGRAM_SOURCES)

    Returns:
        Dict with 'items' list and optional 'error'.
    """
    config = config or {}

    if not token:
        return {"items": [], "error": "No SCRAPECREATORS_API_KEY configured"}

    channels = _get_channel_sources(config)
    if not channels:
        return {"items": [], "error": "No TELEGRAM_SOURCES configured (channel list required)"}

    base_cap = DEPTH_PAGE_CAPS.get(depth, DEPTH_PAGE_CAPS["default"])
    override = config.get("TELEGRAM_MAX_PAGES")
    if override:
        try:
            max_pages = max(base_cap, int(override))
        except (ValueError, TypeError):
            max_pages = base_cap
    else:
        max_pages = base_cap

    _log(f"Searching {len(channels)} channel(s) for '{topic}' (depth={depth}, max_pages={max_pages})")

    all_items: list[dict[str, Any]] = []
    for handle in channels:
        channel_items = _fetch_channel_posts(
            handle,
            token,
            from_date=from_date,
            topic=topic,
            max_pages=max_pages,
        )
        all_items.extend(channel_items)

    in_range = [
        item for item in all_items
        if item["date"] and from_date <= item["date"] <= to_date
    ]
    out_of_range = len(all_items) - len(in_range)
    if in_range:
        items = in_range
        if out_of_range:
            _log(f"Filtered {out_of_range} posts outside date range")
    else:
        items = all_items
        _log(f"No posts within date range, keeping all {len(items)}")

    items.sort(key=lambda x: x.get("relevance", 0), reverse=True)

    _log(f"Found {len(items)} Telegram posts")
    return {"items": items}


def parse_telegram_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse Telegram search response to normalized format.

    Returns:
        List of item dicts ready for normalization.
    """
    return response.get("items", [])
