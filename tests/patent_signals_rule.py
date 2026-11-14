"""The decision rule for Patent Secret Signals, written out as arithmetic.

The rule itself is prose in ``skills/last30days/references/patent-signals.md``
and is applied by the model writing the report. This module restates only the
part that is arithmetic, so the labelled set in
``tests/fixtures/patent_signals_labelled.json`` can be checked against it:

- may a filing be a signal at all (a first US publication, filed by an entity
  known to be the company's, with no contradicting location);
- does a candidate qualify, given whether it is concrete, which paths of the
  bar it meets and what the web check found (a cluster is one candidate);
- is the section written, and with how many signals, given a company's
  candidates and how many jobs signals the same report carries.

It does NOT test whether a model finds the right filings in a digest or judges
the bar correctly. Nothing here reads a digest. That needs live runs, read by a
person. It is test support, not shipped code.
"""

from __future__ import annotations

from typing import Any

OUT_OF_CHARACTER, UNSHIPPED_PRODUCT, BEARS_ON_STORY = 1, 2, 3
FIRST_PUBLICATION = "first publication"
# Counted in the digest, never listed there, never a signal.
NEVER_SIGNALS = frozenset({"continuation", "divisional", "grant published earlier", "design"})
MIN_SIGNALS = 1
MAX_SIGNALS = 2
# Signal paragraphs across the jobs section and this one; the jobs section goes first.
MAX_COMBINED_SIGNALS = 3
# Invention families that must share one specific focus to count together as one signal.
MIN_CLUSTER_FAMILIES = 3
# Filings of a cluster cited in its paragraph, and sentences per paragraph.
CLUSTER_FILINGS_CITED = 2
MAX_PARAGRAPH_SENTENCES = 3


def usable(filing: dict[str, Any]) -> bool:
    """A first US publication the report may attribute to the company."""
    if filing.get("kind", FIRST_PUBLICATION) != FIRST_PUBLICATION:
        return False
    if filing.get("name_matched") and filing.get("entity_known") is not True:
        return False
    return not filing.get("location_contradicts")


def families(candidate: dict[str, Any]) -> int:
    """Invention families a candidate stands for: a cluster's distinct ones, else one.

    Filings that share one earliest priority date and one filed-as name are
    one family. A titles-only member prints neither date, so it cannot be
    shown to be a separate family and is not counted.
    """
    members = candidate.get("cluster")
    if members is None:
        return 1
    return len({
        (m["priority"], m["filed_as"])
        for m in members if usable(m) and not m.get("titles_only")
    })


def paths_met(candidate: dict[str, Any]) -> set[int]:
    """The paths that survive the web check: a shipped or reported match removes the second."""
    met = set(candidate["paths"])
    if candidate.get("shipped_match") is True or candidate.get("reported_match") is True:
        met.discard(UNSHIPPED_PRODUCT)
    return met


def qualifies(candidate: dict[str, Any]) -> bool:
    """Concrete and out of character or unshipped, or bearing on a story.

    A cluster qualifies by any path once it has three families. A titles-only
    entry qualifies only by the story path.
    """
    if not usable(candidate):
        return False
    met = paths_met(candidate)
    if "cluster" in candidate:
        return families(candidate) >= MIN_CLUSTER_FAMILIES and bool(met)
    if BEARS_ON_STORY in met:
        return True
    if candidate.get("titles_only") or not candidate.get("concrete"):
        return False
    return bool(met & {OUT_OF_CHARACTER, UNSHIPPED_PRODUCT})


def qualifying(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in candidates if qualifies(candidate)]


def max_section_paragraphs() -> int:
    """The lead-in line, the signal paragraphs and the closing line."""
    return 1 + MAX_SIGNALS + 1


def section_present(candidates: list[dict[str, Any]], *, web_search: bool = True) -> bool:
    """One qualifying signal is enough; never without web search."""
    if not web_search:
        return False
    return len(qualifying(candidates)) >= MIN_SIGNALS


def signals_shown(
    candidates: list[dict[str, Any]],
    *,
    jobs_signals: int = 0,
    web_search: bool = True,
) -> int:
    """How many patent paragraphs the report has, after the jobs section took its own."""
    if not section_present(candidates, web_search=web_search):
        return 0
    return min(len(qualifying(candidates)), MAX_SIGNALS, MAX_COMBINED_SIGNALS - jobs_signals)


def wording(candidate: dict[str, Any]) -> str:
    """``public`` when the check found a match or coverage, else what it may say."""
    found = (candidate.get("shipped_match"), candidate.get("covered"))
    if True in found or candidate.get("reported_match") is True:
        return "public"
    if found == (False, False):
        return "no coverage found"
    return "check first"


def standout(candidate: dict[str, Any]) -> bool:
    """May also be one KEY PATTERNS line: concrete, path 1 or 2, and the check found nothing."""
    return (
        qualifies(candidate)
        and bool(candidate.get("concrete"))
        and bool(paths_met(candidate) & {OUT_OF_CHARACTER, UNSHIPPED_PRODUCT})
        and wording(candidate) == "no coverage found"
    )
