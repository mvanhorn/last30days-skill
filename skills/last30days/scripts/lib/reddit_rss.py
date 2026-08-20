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

import math
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlsplit, urlunsplit

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

# Reddit's RSS hosts are public, but can independently return anti-bot 403/429
# responses.  Keep the fallback deliberately small and bounded: one canonical
# request, then one serialized old.reddit.com request for subreddit feeds.
WWW_REDDIT_HOST = "www.reddit.com"
OLD_REDDIT_HOST = "old.reddit.com"
MAX_FEED_ATTEMPTS = 2

# Cache a successful feed briefly, then allow the last good response to bridge
# a bounded outage.  CACHE_STALE_TTL is the additional stale window after the
# fresh window, not an unbounded persistence policy.
CACHE_FRESH_TTL = 60.0
CACHE_STALE_TTL = 300.0
MAX_CACHE_ENTRIES = 128

# Cooldowns are checked before every host request.  No request sleeps: a
# Retry-After value becomes host state, which prevents parallel fan-out from
# multiplying a block or rate limit.
HOST_COOLDOWN_BASE = 5.0
HOST_COOLDOWN_MAX = 300.0
MAX_RETRY_AFTER = HOST_COOLDOWN_MAX
MAX_BACKOFF_FAILURES = 6


@dataclass(frozen=True)
class _CacheEntry:
    text: str
    stored_at: float


@dataclass
class _HostState:
    failures: int = 0
    cooldown_until: float = 0.0


@dataclass(frozen=True)
class _HTTPResult:
    text: Optional[str]
    status_code: Optional[int] = None
    retry_after: Optional[float] = None


_STATE_LOCK = threading.RLock()
_FEED_CACHE: "OrderedDict[str, _CacheEntry]" = OrderedDict()
_HOST_STATE: Dict[str, _HostState] = {}
_HOST_LOCKS = {
    WWW_REDDIT_HOST: threading.Lock(),
    OLD_REDDIT_HOST: threading.Lock(),
}


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditRSS] {msg}\n")
    sys.stderr.flush()


def _now() -> float:
    """Return the clock used by cache and cooldown state (easy to fake in tests)."""
    return time.monotonic()


def _reset_state() -> None:
    """Reset process-local cache/cooldown state for tests and one-shot callers."""
    with _STATE_LOCK:
        _FEED_CACHE.clear()
        _HOST_STATE.clear()


def _replace_url_host(url: str, host: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))


