"""Canonical Reddit thread identity, shared by every Reddit discovery path.

One thread reaches the engine in many shapes: ``old.`` and ``new.`` hosts,
slugless links, comment permalinks, ``redd.it/<id>`` short links, bare ids.
:func:`canonical_thread` turns each of them into one :class:`ThreadRef` so the
lanes, the hydrator and fusion agree on what "the same thread" is. Share links
(``/r/<sub>/s/<code>``), search-engine redirect wrappers and other hosts are
rejected: their target cannot be known without following them.

This module also names where a thread's score came from, so a live score can
beat an archive snapshot when copies merge, and holds the comment union used
by that merge. It imports nothing from the rest of the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

# Where a thread's score came from, best first. A score Reddit itself served
# (site search, a listing, a logged-in session, ScrapeCreators) is live. Arctic
# Shift snapshots a post when it ingests it and rescans it about once, so a post
# under PENDING_SECONDS old still reads 0 or 1 there: that score is withheld
# (``None``) and labeled pending. An older archive score is frozen but real.
# A thread nothing hydrated has an unknown score.
LIVE = "live"
ARCTIC = "arctic"
ARCTIC_PENDING = "arctic-pending"
UNKNOWN = "unknown"
_SCORE_RANK = {LIVE: 3, ARCTIC: 2, ARCTIC_PENDING: 1, UNKNOWN: 0}
PENDING_SECONDS = 36 * 3600

_HOSTS = frozenset({
    "reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
    "np.reddit.com", "m.reddit.com", "sh.reddit.com",
})
_SHORT_HOSTS = frozenset({"redd.it", "www.redd.it"})
_ID = re.compile(r"^[0-9a-z]{1,13}$")
_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")


@dataclass
class MatchedComment:
    """The comment a comment search matched inside a thread."""

    comment_id: str
    created_utc: int | None = None
    permalink: str = ""


@dataclass
class ThreadRef:
    """One discovered thread, before hydration.

    ``provenance`` names the discovery paths that found it (``web``, ``host``,
    ``session``). ``title`` and ``date`` are what the search result showed;
    they stand in only when no hydrator could fill the thread.
    """

    post_id: str
    url: str
    subreddit: str = ""
    provenance: set[str] = field(default_factory=set)
    matched_comment: MatchedComment | None = None
    title: str = ""
    date: str | None = None


def _canonical_url(post_id: str, subreddit: str) -> str:
    if subreddit:
        return f"https://www.reddit.com/r/{subreddit}/comments/{post_id}/"
    return f"https://www.reddit.com/comments/{post_id}/"


def _bare_id(text: str) -> str | None:
    candidate = text.strip().lower().removeprefix("t3_")
    return candidate if _ID.match(candidate) else None


def canonical_thread(url_or_id: str) -> ThreadRef | None:
    """The canonical ref for a Reddit thread URL or base36 id, else ``None``."""
    text = str(url_or_id or "").strip()
    if not text:
        return None
    if "/" not in text and "." not in text:
        post_id = _bare_id(text)
        return ThreadRef(post_id=post_id, url=_canonical_url(post_id, "")) if post_id else None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in _SHORT_HOSTS:
        post_id = _bare_id(segments[0]) if len(segments) == 1 else None
        return ThreadRef(post_id=post_id, url=_canonical_url(post_id, "")) if post_id else None
    if host not in _HOSTS:
        return None
    subreddit = ""
    if len(segments) >= 2 and segments[0].lower() == "r":
        subreddit = segments[1]
        if not _SUBREDDIT.match(subreddit):
            return None
        segments = segments[2:]
    if len(segments) < 2 or segments[0].lower() != "comments":
        return None
    post_id = _bare_id(segments[1])
    if not post_id:
        return None
    return ThreadRef(post_id=post_id, url=_canonical_url(post_id, subreddit), subreddit=subreddit)


def thread_key(url: str) -> str | None:
    """A merge key every URL form of one thread shares, or ``None``."""
    ref = canonical_thread(url)
    return f"https://reddit.com/comments/{ref.post_id}" if ref else None


def score_rank(engagement_source: str | None) -> int:
    """How much a score from ``engagement_source`` is trusted (higher wins).

    A copy with no label came from a lane that served Reddit's own score.
    """
    return _SCORE_RANK.get(engagement_source or LIVE, _SCORE_RANK[LIVE])


def score_pending(metadata: dict[str, Any] | None) -> bool:
    """Whether an item's score is a withheld fresh archive snapshot."""
    return bool(metadata) and metadata.get("engagement_source") == ARCTIC_PENDING


def comment_key(comment: dict[str, Any]) -> str:
    """A comment's identity: its id, the last segment of its permalink, or its text."""
    cid = str(comment.get("id") or "").strip().removeprefix("t1_")
    if cid:
        return cid
    link = str(comment.get("permalink") or comment.get("url") or "")
    segments = [s for s in urlsplit(link).path.split("/") if s]
    if len(segments) >= 2 and "comments" in segments:
        after = segments[segments.index("comments") + 1:]
        if len(after) >= 3:
            return after[2].lower()
    text = str(comment.get("excerpt") or comment.get("body") or comment.get("text") or "")
    return " ".join(text.split())[:80].lower()


def _comment_order(comment: dict[str, Any]) -> tuple:
    score = comment.get("score")
    scored = isinstance(score, (int, float)) and not isinstance(score, bool) and not comment.get("score_pending")
    return (0, -score) if scored else (1, 0)


def union_comments(
    mine: Iterable[dict[str, Any]], theirs: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Both comment lists, one entry per comment id, scored comments first by score.

    Where both lists hold a comment, the copy in ``mine`` stays unless it is
    pending and the other copy carries a score. Pending comments keep their
    order after every scored one.
    """
    merged: dict[str, dict[str, Any]] = {}
    for comment in [*mine, *theirs]:
        if not isinstance(comment, dict):
            continue
        key = comment_key(comment)
        current = merged.get(key)
        if current is None or (_comment_order(current)[0] == 1 and _comment_order(comment)[0] == 0):
            merged[key] = comment
    return sorted(merged.values(), key=_comment_order)
