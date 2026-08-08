"""Tests for LAST30DAYS_QUIET in hooks/scripts/check-config.sh.

The SessionStart status hook prints an onboarding welcome when nothing is
configured, and a ScrapeCreators tip when setup is done without that key.
Neither can be turned off, so a user who has deliberately settled on the
free sources sees setup advice at every session start forever.

LAST30DAYS_QUIET suppresses the onboarding and upsell lines while keeping
the one-line status. Every case below is asserted in both directions: the
quiet run drops the copy, and the otherwise-identical non-quiet run still
emits it, so a test cannot pass because the hook printed nothing at all.

Cases:
  - unconfigured + quiet          -> silent; without quiet -> welcome
  - setup done, no ScrapeCreators -> status kept, tip dropped
  - quiet read from the config file, not only the process environment
  - falsy values do not enable quiet
  - last-run status survives quiet on the unconfigured path
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "scripts" / "check-config.sh"

_CLEARED = (
    "LAST30DAYS_MEMORY_DIR",
    "LAST30DAYS_QUIET",
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
)

WELCOME = "Ready to use. Run /last30days"
TIP = "Tip: Add ScrapeCreators"
STATUS = "sources active"


def _run_hook(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    for key in _CLEARED:
        env.pop(key, None)
    env.update(env_overrides)
    bash_path = shutil.which("bash")
    if bash_path is None:
        pytest.skip("bash not on PATH")
    return subprocess.run(
        [bash_path, str(HOOK)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _cfg_dir(tmp_path: Path, name: str = "cfg", env_body: str | None = None) -> str:
    """Config dir holding a well-formed last-run.json, optionally an .env.

    The hook reads last-run.json through a python3 subshell; supplying a valid
    one keeps these tests independent of the empty-last-run exit bug (#440).
    """
    cfg = tmp_path / name
    cfg.mkdir()
    (cfg / "last-run.json").write_text(
        json.dumps({"topic": "quiet mode", "timestamp": "2026-06-01T00:00:00Z", "total": 0})
    )
    if env_body is not None:
        env_file = cfg / ".env"
        env_file.write_text(env_body)
        env_file.chmod(0o600)
    return str(cfg)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_quiet_suppresses_new_user_welcome(tmp_path: Path):
    """Unconfigured + quiet is silent; the same run without quiet is not."""
    quiet = _run_hook({"LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path, "a"), "LAST30DAYS_QUIET": "1"})
    loud = _run_hook({"LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path, "b")})

    assert quiet.returncode == 0, f"hook failed: stderr={quiet.stderr!r}"
    assert loud.returncode == 0, f"hook failed: stderr={loud.stderr!r}"

    # Positive control: without quiet the welcome is present, so the assertion
    # below is testing suppression rather than an unrelated early exit.
    assert WELCOME in loud.stdout, f"control run lost the welcome: {loud.stdout!r}"
    assert WELCOME not in quiet.stdout, f"quiet run kept the welcome: {quiet.stdout!r}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_quiet_keeps_status_and_drops_scrapecreators_tip(tmp_path: Path):
    """Setup done without ScrapeCreators: status survives, the upsell does not."""
    base = {"SETUP_COMPLETE": "true"}
    quiet = _run_hook({**base, "LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path, "a"), "LAST30DAYS_QUIET": "1"})
    loud = _run_hook({**base, "LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path, "b")})

    assert quiet.returncode == 0, f"hook failed: stderr={quiet.stderr!r}"
    assert loud.returncode == 0, f"hook failed: stderr={loud.stderr!r}"

    assert TIP in loud.stdout, f"control run lost the tip: {loud.stdout!r}"
    assert TIP not in quiet.stdout, f"quiet run kept the tip: {quiet.stdout!r}"
    # Quiet trims the copy, it does not silence a configured install.
    assert STATUS in quiet.stdout, f"quiet run lost the status line: {quiet.stdout!r}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_quiet_honored_from_config_file(tmp_path: Path):
    """The global .env can set quiet, matching how every other setting resolves."""
    cfg = _cfg_dir(tmp_path, "a", env_body="SETUP_COMPLETE=true\nLAST30DAYS_QUIET=true\n")
    control = _cfg_dir(tmp_path, "b", env_body="SETUP_COMPLETE=true\n")

    quiet = _run_hook({"LAST30DAYS_CONFIG_DIR": cfg})
    loud = _run_hook({"LAST30DAYS_CONFIG_DIR": control})

    assert quiet.returncode == 0, f"hook failed: stderr={quiet.stderr!r}"
    assert loud.returncode == 0, f"hook failed: stderr={loud.stderr!r}"

    assert TIP in loud.stdout, f"control run lost the tip: {loud.stdout!r}"
    assert TIP not in quiet.stdout, f"config-file quiet was ignored: {quiet.stdout!r}"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_falsy_values_do_not_enable_quiet(tmp_path: Path, value: str):
    """Only the documented truthy set turns quiet on; everything else is a no-op."""
    result = _run_hook(
        {
            "SETUP_COMPLETE": "true",
            "LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path),
            "LAST30DAYS_QUIET": value,
        }
    )

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert TIP in result.stdout, (
        f"LAST30DAYS_QUIET={value!r} should not enable quiet mode: {result.stdout!r}"
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not on PATH")
def test_quiet_preserves_last_run_line_when_unconfigured(tmp_path: Path):
    """Quiet drops onboarding copy, not the last-run status a returning user wants."""
    result = _run_hook(
        {"LAST30DAYS_CONFIG_DIR": _cfg_dir(tmp_path), "LAST30DAYS_QUIET": "1"}
    )

    assert result.returncode == 0, f"hook failed: stderr={result.stderr!r}"
    assert WELCOME not in result.stdout
    assert "quiet mode" in result.stdout, (
        f"expected the last-run topic to survive quiet mode: {result.stdout!r}"
    )
