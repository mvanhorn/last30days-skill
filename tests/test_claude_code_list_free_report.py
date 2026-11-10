"""Claude Code reports avoid markdown lists so streamed links stay clickable.

While a markdown list streams, Claude Code can redraw every link in the reply
as ``label (url)``. In live terminal trials the list format did this in 2 of 9
runs and the list-free format in 0 of 15. These pin the instruction only.
"""

from tests.skill_contract import reference_text

MARKER = "**NO MARKDOWN LISTS ON CLAUDE CODE.**"


def test_rule_is_scoped_to_claude_code_and_names_both_replacements():
    rule = reference_text("synthesis").split(MARKER, 1)[1].split("\n\n", 1)[0]
    assert "`CLAUDECODE`" in rule
    assert "`1\\.`" in rule
    assert "`• `" in rule
    assert "Other hosts keep normal lists" in rule


def test_comparison_template_applies_the_rule():
    text = reference_text("comparison")
    assert "NO MARKDOWN LISTS applies here too" in text
