"""Keyless Reddit discovery via public RSS/Atom feeds.

Reddit's ``.json`` search endpoints now return HTTP 403 (shreddit anti-bot).
RSS feeds still serve HTTP 200 with no API key, so this module uses them for
post discovery, replacing ``reddit_public.search`` as the free search path.

Two feed families are combined and deduped:
- search:  /search.rss?q=... and /r/{sub}/search.rss?q=...&restrict_sr=on
- listing: /r/{sub}/{top,hot}.rss?t=month

RSS entries carry no engagement score, so ``score``/``num_comments`` start at 0
and are backfilled during shreddit enrichment (see reddit_shreddit.py). Output
dicts match the normalized shape emitted by ``reddit_public._parse_posts`` so
downstream code (pipeline, renderer) is unaffected.
"""

import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from . import http
from .relevance import token_overlap_relevance

ATOM = "{http://www.w3.org/2005/Atom}"

# Mirror reddit_public depth-aware limits so the two free paths behave alike.
DEPTH_LIMITS = {
    "quick": 10,
    "default": 25,
    "deep": 50,
}

# Listing sorts pulled per subreddit (in addition to search), for volume.
LISTING_SORTS = {
    "quick": ["top"],
    "default": ["top", "hot"],
    "deep": ["top", "hot", "new"],
}

MAX_WORKERS = 4
FEED_TIMEOUT = 15


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditRSS] {msg}\n")
    sys.stderr.flush()


def _iso_to_date(value: Optional[str]) -> Optional[str]:
    """Parse an ISO-8601 timestamp (e.g. 2026-05-20T18:48:31+00:00) to YYYY-MM-DD."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return None


def _iso_to_epoch(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


def _subreddit_from(category: str, url: str) -> str:
    """Derive subreddit name from the entry category or, failing that, the URL."""
    if category:
        return category
    # URL form: https://www.reddit.com/r/{sub}/comments/{id}/...
    parts = url.split("/r/", 1)
    if len(parts) == 2:
        return parts[1].split("/", 1)[0]
    return ""


def _parse_feed(xml_text: str, query: str = "") -> List[Dict[str, Any]]:
    """Parse an Atom feed string into normalized post dicts. Never raises."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        _log(f"feed parse error: {e}")
        return []

    posts: List[Dict[str, Any]] = []
    for entry in root.iter(f"{ATOM}entry"):
        link_el = entry.find(f"{ATOM}link")
        url = link_el.get("href", "").strip() if link_el is not None else ""
        if not url or "/comments/" not in url:
            continue

        title_el = entry.find(f"{ATOM}title")
        title = (title_el.text or "").strip() if title_el is not None else ""

        author = ""
        author_el = entry.find(f"{ATOM}author/{ATOM}name")
        if author_el is not None and author_el.text:
            author = author_el.text.strip().removeprefix("/u/").removeprefix("u/")
        if author in ("[deleted]", "[removed]", ""):
            author = "[deleted]"

        cat_el = entry.find(f"{ATOM}category")
        category = cat_el.get("term", "").strip() if cat_el is not None else ""
        subreddit = _subreddit_from(category, url)

        updated_el = entry.find(f"{ATOM}updated")
        updated = (updated_el.text or "").strip() if updated_el is not None else ""

        content_el = entry.find(f"{ATOM}content")
        selftext = ""
        if content_el is not None and content_el.text:
            # Strip the simplest HTML; renderer only needs an excerpt.
            import re as _re
            selftext = _re.sub(r"<[^>]+>", " ", content_el.text)
            selftext = _re.sub(r"\s+", " ", selftext).strip()[:500]

        relevance = round(token_overlap_relevance(query, title), 3) if query else 0.0

        posts.append({
            "id": "",  # assigned after dedup
            "title": title,
            "url": url,
            "score": 0,            # backfilled by shreddit enrichment
            "num_comments": 0,     # backfilled by shreddit enrichment
            "subreddit": subreddit,
            "created_utc": _iso_to_epoch(updated),
            "author": author,
            "selftext": selftext,
            "date": _iso_to_date(updated),
            "engagement": {
                "score": 0,
                "num_comments": 0,
                "upvote_ratio": None,
            },
            "relevance": relevance,
            "why_relevant": "Reddit RSS",
            "metadata": {},
        })

    return posts


def _build_urls(query: str, depth: str, subreddits: Optional[List[str]]) -> List[str]:
    """Build the keyless RSS feed URLs to fan out across."""
    q = quote_plus(query)
    urls: List[str] = [
        f"https://www.reddit.com/search.rss?q={q}&sort=relevance&t=month"
    ]
    for raw_sub in (subreddits or []):
        sub = raw_sub.removeprefix("r/").strip()
        if not sub:
            continue
        urls.append(
            f"https://www.reddit.com/r/{sub}/search.rss"
            f"?q={q}&restrict_sr=on&sort=relevance&t=month"
        )
        for sort in LISTING_SORTS.get(depth, LISTING_SORTS["default"]):
            urls.append(f"https://www.reddit.com/r/{sub}/{sort}.rss?t=month")
    return urls


def _fetch_feed(url: str, query: str) -> List[Dict[str, Any]]:
    """Fetch and parse one feed. Never raises."""
    try:
        text = http.reddit_keyless_get_text(url, timeout=FEED_TIMEOUT, accept="application/atom+xml")
        return _parse_feed(text, query) if text else []
    except Exception as e:  # defensive: a single bad feed must not sink the run
        _log(f"feed fetch failed for {url}: {e}")
        return []


def search_rss(
    query: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Discover Reddit posts for a query via keyless RSS feeds.

    Args:
        query: Search query string
        depth: 'quick', 'default', or 'deep' — controls result limit and feeds
        subreddits: Optional pre-resolved subreddit names (without r/) to target

    Returns:
        List of normalized post dicts (deduped by URL, capped by depth),
        with placeholder scores to be backfilled during enrichment.
        Empty list on any failure.
    """
    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
    urls = _build_urls(query, depth, subreddits)

    all_posts: List[Dict[str, Any]] = []
    workers = min(MAX_WORKERS, len(urls)) or 1
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_feed, url, query): url for url in urls}
        for future in futures:
            try:
                all_posts.extend(future.result(timeout=FEED_TIMEOUT + 5))
            except (Exception, FuturesTimeoutError) as e:
                _log(f"feed future failed: {e}")

    # Dedupe by URL (first occurrence wins).
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for post in all_posts:
        if post["url"] not in seen:
            seen.add(post["url"])
            unique.append(post)

    for i, post in enumerate(unique):
        post["id"] = f"R{i + 1}"

    return unique[:limit]
