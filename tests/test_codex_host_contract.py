"""Host-contract tests for non-modal agent runtimes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


def _prose_flow() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    start_marker = "### Non-Modal Prose Flow"
    end_marker = "### Manual Setup Guide"
    start = text.find(start_marker)
    assert start != -1, f"missing section marker: {start_marker}"
    end = text.find(end_marker, start)
    assert end != -1, f"missing section marker: {end_marker}"
    return text[start:end]


def test_non_modal_hosts_are_named():
    prose = _prose_flow()
    for host in ("Codex", "Cursor", "Gemini CLI", "raw CLI"):
        assert host in prose


def test_non_modal_cookie_consent_uses_engine_allow_flag():
    prose = _prose_flow()
    consent = prose.index("Cookie consent")
    allow = prose.index("setup --allow-browser-cookies")
    decline = prose.index("FROM_BROWSER=off")
    assert consent < allow
    assert consent < decline


def test_non_modal_preflight_runs_before_cookie_consent():
    prose = _prose_flow()
    preflight = prose.index("--preflight")
    consent = prose.index("Cookie consent")
    assert preflight < consent
    assert "does not read browser-cookie values" in prose
    assert "does not write setup/config/report files" in prose
    assert "does not run research" in prose


def test_non_modal_completion_mentions_safe_diagnose_and_project_trust():
    prose = _prose_flow()
    assert "safe `--diagnose`" in prose
    assert "LAST30DAYS_TRUST_PROJECT_CONFIG=1" in prose
    assert "Codex desktop" in prose


def _step0_search_contract() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    start_marker = "**STEP 0 - RESOLVE HOST WEB SEARCH FIRST.**"
    end_marker = "**FIRST-RUN GATE"
    start = text.find(start_marker)
    assert start != -1, f"missing section marker: {start_marker}"
    end = text.find(end_marker, start)
    assert end != -1, f"missing section marker: {end_marker}"
    return text[start:end]


def test_host_web_search_uses_available_capability_not_specific_tool_name():
    step0 = _step0_search_contract()
    assert "usable web-search tool" in step0
    assert "built in, exposed as a deferred tool, or provided by an installed connector" in step0
    assert "Brave, Firecrawl, Exa, Serper" in step0
    assert "If your host requires loading, selecting, or enabling the web-search tool" in step0
    assert "Do not fail the skill just because one particular schema lookup or tool name is unavailable" in step0


def test_no_host_search_uses_auto_resolve_and_leaves_native_signal_unset():
    step0 = _step0_search_contract()
    assert "If no web-search tool is available in the agent session" in step0
    assert "--auto-resolve" in step0
    assert "LAST30DAYS_NATIVE_SEARCH=1" in step0
    assert "Leave it unset when the agent session has no web-search tool" in step0


def _law8_block() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.find("**LAW 8 -")
    assert start != -1, "missing LAW 8 marker"
    end = text.find("**LAW 9 -", start)
    assert end != -1, "missing LAW 9 marker (LAW 8 block end)"
    return text[start:end]


def test_law8_is_renderer_aware_with_both_regimes():
    # LAW 8 must keep the inline-link default for hidden-link hosts AND carry a
    # plain-label branch for visible-URL hosts. Codex rendered every inline link
    # as `label (https://...)`, so a single-renderer LAW 8 produced URL soup.
    law8 = _law8_block()
    # Hidden-link hosts are Claude Code AND Grok Bot / Cursor agent chat: the
    # 2026-09-01 Grok Bot brief showed Cursor agent chat hides markdown URLs
    # like Claude Code, so lumping it with Codex produced unclickable cites.
    assert "Hidden-link hosts (Claude Code; Grok Bot / Cursor agent chat)" in law8
    assert "Visible-URL hosts (Codex" in law8
    assert "URL soup" in law8
    # Hidden-link default must remain inline `[name](url)` (no Claude Code regression).
    assert "`[name](url)`" in law8


def test_law8_host_detection_is_deterministic_via_claudecode():
    law8 = _law8_block()
    assert "CLAUDECODE" in law8
    # CURSOR_AGENT is the second deterministic hidden-link signal (Grok Bot /
    # Cursor agent chat). Dropping it regresses those chats to plain labels.
    assert "CURSOR_AGENT" in law8
    assert "Grok Bot" in law8
    # The detection must be stated as deterministic, not left to the model guessing.
    assert "do not guess" in law8


def test_law8_visible_url_hosts_exclude_cursor_and_split_from_step0():
    # Grok Bot / Cursor agent chat hides markdown URLs, so Cursor must not be
    # named a visible-URL citation host anywhere. Cursor stays a NON-MODAL
    # SETUP host (see test_non_modal_hosts_are_named) - the citation renderer
    # is a different axis, and LAW 8 must not claim it is the Step 0 split.
    text = SKILL_MD.read_text(encoding="utf-8")
    visible_lists = re.findall(r"[Vv]isible-URL hosts? \(([^)]*)\)", text)
    assert visible_lists, "no visible-URL host list found"
    assert any("Codex" in hosts for hosts in visible_lists)
    for hosts in visible_lists:
        assert "Cursor" not in hosts, f"Cursor named as visible-URL host: {hosts!r}"
    law8 = _law8_block()
    assert "Gemini CLI, raw CLI" in law8
    assert "is the same split" not in law8


def test_law8_wrap_list_includes_u_name_and_github_repo_first_mentions():
    # u/name comment authors and GitHub repos must be in the wrap-every-citation
    # list, with URLs copied from the comment row / engine evidence block.
    law8 = _law8_block()
    assert "u/name" in law8
    assert "GitHub repo" in law8
    assert "never guess" in law8.lower()
    # GitHub evidence can carry an issue/PR URL, not the repo root: the label
    # must match what the URL opens - never `[owner/repo]` over an item URL,
    # and never an item URL trimmed to a guessed repo root.
    assert "a label that matches what that URL opens" in law8
    assert "never trim an item URL down to a guessed repo root" in law8


def test_law8_post_synthesis_self_check_branches_on_both_env_signals():
    # The post-synthesis self-check is the env-branching gate; it must branch
    # on CLAUDECODE or CURSOR_AGENT, and PRE-PRESENT is a supplemental sweep.
    law8 = _law8_block()
    start = law8.index("Post-synthesis self-check")
    self_check = law8[start:]
    assert "`CLAUDECODE` or `CURSOR_AGENT` set" in self_check
    assert "both `CLAUDECODE` and `CURSOR_AGENT` unset" in self_check
    assert "not a substitute" in self_check


def test_citation_renderer_host_list_is_mirrored_outside_law8():
    # LAW 9, FUN CONTENT, CITATION PRIORITY, and the PRE-PRESENT sweep must
    # carry the same renderer split - Grok Bot / Cursor agent chat hidden-link,
    # Codex/Gemini CLI/raw CLI visible-URL - so a chunked read of any one
    # section cannot resurrect "Cursor is visible-URL".
    text = SKILL_MD.read_text(encoding="utf-8")
    law9_start = text.index("**LAW 9 -")
    law9 = text[law9_start : text.index("**LAW 10 -", law9_start)]
    assert "hidden-link host (Claude Code; Grok Bot / Cursor agent chat)" in law9
    fun_start = text.index("**FUN CONTENT")
    fun = text[fun_start : fun_start + 2000]
    assert "Grok Bot / Cursor agent chat" in fun
    citation_start = text.index("**URL formatting is governed by LAW 8**")
    citation = text[citation_start : citation_start + 1500]
    assert "hidden-link hosts (Claude Code; Grok Bot / Cursor agent chat)" in citation
    assert "Codex/Gemini CLI/raw CLI" in citation
    pre_present_start = text.index("## PRE-PRESENT SELF-CHECK")
    pre_present = text[pre_present_start:]
    assert "`CLAUDECODE` or `CURSOR_AGENT` set" in pre_present


def test_plan_invocation_warns_against_bash_lc_apostrophe_wrapper():
    # Codex aborted its first engine run by wrapping the query-plan heredoc in
    # `bash -lc '...'`; the outer single quote ended at the first apostrophe in a
    # ranking string. The guidance must steer off that wrapper explicitly.
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "bash -lc '...'" in text
    assert "unmatched" in text


def test_host_json_invocations_use_stdin_without_forced_cleanup():
    # Codex rejects the whole shell command before execution when a mandatory
    # skill template contains an `rm -f` cleanup trap, even if its target came
    # from mktemp. Keep all host-authored JSON lanes temp-file-free.
    text = SKILL_MD.read_text(encoding="utf-8")
    for invocation in (
        "--plan -",
        "--competitors-plan -",
        "--judgments -",
        "--angles -",
    ):
        assert invocation in text
    assert "trap 'rm -f" not in text
    for legacy_temp_var in (
        "QUERY_PLAN_FILE",
        "COMPETITORS_PLAN_FILE",
        "JUDGMENTS_FILE",
        "ANGLES_FILE",
    ):
        assert legacy_temp_var not in text


def test_step055_documents_dedicated_vs_broad_subreddits():
    # Step 0.55 must instruct the model to split entity-home (dedicated) subs from
    # broad subs and pass them via --dedicated-subreddits, which the engine pulls
    # in full and exempts from the relevance floor.
    text = SKILL_MD.read_text(encoding="utf-8")
    assert "RESOLVED_DEDICATED_SUBREDDITS" in text
    assert "--dedicated-subreddits" in text
    assert "relevance floor" in text
