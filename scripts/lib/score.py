"""Popularity-aware scoring for last30days skill."""

import math
from typing import List, Optional, Union

from . import dates, schema
from .query_type import QueryType, WEBSEARCH_PENALTY_BY_TYPE, TIEBREAKER_BY_TYPE

# Score weights for Reddit/X (has engagement)
WEIGHT_RELEVANCE = 0.45
WEIGHT_RECENCY = 0.25
WEIGHT_ENGAGEMENT = 0.30

# Polymarket needs stronger semantic weighting because volume/liquidity already
# show up as engagement and lightly influence parse-time relevance.
PM_WEIGHT_RELEVANCE = 0.60
PM_WEIGHT_RECENCY = 0.20
PM_WEIGHT_ENGAGEMENT = 0.20

# WebSearch weights (no engagement data available)
WEBSEARCH_WEIGHT_RELEVANCE = 0.55
WEBSEARCH_WEIGHT_RECENCY = 0.45
# Default web search penalty (fallback when query_type is not provided).
# Per-type penalties in query_type.WEBSEARCH_PENALTY_BY_TYPE.
WEBSEARCH_SOURCE_PENALTY = 15

# WebSearch date confidence adjustments
WEBSEARCH_VERIFIED_BONUS = 10   # Bonus for URL-verified recent date (high confidence)
WEBSEARCH_NO_DATE_PENALTY = 20  # Heavy penalty for no date signals (low confidence)

# Default engagement score for unknown
DEFAULT_ENGAGEMENT = 35
UNKNOWN_ENGAGEMENT_PENALTY = 3


def log1p_safe(x: Optional[int]) -> float:
    """Safe log1p that handles None and negative values."""
    if x is None or x < 0:
        return 0.0
    return math.log1p(x)


def compute_reddit_engagement_raw(
    engagement: Optional[schema.Engagement],
    top_comment_score: Optional[int] = None,
) -> Optional[float]:
    """Compute raw engagement score for Reddit item.

    Formula: 0.50*log1p(score) + 0.35*log1p(num_comments) + 0.05*(upvote_ratio*10) + 0.10*log1p(top_comment_score)

    The 10% comment quality weight rewards posts where the community engaged deeply
    — a highly upvoted top comment means the thread sparked real discussion.
    """
    if engagement is None:
        return None

    if engagement.score is None and engagement.num_comments is None:
        return None

    score = log1p_safe(engagement.score)
    comments = log1p_safe(engagement.num_comments)
    ratio = (engagement.upvote_ratio or 0.5) * 10
    top_cmt = log1p_safe(top_comment_score)

    return 0.50 * score + 0.35 * comments + 0.05 * ratio + 0.10 * top_cmt


