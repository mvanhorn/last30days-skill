"""Engagement counts select evidence; they are not printed beside every quote.

These pin the instruction text only. They catch the old "include the vote
count" instruction coming back; they cannot catch a model that prints counts
anyway, which needs a fresh run of the skill on a busy topic.
"""

from pathlib import Path

from tests.skill_contract import reference_text


SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "SKILL.md"
RULE_MARKER = "**COUNTS IN PROSE"


def _rule() -> str:
    return reference_text("synthesis").split(RULE_MARKER, 1)[1].split("\n\n", 1)[0]


def test_synthesis_no_longer_asks_for_a_count_beside_each_quote():
    text = reference_text("synthesis")
    assert "including the vote count" not in text
    assert '"N upvotes" for Reddit' not in text
    top_comments = text.split("Pay special attention to top comments", 1)[1].splitlines()[0]
    assert "do not print the count" in top_comments


def test_synthesis_states_when_a_count_may_stay():
    rule = _rule()
    for kept in ("comparison", "contrast", "surprise"):
        assert kept in rule
    assert "rank word" in rule
    assert "rounded and single" in rule


def test_rule_leaves_odds_registers_and_footer_alone():
    rule = _rule()
    assert "Polymarket" in rule
    assert "`creator`" in rule
    assert "`exec`" in rule
    assert "footer" in rule


def test_instruction_examples_do_not_model_tacked_on_counts():
    text = reference_text("synthesis")
    assert "1,338-upvote" not in text
    assert "flow, 800 upvotes`" not in text
    assert "323pt HN`" not in text


def test_comparison_template_points_at_the_rule():
    text = reference_text("comparison")
    do_not = text.split("**Do NOT:**", 1)[1].split("\n\n", 1)[0]
    assert "COUNTS IN PROSE" in do_not
    assert "with its engagement" not in text


def test_final_check_strips_counts_that_are_not_the_finding():
    text = SKILL_MD.read_text(encoding="utf-8")
    checklist = text.split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n## ", 1)[0]
    assert "Engagement counts select evidence" in checklist
    assert "size is the finding" in checklist
    assert "COUNTS IN PROSE exceptions" in checklist
