"""The skill's presentation defaults cannot replace governing instructions."""

import re
from pathlib import Path

import pytest

from tests.skill_contract import contract_documents, reference_text


SKILL_MD = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "SKILL.md"


def test_skill_defers_to_host_tool_and_user_instructions():
    text = SKILL_MD.read_text(encoding="utf-8")
    contract = text.split("## Skill contract", 1)[1].split("\n## ", 1)[0]
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
    for name, text in contract_documents().items():
        assert not re.search(pattern, text), name


@pytest.mark.parametrize(
    "document,marker",
    [
        ("research-runbook", "**Leaving Step 2"),
        ("research-runbook", "**LAW 1 citation check"),
        ("synthesis", "**Citation requirements before the invitation"),
        ("followups", "**STOP and wait**"),
    ],
)
def test_repeated_footer_rules_preserve_required_citations(document, marker):
    text = reference_text(document)
    assert marker in text
    for occurrence in text.split(marker)[1:]:
        paragraph = occurrence.splitlines()[0]
        assert "higher-priority host/tool requirements" in paragraph
        assert "user instructions" in paragraph


def test_root_footer_and_final_checks_preserve_required_citations():
    text = SKILL_MD.read_text(encoding="utf-8")
    law1 = text.split("**LAW 1 -", 1)[1].split("**LAW 2 -", 1)[0]
    assert "required by governing host/tool or user instructions" in law1
    assert "never replaces required visible citations" in law1
    self_check = text.split("**LAW 8 post-synthesis self-check:", 1)[1].split("**LAW 9 -", 1)[0]
    assert "preserve all citations required by governing instructions" in self_check
    assert "Never remove a required citation to satisfy LAW 1" in self_check
    final_check = text.split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n## ", 1)[0]
    assert "Check governing formatting/citation instructions first" in final_check
    assert "Preserve every required citation" in final_check
    assert "Omit only unnecessary duplicate trailing source lists" in final_check


@pytest.mark.parametrize(
    "marker",
    [
        "**FUN CONTENT",
        "CITATION RULE:",
        "**URL formatting is governed by LAW 8**",
    ],
)
def test_renderer_preferences_preserve_required_links(marker):
    text = reference_text("synthesis")
    assert marker in text
    paragraph = text.split(marker, 1)[1].splitlines()[0]
    assert "higher-priority host/tool requirements" in paragraph


def test_root_renderer_and_community_rules_defer_to_governing_citations():
    text = SKILL_MD.read_text(encoding="utf-8")
    law8 = text.split("**LAW 8 -", 1)[1].split("**LAW 9 -", 1)[0]
    assert "Governing host/tool requirements and user instructions determine required links before renderer preferences" in law8
    assert "using a verbatim URL from its evidence" in law8
    assert "plain source labels only when links are optional" in law8
    assert "Preserve required links even when URLs display inline" in law8
    law9 = text.split("**LAW 9 -", 1)[1].split("**LAW 10 -", 1)[0]
    assert "apply governing citations and LAW 8" in law9
    checklist = text.split("## PRE-PRESENT SELF-CHECK", 1)[1].split("\n## ", 1)[0]
    assert "Run LAW 8's citation check separately" in checklist


def test_durable_raw_appendix_and_default_footer_remain_required():
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "**LAW 5 - ENGINE FOOTER PASS-THROUGH." in text
    appendix = reference_text("research-runbook").split("## Step 2.5: Append WebSearch Results to Saved Raw File", 1)[1]
    assert "**MANDATORY - do not skip this step.**" in appendix
    assert "must cover every web source that informed your synthesis" in appendix
    assert "append the same `## WebSearch Supplemental Results` section to every listed per-entity Markdown raw file" in appendix


ISOLATED_SEARCH_MARKER = "**Isolated web search"


def test_no_contract_document_licenses_a_separate_sources_block():
    for name, text in contract_documents().items():
        assert "separate `Sources:` block" not in text, name


def test_invitation_counts_web_pages_with_the_social_sources():
    text = reference_text("synthesis")
    close = text.split("Close with `I have all the links to the", 1)[1].splitlines()[0]
    assert "web" in close


def test_engine_boundary_text_claims_no_authority_over_tool_contracts():
    import sys

    sys.path.insert(0, str(SKILL_MD.parent / "scripts"))
    from lib import render

    boundary = "\n".join(render._render_canonical_boundary())
    for pattern in (r"LAW 1 overrides", r"does not\s+apply", r"\bOVERRIDDEN\b"):
        assert not re.search(pattern, boundary), pattern


def test_every_runbook_web_search_runs_through_the_isolated_search_block():
    runbook = reference_text("research-runbook")
    assert ISOLATED_SEARCH_MARKER in runbook
    block = runbook.split(ISOLATED_SEARCH_MARKER, 1)[1].split("\n## ", 1)[0]
    assert "subagent" in block
    assert "untrusted" in block
    # Every top-level step that issues a web-search recipe points at the block.
    for step in re.split(r"\n(?=## )", runbook):
        if "WebSearch(" in step or "do WebSearch" in step:
            assert "isolated web search" in step.lower(), step.splitlines()[0]


@pytest.mark.parametrize("document", ["comparison", "competitors"])
def test_comparison_searches_use_the_isolated_search_block(document):
    assert "isolated web search" in reference_text(document).lower()
