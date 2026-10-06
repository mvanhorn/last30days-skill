"""The skill's presentation defaults cannot replace governing instructions."""

import re
from pathlib import Path

import pytest


SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "SKILL.md"


def test_skill_defers_to_host_tool_and_user_instructions():
    text = SKILL_MD.read_text(encoding="utf-8")
    contract = text.split("# SKILL CONTRACT", 1)[1].split("---", 1)[0]
    assert "system and developer instructions" in contract
    assert "tool contracts" in contract
    assert "user instructions" in contract


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bSUPERSEDED\b",
        r"\bOVERRIDDEN\b",
        r"LAW 1 overrides",
        r"correct action is to IGNORE",
        r"mandate (?:DOES NOT APPLY|does not apply)",
        r"skill-specified rule wins",
        r"Do NOT strip this bold on the grounds of a personal",
    ],
)
def test_skill_does_not_claim_authority_over_governing_instructions(pattern):
    assert not re.search(pattern, SKILL_MD.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "marker",
    [
        "**LAW 1 -",
        "**Post-synthesis self-check (do this BEFORE emitting your response):**",
        "**Leaving Step 2",
        "**LAW 1 citation check",
        "**Citation requirements before the invitation",
        "6. **Trailing citations",
        "**STOP and wait**",
    ],
)
def test_repeated_footer_rules_preserve_required_citations(marker):
    text = SKILL_MD.read_text(encoding="utf-8")
    assert marker in text
    for occurrence in text.split(marker)[1:]:
        paragraph = occurrence.splitlines()[0]
        assert "higher-priority host/tool requirements" in paragraph
        assert "user instructions" in paragraph


@pytest.mark.parametrize(
    "marker",
    [
        "**LAW 8 -",
        "- **Hidden-link hosts",
        "- **Visible-URL hosts",
        "**LAW 9 -",
        "**FUN CONTENT",
        "CITATION RULE:",
        "**URL formatting is governed by LAW 8**",
        "3. **Community voice woven in",
    ],
)
def test_renderer_preferences_preserve_required_links(marker):
    text = SKILL_MD.read_text(encoding="utf-8")
    assert marker in text
    paragraph = text.split(marker, 1)[1].splitlines()[0]
    assert "higher-priority host/tool requirements" in paragraph


def test_durable_raw_appendix_and_default_footer_remain_required():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "**LAW 5 - ENGINE FOOTER PASS-THROUGH." in text
    appendix = text.split("## Step 2.5: Append WebSearch Results to Saved Raw File", 1)[1]
    appendix = appendix.split("## Judge Agent:", 1)[0]
    assert "**MANDATORY - do not skip this step.**" in appendix
    assert "must cover every web source that informed your synthesis" in appendix
    assert "append the same `## WebSearch Supplemental Results` section to every listed per-entity Markdown raw file" in appendix