def compute_x_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for X item.

    Formula: 0.55*log1p(likes) + 0.25*log1p(reposts) + 0.15*log1p(replies) + 0.05*log1p(quotes)
    """
    if engagement is None:
        return None

    if engagement.likes is None and engagement.reposts is None:
        return None

    likes = log1p_safe(engagement.likes)
    reposts = log1p_safe(engagement.reposts)
    replies = log1p_safe(engagement.replies)
    quotes = log1p_safe(engagement.quotes)

    return 0.55 * likes + 0.25 * reposts + 0.15 * replies + 0.05 * quotes


def normalize_to_100(values: List[float], default: float = 50) -> List[float]:
    """Normalize a list of values to 0-100 scale.

    Args:
        values: Raw values (None values are preserved)
        default: Default value for None entries

    Returns:
        Normalized values
    """
    # Filter out None
    valid = [v for v in values if v is not None]
    if not valid:
        return [default if v is None else 50 for v in values]

    min_val = min(valid)
    max_val = max(valid)
    range_val = max_val - min_val

    if range_val == 0:
        return [50 if v is None else 50 for v in values]

    result = []
    for v in values:
        if v is None:
            result.append(None)
        else:
            normalized = ((v - min_val) / range_val) * 100
            result.append(normalized)

    return result


def score_reddit_items(items: List[schema.RedditItem]) -> List[schema.RedditItem]:
    """Compute scores for Reddit items.

    Args:
        items: List of Reddit items

    Returns:
        Items with updated scores
    """
    if not items:
        return items

    # Compute raw engagement scores (with top comment quality signal)
    eng_raw = []
    for item in items:
        top_cmt_score = None
        if item.top_comments:
            top_cmt_score = item.top_comments[0].score
        eng_raw.append(compute_reddit_engagement_raw(item.engagement, top_cmt_score))

    # Normalize engagement to 0-100
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        # Relevance subscore (model-provided, convert to 0-100)
        rel_score = int(item.relevance * 100)

        # Recency subscore
        rec_score = dates.recency_score(item.date)

        # Engagement subscore
        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        # Store subscores
        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        # Compute overall score
        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        # Apply penalty for unknown engagement
        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        # Apply penalty for low date confidence
        if item.date_confidence == "low":
            overall -= 5
        elif item.date_confidence == "med":
            overall -= 2

        item.score = max(0, min(100, int(overall)))

    return items


def score_x_items(items: List[schema.XItem]) -> List[schema.XItem]:
    """Compute scores for X items.

    Args:
        items: List of X items

    Returns:
        Items with updated scores
    """
    if not items:
        return items

    # Compute raw engagement scores
    eng_raw = [compute_x_engagement_raw(item.engagement) for item in items]

    # Normalize engagement to 0-100
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        # Relevance subscore (model-provided, convert to 0-100)
        rel_score = int(item.relevance * 100)

        # Recency subscore
        rec_score = dates.recency_score(item.date)

        # Engagement subscore
        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        # Store subscores
        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        # Compute overall score
        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        # Apply penalty for unknown engagement
        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        # Apply penalty for low date confidence
        if item.date_confidence == "low":
            overall -= 5
        elif item.date_confidence == "med":
            overall -= 2

        item.score = max(0, min(100, int(overall)))

    return items


def compute_youtube_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for YouTube item.

    Formula: 0.50*log1p(views) + 0.35*log1p(likes) + 0.15*log1p(comments)
    Views dominate on YouTube — they're the primary discovery signal.
    """
    if engagement is None:
        return None

    if engagement.views is None and engagement.likes is None:
        return None

    views = log1p_safe(engagement.views)
    likes = log1p_safe(engagement.likes)
    comments = log1p_safe(engagement.num_comments)

    return 0.50 * views + 0.35 * likes + 0.15 * comments


def score_youtube_items(items: List[schema.YouTubeItem]) -> List[schema.YouTubeItem]:
    """Compute scores for YouTube items.

    Uses same weight structure as Reddit/X (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_youtube_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_tiktok_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for TikTok item.

    Formula: 0.50*log1p(views) + 0.30*log1p(likes) + 0.20*log1p(comments)
    Views dominate on TikTok — they're the primary discovery signal.
    """
    if engagement is None:
        return None

    if engagement.views is None and engagement.likes is None:
        return None

    views = log1p_safe(engagement.views)
    likes = log1p_safe(engagement.likes)
    comments = log1p_safe(engagement.num_comments)

    return 0.50 * views + 0.30 * likes + 0.20 * comments


def score_tiktok_items(items: List[schema.TikTokItem]) -> List[schema.TikTokItem]:
    """Compute scores for TikTok items.

    Uses same weight structure as YouTube (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_tiktok_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_instagram_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Instagram item.

    Formula: 0.50*log1p(views) + 0.30*log1p(likes) + 0.20*log1p(comments)
    Views dominate on Instagram Reels — they're the primary discovery signal.
    """
    if engagement is None:
        return None

    if engagement.views is None and engagement.likes is None:
        return None

    views = log1p_safe(engagement.views)
    likes = log1p_safe(engagement.likes)
    comments = log1p_safe(engagement.num_comments)

    return 0.50 * views + 0.30 * likes + 0.20 * comments


def score_instagram_items(items: List[schema.InstagramItem]) -> List[schema.InstagramItem]:
    """Compute scores for Instagram items.

    Uses same weight structure as TikTok (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_instagram_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_hackernews_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Hacker News item.

    Formula: 0.55*log1p(points) + 0.45*log1p(num_comments)
    Points are the primary signal on HN; comments indicate depth of discussion.
    """
    if engagement is None:
        return None

    if engagement.score is None and engagement.num_comments is None:
        return None

    points = log1p_safe(engagement.score)
    comments = log1p_safe(engagement.num_comments)

    return 0.55 * points + 0.45 * comments


def score_hackernews_items(items: List[schema.HackerNewsItem]) -> List[schema.HackerNewsItem]:
    """Compute scores for Hacker News items.

    Uses same weight structure as Reddit/X (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_hackernews_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_github_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for GitHub item.

    Formula: 0.45*log1p(reactions) + 0.55*log1p(num_comments)
    Comments indicate depth of discussion; reactions indicate broad interest.
    """
    if engagement is None:
        return None

    if engagement.score is None and engagement.num_comments is None:
        return None

    reactions = log1p_safe(engagement.score)
    comments = log1p_safe(engagement.num_comments)

    return 0.45 * reactions + 0.55 * comments


