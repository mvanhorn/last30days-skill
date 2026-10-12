"""Reusable local scoring signals for v3 pipeline stages."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

from . import dates, relevance, schema

# Editorial signal-to-noise scores. Grounding (Google Search) is 1.0 baseline;
# social platforms discounted for noise.
SOURCE_QUALITY = {
    "xiaohongshu": 0.7,
    "hackernews": 0.8,
    "devto": 0.78,
    "youtube": 0.85,
    "digg": 0.85,
    "arxiv": 0.9,
    "techmeme": 0.85,
    "trustpilot": 0.78,
    # Verified-purchase reviews on a live aggregate rating: high-quality
    # buyer evidence, a notch above Trustpilot's open review model.
    "amazon": 0.8,
    # Paid creative is first-party marketing copy: a reliable statement of
    # what the brand is pushing, but not independent evidence about it, so it
    # sits below the review-backed sources and above open social.
    "meta_ads": 0.72,
    "reddit": 0.6,
    "x": 0.68,
    "bluesky": 0.66,
    "truthsocial": 0.6,
    "polymarket": 0.5,
    "instagram": 0.58,
    "tiktok": 0.58,
    "jobs": 0.72,
    "corpus": 0.75,
}


def source_quality(source: str) -> float:
    return SOURCE_QUALITY.get(source, 0.6)


def local_relevance(
    item: schema.SourceItem,
    ranking_query: "str | relevance.PreparedQuery",
) -> float:
    text = "\n".join(
        part
        for part in [item.title, item.body, item.snippet]
        if part
    )
    hashtags = item.metadata.get("hashtags") if isinstance(item.metadata, dict) else None
    score = relevance.token_overlap_relevance(ranking_query, text, hashtags=hashtags)

    # High-engagement YouTube floor: official videos with millions of views
    # often have titles that don't keyword-match the query (e.g., "YE - FATHER
    # (feat. TRAVIS SCOTT)" doesn't match "kanye west"). The engagement signals
    # say "this is important" even when text overlap is weak.
    if item.source == "youtube" and (item.engagement.get("views") or 0) > 100_000:
        score = max(score, 0.3)

    # Project-mode GitHub floor: items fetched via --github-repo are explicitly
    # requested by the user and relevant by construction. Without this floor,
    # repos with low token diversity (e.g., "openclaw/openclaw" -> 1 unique token)
    # get pruned despite being the primary search target.
    labels = item.metadata.get("labels", []) if isinstance(item.metadata, dict) else []
    if "project-mode" in labels:
        score = max(score, 0.8)

    # Grounding-exempt floor (currently Amazon): the adapter already gated
    # these against the model-supplied product keyword before creating them,
    # so they are relevant by construction. Their text is marketing copy plus
    # buyer reviews, which rarely repeats the topic phrasing -- a "Weber
    # Grills" run surfaces a product named "Spirit E-325" whose reviews talk
    # about searing, not about Weber. Without the floor, correctly-retrieved
    # evidence gets pruned for failing a keyword match it was never going to
    # win. Mirrors the project-mode GitHub floor above.
    if isinstance(item.metadata, dict) and item.metadata.get("grounding_exempt"):
        score = max(score, 0.8)

    return score


def freshness(
    item: schema.SourceItem,
    freshness_mode: str = "balanced_recent",
    *,
    reference_date: str | None = None,
    max_days: int = 30,
) -> int:
    # A live repo snapshot dated to the window end (no in-window release) has a
    # read time, not an event date: rank it at the scale's midpoint instead of
    # maximum freshness, or every pinned repo card sorts to #1.
    if isinstance(item.metadata, dict) and item.metadata.get("date_is_snapshot"):
        return 50
    score = dates.recency_score(
        item.published_at,
        max_days=max_days,
        reference_date=reference_date,
    )
    if freshness_mode == "strict_recent":
        return int(score)
    if freshness_mode == "evergreen_ok":
        return int((score * 0.6) + 40)
    return int((score * 0.8) + 10)


def log1p_safe(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    if numeric <= 0:
        return 0.0
    return math.log1p(numeric)


def _top_comment_score(item: schema.SourceItem) -> float:
    comments = item.metadata.get("top_comments") or []
    if not comments or not isinstance(comments[0], dict):
        return 0.0
    return log1p_safe(comments[0].get("score"))


# Per-platform log-reference for normalizing a top comment's vote count into a
# [0,1] signal. Reddit upvotes run in the hundreds-to-thousands; YouTube/TikTok
# likes run 10-600x higher (and the top end is display-abbreviated: "39K" is
# stored as 39000). A raw or single-scale log compare would let YouTube/TikTok
# dominate purely by platform scale, not by being funnier. Each value is the
# log1p of a "very high" top-comment count for that platform, so dividing a
# comment's log1p(score) by it yields a comparable cross-platform strength.
_VOTE_LOG_REFERENCE: dict[str, float] = {
    "reddit":     7.6,   # ~log1p(2000)
    "hackernews": 6.2,   # ~log1p(500)
    "devto":      3.0,   # ~log1p(20) direct replies; the API has no comment likes
    "youtube":    10.3,  # ~log1p(30000)
    "tiktok":     10.3,  # ~log1p(30000)
    "instagram":  9.2,   # ~log1p(10000)
    "x":          9.2,   # ~log1p(10000)
    "bluesky":    9.2,   # ~log1p(10000); like X/IG, not the Reddit default
}
_VOTE_LOG_REFERENCE_DEFAULT = 7.6


def normalized_comment_vote(source: str, score: "float | int | None") -> float:
    """Normalize a single comment's vote count to [0,1] within its platform.

    Same per-platform reference as ``top_comment_vote_signal`` so a 22k-like
    TikTok comment and a 600-upvote Reddit comment rank on a comparable scale.
    Used to rank the cross-candidate Top Community Comments block.
    """
    base = log1p_safe(score)
    if base <= 0.0:
        return 0.0
    ref = _VOTE_LOG_REFERENCE.get(source, _VOTE_LOG_REFERENCE_DEFAULT)
    return max(0.0, min(1.0, base / ref))


def top_comment_vote_signal(candidate: schema.Candidate) -> float:
    """Strength of a candidate's most-upvoted top comment, as [0,1].

    Normalized *within the candidate's platform* (see ``_VOTE_LOG_REFERENCE``)
    so a 22k-like TikTok comment and a 600-upvote Reddit comment land on a
    comparable scale rather than letting raw counts dominate. Returns 0.0 when
    no top comment carries votes. Used by the fun judge to amplify (never
    drive) crowd-certified comments.
    """
    best_log = 0.0
    for item in candidate.source_items:
        comments = item.metadata.get("top_comments") or []
        for comment in comments[:3]:
            if isinstance(comment, dict):
                best_log = max(best_log, log1p_safe(comment.get("score")))
    if best_log <= 0.0:
        return 0.0
    ref = _VOTE_LOG_REFERENCE.get(candidate.source, _VOTE_LOG_REFERENCE_DEFAULT)
    return max(0.0, min(1.0, best_log / ref))


# Per-source engagement weights: list of (field_name, weight) tuples.
# Reddit, YouTube, and TikTok use custom functions because they include
# a dedicated 10% top-comment-score slot (see _reddit_engagement,
# _youtube_engagement, _tiktok_engagement).
ENGAGEMENT_WEIGHTS: dict[str, list[tuple[str, float]]] = {
    "x":            [("likes", 0.55), ("reposts", 0.25), ("replies", 0.15), ("quotes", 0.05)],
    "instagram":    [("views", 0.50), ("likes", 0.30), ("comments", 0.20)],
    "hackernews":   [("points", 0.55), ("comments", 0.45)],
    "devto":        [("reactions", 0.60), ("comments", 0.40)],
    "bluesky":      [("likes", 0.40), ("reposts", 0.30), ("replies", 0.20), ("quotes", 0.10)],
    "truthsocial":  [("likes", 0.45), ("reposts", 0.30), ("replies", 0.25)],
    "polymarket":   [("volume", 0.60), ("liquidity", 0.40)],
    "digg":         [("postCount", 0.40), ("uniqueAuthors", 0.30), ("rank_score", 0.30)],
    "trustpilot":   [("reviews", 1.0)],
    "amazon":       [("ratings", 1.0)],
    # Meta publishes reach and spend only for political and issue ads, so a
    # commercial creative has no audience signal at all. How many variants a
    # brand cut of one creative is the closest honest proxy for how hard it is
    # pushing that message.
    "meta_ads":     [("variants", 1.0)],
}


def _weighted_engagement(item: schema.SourceItem, weights: list[tuple[str, float]]) -> float | None:
    values = [(log1p_safe(item.engagement.get(field)), weight) for field, weight in weights]
    if not any(v for v, _ in values):
        return None
    return sum(v * w for v, w in values)


def _reddit_engagement(item: schema.SourceItem) -> float | None:
    score = log1p_safe(item.engagement.get("score"))
    comments = log1p_safe(item.engagement.get("num_comments"))
    ratio = float(item.engagement.get("upvote_ratio") or 0.0)
    top_comment = _top_comment_score(item)
    if not any([score, comments, ratio, top_comment]):
        return None
    return (0.50 * score) + (0.35 * comments) + (0.05 * (ratio * 10.0)) + (0.10 * top_comment)


def _youtube_engagement(item: schema.SourceItem) -> float | None:
    views = log1p_safe(item.engagement.get("views"))
    likes = log1p_safe(item.engagement.get("likes"))
    comments = log1p_safe(item.engagement.get("comments"))
    top_comment = _top_comment_score(item)
    if not any([views, likes, comments, top_comment]):
        return None
    # Mirrors Reddit: carve out 10% for top-comment signal, keep view-weight
    # dominant. Without comments, the pre-change weights (0.50/0.35/0.15)
    # still govern relative ordering.
    return (0.45 * views) + (0.32 * likes) + (0.13 * comments) + (0.10 * top_comment)


def _tiktok_engagement(item: schema.SourceItem) -> float | None:
    views = log1p_safe(item.engagement.get("views"))
    likes = log1p_safe(item.engagement.get("likes"))
    comments = log1p_safe(item.engagement.get("comments"))
    top_comment = _top_comment_score(item)
    if not any([views, likes, comments, top_comment]):
        return None
    return (0.45 * views) + (0.27 * likes) + (0.18 * comments) + (0.10 * top_comment)


def _instagram_engagement(item: schema.SourceItem) -> float | None:
    # Mirrors _tiktok_engagement: reels are video-shaped, and a highly-liked top
    # comment carves out 10% of the signal (via comment_like_count -> score) so
    # crowd-loved IG comments lift their post's ranking like YouTube/TikTok.
    views = log1p_safe(item.engagement.get("views"))
    likes = log1p_safe(item.engagement.get("likes"))
    comments = log1p_safe(item.engagement.get("comments"))
    top_comment = _top_comment_score(item)
    if not any([views, likes, comments, top_comment]):
        return None
    return (0.45 * views) + (0.27 * likes) + (0.18 * comments) + (0.10 * top_comment)


def _generic_engagement(item: schema.SourceItem) -> float | None:
    if not item.engagement:
        return None
    values = [logged for v in item.engagement.values() if (logged := log1p_safe(v)) > 0]
    if not values:
        return None
    return sum(values) / len(values)


def engagement_raw(item: schema.SourceItem) -> float | None:
    if item.source == "reddit":
        return _reddit_engagement(item)
    if item.source == "youtube":
        return _youtube_engagement(item)
    if item.source == "tiktok":
        return _tiktok_engagement(item)
    if item.source == "instagram":
        return _instagram_engagement(item)
    weights = ENGAGEMENT_WEIGHTS.get(item.source)
    if weights:
        return _weighted_engagement(item, weights)
    return _generic_engagement(item)


# Per-source log reference for engagement_score, in each source's own units.
# Same convention as _VOTE_LOG_REFERENCE (which it extends): the value is
# engagement_raw for a "very high" item, so that item scores 100 and, the scale
# being logarithmic, roughly its square root scores 50. Every source that can
# reach engagement_raw is listed here or in _NO_ENGAGEMENT_SCORE_SOURCES; an
# unlisted source gets no score rather than an assumed Reddit-vote scale.
_ENGAGEMENT_LOG_REFERENCE: dict[str, float] = {
    **_VOTE_LOG_REFERENCE,
    # Microblog likes/reposts/replies run on X's scale (~10k likes is very high).
    "truthsocial": 9.2,  # ~log1p(10000)
    "threads":     9.2,  # ~log1p(10000)
    # Likes/comments/favorites on an Instagram-like feed.
    "xiaohongshu": 9.2,  # ~log1p(10000)
    # Saves on a viral pin reach ~10k.
    "pinterest":   9.2,  # ~log1p(10000)
    # Reactions run like Reddit upvotes: a viral post has ~2k.
    "linkedin":    7.6,  # ~log1p(2000)
    # Per-issue/PR reactions and comments (repo snapshot cards, whose counts
    # are lifetime stars, get no score: see is_repo_snapshot). A
    # ~100-reaction thread lands near 50.
    "github":      9.2,  # ~log1p(10000)
    # Dollars, not votes. polymarket.py's own saturation anchors: $9M volume
    # (log1p 16) and $1.2M liquidity (log1p 14), weighted 0.6/0.4 as in
    # ENGAGEMENT_WEIGHTS: 0.6*16 + 0.4*14 = 15.2.
    "polymarket": 15.2,
    # A rank-1 cluster (rank_score 50) with ~100 posts from ~100 authors:
    # 0.4*log1p(100) + 0.3*log1p(100) + 0.3*log1p(50) = 4.4.
    "digg":        4.4,
    # Lifetime totals for a whole company or product, not one post: big
    # consumer brands and best-sellers carry ~100k, so ~300 lands near 50.
    "trustpilot": 11.5,  # ~log1p(100000) reviews
    "amazon":     11.5,  # ~log1p(100000) ratings
}

# Sources whose engagement fields are not an audience response, so no
# engagement score is comparable with votes or likes:
#   jobs       - a constant open_roles=1 per posting
#   meta_ads   - variant count measures the advertiser's push, not reach
#   grounding, perplexity - web results; counts are citations, not readers
#   stocktwits - the author's follower count dominates sparse likes/reshares
#   telegram   - the channel's subscriber count dominates per-post views
#   arxiv, techmeme, dripstack, corpus - no engagement fields at all
_NO_ENGAGEMENT_SCORE_SOURCES = frozenset({
    "jobs", "meta_ads", "grounding", "perplexity", "stocktwits", "telegram",
    "arxiv", "techmeme", "dripstack", "corpus",
})


# YouTube, TikTok, and Instagram weight log1p(views) at 0.45 in
# engagement_raw, and views run orders of magnitude above votes: any upload
# collects ~100 passive feed views, so a 117-view, 1-like video already had raw
# 2.4 (23 on the vote-style 10.3 reference, three times a 3-like X post).
# These sources map raw linearly from a floor (an upload with ~100 views and no
# reactions: 0.45*log1p(100) = 2.1) to a "very high" month-window post (~2-3M
# views, ~100k likes, ~3k comments: 0.45*14.9 + 0.32*11.5 + 0.13*8.0 = 11.4).
# Resulting scores: 117 views/1 like ~3, 655 views/17 likes ~17, 133k views/
# 8.4k likes ~70, 726k views/13k likes ~85, 1M+ views ~90-100.
_VIEW_ENGAGEMENT_SOURCES = frozenset({"youtube", "tiktok", "instagram"})
_VIEW_ENGAGEMENT_LOG_FLOOR = 2.1
_VIEW_ENGAGEMENT_LOG_REFERENCE = 11.5


def is_repo_snapshot(item: schema.SourceItem) -> bool:
    """True for a GitHub repo card (project mode, person-mode repo items).

    Their engagement is lifetime stars and the open-issue count, a snapshot
    of the repo's whole history rather than an audience response inside the
    window. Issues and PRs carry reactions/comments, never stars.
    """
    return (
        item.source == "github"
        and "stars" in (item.engagement or {})
        and not (item.metadata or {}).get("is_pr")
    )


def engagement_score(item: schema.SourceItem) -> int | None:
    """Engagement on an absolute 0-100 scale, comparable across streams.

    ``engagement_raw`` is a weighted mean of log1p counts, so dividing it by
    the source's log reference (``_ENGAGEMENT_LOG_REFERENCE``, in that
    source's own units) places a 250-upvote Reddit thread near 50 and a 3-like
    X post near 8 no matter what else shares their stream. The per-stream
    min-max rescale this replaces mapped every stream's best item to 100 and
    a lone item to 50, so the score said nothing about actual engagement.
    View-weighted sources (YouTube, TikTok, Instagram) use the view scale
    (``_VIEW_ENGAGEMENT_LOG_FLOOR`` to ``_VIEW_ENGAGEMENT_LOG_REFERENCE``)
    instead of their vote reference. Sources without a comparable audience
    signal, and GitHub repo snapshots (lifetime stars), return None.
    """
    if item.source in _NO_ENGAGEMENT_SCORE_SOURCES or is_repo_snapshot(item):
        return None
    ref = _ENGAGEMENT_LOG_REFERENCE.get(item.source)
    if ref is None:
        return None
    raw = engagement_raw(item)
    if raw is None:
        return None
    if item.source in _VIEW_ENGAGEMENT_SOURCES:
        floor = _VIEW_ENGAGEMENT_LOG_FLOOR
        fraction = (raw - floor) / (_VIEW_ENGAGEMENT_LOG_REFERENCE - floor)
    else:
        fraction = raw / ref
    return int(round(100.0 * max(0.0, min(1.0, fraction))))


def normalize(values: list[float | None]) -> list[int | None]:
    valid = [value for value in values if value is not None]
    if not valid:
        return [None for _ in values]
    low = min(valid)
    high = max(valid)
    if math.isclose(low, high):
        return [50 if value is not None else None for value in values]
    return [
        None
        if value is None
        else int(((value - low) / (high - low)) * 100)
        for value in values
    ]


def annotate_stream(
    items: list[schema.SourceItem],
    ranking_query: "str | relevance.PreparedQuery",
    freshness_mode: str,
    reference_date: str | None = None,
    max_days: int = 30,
) -> list[schema.SourceItem]:
    """Attach local scoring metadata and return items sorted by local_rank_score."""
    prepared_query = ranking_query if isinstance(ranking_query, relevance.PreparedQuery) else relevance.PreparedQuery(ranking_query)
    for item in items:
        eng_score = engagement_score(item)
        item.local_relevance = local_relevance(item, prepared_query)
        item.freshness = freshness(
            item,
            freshness_mode,
            reference_date=reference_date,
            max_days=max_days,
        )
        item.engagement_score = eng_score
        item.source_quality = source_quality(item.source)
        item.local_rank_score = (
            0.65 * item.local_relevance
            + 0.25 * (item.freshness / 100.0)
            + 0.10 * ((eng_score or 0) / 100.0)
        )
    return sorted(items, key=lambda item: item.local_rank_score or 0, reverse=True)


_SOCIAL_SOURCES = {"reddit", "x", "tiktok", "instagram", "bluesky", "truthsocial"}

# Minimum view count for short-video platforms. Items below this floor
# are typically spam reposts or low-effort clips that add no unique signal.
_VIDEO_ENGAGEMENT_FLOOR_SOURCES = {"tiktok", "instagram"}
_VIDEO_ENGAGEMENT_FLOOR_VIEWS = 1000


def _passes_engagement_floor(item: schema.SourceItem, sole_source: bool) -> bool:
    """Check whether a TikTok/Instagram item meets the minimum view floor.

    Items from sources not in _VIDEO_ENGAGEMENT_FLOOR_SOURCES always pass.
    If the item's source is the *only* source represented in the batch
    (sole_source=True), all items pass so we never return an empty result
    for a whole source.
    """
    if item.source not in _VIDEO_ENGAGEMENT_FLOOR_SOURCES:
        return True
    if sole_source:
        return True
    views = item.engagement.get("views") or 0 if item.engagement else 0
    return views >= _VIDEO_ENGAGEMENT_FLOOR_VIEWS


def prune_low_relevance(
    items: list[schema.SourceItem],
    minimum: float = 0.15,
    first_party_handles: Iterable[str] | None = None,
    first_party_by_source: Mapping[str, Iterable[str]] | None = None,
) -> list[schema.SourceItem]:
    """Drop weak lexical matches when stronger evidence exists.

    Social-source items with genuinely zero engagement get a stricter
    threshold because zero engagement on a social platform is a strong noise
    signal.

    TikTok and Instagram items with fewer than 1000 views are pruned
    (unless they are the only source represented in the batch).

    ``first_party_handles`` names accounts this run is explicitly searching
    (the subject of the topic). Their own posts are exempt from the floor: a
    post almost never contains its own author's name, so lexical relevance
    scores it at or near zero no matter how on-topic it is. Without the
    exemption a mixed batch loses them silently, because the ``filtered or
    items`` rescue below only fires when *every* item fails.

    ``first_party_by_source`` scopes exemptions to one platform each (e.g.
    ``--ig-creators`` names Instagram accounts). A creator flag must not
    exempt a same-name account on a *different* platform: username reuse
    across platforms is common, and an unrelated account would otherwise
    bypass the relevance and engagement floors.
    """
    sources_present = {item.source for item in items}
    first_party = {
        h.strip().lstrip("@").lower()
        for h in (first_party_handles or ())
        if h and h.strip()
    }
    scoped_first_party = {
        source: {
            h.strip().lstrip("@").lower()
            for h in handles
            if h and h.strip()
        }
        for source, handles in (first_party_by_source or {}).items()
    }

    def _is_first_party(item: schema.SourceItem) -> bool:
        if not item.author:
            return False
        author = item.author.strip().lstrip("@").lower()
        if author in first_party:
            return True
        return author in scoped_first_party.get(item.source, frozenset())

    def passes(item: schema.SourceItem) -> bool:
        # YouTube items with successfully extracted transcripts should not
        # be pruned by title-only relevance scoring — the transcript content
        # already proves substantive topical coverage.
        if item.source == "youtube" and item.snippet:
            return True
        # Posts by an account this run is explicitly searching are evidence by
        # provenance, not by lexical overlap with the topic.
        if _is_first_party(item):
            return True
        rel = item.local_relevance if item.local_relevance is not None else 0.0
        if rel < minimum:
            return False
        # Key the stricter social gate on genuinely absent engagement (no
        # counts at all), not on a rounded score: a handful of likes is still
        # engagement even where the 0-100 scale rounds it low.
        if item.source in _SOCIAL_SOURCES and not engagement_raw(item):
            if rel < minimum * 1.5:
                return False
        sole_source = sources_present == {item.source}
        if not _passes_engagement_floor(item, sole_source):
            return False
        return True

    filtered = [item for item in items if passes(item)]
    return filtered or items
