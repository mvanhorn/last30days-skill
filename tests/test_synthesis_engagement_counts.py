"""Engagement counts select evidence; one place prints them beside a quote.

Prose carries a count only when its size is the finding. The pulled quote, a
blockquote line under a story paragraph, is the named exception: it shows the
comment's author and count as the engine printed them. Every quoted comment
The report's best six quotes are such lines; other good quotes may stay
inside a paragraph, attributed and linked, with no count.

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
    assert "Position picks the quote" in top_comments
    assert "Votes pick the quote" not in text
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


def test_pulled_quote_is_the_named_exception_and_every_quote_is_one():
    rule = _pulled_quote_rule()
    assert rule.startswith(" (the named exception to COUNTS IN PROSE).**")
    assert "The report's best six quotes of a community comment, a post" in rule
    assert "the subject's own post" in rule
    assert "their own blockquote line directly under the paragraph they support" in rule
    assert '`> "Ofc shes literally him😭" 🎵 @haktimel2 on TikTok · 825,451 likes`' in rule


def test_other_good_quotes_may_stay_inline_without_a_quota_or_a_count():
    rule = _pulled_quote_rule()
    assert "Another good quote may stay inside its paragraph in quotation marks, author linked (LAW 8), with no count" in rule
    assert "allowed, not required" in rule
    assert "Never use one quote both ways" in rule
    assert "never quote weak or off-topic comments" in rule
    assert "None goes inside a paragraph" not in rule
    text = reference_text("synthesis") + SKILL_MD.read_text(encoding="utf-8")
    for gone in ("none inside a paragraph", "never inside a paragraph", "at least two", "at least 2"):
        assert gone not in text.replace("at least two signals", "")


def test_pulled_quotes_run_up_to_six_in_the_whole_report_with_a_quality_bar():
    rule = _pulled_quote_rule()
    assert "top comment first, then its #2" in rule
    assert "like counts do not rank a comment from one place against another" in rule
    assert "highest-voted" not in rule
    assert "Six in the whole report is a ceiling, not a target" in rule
    assert "the subject's standout posts, `funny` entries breaking ties" in rule
    assert "per story" not in rule and "each paragraph up to" not in rule
    assert "a blank line between" in rule
    for cap in ("at most one", "up to three", "up to five", "one per story"):
        assert cap not in rule
    text = reference_text("synthesis") + SKILL_MD.read_text(encoding="utf-8")
    assert "at most one comment" not in text
    assert "at least two attributed comments" not in text
    assert "weave at least 2" not in text


def test_pulled_quote_names_the_engine_blocks_it_copies_from():
    rule = _pulled_quote_rule()
    assert "`## Top Community Comments`" in rule
    assert "`## Posts by @handle`" in rule
    assert "the paragraph above keeps the source link" in rule


def test_pulled_quote_copies_the_engine_attribution_and_carries_no_link():
    rule = _pulled_quote_rule()
    assert "Copy the entry through its count (through the platform name when it has none)" in rule
    assert "up to its URL" not in rule
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


def test_pulled_quote_stays_out_of_fixed_shape_reports():
    rule = _pulled_quote_rule()
    for shape in ("`exec`", "RECOMMENDATIONS", "COMPARISON"):
        assert shape in rule.rsplit("Not in", 1)[1]


def test_pulled_quote_rule_adds_no_markdown_list():
    for line in _pulled_quote_rule().splitlines():
        assert not line.lstrip().startswith(("- ", "* ", "1. "))


def test_fun_content_sends_translated_or_verbatim_quotes_to_quote_lines():
    text = reference_text("synthesis")
    fun = text.split("**FUN CONTENT (see LAW 9)", 1)[1].split("\n\n", 1)[0]
    assert "quote the funniest/cleverest, VERBATIM or translated (PULLED QUOTE)" in fun
    assert "No separate section." in fun
    assert "eave" not in fun


def test_root_law_puts_every_quoted_comment_on_a_quote_line():
    law9 = _law9()
    assert "QUOTE THE COMMUNITY;" in law9
    assert "the report's best six quotes, verbatim or translated, each get a quote line" in law9
    assert "others may be quoted inline, attributed" in law9
    assert "synthesis PULLED QUOTE" in law9
    assert "Do not create a separate Comments section." in law9
    assert "eave" not in law9


def test_root_law_quotes_the_subjects_own_posts_on_quote_lines():
    text = SKILL_MD.read_text(encoding="utf-8")
    law10 = text.split("**LAW 10 -", 1)[1].split("**LAW 11 -", 1)[0]
    assert "`## Posts by @handle`" in law10
    assert "quote the standout ones" in law10


def test_final_check_looks_for_quotes_left_inside_paragraphs():
    text = SKILL_MD.read_text(encoding="utf-8")
    checklist = text.split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n## ", 1)[0]
    assert "At most six quote lines, the report's best; no quote both on a line and inline." in checklist
    assert "Weave" not in checklist


def test_report_template_shows_quote_lines_under_paragraphs():
    text = reference_text("synthesis")
    template = text.split("{the engine's first line, copied verbatim}", 1)[1].split("```", 1)[0]
    assert template.count('\n> "') >= 2
    assert "](" not in "".join(line for line in template.splitlines() if line.startswith("> "))


def test_runbook_describes_what_compact_output_really_holds():
    runbook = reference_text("research-runbook")
    assert "EIGHT data sections" not in runbook
    line = next(line for line in runbook.splitlines() if line.startswith("**Read the ENTIRE output.**"))
    for part in (
        "`## Posts by @handle`", "`## Polymarket Markets`", "`## Top Community Comments`",
        "top ranked clusters", "format checklist",
    ):
        assert part in line, part


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