def _canonicalize_reddit_url(url: str) -> str:
    """Use www.reddit.com for Reddit post links regardless of feed host."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.hostname and parts.hostname.lower() == OLD_REDDIT_HOST:
        return _replace_url_host(url, WWW_REDDIT_HOST)
    return url


def _canonical_feed_url(url: str) -> str:
    """Treat an old-host input as the canonical www feed plus optional fallback."""
    return _replace_url_host(url, WWW_REDDIT_HOST) if _feed_host(url) == OLD_REDDIT_HOST else url


def _feed_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_subreddit_feed(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return (
        (parts.hostname or "").lower() == WWW_REDDIT_HOST
        and parts.path.startswith("/r/")
        and parts.path.lower().endswith(".rss")
    )


def _feed_candidates(url: str) -> List[str]:
    """Return canonical-first candidates, never placing alternatives in parallel."""
    primary = _canonical_feed_url(url)
    candidates = [primary]
    if _is_subreddit_feed(primary):
        candidates.append(_replace_url_host(primary, OLD_REDDIT_HOST))
    return candidates[:MAX_FEED_ATTEMPTS]


def _cache_lookup(url: str, now: float) -> tuple[Optional[str], Optional[str]]:
    """Return cached text and its freshness class (fresh/stale/None)."""
    with _STATE_LOCK:
        entry = _FEED_CACHE.get(url)
        if entry is None:
            return None, None
        age = max(0.0, now - entry.stored_at)
        if age <= CACHE_FRESH_TTL:
            return entry.text, "fresh"
        if age <= CACHE_FRESH_TTL + CACHE_STALE_TTL:
            return entry.text, "stale"
        _FEED_CACHE.pop(url, None)
        return None, None


def _cache_store(url: str, text: str, now: float) -> None:
    """Store only a successful feed, evicting the oldest entry at the hard cap."""
    if not text or MAX_CACHE_ENTRIES <= 0:
        return
    with _STATE_LOCK:
        _FEED_CACHE.pop(url, None)
        _FEED_CACHE[url] = _CacheEntry(text=text, stored_at=now)
        while len(_FEED_CACHE) > MAX_CACHE_ENTRIES:
            _FEED_CACHE.popitem(last=False)


def _numeric_retry_after(headers: Any) -> Optional[float]:
    """Read only numeric Retry-After values and clamp them to a finite bound."""
    if headers is None:
        return None
    raw = None
    for key in ("Retry-After", "retry-after"):
        try:
            raw = headers.get(key)
        except AttributeError:
            raw = None
        if raw is not None:
            break
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return min(MAX_RETRY_AFTER, max(0.0, value))


def _record_host_failure(host: str, retry_after: Optional[float] = None) -> None:
    """Record bounded backoff for one of the two Reddit feed hosts."""
    if host not in _HOST_LOCKS:
        return
    with _STATE_LOCK:
        state = _HOST_STATE.setdefault(host, _HostState())
        state.failures = min(state.failures + 1, MAX_BACKOFF_FAILURES)
        backoff = min(
            HOST_COOLDOWN_MAX,
            HOST_COOLDOWN_BASE * (2 ** (state.failures - 1)),
        )
        if retry_after is not None:
            backoff = max(backoff, min(MAX_RETRY_AFTER, retry_after))
        state.cooldown_until = max(state.cooldown_until, _now() + min(backoff, HOST_COOLDOWN_MAX))


def _host_in_cooldown(host: str, now: float) -> bool:
    with _STATE_LOCK:
        state = _HOST_STATE.get(host)
        return state is not None and now < state.cooldown_until


def _clear_host_failure(host: str) -> None:
    with _STATE_LOCK:
        _HOST_STATE.pop(host, None)


def _response_status(response: Any) -> int:
    value = getattr(response, "status", None)
    if value is None:
        value = getattr(response, "code", None)
    if value is None and hasattr(response, "getcode"):
        value = response.getcode()
    try:
        return int(value)
    except (TypeError, ValueError):
        return 200


def _request_feed_unlocked(url: str, host: str) -> _HTTPResult:
    """Make one bounded, status-aware request; caller holds the host lock."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": http.BROWSER_USER_AGENT,
            "Accept": "application/atom+xml",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT) as response:
            status = _response_status(response)
            retry_after = _numeric_retry_after(getattr(response, "headers", None))
            if status >= 400:
                _record_host_failure(host, retry_after)
                return _HTTPResult(None, status, retry_after)
            body = response.read()
            text = (
                body.decode("utf-8", errors="replace")
                if isinstance(body, bytes)
                else str(body)
            )
            return _HTTPResult(text, status, retry_after)
    except urllib.error.HTTPError as exc:
        retry_after = _numeric_retry_after(getattr(exc, "headers", None))
        _record_host_failure(host, retry_after)
        _log(f"HTTP {exc.code} from {host} for {url}")
        return _HTTPResult(None, exc.code, retry_after)
    except Exception as exc:  # one unavailable feed must never sink the run
        _record_host_failure(host)
        _log(f"request failed for {url}: {exc}")
        return _HTTPResult(None)


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


def _parse_feed_document(
    xml_text: str, query: str = ""
) -> tuple[List[Dict[str, Any]], bool]:
    """Parse an Atom feed and report whether its document shape is usable."""
    if not xml_text:
        return [], False
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        _log(f"feed parse error: {e}")
        return [], False
    if root.tag != f"{ATOM}feed":
        _log("feed parse error: unexpected Atom root")
        return [], False

    posts: List[Dict[str, Any]] = []
    for entry in root.iter(f"{ATOM}entry"):
        link_el = entry.find(f"{ATOM}link")
        url = link_el.get("href", "").strip() if link_el is not None else ""
        url = _canonicalize_reddit_url(url)
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

    return posts, True


