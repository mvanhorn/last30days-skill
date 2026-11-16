"""Story terms: the few phrases the jobs lane searches each board for.

They come from the run's own stories. The host plan sends them as
``jobs_queries``; when it sends none, ``derive_terms`` takes them from the
plan's subqueries, leaving out the company, the people, the other side of a
comparison and generic words. Terms are fixed once, before the first jobs
stream starts, because that stream settles the lane's budget.
"""

from __future__ import annotations

import re
from typing import Iterable

MAX_TERMS = 4
MAX_TERM_CHARS = 40
# A derived phrase longer than this reads as a sentence, which no job
# title or description repeats.
MAX_DERIVED_WORDS = 3
# A term shorter than this is matched as a whole word ("AI" is not in
# "Maintenance"); a longer one anywhere in the text.
WHOLE_WORD_UNDER = 5

GENERIC_WORDS = frozenset("""
a about after an and announcement announcements are as at by careers ceo
company compared discussion discussions for from has have hiring how in is
it its job jobs latest new news of on opinion opinions or over people
reaction reactions recent reddit review reviews say says the think this to
today twitter update updates versus vs what who why will with x youtube
tiktok week month year
""".split())

_EDGE_PUNCT = "\"'()[]{}<>.,;:!?"


def clean_terms(raw: object) -> list[str]:
    """The plan's story terms: strings, trimmed, deduplicated, the first four."""
    if not isinstance(raw, list):
        return []
    return _first_distinct(
        " ".join(term.split()) for term in raw if isinstance(term, str)
    )


def _first_distinct(terms: Iterable[str]) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.casefold()
        if not term or len(term) > MAX_TERM_CHARS or key in seen:
            continue
        seen.add(key)
        kept.append(term)
        if len(kept) == MAX_TERMS:
            break
    return kept


def _name_words(names: Iterable[str]) -> set[str]:
    """Each word of each name, case-folded, a domain's suffix dropped."""
    words: set[str] = set()
    for name in names:
        for word in str(name or "").split():
            word = word.strip(_EDGE_PUNCT).casefold()
            if word:
                words.add(word)
                words.add(re.sub(r"\.(com|net|org|io|ai|co)$", "", word))
    return words


def _is_title_word(word: str) -> bool:
    return len(word) > 1 and word[0].isupper() and word[1:].islower() and word.isalpha()


def derive_terms(search_queries: Iterable[str], excluded_names: Iterable[str]) -> list[str]:
    """Story terms from the plan's subqueries, when the host sent none.

    Each subquery is cut at every word that is part of an excluded name (the
    topic, the company, the person, a comparison entity), a generic word, a
    number, or a run of two or more capitalized words (a person's name such
    as "Andy Jassy"; the engine cannot tell it from a product's, so both are
    left out). What remains between the cuts, at most three words, is a term.
    """
    excluded = _name_words(excluded_names)
    found: list[str] = []
    for query in search_queries:
        tokens = str(query or "").split()
        cleaned = [token.strip(_EDGE_PUNCT) for token in tokens]
        dropped = [
            not word
            or word.casefold() in excluded
            or word.casefold() in GENERIC_WORDS
            or word.isdigit()
            for word in cleaned
        ]
        # Two or more capitalized words in a row, among the words kept.
        index = 0
        while index < len(cleaned):
            end = index
            while end < len(cleaned) and not dropped[end] and _is_title_word(cleaned[end]):
                end += 1
            if end - index >= 2:
                for position in range(index, end):
                    dropped[position] = True
            index = max(end, index + 1)
        segment: list[str] = []
        for token, word, drop in zip(tokens, cleaned, dropped):
            if not drop:
                segment.append(word)
            if drop or token != word:
                # A dropped word or a mark of punctuation ends the phrase.
                found.extend(_phrase(segment))
                segment = []
        found.extend(_phrase(segment))
    return _first_distinct(found)


def _phrase(words: list[str]) -> list[str]:
    return [" ".join(words)] if 0 < len(words) <= MAX_DERIVED_WORDS else []


def matches(term: str, text: str) -> bool:
    """Whether a job's text mentions the story term.

    A term under five characters must stand as a whole word ("AI" matches
    "AI Native", not "Maintenance"); a longer one may appear anywhere. Case
    is ignored either way.
    """
    term = " ".join(str(term or "").split())
    if not term:
        return False
    if len(term) < WHOLE_WORD_UNDER:
        return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text or "", re.IGNORECASE) is not None
    return term.casefold() in (text or "").casefold()
