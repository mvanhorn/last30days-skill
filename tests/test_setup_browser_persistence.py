import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import last30days as cli
from lib import cookie_extract, env


@pytest.fixture
def run_setup(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "config" / ".env"
    monkeypatch.setattr(env, "CONFIG_FILE", config_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    installed = {"yt-dlp", "digg-pp-cli", "arxiv-pp-cli", "techmeme-pp-cli"}
    monkeypatch.setattr(
        shutil, "which", lambda name: f"/test/bin/{name}" if name in installed else None
    )

    def run_process(command, **kwargs):
        assert command == ["sysctl", "-n", "hw.model"]
        return subprocess.CompletedProcess(command, 0, "MacBookPro18,1\n", "")

    monkeypatch.setattr(subprocess, "run", run_process)

    def run(winners, *, flags=("--allow-browser-cookies",), existing="", cookie_results=None):
        if existing:
            config_path.parent.mkdir(parents=True)
            config_path.write_text(existing)
            config_path.chmod(0o600)
        reads = []

        def read_cookies(browser, domain, cookie_names):
            reads.append((browser, domain))
            if cookie_results and (browser, domain) in cookie_results:
                result = cookie_results[(browser, domain)]
                if isinstance(result, Exception):
                    raise result
                return result, browser
            winner = winners.get(domain)
            selector = "firefox" if winner == "firefox-wsl" else winner
            if browser != selector:
                return None
            return {name: f"dummy-setup-{name}" for name in cookie_names}, winner

        monkeypatch.setattr(cookie_extract, "extract_cookies_with_source", read_cookies)
        monkeypatch.setattr(sys, "argv", ["last30days", "setup", *flags])
        with patch.dict(os.environ, {"LAST30DAYS_SKIP_KEYCHAIN": "1"}, clear=True):
            assert cli.main() == 0
            next_config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
        saved = env.load_env_file(config_path)
        output = capsys.readouterr()
        assert "dummy-setup-" not in config_path.read_text()
        assert "dummy-setup-" not in output.out + output.err
        assert saved["SETUP_COMPLETE"] == "true"
        return saved, next_config, reads

    return run


@pytest.mark.parametrize(
    "browser",
    ["chrome", "brave", "edge", "vivaldi", "arc", "chromium", "firefox", "safari"],
)
def test_consented_browser_remains_usable_after_setup(run_setup, browser):
    saved, config, _ = run_setup({".x.com": browser})

    assert saved["FROM_BROWSER"] == browser
    assert saved["BROWSER_CONSENT"] == "true"
    assert env.cookie_extraction_browsers(config) == [browser]
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"
    assert config["CT0"] == "dummy-setup-ct0"


def test_mixed_browser_winners_remain_usable_after_setup(run_setup):
    saved, config, _ = run_setup({".x.com": "chrome", ".truthsocial.com": "firefox"})

    assert saved["FROM_BROWSER"] == "chrome,firefox"
    assert env.cookie_extraction_browsers(config) == ["chrome", "firefox"]
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"
    assert config["TRUTHSOCIAL_TOKEN"] == "dummy-setup-_session_id"


def test_shared_browser_winner_is_saved_once(run_setup):
    saved, config, _ = run_setup({".x.com": "chrome", ".truthsocial.com": "chrome"})

    assert saved["FROM_BROWSER"] == "chrome"
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"
    assert config["TRUTHSOCIAL_TOKEN"] == "dummy-setup-_session_id"


def test_explicit_opera_selection_remains_usable_after_setup(run_setup):
    saved, config, _ = run_setup({".x.com": "opera"}, existing="FROM_BROWSER=opera\n")

    assert saved["FROM_BROWSER"] == "opera"
    assert env.cookie_extraction_browsers(config) == ["opera"]
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"


def test_wsl_firefox_source_is_saved_as_a_supported_selector(run_setup):
    saved, config, _ = run_setup({".x.com": "firefox-wsl"})

    assert saved["FROM_BROWSER"] == "firefox"
    assert env.cookie_extraction_browsers(config) == ["firefox"]
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"


def test_no_cookie_match_does_not_select_a_native_browser(run_setup):
    saved, config, reads = run_setup({})

    assert reads
    assert "FROM_BROWSER" not in saved
    assert env.cookie_extraction_browsers(config) == []
    assert not config["AUTH_TOKEN"]


@pytest.mark.parametrize(
    "cookies",
    [
        {"auth_token": "dummy-setup-auth_token"},
        {"ct0": "dummy-setup-ct0"},
        {"auth_token": "dummy-setup-auth_token", "ct0": ""},
        OSError("dummy browser read failure"),
    ],
)
def test_incomplete_or_failed_browser_is_not_retained(run_setup, cookies):
    saved, config, reads = run_setup({}, cookie_results={("chrome", ".x.com"): cookies})

    assert ("chrome", ".x.com") in reads
    assert "FROM_BROWSER" not in saved
    assert saved["BROWSER_CONSENT"] == "true"
    assert env.cookie_extraction_browsers(config) == []
    assert not config["AUTH_TOKEN"]
    assert not config["CT0"]


def test_partial_chromium_does_not_displace_complete_firefox(run_setup):
    saved, config, _ = run_setup(
        {".x.com": "firefox"},
        cookie_results={("chrome", ".x.com"): {"ct0": "dummy-setup-partial-ct0"}},
    )

    assert saved["FROM_BROWSER"] == "firefox"
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"
    assert config["CT0"] == "dummy-setup-ct0"


def test_consent_can_restore_chromium_after_a_saved_refusal(run_setup):
    saved, config, _ = run_setup({".x.com": "chrome"}, existing="BROWSER_CONSENT=false\n")

    assert saved["BROWSER_CONSENT"] == "true"
    assert saved["FROM_BROWSER"] == "chrome"
    assert config["AUTH_TOKEN"] == "dummy-setup-auth_token"


def test_decline_disables_previously_saved_chromium(run_setup):
    saved, config, reads = run_setup(
        {".x.com": "chrome"},
        flags=(),
        existing="BROWSER_CONSENT=true\nFROM_BROWSER=chrome\n",
    )

    assert saved["BROWSER_CONSENT"] == "false"
    assert saved["FROM_BROWSER"] == "chrome"
    assert env.cookie_extraction_browsers(config) == []
    assert not config["AUTH_TOKEN"]
    assert reads == []


@pytest.mark.parametrize("flags", [(), ("--allow-browser-cookies", "--no-browser-cookies")])
def test_no_consent_does_not_read_or_save_browser_credentials(run_setup, flags):
    saved, config, reads = run_setup({".x.com": "chrome"}, flags=flags)

    assert saved.get("FROM_BROWSER") in {None, "off"}
    assert saved["BROWSER_CONSENT"] == "false"
    assert env.cookie_extraction_browsers(config) == []
    assert not config["AUTH_TOKEN"]
    assert reads == []


def test_explicit_browser_off_is_preserved(run_setup):
    saved, config, reads = run_setup({".x.com": "chrome"}, existing="FROM_BROWSER=off\n")

    assert saved["FROM_BROWSER"] == "off"
    assert env.cookie_extraction_browsers(config) == []
    assert not config["AUTH_TOKEN"]
    assert reads == []