def _parse_feed(xml_text: str, query: str = "") -> List[Dict[str, Any]]:
    """Parse an Atom feed string into normalized post dicts. Never raises."""
    return _parse_feed_document(xml_text, query)[0]


def _build_urls(query: str, depth: str, subreddits: Optional[List[str]]) -> List[str]:
    """Build the keyless RSS feed URLs to fan out across."""
    q = quote_plus(query)
    urls: List[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            urls.append(url)

    add(f"https://www.reddit.com/search.rss?q={q}&sort=relevance&t=month")
    for raw_sub in (subreddits or []):
        sub = raw_sub.removeprefix("r/").strip()
        if not sub:
            continue
        add(
            f"https://www.reddit.com/r/{sub}/search.rss"
            f"?q={q}&restrict_sr=on&sort=relevance&t=month"
        )
        for sort in LISTING_SORTS.get(depth, LISTING_SORTS["default"]):
            add(f"https://www.reddit.com/r/{sub}/{sort}.rss?t=month")
    return urls


def _fetch_candidate(candidate: str, primary: str, query: str) -> List[Dict[str, Any]]:
    """Fetch one candidate while serializing requests and cache publication per host."""
    host = _feed_host(candidate)
    lock = _HOST_LOCKS.get(host)
    if lock is None:
        return []

    with lock:
        # A concurrent caller may have populated the canonical cache while this
        # caller waited for either host lock. Re-check before issuing a duplicate.
        cached_text, cache_kind = _cache_lookup(primary, _now())
        if cached_text and cache_kind == "fresh":
            cached_posts, _ = _parse_feed_document(cached_text, query)
            if cached_posts:
                return cached_posts

        if _host_in_cooldown(host, _now()):
            _log(f"{host} cooldown active; skipping {candidate}")
            return []

        result = _request_feed_unlocked(candidate, host)
        if not result.text:
            return []

        posts, valid_document = _parse_feed_document(result.text, query)
        if posts:
            # Cache by the canonical www URL even when old.reddit.com supplied
            # the body, so equivalent feed calls share one success entry.
            _cache_store(primary, result.text, _now())
            _clear_host_failure(host)
            return posts

        # A malformed document is a host failure; a valid empty feed is merely
        # an unusable result for this query and must not suppress other feeds.
        if not valid_document:
            _record_host_failure(host)
        return []


def _fetch_feed(url: str, query: str) -> List[Dict[str, Any]]:
    """Fetch a canonical feed, then its serialized subreddit fallback, never raising."""
    try:
        primary = _canonical_feed_url(url)
        cached_text, cache_kind = _cache_lookup(primary, _now())
        if cached_text and cache_kind == "fresh":
            cached_posts, _ = _parse_feed_document(cached_text, query)
            if cached_posts:
                return cached_posts

        # Candidates are deliberately handled in order. The old host is never
        # submitted to the executor as a second future for the same feed.
        for candidate in _feed_candidates(primary):
            posts = _fetch_candidate(candidate, primary, query)
            if posts:
                return posts

        # Refresh/fallback failed: a still-valid last success is safe to serve.
        stale_text, stale_kind = _cache_lookup(primary, _now())
        if stale_text and stale_kind in ("fresh", "stale"):
            stale_posts, _ = _parse_feed_document(stale_text, query)
            if stale_posts:
                _log(f"using {stale_kind} cache for {primary}")
                return stale_posts
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
        url = _canonicalize_reddit_url(post.get("url", ""))
        if not url:
            continue
        post["url"] = url
        if url not in seen:
            seen.add(url)
            unique.append(post)

    for i, post in enumerate(unique):
        post["id"] = f"R{i + 1}"

    return unique[:limit]
