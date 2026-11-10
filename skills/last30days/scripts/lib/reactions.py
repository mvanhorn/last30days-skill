"""Reactions: the top-voted comments under each thread, grouped by venue.

A reactions run (``--reactions``) reads how a launch landed room by room, so
it needs more comments than the default report shows and it needs them kept
apart by venue. Selection is by votes only. Nothing here groups comments by
theme: the reading model does that.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import schema

# Comments kept per thread when the deeper pass retrieves them.
THREAD_COMMENT_LIMIT = 40
# Crowd comments shown per thread, and per venue across its threads.
PER_ITEM_LIMIT = 10
PER_VENUE_LIMIT = 25
# The launcher's own replies are kept beyond the per-thread cut, up to this many.
LAUNCHER_REPLY_LIMIT = 3

_VENUE_NAMES = {
    "hackernews": "Hacker News",
    "devto": "dev.to",
    "youtube": "YouTube",
    "tiktok": "TikTok",
    "instagram": "Instagram",
    "github": "GitHub",
    "amazon": "Amazon reviews",
}


def venue_for(item: schema.SourceItem) -> str:
    """The room a thread lives in: a subreddit, or the platform itself."""
    if item.source == "reddit" and item.container:
        return f"r/{str(item.container).removeprefix('r/')}"
    return _VENUE_NAMES.get(item.source, item.source)


def _by_votes(row: dict[str, Any]) -> tuple[bool, int]:
    # A source that reports no count (Hacker News) sorts after counted
    # comments and otherwise keeps the order the source gave.
    score = row["score"]
    return (score is None, -(score or 0))


def _is_launcher(item: schema.SourceItem, comment: dict[str, Any]) -> bool:
    if comment.get("by_post_author"):
        return True
    author = str(comment.get("author") or "").strip().lstrip("@").casefold()
    post_author = str(item.author or "").strip().lstrip("@").casefold()
    return bool(author) and author == post_author and author not in ("[deleted]", "[removed]")


def _thread_link(item: schema.SourceItem) -> str:
    # A Hacker News item's URL is the article; its comments live on the
    # discussion page.
    if item.source == "hackernews":
        hn_url = (item.metadata or {}).get("hn_url")
        if hn_url:
            return str(hn_url)
    return item.url or ""


def _thread_rows(item: schema.SourceItem) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for comment in (item.metadata or {}).get("top_comments") or []:
        if not isinstance(comment, dict):
            continue
        text = str(comment.get("excerpt") or comment.get("text") or comment.get("body") or "").strip()
        if not text:
            continue
        score = comment.get("score")
        rows.append({
            "text": text,
            "score": score if isinstance(score, int) and not isinstance(score, bool) else None,
            "author": str(comment.get("author") or ""),
            "url": str(comment.get("url") or "") or _thread_link(item),
            "launcher": _is_launcher(item, comment),
            "source": item.source,
            "thread_title": item.title,
        })
    rows.sort(key=_by_votes)
    kept = rows[:PER_ITEM_LIMIT]
    kept.extend([row for row in rows[PER_ITEM_LIMIT:] if row["launcher"]][:LAUNCHER_REPLY_LIMIT])
    return kept


def collect(items: Iterable[schema.SourceItem]) -> list[dict[str, Any]]:
    """Group the threads' comments by venue, top-voted first.

    Returns one entry per venue, in first-seen order:
    ``{"venue", "threads", "reactions"}``. Each reaction carries ``text``,
    ``score`` (``None`` when the source reports no count), ``author``, ``url``,
    ``launcher`` (written by the post's own author), ``source``, and
    ``thread_title``.
    """
    venues: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.source, item.item_id)
        if key in seen:
            continue
        seen.add(key)
        rows = _thread_rows(item)
        if not rows:
            continue
        venue = venues.setdefault(
            venue_for(item), {"venue": venue_for(item), "threads": 0, "reactions": []},
        )
        venue["threads"] += 1
        venue["reactions"].extend(rows)
    for venue in venues.values():
        rows = sorted(venue["reactions"], key=_by_votes)
        crowd = [row for row in rows if not row["launcher"]][:PER_VENUE_LIMIT]
        kept = {id(row) for row in crowd}
        venue["reactions"] = [row for row in rows if row["launcher"] or id(row) in kept]
    return list(venues.values())
