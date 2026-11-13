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
from tests.skill_contract import ROUTES, reference_text, root_text, routing_table

CONDITION = "Hiring intent or an engine ## Hiring Signals, ## Jobs Digest or ## Patent Digest block"
ROUTED = ("hiring-signals", "patent-signals")
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
    # One row routes the jobs reference and the patent reference together.
    assert ROUTES[CONDITION] == ROUTED
    assert routing_table()[CONDITION] == ROUTED
    row = next(line for line in root_text().splitlines() if "`## Jobs Digest`" in line)
    assert row.startswith(
        "| Hiring intent or an engine `## Hiring Signals`, `## Jobs Digest` or `## Patent Digest` block "
        "| Read [references/hiring-signals.md](references/hiring-signals.md) and "
        "[references/patent-signals.md](references/patent-signals.md) in full. |"
    )
    assert "Apply the jobs lane and its scoped planning exception" in row
    opening = reference_text("hiring-signals").split("\n## ", 1)[0]
    assert "`## Jobs Digest`" in opening
    assert "`--hiring-signals`" in opening


def test_synthesis_names_the_section_its_place_and_its_rules():
    synthesis = reference_text("synthesis")
    marker = "**Jobs Page Secret Signals.**"
    assert synthesis.count(marker) == 1
    gate = synthesis.split(marker, 1)[1].split("\n\n", 1)[0]
    assert gate.strip() == (
        "When the engine output has a `## Jobs Digest` section, `hiring-signals.md` decides "
        "whether this section is written after the last story paragraph and before "
        "`KEY PATTERNS from the research:`. A `## Patent Digest` section means "
        "`patent-signals.md` decides whether a Patent Secret Signals section follows it. "
        "Most runs omit both."
    )
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


def test_at_most_two_signals_and_one_is_enough():
    count = _rule("**How many.**")
    assert "At most two signals in the section" in count
    assert "One qualifying signal is enough to write the section." in count
    assert "With none, write nothing about jobs in the body" in count
    assert "a cluster is one of the two" in count
    # The old floor is gone from the whole reference.
    text = reference_text("hiring-signals")
    for old in ("at least two signals qualify", "two-signal floor", "At most five signals", "**The floor.**"):
        assert old not in text, old
    assert rule.MIN_SIGNALS == 1 and rule.MAX_SIGNALS == 2


def test_a_cluster_of_three_or_more_postings_is_one_signal():
    cluster = _rule("**Clusters.**")
    assert "A cluster counts as one signal." in cluster
    assert "Three or more postings in the digest that share one specific, concrete focus" in cluster
    assert "a customer type, a place, a named program, or a function that is new for this company" in cluster
    assert "even when no single posting would" in cluster
    assert "out of character (test 1) or bears on a story in this report (test 4)" in cluster
    assert "Two postings are not a cluster" in cluster
    assert "General Accounts Federal and SLED team" in cluster
    assert rule.MIN_CLUSTER_POSTINGS == 3


def test_each_signal_says_what_is_hired_for_and_why_it_is_interesting():
    paragraph = _rule("**Each signal paragraph**")
    assert "two to three sentences" in paragraph
    assert "**What the company is hiring for.**" in paragraph
    assert "**Why it is interesting.**" in paragraph
    assert "what it suggests and why that matters this month" in paragraph
    assert "tied to a story in this report when one exists" in paragraph
    assert '"which suggests", "that points to"' in paragraph
    assert "never as an announced fact" in paragraph
    assert "say how many postings there are and cite two of them" in paragraph
    assert rule.CLUSTER_POSTINGS_CITED == 2 and rule.MAX_PARAGRAPH_SENTENCES == 3


def test_section_length_is_a_lead_in_two_short_paragraphs_at_most_and_the_closing_line():
    length = _rule("**Length.**")
    assert "at most two signal paragraphs of two to three sentences each" in length
    assert "Nothing more." in length
    assert rule.max_section_paragraphs() == 4


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
    assert fmt  # the lead-in rule is still there
    paragraph = _rule("**Each signal paragraph**")
    assert "quote the posting's own words" in paragraph
    assert "name the role" in paragraph
    assert "exactly as the digest printed it" in paragraph


def test_section_closes_with_the_exact_disclaimer():
    assert f'"{CLOSING}"' in _rule("**Format.**")


def test_format_example_is_list_free_and_ends_on_the_disclaimer():
    example = _section().split("```\n", 1)[1].split("\n```", 1)[0]
    lines = [line for line in example.splitlines() if line.strip()]
    assert lines[0].startswith("**Jobs Page Secret Signals** - ")
    assert lines[-1] == CLOSING
    for line in lines:
        assert not re.match(r"\s*(?:[-*•]\s|\d+[.)\\]|#)", line), line
    # Lead-in, a single-posting signal, a cluster signal, the closing line.
    assert len(lines) == rule.max_section_paragraphs()
    for paragraph in lines[1:-1]:
        sentences = [part for part in re.split(r"(?<=[.!?])\s+(?=[{A-Z])", paragraph) if part]
        assert 2 <= len(sentences) <= rule.MAX_PARAGRAPH_SENTENCES, paragraph
        assert re.search(r"points to|suggests", paragraph), paragraph
    assert lines[2].count("]({posting URL})") == rule.CLUSTER_POSTINGS_CITED


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


def test_labelled_set_covers_the_four_boards_and_the_nvidia_cluster():
    assert set(BOARDS) == {
        "Anthropic", "Stripe", "SpaceX", "Nvidia", "Nvidia, public sector cluster",
    }


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
        assert bool(rule.qualifying([candidate])) is expected, candidate["role"]
        if "cluster" in candidate:
            assert len(candidate["cluster"]) >= rule.MIN_CLUSTER_POSTINGS, candidate["role"]
            assert candidate["focus"], candidate["role"]


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


def test_stripe_has_one_signal_and_one_is_enough():
    stripe = BOARDS["Stripe"]["candidates"]
    assert rule.signals_shown(stripe) == 1
    assert rule.signals_shown(stripe, hiring_signals_run=True) == 1


def test_boards_with_more_qualifying_signals_show_two():
    for company in ("Anthropic", "SpaceX"):
        assert rule.signals_shown(BOARDS[company]["candidates"]) == 2


def test_nvidia_public_sector_cluster_is_one_signal_and_the_section_is_written():
    # A real "Jensen Huang" run got this digest and wrote no section: no
    # single posting qualified, and one signal was below the old floor.
    board = BOARDS["Nvidia, public sector cluster"]
    cluster, alone, _other = board["candidates"]
    assert len(cluster["cluster"]) == 4
    assert rule.qualifying(board["candidates"]) == [cluster]
    assert not rule.qualifies(alone["tests"])
    assert rule.section_present(board["candidates"])
    assert rule.signals_shown(board["candidates"]) == board["expected_signals"] == 1
    assert "Congress" in board["report_story"]


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
    assert rule.signals_shown(seven) == rule.MAX_SIGNALS == 2
    assert rule.signals_shown([{"tests": [], "public": None}]) == 0
    # Out of character alone: three postings sharing a focus qualify, two do not.
    assert rule.qualifies([1], postings=3)
    assert not rule.qualifies([1], postings=2)
    assert not rule.qualifies([2], postings=5)
    assert rule.wording({"public": None}) == "check first"
    assert not rule.standout({"tests": [1, 2, 3], "public": None})
