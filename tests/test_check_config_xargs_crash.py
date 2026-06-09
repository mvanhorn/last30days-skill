"""Tests for issue #506: check-config.sh crashes on .env values with unbalanced quotes.

Under `set -euo pipefail`, the `xargs`-based whitespace trimming in
`load_env_vars()` parses shell quoting. An unbalanced single quote in a
value (e.g. ``XAI_API_KEY=xai-Toms'key123``) causes `xargs` to exit 1,
which propagates to the whole hook and kills every Claude Code session
start for that user.

This test suite is RED before the fix (hook exits 1) and GREEN after
(hook exits 0), providing the required evidence gate.

Reference: PR #337 fixed the same file with a similar class of shell bug.
"""

import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

# Resolve path to the hook script from the repo root (one level above tests/).
SCRIPT = Path(__file__).resolve().parent.parent / "hooks" / "scripts" / "check-config.sh"


def run_hook(env_content: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Write env_content to a temp .env file and invoke check-config.sh against it.

    The hook reads from ``$HOME/.config/last30days/.env`` by default, but we
    override ``HOME`` to point at a temp directory so the test is isolated from
    the developer's real config.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir) / ".config" / "last30days"
        config_dir.mkdir(parents=True)
        env_file = config_dir / ".env"
        env_file.write_text(env_content)
        env_file.chmod(0o600)

        env = {
            **os.environ,
            "HOME": tmpdir,
            # Suppress last-run.json lookup noise
            "LAST30DAYS_CONFIG_DIR": "",
            **(extra_env or {}),
        }

        return subprocess.run(
            ["bash", str(SCRIPT)],
            capture_output=True,
            text=True,
            env=env,
        )


class TestUnbalancedQuoteCrash(unittest.TestCase):
    """RED before fix: hook must exit 0 even when values contain unbalanced quotes."""

    def test_unbalanced_single_quote_in_value(self):
        """Core regression: XAI_API_KEY with apostrophe in value must not crash hook."""
        env_content = textwrap.dedent("""\
            SETUP_COMPLETE=true
            XAI_API_KEY=xai-Toms'key123
        """)
        result = run_hook(env_content)
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Hook crashed (exit {result.returncode}) on unbalanced single quote.\n"
                f"stderr: {result.stderr!r}\n"
                f"stdout: {result.stdout!r}"
            ),
        )

    def test_unbalanced_double_quote_in_value(self):
        """Double-quote mid-value should also not crash the hook."""
        env_content = textwrap.dedent("""\
            SETUP_COMPLETE=true
            OPENAI_API_KEY=sk-he"said-hello
        """)
        result = run_hook(env_content)
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Hook crashed (exit {result.returncode}) on unbalanced double quote.\n"
                f"stderr: {result.stderr!r}\n"
                f"stdout: {result.stdout!r}"
            ),
        )


class TestPreservedBehavior(unittest.TestCase):
    """Ensure the fix does not regress existing correct behavior."""

    def test_empty_env_file_exits_zero(self):
        """Empty .env file: hook must exit 0 (no crash, no false warnings)."""
        result = run_hook("")
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Empty .env should exit 0. Got {result.returncode}. stderr: {result.stderr!r}",
        )

    def test_comment_lines_skipped(self):
        """Lines starting with # are skipped and must not cause a crash."""
        env_content = textwrap.dedent("""\
            # This is a comment with an unbalanced ' quote
            SETUP_COMPLETE=true
        """)
        result = run_hook(env_content)
        self.assertEqual(result.returncode, 0)

    def test_balanced_surrounding_double_quotes_stripped(self):
        """Values wrapped in double quotes must have surrounding quotes stripped."""
        env_content = textwrap.dedent("""\
            SETUP_COMPLETE=true
            OPENAI_API_KEY="sk-cleanvalue"
        """)
        result = run_hook(env_content)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Balanced double-quoted value should parse cleanly. stderr: {result.stderr!r}",
        )
        # The hook outputs a "Ready" line when SETUP_COMPLETE is set
        self.assertIn("Ready", result.stdout)

    def test_balanced_surrounding_single_quotes_stripped(self):
        """Values wrapped in single quotes must have surrounding quotes stripped."""
        env_content = textwrap.dedent("""\
            SETUP_COMPLETE=true
            OPENAI_API_KEY='sk-cleanvalue'
        """)
        result = run_hook(env_content)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Balanced single-quoted value should parse cleanly. stderr: {result.stderr!r}",
        )
        self.assertIn("Ready", result.stdout)

    def test_setup_complete_recognized(self):
        """SETUP_COMPLETE=true in .env causes the hook to show the Ready message."""
        env_content = textwrap.dedent("""\
            SETUP_COMPLETE=true
        """)
        result = run_hook(env_content)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Ready", result.stdout)


if __name__ == "__main__":
    unittest.main()
