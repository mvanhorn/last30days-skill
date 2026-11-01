"""Scavio transport for the TikTok source.

An optional alternative to ScrapeCreators for users who set SCAVIO_API_KEY
(scavio.dev). Every function returns the same raw TikTok shapes the
ScrapeCreators calls in ``tiktok.py`` return (aweme items with ``aweme_id``,
``desc``, ``statistics``, ``author``; comment dicts with ``text``,
``digg_count``, ``user``, ``create_time``), so ``tiktok._parse_items`` and the
comment shaping run unchanged. Only the transport differs.

Errors raise; the callers in ``tiktok.py`` already catch and log them the same
way they do for ScrapeCreators. ``http.DeadlineExceeded`` propagates.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List

from . import http

_BASE = "https://api.scavio.dev/api/v1/tiktok"


def _headers(token: str) -> Dict[str, str]:
    # X-Client-Source lets Scavio attribute calls to last30days in its own logs.
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client-Source": "last30days",
    }


def _post(
    path: str,
    body: Dict[str, Any],
    token: str,
    *,
    timeout: int = 30,
    retries: int = 2,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> Dict[str, Any]:
    """POST one Scavio TikTok call and return its ``data`` payload."""
    response = http.post(
        f"{_BASE}{path}", body, headers=_headers(token),
        timeout=timeout, retries=retries,
        deadline_monotonic=deadline, cancel=cancel,
    )
    data = response.get("data") if isinstance(response, dict) else None
    return data if isinstance(data, dict) else {}


def keyword_search(
    query: str,
    token: str,
    count: int,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Keyword video search, relevance order. Returns raw aweme items."""
    data = _post(
        "/search/videos",
        {"keyword": query, "count": max(1, min(count, 30)), "sort_type": "0"},
        token, deadline=deadline, cancel=cancel,
    )
    entries = data.get("search_item_list") or data.get("aweme_list") or []
    return [e.get("aweme_info", e) for e in entries if isinstance(e, dict)]


def hashtag_items(
    hashtag: str,
    token: str,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Videos for a hashtag name. Two calls: name -> hashtag id -> videos."""
    info = _post("/hashtag", {"hashtag_name": hashtag}, token, deadline=deadline, cancel=cancel)
    challenge = (info.get("challengeInfo") or {}).get("challenge") or {}
    hashtag_id = str(challenge.get("id") or "")
    if not hashtag_id:
        return []
    data = _post(
        "/hashtag/videos", {"hashtag_id": hashtag_id, "count": 20},
        token, deadline=deadline, cancel=cancel,
    )
    return [i for i in (data.get("aweme_list") or []) if isinstance(i, dict)]


def profile_items(
    handle: str,
    token: str,
    count: int,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """A creator's latest videos. Two calls: username -> sec_uid -> posts."""
    profile = _post("/profile", {"username": handle}, token, deadline=deadline, cancel=cancel)
    sec_uid = str((profile.get("user") or {}).get("sec_uid") or "")
    if not sec_uid:
        return []
    data = _post(
        "/user/posts",
        {"sec_user_id": sec_uid, "count": max(1, min(count, 30)), "sort_type": "0"},
        token, deadline=deadline, cancel=cancel,
    )
    return [i for i in (data.get("aweme_list") or []) if isinstance(i, dict)][:count]


def transcript(
    video_id: str,
    token: str,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> str:
    """Spoken-word transcript as plain text ('' when the video has none)."""
    data = _post(
        "/video/transcript", {"video_id": video_id}, token,
        timeout=15, retries=1, deadline=deadline, cancel=cancel,
    )
    if not data.get("available"):
        return ""
    text = data.get("text")
    return text if isinstance(text, str) else ""


def comments(
    video_id: str,
    token: str,
    count: int = 20,
    *,
    deadline: float | None = None,
    cancel: threading.Event | None = None,
) -> List[Dict[str, Any]]:
    """Top-level comments on a video, as raw TikTok comment dicts."""
    data = _post(
        "/video/comments",
        {"video_id": video_id, "count": max(1, min(count, 50))},
        token, deadline=deadline, cancel=cancel,
    )
    return [c for c in (data.get("comments") or []) if isinstance(c, dict)]
