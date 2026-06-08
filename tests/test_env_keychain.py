"""Tests for macOS Keychain credential source in lib/env.py.

Covers:
  - non-Darwin returns {}
  - missing `security` binary returns {}
  - successful lookups return parsed key/value pairs
  - subprocess timeout / OSError are swallowed
  - get_config merges keychain at lowest priority and labels _CONFIG_SOURCE
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from lib import env

SETUP_KEYCHAIN_SH = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts" / "setup-keychain.sh"

# ---------------------------------------------------------------------------
# _load_keychain unit tests
# ---------------------------------------------------------------------------


def test_load_keychain_returns_empty_on_non_darwin():
    with mock.patch("platform.system", return_value="Linux"):
        assert env._load_keychain(["XAI_API_KEY"]) == {}


def test_load_keychain_returns_empty_when_security_missing():
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value=None):
        assert env._load_keychain(["XAI_API_KEY"]) == {}


def _run_result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_load_keychain_loads_present_keys_skips_missing():
    def fake_run(cmd, **kwargs):
        service = cmd[cmd.index("-s") + 1]
        if service == "last30days-XAI_API_KEY":
            return _run_result(0, "xai-abc\n")
        if service == "last30days-BRAVE_API_KEY":
            return _run_result(0, "brv-xyz\n")
        return _run_result(44)  # security's "not found" exit code

    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/security"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        result = env._load_keychain(["XAI_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"])

    assert result == {"XAI_API_KEY": "xai-abc", "BRAVE_API_KEY": "brv-xyz"}


def test_load_keychain_strips_whitespace_and_newlines():
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/security"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "  hello-key  \n")):
        result = env._load_keychain(["FOO"])
    assert result == {"FOO": "hello-key"}


def test_load_keychain_swallows_subprocess_errors():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/security"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        assert env._load_keychain(["XAI_API_KEY"]) == {}


def test_load_keychain_swallows_oserror():
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/security"), \
         mock.patch("subprocess.run", side_effect=OSError("boom")):
        assert env._load_keychain(["XAI_API_KEY"]) == {}


def test_load_keychain_skips_empty_stdout():
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/security"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "")):
        assert env._load_keychain(["XAI_API_KEY"]) == {}

# ---------------------------------------------------------------------------
# get_config integration tests
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Hide every key get_config might touch and point CONFIG_FILE at a
    non-existent path so no real user config bleeds in."""
    for var in [
        "OPENAI_API_KEY", "XAI_API_KEY", "BRAVE_API_KEY", "AUTH_TOKEN", "CT0",
        "SCRAPECREATORS_API_KEY", "APIFY_API_TOKEN", "BSKY_HANDLE",
        "BSKY_APP_PASSWORD", "TRUTHSOCIAL_TOKEN", "EXA_API_KEY",
        "SERPER_API_KEY", "OPENROUTER_API_KEY", "PARALLEL_API_KEY",
        "XQUIK_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY", "INCLUDE_SOURCES", "FROM_BROWSER",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)  # no project .env in this tree either


def test_get_config_reports_keychain_source(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "xai-from-kc"}):
        cfg = env.get_config()
    assert cfg["_CONFIG_SOURCE"] == "keychain"
    assert cfg["XAI_API_KEY"] == "xai-from-kc"


def test_get_config_env_var_overrides_keychain(clean_env, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "xai-from-kc"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-env"


def test_get_config_reports_env_only_when_keychain_empty(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}):
        cfg = env.get_config()
    assert cfg["_CONFIG_SOURCE"] == "env_only"


def test_get_config_global_file_outranks_keychain(clean_env, tmp_path, monkeypatch):
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("XAI_API_KEY=xai-from-file\n")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)
    with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "xai-from-kc"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-file"
    assert cfg["_CONFIG_SOURCE"].startswith("global:")


def test_get_config_openai_key_can_come_from_keychain(clean_env):
    """OPENAI_API_KEY must be visible to get_openai_auth via the keychain
    merge — wiring regression test."""
    with mock.patch.object(env, "_load_keychain", return_value={"OPENAI_API_KEY": "sk-from-kc"}):
        cfg = env.get_config()
    assert cfg["OPENAI_API_KEY"] == "sk-from-kc"
    assert cfg["OPENAI_AUTH_SOURCE"] == "api_key"

# ---------------------------------------------------------------------------
# Drift guard: lib/env.py KEYCHAIN_KEYS and setup-keychain.sh ALL_KEYS must
# stay in lockstep. A mismatch means users storing a key via the helper script
# wouldn't see it picked up by the loader, or vice versa.
# ---------------------------------------------------------------------------


def _parse_all_keys_from_shell(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8")
    match = re.search(r"ALL_KEYS=\(\s*(.*?)\s*\)", text, re.DOTALL)
    if not match:
        raise AssertionError(f"ALL_KEYS=( ... ) array not found in {script}")
    body = match.group(1)
    # Strip shell comments and split on whitespace
    body = re.sub(r"#[^\n]*", "", body)
    return [tok for tok in body.split() if tok]


def test_keychain_keys_match_setup_script():
    shell_keys = _parse_all_keys_from_shell(SETUP_KEYCHAIN_SH)
    python_keys = list(env.KEYCHAIN_KEYS)
    assert shell_keys == python_keys, (
        "lib/env.py::KEYCHAIN_KEYS and scripts/setup-keychain.sh::ALL_KEYS "
        f"have drifted.\n  python: {python_keys}\n  shell:  {shell_keys}"
    )
