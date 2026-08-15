"""DripStack source for last30days — premium financial newsletter search.

DripStack indexes paid Substack newsletters, analyst writeups, and financial
podcasts. The API has two tiers:

  Free (no auth): search, publication listing, publication search, and post
  metadata (titles, dates, prices). These endpoints power discovery — they
  tell you *what* analysts are writing about with publication attribution
  (e.g. "SemiAnalysis", "Bloomberg").

  Paid (DRIPSTACK_API_KEY): full synthesized article summaries and stock
  picks. Requires an API key from dripstack.xyz (My Profile > Dashboard >
  API Keys > + Create Api Key) with preloaded credits. The post endpoint
  returns HTTP 402 when unauthenticated; with a key, the bearer token is
  sent on retry.

The signal is complementary to the other financial sources: StockTwits gives
retail sentiment, Polymarket gives prediction-market odds, and DripStack gives
what professional analysts and paid newsletter authors are actually writing
about. The search results carry publication attribution which is high-
credibility signal for synthesis.

GATING: DripStack search is most valuable for finance, markets, company
analysis, and industry research topics. Like arXiv (science) and Techmeme
(tech news), DripStack is relevance-gated — the search API itself filters
for topic match, so off-topic runs return thin results naturally and the
engine's thin-retry + relevance scoring handles the rest.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import urllib.parse
from typing import Any

from . import http

_BASE_URL = "https://dripstack.xyz"
_SEARCH_URL = f"{_BASE_URL}/api/v1/search"
_PUBLICATIONS_URL = f"{_BASE_URL}/api/v1/publications"
_PUBLICATION_SEARCH_URL = f"{_BASE_URL}/api/v1/publications/search"
_UA = "Mozilla/5.0 (last30days dripstack source)"

# Depth controls how many results we request per subquery.
_DEPTH_LIMITS = {"quick": 5, "default": 10, "deep": 20}


def _log(msg: str) -> None:
    try:
        from . import log as _enginelog
        _enginelog.source_log("DripStack", msg, tty_only=False)
    except Exception:
        print(f"[DripStack] {msg}", file=sys.stderr)


def _get_json(url: str, timeout: int = 20, headers: dict[str, str] | None = None) -> dict[str, Any]:
    # All engine traffic goes through the shared lib/http.py choke point so
    # capture/replay, fixtures, and failure taxonomy apply to this source too.
    merged = {"User-Agent": _UA}
    if headers:
        merged.update(headers)
    return http.get(url, headers=merged, timeout=timeout, retries=2)


def search_dripstack(
    topic: str,
    from_date: str | None = None,
    to_date: str | None = None,
    *,
    depth: str = "default",
) -> list[dict[str, Any]]:
    """Search DripStack for articles matching the topic.

    Returns a list of raw item dicts from the search API. The free endpoint
    requires no authentication. Results are relevance-ranked by DripStack's
    own scoring (hybrid RRF — blended semantic + keyword match).

    Args:
        topic: The search query (e.g. "AI capex risk", "Tesla earnings").
        from_date: ISO date string for start of window (YYYY-MM-DD). Not sent
            to the API (DripStack search has its own time handling), but
            available for post-filtering if needed.
        to_date: ISO date string for end of window (YYYY-MM-DD).
        depth: One of "quick", "default", "deep" — controls result count.
    """
    limit = _DEPTH_LIMITS.get(depth, 10)
    params = urllib.parse.urlencode({"q": topic, "limit": limit})
    url = f"{_SEARCH_URL}?{params}"

    try:
        data = _get_json(url)
    except Exception as e:
        _log(f"search failed for '{topic}': {e}")
        return []

    items = data.get("items") or []
    if from_date or to_date:
        windowed = []
        dropped = 0
        for item in items:
            published = str(item.get("publishedAt") or "")[:10]
            if published and from_date and published < from_date:
                dropped += 1
                continue
            if published and to_date and published > to_date:
                dropped += 1
                continue
            windowed.append(item)
        if dropped:
            _log(f"dropped {dropped} result(s) outside the {from_date}..{to_date} window")
        items = windowed
    _log(f"search '{topic}': {len(items)} results (confidence: {data.get('matchConfidence', '?')})")
    return items


def parse_dripstack_response(
    items: list[dict[str, Any]],
    query: str = "",
) -> list[dict[str, Any]]:
    """Normalize DripStack search results into engine-style item dicts.

    Each item maps to the same shape as other sources (HN, Reddit, StockTwits):
    id, title, url, author, date, engagement, relevance, why_relevant,
    snippet, metadata.

    DripStack has no engagement signal (upvotes, likes), so engagement is
    empty. Ranking relies on DripStack's own relevanceScore (0-100) which we
    normalize to 0-1, plus recency.
    """
    parsed: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        title = (item.get("title") or "").strip()
        subtitle = (item.get("subtitle") or "").strip()
        snippet_text = (item.get("snippet") or "").strip()
        pub_slug = (item.get("publicationSlug") or "").strip()
        post_slug = (item.get("slug") or "").strip()
        published_at = (item.get("publishedAt") or "")[:10] or None

        # Build the article URL. For Substack-hosted publications the slug is
        # the full hostname (e.g. "newsletter.doomberg.com") and the post slug
        # is the path segment. For other domains the same pattern applies.
        if pub_slug and post_slug:
            url = f"https://{pub_slug}/{post_slug}"
        else:
            url = ""

        # Normalize DripStack's 0-100 relevanceScore to 0-1 for the engine.
        raw_score = item.get("relevanceScore", 0)
        try:
            relevance = round(min(1.0, max(0.0, float(raw_score) / 100.0)), 2)
        except (TypeError, ValueError):
            relevance = 0.5

        # Build a human-readable why_relevant from the whyMatched array.
        why_parts = item.get("whyMatched") or []
        # Filter out internal RRF details; keep the useful match explanations.
        why_clean = [
            w for w in why_parts
            if "RRF" not in w and "Hybrid" not in w
        ]
        why_relevant = "; ".join(why_clean) if why_clean else f"DripStack newsletter match for: {query}"

        # The body feeds rerank and synthesis. Use subtitle (the article
        # summary/lede) as the primary content, falling back to snippet.
        body = subtitle or snippet_text or title

        # Publication name as author — gives attribution credit to the
        # newsletter/analyst who wrote it (e.g. "SemiAnalysis", "Bloomberg").
        # Use the slug as a readable fallback.
        author = pub_slug.replace(".substack.com", "").replace(".com", "")

        parsed.append({
            "id": f"DS{i + 1}",
            "title": title or f"DripStack result {i + 1}",
            "url": url,
            "author": author or None,
            "date": published_at,
            "engagement": {},
            "relevance": relevance,
            "why_relevant": why_relevant,
            "body": body,
            "snippet": snippet_text[:400],
            "metadata": {
                "publication_slug": pub_slug,
                "post_slug": post_slug,
                "relevance_score": raw_score,
                "match_confidence": item.get("matchConfidence"),
                "topic_coverage_ratio": item.get("topicCoverageRatio"),
            },
        })
    return parsed


# --------------------------------------------------------------------------- #
# Publication browsing (free) and post fetch (paid)                            #
# --------------------------------------------------------------------------- #


def get_publications(timeout: int = 20) -> list[dict[str, Any]]:
    """List all curated publications. Free, no auth required.

    Returns a list of publication dicts with slug, title, description,
    siteUrl, and lastSyncedAt.
    """
    try:
        data = _get_json(_PUBLICATIONS_URL, timeout=timeout)
    except Exception as e:
        _log(f"list publications failed: {e}")
        return []
    return data.get("publications") or []


def search_publications(query: str, timeout: int = 20) -> list[dict[str, Any]]:
    """Search publications by name, author, or slug. Free, no auth required.

    Args:
        query: Publication name, author, podcast show, newsletter, or slug.
            Minimum 2 characters.

    Returns up to 3 matches with publicationSlug, title, author, siteUrl.
    """
    if len(query.strip()) < 2:
        return []
    params = urllib.parse.urlencode({"q": query})
    url = f"{_PUBLICATION_SEARCH_URL}?{params}"
    try:
        data = _get_json(url, timeout=timeout)
    except Exception as e:
        _log(f"search publications failed for '{query}': {e}")
        return []
    return data.get("items") or []


def get_publication_posts(
    pub_slug: str,
    limit: int = 10,
    timeout: int = 20,
) -> dict[str, Any]:
    """Get publication metadata and recent post list. Free, no auth required.

    Args:
        pub_slug: Publication slug (the normalized host, e.g.
            "newsletter.semianalysis.com").
        limit: Max posts to return (1-100).

    Returns the full publication detail dict including a "posts" list with
    title, slug, subtitle, publishedAt, and priceCents per post. Returns an
    empty dict on failure.
    """
    params = urllib.parse.urlencode({"limit": limit})
    url = f"{_PUBLICATIONS_URL}/{urllib.parse.quote(pub_slug, safe='')}?{params}"
    try:
        return _get_json(url, timeout=timeout)
    except Exception as e:
        _log(f"get publication posts failed for '{pub_slug}': {e}")
        return {}


def get_publication_post(
    pub_slug: str,
    post_slug: str,
    api_key: str | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Fetch a single post's synthesized summary. Paid; requires api_key.

    Without an API key, the endpoint returns HTTP 402 (payment required).
    With a key, the bearer token is sent on the first attempt. If the key
    is missing, returns an empty dict without hitting the endpoint.

    Args:
        pub_slug: Publication slug.
        post_slug: Post slug from the publication feed.
        api_key: Optional DripStack API key (starts with pk_drip_). When
            None, the fetch is skipped to avoid a guaranteed 402.
        timeout: HTTP timeout in seconds.

    Returns the post dict with synthesizedSummary on success, empty dict
    on failure or missing key.
    """
    if not api_key:
        return {}
    encoded_pub = urllib.parse.quote(pub_slug, safe="")
    encoded_post = urllib.parse.quote(post_slug, safe="")
    url = f"{_PUBLICATIONS_URL}/{encoded_pub}/{encoded_post}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        return _get_json(url, timeout=timeout, headers=headers)
    except http.HTTPError as e:
        if e.status_code == 402:
            _log(f"post '{pub_slug}/{post_slug}' requires payment (402)")
        elif e.status_code == 404:
            _log(f"post '{pub_slug}/{post_slug}' not found (404)")
        elif e.status_code == 503:
            _log(f"post '{pub_slug}/{post_slug}' summary not ready (503)")
        else:
            _log(f"fetch post '{pub_slug}/{post_slug}' failed: {e}")
        return {}
    except Exception as e:
        _log(f"fetch post '{pub_slug}/{post_slug}' failed: {e}")
        return {}


