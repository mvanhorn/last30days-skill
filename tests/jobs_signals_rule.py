"""The decision rule for Jobs Page Secret Signals, written out as arithmetic.

The rule itself is prose in ``skills/last30days/references/hiring-signals.md``
and is applied by the model writing the report. This module restates only the
part that is arithmetic, so the labelled set in
``tests/fixtures/jobs_signals_labelled.json`` can be checked against it:

- does a candidate signal qualify, given which of the four tests it meets;
- is the section written, given a board's candidates;
- how may a qualifying signal be worded, given what the web check found.

It does NOT test whether a model finds the right signals in a digest or judges
the four tests correctly. Nothing here reads a digest. That needs live runs,
compared against the labels by a person. It is test support, not shipped code.
"""

from __future__ import annotations

from typing import Any

OUT_OF_CHARACTER, CONCRETE, HARD_TO_UNDO, BEARS_ON_STORY = 1, 2, 3, 4
MIN_SIGNALS = 2
MAX_SIGNALS = 5


def qualifies(tests: list[int]) -> bool:
    """At least two of the first three tests, or the fourth."""
    met = set(tests)
    return len(met & {OUT_OF_CHARACTER, CONCRETE, HARD_TO_UNDO}) >= 2 or BEARS_ON_STORY in met


def qualifying(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in candidates if qualifies(candidate["tests"])]


def section_present(
    candidates: list[dict[str, Any]],
    *,
    hiring_signals_run: bool = False,
    web_search: bool = True,
) -> bool:
    """Two qualifying signals, or one on a ``--hiring-signals`` run; never without web search."""
    if not web_search:
        return False
    return len(qualifying(candidates)) >= (1 if hiring_signals_run else MIN_SIGNALS)


def signals_shown(candidates: list[dict[str, Any]], **run: bool) -> int:
    """How many signal paragraphs the section has. Which five is the model's call."""
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
