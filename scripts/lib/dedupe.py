"""Within-source near-duplicate detection."""

from __future__ import annotations

import re

from . import schema

STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "to",
        "for",
        "how",
        "is",
        "in",
        "of",
        "on",
        "and",
        "with",
        "from",
        "by",
        "at",
        "this",
        "that",
        "it",
        "what",
        "are",
        "do",
        "can",
    }
)


def normalize_text(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def get_ngrams(text: str, n: int = 3) -> set[str]:
    text = normalize_text(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[index:index + n] for index in range(len(text) - n + 1)}


def jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def token_jaccard(text_a: str, text_b: str) -> float:
    tokens_a = {
        token
        for token in normalize_text(text_a).split()
        if len(token) > 1 and token not in STOPWORDS
    }
    tokens_b = {
        token
        for token in normalize_text(text_b).split()
        if len(token) > 1 and token not in STOPWORDS
    }
    return jaccard_similarity(tokens_a, tokens_b)


def hybrid_similarity(text_a: str, text_b: str) -> float:
    return max(
        jaccard_similarity(get_ngrams(text_a), get_ngrams(text_b)),
        token_jaccard(text_a, text_b),
    )


def item_text(item: schema.SourceItem) -> str:
    parts = [item.title, item.body, item.author or "", item.container or ""]
    return " ".join(part for part in parts if part).strip()


def dedupe_items(items: list[schema.SourceItem], threshold: float = 0.7) -> list[schema.SourceItem]:
    """Remove near-duplicates while keeping earlier, better-scored items."""
    kept: list[schema.SourceItem] = []
    for item in items:
        text = item_text(item)
        if not text:
            kept.append(item)
            continue
        is_duplicate = False
        for existing in kept:
            if hybrid_similarity(text, item_text(existing)) >= threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(item)
    return kept
