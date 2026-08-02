"""LinkedIn post search via ScrapeCreators API.

Searches public LinkedIn posts by keyword using the ScrapeCreators
/v1/linkedin/search/posts endpoint, which uses Google-indexed LinkedIn
content to bypass auth requirements.

Requires SCRAPECREATORS_API_KEY environment variable.
"""

from __future__ import annotations

import datetime
import hashlib
import re
import time
from typing import Any, Dict, List

from . import http, log

SC_BASE = "https://api.scrapecreators.com/v1/linkedin"

DEPTH_CONFIG: dict[str, dict[str, Any]] = {
    "quick": {"date_posted": "last-week", "max_results": 10},
    "default": {"date_posted": "last-month", "max_results": 20},
    "deep": {"date_posted": "last-month", "max_results": 30},
}

# The upstream endpoint only accepts these coarse buckets. Anything else is a
# hard 400, so an arbitrary day count has to be widened to the smallest bucket
# that still covers it; the caller's real from_date/to_date window is then
# enforced downstream by normalize.filter_by_date_range().
DATE_POSTED_BUCKETS: tuple[tuple[int, str], ...] = (
    (1, "last-day"),
    (7, "last-week"),
    (31, "last-month"),
    (366, "last-year"),
)

# Queries past this length 404 upstream. Observed: 108 chars fails, 79 passes.
MAX_QUERY_CHARS = 80

# Each cursor page returns ~10 posts; this bounds a runaway pagination loop.
MAX_PAGES = 10

# Consecutive all-duplicate pages tolerated before a bucket is abandoned. The
# covering bucket's first page routinely repeats the narrow bucket's top hits,
# so breaking on the first zero-add page would skip the older tail the second
# bucket exists to fetch.
MAX_EMPTY_ADD_PAGES = 3

# Whole-call wall-clock budget. Pagination bounds the request COUNT, not time:
# 2 buckets x MAX_PAGES pages x (30s timeout + retry) is ~10 minutes against a
# slow-but-alive endpoint, with no error to attribute the stall to.
SEARCH_BUDGET_SECONDS = 120.0

# Endpoint-scoped failures. These are properties of the credential or the
# account, not of one bucket, so retrying the next bucket only wastes a call
# and — for 429 — deepens the rate limit already being hit.
FATAL_STATUS_CODES = frozenset({401, 403, 429})


def _log(msg: str) -> None:
    log.source_log("LinkedIn", msg, tty_only=False)


def _coerce_date(raw: Any) -> datetime.date | None:
    """Parse a YYYY-MM-DD date, tolerating a full ISO timestamp."""
    try:
        return datetime.date.fromisoformat(str(raw)[:10])
    except (TypeError, ValueError):
        return None


def _buckets_for_window(
    from_date: str,
    to_date: str,
    fallback: str,
    today: datetime.date | None = None,
) -> List[str]:
    """Buckets to query for a window, narrowest first.

    Two properties of the upstream endpoint drive this:

    1. Buckets are relative to NOW, not to the requested window. So the bucket
       has to be sized by how OLD the requested window is (today - from_date),
       never by its span. Sizing by span would send a 31-day window from two
       years ago to 'last-month', which cannot contain a single matching post.
    2. Results inside a bucket are relevance-ranked, not recency-ranked, so a
       widened bucket does NOT contain the narrower one's yield: 'last-year'
       for a 90-day window returned 30 posts spread across a full year, only 4
       inside the window. So query the narrow bucket for dense recent coverage
       AND the covering bucket for the older tail, then union them.

    The caller's exact window is still enforced downstream by
    filter_by_date_range(); these buckets only have to COVER it.
    """
    start = _coerce_date(from_date)
    end = _coerce_date(to_date)
    if start is None or end is None:
        _log(f"Unparseable window ({from_date!r}..{to_date!r}), using {fallback}")
        return [fallback]
    if end < start:
        _log(f"Inverted window ({from_date}..{to_date}), using {fallback}")
        return [fallback]

    today = today or datetime.date.today()
    # Age of the OLDEST requested post, which is what the bucket must reach
    # back to. +1 because the window is inclusive of from_date.
    age_days = (today - start).days + 1
    if age_days < 0:
        _log(f"Window starts in the future ({from_date}), using {fallback}")
        return [fallback]

    widest_limit, widest_bucket = DATE_POSTED_BUCKETS[-1]
    covering = widest_bucket
    for limit, bucket in DATE_POSTED_BUCKETS:
        if age_days <= limit:
            covering = bucket
            break
    else:
        _log(
            f"Window reaches back {age_days}d but the widest bucket is "
            f"{widest_bucket} ({widest_limit}d) — older results are unreachable"
        )

    buckets = [covering]
    # Backfill the dense recent block that the wider bucket skips over.
    for _, bucket in DATE_POSTED_BUCKETS:
        if bucket == covering:
            break
        if bucket not in buckets:
            buckets.insert(-1, bucket)
    # Only the immediately-narrower bucket is worth the extra calls.
    return buckets[-2:]


