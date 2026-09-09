"""hooks/scripts/check-config.sh: X_BEARER_TOKEN counts toward HAS_X (U6/R17).

The SessionStart hook counts X as an active source when a cookie pair
(AUTH_TOKEN + CT0) or XAI_API_KEY is present. The X API bearer is a third
way to have X, so a bearer set alone must raise the numeric source count by
exactly one, the same way XAI_API_KEY alone does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"

_CREDENTIAL_VARS = (
    "LAST30DAYS_MEMORY_DIR",
    "SETUP_COMPLETE",
    "LAST30DAYS_CONFIG_DIR",
    "LAST30DAYS_TRUST_PROJECT_CONFIG",
    "OPENAI_API_KEY",
    "SCRAPECREATORS_API_KEY",
    "AUTH_TOKEN",
    "CT0",
    "XAI_API_KEY",
    "X_BEARER_TOKEN",
    "BSKY_HANDLE",
    "EXA_API_KEY",
    "EXCLUDE_SOURCES",
)


def _isolated_path(tmp_path: Path) -> str:
    """PATH with a stub ``security`` so a real Keychain item cannot leak in."""
    bin_dir = tmp_path / "hook-bin"
    bin_dir.mkdir(exist_ok=True)
    security = bin_dir / "security"
    if not security.exists():
        security.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        security.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def _run_hook(tmp_path: Path, overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash_path = shutil.which("bash")
    if bash_path is None:
        pytest.skip("bash not on PATH")
    env = os.environ.copy()
    for key in _CREDENTIAL_VARS:
        env.pop(key, None)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    env["LAST30DAYS_CONFIG_DIR"] = str(cfg_dir)  # no .env file inside: env-only
    env["LAST30DAYS_MEMORY_DIR"] = str(tmp_path / "mem")
    env["PATH"] = _isolated_path(tmp_path)
    env["SETUP_COMPLETE"] = "true"
    env.update(overrides)
    return subprocess.run(
        [bash_path, str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=30,
        check=False,
    )


def _source_count(stdout: str) -> int:
    match = re.search(r"Ready\s+[—–-]\s+(\d+)\s+sources?\s+active", stdout)
    assert match, f"could not find source count in hook stdout: {stdout!r}"
    return int(match.group(1))


def test_x_bearer_token_alone_counts_x_as_active(tmp_path: Path):
    base = _run_hook(tmp_path, {})
    assert base.returncode == 0, base.stderr
    bearer = _run_hook(tmp_path, {"X_BEARER_TOKEN": "dummy-x-bearer-not-real"})
    assert bearer.returncode == 0, bearer.stderr
    xai = _run_hook(tmp_path, {"XAI_API_KEY": "dummy-xai-not-real"})
    assert xai.returncode == 0, xai.stderr

    assert _source_count(bearer.stdout) == _source_count(base.stdout) + 1
    # Same weight as the xAI key: X is one source however it is reached.
    assert _source_count(bearer.stdout) == _source_count(xai.stdout)
    # The hook never echoes credential values.
    assert "dummy-x-bearer-not-real" not in bearer.stdout + bearer.stderr


def test_x_bearer_token_from_env_file_counts_x_as_active(tmp_path: Path):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir(exist_ok=True)
    env_file = cfg_dir / ".env"
    env_file.write_text("SETUP_COMPLETE=true\nX_BEARER_TOKEN=dummy-x-bearer-not-real\n", encoding="utf-8")
    env_file.chmod(0o600)
    with_file = _run_hook(tmp_path, {})
    assert with_file.returncode == 0, with_file.stderr

    env_file.write_text("SETUP_COMPLETE=true\n", encoding="utf-8")
    without = _run_hook(tmp_path, {})
    assert without.returncode == 0, without.stderr

    assert _source_count(with_file.stdout) == _source_count(without.stdout) + 1
    assert "dummy-x-bearer-not-real" not in with_file.stdout + with_file.stderr


def test_bearer_and_xai_key_together_count_x_once(tmp_path: Path):
    both = _run_hook(tmp_path, {"X_BEARER_TOKEN": "dummy-a", "XAI_API_KEY": "dummy-b"})
    xai = _run_hook(tmp_path, {"XAI_API_KEY": "dummy-b"})
    assert _source_count(both.stdout) == _source_count(xai.stdout)
