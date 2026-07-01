"""Documentation contract for Python CLI and wrapper-only flags."""

from __future__ import annotations

from pathlib import Path

import last30days as cli

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = ROOT / "CONFIGURATION.md"
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
HTML_REFERENCE = ROOT / "skills" / "last30days" / "references" / "save-html-brief.md"


def _parser_flags() -> set[str]:
    parser = cli.build_parser()
    flags: set[str] = set()
    for action in parser._actions:
        flags.update(action.option_strings)
    return flags


def test_configuration_documents_new_safety_flags():
    text = CONFIGURATION.read_text(encoding="utf-8")
    flags = _parser_flags()
    assert "--no-browser-cookies" in flags
    assert "--no-browser-cookies" in text
    assert "--preflight" in flags
    assert "--preflight" in text
    assert "--save-dir" in text
    assert "--output" in text
    assert "--publish-html" in flags
    assert "--publish-html" in text
    assert "--publish-password" in flags
    assert "--publish-password" in text
    assert "LAST30DAYS_PUBLISH_PASSWORD" in text


def test_html_publish_reference_prompts_for_password_choice():
    text = HTML_REFERENCE.read_text(encoding="utf-8")
    publish_section = text[text.index("## Optional hosted publishing"):text.index("## What ends up in the HTML file")]
    assert "Respect any existing user, project, or host preference for HTML publishing first" in publish_section
    assert "If multiple publishing options are available, show each as its own choice" in publish_section
    assert "label `ht-ml.app` as supporting optional password protection" in publish_section
    assert "Show the absolute saved path" in publish_section
    assert "Open HTML file" in publish_section
    assert "Done for now" in publish_section
    assert "Do not upload until the user chooses a publishing option" in publish_section
    assert "ask a second question" in publish_section
    assert "**Public link** - publish without a password" in publish_section
    assert "**Password-protected link** - ask the user to type the shared password" in publish_section
    assert "repeat the shared password they selected" in publish_section
    assert "LAST30DAYS_PUBLISH_PASSWORD" in publish_section
    assert '--output "$HTML_PATH"' in publish_section
    assert "<HTML_PATH>.publish.json" in publish_section


def test_reddit_backend_env_var_is_documented_for_users_and_runtime_skill():
    config_text = CONFIGURATION.read_text(encoding="utf-8")
    skill_text = SKILL_MD.read_text(encoding="utf-8")

    assert "LAST30DAYS_REDDIT_BACKEND=scrapecreators" in config_text
    assert "LAST30DAYS_REDDIT_BACKEND=scrapecreators" in skill_text


def test_save_is_not_documented_as_python_cli_flag():
    text = CONFIGURATION.read_text(encoding="utf-8")
    assert "--save-dir <path>" in text
    assert "--save " not in text
    assert "`--save`" not in text


def test_agent_is_documented_as_skill_argument_not_python_flag():
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.index("## Agent Mode (--agent flag)")
    agent_section = text[start:start + 2000]
    assert "If `--agent` appears in ARGUMENTS" in agent_section
    assert "slash-command skill contract" in text
    assert "not a Python CLI flag" in text


def test_html_reference_documents_structured_cache_reuse():
    text = HTML_REFERENCE.read_text(encoding="utf-8")
    assert "~/.config/last30days/last-report.json" in text
    assert "without re-running source fetchers" in text
    assert "No matching cached report data" in text
    assert "LAST30DAYS_REPORT_CACHE_TTL_SECONDS" in text
    assert "default: one hour" in text


def test_configuration_documents_report_cache_ttl():
    text = CONFIGURATION.read_text(encoding="utf-8")
    assert "LAST30DAYS_REPORT_CACHE_TTL_SECONDS" in text
    assert "defaults to `3600`" in text
    assert "`0` to disable report-cache reuse" in text


def test_comparison_artifact_contract_documents_actual_paths():
    text = SKILL_MD.read_text(encoding="utf-8")
    comparison_start = text.index("\n## If QUERY_TYPE = COMPARISON\n")
    comparison_section = text[comparison_start:comparison_start + 5000]
    assert "there is no separate merged Markdown raw file" in comparison_section
    assert "[last30days] Comparison artifact set: main={path}; peers={path, ...}" in comparison_section
    assert "Treat that log line as authoritative" in comparison_section

    step_start = text.index("## Step 2.5: Append WebSearch Results to Saved Raw File")
    step_section = text[step_start:step_start + 3500]
    assert "append the same `## WebSearch Supplemental Results` section to every listed per-entity Markdown raw file" in step_section
    assert "do not append Markdown text to `.html` or `.json`" in step_section
