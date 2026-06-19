"""Tests for the pass(1) credential source in lib/env.py.

Covers:
  - missing `pass` binary returns {}
  - successful lookups return parsed key/value pairs at the prefix convention
  - first-line extraction + whitespace stripping
  - subprocess timeout / OSError are swallowed
  - the path prefix is honored (default + LAST30DAYS_PASS_PREFIX override)
  - get_config merges pass below keychain and below explicit env, and labels
    _CONFIG_SOURCE = 'pass' when pass is the effective source
  - lib/env.py KEYCHAIN_KEYS and setup-pass.sh ALL_KEYS stay in lockstep
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from lib import env

SETUP_PASS_SH = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts" / "setup-pass.sh"

# ---------------------------------------------------------------------------
# _load_pass unit tests
# ---------------------------------------------------------------------------


def _run_result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_load_pass_returns_empty_when_pass_missing():
    with mock.patch("shutil.which", return_value=None):
        assert env._load_pass(["XAI_API_KEY"], "last30days/") == {}


def test_load_pass_loads_present_keys_skips_missing():
    def fake_run(cmd, **kwargs):
        path = cmd[-1]  # [pass_bin, "show", "<prefix><key>"]
        if path == "last30days/XAI_API_KEY":
            return _run_result(0, "xai-abc\n")
        if path == "last30days/BRAVE_API_KEY":
            return _run_result(0, "brv-xyz\n")
        return _run_result(1)  # pass exits non-zero for a missing entry

    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        result = env._load_pass(["XAI_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"], "last30days/")

    assert result == {"XAI_API_KEY": "xai-abc", "BRAVE_API_KEY": "brv-xyz"}


def test_load_pass_takes_first_line_only():
    # pass entries keep the secret on line 1; metadata may follow.
    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "sk-secret\nurl: https://x\nuser: bob\n")):
        assert env._load_pass(["OPENAI_API_KEY"], "last30days/") == {"OPENAI_API_KEY": "sk-secret"}


def test_load_pass_strips_whitespace():
    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "  hello-key  \n")):
        assert env._load_pass(["FOO"], "last30days/") == {"FOO": "hello-key"}


def test_load_pass_skips_empty_and_whitespace_only_stdout():
    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", return_value=_run_result(0, "   \n")):
        assert env._load_pass(["XAI_API_KEY"], "last30days/") == {}


def test_load_pass_swallows_timeout():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        assert env._load_pass(["XAI_API_KEY"], "last30days/") == {}


def test_load_pass_swallows_oserror():
    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", side_effect=OSError("boom")):
        assert env._load_pass(["XAI_API_KEY"], "last30days/") == {}


def test_load_pass_stops_probing_after_timeout():
    # A hanging store (GPG/pinentry) must not be probed once per key — otherwise
    # a locked store stalls every config load by 5s x len(keys).
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5)

    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        result = env._load_pass(["XAI_API_KEY", "BRAVE_API_KEY", "OPENAI_API_KEY"], "last30days/")

    assert result == {}
    assert calls["n"] == 1  # stopped after the first timeout, didn't probe the rest


def test_load_pass_honors_prefix():
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["path"] = cmd[-1]
        return _run_result(0, "v\n")

    with mock.patch("shutil.which", return_value="/usr/bin/pass"), \
         mock.patch("subprocess.run", side_effect=fake_run):
        env._load_pass(["XAI_API_KEY"], "secrets/l30/")

    assert seen["path"] == "secrets/l30/XAI_API_KEY"


# ---------------------------------------------------------------------------
# get_config integration tests (pass merged below keychain and explicit env)
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
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)


def test_get_config_reports_pass_source(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={"XAI_API_KEY": "xai-from-pass"}):
        cfg = env.get_config()
    assert cfg["_CONFIG_SOURCE"] == "pass"
    assert cfg["XAI_API_KEY"] == "xai-from-pass"


def test_get_config_keychain_outranks_pass(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "xai-from-kc"}), \
         mock.patch.object(env, "_load_pass", return_value={"XAI_API_KEY": "xai-from-pass"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-kc"
    assert cfg["_CONFIG_SOURCE"] == "keychain"


def test_get_config_env_var_overrides_pass(clean_env, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={"XAI_API_KEY": "xai-from-pass"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-env"


def test_get_config_global_file_outranks_pass(clean_env, tmp_path, monkeypatch):
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("XAI_API_KEY=xai-from-file\n")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={"XAI_API_KEY": "xai-from-pass"}):
        cfg = env.get_config()
    assert cfg["XAI_API_KEY"] == "xai-from-file"
    assert cfg["_CONFIG_SOURCE"].startswith("global:")


def test_get_config_probes_pass_only_for_missing_keys(clean_env, monkeypatch):
    # A key already supplied by a higher-priority source must not be probed in
    # pass — that's what keeps a `pass`-installed but `.env`-using box off gpg.
    monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
    seen = {}

    def fake_load_pass(keys, prefix):
        seen["keys"] = list(keys)
        return {}

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", side_effect=fake_load_pass):
        env.get_config()

    assert "XAI_API_KEY" not in seen["keys"]   # already supplied by env
    assert "BRAVE_API_KEY" in seen["keys"]      # still missing, so probed


def test_get_config_pass_prefix_resolved_from_config_file(clean_env, tmp_path, monkeypatch):
    # LAST30DAYS_PASS_PREFIX set in the .env config layer (not shell-exported)
    # must reach _load_pass — i.e. the prefix is resolved at call time.
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("LAST30DAYS_PASS_PREFIX=secrets/l30/\n")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)
    seen = {}

    def fake_load_pass(keys, prefix):
        seen["prefix"] = prefix
        return {}

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", side_effect=fake_load_pass):
        env.get_config()

    assert seen["prefix"] == "secrets/l30/"


def test_get_config_openai_key_can_come_from_pass(clean_env):
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={"OPENAI_API_KEY": "sk-from-pass"}):
        cfg = env.get_config()
    assert cfg["OPENAI_API_KEY"] == "sk-from-pass"
    assert cfg["OPENAI_AUTH_SOURCE"] == "api_key"


# ---------------------------------------------------------------------------
# Drift guard: lib/env.py KEYCHAIN_KEYS and setup-pass.sh ALL_KEYS must stay in
# lockstep, same as the Keychain helper. A mismatch means a key stored via the
# helper wouldn't be picked up by the loader, or vice versa.
# ---------------------------------------------------------------------------


def _parse_all_keys_from_shell(script: Path) -> list[str]:
    text = script.read_text(encoding="utf-8")
    match = re.search(r"ALL_KEYS=\(\s*(.*?)\s*\)", text, re.DOTALL)
    if not match:
        raise AssertionError(f"ALL_KEYS=( ... ) array not found in {script}")
    body = re.sub(r"#[^\n]*", "", match.group(1))
    return [tok for tok in body.split() if tok]


def test_pass_keys_match_setup_script():
    shell_keys = _parse_all_keys_from_shell(SETUP_PASS_SH)
    python_keys = list(env.KEYCHAIN_KEYS)
    assert shell_keys == python_keys, (
        "lib/env.py::KEYCHAIN_KEYS and scripts/setup-pass.sh::ALL_KEYS have "
        f"drifted.\n  python: {python_keys}\n  shell:  {shell_keys}"
    )
