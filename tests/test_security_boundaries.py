"""Regression tests for agent-host local-read boundaries."""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import env


def test_importing_cli_does_not_load_config_or_propagate_endpoints(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("XAI_BASE_URL", raising=False)
    with mock.patch("lib.env.get_config", side_effect=AssertionError("import loaded config")) as get_config:
        importlib.reload(cli)
    get_config.assert_not_called()
    assert os.environ.get("OPENAI_BASE_URL") is None
    assert os.environ.get("XAI_BASE_URL") is None


@pytest.mark.parametrize("mode", ["import", "help"])
def test_cold_cli_does_not_attempt_config_or_cookie_access(tmp_path, mode):
    scripts = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
    attempts = tmp_path / "attempts.json"
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    sidecar = binary_dir / "agentcookie"
    sidecar.write_text("#!/bin/sh\nprintf 'unexpected sidecar execution'\nexit 1\n")
    sidecar.chmod(0o700)
    # No inherited credentials or host paths: even a regression stays inside
    # this fixture, and an exception caught by the CLI cannot hide an attempt.
    child_env = {
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "LAST30DAYS_CONFIG_DIR": str(tmp_path / ".config" / "last30days"),
        "LAST30DAYS_SKIP_KEYCHAIN": "1",
        "PATH": str(binary_dir),
    }
    result = subprocess.run(
        [sys.executable, "-I", "-c", """
import json
from pathlib import Path
import socket
import subprocess
import sys
scripts, output, mode = sys.argv[1:]
sys.path.insert(0, scripts)
attempts = []
def forbid(name):
    def denied(*args, **kwargs):
        attempts.append(name)
        raise AssertionError("unexpected acquisition")
    return denied
subprocess.Popen = forbid("subprocess")
socket.create_connection = forbid("network")
socket.socket.connect = forbid("network")
socket.getaddrinfo = forbid("network")
from lib import env
env.get_config = forbid("get_config")
try:
    import last30days
    if mode == "help":
        sys.argv = ["last30days.py", "--help"]
        try:
            last30days.main()
        except SystemExit as error:
            if error.code != 0:
                raise
finally:
    Path(output).write_text(json.dumps(attempts))
""", str(scripts), str(attempts), mode],
        env=child_env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(attempts.read_text()) == []
    if mode == "help":
        assert "usage:" in result.stdout.lower()


def test_diagnose_uses_plan_only_cookie_policy_and_safe_pipeline(monkeypatch):
    seen: dict[str, object] = {}

    def fake_get_config(*, policy):
        seen["policy"] = policy
        return {"_BROWSER_COOKIE_MODE": policy.browser_cookies, "_BROWSER_COOKIE_BROWSERS": ["firefox"]}

    with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={"ok": True}) as diagnose, \
         mock.patch.object(sys, "argv", ["last30days.py", "--diagnose"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert seen["policy"].browser_cookies == "plan_only"
    diagnose.assert_called_once_with(
        {"_BROWSER_COOKIE_MODE": "plan_only", "_BROWSER_COOKIE_BROWSERS": ["firefox"]},
        None,
        safe=True,
        x_envelope=False,
    )
    assert json.loads(stdout.getvalue()) == {"ok": True}


def test_setup_without_cookie_flag_disables_browser_cookie_setup(monkeypatch):
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as setup, \
         mock.patch("lib.setup_wizard.write_setup_config", return_value=True), \
         mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
         mock.patch.object(sys, "argv", ["last30days.py", "setup"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert setup.call_args.kwargs["allow_browser_cookies"] is False


def test_setup_cookie_flag_allows_browser_cookie_setup(monkeypatch):
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as setup, \
         mock.patch("lib.setup_wizard.write_setup_config", return_value=True), \
         mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
         mock.patch.object(sys, "argv", ["last30days.py", "setup", "--allow-browser-cookies"]):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert setup.call_args.kwargs["allow_browser_cookies"] is True


def test_no_browser_cookies_overrides_setup_cookie_flag(monkeypatch):
    seen: dict[str, object] = {}

    def fake_get_config(*, policy):
        seen["policy"] = policy
        return {}

    with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
         mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as setup, \
         mock.patch("lib.setup_wizard.write_setup_config", return_value=True), \
         mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
         mock.patch.object(
             sys,
             "argv",
             ["last30days.py", "--no-browser-cookies", "setup", "--allow-browser-cookies"],
         ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert seen["policy"].browser_cookies == "off"
    assert setup.call_args.kwargs["allow_browser_cookies"] is False


def test_diagnose_overrides_setup_cookie_flag(monkeypatch):
    seen: dict[str, object] = {}

    def fake_get_config(*, policy):
        seen["policy"] = policy
        return {}

    with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
         mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as setup, \
         mock.patch("lib.setup_wizard.write_setup_config", return_value=True), \
         mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
         mock.patch.object(
             sys,
             "argv",
             ["last30days.py", "--diagnose", "setup", "--allow-browser-cookies"],
         ):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert cli.main() == 0

    assert seen["policy"].browser_cookies == "plan_only"
    assert setup.call_args.kwargs["allow_browser_cookies"] is False


def test_research_run_defaults_to_browser_cookie_read():
    """A plain research run reads cookies (the path that powers X auth)."""
    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["some topic"])
    policy = cli._config_policy_for_args(args, "some topic", extra)
    assert policy.browser_cookies == "read"


def test_no_browser_cookies_flag_disables_research_run_cookie_read():
    """--no-browser-cookies flips a research run to the no-read policy."""
    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["--no-browser-cookies", "some topic"])
    policy = cli._config_policy_for_args(args, "some topic", extra)
    assert policy.browser_cookies == "off"


def test_watchlist_subprocess_disables_browser_cookies():
    """The unattended watchlist cron must never probe browser cookies."""
    import watchlist

    fake_result = mock.Mock(returncode=1, stdout="", stderr="boom")
    with mock.patch.object(watchlist, "store") as store, \
         mock.patch.object(watchlist.subprocess, "run", return_value=fake_result) as run:
        store.record_run.return_value = 1
        watchlist._run_topic({"id": 1, "name": "test topic", "search_queries": None})

    argv = run.call_args.args[0]
    assert "--no-browser-cookies" in argv


def test_project_config_ignored_by_default_and_cannot_self_trust(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text(
        "LAST30DAYS_TRUST_PROJECT_CONFIG=1\nOPENAI_BASE_URL=https://example.invalid\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()

    assert cfg["OPENAI_BASE_URL"] is None
    assert cfg["_CONFIG_SOURCE"] == "env_only"


def test_project_config_loads_with_process_trust_signal(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text("OPENAI_BASE_URL=https://trusted.example\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()

    assert cfg["OPENAI_BASE_URL"] == "https://trusted.example"
    assert cfg["_CONFIG_SOURCE"].startswith(f"project:{project_env}")
