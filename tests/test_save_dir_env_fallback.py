# ruff: noqa: E402
"""Tests for `LAST30DAYS_MEMORY_DIR` env-var fallback for `--save-dir`.

When an agent invokes the engine directly (bypassing the SKILL.md wrapper),
the `--save-dir` flag is often omitted. Previously this silently no-op'd the
file save while DB persistence still happened (via LAST30DAYS_STORE env var),
making the failure invisible. These tests pin the fix: an unset `--save-dir`
now defaults to `LAST30DAYS_MEMORY_DIR` from either os.environ or the user's
.env file, mirroring the LAST30DAYS_STORE pattern.

Issue: https://github.com/dzivkovi/last30days-skill/issues/8
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine_path() -> Path:
    return REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


def _run_engine(
    topic: str,
    extra_argv: list[str],
    env_overrides: dict[str, str],
) -> subprocess.CompletedProcess:
    cmd = [
        sys.executable,
        str(_engine_path()),
        topic,
        "--mock",
        "--emit=md",
        *extra_argv,
    ]
    # Scrub LAST30DAYS_MEMORY_DIR from the inherited parent env so a developer
    # who has it exported (or in a global .env) doesn't accidentally satisfy
    # the no-env-no-flag negative test. Each test re-introduces it explicitly
    # via env_overrides when needed.
    base = {k: v for k, v in os.environ.items() if k != "LAST30DAYS_MEMORY_DIR"}
    env = {**base, "LAST30DAYS_SKIP_PREFLIGHT": "1", **env_overrides}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class SaveDirEnvFallbackTests(unittest.TestCase):
    """Pin the fallback contract: env var supplies --save-dir when flag omitted."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-savedir-env-"))
        self.config_dir = self.tmp / "config"
        self.config_dir.mkdir()
        self.save_target = self.tmp / "Last30Days"
        self.save_target.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_dotenv(self, contents: str) -> None:
        (self.config_dir / ".env").write_text(contents, encoding="utf-8")

    def test_env_var_in_dotenv_file_triggers_save(self) -> None:
        """Setting LAST30DAYS_MEMORY_DIR in .env makes --save-dir-less runs save."""
        self._write_dotenv(f"LAST30DAYS_MEMORY_DIR={self.save_target}\n")
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={"LAST30DAYS_CONFIG_DIR": str(self.config_dir)},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertGreaterEqual(
            len(files), 1,
            msg=f"No file in {self.save_target}. stderr: {result.stderr}",
        )

    def test_shell_exported_env_var_triggers_save(self) -> None:
        """Setting LAST30DAYS_MEMORY_DIR in os.environ (not in .env) also works."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_MEMORY_DIR": str(self.save_target),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertGreaterEqual(
            len(files), 1,
            msg=f"No file in {self.save_target}. stderr: {result.stderr}",
        )

    def test_explicit_save_dir_flag_wins_over_env_var(self) -> None:
        """--save-dir always wins; env var is fallback only."""
        flag_target = self.tmp / "flag-wins"
        flag_target.mkdir()
        self._write_dotenv(f"LAST30DAYS_MEMORY_DIR={self.save_target}\n")
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--save-dir", str(flag_target)],
            env_overrides={"LAST30DAYS_CONFIG_DIR": str(self.config_dir)},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        flag_files = sorted(flag_target.glob("*.md"))
        env_files = sorted(self.save_target.glob("*.md"))
        self.assertGreaterEqual(
            len(flag_files), 1,
            msg=f"Flag dir empty — flag-over-env precedence broken. stderr: {result.stderr}",
        )
        self.assertEqual(
            len(env_files), 0,
            msg="Env-var dir got a file when flag was explicit — precedence broken.",
        )

    def test_no_env_no_flag_preserves_no_save_behavior(self) -> None:
        """Neither flag nor env var → no file saved, no error (current behavior)."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={"LAST30DAYS_CONFIG_DIR": ""},
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertEqual(len(files), 0)

    def test_empty_string_env_var_does_not_trigger_save(self) -> None:
        """LAST30DAYS_MEMORY_DIR='' is treated as 'no fallback', not as a path."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_MEMORY_DIR": "",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertEqual(len(files), 0)

    def test_explicit_empty_save_dir_flag_does_not_trigger_fallback(self) -> None:
        """--save-dir '' (explicit empty) suppresses save even when env var is set."""
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--save-dir", ""],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_MEMORY_DIR": str(self.save_target),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertEqual(
            len(files), 0,
            msg="Explicit empty --save-dir was overridden by env var fallback — `is None` check broken.",
        )

    def test_shell_empty_env_var_overrides_dotenv_value(self) -> None:
        """LAST30DAYS_MEMORY_DIR='' in shell suppresses save even when .env has a value.

        Without `is not None` semantics at the env layer, the empty shell export
        would silently fall through to the .env value (the `or` operator treats
        '' and None identically). This pins the env-over-config-when-explicit rule.
        """
        self._write_dotenv(f"LAST30DAYS_MEMORY_DIR={self.save_target}\n")
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": str(self.config_dir),
                "LAST30DAYS_MEMORY_DIR": "",
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(self.save_target.glob("*.md"))
        self.assertEqual(
            len(files), 0,
            msg="Shell-empty env var must suppress save even when .env has a value.",
        )

    def test_env_var_pointing_to_nonexistent_dir_creates_it(self) -> None:
        """save_output calls mkdir(parents=True, exist_ok=True); env-var path should too."""
        deep_target = self.tmp / "does" / "not" / "exist" / "yet"
        self.assertFalse(deep_target.exists())
        result = _run_engine(
            topic="OpenAI",
            extra_argv=[],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_MEMORY_DIR": str(deep_target),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(deep_target.exists())
        files = sorted(deep_target.glob("*.md"))
        self.assertGreaterEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
