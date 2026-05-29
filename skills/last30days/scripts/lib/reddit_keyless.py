"""Keyless Reddit pipeline: tiered free search + comment enrichment.

Replaces the dead ``.json`` free path. Discovery tiers, cheapest/most-likely
first; enrichment then runs on whatever was discovered:

  Tier 0  one-shot legacy ``.json`` search — demoted. Datacenter IPs get 403,
          but a residential machine (where the skill usually runs) may still
          get 200, so it is worth one cheap try. Honors the "brute-force .json"
          intent without depending on it.
  Tier 1  RSS discovery (reddit_rss) — keyless, robust, the load-bearing path.
  Tier 2  shreddit comment + count enrichment (reddit_shreddit) for top posts.

Returns ``[]`` (never raises) so ``pipeline.py`` can fall through to the
ScrapeCreators backup when every keyless tier comes up empty.
"""

import concurrent.futures
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from collections import Counter

from . import reddit_rss, reddit_shreddit, reddit_listing

ENRICH_LIMITS = reddit_shreddit.ENRICH_LIMITS
ENRICH_BUDGET = 45  # seconds total across all enrichment threads
MAX_ENRICH_WORKERS = 4
MAX_DERIVED_SUBS = 5  # subreddits derived from RSS results for score backfill


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditKeyless] {msg}\n")
    sys.stderr.flush()


def _tier0_json(topic: str, depth: str) -> List[Dict[str, Any]]:
    """One cheap global ``.json`` discovery attempt. Returns [] on the 403 wall."""
    try:
        from . import reddit_public
        return reddit_public.search(topic, depth=depth) or []
    except Exception as e:  # never let the demoted tier sink the run
        _log(f"Tier 0 (.json) unavailable: {e}")
        return []


def _top_subreddits(posts: List[Dict[str, Any]], limit: int = MAX_DERIVED_SUBS) -> List[str]:
    """Most frequent subreddits across discovered posts (for score backfill)."""
    counts = Counter(p.get("subreddit", "") for p in posts if p.get("subreddit"))
    return [sub for sub, _ in counts.most_common(limit)]


def _apply_scores(post: Dict[str, Any], scored: Dict[str, int]) -> None:
    post["score"] = scored["score"]
    post["num_comments"] = scored["num_comments"]
    post.setdefault("engagement", {})["score"] = scored["score"]
    post["engagement"]["num_comments"] = scored["num_comments"]


def _discover(topic: str, depth: str, subreddits: Optional[List[str]]) -> List[Dict[str, Any]]:
    # Tier 0: demoted one-shot .json (dead for normal users too, but free to try).
    posts = _tier0_json(topic, depth)
    if posts:
        _log(f"Tier 0 (.json) returned {len(posts)} posts")
        return posts

    # Tier 1: keyless discovery. RSS gives breadth (incl. global keyword search);
    # the listing partials give real upvote scores.
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

    # Merge: scored listing posts first (targeted only), then RSS breadth,
    # backfilled with real scores where the post appears in a listing.
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    for p in listing_posts:
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
                executor.submit(_enrich_one, post): i
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


def search_and_enrich(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    subreddits: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Full keyless Reddit pipeline: discover (Tier 0/1) then enrich (Tier 2).

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        subreddits: Optional pre-resolved subreddit names (without r/)

    Returns:
        List of normalized item dicts matching the reddit_public output shape,
        with top_comments/comment_insights attached on enriched posts.
        Empty list when all keyless tiers fail (so SC backup can engage).
    """
    posts = _discover(topic, depth, subreddits)
    if not posts:
        return []

    # Date filter: keep posts in range or with unknown dates (mirrors reddit_public).
    posts = [
        p for p in posts
        if p.get("date") is None or (from_date <= p["date"] <= to_date)
    ]

    # Rank before enrichment by real upvote score (from listing cards / backfill),
    # then query relevance, then recency. Posts without a recovered score sort by
    # the latter two — same behavior as before scores were available.
    posts.sort(
        key=lambda p: (
            p.get("engagement", {}).get("score", 0) or 0,
            p.get("relevance", 0) or 0,
            p.get("date") or "",
        ),
        reverse=True,
    )

    posts = _enrich(posts, depth)

    for i, post in enumerate(posts):
        post["id"] = f"R{i + 1}"

    return posts
