"""Keyless Reddit pipeline: free discovery + comment enrichment.

``search.json`` is permanently 403/429 keyless, so it is not used. Discovery
runs on the surfaces that still serve data without a key, then enrichment runs
on whatever was discovered:

  Dedicated lane  entity-home subreddits (e.g. r/Kanye) pulled in full via the
                  shreddit listing partials (top+hot+new, real scores), kept
                  whole (floor-exempt) because the sub IS the topic.
  Search lane     Reddit's site search fragment (reddit_search): global keyword
                  search plus per-sub search for targeted subreddits. Results
                  arrive dated and scored. Targeted runs also merge the
                  subreddits' listing partials. Relevance-floored.
  Enrichment      shreddit comment + count enrichment (reddit_shreddit) for the
                  top-ranked posts (author + score + text + permalink).

Returns ``[]`` (never raises) so ``pipeline.py`` can fall through to the
ScrapeCreators backup when every keyless lane comes up empty.
"""

import concurrent.futures
import contextlib
import contextvars
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import http
from . import reddit_search, reddit_shreddit, reddit_listing, reddit_arctic
# High-upvote posts from targeted-sub listings can bury on-topic search hits
# under an engagement-first final sort. A relevance floor + relevance-first
# final ranking keeps the section on-topic. Thresholds are shared with the
# keyed path (reddit.py) via relevance.py.
from .relevance import RELEVANCE_FLOOR, MIN_ON_TOPIC

ENRICH_LIMITS = reddit_shreddit.ENRICH_LIMITS
ENRICH_BUDGET = 45  # seconds total across all enrichment threads
MAX_ENRICH_WORKERS = 4
# Dedicated subreddits (the entity's home, e.g. r/Kanye for "Kanye West") are
# wholly on-topic, so pull top+hot+new — the top-of-month listing alone misses
# fresh threads — and keep every item (floor-exempt).
DEDICATED_SORTS = ["top", "hot", "new"]
# The lane's stages each carry their own budget (listing archive 45s twice,
# site search, enrichment 45s plus the shared bucket's queue), and on a busy
# run their sum passes the pipeline's wait for the source, which then reports
# a timeout and keeps none of the threads already found. The caller's
# deadline bounds the whole lane: a stage that no longer fits is skipped and
# the lane returns what it has.
MIN_STAGE_SECONDS = 2.0
_lane_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "last30days_reddit_lane_deadline", default=None,
)


@contextlib.contextmanager
def lane_deadline(deadline: Optional[float]):
    """Bound search_and_enrich calls in this context by a ``time.monotonic()`` deadline."""
    token = _lane_deadline.set(deadline)
    try:
        yield
    finally:
        _lane_deadline.reset(token)


def _out_of_time(deadline: Optional[float]) -> bool:
    return deadline is not None and deadline - time.monotonic() < MIN_STAGE_SECONDS


# A stage is told to finish this long before the lane stops waiting for it,
# so it has time to assemble and hand back what it fetched. With one shared
# deadline the stage reached it mid-request, and the wait gave up at the same
# moment and put an empty result in place of the threads already fetched.
STAGE_RETURN_MARGIN_SECONDS = 3.0


def _stage_deadline(deadline: Optional[float], name: str = "deadline") -> Dict[str, float]:
    """The keyword that gives a stage its own, earlier deadline; ``{}`` with none."""
    if deadline is None:
        return {}
    return {name: deadline - STAGE_RETURN_MARGIN_SECONDS}


def _within(wait_until: Optional[float], stage: str, default: Any, call, /, *args, **kwargs) -> Any:
    """Run one discovery call and stop waiting for it at the lane deadline.

    The search, listing and score calls wait for their own workers on their
    own timeouts, so a call started with seconds left could run past the
    pipeline's wait for the source and take the threads already found with
    it. Under a deadline the call runs on a worker that is abandoned when the
    deadline passes, and ``default`` stands in for its result. With no
    deadline the call runs inline, as before.
    """
    if wait_until is None:
        return call(*args, **kwargs)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = http.submit_with_context(executor, call, *args, **kwargs)
        try:
            return future.result(timeout=max(0.0, wait_until - time.monotonic()))
        except concurrent.futures.TimeoutError:
            _log(f"{stage} still running at the lane deadline; returning without it")
            return default
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _relevance_rank_key(post: Dict[str, Any]) -> float:
    """Rank by relevance first, with a bounded engagement bonus as tiebreaker.

    Mirrors reddit.py: the log-scaled bonus (capped at 0.25) orders
    similarly-relevant posts by discussion volume but is too small to lift an
    off-topic post (relevance ~0) above an on-topic one.
    """
    eng = post.get("engagement", {})
    total = (eng.get("score", 0) or 0) + (eng.get("num_comments", 0) or 0)
    return (post.get("relevance") or 0.0) + min(0.25, math.log10(max(0, total) + 1) / 20.0)


