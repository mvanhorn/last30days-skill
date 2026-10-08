"""Durable browser consent at config, setup, and CDP boundaries."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import chrome_cdp, env, setup_wizard


@pytest.fixture
def browser_session(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.touch(mode=0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", env_path)
    for name in ("FROM_BROWSER", "BROWSER_CONSENT", "BROWSER_CDP_URL", "LAST30DAYS_HOST"):
        monkeypatch.delenv(name, raising=False)
    cookies = [
        {"name": "auth_token", "value": f"test-auth-{tmp_path.name}", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
    ]
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch.object(env, "_load_keychain", return_value={}),
        mock.patch.object(env, "_load_pass", return_value={}),
        mock.patch("lib.agentcookie.read_x_cookies", return_value=None),
        mock.patch("lib.cookie_extract.extract_cookies_with_source", return_value=None) as native,
        mock.patch.object(chrome_cdp, "_get_all_cookies", return_value=cookies) as cdp,
    ):
        yield env_path, cdp, native, cookies[0]["value"]


@pytest.mark.parametrize("settings", ["", "BROWSER_CONSENT=false\nFROM_BROWSER=chrome\n"])
def test_repeated_research_does_not_read_unconsented_browser(browser_session, settings):
    env_path, cdp, native, _ = browser_session
    env_path.write_text(settings + "BROWSER_CDP_URL=ws://127.0.0.1:18800/devtools/page/test\n")

    for _ in range(2):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
        assert not config.get("AUTH_TOKEN")
        assert not config.get("CT0")
    cdp.assert_not_called()
    native.assert_not_called()


@pytest.mark.parametrize("settings", ["BROWSER_CONSENT=true\n", "FROM_BROWSER=chrome\n"])
def test_repeated_research_reads_explicitly_consented_browser(browser_session, settings):
    env_path, cdp, _, auth_token = browser_session
    env_path.write_text(settings + "BROWSER_CDP_URL=ws://127.0.0.1:18800/devtools/page/test\n")

    for _ in range(2):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
        assert config["AUTH_TOKEN"] == auth_token
        assert config["CT0"] == "test-ct0"
    assert cdp.call_count == 2
    assert auth_token not in env_path.read_text()


def test_law7_rejection_skips_consented_browser_probe(browser_session, monkeypatch):
    env_path, cdp, native, _ = browser_session
    env_path.write_text(
        "BROWSER_CONSENT=true\n"
        "AGENTCOOKIE=off\n"
        "BROWSER_CDP_URL=ws://127.0.0.1:18800/devtools/page/test\n"
    )
    cdp.return_value = []
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "best", "espresso", "grinders", "--search=reddit",
    ])

    assert cli.main() == cli.LAW7_HOST_PLAN_EXIT
    cdp.assert_not_called()
    native.assert_not_called()


def test_law7_auto_resolve_keeps_consented_browser_probe(browser_session, monkeypatch):
    env_path, cdp, _, _ = browser_session
    env_path.write_text(
        "BROWSER_CONSENT=true\n"
        "AGENTCOOKIE=off\n"
        "BROWSER_CDP_URL=ws://127.0.0.1:18800/devtools/page/test\n"
    )
    cdp.return_value = []
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "best", "espresso", "grinders",
        "--search=reddit", "--auto-resolve",
    ])

    class DiagnoseReached(Exception):
        pass

    with mock.patch.object(cli.pipeline, "diagnose", side_effect=DiagnoseReached):
        with pytest.raises(DiagnoseReached):
            cli.main()
    cdp.assert_called_once_with("ws://127.0.0.1:18800/devtools/page/test")


def test_from_browser_off_overrides_recorded_consent(browser_session):
    env_path, cdp, native, _ = browser_session
    env_path.write_text("BROWSER_CONSENT=true\nFROM_BROWSER=off\n")
    config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
    assert not config.get("AUTH_TOKEN")
    cdp.assert_not_called()
    native.assert_not_called()


def test_setup_decline_replaces_prior_consent_and_survives_next_run(browser_session):
    env_path, cdp, native, _ = browser_session
    env_path.write_text("BROWSER_CONSENT=true\nFROM_BROWSER=chrome\n")
    with (
        mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}),
        mock.patch.object(sys, "argv", ["last30days.py", "setup"]),
    ):
        assert cli.main() == 0

    persisted = env.load_env_file(env_path)
    assert persisted["BROWSER_CONSENT"] == "false"
    assert persisted["FROM_BROWSER"] == "chrome"
    for _ in range(2):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
        assert not config.get("AUTH_TOKEN")
    cdp.assert_not_called()
    native.assert_not_called()


def test_setup_grant_records_consent_for_cdp_without_persisting_cookies(browser_session):
    env_path, cdp, _, auth_token = browser_session
    env_path.write_text("BROWSER_CONSENT=false\nBROWSER_CDP_URL=ws://127.0.0.1:18800/devtools/page/test\n")
    with (
        mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}),
        mock.patch.object(sys, "argv", ["last30days.py", "setup", "--allow-browser-cookies"]),
    ):
        assert cli.main() == 0

    assert env.load_env_file(env_path)["BROWSER_CONSENT"] == "true"
    for _ in range(2):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
        assert config["AUTH_TOKEN"] == auth_token
    assert cdp.call_count == 2
    assert auth_token not in env_path.read_text()


def test_setup_reports_failure_when_consent_cannot_be_saved(browser_session, capsys):
    with (
        mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}),
        mock.patch("lib.setup_wizard.write_api_key", return_value=False),
        mock.patch.object(sys, "argv", ["last30days.py", "setup"]),
    ):
        assert cli.main() == 1
    assert "configuration could not be fully saved" in capsys.readouterr().err


@pytest.mark.parametrize("allow_cookies", [False, True])
def test_setup_reports_partial_save_without_reverting_consent(
    browser_session, capsys, allow_cookies,
):
    env_path, cdp, native, _ = browser_session
    previous_consent = "false" if allow_cookies else "true"
    env_path.write_text(f"BROWSER_CONSENT={previous_consent}\nFROM_BROWSER=chrome\n")
    real_open = os.open

    def fail_completion_append(path, flags, *args, **kwargs):
        if Path(path) == env_path and flags & os.O_APPEND:
            raise OSError("test-only completion write failure")
        return real_open(path, flags, *args, **kwargs)

    argv = ["last30days.py", "setup"]
    if allow_cookies:
        argv.append("--allow-browser-cookies")
    with (
        mock.patch.object(setup_wizard, "run_auto_setup", return_value={"cookies_found": {}}),
        mock.patch.object(setup_wizard.os, "open", side_effect=fail_completion_append),
        mock.patch.object(sys, "argv", argv),
    ):
        assert cli.main() == 1

    persisted = env.load_env_file(env_path)
    assert persisted["BROWSER_CONSENT"] == ("true" if allow_cookies else "false")
    assert "SETUP_COMPLETE" not in persisted
    assert persisted["FROM_BROWSER"] == "chrome"
    if not allow_cookies:
        for _ in range(2):
            config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
            assert not config.get("AUTH_TOKEN")
        cdp.assert_not_called()
        native.assert_not_called()
    stderr = capsys.readouterr().err
    assert "some settings may already be saved" in stderr
    assert "configuration was not saved" not in stderr
