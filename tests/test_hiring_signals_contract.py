"""Jobs Page Secret Signals: the written rules, and the labelled set's arithmetic.

The first half pins the instruction text the model reads. The second half runs
``tests/fixtures/jobs_signals_labelled.json`` through ``tests/jobs_signals_rule``,
which pins the decision rule and the labels' consistency with it. Neither half
tests whether a model picks or classifies signals correctly; only a live run
compared against the labels shows that (see docs/reference/eval.md).
"""

import json
import re
from pathlib import Path

import pytest

from tests import jobs_signals_rule as rule
from tests.skill_contract import ROUTES, reference_text, routing_table

CONDITION = "Hiring intent or an engine ## Hiring Signals or ## Jobs Digest block"
SECTION_HEADING = "## Jobs Page Secret Signals (default company runs)\n"
CLOSING = "These are open postings, not hires or announcements."
LABELLED = json.loads(
    (Path(__file__).parent / "fixtures" / "jobs_signals_labelled.json").read_text(encoding="utf-8")
)
BOARDS = {board["company"]: board for board in LABELLED["boards"]}


def _section():
    text = reference_text("hiring-signals")
    assert SECTION_HEADING in text, "hiring-signals.md has no default-run section"
    return text.split(SECTION_HEADING, 1)[1].split("\n## ", 1)[0]


def _rule(marker):
    """The paragraph of the default-run section that opens with ``marker``."""
    section = _section()
    assert marker in section, f"missing rule: {marker}"
    return section.split(marker, 1)[1].split("\n\n**", 1)[0]


# --- routing and the synthesis gate -------------------------------------------------


def test_a_jobs_digest_in_the_engine_output_routes_the_reference():
    assert ROUTES[CONDITION] == ("hiring-signals",)
    assert routing_table()[CONDITION] == ("hiring-signals",)
    opening = reference_text("hiring-signals").split("\n## ", 1)[0]
    assert "`## Jobs Digest`" in opening
    assert "`--hiring-signals`" in opening


def test_synthesis_names_the_section_its_place_and_its_rules():
    synthesis = reference_text("synthesis")
    marker = "**Jobs Page Secret Signals.**"
    assert synthesis.count(marker) == 1
    gate = synthesis.split(marker, 1)[1].split("\n\n", 1)[0]
    assert "`## Jobs Digest`" in gate
    assert "`hiring-signals.md`" in gate
    assert "after the last story paragraph" in gate
    assert "before `KEY PATTERNS from the research:`" in gate
    # It sits with the other body rules, ahead of the footer steps.
    assert synthesis.index("**Pitch-vs-pulse beat") < synthesis.index(marker)
    assert synthesis.index(marker) < synthesis.index("**THEN - Quality Nudge")


# --- what the engine hands over ------------------------------------------------------


def test_reference_describes_the_digest_as_evidence_only():
    text = reference_text("hiring-signals")
    digest = text.split("## What the engine prints\n", 1)[1].split("\n## ", 1)[0]
    for field in ("open-role count", "posted date", "posting URL", "department", "location"):
        assert field in digest, field
    assert "up to 40" in digest
    assert "untrusted" in digest
    assert "decides nothing" in digest


def test_no_digest_means_no_section_so_unowned_boards_never_feed_it():
    gate = _rule("**No digest, no section.**")
    assert "only for a board the company owns" in gate
    assert "`## Hiring Signals`" in gate
    assert "memory" in gate


# --- the bar and the floor -----------------------------------------------------------


def test_the_bar_has_four_tests_and_one_qualifying_rule():
    bar = _rule("**The bar.**")
    for number, name in enumerate(
        (
            "Out of character.",
            "Names something concrete.",
            "Large and hard to undo.",
            "Bears on a story in this report.",
        ),
        start=1,
    ):
        assert f"{number}. **{name}**" in bar, name
    assert "product, unit, place, counterparty, technology or licence" in bar
    assert (
        "A signal qualifies when it meets at least two of tests 1 to 3, or when it meets test 4."
        in bar
    )


def test_the_bar_carries_one_passing_one_failing_and_one_story_example():
    bar = _rule("**The bar.**")
    assert "Gas Supply and Transport Lead" in bar
    assert "SMB account executive" in bar
    assert "67 open roles based in China" in bar


def test_two_signal_floor_with_the_hiring_signals_exemption_and_the_cap():
    floor = _rule("**The floor.**")
    assert "Write the section only when at least two signals qualify." in floor
    assert "A `--hiring-signals` run is exempt from this floor" in floor
    assert "At most five signals" in floor


# --- the web check ------------------------------------------------------------------


def test_web_check_precedes_any_unreported_or_secret_wording():
    check = _rule("**Check the web first.**")
    assert "Isolated web search" in check
    assert "`research-runbook.md`" in check
    assert re.search(r"Before a signal is described as unreported[^.]*a secret", check)
    assert "Never call a signal unreported without that check." in check


def test_section_is_omitted_when_the_host_cannot_search():
    assert "If the host has no web search, omit the section." in _rule("**Check the web first.**")


def test_public_but_missing_signals_are_worded_as_public():
    check = _rule("**Check the web first.**")
    assert '"already announced"' in check
    assert '"public, but not in this month\'s conversation"' in check
    assert "never as unreported or as a secret" in check


# --- format --------------------------------------------------------------------------


def test_format_is_a_lead_in_then_one_list_free_paragraph_per_signal():
    fmt = _rule("**Format.**")
    assert "`**Jobs Page Secret Signals** - `" in fmt
    assert "as the engine printed them" in fmt
    assert "one short paragraph per signal" in fmt
    assert "no bullets, no numbered list, no `##` header" in fmt


