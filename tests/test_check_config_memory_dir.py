"""Tests for hooks/scripts/check-config.sh auto-creating LAST30DAYS_MEMORY_DIR.

Covers issue #395 — fresh installs failed silently on first --emit=html run
because nothing created the default memory dir. The SessionStart hook should
mkdir -p the configured memory dir on every run.

The default path is the same one used throughout the engine:
  LAST30DAYS_MEMORY_DIR="${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"

Cases:
  - LAST30DAYS_MEMORY_DIR points to a non-existent path -> dir is created
  - LAST30DAYS_MEMORY_DIR points to an existing path -> no error, exit 0
  - LAST30DAYS_MEMORY_DIR unset -> default dir is created
  - LAST30DAYS_MEMORY_DIR points to an unwritable path -> script still exits 0
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"


def _run_hook(env_overrides: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    # Clear any pre-existing keys so the test is deterministic.
    for k in ("LAST30DAYS_MEMORY_DIR", "SETUP_COMPLETE", "LAST30DAYS_CONFIG_DIR"):
        env.pop(k, None)
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd) if cwd else None,
        timeout=30,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_creates_dir_when_memory_dir_missing(tmp_path: Path):
    target = tmp_path / "Last30Days"
    assert not target.exists()

    result = _run_hook({"LAST30DAYS_MEMORY_DIR": str(target)})

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert target.is_dir(), "LAST30DAYS_MEMORY_DIR was not created"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_no_error_when_memory_dir_already_exists(tmp_path: Path):
    target = tmp_path / "Last30Days"
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("preserve me")

    result = _run_hook({"LAST30DAYS_MEMORY_DIR": str(target)})

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert sentinel.read_text() == "preserve me", "existing dir contents were disturbed"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_default_memory_dir_created_when_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Override $HOME so the default fallback path lands inside tmp_path.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    expected = fake_home / "Documents" / "Last30Days"
    assert not expected.exists()

    # Use a clean env without LAST30DAYS_MEMORY_DIR (also drop SETUP_COMPLETE so
    # the welcome path runs, but it doesn't matter — mkdir runs first).
    result = _run_hook({})

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert expected.is_dir(), f"default dir {expected} was not created"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_tolerates_unwritable_memory_dir(tmp_path: Path):
    """Hook should swallow mkdir errors and still exit 0 — never crash Claude Code startup."""
    # /proc/1 is owned by root; under sandboxed runners this will fail with EACCES.
    bad_path = "/proc/should/not/be/writable/last30days-test-395"
    if os.path.exists(bad_path):
        pytest.skip("unwritable test path already exists; skipping")

    result = _run_hook({"LAST30DAYS_MEMORY_DIR": bad_path})

    # Either mkdir silently failed (2>/dev/null) or it succeeded under a permissive
    # test runner. Both are acceptable. The contract is: exit 0, no crash.
    assert result.returncode == 0, (
        f"hook should not crash on mkdir failure: rc={result.returncode} stderr={result.stderr!r}"
    )
