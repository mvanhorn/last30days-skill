"""DripStack source for last30days — premium financial newsletter search.

DripStack indexes paid Substack newsletters, analyst writeups, and financial
podcasts. The search endpoint is free and public (no API key); it returns
article metadata including title, publication, date, and a relevance-scored
snippet. Full article summaries and stock picks are behind a paid layer and
are out of scope for this source adapter.

The signal is complementary to the other financial sources: StockTwits gives
retail sentiment, Polymarket gives prediction-market odds, and DripStack gives
what professional analysts and paid newsletter authors are actually writing
about. The search results carry publication attribution (e.g. "SemiAnalysis",
"Bloomberg") which is high-credibility signal for synthesis.

GATING: DripStack search is most valuable for finance, markets, company
analysis, and industry research topics. Like arXiv (science) and Techmeme
(tech news), DripStack is relevance-gated — the search API itself filters
for topic match, so off-topic runs return thin results naturally and the
engine's thin-retry + relevance scoring handles the rest.

API: public, no auth. Search endpoint returns up to 30 items per query.
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
_UA = "Mozilla/5.0 (last30days dripstack source)"

# Depth controls how many results we request per subquery.
_DEPTH_LIMITS = {"quick": 5, "default": 10, "deep": 20}


def _log(msg: str) -> None:
    try:
        from . import log as _enginelog
        _enginelog.source_log("DripStack", msg, tty_only=False)
    except Exception:
        print(f"[DripStack] {msg}", file=sys.stderr)


def _get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    # All engine traffic goes through the shared lib/http.py choke point so
    # capture/replay, fixtures, and failure taxonomy apply to this source too.
    return http.get(url, headers={"User-Agent": _UA}, timeout=timeout, retries=2)


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
# Standalone CLI                                                               #
#   python3 dripstack.py "AI capex risk"                                      #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    topic = " ".join(sys.argv[1:]) or "AI capex"
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=30)).isoformat()

    raw = search_dripstack(topic, from_date=since, depth="default")
    items = parse_dripstack_response(raw, query=topic)

    print(f"Query: {topic} | {len(items)} results")
    for it in items[:10]:
        print(f"  [{it['relevance']:.0%}] {it['title']} ({it['author']}, {it['date'] or 'no date'})")
        if it["snippet"]:
            print(f"    {it['snippet'][:120]}")