def score_github_items(items: List[schema.GitHubItem]) -> List[schema.GitHubItem]:
    """Compute scores for GitHub items."""
    if not items:
        return items

    eng_raw = [compute_github_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_bluesky_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Bluesky item.

    Formula: 0.40*log1p(likes) + 0.30*log1p(reposts) + 0.20*log1p(replies) + 0.10*log1p(quotes)
    Likes are primary signal; reposts indicate reach; replies indicate discussion depth.
    """
    if engagement is None:
        return None

    if engagement.likes is None and engagement.reposts is None:
        return None

    likes = log1p_safe(engagement.likes)
    reposts = log1p_safe(engagement.reposts)
    replies = log1p_safe(engagement.replies)
    quotes = log1p_safe(engagement.quotes)

    return 0.40 * likes + 0.30 * reposts + 0.20 * replies + 0.10 * quotes


def score_bluesky_items(items: List[schema.BlueskyItem]) -> List[schema.BlueskyItem]:
    """Compute scores for Bluesky items.

    Uses same weight structure as Reddit/X (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_bluesky_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_truthsocial_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Truth Social item.

    Formula: 0.45*log1p(likes) + 0.30*log1p(reposts) + 0.25*log1p(replies)
    Likes are primary signal; reposts indicate reach; replies indicate discussion.
    """
    if engagement is None:
        return None

    if engagement.likes is None and engagement.reposts is None:
        return None

    likes = log1p_safe(engagement.likes)
    reposts = log1p_safe(engagement.reposts)
    replies = log1p_safe(engagement.replies)

    return 0.45 * likes + 0.30 * reposts + 0.25 * replies


def score_truthsocial_items(items: List[schema.TruthSocialItem]) -> List[schema.TruthSocialItem]:
    """Compute scores for Truth Social items."""
    if not items:
        return items

    eng_raw = [compute_truthsocial_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            WEIGHT_RELEVANCE * rel_score +
            WEIGHT_RECENCY * rec_score +
            WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def compute_polymarket_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Polymarket item.

    Formula: 0.60*log1p(volume) + 0.40*log1p(liquidity)
    Volume is the primary signal (money flowing); liquidity indicates market depth.
    """
    if engagement is None:
        return None

    if engagement.volume is None and engagement.liquidity is None:
        return None

    volume = math.log1p(engagement.volume or 0)
    liquidity = math.log1p(engagement.liquidity or 0)

    return 0.60 * volume + 0.40 * liquidity


def score_polymarket_items(items: List[schema.PolymarketItem]) -> List[schema.PolymarketItem]:
    """Compute scores for Polymarket items.

    Uses same weight structure as Reddit/X (relevance + recency + engagement).
    """
    if not items:
        return items

    eng_raw = [compute_polymarket_engagement_raw(item.engagement) for item in items]
    eng_normalized = normalize_to_100(eng_raw)

    for i, item in enumerate(items):
        rel_score = int(item.relevance * 100)
        rec_score = dates.recency_score(item.date)

        if eng_normalized[i] is not None:
            eng_score = int(eng_normalized[i])
        else:
            eng_score = DEFAULT_ENGAGEMENT

        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=eng_score,
        )

        overall = (
            PM_WEIGHT_RELEVANCE * rel_score +
            PM_WEIGHT_RECENCY * rec_score +
            PM_WEIGHT_ENGAGEMENT * eng_score
        )

        if eng_raw[i] is None:
            overall -= UNKNOWN_ENGAGEMENT_PENALTY

        item.score = max(0, min(100, int(overall)))

    return items


def score_websearch_items(items: List[schema.WebSearchItem], query_type: QueryType = None) -> List[schema.WebSearchItem]:
    """Compute scores for WebSearch items WITHOUT engagement metrics.

    Uses reweighted formula: 55% relevance + 45% recency - penalty.
    Penalty varies by query type: concept queries get 0 penalty (web docs
    are authoritative), while product/opinion queries get full 15pt penalty
    (social discussion is more valuable).

    Args:
        items: List of WebSearch items
        query_type: Query classification for penalty adjustment

    Returns:
        Items with updated scores
    """
    if not items:
        return items

    for item in items:
        # Relevance subscore (model-provided, convert to 0-100)
        rel_score = int(item.relevance * 100)

        # Recency subscore
        rec_score = dates.recency_score(item.date)

        # Store subscores (engagement is 0 for WebSearch - no data)
        item.subs = schema.SubScores(
            relevance=rel_score,
            recency=rec_score,
            engagement=0,  # Explicitly zero - no engagement data available
        )

        # Compute overall score using WebSearch weights
        overall = (
            WEBSEARCH_WEIGHT_RELEVANCE * rel_score +
            WEBSEARCH_WEIGHT_RECENCY * rec_score
        )

        # Apply source penalty (varies by query type)
        penalty = WEBSEARCH_PENALTY_BY_TYPE.get(query_type, WEBSEARCH_SOURCE_PENALTY) if query_type else WEBSEARCH_SOURCE_PENALTY
        overall -= penalty

        # Apply date confidence adjustments
        # High confidence (URL-verified): reward with bonus
        # Med confidence (snippet-extracted): neutral
        # Low confidence (no date signals): heavy penalty
        if item.date_confidence == "high":
            overall += WEBSEARCH_VERIFIED_BONUS  # Reward verified recent dates
        elif item.date_confidence == "low":
            overall -= WEBSEARCH_NO_DATE_PENALTY  # Heavy penalty for unknown

        item.score = max(0, min(100, int(overall)))

    return items


_ITEM_SOURCE_MAP = {
    schema.RedditItem: "reddit",
    schema.XItem: "x",
    schema.YouTubeItem: "youtube",
    schema.TikTokItem: "tiktok",
    schema.InstagramItem: "instagram",
    schema.HackerNewsItem: "hn",
    schema.GitHubItem: "github",
    schema.BlueskyItem: "bluesky",
    schema.TruthSocialItem: "truthsocial",
    schema.PolymarketItem: "polymarket",
}
_DEFAULT_TIEBREAKER = {"reddit": 0, "x": 1, "youtube": 2, "tiktok": 3, "instagram": 4, "hn": 5, "github": 6, "bluesky": 7, "truthsocial": 8, "polymarket": 9, "web": 10}


def sort_items(items: List[Union[schema.RedditItem, schema.XItem, schema.WebSearchItem, schema.YouTubeItem, schema.TikTokItem, schema.InstagramItem, schema.HackerNewsItem, schema.GitHubItem, schema.BlueskyItem, schema.TruthSocialItem, schema.PolymarketItem]], query_type: QueryType = None) -> List:
    """Sort items by score (descending), then date, then source tiebreaker.

    Tiebreaker (tertiary sort key, after score and date): source priority
    varies by query type. YouTube ranks first for how_to, X ranks first
    for breaking_news, Polymarket ranks first for prediction.

    Args:
        items: List of items to sort
        query_type: Query classification for tiebreaker adjustment

    Returns:
        Sorted items
    """
    tiebreaker = TIEBREAKER_BY_TYPE.get(query_type, _DEFAULT_TIEBREAKER) if query_type else _DEFAULT_TIEBREAKER

    def sort_key(item):
        # Primary: score descending (negate for descending)
        score = -item.score

        # Secondary: date descending (recent first)
        date = item.date or "0000-00-00"
        date_key = -int(date.replace("-", ""))

        # Tertiary: query-type-aware source priority
        source_name = _ITEM_SOURCE_MAP.get(type(item), "web")
        source_priority = tiebreaker.get(source_name, 99)

        # Quaternary: title/text for stability
        text = getattr(item, "title", "") or getattr(item, "text", "")

        return (score, date_key, source_priority, text)

    return sorted(items, key=sort_key)


def relevance_filter(items, source_name: str, threshold: float = 0.3):
    """Filter items below relevance threshold with minimum-result guarantee.

    Items with no relevance attribute are treated as 0.0 (fail the filter).
    If all items are below threshold, keeps the top 3 by relevance.
    Lists with 3 or fewer items are returned unchanged.
    """
    import sys
    if len(items) <= 3:
        return items
    passed = [i for i in items if getattr(i, 'relevance', 0.0) >= threshold]
    if not passed:
        print(f"[{source_name} WARNING] All results below relevance {threshold}, keeping top 3", file=sys.stderr)
        by_rel = sorted(items, key=lambda x: getattr(x, 'relevance', 0.0), reverse=True)
        return by_rel[:3]
    return passed
