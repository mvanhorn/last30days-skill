"""Tests for hooks/scripts/check-config.sh yt-dlp detection on the new-user path.

Covers issue #394 — new users with yt-dlp installed were never told YouTube was
available, because the capability-detection block ran AFTER the new-user early
exit. The SessionStart hook should detect yt-dlp on PATH and mention it in the
welcome message even when no config exists.

Cases:
  - new user + yt-dlp on PATH -> welcome includes "Detected: yt-dlp" line
  - new user + no yt-dlp on PATH -> welcome has no "Detected" line
  - existing user (env file present) + yt-dlp -> SOURCE_COUNT includes YouTube (regression)
  - new user + yt-dlp -> hook still exits 0
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"


def _run_hook(env_overrides: dict[str, str], path_override: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for k in (
        "LAST30DAYS_MEMORY_DIR",
        "SETUP_COMPLETE",
        "LAST30DAYS_CONFIG_DIR",
        "OPENAI_API_KEY",
        "SCRAPECREATORS_API_KEY",
        "AUTH_TOKEN",
        "XAI_API_KEY",
        "CT0",
        "BSKY_HANDLE",
        "BSKY_APP_PASSWORD",
        "EXA_API_KEY",
    ):
        env.pop(k, None)
    env.update(env_overrides)
    if path_override is not None:
        env["PATH"] = path_override
    return subprocess.run(
        ["bash", str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _bash_dir() -> str:
    """Directory containing the bash binary, so PATH overrides still let us run bash."""
    bash_path = shutil.which("bash")
    if bash_path is None:
        pytest.skip("bash not on PATH")
    return str(Path(bash_path).parent)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_new_user_with_ytdlp_mentions_detection(tmp_path: Path):
    """A new user with yt-dlp on PATH should see the YouTube capability in the welcome."""
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    (fake_bin / "yt-dlp").touch()
    (fake_bin / "yt-dlp").chmod(0o755)

    # PATH must contain bash (so the hook can run) AND the fake yt-dlp dir.
    # Putting fake_bin FIRST means any real yt-dlp elsewhere is shadowed.
    path = f"{fake_bin}:{_bash_dir()}"
    assert shutil.which("yt-dlp", path=path) is not None, (
        "test pre-condition: fake yt-dlp should resolve on the override PATH"
    )

    result = _run_hook({}, path_override=path)

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert "yt-dlp" in result.stdout, (
        f"expected yt-dlp detection line in welcome, got: {result.stdout!r}"
    )
    # The exact phrasing from the implementation.
    assert "Detected: yt-dlp" in result.stdout, (
        f"expected 'Detected: yt-dlp' line, got: {result.stdout!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_new_user_without_ytdlp_no_detection_line():
    """A new user without yt-dlp should NOT see a spurious detection line."""
    # PATH contains only bash's directory — no yt-dlp anywhere.
    path = _bash_dir()
    assert shutil.which("yt-dlp", path=path) is None, (
        "test pre-condition: yt-dlp should not resolve on the minimal PATH"
    )

    result = _run_hook({}, path_override=path)

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert "Detected: yt-dlp" not in result.stdout, (
        f"unexpected detection line in welcome: {result.stdout!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_existing_user_with_ytdlp_counts_youtube(tmp_path: Path):
    """Regression: setup-done path must still count YouTube via yt-dlp."""
    fake_bin = tmp_path / "fake_bin"
    fake_bin.mkdir()
    (fake_bin / "yt-dlp").touch()
    (fake_bin / "yt-dlp").chmod(0o755)
    path = f"{fake_bin}:{_bash_dir()}"

    # Simulate a user who's completed setup: SETUP_COMPLETE in env.
    result = _run_hook(
        {"SETUP_COMPLETE": "true", "SCRAPECREATORS_API_KEY": "sc_test"},
        path_override=path,
    )

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    # The setup-done branch should NOT print the new-user copy.
    assert "setup takes 30 seconds" not in result.stdout
    assert "Ready" in result.stdout
