"""The decision rule for Patent Secret Signals, written out as arithmetic.

The rule itself is prose in ``skills/last30days/references/patent-signals.md``
and is applied by the model writing the report. This module restates only the
part that is arithmetic, so the labelled set in
``tests/fixtures/patent_signals_labelled.json`` can be checked against it:

- may a filing be a signal at all (a first US publication with its text,
  filed by an entity known to be the company's, with no contradicting
  location);
- does a candidate qualify, given whether it is concrete, which of the two
  paths of the bar it meets and what the web check found (a cluster is one
  candidate; a tie to a story in the report never qualifies one);
- is the section written, and with how many signals, given a company's
  candidates and how many jobs signals the same report carries.

It does NOT test whether a model finds the right filings in a digest or judges
the bar correctly. Nothing here reads a digest. That needs live runs, read by a
person. It is test support, not shipped code.
"""

from __future__ import annotations

from typing import Any

OUT_OF_CHARACTER, UNSHIPPED_PRODUCT = 1, 2
QUALIFYING_PATHS = frozenset({OUT_OF_CHARACTER, UNSHIPPED_PRODUCT})
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
PARAGRAPH_SENTENCES = 2


def usable(filing: dict[str, Any]) -> bool:
    """A first US publication the report may attribute to the company."""
    if filing.get("kind", FIRST_PUBLICATION) != FIRST_PUBLICATION:
        return False
    if filing.get("not_verified") and filing.get("entity_known") is not True:
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


def _matched(candidate: dict[str, Any]) -> bool:
    """The web check found a matching product: shipped, announced or reported."""
    return candidate.get("shipped_match") is True or candidate.get("reported_match") is True


def _unmatched(candidate: dict[str, Any]) -> bool:
    """The web check ran and found no matching product of any kind."""
    return candidate.get("shipped_match") is False and candidate.get("reported_match") is not True


def paths_met(candidate: dict[str, Any]) -> set[int]:
    """The paths that survive the web check: a shipped or reported match removes the second."""
    met = set(candidate["paths"]) & QUALIFYING_PATHS
    if _matched(candidate):
        met.discard(UNSHIPPED_PRODUCT)
    return met


def qualifies(candidate: dict[str, Any]) -> bool:
    """Out of character or an unshipped product, judged from the filing's text.

    A titles-only entry never qualifies. A cluster qualifies by either path
    once it has three families. A tie to a story in the report is never a path.
    """
    if not usable(candidate) or candidate.get("titles_only"):
        return False
    met = paths_met(candidate)
    if "cluster" in candidate:
        return families(candidate) >= MIN_CLUSTER_FAMILIES and bool(met)
    return bool(candidate.get("concrete")) and bool(met)


def qualifying(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [candidate for candidate in candidates if qualifies(candidate)]


def max_section_paragraphs() -> int:
    """The lead-in runs into the first signal, so each signal is one paragraph."""
    return MAX_SIGNALS


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
    """How many patent paragraphs the report has, after the jobs section took its own.

    One by default. Two only when two qualifying signals both had no matching
    product on the web.
    """
    if not section_present(candidates, web_search=web_search):
        return 0
    unmatched = [candidate for candidate in qualifying(candidates) if _unmatched(candidate)]
    wanted = MAX_SIGNALS if len(unmatched) >= MAX_SIGNALS else MIN_SIGNALS
    return max(MIN_SIGNALS, min(wanted, MAX_COMBINED_SIGNALS - jobs_signals))


def wording(candidate: dict[str, Any]) -> str:
    """``public`` when the check found a match or coverage, else what it may say."""
    found = (candidate.get("shipped_match"), candidate.get("covered"))
    if True in found or candidate.get("reported_match") is True:
        return "public"
    if found == (False, False):
        return "no coverage found"
    return "check first"


def standout(candidate: dict[str, Any]) -> bool:
    """May also be one KEY PATTERNS line: concrete, qualifying, and the check found nothing."""
    return (
        qualifies(candidate)
        and bool(candidate.get("concrete"))
        and wording(candidate) == "no coverage found"
    )
