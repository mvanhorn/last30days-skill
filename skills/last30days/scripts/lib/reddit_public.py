"""Reddit public ``.json`` search helpers, and the keyless lane's entry point.

Reddit's anonymous ``.json`` endpoints return HTTP 403 from every context
since about 2026-05-28, so ``search`` below is no longer wired into any
production path; it stays as a standalone ``.json`` utility with its own test
coverage.

``search_reddit_public`` is the entry point ``pipeline.py`` calls for the free
Reddit path. It delegates to ``reddit_keyless.search_and_enrich``, which runs
the layered keyless paths (site search, the web floor hydrated through Arctic
Shift, subreddit listings) under per-path health (``reddit_health``): a path
refused with a 403 is skipped for 6 h across runs, a 429 for the rest of the
run, and the stream's detail names the path that served.

Endpoints (standalone ``search`` only):
- Global: https://www.reddit.com/search.json?q={query}&sort=relevance&t=month&limit={limit}
- Subreddit: https://www.reddit.com/r/{sub}/search.json?q={query}&restrict_sr=on&sort=relevance&t=month
"""

import gzip
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

from lib import http
from . import reddit_refs


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Depth-aware limits for thread counts
DEPTH_LIMITS = {
    "quick": 10,
    "default": 25,
    "deep": 50,
}

MAX_RETRIES = 3
BASE_BACKOFF = 2.0  # seconds


def _log(msg: str):
    """Log to stderr."""
    sys.stderr.write(f"[RedditPublic] {msg}\n")
    sys.stderr.flush()


def _url_encode(text: str) -> str:
    """URL-encode a query string."""
    return urllib.parse.quote_plus(text)


