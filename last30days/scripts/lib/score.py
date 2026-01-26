"""Popularity-aware scoring for last30days skill."""

import math
from typing import List, Optional, Union

from . import dates, schema

# Score weights for Reddit/X (has engagement)
WEIGHT_RELEVANCE = 0.45
WEIGHT_RECENCY = 0.25
WEIGHT_ENGAGEMENT = 0.30

# WebSearch weights (no engagement, reweighted to 100%)
WEBSEARCH_WEIGHT_RELEVANCE = 0.55
WEBSEARCH_WEIGHT_RECENCY = 0.45
WEBSEARCH_SOURCE_PENALTY = 15  # Points deducted for lacking engagement

# WebSearch date confidence adjustments
WEBSEARCH_VERIFIED_BONUS = 10   # Bonus for URL-verified recent date (high confidence)
WEBSEARCH_NO_DATE_PENALTY = 20  # Heavy penalty for no date signals (low confidence)

# Default engagement score for unknown
DEFAULT_ENGAGEMENT = 35
UNKNOWN_ENGAGEMENT_PENALTY = 10


def log1p_safe(x: Optional[int]) -> float:
    """Safe log1p that handles None and negative values."""
    if x is None or x < 0:
        return 0.0
    return math.log1p(x)


def compute_reddit_engagement_raw(engagement: Optional[schema.Engagement]) -> Optional[float]:
    """Compute raw engagement score for Reddit item.

    Formula: 0.55*log1p(score) + 0.40*log1p(num_comments) + 0.05*(upvote_ratio*10)
    """
    if engagement is None:
        return None

    if engagement.score is None and engagement.num_comments is None:
        return None

    score = log1p_safe(engagement.score)
    comments = log1p_safe(engagement.num_comments)
    ratio = (engagement.upvote_ratio or 0.5) * 10

    return 0.55 * score + 0.40 * comments + 0.05 * ratio


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

    # Compute raw engagement scores
    eng_raw = [compute_reddit_engagement_raw(item.engagement) for item in items]

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
            overall -= 10
        elif item.date_confidence == "med":
            overall -= 5

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
            overall -= 10
        elif item.date_confidence == "med":
            overall -= 5

        item.score = max(0, min(100, int(overall)))

    return items


def score_websearch_items(items: List[schema.WebSearchItem]) -> List[schema.WebSearchItem]:
    """Compute scores for WebSearch items WITHOUT engagement metrics.

    Uses reweighted formula: 55% relevance + 45% recency - 15pt source penalty.
    This ensures WebSearch items rank below comparable Reddit/X items.

    Date confidence adjustments:
    - High confidence (URL-verified date): +10 bonus
    - Med confidence (snippet-extracted date): no change
    - Low confidence (no date signals): -20 penalty

    Args:
        items: List of WebSearch items

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

        # Apply source penalty (WebSearch < Reddit/X for same relevance/recency)
        overall -= WEBSEARCH_SOURCE_PENALTY

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


def sort_items(items: List[Union[schema.RedditItem, schema.XItem, schema.WebSearchItem]]) -> List:
    """Sort items by score (descending), then date, then source priority.

    Args:
        items: List of items to sort

    Returns:
        Sorted items
    """
    def sort_key(item):
        # Primary: score descending (negate for descending)
        score = -item.score

        # Secondary: date descending (recent first)
        date = item.date or "0000-00-00"
        date_key = -int(date.replace("-", ""))

        # Tertiary: source priority (Reddit > X > WebSearch)
        if isinstance(item, schema.RedditItem):
            source_priority = 0
        elif isinstance(item, schema.XItem):
            source_priority = 1
        else:  # WebSearchItem
            source_priority = 2

        # Quaternary: title/text for stability
        text = getattr(item, "title", "") or getattr(item, "text", "")

        return (score, date_key, source_priority, text)

    return sorted(items, key=sort_key)
