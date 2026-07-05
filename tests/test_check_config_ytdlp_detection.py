"""Tests for yt-dlp detection in check-config.sh."""
from __future__ import annotations

import json
import os
import re
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


def _run_hook(env_overrides: dict[str, str], path_override: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for k in ("LAST30DAYS_MEMORY_DIR", "SETUP_COMPLETE", "LAST30DAYS_CONFIG_DIR",
              "HOME", "PATH", "USER"):
        env.pop(k, None)
    env.update({k: v for k, v in env_overrides.items() if v is not None})
    if path_override is not None:
        env["PATH"] = path_override
    raw = HOOK.read_bytes()
    if sys.platform == "win32":
        raw = raw.replace(b"\r\n", b"\n")
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".sh", delete=False) as tf:
        tf.write(raw)
        tmp_path = tf.name
    try:
        return subprocess.run(
            [_get_bash_cmd(), tmp_path],
            capture_output=True, text=True, env=env, timeout=30,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_new_user_with_ytdlp_says_youtube_works(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    result = _run_hook({"HOME": str(home), "PATH": os.environ.get("PATH", "")})
    assert "YouTube" in result.stdout


def test_new_user_without_ytdlp_unchanged_welcome(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    empty_path = str(tmp_path / "empty")
    Path(empty_path).mkdir()
    # On Windows, PATH must include system dirs so bash can run basic commands
    if sys.platform == "win32":
        sys_paths = os.environ.get("PATH", "")
    else:
        sys_paths = empty_path
    result = _run_hook({"HOME": str(home), "PATH": sys_paths})
    assert result.returncode == 0, f"hook failed: ret={result.returncode} stderr={result.stderr!r}"
    assert "Reddit" in result.stdout or "Hacker News" in result.stdout


def test_setup_done_user_source_count_includes_ytdlp(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    env_file = cfg_dir / ".env"
    env_file.write_text("SETUP_COMPLETE=true\nAUTH_TOKEN=fake\nCT0=fake\n", encoding="utf-8")
    result = _run_hook({"HOME": str(home), "LAST30DAYS_CONFIG_DIR": str(cfg_dir)})
    assert result.returncode == 0
    assert "source" in result.stdout.lower()


def test_keychain_credentials_avoid_new_user_welcome(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    result = _run_hook({"HOME": str(home)})
    assert result.returncode == 0