def _fetch_json(url: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
    """Fetch JSON from a URL with retry on 429 and error handling.

    Returns parsed JSON dict, or None on unrecoverable failure.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                if "json" not in content_type and "text/html" in content_type:
                    _log(f"Anti-bot HTML response (Content-Type: {content_type})")
                    return None

                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                body = raw.decode("utf-8")
                return json.loads(body)

        except urllib.error.HTTPError as e:
            if e.code == 429:
                # Reddit answers an anonymous 429 with x-ratelimit-reset and no
                # Retry-After; honour either. See http.retry_delay_from_headers.
                delay = http.retry_delay_from_headers(
                    getattr(e, "headers", None),
                    BASE_BACKOFF * (2 ** attempt),
                )
                _log(f"429 rate limited, retry {attempt + 1}/{MAX_RETRIES} after {delay:.1f}s")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                    continue
                # Last attempt exhausted
                _log("429 retries exhausted")
                return None
            elif e.code == 404:
                _log(f"404 not found: {url}")
                return None
            elif e.code == 403:
                _log(f"403 forbidden: {url}")
                return None
            else:
                _log(f"HTTP {e.code}: {e.reason}")
                return None

        except (urllib.error.URLError, OSError, TimeoutError) as e:
            _log(f"Network error: {e}")
            return None

        except json.JSONDecodeError as e:
            _log(f"JSON decode error: {e}")
            return None

    return None


def _parse_posts(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse Reddit listing JSON into normalized post dicts."""
    if not data:
        return []

    children = data.get("data", {}).get("children", [])
    posts = []

    for child in children:
        if child.get("kind") != "t3":
            continue
        post = child.get("data", {})
        permalink = str(post.get("permalink", "")).strip()
        if not permalink or "/comments/" not in permalink:
            continue

        score = int(post.get("score", 0) or 0)
        num_comments = int(post.get("num_comments", 0) or 0)
        selftext = str(post.get("selftext", ""))
        author = str(post.get("author", "[deleted]"))
        created_utc = post.get("created_utc")

        # Parse date
        date_str = None
        if created_utc:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
                date_str = dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                pass

        posts.append({
            "id": "",  # Will be assigned after dedup
            "title": str(post.get("title", "")).strip(),
            "url": f"https://www.reddit.com{permalink}",
            "score": score,
            "num_comments": num_comments,
            "subreddit": str(post.get("subreddit", "")).strip(),
            "created_utc": float(created_utc) if created_utc else None,
            "author": author if author not in ("[deleted]", "[removed]") else "[deleted]",
            "selftext": selftext[:500] if selftext else "",
            # Normalized fields matching ScrapeCreators output
            "date": date_str,
            "engagement": {
                "score": score,
                "num_comments": num_comments,
                "upvote_ratio": post.get("upvote_ratio"),
            },
            "relevance": _compute_relevance(score, num_comments),
            "why_relevant": "Reddit public search",
            "metadata": {},
        })

    return posts


def _compute_relevance(score: int, num_comments: int) -> float:
    """Estimate relevance from engagement signals."""
    score_component = min(1.0, max(0.0, score / 500.0))
    comments_component = min(1.0, max(0.0, num_comments / 200.0))
    return round((score_component * 0.6) + (comments_component * 0.4), 3)


def search(
    query: str,
    depth: str = "default",
    subreddit: Optional[str] = None,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """Search Reddit via the public JSON endpoint.

    Args:
        query: Search query string
        depth: 'quick', 'default', or 'deep' — controls result limit
        subreddit: Optional subreddit name (without r/) for scoped search
        timeout: HTTP timeout in seconds

    Returns:
        List of normalized post dicts. Empty list on any failure.
    """
    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])

    if subreddit:
        sub = reddit_refs.normalize_subreddit(subreddit, allow_all=True)
        if not sub:
            _log(f"Invalid subreddit rejected: {subreddit!r}")
            return []
        params = [
            ("q", str(query or "")),
            ("restrict_sr", "on"),
            ("sort", "relevance"),
            ("t", "month"),
            ("limit", str(limit)),
            ("raw_json", "1"),
        ]
        url = (
            f"https://www.reddit.com/r/{urllib.parse.quote(sub, safe='')}/search.json"
            f"?{urllib.parse.urlencode(params)}"
        )
    else:
        params = [
            ("q", str(query or "")),
            ("sort", "relevance"),
            ("t", "month"),
            ("limit", str(limit)),
            ("raw_json", "1"),
        ]
        url = f"https://www.reddit.com/search.json?{urllib.parse.urlencode(params)}"

    data = _fetch_json(url, timeout=timeout)
    posts = _parse_posts(data)

    # Dedupe by URL and assign IDs
    seen_urls = set()
    unique = []
    for post in posts:
        if post["url"] not in seen_urls:
            seen_urls.add(post["url"])
            unique.append(post)

    for i, post in enumerate(unique):
        post["id"] = f"R{i + 1}"

    return unique[:limit]


def search_reddit_public(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
    dedicated_subreddits: Optional[List[str]] = None,
    config: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """High-level free Reddit search + enrichment (keyless).

    Thin compatibility shim over the keyless pipeline: the legacy ``.json``
    search/enrichment endpoints now return HTTP 403, so this delegates to
    ``reddit_keyless.search_and_enrich`` (site search, the keyless web floor
    with Arctic Shift hydration, and dedicated/targeted listings, then comment
    enrichment; no ``.json`` search). Each path's state for the call lands in
    ``reddit_keyless.lane_paths()`` when the caller opened one. The name and
    signature are preserved so ``pipeline.py`` and other callers need no
    change and the ScrapeCreators backup still engages when this returns
    empty.

    The module-level ``search`` / ``_parse_posts`` helpers remain as a
    standalone ``.json`` search utility (own test coverage), no longer wired
    into the keyless production path.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        subreddits: Optional list of subreddit names (without r/) for targeted search
        dedicated_subreddits: Optional list of entity-home subreddits (without r/)
        config: Optional pre-loaded per-run config mapping.

    Returns:
        List of normalized item dicts matching ScrapeCreators output format.
        Empty list on total failure (so SC backup can engage).
    """
    from . import reddit_keyless
    kwargs: Dict[str, Any] = {
        "depth": depth,
        "subreddits": subreddits,
        "dedicated_subreddits": dedicated_subreddits,
    }
    if config is not None:
        kwargs["config"] = config
    return reddit_keyless.search_and_enrich(
        topic, from_date, to_date, **kwargs,
    )
