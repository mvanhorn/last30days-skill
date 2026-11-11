"""Best-window extraction for rerankable evidence snippets."""

from __future__ import annotations

import re

from . import relevance, schema


# Stored length of a quoted comment. Renderers shorten further at display
# time and add their own ellipsis, so none is added here.
QUOTE_LIMIT = 400
# A sentence end is used when it falls in the last part of the allowed text;
# an earlier one would throw away most of the quote.
_QUOTE_SENTENCE_ZONE = 0.6
_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*(?=\s)")


def cut_quote(text: str, limit: int = QUOTE_LIMIT) -> str:
    """Shorten a comment to ``limit`` characters without cutting a word.

    Cuts at the last sentence end inside the limit when one falls in its last
    part, otherwise at the last word boundary, otherwise (one unbroken run of
    characters) at the limit. Text that already fits is returned stripped.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    # One character past the limit, so an end or a space right at it counts.
    window = text[: limit + 1]
    ends = [match.end() for match in _SENTENCE_END.finditer(window) if match.end() <= limit]
    if ends and ends[-1] >= limit * _QUOTE_SENTENCE_ZONE:
        return window[: ends[-1]].rstrip()
    # Any whitespace is a word boundary: comments carry newlines and tabs.
    space = next((i for i in range(len(window) - 1, -1, -1) if window[i].isspace()), -1)
    if space > 0:
        return window[:space].rstrip()
    return text[:limit]


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip() + "..."


def _windows(words: list[str], size: int, overlap: int) -> list[str]:
    if not words:
        return []
    if len(words) <= size:
        return [" ".join(words)]
    step = max(1, size - overlap)
    return [
        " ".join(words[start:start + size])
        for start in range(0, len(words), step)
    ]


def extract_best_snippet(
    item: schema.SourceItem,
    ranking_query: "str | relevance.PreparedQuery",
    max_words: int = 120,
) -> str:
    """Prefer existing snippets, else extract the best matching evidence window."""
    preferred = item.snippet.strip()
    if preferred:
        return _truncate_words(preferred, max_words)

    body = item.body.strip()
    if not body:
        return _truncate_words(item.title, max_words)

    words = body.split()
    candidates = _windows(words, size=min(max_words, 110), overlap=30)
    if not candidates:
        return _truncate_words(body, max_words)

    prepared_query = ranking_query if isinstance(ranking_query, relevance.PreparedQuery) else relevance.PreparedQuery(ranking_query)
    best = max(
        candidates,
        key=lambda candidate: relevance.token_overlap_relevance(prepared_query, candidate),
    )
    return _truncate_words(best, max_words)
