"""Keyless Reddit pipeline: free discovery + comment enrichment.

``search.json`` is permanently 403/429 keyless, so it is not used. Discovery
runs on the surfaces that still serve data without a key, then enrichment runs
on whatever was discovered:

  Dedicated lane  entity-home subreddits (e.g. r/Kanye) pulled in full via the
                  shreddit listing partials (top+hot+new, real scores), kept
                  whole — floor-exempt — because the sub IS the topic.
  RSS lane        reddit_rss breadth (incl. global keyword search) + broad-sub
                  listing partials for real upvote scores. Relevance-floored.
  Enrichment      shreddit comment + count enrichment (reddit_shreddit) for the
                  top-ranked posts (author + score + text + permalink).

Returns ``[]`` (never raises) so ``pipeline.py`` can fall through to the
ScrapeCreators backup when every keyless lane comes up empty.
"""

import concurrent.futures
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from collections import Counter

from . import http
from . import reddit_rss, reddit_shreddit, reddit_listing, reddit_arctic
# Scores are backfilled from popular derived subreddits, so an engagement-first
# final sort buries on-topic RSS hits under viral off-topic posts. A relevance
# floor + relevance-first final ranking keeps the section on-topic. Thresholds
# are shared with the keyed path (reddit.py) via relevance.py.
from .relevance import RELEVANCE_FLOOR, MIN_ON_TOPIC

ENRICH_LIMITS = reddit_shreddit.ENRICH_LIMITS
ENRICH_BUDGET = 45  # seconds total across all enrichment threads
MAX_ENRICH_WORKERS = 4
MAX_DERIVED_SUBS = 5  # subreddits derived from RSS results for score backfill
# Dedicated subreddits (the entity's home, e.g. r/Kanye for "Kanye West") are
# wholly on-topic, so pull top+hot+new — the top-of-month listing alone misses
# fresh threads — and keep every item (floor-exempt).
DEDICATED_SORTS = ["top", "hot", "new"]


def _relevance_rank_key(post: Dict[str, Any]) -> float:
    """Rank by relevance first, with a bounded engagement bonus as tiebreaker.

    Mirrors reddit.py: the log-scaled bonus (capped at 0.25) orders
    similarly-relevant posts by discussion volume but is too small to lift an
    off-topic post (relevance ~0) above an on-topic one.
    """
    eng = post.get("engagement", {})
    total = (eng.get("score", 0) or 0) + (eng.get("num_comments", 0) or 0)
    return (post.get("relevance") or 0.0) + min(0.25, math.log10(total + 1) / 20.0)


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditKeyless] {msg}\n")
    sys.stderr.flush()


def _top_subreddits(posts: List[Dict[str, Any]], limit: int = MAX_DERIVED_SUBS) -> List[str]:
    """Most frequent subreddits across discovered posts (for score backfill)."""
    counts = Counter(p.get("subreddit", "") for p in posts if p.get("subreddit"))
    return [sub for sub, _ in counts.most_common(limit)]


def _apply_scores(post: Dict[str, Any], scored: Dict[str, int]) -> None:
    post["score"] = scored["score"]
    post["num_comments"] = scored["num_comments"]
    post.setdefault("engagement", {})["score"] = scored["score"]
    post["engagement"]["num_comments"] = scored["num_comments"]


