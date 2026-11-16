"""Reddit session consent, the cookie read, and onboarding (plan U6, AE1, AE10).

Reading the reddit.com login cookie needs its own consent, separate from the
X browser consent, and every global refusal vetoes it. These tests watch the
cookie reader itself: a ``.reddit.com`` read is any ``extract_cookies`` call
for that domain.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import cookie_extract, doctor, env, reddit_session, safari_cookies, setup_wizard

COOKIE = "dummy-reddit-cookie-value-9876543210"


class CookieSpy:
    """Stands in for cookie_extract.extract_cookies; records every domain read."""

    def __init__(self, logged_in=("chrome",)):
        self.logged_in = set(logged_in)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, browser, domain, names):
        self.calls.append((browser, domain))
        if domain == env.REDDIT_COOKIE_DOMAIN and browser in self.logged_in:
            return {"reddit_session": COOKIE}
        return None

    def reddit_reads(self):
        return [b for b, d in self.calls if "reddit" in d]


@pytest.fixture
def spy(monkeypatch):
    s = CookieSpy()
    monkeypatch.setattr(cookie_extract, "extract_cookies", s)
    monkeypatch.setattr(env, "x_extras_enabled", lambda _c: False)
    return s


def _config(monkeypatch, mode="read", **values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return env.get_config(policy=env.ConfigLoadPolicy(browser_cookies=mode))


def _session(config):
    return reddit_session.session_for(config)


class TestAE1:
    def test_x_consent_alone_never_reads_reddit(self, spy, monkeypatch):
        config = _config(monkeypatch, BROWSER_CONSENT="true", FROM_BROWSER="chrome")
        assert _session(config) is None
        assert spy.reddit_reads() == []
        assert any(d == ".x.com" for _b, d in spy.calls)  # X itself still reads

    def test_reddit_yes_plus_from_browser_off_reads_nothing(self, spy, monkeypatch):
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome",
                         FROM_BROWSER="off")
        assert _session(config) is None
        assert spy.calls == []

    def test_reddit_yes_outranks_an_x_refusal(self, monkeypatch):
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome",
                         BROWSER_CONSENT="false")
        assert env.reddit_session_gate(config) == (True, "")

    def test_browser_consent_false_without_a_reddit_yes_reads_nothing(self, spy, monkeypatch):
        config = _config(monkeypatch, REDDIT_FROM_BROWSER="chrome", BROWSER_CONSENT="false")
        assert _session(config) is None
        assert spy.reddit_reads() == []

    def test_from_browser_off_blocks_even_a_reddit_yes(self, spy, monkeypatch):
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome",
                         FROM_BROWSER="off")
        assert _session(config) is None
        assert spy.reddit_reads() == []

    def test_grok_bot_with_bird_pin_and_reddit_yes_reads_nothing(self, spy, monkeypatch):
        config = _config(monkeypatch, LAST30DAYS_HOST="grok-bot", LAST30DAYS_X_BACKEND="bird",
                         REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome", FROM_BROWSER="chrome")
        assert _session(config) is None
        assert spy.reddit_reads() == []
        assert env.reddit_session_gate(config) == (False, "never on a Grok Bot host")


class TestVetoesAndContexts:
    def test_diagnose_and_doctor_plan_only_read_nothing(self, spy, monkeypatch):
        config = _config(monkeypatch, mode="plan_only", REDDIT_BROWSER_CONSENT="true",
                         REDDIT_FROM_BROWSER="chrome")
        allowed, why = env.reddit_session_gate(config)
        assert not allowed and "reads no browser cookies" in why
        assert _session(config) is None
        assert spy.calls == []

    def test_no_browser_cookies_and_the_mcp_default_read_nothing(self, spy, monkeypatch):
        config = _config(monkeypatch, mode="off", REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome")
        assert _session(config) is None and spy.calls == []

    def test_reddit_consent_false_disables_and_agentcookie_on_does_not_enable(self, spy, monkeypatch):
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="false", REDDIT_FROM_BROWSER="chrome",
                         AGENTCOOKIE="on")
        assert _session(config) is None
        assert spy.reddit_reads() == []

    def test_hosted_backend_reads_nothing(self, spy, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_API_KEY", "hosted-test-key")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example.test")
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome")
        assert env.reddit_session_gate(config)[1] == "the hosted API backend runs research remotely"
        assert _session(config) is None and spy.reddit_reads() == []

    def test_scheduled_run_reads_nothing_without_its_opt_in(self, spy, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_SCHEDULED_RUN", "1")
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome")
        assert _session(config) is None and spy.reddit_reads() == []
        reddit_session.reset_run_state()
        config = _config(monkeypatch, LAST30DAYS_REDDIT_SESSION_SCHEDULED="1")
        assert _session(config) is not None
        assert spy.reddit_reads() == ["chrome"]

    def test_scheduled_marker_in_a_config_file_is_ignored(self, monkeypatch, tmp_path):
        env.CONFIG_FILE.write_text("LAST30DAYS_SCHEDULED_RUN=1\n", encoding="utf-8")
        env.CONFIG_FILE.chmod(0o600)
        config = env.get_config()
        assert not config.get("LAST30DAYS_SCHEDULED_RUN")

    def test_watchlist_runs_are_scheduled_and_cookie_free(self, monkeypatch):
        import watchlist

        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"], seen["env"] = argv, kwargs["env"]
            raise RuntimeError("stop after capture")

        monkeypatch.setattr(watchlist.subprocess, "run", fake_run)
        monkeypatch.setattr(watchlist.store, "update_run", lambda *a, **k: None)
        with contextlib.suppress(Exception):
            watchlist._research_topic({"id": 1, "name": "x", "search_queries": None}, 1,
                                      {"LAST30DAYS_SCHEDULED_RUN": "1"})
        assert "--no-browser-cookies" in seen["argv"]
        assert seen["env"]["LAST30DAYS_SCHEDULED_RUN"] == "1"
        source = Path(watchlist.__file__).read_text(encoding="utf-8")
        assert '"LAST30DAYS_SCHEDULED_RUN": "1"' in source


class TestBrowserChoice:
    def test_reads_only_the_recorded_reddit_browser(self, spy, monkeypatch):
        config = _config(monkeypatch, REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome")
        session = _session(config)
        assert session is not None and session.browser == "chrome"
        assert spy.reddit_reads() == ["chrome"]
        assert COOKIE not in repr(session) and COOKIE not in json.dumps(
            {k: str(v) for k, v in config.items()}
        )

    def test_returning_user_with_firefox_for_x_gets_a_chrome_reddit_session(self, spy, monkeypatch):
        config = _config(monkeypatch, BROWSER_CONSENT="true", FROM_BROWSER="firefox",
                         REDDIT_BROWSER_CONSENT="true", REDDIT_FROM_BROWSER="chrome")
        assert _session(config) is not None
        assert ("chrome", env.REDDIT_COOKIE_DOMAIN) in spy.calls
        assert ("firefox", env.REDDIT_COOKIE_DOMAIN) not in spy.calls
        assert all(b == "firefox" for b, d in spy.calls if d == ".x.com")

    def test_generic_browser_extract_never_reads_reddit(self, spy):
        env.extract_browser_credentials({"FROM_BROWSER": "chrome", env.REDDIT_CONSENT_VAR: "true"})
        assert spy.reddit_reads() == []

    def test_lookalike_host_cookie_is_rejected(self):
        assert safari_cookies._host_matches(".reddit.com", env.REDDIT_COOKIE_DOMAIN)
        assert safari_cookies._host_matches("www.reddit.com", env.REDDIT_COOKIE_DOMAIN)
        assert not safari_cookies._host_matches("reddit.com.evil.tld", env.REDDIT_COOKIE_DOMAIN)
        assert not safari_cookies._host_matches("notreddit.com", env.REDDIT_COOKIE_DOMAIN)

    def test_windows_app_bound_store_is_reported_as_unsupported(self, monkeypatch):
        monkeypatch.setattr(env.os, "name", "nt")
        assert env.read_reddit_session_cookie({}, "chrome") == (None, "unsupported-encryption")


class TestSetup:
    def test_yes_scans_chrome_first_records_the_browser_and_never_prints_the_cookie(self, spy, tmp_path):
        env_path = tmp_path / ".env"
        spy.logged_in = {"firefox"}
        result = setup_wizard.run_reddit_session_setup({}, env_path, consent=True)
        assert result["status"] == "found" and result["browser"] == "firefox"
        assert [c["browser"] for c in result["checked"]][0] == "chrome"
        text = env_path.read_text(encoding="utf-8")
        assert "REDDIT_BROWSER_CONSENT=true" in text
        assert "REDDIT_FROM_BROWSER=firefox" in text
        assert "REDDIT_SESSION_OFFERED=true" in text
        assert COOKIE not in text and COOKIE not in json.dumps(result)

    def test_no_records_the_refusal_and_the_offer(self, spy, tmp_path):
        env_path = tmp_path / ".env"
        result = setup_wizard.run_reddit_session_setup({}, env_path, consent=False)
        assert result["status"] == "declined" and spy.calls == []
        text = env_path.read_text(encoding="utf-8")
        assert "REDDIT_BROWSER_CONSENT=false" in text and "REDDIT_SESSION_OFFERED=true" in text

    def test_global_refusal_vetoes_the_setup_read(self, spy, tmp_path):
        result = setup_wizard.run_reddit_session_setup({"FROM_BROWSER": "off"}, tmp_path / ".env", consent=True)
        assert result["status"] == "vetoed" and spy.calls == []

    def test_reddit_yes_after_skipping_x_still_reads(self, spy, tmp_path):
        setup_wizard.run_reddit_session_setup({"BROWSER_CONSENT": "false"}, tmp_path / ".env", consent=True)
        assert spy.calls

    def test_grok_bot_setup_reads_and_writes_nothing(self, spy, tmp_path):
        env_path = tmp_path / ".env"
        result = setup_wizard.run_reddit_session_setup({"LAST30DAYS_HOST": "grok-bot"}, env_path, consent=True)
        assert result["status"] == "unavailable" and spy.calls == [] and not env_path.exists()

    def test_cli_setup_flag_is_a_passthrough(self):
        assert "--reddit-session" in cli.SETUP_PASSTHROUGH_FLAGS
        assert "--no-reddit-session" in cli.SETUP_PASSTHROUGH_FLAGS


class TestDoctorRow:
    def test_session_row_shows_consent_without_reading_a_cookie(self, spy):
        config = {env.REDDIT_CONSENT_VAR: "true", env.REDDIT_FROM_BROWSER_VAR: "chrome"}
        report = doctor.build_report(config)
        row = next(r for r in report["sources"]["reddit"]["paths"] if r["path"] == "session")
        assert row["consent"] == "granted" and row["browser"] == "chrome" and row["would_run"] is True
        assert spy.calls == []
        assert "consent granted (chrome)" in doctor.render_text(report)

    def test_unset_consent_row_says_how_to_opt_in(self, spy):
        row = next(r for r in doctor.build_report({})["sources"]["reddit"]["paths"] if r["path"] == "session")
        assert row["consent"] == "unset" and "setup --reddit-session" in row["action"]


def _cli(argv, config, environ=None):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(cli.env, "get_config", return_value=dict(config)), \
            mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1", **(environ or {})}), \
            mock.patch.object(sys, "argv", ["last30days.py", *argv]), \
            mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("must not run")), \
            redirect_stdout(out), redirect_stderr(err):
        rc = cli.main()
    return rc, out.getvalue(), err.getvalue()


class TestCli:
    SESSION_ON = {"_BROWSER_COOKIE_MODE": "read", env.REDDIT_CONSENT_VAR: "true", env.REDDIT_FROM_BROWSER_VAR: "chrome"}

    def test_ae10_record_fixtures_refuses_while_the_session_is_on(self, tmp_path):
        rc, _out, err = _cli(["Ghostty terminal", "--record-fixtures", str(tmp_path / "rec")], self.SESSION_ON)
        assert rc == 2
        assert "--record-fixtures refuses" in err and "--no-browser-cookies" in err

    def test_hosted_backend_prints_a_notice_and_reads_no_cookie(self, spy):
        with mock.patch.object(cli.env, "read_secret_env", return_value="hosted-test-key"), \
                mock.patch("lib.hosted.run_hosted", return_value=0):
            rc, _out, err = _cli(["Ghostty terminal"], self.SESSION_ON,
                                 environ={"LAST30DAYS_API_BASE": "https://hosted.example.test"})
        assert rc == 0
        assert "Reddit session is not used with the hosted backend" in err
        assert spy.calls == []