def _log(msg: str) -> None:
    sys.stderr.write(f"[RedditKeyless] {msg}\n")
    sys.stderr.flush()


def _apply_scores(post: Dict[str, Any], scored: Dict[str, int]) -> None:
    post["score"] = scored["score"]
    post["num_comments"] = scored["num_comments"]
    post.setdefault("engagement", {})["score"] = scored["score"]
    post["engagement"]["num_comments"] = scored["num_comments"]


def _scored_listings(
    subreddits: List[str],
    depth: str = "default",
    query: str = "",
    sorts: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Scored subreddit listings: shreddit partials, arctic-shift supplement.

    The shreddit ``community-more-posts`` partials 403 from datacenter IPs
    (and any host Reddit decides to block). Shreddit is tried first; arctic-
    shift supplements with any posts shreddit missed. Individual sort lanes
    can fail silently (shreddit's ``fetch_listings`` flattens results without
    exposing per-sort status), so arctic is called for all requested subreddits
    and merged via deduplication. This ensures fresh posts sought through
    ``hot`` or ``new`` are recovered even when only ``top`` succeeded. Never
    raises.
    """
    posts = _within(
        deadline, "subreddit listing", [],
        reddit_listing.fetch_listings, subreddits, depth=depth, query=query, sorts=sorts,
        **_stage_deadline(deadline),
    )
    arctic_posts: List[Dict[str, Any]] = []

    # Supplement with arctic for all requested subreddits. Shreddit's per-sort
    # success/failure is opaque, so arctic provides coverage for any failed
    # sort lanes (e.g., hot/new failing while top succeeded). Deduplication
    # ensures no redundant posts when shreddit fully succeeded.
    if subreddits and _out_of_time(deadline):
        _log("arctic-shift supplement skipped: lane budget spent")
    elif subreddits:
        try:
            arctic_posts = _within(
                deadline, "arctic-shift supplement", [],
                reddit_arctic.fetch_listings,
                subreddits, depth=depth, query=query, sorts=sorts,
                from_date=from_date, to_date=to_date,
                **_stage_deadline(deadline, "lane_deadline"),
            )
        except Exception as exc:  # the fallback must never break the pipeline
            _log(f"arctic-shift listing supplement failed: {exc}")
            arctic_posts = []
        if arctic_posts:
            # Merge and dedupe by URL — shreddit posts take priority.
            seen = {p["url"] for p in posts}
            added = 0
            for p in arctic_posts:
                if p["url"] not in seen:
                    seen.add(p["url"])
                    posts.append(p)
                    added += 1
            if added:
                _log(f"arctic-shift supplement: {added} new posts from {len(arctic_posts)} arctic results")
    return posts


def _discover(
    topic: str,
    depth: str,
    subreddits: Optional[List[str]],
    dedicated_subreddits: Optional[List[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    # Dedicated lane: the entity's home subs are wholly on-topic. Pull
    # top+hot+new (real scores from the listing) and mark them floor-exempt so
    # an on-topic post whose title lacks the entity name is never dropped.
    dedicated_posts: List[Dict[str, Any]] = []
    if dedicated_subreddits:
        dedicated_posts = _scored_listings(
            dedicated_subreddits, depth=depth, query=topic, sorts=DEDICATED_SORTS,
            from_date=from_date, to_date=to_date, deadline=deadline,
        )
        for p in dedicated_posts:
            p["dedicated"] = True
        _log(f"Dedicated lane: {len(dedicated_posts)} posts from {dedicated_subreddits}")

    # search.json is permanently 403/429 keyless (no Tier 0). Discovery is
    # Reddit's site search: global, plus per-sub for targeted subreddits. The
    # dedicated subs keep their full-listing lane above and are not searched.
    if _out_of_time(deadline):
        _log("site search skipped: lane budget spent")
        search_posts = []
    else:
        search_posts = _within(
            deadline, "site search", [],
            reddit_search.search,
            topic, depth=depth, subreddits=subreddits, from_date=from_date, to_date=to_date,
            **_stage_deadline(deadline),
        )

    # Targeted run: the caller chose these subreddits, so their listing cards
    # are on-topic; include them as scored discovery. A bare run fetches no
    # listings, so high-upvote off-topic posts cannot flood the results.
    listing_posts = (
        _scored_listings(
            subreddits, depth=depth, query=topic, from_date=from_date, to_date=to_date,
            deadline=deadline,
        )
        if subreddits and not _out_of_time(deadline)
        else []
    )
    _log(
        f"Tier 1 (site search) {len(search_posts)} posts; "
        f"{'listing discovery ' + str(len(listing_posts)) if subreddits else 'no listings'}"
    )

    # Score lookup by post id, from the scored listing cards.
    score_map: Dict[str, Dict[str, int]] = {}
    for p in listing_posts:
        pid = p.get("metadata", {}).get("post_id", "")
        if pid:
            score_map[pid] = {"score": p["score"], "num_comments": p["num_comments"]}

    # Merge: dedicated-sub posts first (floor-exempt), then scored listing posts
    # (targeted only), then search results, taking a listing's live score where
    # the post also appears there. First writer wins the dedupe, so a thread in
    # both the dedicated lane and a listing keeps its floor-exempt status.
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    for p in dedicated_posts + listing_posts:
        if p["url"] not in seen:
            seen.add(p["url"])
            merged.append(p)
    for p in search_posts:
        if p["url"] in seen:
            continue
        pid = reddit_listing._post_id(p["url"])
        if pid in score_map:
            _apply_scores(p, score_map[pid])
        seen.add(p["url"])
        merged.append(p)

    # Backfill posts still at score 0 from the free arctic-shift archive. Posts
    # already scored by search or a listing keep that live score; arctic only
    # fills the gap, and is best-effort (never raises).
    need = [pid for p in merged
            if not (p.get("engagement", {}).get("score"))
            for pid in [reddit_listing._post_id(p["url"])] if pid]
    if need and not _out_of_time(deadline):
        scores = _within(
            deadline, "score lookup", {}, reddit_arctic.fetch_scores, need,
            **_stage_deadline(deadline),
        )
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


def _enrich_one(post: Dict[str, Any], deadline: Optional[float] = None) -> Dict[str, Any]:
    """Attach shreddit comments + real comment count. Never raises."""
    try:
        data = reddit_shreddit.fetch_comments(post.get("url", ""), deadline=deadline)
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


@contextlib.contextmanager
def _enrich_executor(max_workers: int, lane_deadline: Optional[float]):
    """The enrichment pool. Under a lane deadline its exit does not join a
    worker still queued in the shared bucket, so the lane returns on time
    with the comments fetched so far."""
    executor = ThreadPoolExecutor(max_workers=max_workers)
    try:
        yield executor
    finally:
        executor.shutdown(wait=lane_deadline is None, cancel_futures=lane_deadline is not None)


def _enrich(
    posts: List[Dict[str, Any]], depth: str, lane_deadline: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Enrich the top N posts with comments under a total time budget."""
    limit = ENRICH_LIMITS.get(depth, ENRICH_LIMITS["default"])
    to_enrich = posts[:limit]
    rest = posts[limit:]
    if not to_enrich:
        return posts
    if _out_of_time(lane_deadline):
        _log("comment enrichment skipped: lane budget spent")
        return posts

    result_map: Dict[int, Dict[str, Any]] = {}
    # Leaving the executor joins running workers, so the archive fallback
    # must stop by the same budget the wait below enforces.
    budget = ENRICH_BUDGET
    wait_timeout = ENRICH_BUDGET + http.reddit_keyless_wait_allowance(len(to_enrich))
    if lane_deadline is not None:
        lane_left = lane_deadline - time.monotonic()
        budget = min(budget, lane_left)
        wait_timeout = min(wait_timeout, lane_left)
    deadline = time.monotonic() + budget
    try:
        with _enrich_executor(min(limit, MAX_ENRICH_WORKERS), lane_deadline) as executor:
            futures = {
                http.submit_with_context(executor, _enrich_one, post, deadline): i
                for i, post in enumerate(to_enrich)
            }
            # The budget covers the fetches; the allowance covers the shared
            # bucket's queue (other lanes, other entities in compare mode).
            done, not_done = concurrent.futures.wait(futures, timeout=wait_timeout)
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


def _by_comments(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable-sort posts by comment count descending for enrichment slots.

    Ties (equal counts, including unknown counts treated as 0) preserve the
    incoming order, which search_and_enrich's provisional score-first sort
    establishes. Mirrors _relevance_rank_key's `or 0` guard so a present-but-
    None count is treated as 0 rather than raising.
    """
    def _comment_count(post: Dict[str, Any]) -> int:
        eng = post.get("engagement") or {}
        return eng.get("num_comments") or post.get("num_comments") or 0

    return sorted(posts, key=_comment_count, reverse=True)


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
    Within each tier posts are ordered by comment count descending (stable:
    equal or unknown counts preserve the incoming score-first order), so the
    scarce slots go to the threads with the most discussion rather than to
    near-empty threads that merely ranked higher by score. Never raises; on
    any failure the incoming order is returned unchanged.
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
        return _by_comments(matches) + _by_comments(misses)
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
    deadline = _lane_deadline.get()
    posts = _discover(
        topic, depth, subreddits, dedicated_subreddits, from_date=from_date, to_date=to_date,
        **({"deadline": deadline} if deadline is not None else {}),
    )
    if not posts:
        return []

    # Date filter: keep posts in range or with unknown dates (mirrors reddit_public).
    posts = [
        p for p in posts
        if p.get("date") is None or (from_date <= p["date"] <= to_date)
    ]

    # Relevance floor: strip zero-overlap posts (relevance exactly 0 = no
    # title/body token match at all) when anything relevant remains, so
    # high-upvote listing posts can't bury on-topic search
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
    # within-tier tiebreak order to preserve: within each entity tier, slots go
    # to the most-commented threads first, and equal counts keep score order.
    posts.sort(
        key=lambda p: (
            p.get("engagement", {}).get("score", 0) or 0,
            p.get("relevance", 0) or 0,
            p.get("date") or "",
        ),
        reverse=True,
    )

    # Enrichment slot selection is comment-aware within entity tiers:
    # entity-matching posts claim the scarce comment slots first, and within
    # each tier the most-commented threads get slots first (score order is the
    # stable tiebreak for equal counts).
    posts = _enrich(
        _slot_priority(topic, posts), depth,
        **({"lane_deadline": deadline} if deadline is not None else {}),
    )

    # Final display order ranks relevance-first with a bounded engagement bonus,
    # so an off-topic high-upvote post can't outrank an on-topic one in what the
    # user sees. Enrichment above may have backfilled real comment counts.
    posts.sort(key=_relevance_rank_key, reverse=True)

    for i, post in enumerate(posts):
        post["id"] = f"R{i + 1}"

    return posts


# Comments for the threads the report keeps. Lane-time enrichment above picks
# its slots before the pipeline's own relevance prune and ranking, so most of
# the threads that survive arrived without comments (3 to 5 of 12 in the
# 2026-11-10 runs), and a thread's top comment is usually its best quote.
# This pass runs from ``_finalize_items_by_source`` on the survivors, highest
# ranked first. Threads the lane already enriched cost nothing, and repeat
# URLs are served from the run memo.
FINAL_ENRICH_LIMITS = {"quick": 6, "default": 12, "deep": 16}
FINAL_ENRICH_BUDGET = 30  # seconds, at the shared 1 req/s pacing


def enrich_source_items(
    items: List[Any],
    depth: str = "default",
    deadline: Optional[float] = None,
) -> List[Any]:
    """Attach top comments to the kept Reddit SourceItems that lack them.

    Considers the first ``FINAL_ENRICH_LIMITS[depth]`` items (they arrive
    ranked), skips threads that have comments already or report none, and
    fetches the rest through the same paced, memoized comment fetch as the
    lane, under ``FINAL_ENRICH_BUDGET`` and the caller's ``deadline``. Each
    comment carries author, score and its own permalink. Never raises and
    never waits for a straggler: a thread whose fetch did not finish stays as
    it was.
    """
    limit = FINAL_ENRICH_LIMITS.get(depth, FINAL_ENRICH_LIMITS["default"])
    kept = [item for item in items if getattr(item, "source", None) == "reddit"][:limit]
    targets = [
        item for item in kept
        if item.url
        and not (item.metadata or {}).get("top_comments")
        and (item.engagement or {}).get("num_comments") != 0
    ]
    if not targets:
        return items
    budget = float(FINAL_ENRICH_BUDGET)
    if deadline is not None:
        budget = min(budget, deadline - time.monotonic())
    if budget < MIN_STAGE_SECONDS:
        _log("comments for kept threads skipped: no time left")
        return items
    end = time.monotonic() + budget
    executor = ThreadPoolExecutor(max_workers=min(len(targets), MAX_ENRICH_WORKERS))
    try:
        futures = {
            http.submit_with_context(executor, reddit_shreddit.fetch_comments, item.url, deadline=end): item
            for item in targets
        }
        done, _not_done = concurrent.futures.wait(futures, timeout=budget)
        for future in done:
            item = futures[future]
            try:
                data = future.result(timeout=0) or {}
            except Exception:
                continue
            if data.get("top_comments"):
                item.metadata["top_comments"] = data["top_comments"]
                item.metadata["comment_insights"] = data.get("comment_insights") or []
            if data.get("num_comments") is not None:
                item.engagement["num_comments"] = data["num_comments"]
    except Exception as exc:
        _log(f"comments for kept threads failed: {exc}")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    have = sum(1 for item in kept if (item.metadata or {}).get("top_comments"))
    _log(f"comments on {have} of {len(kept)} kept threads ({len(targets)} fetched after ranking)")
    return items