def _discover(
    topic: str,
    depth: str,
    subreddits: Optional[List[str]],
    dedicated_subreddits: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    # Dedicated lane: the entity's home subs are wholly on-topic. Pull
    # top+hot+new (real scores from the listing) and mark them floor-exempt so
    # an on-topic post whose title lacks the entity name is never dropped.
    dedicated_posts: List[Dict[str, Any]] = []
    if dedicated_subreddits:
        dedicated_posts = reddit_listing.fetch_listings(
            dedicated_subreddits, depth=depth, query=topic, sorts=DEDICATED_SORTS
        )
        for p in dedicated_posts:
            p["dedicated"] = True
        _log(f"Dedicated lane: {len(dedicated_posts)} posts from {dedicated_subreddits}")

    # search.json is permanently 403/429 keyless (no Tier 0). Discovery is RSS
    # breadth (incl. global keyword search) + broad-sub listing partials for
    # real upvote scores.
    rss_posts = reddit_rss.search_rss(topic, depth=depth, subreddits=subreddits)

    if subreddits:
        # Targeted run: the caller chose these subreddits, so their listing cards
        # are on-topic — include them as scored discovery AND as a score source.
        listing_posts = reddit_listing.fetch_listings(subreddits, depth=depth, query=topic)
        score_source = listing_posts
    else:
        # Bare global run: subreddits derived from noisy RSS results are NOT
        # reliably on-topic, so their listings are used ONLY to backfill scores
        # onto the keyword-matched RSS posts — never merged as discovery, which
        # would flood results with high-upvote but irrelevant posts.
        listing_posts = []
        derived = _top_subreddits(rss_posts)
        score_source = reddit_listing.fetch_listings(derived, depth=depth, query=topic)
    _log(
        f"Tier 1 (RSS) {len(rss_posts)} posts; "
        f"{'listing discovery ' + str(len(listing_posts)) if subreddits else 'score-only'}; "
        f"{len(score_source)} scored cards"
    )

    # Score lookup by post id, from the scored listing cards.
    score_map: Dict[str, Dict[str, int]] = {}
    for p in score_source:
        pid = p.get("metadata", {}).get("post_id", "")
        if pid:
            score_map[pid] = {"score": p["score"], "num_comments": p["num_comments"]}

    # Merge: dedicated-sub posts first (floor-exempt), then scored broad listing
    # posts (targeted only), then RSS breadth backfilled with real scores where
    # the post appears in a listing. First writer wins the dedupe, so a thread
    # in both the dedicated lane and a listing keeps its floor-exempt status.
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    for p in dedicated_posts + listing_posts:
        if p["url"] not in seen:
            seen.add(p["url"])
            merged.append(p)
    for p in rss_posts:
        if p["url"] in seen:
            continue
        pid = reddit_listing._post_id(p["url"])
        if pid in score_map:
            _apply_scores(p, score_map[pid])
        seen.add(p["url"])
        merged.append(p)

    # Backfill scores for RSS-only posts (no listing card scored them) from the
    # free arctic-shift archive. Posts already scored by a listing keep that
    # live score; arctic only fills the gap, and is best-effort (never raises).
    need = [pid for p in merged
            if not (p.get("engagement", {}).get("score"))
            for pid in [reddit_listing._post_id(p["url"])] if pid]
    if need:
        scores = reddit_arctic.fetch_scores(need)
        filled = 0
        for p in merged:
            if p.get("engagement", {}).get("score"):
                continue
            pid = reddit_listing._post_id(p["url"])
            if pid in scores:
                _apply_scores(p, scores[pid])
                filled += 1
        if filled:
            _log(f"arctic-shift backfilled {filled} post scores")
    return merged


def _enrich_one(post: Dict[str, Any]) -> Dict[str, Any]:
    """Attach shreddit comments + real comment count. Never raises."""
    try:
        data = reddit_shreddit.fetch_comments(post.get("url", ""))
        if data.get("top_comments"):
            post["top_comments"] = data["top_comments"]
        if data.get("comment_insights"):
            post["comment_insights"] = data["comment_insights"]
        num = data.get("num_comments")
        if num is not None:
            post["num_comments"] = num
            post.setdefault("engagement", {})["num_comments"] = num
    except Exception:
        pass  # keep the post with whatever discovery gave us
    return post


def _enrich(posts: List[Dict[str, Any]], depth: str) -> List[Dict[str, Any]]:
    """Enrich the top N posts with comments under a total time budget."""
    limit = ENRICH_LIMITS.get(depth, ENRICH_LIMITS["default"])
    to_enrich = posts[:limit]
    rest = posts[limit:]
    if not to_enrich:
        return posts

    result_map: Dict[int, Dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=min(limit, MAX_ENRICH_WORKERS)) as executor:
            futures = {
                http.submit_with_context(executor, _enrich_one, post): i
                for i, post in enumerate(to_enrich)
            }
            done, not_done = concurrent.futures.wait(futures, timeout=ENRICH_BUDGET)
            for future in done:
                idx = futures[future]
                try:
                    result_map[idx] = future.result(timeout=0)
                except Exception:
                    result_map[idx] = to_enrich[idx]
            for future in not_done:
                idx = futures[future]
                result_map[idx] = to_enrich[idx]
                future.cancel()
        enriched = [result_map[i] for i in range(len(to_enrich))]
    except Exception:
        enriched = to_enrich

    return enriched + rest


def _slot_priority(topic: str, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Order posts for enrichment slots: entity-matching posts first.

    Comment slots (ENRICH_LIMITS) are scarce; spending them on high-upvote
    posts that rerank later demotes as entity misses starves the on-topic
    posts the user actually sees (2026-06-06 "OpenClaw vs Hermes" run:
    2,000+ upvote Gemma/GPU threads took every slot, then were demoted to
    zero). Mirror rerank's demotion signal via the shared `_entity_grounded`
    check (head token of the topic's stripped primary entity present in the
    post text) so slots go to posts likely to survive final ranking — keying
    on the same head token keeps the two paths from diverging. Falls back to
    token-overlap relevance when the topic yields no usable primary entity.
    Within each tier the incoming
    (score-first) order is preserved. Never raises; on any failure the
    incoming order is returned unchanged.
    """
    try:
        from . import relevance, rerank

        def _post_text(post: Dict[str, Any]) -> str:
            return f"{post.get('title') or ''} {post.get('selftext') or ''}"

        entity = rerank._primary_entity(topic).lower()
        if entity:
            def _matches(post: Dict[str, Any]) -> bool:
                return rerank._entity_grounded(_post_text(post), entity)
        else:
            prepared = relevance.PreparedQuery(topic)

            def _matches(post: Dict[str, Any]) -> bool:
                return relevance.token_overlap_relevance(prepared, _post_text(post)) > 0.24

        matches: List[Dict[str, Any]] = []
        misses: List[Dict[str, Any]] = []
        for post in posts:
            (matches if _matches(post) else misses).append(post)
        return matches + misses
    except Exception:
        return posts


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
    dedicated_subreddits: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Full keyless Reddit pipeline: discover then enrich.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        subreddits: Optional pre-resolved broad/category subreddit names (no r/)
        dedicated_subreddits: Optional entity-home subreddit names (no r/) pulled
            in full (top+hot+new) and exempt from the relevance floor.

    Returns:
        List of normalized item dicts matching the reddit_public output shape,
        with top_comments/comment_insights attached on enriched posts.
        Empty list when all keyless tiers fail (so SC backup can engage).
    """
    posts = _discover(topic, depth, subreddits, dedicated_subreddits)
    if not posts:
        return []

    # Date filter: keep posts in range or with unknown dates (mirrors reddit_public).
    posts = [
        p for p in posts
        if p.get("date") is None or (from_date <= p["date"] <= to_date)
    ]

    # Relevance floor: strip zero-overlap posts (relevance exactly 0 = no
    # title/body token match at all) when anything relevant remains, so
    # backfilled high-upvote posts from popular subs can't bury on-topic RSS
    # hits. Keep all only when nothing scored above zero.
    before = len(posts)
    # Dedicated-sub posts are floor-exempt: their whole subreddit is the topic,
    # so an on-topic post whose title lacks the entity name must not be dropped.
    on_topic = [p for p in posts if p.get("dedicated") or (p.get("relevance") or 0) >= RELEVANCE_FLOOR]
    if len(on_topic) >= MIN_ON_TOPIC:
        posts = on_topic
    else:
        nonzero = [p for p in posts if p.get("dedicated") or (p.get("relevance") or 0) > 0]
        if nonzero:
            posts = nonzero
    if len(posts) < before:
        _log(f"Relevance floor dropped {before - len(posts)} off-topic posts")

    # Provisional score-first order so enrichment-slot selection has a stable
    # within-tier order to preserve.
    posts.sort(
        key=lambda p: (
            p.get("engagement", {}).get("score", 0) or 0,
            p.get("relevance", 0) or 0,
            p.get("date") or "",
        ),
        reverse=True,
    )

    # Enrichment slot selection is relevance-aware: entity-matching posts
    # claim the scarce comment slots first (score order preserved within
    # each tier).
    posts = _enrich(_slot_priority(topic, posts), depth)

    # Final display order ranks relevance-first with a bounded engagement bonus,
    # so an off-topic high-upvote post can't outrank an on-topic one in what the
    # user sees. Enrichment above may have backfilled real comment counts.
    posts.sort(key=_relevance_rank_key, reverse=True)

    for i, post in enumerate(posts):
        post["id"] = f"R{i + 1}"

    return posts
