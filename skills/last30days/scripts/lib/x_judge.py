"""X corpus judging for retrieve-judge-retry.

No I/O: judges items already retrieved. Reuses relevance.token_overlap_relevance.

The judge detects off-topic floods and determines which extracted handles should
be promoted to the FROM lane based on on-topic post ratio, not frequency.
"""

from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from . import relevance

# Minimum on-topic ratio for the overall corpus. Below this, the engine should
# retry with a wider keyword query. Rome measured ~0.2 (8/40 on-topic).
CORPUS_ON_TOPIC_FLOOR = 0.4

# Minimum on-topic ratio for a handle's posts to qualify for FROM promotion.
HANDLE_ON_TOPIC_FLOOR = 0.5

# Minimum on-topic keyword hits before a handle can be promoted to FROM lane.
# Prevents promoting handles that appeared in thin phrase hits with only 1 match.
MIN_ON_TOPIC_HITS = 2


def _compute_relevance(query: str, text: str) -> float:
    """Compute relevance score for a post against the topic query.

    Returns 0.0 for empty/stopword-only queries to avoid treating all items
    as equally relevant (the shared relevance module returns 0.5 for empty
    queries as a neutral fallback, but x_judge needs strict filtering).
    """
    if not query or not text:
        return 0.0
    # Check if query has any tokens after stopword removal
    tokens = relevance.tokenize(query)
    if not tokens:
        return 0.0  # All query tokens were stopwords
    return relevance.token_overlap_relevance(query, text)


def judge_x_corpus(
    items: List[Dict[str, Any]],
    topic: str,
    *,
    ranking_query: str = "",
) -> Dict[str, Any]:
    """Judge the retrieved X corpus for on-topic ratio.

    Args:
        items: List of X items with 'author_handle' and 'text' fields
        topic: The user topic (e.g., "Rome")
        ranking_query: Optional ranking query for better relevance scoring

    Returns:
        Dict with:
        - on_topic_ratio: float, fraction of posts that are on-topic
        - is_off_topic_flood: bool, True if corpus fails the on-topic floor
        - on_topic_items: list, items that passed relevance check
        - off_topic_items: list, items that failed relevance check
        - handle_stats: dict, per-handle on-topic counts and totals
    """
    if not items:
        return {
            "on_topic_ratio": 1.0,
            "is_off_topic_flood": False,
            "on_topic_items": [],
            "off_topic_items": [],
            "handle_stats": {},
        }

    # Use ranking_query if provided, otherwise topic
    query = ranking_query or topic

    on_topic_items = []
    off_topic_items = []
    handle_stats: Dict[str, Dict[str, int]] = {}

    for item in items:
        handle = (item.get("author_handle") or "").lower()
        text = item.get("text") or ""
        score = _compute_relevance(query, text)

        # On-topic threshold: relevance.RELEVANCE_FLOOR is 0.1
        is_on_topic = score >= relevance.RELEVANCE_FLOOR

        if is_on_topic:
            on_topic_items.append(item)
        else:
            off_topic_items.append(item)

        if handle:
            if handle not in handle_stats:
                handle_stats[handle] = {"on_topic": 0, "total": 0}
            handle_stats[handle]["total"] += 1
            if is_on_topic:
                handle_stats[handle]["on_topic"] += 1

    on_topic_ratio = len(on_topic_items) / len(items) if items else 1.0

    # Check if top-3 frequency authors have poor on-topic ratio
    top_handles = sorted(
        handle_stats.items(),
        key=lambda x: x[1]["total"],
        reverse=True,
    )[:3]
    top_authors_off_topic = all(
        stats["on_topic"] / stats["total"] < HANDLE_ON_TOPIC_FLOOR
        for _, stats in top_handles
        if stats["total"] > 0
    ) if top_handles else False

    is_off_topic_flood = (
        on_topic_ratio < CORPUS_ON_TOPIC_FLOOR
        or (top_authors_off_topic and len(on_topic_items) < MIN_ON_TOPIC_HITS)
    )

    return {
        "on_topic_ratio": on_topic_ratio,
        "is_off_topic_flood": is_off_topic_flood,
        "on_topic_items": on_topic_items,
        "off_topic_items": off_topic_items,
        "handle_stats": handle_stats,
    }


