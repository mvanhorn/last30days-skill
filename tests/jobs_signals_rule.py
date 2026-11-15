"""The decision rule for Jobs Page Secret Signals, written out as arithmetic.

The rule itself is prose in ``skills/last30days/references/hiring-signals.md``
and is applied by the model writing the report. This module restates only the
part that is arithmetic, so the labelled set in
``tests/fixtures/jobs_signals_labelled.json`` can be checked against it:

- does a candidate signal qualify, given which of the four tests it meets;
- is the section written, and with how many signals, given a board's candidates
  (a cluster of postings is one candidate);
- how may a qualifying signal be worded, given what the web check found.

It does NOT test whether a model finds the right signals in a digest or judges
the four tests correctly. Nothing here reads a digest. That needs live runs,
compared against the labels by a person. It is test support, not shipped code.
"""

from __future__ import annotations

import re
from typing import Any

OUT_OF_CHARACTER, CONCRETE, HARD_TO_UNDO, BEARS_ON_STORY = 1, 2, 3, 4
MIN_SIGNALS = 1
MAX_SIGNALS = 2
# Postings that must share one concrete focus to count together as one signal.
MIN_CLUSTER_POSTINGS = 3
# Postings of a cluster cited in its paragraph, and sentences per paragraph.
CLUSTER_POSTINGS_CITED = 2
MAX_PARAGRAPH_SENTENCES = 2
# The longest optional quote from a posting.
MAX_QUOTE_WORDS = 12
LEAD_IN = "**Jobs Page Secret Signals** - "

_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[{A-Z])")
_QUOTE = re.compile(r'"([^"]+)"')
_OVERCLAIM = re.compile(
    r"\b(?:is|are) (?:building|developing|launching|shipping)\b"
    r"|\bwill (?:ship|launch|release|sell|build)\b"
    r"|\bhas decided\b|\bplans to\b",
    re.IGNORECASE,
)


def qualifies(tests: list[int], postings: int = 1) -> bool:
    """At least two of the first three tests, or the fourth.

    A cluster (``postings`` sharing one concrete focus, three or more) also
    qualifies when it is out of character: the shared focus is the concrete
    thing it names.
    """
    met = set(tests)
    if BEARS_ON_STORY in met or len(met & {OUT_OF_CHARACTER, CONCRETE, HARD_TO_UNDO}) >= 2:
        return True
    return postings >= MIN_CLUSTER_POSTINGS and OUT_OF_CHARACTER in met


def postings(candidate: dict[str, Any]) -> int:
    """How many postings a candidate stands for: a cluster's, else one."""
    return len(candidate.get("cluster") or []) or 1


def qualifying(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        candidate for candidate in candidates
        if qualifies(candidate["tests"], postings(candidate))
    ]


def max_section_paragraphs() -> int:
    """The signal paragraphs (the lead-in runs into the first) and the closing line."""
    return MAX_SIGNALS + 1


def section_present(
    candidates: list[dict[str, Any]],
    *,
    hiring_signals_run: bool = False,
    web_search: bool = True,
) -> bool:
    """One qualifying signal, on any run; never without web search."""
    if not web_search:
        return False
    return len(qualifying(candidates)) >= MIN_SIGNALS


def signals_shown(candidates: list[dict[str, Any]], **run: bool) -> int:
    """How many signal paragraphs the section has. Which two is the model's call."""
    if not section_present(candidates, **run):
        return 0
    return min(len(qualifying(candidates)), MAX_SIGNALS)


def wording(candidate: dict[str, Any]) -> str:
    """``public`` when already announced, ``unreported`` only when a check found nothing."""
    if candidate["public"] is True:
        return "public"
    if candidate["public"] is False:
        return "unreported"
    return "check first"


def standout(candidate: dict[str, Any]) -> bool:
    """May also be one KEY PATTERNS line: all of the first three tests, and checked unreported."""
    return (
        {OUT_OF_CHARACTER, CONCRETE, HARD_TO_UNDO} <= set(candidate["tests"])
        and wording(candidate) == "unreported"
    )


def sentences(paragraph: str) -> list[str]:
    body = paragraph.removeprefix(LEAD_IN)
    return [part for part in _SENTENCE_BREAK.split(body) if part]


def paragraph_ok(paragraph: str) -> bool:
    """A signal paragraph in the plain-words shape: two sentences, the role linked,
    any quote at most 12 words, and nothing worded as a plan."""
    return (
        len(sentences(paragraph)) == MAX_PARAGRAPH_SENTENCES
        and bool(_LINK.search(paragraph))
        and all(len(quote.split()) <= MAX_QUOTE_WORDS for quote in _QUOTE.findall(paragraph))
        and not _OVERCLAIM.search(paragraph)
    )