# --------------------------------------------------------------------------- #
# Standalone CLI                                                               #
#   python3 dripstack.py "AI capex risk"                                      #
#   python3 dripstack.py --publications                                       #
#   python3 dripstack.py --search-pub semianalysis                            #
#   python3 dripstack.py --pub-posts newsletter.semianalysis.com              #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    args = sys.argv[1:]

    if args and args[0] == "--publications":
        pubs = get_publications()
        print(f"{len(pubs)} publications:")
        for p in pubs:
            desc = (p.get("description") or "")[:80]
            print(f"  {p['slug']} - {p.get('title') or '(untitled)'}")
            if desc:
                print(f"    {desc}")
        sys.exit(0)

    if args and args[0] == "--search-pub":
        q = " ".join(args[1:]) or ""
        results = search_publications(q)
        print(f"{len(results)} matches for '{q}':")
        for r in results:
            print(f"  {r['publicationSlug']} - {r.get('title') or '(untitled)'} ({r.get('author') or '?'})")
        sys.exit(0)

    if args and args[0] == "--pub-posts":
        slug = args[1] if len(args) > 1 else ""
        if not slug:
            print("Usage: dripstack.py --pub-posts <publication-slug>", file=sys.stderr)
            sys.exit(1)
        detail = get_publication_posts(slug)
        posts = detail.get("posts") or []
        title = detail.get("title") or slug
        print(f"{title} - {len(posts)} posts:")
        for p in posts:
            date = (p.get("publishedAt") or "")[:10] or "no date"
            price = p.get("priceCents")
            price_str = f" (${price / 100:.2f})" if isinstance(price, (int, float)) else ""
            print(f"  [{date}] {p['title']}{price_str}")
        sys.exit(0)

    topic = " ".join(args) or "AI capex"
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=30)).isoformat()

    raw = search_dripstack(topic, from_date=since, depth="default")
    items = parse_dripstack_response(raw, query=topic)

    print(f"Query: {topic} | {len(items)} results")
    for it in items[:10]:
        print(f"  [{it['relevance']:.0%}] {it['title']} ({it['author']}, {it['date'] or 'no date'})")
        if it["snippet"]:
            print(f"    {it['snippet'][:120]}")
