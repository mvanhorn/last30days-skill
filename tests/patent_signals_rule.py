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
  candidates and how many jobs signals the same report carries;
- was the decision made properly: the strongest two concrete candidates (in
  the order listed, strongest first) were web-checked before either was
  dropped, no shipped or reported match was claimed without a check, and no
  search ran when nothing was concrete.

It does NOT test whether a model finds the right filings in a digest or judges
the bar correctly. Nothing here reads a digest. That needs live runs, read by a
person. It is test support, not shipped code.
"""

from __future__ import annotations

import re
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
# Words in one signal paragraph, counted outside its linked filing titles, and
# the longest optional quote from the filing.
MAX_PARAGRAPH_WORDS = 60
MAX_QUOTE_WORDS = 12
# Concrete candidates web-checked before the section is written or left out.
MAX_CHECKS = 2

_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[{A-Z])")
_QUOTE = re.compile(r'"([^"]+)"')
_FILED = re.compile(r"\(filed (?:[A-Z][a-z]+ \d{4}|\{filing month and year\})\)")
# Wording that turns a filing into a plan or a product announcement.
_OVERCLAIM = re.compile(
    r"\b(?:is|are) (?:building|developing|launching|shipping)\b"
    r"|\bwill (?:ship|launch|release|sell|build)\b"
    r"|\bhas decided\b|\bplans to\b",
    re.IGNORECASE,
)


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


def check_candidate(candidate: dict[str, Any]) -> bool:
    """A concrete candidate whose fate turns on the web check.

    It must be one the report could use: a first US publication with its text,
    by an entity known to be the company's, and for a cluster, enough families.
    """
    if not usable(candidate) or candidate.get("titles_only") or not candidate.get("concrete"):
        return False
    return "cluster" not in candidate or families(candidate) >= MIN_CLUSTER_FAMILIES


def checks_owed(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The strongest two concrete candidates; none when nothing is concrete."""
    return [candidate for candidate in candidates if check_candidate(candidate)][:MAX_CHECKS]


def decision_valid(candidates: list[dict[str, Any]]) -> bool:
    """The candidates were checked as the rule requires before any was dropped.

    Every owed check ran, no other search ran, and no candidate carries a
    shipped, reported or coverage finding that no search produced.
    """
    owed = checks_owed(candidates)
    if not all(candidate.get("checked") for candidate in owed):
        return False
    if sum(bool(candidate.get("checked")) for candidate in candidates) != len(owed):
        return False
    return not any(
        not candidate.get("checked")
        and True in (
            candidate.get("shipped_match"), candidate.get("reported_match"), candidate.get("covered"),
        )
        for candidate in candidates
    )


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


def sentences(paragraph: str) -> list[str]:
    body = paragraph.removeprefix("**Patent Secret Signals** - ")
    return [part for part in _SENTENCE_BREAK.split(body) if part]


def words(paragraph: str) -> int:
    """Words in a paragraph, not counting its linked filing titles or the lead-in."""
    body = paragraph.removeprefix("**Patent Secret Signals** - ")
    return len(_LINK.sub("", body).split())


def paragraph_ok(paragraph: str) -> bool:
    """A signal paragraph in the plain-words shape.

    Two sentences, at most 60 words outside the linked titles, application or
    grant named, the filing linked with its month and year of filing in
    parentheses (a cluster cites two filings by publication date instead), any quote at most 12 words, and nothing worded as a plan.
    """
    return (
        len(sentences(paragraph)) == PARAGRAPH_SENTENCES
        and words(paragraph) <= MAX_PARAGRAPH_WORDS
        and bool(re.search(r"\b(?:application|grant)\b", paragraph))
        and bool(_LINK.search(paragraph))
        and (len(_LINK.findall(paragraph)) > 1 or bool(_FILED.search(paragraph)))
        and all(len(quote.split()) <= MAX_QUOTE_WORDS for quote in _QUOTE.findall(paragraph))
        and not _OVERCLAIM.search(paragraph)
    )