def _trim_query(topic: str) -> str:
    """Trim to MAX_QUERY_CHARS to avoid an upstream 404.

    Trimming is lossy: a qualifier at the end of a long query is dropped and
    the search means something narrower than the caller asked for. The log
    distinguishes a clean word-boundary trim from a mid-word hard cut so the
    message never overstates what was preserved.
    """
    if len(topic) <= MAX_QUERY_CHARS:
        return topic
    head = topic[:MAX_QUERY_CHARS]
    cut = head.rsplit(" ", 1)[0].strip() if " " in head.strip() else ""
    if cut:
        _log(f"Query too long ({len(topic)} chars), trimmed to {len(cut)} on a word boundary")
        return cut
    hard = head.strip() or topic.strip()[:MAX_QUERY_CHARS]
    if not hard:
        _log(f"Query is {len(topic)} chars of whitespace — nothing to search")
    else:
        _log(f"Query too long ({len(topic)} chars), HARD-CUT mid-token to {len(hard)}")
    return hard


def _dedupe_key(post: Dict[str, Any]) -> str:
    """Stable identity for a post.

    Falls back to a content fingerprint when no identifier is present. Without
    it, keyless posts are never deduped, so the union returns them once per
    bucket AND a repeating cursor never trips the all-duplicates guard.
    """
    for field in ("url", "postUrl", "post_url", "urn", "id", "postId"):
        val = post.get(field)
        if val:
            return str(val)
    body = str(post.get("description") or post.get("text") or "")
    author = str(post.get("author") or "")
    if not body and not author:
        return ""
    return "sha1:" + hashlib.sha1(f"{author}\x00{body}".encode()).hexdigest()


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
        from_date: Window start date (YYYY-MM-DD) — sets the date_posted bucket.
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
    buckets = _buckets_for_window(from_date, to_date, cfg["date_posted"])
    max_results = cfg["max_results"]
    query = _trim_query(topic)
    if not query:
        _log("Empty query after trim — skipping")
        return {"posts": []}

    # max_results bounds the RETURNED collection, not each retrieval lane, so
    # split it across buckets rather than granting each the full budget.
    quota = max(1, -(-max_results // len(buckets)))
    deadline = time.monotonic() + SEARCH_BUDGET_SECONDS

    _log(
        f"Searching for '{query}' (date_posted={','.join(buckets)}, "
        f"max_results={max_results}, quota/window={quota})"
    )

    posts: List[Dict[str, Any]] = []
    seen: set[str] = set()
    errors: List[str] = []
    fatal = False

    for date_posted in buckets:
        if fatal or len(posts) >= max_results:
            break

        bucket_count = 0
        pages = 0
        empty_adds = 0
        cursor: Any = None
        prev_cursor: Any = None
        stop = "pages exhausted"

        for _ in range(MAX_PAGES):
            if time.monotonic() > deadline:
                stop = "wall-clock budget exceeded"
                errors.append(f"{date_posted}: search budget of {SEARCH_BUDGET_SECONDS}s exceeded")
                break

            params: Dict[str, Any] = {"query": query, "date_posted": date_posted}
            if cursor:
                params["cursor"] = cursor
            try:
                response = http.get(
                    f"{SC_BASE}/search/posts",
                    params=params,
                    headers=http.scrapecreators_headers(token),
                    timeout=30,
                    retries=2,
                )
            except http.HTTPError as exc:
                _log(f"Search failed (HTTP {exc.status_code}, {date_posted}): {exc}")
                errors.append(f"{date_posted}: {exc}")
                stop = f"HTTP {exc.status_code}"
                if exc.status_code in FATAL_STATUS_CODES:
                    # Credential- or account-scoped: the next bucket would fail
                    # identically, and retrying a 429 deepens the rate limit.
                    fatal = True
                break
            except Exception as exc:
                _log(f"Search failed ({date_posted}): {type(exc).__name__}: {exc}")
                errors.append(f"{date_posted}: {type(exc).__name__}: {exc}")
                stop = type(exc).__name__
                fatal = True
                break

            pages += 1
            page = _extract_posts(response)
            if not page:
                stop = "empty page"
                break

            added = 0
            for post in page:
                if len(posts) >= max_results:
                    break
                key = _dedupe_key(post)
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
                posts.append(post)
                added += 1
                bucket_count += 1

            if len(posts) >= max_results:
                stop = "max_results reached"
                break
            if bucket_count >= quota:
                stop = "window quota reached"
                break

            # An all-duplicate page is expected where buckets overlap; only a
            # run of them means the cursor has stopped yielding anything new.
            empty_adds = empty_adds + 1 if added == 0 else 0
            if empty_adds >= MAX_EMPTY_ADD_PAGES:
                stop = f"{empty_adds} consecutive all-duplicate pages"
                break

            raw_cursor = response.get("cursor") if isinstance(response, dict) else None
            cursor = raw_cursor if isinstance(raw_cursor, (str, int)) else None
            if not cursor:
                stop = "no cursor"
                break
            if cursor == prev_cursor:
                stop = "cursor stopped advancing"
                break
            prev_cursor = cursor

        _log(f"  {date_posted}: {bucket_count} posts over {pages} page(s), stopped: {stop}")

    if not posts and errors:
        return {"posts": [], "error": errors[0]}

    result: Dict[str, Any] = {"posts": posts}
    if errors:
        # A window that failed after another succeeded must not read as a
        # complete result — downstream would treat thin coverage as a finding
        # about LinkedIn rather than about the request that never landed.
        result["partial"] = True
        result["error"] = errors[0]
        _log(f"PARTIAL — {len(errors)} window/page failure(s); first: {errors[0]}")

    _log(f"Found {len(posts)} posts across {len(buckets)} window(s)")
    return result


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