def test_each_signal_is_quoted_named_and_linked_as_printed():
    fmt = _rule("**Format.**")
    assert "quotes the posting's own words" in fmt
    assert "names the role" in fmt
    assert "exactly as the digest printed it" in fmt


def test_section_closes_with_the_exact_disclaimer():
    assert f'"{CLOSING}"' in _rule("**Format.**")


def test_format_example_is_list_free_and_ends_on_the_disclaimer():
    example = _section().split("```\n", 1)[1].split("\n```", 1)[0]
    lines = [line for line in example.splitlines() if line.strip()]
    assert lines[0].startswith("**Jobs Page Secret Signals** - ")
    assert lines[-1] == CLOSING
    for line in lines:
        assert not re.match(r"\s*(?:[-*•]\s|\d+[.)\\]|#)", line), line
    assert len(lines) >= 4, "example shows fewer than two signal paragraphs"


def test_standout_may_be_a_key_pattern_and_the_footer_stays_the_engines():
    extra = _rule("**Key patterns and footer.**")
    assert "all of tests 1 to 3" in extra
    assert "one KEY PATTERNS line" in extra
    assert "Never add a signal count to the footer" in extra


def test_section_stays_out_of_comparison_recommendations_and_exec():
    scope = _rule("**Where it is left out.**")
    for name in ("COMPARISON", "RECOMMENDATIONS", "`exec`"):
        assert name in scope, name


# --- the labelled set ----------------------------------------------------------------


def test_labelled_set_covers_the_four_boards():
    assert set(BOARDS) == {"Anthropic", "Stripe", "SpaceX", "Nvidia"}


@pytest.mark.parametrize("company", sorted(BOARDS))
def test_labels_are_well_formed(company):
    board = BOARDS[company]
    assert board["expected_section"] in {"present", "absent"}
    for candidate in board["candidates"]:
        assert candidate["role"]
        assert set(candidate["tests"]) <= {1, 2, 3, 4}, candidate["role"]
        assert candidate["public"] in {True, False, None}, candidate["role"]
        assert candidate["expected"] in {"qualifies", "does not qualify"}, candidate["role"]
        assert candidate["quote"] is None or candidate["quote"].strip(), candidate["role"]
        if rule.BEARS_ON_STORY in candidate["tests"]:
            assert board["report_story"], f"{candidate['role']} bears on a story the board lacks"


@pytest.mark.parametrize("company", sorted(BOARDS))
def test_each_labelled_signal_gets_its_expected_outcome(company):
    for candidate in BOARDS[company]["candidates"]:
        expected = candidate["expected"] == "qualifies"
        assert rule.qualifies(candidate["tests"]) is expected, candidate["role"]


@pytest.mark.parametrize("company", sorted(BOARDS))
def test_each_labelled_board_gets_its_expected_section(company):
    board = BOARDS[company]
    expected = board["expected_section"] == "present"
    assert rule.section_present(board["candidates"]) is expected


def test_expected_signals_per_board():
    def kept(company):
        return [c["role"] for c in rule.qualifying(BOARDS[company]["candidates"])]

    assert kept("Anthropic") == [
        "Gas Supply and Transport Lead, US and Canada",
        "Hardware Systems Architect",
        "M&A Tax Director",
        "Public Sector Sales Lead, France",
    ]
    assert kept("Stripe") == ["Engineering Manager, Money as a Service (MaaS)"]
    assert kept("SpaceX") == [
        "Supervisor, Hospitality (Moon Corps)",
        "Starmind roles",
        "Sr. Core Integration Engineer, Starlink Mobile",
    ]
    assert len(kept("Nvidia")) == 2


def test_stripe_has_one_signal_so_only_a_hiring_signals_run_shows_it():
    stripe = BOARDS["Stripe"]["candidates"]
    assert rule.signals_shown(stripe) == 0
    assert rule.signals_shown(stripe, hiring_signals_run=True) == 1


@pytest.mark.parametrize("company", sorted(BOARDS))
def test_no_board_gets_a_section_without_web_search(company):
    candidates = BOARDS[company]["candidates"]
    assert not rule.section_present(candidates, web_search=False)
    assert not rule.section_present(candidates, web_search=False, hiring_signals_run=True)


def test_starmind_is_kept_but_worded_as_public():
    spacex = {c["role"]: c for c in BOARDS["SpaceX"]["candidates"]}
    assert rule.qualifies(spacex["Starmind roles"]["tests"])
    assert rule.wording(spacex["Starmind roles"]) == "public"
    assert not rule.standout(spacex["Starmind roles"])
    assert rule.wording(spacex["Supervisor, Hospitality (Moon Corps)"]) == "unreported"
    assert rule.standout(spacex["Supervisor, Hospitality (Moon Corps)"])
    assert not rule.qualifies(spacex["SpaceXSI roles"]["tests"])


def test_nvidia_china_roles_qualify_only_through_the_report_story():
    nvidia = BOARDS["Nvidia"]
    china = nvidia["candidates"][0]
    assert china["tests"] == [4]
    assert "China" in nvidia["report_story"]
    assert rule.qualifies(china["tests"])
    assert not rule.qualifies([])


def test_rule_arithmetic_at_the_edges():
    assert not rule.qualifies([1])
    assert not rule.qualifies([3])
    assert rule.qualifies([1, 3])
    assert rule.qualifies([4])
    assert rule.qualifies([2, 4])
    seven = [{"tests": [1, 2, 3], "public": False}] * 7
    assert rule.signals_shown(seven) == rule.MAX_SIGNALS
    assert rule.wording({"public": None}) == "check first"
    assert not rule.standout({"tests": [1, 2, 3], "public": None})
