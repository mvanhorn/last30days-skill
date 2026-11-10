"""Engagement counts select evidence; one place prints them beside a quote.

Prose carries a count only when its size is the finding. The pulled quote, one
blockquote line after a story paragraph, is the named exception: it shows the
comment's author and count as the engine printed them.

These pin the instruction text only. They catch the old "include the vote
count" instruction coming back; they cannot catch a model that prints counts
anyway, which needs a fresh run of the skill on a busy topic.
"""

from pathlib import Path

from tests.skill_contract import reference_text


SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "SKILL.md"
RULE_MARKER = "**COUNTS IN PROSE"
PULLED_MARKER = "**PULLED QUOTE"


def _rule() -> str:
    return reference_text("synthesis").split(RULE_MARKER, 1)[1].split("\n\n", 1)[0]


def _pulled_quote_rule() -> str:
    return reference_text("synthesis").split(PULLED_MARKER, 1)[1].split("\n\n", 1)[0]


def _law9() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    return text.split("**LAW 9 -", 1)[1].split("**LAW 10 -", 1)[0]


def test_synthesis_no_longer_asks_for_a_count_beside_each_quote():
    text = reference_text("synthesis")
    assert "including the vote count" not in text
    assert '"N upvotes" for Reddit' not in text
    top_comments = text.split("Pay special attention to top comments", 1)[1].splitlines()[0]
    assert "Votes pick the quote" in top_comments
    assert "its count appears only on a PULLED QUOTE line" in top_comments
    assert "do not print the count" not in text
    assert "Quote the text, not its vote count" not in text


def test_synthesis_states_when_a_count_may_stay():
    rule = _rule()
    for kept in ("comparison", "contrast", "surprise"):
        assert kept in rule
    assert "rank word" in rule
    assert "A prose count that stays is rounded and single" in rule


def test_rule_leaves_odds_registers_and_footer_alone():
    rule = _rule()
    assert "Polymarket" in rule
    assert "`creator`" in rule
    assert "`exec`" in rule
    assert "footer" in rule


def test_prose_rule_stays_beside_the_pulled_quote():
    rule = _rule()
    assert "engagement numbers choose what you show, rarely what you say" in rule
    assert "print a count only when its size is the finding" in rule
    assert PULLED_MARKER not in rule


def test_pulled_quote_is_the_named_exception_with_one_line_per_story():
    rule = _pulled_quote_rule()
    assert rule.startswith(" (the named exception to COUNTS IN PROSE).**")
    assert "at most one comment" in rule
    assert "its own blockquote line" in rule
    assert "directly" in rule
    assert '`> "Ofc shes literally him😭" 🎵 @haktimel2 on TikTok · 825,451 likes`' in rule


def test_pulled_quote_copies_the_engine_attribution_and_carries_no_link():
    rule = _pulled_quote_rule()
    assert "as the engine printed them" in rule
    for part in ("dot", "author", "`on r/sub`", "count"):
        assert part in rule
    assert "no link" in rule
    assert "](" not in rule


def test_pulled_quote_marks_a_translated_comment():
    rule = _pulled_quote_rule()
    assert "not in the report's language" in rule
    assert "` · translated from Portuguese`" in rule


def test_pulled_quote_skips_authorless_comments_and_stories_without_one():
    rule = _pulled_quote_rule()
    assert "attributed `Comment`" in rule
    assert "none fits, no quote" in rule
    assert "comment or X post" in rule


def test_pulled_quote_stays_out_of_fixed_shape_reports():
    rule = _pulled_quote_rule()
    for shape in ("`exec`", "RECOMMENDATIONS", "COMPARISON"):
        assert shape in rule.rsplit("Not in", 1)[1]


def test_pulled_quote_rule_adds_no_markdown_list():
    for line in _pulled_quote_rule().splitlines():
        assert not line.lstrip().startswith(("- ", "* ", "1. "))


def test_fun_content_allows_a_translated_or_pulled_quote():
    text = reference_text("synthesis")
    fun = text.split("**FUN CONTENT (see LAW 9)", 1)[1].split("\n\n", 1)[0]
    assert "VERBATIM or translated" in fun
    assert "Weave it in or pull it; no separate section." in fun


def test_root_law_counts_pulled_quotes_as_woven_comments():
    law9 = _law9()
    assert "weave at least two attributed comments, verbatim or translated, into the narrative or as pulled quotes" in law9
    assert "Do not create a separate Comments section." in law9


def test_instruction_examples_do_not_model_tacked_on_counts():
    text = reference_text("synthesis")
    assert "1,338-upvote" not in text
    assert "flow, 800 upvotes`" not in text
    assert "323pt HN`" not in text


def test_comparison_template_points_at_the_rule():
    text = reference_text("comparison")
    do_not = text.split("**Do NOT:**", 1)[1].split("\n\n", 1)[0]
    assert "COUNTS IN PROSE" in do_not
    assert "or add pulled-quote lines" in do_not
    assert "with its engagement" not in text


def test_final_check_strips_counts_that_are_not_the_finding():
    text = SKILL_MD.read_text(encoding="utf-8")
    checklist = text.split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n## ", 1)[0]
    assert "Engagement counts select evidence" in checklist
    assert "size is the finding" in checklist
    assert "COUNTS IN PROSE exceptions" in checklist
