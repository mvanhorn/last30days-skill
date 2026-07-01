"""Security-copy contract tests for local reads and credential destinations."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGURATION = ROOT / "CONFIGURATION.md"
README = ROOT / "README.md"
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"
UI_PY = ROOT / "skills" / "last30days" / "scripts" / "lib" / "ui.py"


def test_cookie_setup_requires_explicit_allow_flag_in_docs():
    config = CONFIGURATION.read_text(encoding="utf-8")
    skill = SKILL_MD.read_text(encoding="utf-8")
    assert "setup --allow-browser-cookies" in config
    assert "setup --allow-browser-cookies" in skill
    assert "Unset = no browser-cookie reads" in config


def test_project_config_trust_is_documented():
    config = CONFIGURATION.read_text(encoding="utf-8")
    skill = SKILL_MD.read_text(encoding="utf-8")
    assert "LAST30DAYS_TRUST_PROJECT_CONFIG=1" in config
    assert "LAST30DAYS_TRUST_PROJECT_CONFIG=1" in skill
    assert "Folder-mode hosts such as Codex desktop do not trust hidden project config by default" in config


def test_codex_auth_not_advertised_as_openai_fallback():
    config = CONFIGURATION.read_text(encoding="utf-8")
    assert "Codex ChatGPT auth" in config
    assert "intentionally not used" in config
    assert "or Codex auth" not in config


def test_preflight_permission_contract_is_documented():
    config = CONFIGURATION.read_text(encoding="utf-8")
    skill = SKILL_MD.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    for text in (config, skill, readme):
        assert "--preflight" in text
    assert "without reading browser cookies, writing setup/config/report files, or running research" in config
    assert "does not read browser-cookie values" in skill
    assert "without reading cookies, writing files, or running research" in readme


def test_security_copy_avoids_stale_cookie_and_endpoint_claims():
    skill = SKILL_MD.read_text(encoding="utf-8")
    assert "no browser session access" not in skill
    assert "OpenAI key only goes to api.openai.com" not in skill
    assert "pass `--agent` for non-interactive report output" not in skill
    assert "Codex ChatGPT auth" in skill
    assert "Endpoint destinations follow configured provider base URLs" in skill
    assert "do not read browser-cookie values" in skill


def test_scrapecreators_copy_uses_canonical_free_call_count():
    text = "\n".join(
        [
            CONFIGURATION.read_text(encoding="utf-8"),
            README.read_text(encoding="utf-8"),
            SKILL_MD.read_text(encoding="utf-8"),
            UI_PY.read_text(encoding="utf-8"),
        ]
    )
    assert "10,000 free calls" in text
    assert "100 free credits" not in text
    assert "1,000 free" not in text