def promotable_handles(
    items: List[Dict[str, Any]],
    topic: str,
    extracted_handles: List[str],
    *,
    explicit_handles: Optional[List[str]] = None,
    ranking_query: str = "",
) -> Tuple[List[str], List[str]]:
    """Determine which handles should be promoted to the FROM lane.

    Split FROM promotion:
    - Explicit handles (--x-handle/--x-related): always promoted, no AND topic
    - Extracted handles: promoted only if:
      - ≥MIN_ON_TOPIC_HITS on-topic keyword hits AND
      - author on-topic ratio ≥ HANDLE_ON_TOPIC_FLOOR
      - These pulls AND the topic (from:handle Rome)

    Args:
        items: List of X items with 'author_handle' and 'text' fields
        topic: The user topic
        extracted_handles: Handles extracted from entity_extract
        explicit_handles: Explicit --x-handle/--x-related handles
        ranking_query: Optional ranking query for relevance scoring

    Returns:
        Tuple of (explicit_promotable, extracted_promotable):
        - explicit_promotable: handles that get FROM without AND topic
        - extracted_promotable: handles that get FROM with AND topic
    """
    explicit_set = {
        h.lower().lstrip("@")
        for h in (explicit_handles or [])
        if h and h.strip()
    }

    # Judge corpus to get handle stats
    judgment = judge_x_corpus(items, topic, ranking_query=ranking_query)
    handle_stats = judgment["handle_stats"]

    explicit_promotable = []
    extracted_promotable = []

    for handle in extracted_handles:
        handle_lower = handle.lower().lstrip("@")

        # Explicit handles always promoted (no AND topic)
        if handle_lower in explicit_set:
            explicit_promotable.append(handle)
            continue

        # Check if handle qualifies for extracted promotion
        stats = handle_stats.get(handle_lower, {"on_topic": 0, "total": 0})

        # Need ≥MIN_ON_TOPIC_HITS on-topic posts
        if stats["on_topic"] < MIN_ON_TOPIC_HITS:
            continue

        # Need ≥HANDLE_ON_TOPIC_FLOOR ratio
        if stats["total"] > 0:
            ratio = stats["on_topic"] / stats["total"]
            if ratio >= HANDLE_ON_TOPIC_FLOOR:
                extracted_promotable.append(handle)

    # Also check explicit handles not in extracted list
    for handle in (explicit_handles or []):
        handle_lower = handle.lower().lstrip("@")
        if handle_lower not in [h.lower() for h in explicit_promotable]:
            if handle_lower not in [h.lower() for h in extracted_handles]:
                explicit_promotable.append(handle)

    return explicit_promotable, extracted_promotable


def should_retry_x_search(
    items: List[Dict[str, Any]],
    topic: str,
    *,
    ranking_query: str = "",
    depth: str = "default",
) -> bool:
    """Determine if X search should retry with wider keyword query.

    Skip retry on quick/mock (same as Phase 2).

    Args:
        items: Retrieved X items
        topic: The user topic
        ranking_query: Optional ranking query
        depth: Search depth ("quick", "default", "deep")

    Returns:
        True if retry is warranted
    """
    if depth == "quick":
        return False

    if not items:
        return False  # Nothing to judge, no retry

    judgment = judge_x_corpus(items, topic, ranking_query=ranking_query)
    return judgment["is_off_topic_flood"]


def prune_off_topic_items(
    items: List[Dict[str, Any]],
    topic: str,
    *,
    ranking_query: str = "",
) -> List[Dict[str, Any]]:
    """Prune off-topic items before the pool.

    Eight on-topic items with 32 pruned → ok with 8.
    Zero on-topic → no-results, not ok with 40 junk.

    Args:
        items: Retrieved X items
        topic: The user topic
        ranking_query: Optional ranking query

    Returns:
        Only on-topic items
    """
    judgment = judge_x_corpus(items, topic, ranking_query=ranking_query)
    return judgment["on_topic_items"]
