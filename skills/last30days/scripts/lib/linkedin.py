"""LinkedIn post search via ScrapeCreators API.

Searches public LinkedIn posts by keyword using the ScrapeCreators
/v1/linkedin/search/posts endpoint, which uses Google-indexed LinkedIn
content to bypass auth requirements.

Requires SCRAPECREATORS_API_KEY environment variable.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from . import http, log

SC_BASE = "https://api.scrapecreators.com/v1/linkedin"

DEPTH_CONFIG: dict[str, dict[str, Any]] = {
    "quick": {"date_posted": "last-week", "max_results": 10},
    "default": {"date_posted": "last-month", "max_results": 20},
    "deep": {"date_posted": "last-month", "max_results": 30},
}


def _log(msg: str) -> None:
    log.source_log("LinkedIn", msg, tty_only=False)


def search_linkedin(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    token: str = "",
) -> Dict[str, Any]:
    """Search LinkedIn posts via ScrapeCreators API.

    Args:
        topic: Search query / topic string.
        from_date: Window start date (YYYY-MM-DD) — used for depth mapping.
        to_date: Window end date (YYYY-MM-DD).
        depth: Retrieval profile — 'quick', 'default', or 'deep'.
        token: ScrapeCreators API key.

    Returns:
        Dict with a 'posts' list of raw post dicts.
    """
    if not token:
        _log("No SCRAPECREATORS_API_KEY — skipping")
        return {"posts": []}

    cfg = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    date_posted = cfg["date_posted"]

    _log(f"Searching for '{topic}' (date_posted={date_posted})")

    try:
        response = http.get(
            f"{SC_BASE}/search/posts",
            params={"query": topic, "date_posted": date_posted},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except http.HTTPError as exc:
        _log(f"Search failed (HTTP {exc.status_code}): {exc}")
        return {"posts": [], "error": str(exc)}
    except Exception as exc:
        _log(f"Search failed: {type(exc).__name__}: {exc}")
        return {"posts": [], "error": str(exc)}

    posts = _extract_posts(response)
    max_results = cfg["max_results"]
    posts = posts[:max_results]
    _log(f"Found {len(posts)} posts")
    return {"posts": posts}


def _extract_posts(response: Any) -> List[Dict[str, Any]]:
    """Extract the posts list from various possible response shapes."""
    if not isinstance(response, dict):
        return []
    for key in ("posts", "items", "data", "results"):
        val = response.get(key)
        if isinstance(val, list):
            return val
    return []


def _parse_date(raw: Any) -> str | None:
    """Extract a YYYY-MM-DD string from various date formats."""
    if not raw:
        return None
    s = str(raw).strip()
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    return None


def _int_field(post: dict[str, Any], *keys: str) -> int:
    """Return the first present integer field from a post dict."""
    for key in keys:
        val = post.get(key)
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return 0


def _is_article(url: str) -> bool:
    """A LinkedIn long-form article (Pulse) lives under a /pulse/ URL.

    Articles are higher-signal than ordinary posts — someone who wrote a
    full article on a topic is a stronger source than someone who dashed off
    a status update.
    """
    return "/pulse/" in (url or "").lower()


# Relevance hints: articles outrank ordinary posts at rerank time.
_ARTICLE_RELEVANCE = 0.9
_POST_RELEVANCE = 0.5


def parse_linkedin_response(
    result: Dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[Dict[str, Any]]:
    """Parse ScrapeCreators LinkedIn response into engine-compatible item dicts.

    Each returned dict must be normalizable by normalize._normalize_linkedin.

    If from_date/to_date are given, applies the same hard date-range filter
    used by instagram.search_and_enrich: drop items outside the window, but
    fall back to keeping everything if the filter would otherwise empty the
    result (SC doesn't always return a usable date per post).
    """
    posts = result.get("posts") or []
    items: List[Dict[str, Any]] = []

    for i, post in enumerate(posts):
        if not isinstance(post, dict):
            continue

        # The live ScrapeCreators post object carries the body in `description`
        # and the timestamp in `datePublished`. The other keys are tolerated
        # fallbacks for shape drift / alternate endpoints.
        text = str(
            post.get("description")
            or post.get("text")
            or post.get("content")
            or post.get("body")
            or ""
        ).strip()
        if not text:
            continue

        author_raw = (
            post.get("author")
            or post.get("authorName")
            or post.get("author_name")
            or ""
        )
        author_url = ""
        if isinstance(author_raw, dict):
            author = str(
                author_raw.get("name") or author_raw.get("full_name") or ""
            ).strip()
            author_url = str(author_raw.get("url") or author_raw.get("link") or "").strip()
        else:
            author = str(author_raw).strip()

        url = str(
            post.get("url") or post.get("postUrl") or post.get("post_url") or ""
        ).strip()

        post_id = str(
            post.get("urn") or post.get("id") or post.get("postId") or f"LI{i + 1}"
        )

        date_raw = (
            post.get("datePublished")
            or post.get("date")
            or post.get("postedAt")
            or post.get("posted_at")
            or post.get("createdAt")
            or post.get("created_at")
        )
        date = _parse_date(date_raw)

        likes = _int_field(post, "likes", "likesCount", "likes_count", "numLikes", "likeCount")
        comments = _int_field(post, "comments", "commentsCount", "comments_count", "numComments", "commentCount")
        reposts = _int_field(post, "reposts", "repostsCount", "shares", "shareCount", "reshares")

        is_article = _is_article(url)
        items.append({
            "id": post_id,
            "text": text,
            "url": url,
            "author": author,
            "author_url": author_url,
            "date": date,
            "engagement": {
                "likes": likes,
                "comments": comments,
                "reposts": reposts,
            },
            "relevance": _ARTICLE_RELEVANCE if is_article else _POST_RELEVANCE,
            "is_article": is_article,
        })

    if from_date and to_date:
        in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
        out_of_range = len(items) - len(in_range)
        if in_range:
            items = in_range
            if out_of_range:
                _log(f"Filtered {out_of_range} posts outside date range")
        elif items:
            _log(f"No posts within date range, keeping all {len(items)}")

    return items


# --- Article enrichment ---------------------------------------------------
#
# LinkedIn articles (Pulse long-form) never appear in /search/posts results —
# every search hit is a /posts/ status update. Articles live only on the
# author's profile, under `articles[]`. To honor "an article is high signal"
# we run a bounded enrichment lane: when a returned post's author name matches
# the topic (i.e. this is a person topic and we already hold their profile
# URL), make ONE profile call and surface their articles as high-signal items.


def _normalize_name(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for name matching."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _token_run(needle: List[str], haystack: List[str]) -> bool:
    """True if `needle` appears as a contiguous run of whole tokens in `haystack`.

    Token-level (not substring) so "ai" never matches inside "daisuke" — matching
    is on word boundaries. Equality is the n == len(haystack) case.
    """
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _best_author_match(items: List[Dict[str, Any]], topic: str) -> str:
    """Return the profile URL of the post author whose name matches the topic.

    Person-topic detection without a global predicate: when a returned post's
    author has a multi-word name that the topic clearly refers to, treat the
    topic as being about that person and return their profile URL. Matching is
    on whole-token runs (the author's full name appears in the topic, or vice
    versa), and the topic itself must be at least two tokens — so single-word
    keyword topics ("AI", "Tesla") and short phrases never enrich, and a topic
    token can't accidentally match inside an unrelated author's name.
    """
    topic_tokens = _normalize_name(topic).split()
    if len(topic_tokens) < 2:
        return ""
    for item in items:
        name_tokens = _normalize_name(item.get("author", "")).split()
        url = (item.get("author_url") or "").strip()
        if not url or len(name_tokens) < 2:
            continue
        if _token_run(name_tokens, topic_tokens) or _token_run(topic_tokens, name_tokens):
            return url
    return ""


def search_profile(profile_url: str, token: str) -> Dict[str, Any]:
    """Fetch a LinkedIn profile (incl. `articles[]`) via ScrapeCreators."""
    if not token or not profile_url:
        return {}
    try:
        response = http.get(
            f"{SC_BASE}/profile",
            params={"url": profile_url},
            headers=http.scrapecreators_headers(token),
            timeout=30,
            retries=2,
        )
    except http.HTTPError as exc:
        _log(f"Profile fetch failed (HTTP {exc.status_code}): {exc}")
        return {}
    except Exception as exc:
        _log(f"Profile fetch failed: {type(exc).__name__}: {exc}")
        return {}
    return response if isinstance(response, dict) else {}


def parse_profile_articles(
    profile: Dict[str, Any],
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[Dict[str, Any]]:
    """Map a profile's `articles[]` into high-signal engine item dicts."""
    articles = profile.get("articles") or []
    author = str(profile.get("name") or "").strip()
    items: List[Dict[str, Any]] = []

    for i, art in enumerate(articles):
        if not isinstance(art, dict):
            continue
        headline = str(art.get("headline") or art.get("title") or "").strip()
        if not headline:
            continue
        url = str(art.get("url") or art.get("link") or "").strip()
        date = _parse_date(art.get("datePublished") or art.get("date"))
        items.append({
            "id": str(art.get("id") or f"LIA{i + 1}"),
            "text": headline,
            "url": url,
            "author": author,
            "date": date,
            "engagement": {},
            "relevance": _ARTICLE_RELEVANCE,
            "is_article": True,
        })

    if from_date and to_date:
        in_range = [i for i in items if i["date"] and from_date <= i["date"] <= to_date]
        if in_range:
            items = in_range
    return items


def enrich_articles(
    items: List[Dict[str, Any]],
    topic: str,
    token: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> List[Dict[str, Any]]:
    """Surface a person's LinkedIn articles as high-signal items.

    Bounded: fires only on person topics (a returned post author matches the
    topic) and makes at most ONE profile API call. No-ops gracefully when
    there's no match, no token, no profile, or no articles.
    """
    if not token:
        return []
    profile_url = _best_author_match(items, topic)
    if not profile_url:
        return []
    _log(f"Person topic — enriching articles from {profile_url}")
    profile = search_profile(profile_url, token)
    if not profile:
        return []
    articles = parse_profile_articles(profile, from_date=from_date, to_date=to_date)
    if articles:
        _log(f"Found {len(articles)} article(s)")
    return articles
