"""Tests for hooks/scripts/check-config.sh auto-creating LAST30DAYS_MEMORY_DIR."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"

_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\msys64\usr\bin\bash.exe",
]


def _get_bash_cmd() -> str:
    bash = shutil.which("bash")
    if bash is None and sys.platform == "win32":
        for p in _GIT_BASH_CANDIDATES:
            if Path(p).exists():
                bash = p
                break
    return bash or "bash"


def _run_hook(env_overrides: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for k in ("LAST30DAYS_MEMORY_DIR", "SETUP_COMPLETE", "LAST30DAYS_CONFIG_DIR"):
        env.pop(k, None)
    env.update(env_overrides)
    # On Windows, git autocrlf may store CRLF. Strip CR to avoid "pipefail: invalid option".
    raw = HOOK.read_bytes()
    if sys.platform == "win32":
        raw = raw.replace(b"\r\n", b"\n")
    # Write to temp file to avoid command-line length limits on Windows
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".sh", delete=False) as tf:
        tf.write(raw)
        tmp_path = tf.name
    try:
        return subprocess.run(
            [_get_bash_cmd(), tmp_path],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(cwd) if cwd else None,
            timeout=30,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_creates_dir_when_memory_dir_missing(tmp_path: Path):
    target = tmp_path / "Last30Days"
    assert not target.exists()
    result = _run_hook({"LAST30DAYS_MEMORY_DIR": str(target)})
    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert target.is_dir()


def test_no_error_when_memory_dir_already_exists(tmp_path: Path):
    target = tmp_path / "Last30Days"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("preserve me")
    result = _run_hook({"LAST30DAYS_MEMORY_DIR": str(target)})
    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert sentinel.read_text() == "preserve me"


def test_default_memory_dir_created_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    expected = fake_home / "Documents" / "Last30Days"
    assert not expected.exists()
    result = _run_hook({})
    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert expected.is_dir()


def test_tolerates_unwritable_memory_dir(tmp_path: Path):
    unwritable = tmp_path / "nope"
    unwritable.mkdir()
    try:
        unwritable.chmod(0o555)
        result = _run_hook({"LAST30DAYS_MEMORY_DIR": str(unwritable / "sub")})
        assert result.returncode == 0, f"hook should not fail on permission error: {result.stderr}"
    finally:
        unwritable.chmod(0o755)
