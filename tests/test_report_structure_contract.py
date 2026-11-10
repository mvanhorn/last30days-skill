"""The paragraph headline phrase is report structure, not a formatting default."""

from tests.skill_contract import reference_text, root_text


GENERAL_PREFERENCE = "general style preference alone does not remove it"


def _output_contract_intro():
    section = root_text().split("## Output contract\n", 1)[1]
    return section.split("**BADGE", 1)[0]


def _law(number):
    text = root_text()
    return text.split(f"**LAW {number} -", 1)[1].split(f"**LAW {number + 1} -", 1)[0]


def _synthesis_headline_rule():
    marker = "**MANDATORY - bold headline per narrative paragraph.**"
    text = reference_text("synthesis")
    assert marker in text
    return text.split(marker, 1)[1].splitlines()[0]


def test_root_contract_names_the_three_cases_that_remove_the_headline():
    intro = _output_contract_intro()
    assert "headline phrase is report structure" in intro
    assert "naming this report or arriving with the request" in intro
    assert "a host or operator requirement" in intro
    assert "a channel that does not render markdown" in intro


def test_general_style_preference_alone_keeps_the_headline():
    assert GENERAL_PREFERENCE in _output_contract_intro()
    assert GENERAL_PREFERENCE in _synthesis_headline_rule()


def test_user_preference_sentence_is_qualified_by_the_structure_rule():
    intro = _output_contract_intro()
    preference = "User formatting preferences, including host memory, take precedence"
    assert preference in intro
    following = intro.split(preference, 1)[1]
    assert following.lstrip(" .").startswith("The paragraph headline phrase is report structure")


def test_root_laws_and_self_check_do_not_make_the_headline_a_formatting_default():
    law2 = _law(2)
    assert "lead-ins where governing formatting permits" not in law2
    assert "headline phrases are structure" in law2
    assert "bold-lead-in paragraphs" in _law(4)
    self_check = root_text().split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n1. ", 1)[0]
    assert "Skip a conflicting presentation default" in self_check
    assert "headline phrases are structure" in self_check


def test_synthesis_agrees_with_the_root_contract():
    rule = _synthesis_headline_rule()
    assert "report structure" in rule
    assert "root output contract" in rule
    opening = reference_text("synthesis").split("\n## ", 1)[0]
    assert "Follow governing host/tool and user instructions before these presentation defaults" in opening
