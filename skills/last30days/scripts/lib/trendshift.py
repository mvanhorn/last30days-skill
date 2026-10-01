"""Keyless Trendshift repository-momentum source.

Trendshift publishes its ranked repository list at ``trendshift.io``.  This
adapter deliberately consumes that public HTML page instead of an undocumented
private API: no account, cookie, or key is required.  It is opt-in because the
site is a third-party signal, not a default research source.
"""

from __future__ import annotations

import html
import re
from datetime import date
from typing import Any

from . import http, log
from .relevance import token_overlap_relevance

BASE_URL = "https://trendshift.io"
_REPO_LINK = re.compile(r'href=["\'](/repositories/(\d+))["\'][^>]*>\s*([^<]+?)\s*</a>', re.I)
_REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_DEPTH_LIMITS = {"quick": 10, "default": 25, "deep": 50}


def _current_snapshot_date() -> str:
    """Return the UTC-independent calendar date for the live listing."""
    return date.today().isoformat()


def _log(message: str) -> None:
    log.source_log("Trendshift", message, tty_only=False)


def parse_listing(page: str, *, topic: str = "", as_of: str | None = None, depth: str = "default") -> list[dict[str, Any]]:
    """Extract ranked repository links from a public Trendshift listing page.

    The site is a Next application, so a repository can occur more than once
    in navigation/live-mention markup.  Preserve its first occurrence, which
    is the order exposed by the ranked listing, and expose that order as rank.
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _REPO_LINK.finditer(page or ""):
        path, repo_id, raw_name = match.groups()
        name = html.unescape(raw_name).strip()
        if not _REPO_NAME.fullmatch(name) or name.lower() in seen:
            continue
        seen.add(name.lower())
        rank = len(found) + 1
        relevance = token_overlap_relevance(topic, name) if topic else 0.8
        found.append({
            "id": f"trendshift:{repo_id}",
            "title": name,
            "url": f"{BASE_URL}{path}",
            "date": as_of,
            "engagement": {"rank": rank},
            "relevance": max(0.35, relevance),
            "why_relevant": f"Trendshift daily rank #{rank}",
            "snippet": f"Trendshift currently ranks {name} #{rank}.",
            "metadata": {
                "repository": name,
                "trendshift_id": repo_id,
                "rank": rank,
                # This is an ordering signal from the listing, not a count of
                # interactions. Discovery consumes it separately from native
                # engagement so a lower rank is always stronger.
                "discovery_signal": 1.0 / rank,
            },
        })
        if len(found) >= _DEPTH_LIMITS.get(depth, _DEPTH_LIMITS["default"]):
            break
    return found


def fetch_trendshift(
    topic: str,
    from_date: str,
    to_date: str,
    *,
    depth: str = "default",
    require_snapshot_date: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch the current listing and retain a fetch or snapshot error.

    ``to_date`` normally describes the research window, not a Trendshift
    snapshot request. Only an explicit historical ``--as-of`` run requires
    the current listing date to match it.
    """
    snapshot_date = _current_snapshot_date()
    if require_snapshot_date and to_date != snapshot_date:
        message = (
            "Trendshift exposes only its current listing; requested snapshot "
            f"for {to_date} is unavailable."
        )
        _log(message)
        return [], message
    page = http.get_text(BASE_URL + "/", timeout=20, retries=2, accept="text/html")
    if not page:
        message = "Trendshift listing fetch failed"
        _log(message)
        return [], message
    items = parse_listing(page, topic=topic, as_of=snapshot_date, depth=depth)
    if topic.strip():
        # Keep only meaningful topic matches.  A direct owner/repo request is
        # exact and should survive even when its token-overlap score is low.
        needle = topic.strip().lower()
        items = [item for item in items if item["relevance"] >= 0.45 or needle in item["title"].lower()]
    _log(f"listing returned {len(items)} matching repositories")
    return items, None


def search_trendshift(topic: str, from_date: str, to_date: str, *, depth: str = "default") -> list[dict[str, Any]]:
    """Return a current listing while preserving the list-only public API."""
    items, _ = fetch_trendshift(topic, from_date, to_date, depth=depth)
    return items
