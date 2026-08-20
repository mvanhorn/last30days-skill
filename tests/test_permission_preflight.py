"""Tests for the safe permission preflight contract."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import last30days as cli
from lib import env, permission_preflight, pipeline

DEFAULT_SAVE_DIR = "~" + "/Documents/Last30Days"


def _diag(**overrides):
    base = {
        "providers": {
            "google": False,
            "openai": False,
            "xai": False,
            "openrouter": False,
            "perplexity": False,
        },
        "has_scrapecreators": False,
        "has_github": False,
        "available_sources": ["reddit", "hackernews", "polymarket"],
        "safe": True,
        "config_source": "env_only",
        "ignored_project_config": None,
        "ignored_project_config_keys": [],
        "ignored_endpoint_overrides": [],
        "browser_cookies": {"mode": "plan_only", "browsers": [], "reads_values": False},
        "external_commands": {"yt-dlp": False, "digg-pp-cli": False, "gh": False},
        "credential_destinations": {"global_env": None},
        "local_writes": [],
        "native_search": False,
    }
    base.update(overrides)
    return base


def _diag_with_preflight(config=None, **overrides):
    config = config or {}
    diag = _diag(**overrides)
    diag["permission_preflight"] = permission_preflight.build(config, diag)
    return diag


def test_preflight_reports_safe_browser_default_without_cookie_values():
    diag = _diag()
    preflight = permission_preflight.build({}, diag)

    assert preflight["status"] == "ready"
    assert preflight["action_items"] == []
    browser = preflight["local_reads"]["browser_cookies"]
    assert browser == {
        "status": "off",
        "mode": "plan_only",
        "browsers": [],
        "reads_values": False,
    }
    text = permission_preflight.render_text(preflight)
    assert "Browser cookies: off" in text
    assert "cookie values" not in text


def test_preflight_reports_configured_browser_without_reading_values():
    diag = _diag(
        browser_cookies={"mode": "plan_only", "browsers": ["firefox"], "reads_values": False}
    )
    preflight = permission_preflight.build({"FROM_BROWSER": "firefox"}, diag)

    browser = preflight["local_reads"]["browser_cookies"]
    assert browser["status"] == "enabled_by_config"
    assert browser["browsers"] == ["firefox"]
    assert browser["reads_values"] is False
    assert "preflight did not read cookie values" in permission_preflight.render_text(preflight)


def test_preflight_reports_conditional_report_on_save_without_definite_write():
    preflight = permission_preflight.build(
        {},
        _diag(),
        report_on_save_dir=DEFAULT_SAVE_DIR,
    )

    assert preflight["local_writes"] == []
    assert preflight["conditional_writes"] == [
        {"kind": "report_on_save", "path": DEFAULT_SAVE_DIR}
    ]
    rendered = permission_preflight.render_text(preflight)
    assert "none planned" in rendered
    assert f"Report (if saved): {DEFAULT_SAVE_DIR}" in rendered


def test_preflight_prefers_definite_save_dir_over_conditional_report_on_save(tmp_path):
    save_dir = tmp_path / "reports"
    preflight = permission_preflight.build(
        {},
        _diag(),
        planned_save_dir=str(save_dir),
        report_on_save_dir=DEFAULT_SAVE_DIR,
    )

    assert preflight["local_writes"] == [{"kind": "report", "path": str(save_dir)}]
    assert preflight["conditional_writes"] == []


def test_preflight_dedupes_env_save_dir_against_conditional_report_on_save():
    preflight = permission_preflight.build(
        {"LAST30DAYS_MEMORY_DIR": DEFAULT_SAVE_DIR},
        _diag(local_writes=[{"kind": "report", "path": DEFAULT_SAVE_DIR}]),
        report_on_save_dir=DEFAULT_SAVE_DIR,
    )

    assert preflight["local_writes"] == [{"kind": "report", "path": DEFAULT_SAVE_DIR}]
    assert preflight["conditional_writes"] == []


def test_preflight_project_config_not_active_is_not_trusted_with_trust_env():
    preflight = permission_preflight.build(
        {"LAST30DAYS_TRUST_PROJECT_CONFIG": "1"},
        _diag(config_source="env_only", ignored_project_config=None),
    )

    project = preflight["local_reads"]["project_config"]
    assert project["status"] == "not_active"
    assert project["trusted"] is False


def test_preflight_reports_ignored_project_config_without_secret_values(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text(
        "OPENAI_BASE_URL=https://attacker.example\nOPENAI_API_KEY=sk-not-reported\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-global")
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        config = env.get_config(
            policy=env.ConfigLoadPolicy(
                browser_cookies="plan_only",
                inspect_ignored_project_config=True,
            )
        )
    diag = pipeline.diagnose(config, safe=True)

    preflight = diag["permission_preflight"]
    assert preflight["local_reads"]["project_config"]["status"] == "ignored_untrusted"
    assert preflight["network"]["ignored_endpoint_overrides"] == ["OPENAI_BASE_URL"]
    rendered = permission_preflight.render_text(preflight)
    assert "Project config: ignored untrusted file" in rendered
    assert "OPENAI_API_KEY" in rendered
    assert "sk-not-reported" not in str(preflight)
    assert "sk-global" not in str(preflight)
    assert "sk-not-reported" not in rendered
    assert "sk-global" not in rendered


def test_diagnose_uses_preflight_endpoint_override_key_set():
    ignored_keys = sorted(permission_preflight.ENDPOINT_OVERRIDE_KEYS) + ["UNRELATED_KEY"]
    diag = pipeline.diagnose(
        {
            "_IGNORED_PROJECT_CONFIG_KEYS": ignored_keys,
            "_BROWSER_COOKIE_MODE": "off",
            "_BROWSER_COOKIE_BROWSERS": [],
        },
        safe=True,
    )

    assert sorted(diag["ignored_endpoint_overrides"]) == sorted(
        permission_preflight.ENDPOINT_OVERRIDE_KEYS
    )


def test_cli_preflight_uses_plan_only_policy_and_does_not_run_research(monkeypatch):
    seen: dict[str, object] = {}

    def fake_get_config(*, policy):
        seen["policy"] = policy
        return {"_BROWSER_COOKIE_MODE": policy.browser_cookies, "_BROWSER_COOKIE_BROWSERS": []}

    with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=_diag_with_preflight()) as diagnose, \
         mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("research should not run")), \
         mock.patch.object(sys, "argv", ["last30days.py", "--preflight"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert seen["policy"].browser_cookies == "plan_only"
    assert seen["policy"].inspect_ignored_project_config is True
    diagnose.assert_called_once()
    assert "last30days preflight" in stdout.getvalue()
    assert "Local writes:" in stdout.getvalue()


def test_cli_preflight_reuses_embedded_preflight_without_save_overrides(monkeypatch):
    embedded = permission_preflight.build({}, _diag())
    diag = _diag(permission_preflight=embedded)
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=diag), \
         mock.patch.object(cli.permission_preflight, "build", side_effect=AssertionError("should reuse embedded preflight")), \
         mock.patch.object(sys, "argv", ["last30days.py", "--preflight", "--emit=json"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    payload = json.loads(stdout.getvalue())
    assert payload == embedded


def test_cli_preflight_reports_explicit_save_dir(monkeypatch, tmp_path):
    save_dir = tmp_path / "reports"
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=_diag_with_preflight()), \
         mock.patch.object(sys, "argv", ["last30days.py", "--preflight", "--save-dir", str(save_dir)]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert str(save_dir) in stdout.getvalue()
    assert "report" in stdout.getvalue()


def test_cli_preflight_reports_conditional_save_dir(monkeypatch):
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=_diag_with_preflight()), \
         mock.patch.object(
             sys,
             "argv",
             [
                 "last30days.py",
                 "--preflight",
                 "--preflight-report-on-save-dir",
                 DEFAULT_SAVE_DIR,
             ],
         ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert f"Report (if saved): {DEFAULT_SAVE_DIR}" in stdout.getvalue()


def test_cli_preflight_json_returns_structured_contract(monkeypatch):
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=_diag_with_preflight()), \
         mock.patch.object(sys, "argv", ["last30days.py", "--preflight", "--emit=json"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    payload = json.loads(stdout.getvalue())
    assert payload["local_reads"]["browser_cookies"]["status"] == "off"
    assert payload["conditional_writes"] == []
    assert payload["safe"] is True
