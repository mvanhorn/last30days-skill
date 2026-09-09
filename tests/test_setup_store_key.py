"""`setup --store-key <NAME>` (U6): persist one allowlisted credential from stdin.

This is the only way the Grok Bot flow persists X_BEARER_TOKEN: the model
pipes the value on stdin, the engine appends it to the global .env as a 0o600
secret through setup_wizard.write_api_key, and stdout carries only the masked
name plus a JSON status line. The value never reaches stdout or stderr; a name
outside env.KEYCHAIN_KEYS or an empty value exits 2 without echoing anything.
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import env

DUMMY = "dummy-x-bearer-token-not-real-0123456789"


def _run(argv: list[str], stdin_text: str, env_path: Path) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(cli.env, "CONFIG_FILE", env_path),
        mock.patch.object(cli.env, "get_config", side_effect=AssertionError("store-key must not load config")),
        mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
        mock.patch.object(sys, "argv", ["last30days.py"] + argv),
    ):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                rc = cli.main()
            except SystemExit as exc:  # argparse parser.error path
                rc = int(exc.code or 0)
    return rc, stdout.getvalue(), stderr.getvalue()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("argv", [
    ["setup", "--store-key", "X_BEARER_TOKEN"],
    ["setup", "--store-key=X_BEARER_TOKEN"],
])
def test_store_key_writes_allowlisted_key_at_0600_and_masks_stdout(tmp_path, argv):
    env_path = tmp_path / "cfg" / ".env"
    rc, out, err = _run(argv, DUMMY + "\n", env_path)

    assert rc == 0, (out, err)
    assert env_path.exists()
    assert _mode(env_path) == 0o600
    assert env.load_env_file(env_path)["X_BEARER_TOKEN"] == DUMMY

    lines = out.strip().splitlines()
    assert lines[0] == "X_BEARER_TOKEN=****"
    assert json.loads(lines[-1]) == {"persisted": True, "key": "X_BEARER_TOKEN"}
    assert DUMMY not in out
    assert DUMMY not in err


def test_store_key_strips_surrounding_whitespace(tmp_path):
    env_path = tmp_path / ".env"
    rc, out, err = _run(["setup", "--store-key", "X_BEARER_TOKEN"], f"  {DUMMY}\t\r\n", env_path)
    assert rc == 0
    assert env.load_env_file(env_path)["X_BEARER_TOKEN"] == DUMMY
    assert DUMMY not in out and DUMMY not in err


def test_store_key_reads_exactly_one_line(tmp_path):
    env_path = tmp_path / ".env"
    rc, out, err = _run(
        ["setup", "--store-key", "X_BEARER_TOKEN"],
        DUMMY + "\nsecond-line-must-be-ignored\n",
        env_path,
    )
    assert rc == 0
    assert env.load_env_file(env_path)["X_BEARER_TOKEN"] == DUMMY
    assert "second-line" not in env_path.read_text()


def test_store_key_replaces_an_existing_value_in_place(tmp_path):
    """A second store-key rotates the credential: a rejected token must not
    survive a 'persisted: true' receipt (review finding)."""
    env_path = tmp_path / ".env"
    env_path.write_text("SETUP_COMPLETE=true\n# note\nX_BEARER_TOKEN=old-dummy\nXAI_API_KEY=other\n")
    os.chmod(env_path, 0o600)
    rotated = "rotated-dummy-value-not-real"
    rc, out, err = _run(["setup", "--store-key", "X_BEARER_TOKEN"], rotated + "\n", env_path)
    assert rc == 0
    assert json.loads(out.strip().splitlines()[-1]) == {"persisted": True, "key": "X_BEARER_TOKEN"}
    content = env_path.read_text()
    assert content.count("X_BEARER_TOKEN=") == 1
    assert "old-dummy" not in content
    loaded = env.load_env_file(env_path)
    assert loaded["X_BEARER_TOKEN"] == rotated
    assert loaded["XAI_API_KEY"] == "other"
    assert loaded["SETUP_COMPLETE"] == "true"
    assert "# note" in content
    assert _mode(env_path) == 0o600
    assert not (tmp_path / ".env.tmp").exists()
    assert rotated not in out and rotated not in err


def test_write_api_key_default_still_keeps_an_existing_value(tmp_path):
    from lib import setup_wizard
    env_path = tmp_path / ".env"
    assert setup_wizard.write_api_key(env_path, DUMMY, key_name="X_BEARER_TOKEN")
    assert setup_wizard.write_api_key(env_path, "other-dummy", key_name="X_BEARER_TOKEN")
    assert env.load_env_file(env_path)["X_BEARER_TOKEN"] == DUMMY


def test_store_key_reads_a_bounded_line(tmp_path):
    env_path = tmp_path / ".env"
    huge = "a" * (cli.STORE_KEY_MAX_BYTES * 2)
    rc, out, err = _run(["setup", "--store-key", "X_BEARER_TOKEN"], huge + "\n", env_path)
    assert rc == 0
    assert len(env.load_env_file(env_path)["X_BEARER_TOKEN"]) == cli.STORE_KEY_MAX_BYTES


def test_store_key_tightens_a_loose_existing_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("SETUP_COMPLETE=true\n")
    os.chmod(env_path, 0o644)
    rc, _, _ = _run(["setup", "--store-key", "X_BEARER_TOKEN"], DUMMY + "\n", env_path)
    assert rc == 0
    assert _mode(env_path) == 0o600
    loaded = env.load_env_file(env_path)
    assert loaded["SETUP_COMPLETE"] == "true"
    assert loaded["X_BEARER_TOKEN"] == DUMMY


@pytest.mark.parametrize("name", ["NOT_A_REAL_KEY", "PATH", "x_bearer_token", "X_BEARER_TOKEN=evil"])
def test_store_key_rejects_name_outside_allowlist_with_exit_2(tmp_path, name):
    env_path = tmp_path / ".env"
    rc, out, err = _run(["setup", "--store-key", name], DUMMY + "\n", env_path)
    assert rc == 2
    assert not env_path.exists()
    assert DUMMY not in out and DUMMY not in err
    assert "store-key" in err
    # R4: the failure hint must not enumerate legacy credential names.
    for banned in ("AUTH_TOKEN", "CT0", "XQUIK_API_KEY"):
        assert banned not in err


def test_store_key_without_name_exits_2(tmp_path):
    env_path = tmp_path / ".env"
    rc, out, err = _run(["setup", "--store-key"], DUMMY + "\n", env_path)
    assert rc == 2
    assert not env_path.exists()
    assert DUMMY not in out and DUMMY not in err


@pytest.mark.parametrize("stdin_text", ["", "\n", "   \n"])
def test_store_key_rejects_empty_value_with_exit_2(tmp_path, stdin_text):
    env_path = tmp_path / ".env"
    rc, out, err = _run(["setup", "--store-key", "X_BEARER_TOKEN"], stdin_text, env_path)
    assert rc == 2
    assert not env_path.exists()
    assert "empty" in err.lower()
    assert "X_BEARER_TOKEN" in err


def test_store_key_works_for_every_allowlisted_name(tmp_path):
    """The allowlist is env.KEYCHAIN_KEYS, not a bearer-only special case."""
    env_path = tmp_path / ".env"
    for name in env.KEYCHAIN_KEYS:
        rc, out, _ = _run(["setup", "--store-key", name], f"dummy-{name.lower()}-not-real\n", env_path)
        assert rc == 0, name
        assert out.strip().splitlines()[0] == f"{name}=****"
    loaded = env.load_env_file(env_path)
    assert set(env.KEYCHAIN_KEYS) <= set(loaded)
    assert _mode(env_path) == 0o600


def test_store_key_is_a_declared_setup_passthrough_flag():
    assert "--store-key" in cli.SETUP_PASSTHROUGH_FLAGS


def test_store_key_reports_persist_failure_as_false(tmp_path):
    env_path = tmp_path / ".env"
    with mock.patch("lib.setup_wizard.write_api_key", return_value=False) as w:
        rc, out, err = _run(["setup", "--store-key", "X_BEARER_TOKEN"], DUMMY + "\n", env_path)
    assert rc == 1
    w.assert_called_once_with(env_path, DUMMY, key_name="X_BEARER_TOKEN", replace=True)
    lines = out.strip().splitlines()
    assert lines[0] == "X_BEARER_TOKEN=****"
    assert json.loads(lines[-1]) == {"persisted": False, "key": "X_BEARER_TOKEN"}
    assert DUMMY not in out and DUMMY not in err
