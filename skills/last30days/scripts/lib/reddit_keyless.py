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

from . import reddit_rss, reddit_shreddit

ENRICH_LIMITS = reddit_shreddit.ENRICH_LIMITS
ENRICH_BUDGET = 45  # seconds total across all enrichment threads
MAX_ENRICH_WORKERS = 4


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


def _discover(topic: str, depth: str, subreddits: Optional[List[str]]) -> List[Dict[str, Any]]:
    posts = _tier0_json(topic, depth)
    if posts:
        _log(f"Tier 0 (.json) returned {len(posts)} posts")
        return posts
    posts = reddit_rss.search_rss(topic, depth=depth, subreddits=subreddits)
    _log(f"Tier 1 (RSS) returned {len(posts)} posts")
    return posts


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

    # Rank before enrichment. Keyless discovery has no post upvote score, so rank
    # by query relevance then recency; RSS listing feeds already front-load
    # popular posts, so the top of this order is a sound enrichment target.
    posts.sort(
        key=lambda p: (p.get("relevance", 0) or 0, p.get("date") or ""),
        reverse=True,
    )

    posts = _enrich(posts, depth)

    for i, post in enumerate(posts):
        post["id"] = f"R{i + 1}"

    return posts
