"""Tests for the Secret Service (libsecret) credential source in lib/env.py.

Covers:
  - missing `secret-tool` binary returns {}
  - Darwin short-circuits (macOS is served by _load_keychain)
  - successful lookups return parsed key/value pairs at the prefix convention
  - first-line extraction + whitespace stripping
  - subprocess timeout / OSError are swallowed, and a hanging store stops the
    probe loop instead of costing 5s per key
  - the attribute prefix is honored (default + LAST30DAYS_KEYRING_PREFIX)
  - get_config merges libsecret below keychain and above pass, below explicit
    env, and labels _CONFIG_SOURCE = 'libsecret' when it is the effective source
  - lib/env.py KEYCHAIN_KEYS and setup-keyring.sh ALL_KEYS stay in lockstep
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from lib import env

SETUP_KEYRING_SH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "last30days" / "scripts" / "setup-keyring.sh"
)

# ---------------------------------------------------------------------------
# _load_libsecret unit tests
# ---------------------------------------------------------------------------


def _run_result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_load_libsecret_returns_empty_when_secret_tool_missing():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value=None):
        assert env._load_libsecret(["XAI_API_KEY"], "last30days-") == {}


def test_load_libsecret_noop_on_darwin():
    # macOS is served by _load_keychain; probing both would double the work and
    # let a keyring shim shadow the Keychain item.
    with mock.patch("platform.system", return_value="Darwin"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"):
        assert env._load_libsecret(["XAI_API_KEY"], "last30days-") == {}


def test_load_libsecret_loads_present_keys_skips_missing():
    def fake_run(cmd, **kwargs):
        service = cmd[-1]  # [secret_tool, "lookup", "service", "<prefix><key>"]
        if service == "last30days-XAI_API_KEY":
            return _run_result(0, "xai-abc")
        if service == "last30days-BRAVE_API_KEY":
            return _run_result(0, "brv-xyz")
        return _run_result(1)  # secret-tool exits non-zero for a missing item

    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        result = env._load_libsecret(
            ["XAI_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"], "last30days-"
        )

    assert result == {"XAI_API_KEY": "xai-abc", "BRAVE_API_KEY": "brv-xyz"}


def test_load_libsecret_takes_first_line_only():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "sk-secret\ntrailing junk\n")):
        assert env._load_libsecret(["OPENAI_API_KEY"], "last30days-") == {"OPENAI_API_KEY": "sk-secret"}


def test_load_libsecret_strips_whitespace():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "  hello-key  \n")):
        assert env._load_libsecret(["FOO"], "last30days-") == {"FOO": "hello-key"}


def test_load_libsecret_skips_empty_and_whitespace_only_stdout():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "   \n")):
        assert env._load_libsecret(["XAI_API_KEY"], "last30days-") == {}


def test_load_libsecret_swallows_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        assert env._load_libsecret(["XAI_API_KEY"], "last30days-") == {}


def test_load_libsecret_swallows_oserror():
    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", side_effect=OSError("boom")):
        assert env._load_libsecret(["XAI_API_KEY"], "last30days-") == {}


def test_load_libsecret_stops_probing_after_timeout():
    # A locked collection blocks on a prompter that never answers in a headless
    # run. That's store-wide, not per-key: stop instead of paying 5s per key.
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        result = env._load_libsecret(
            ["XAI_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"], "last30days-"
        )

    assert result == {}
    assert calls["n"] == 1


def test_load_libsecret_honors_prefix():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["service"] = cmd[-1]
        return _run_result(0, "v")

    with mock.patch("platform.system", return_value="Linux"), \
         mock.patch("shutil.which", return_value="/usr/bin/secret-tool"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        env._load_libsecret(["XAI_API_KEY"], "myorg-")

    assert seen["service"] == "myorg-XAI_API_KEY"


# ---------------------------------------------------------------------------
# get_config integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for var in [
        "OPENAI_API_KEY", "XAI_API_KEY", "BRAVE_API_KEY", "AUTH_TOKEN", "CT0",
        "SCRAPECREATORS_API_KEY", "APIFY_API_TOKEN", "BSKY_HANDLE",
        "BSKY_APP_PASSWORD", "TRUTHSOCIAL_TOKEN", "EXA_API_KEY",
        "SERPER_API_KEY", "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "PARALLEL_API_KEY",
        "XQUIK_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY", "INCLUDE_SOURCES", "FROM_BROWSER",
        "LAST30DAYS_KEYRING_PREFIX",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)


def test_get_config_reports_libsecret_source(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", return_value={"XAI_API_KEY": "xai-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()
    assert cfg["_CONFIG_SOURCE"] == "libsecret"
    assert cfg["XAI_API_KEY"] == "xai-from-keyring"


def test_get_config_keychain_outranks_libsecret(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "xai-from-kc"}), \
         mock.patch.object(env, "_load_libsecret", return_value={"XAI_API_KEY": "xai-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-kc"
    assert cfg["_CONFIG_SOURCE"] == "keychain"


def test_get_config_libsecret_outranks_pass(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", return_value={"XAI_API_KEY": "xai-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={"XAI_API_KEY": "xai-from-pass"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-keyring"
    assert cfg["_CONFIG_SOURCE"] == "libsecret"


def test_get_config_env_var_overrides_libsecret(clean_env, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", return_value={"XAI_API_KEY": "xai-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-env"


def test_get_config_global_file_outranks_libsecret(clean_env, tmp_path, monkeypatch):
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("XAI_API_KEY=xai-from-file\n")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", return_value={"XAI_API_KEY": "xai-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-file"
    assert cfg["_CONFIG_SOURCE"].startswith("global:")


def test_get_config_probes_libsecret_only_for_missing_keys(clean_env, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    seen = {}

    def fake_load_libsecret(keys, prefix):
        seen["keys"] = list(keys)
        return {}

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", side_effect=fake_load_libsecret), \
         mock.patch.object(env, "_load_pass", return_value={}):
        env.get_config()

    assert "XAI_API_KEY" not in seen["keys"]
    assert "BRAVE_API_KEY" in seen["keys"]


def test_get_config_libsecret_prefix_resolved_from_config_file(clean_env, tmp_path, monkeypatch):
    # A prefix set in the .env layer (not shell-exported) must reach the loader,
    # i.e. the prefix is resolved at call time rather than at import time.
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("LAST30DAYS_KEYRING_PREFIX=myorg-\n")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)
    seen = {}

    def fake_load_libsecret(keys, prefix):
        seen["prefix"] = prefix
        return {}

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", side_effect=fake_load_libsecret), \
         mock.patch.object(env, "_load_pass", return_value={}):
        env.get_config()

    assert seen["prefix"] == "myorg-"


def test_get_config_openai_key_can_come_from_libsecret(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_libsecret", return_value={"OPENAI_API_KEY": "sk-from-keyring"}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        cfg = env.get_config()
    assert cfg["OPENAI_API_KEY"] == "sk-from-keyring"
    assert cfg["OPENAI_AUTH_SOURCE"] == "api_key"


# ---------------------------------------------------------------------------
# Drift guard: lib/env.py KEYCHAIN_KEYS and setup-keyring.sh ALL_KEYS must stay
# in lockstep, same as the Keychain and pass helpers.
# ---------------------------------------------------------------------------


def _parse_all_keys_from_shell(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8")
    match = re.search(r"ALL_KEYS=\(\s*(.*?)\s*\)", text, re.DOTALL)
    if not match:
        raise AssertionError(f"ALL_KEYS=( ... ) array not found in {script}")
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return [tok for tok in body.split() if tok]


def test_libsecret_keys_match_setup_script():
    shell_keys = _parse_all_keys_from_shell(SETUP_KEYRING_SH)
    python_keys = list(env.KEYCHAIN_KEYS)
    assert shell_keys == python_keys, (
        "lib/env.py::KEYCHAIN_KEYS and scripts/setup-keyring.sh::ALL_KEYS have "
        f"drifted.\n  python: {python_keys}\n  shell:  {shell_keys}"
    )
