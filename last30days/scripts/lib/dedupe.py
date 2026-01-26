"""Near-duplicate detection for last30days skill."""

import re
from typing import List, Set, Tuple, Union

from . import schema


def normalize_text(text: str) -> str:
    """Normalize text for comparison.

    - Lowercase
    - Remove punctuation
    - Collapse whitespace
    """
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def get_ngrams(text: str, n: int = 3) -> Set[str]:
    """Get character n-grams from text."""
    text = normalize_text(text)
    if len(text) < n:
        return {text}
    return {text[i:i+n] for i in range(len(text) - n + 1)}


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def get_item_text(item: Union[schema.RedditItem, schema.XItem]) -> str:
    """Get comparable text from an item."""
    if isinstance(item, schema.RedditItem):
        return item.title
    else:
        return item.text


def find_duplicates(
    items: List[Union[schema.RedditItem, schema.XItem]],
    threshold: float = 0.7,
) -> List[Tuple[int, int]]:
    """Find near-duplicate pairs in items.

    Args:
        items: List of items to check
        threshold: Similarity threshold (0-1)

    Returns:
        List of (i, j) index pairs where i < j and items are similar
    """
    duplicates = []

    # Pre-compute n-grams
    ngrams = [get_ngrams(get_item_text(item)) for item in items]

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            similarity = jaccard_similarity(ngrams[i], ngrams[j])
            if similarity >= threshold:
                duplicates.append((i, j))

    return duplicates


def dedupe_items(
    items: List[Union[schema.RedditItem, schema.XItem]],
    threshold: float = 0.7,
) -> List[Union[schema.RedditItem, schema.XItem]]:
    """Remove near-duplicates, keeping highest-scored item.

    Args:
        items: List of items (should be pre-sorted by score descending)
        threshold: Similarity threshold

    Returns:
        Deduplicated items
    """
    if len(items) <= 1:
        return items

    # Find duplicate pairs
    dup_pairs = find_duplicates(items, threshold)

    # Mark indices to remove (always remove the lower-scored one)
    # Since items are pre-sorted by score, the second index is always lower
    to_remove = set()
    for i, j in dup_pairs:
        # Keep the higher-scored one (lower index in sorted list)
        if items[i].score >= items[j].score:
            to_remove.add(j)
        else:
            to_remove.add(i)

    # Return items not marked for removal
    return [item for idx, item in enumerate(items) if idx not in to_remove]


def dedupe_reddit(
    items: List[schema.RedditItem],
    threshold: float = 0.7,
) -> List[schema.RedditItem]:
    """Dedupe Reddit items."""
    return dedupe_items(items, threshold)


def dedupe_x(
    items: List[schema.XItem],
    threshold: float = 0.7,
) -> List[schema.XItem]:
    """Dedupe X items."""
    return dedupe_items(items, threshold)
