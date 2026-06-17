import io
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import last30days as cli

REPO_ROOT = Path(__file__).resolve().parents[1]
LAST30DAYS_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"
SKILL_MD = REPO_ROOT / "skills" / "last30days" / "SKILL.md"


def run_last30days(topic: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LAST30DAYS_SCRIPT), topic, "--mock", "--emit=json"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


class LastRunStateTests(unittest.TestCase):
    def test_empty_config_override_disables_last_run_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            env = os.environ.copy()
            env["HOME"] = str(home)
            env["LAST30DAYS_CONFIG_DIR"] = ""

            result = run_last30days("synthetic eval query", env)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((home / ".config" / "last30days" / "last-run.json").exists())

    def test_custom_config_override_writes_last_run_to_custom_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = run_last30days("custom config query", env)

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((config_dir / "last-run.json").read_text())
            self.assertEqual(payload["topic"], "custom config query")
            self.assertGreaterEqual(payload["total"], 0)

    @unittest.skipIf(shutil.which("bash") is None, "bash not available")
    def test_hook_reads_last_run_from_custom_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            config_dir.mkdir()
            (config_dir / "last-run.json").write_text(
                json.dumps(
                    {
                        "topic": "custom hook query",
                        "timestamp": "2026-04-30T00:00:00+00:00",
                        "sources": {"reddit": 2},
                        "total": 2,
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Last run: "custom hook query"', result.stdout)

    def test_hook_exits_0_when_no_last_run(self):
        """Script exits 0 when ScrapeCreators configured but no prior run (last-run.json absent)."""
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["SETUP_COMPLETE"] = "true"
            env["ENV_SCRAPECREATORS_API_KEY"] = "sk-test"

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ready —", result.stdout)
            self.assertNotIn("Last run:", result.stdout)

    def test_hook_parses_dotenv_with_unbalanced_quote(self):
        """Script exits 0 when .env contains an unbalanced quote in a value."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            config_dir = home / ".config" / "last30days"
            config_dir.mkdir(parents=True)
            env_file = config_dir / ".env"
            env_file.write_text(
                "SETUP_COMPLETE=true\n"
                "XAI_API_KEY=xai-key-with-apostrophe's-ok\n"
                "AUTH_TOKEN=test-auth\n"
                "CT0=test-ct0\n"
            )
            env = os.environ.copy()
            env["HOME"] = str(home)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ready —", result.stdout)

    @staticmethod
    def _extract_source_count(output: str) -> int:
        match = re.search(r"Ready — (\d+) sources active", output)
        if not match:
            raise AssertionError(f"Could not find source count in: {repr(output[:200])}")
        return int(match.group(1))

    def _run_hook(self, tmp: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(Path(tmp) / "home")
        env["SETUP_COMPLETE"] = "true"
        # Strip credentials that could bleed in from the test-runner environment
        # and corrupt source-count baseline comparisons.
        for key in ("AUTH_TOKEN", "CT0", "XAI_API_KEY", "BSKY_HANDLE", "EXA_API_KEY", "SCRAPECREATORS_API_KEY"):
            env.pop(key, None)
        env.update(env_overrides)
        return subprocess.run(
            ["bash", "hooks/scripts/check-config.sh"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_x_not_counted_with_only_auth_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            only_auth = self._extract_source_count(
                self._run_hook(tmp, {"AUTH_TOKEN": "test_auth"}).stdout
            )
            self.assertEqual(
                only_auth, neither,
                "X should not be counted when only AUTH_TOKEN is set (CT0 missing)",
            )

    def test_x_not_counted_with_only_ct0(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            only_ct0 = self._extract_source_count(
                self._run_hook(tmp, {"CT0": "test_ct0"}).stdout
            )
            self.assertEqual(
                only_ct0, neither,
                "X should not be counted when only CT0 is set (AUTH_TOKEN missing)",
            )

    def test_x_counted_when_both_auth_token_and_ct0(self):
        with tempfile.TemporaryDirectory() as tmp:
            neither = self._extract_source_count(
                self._run_hook(tmp, {}).stdout
            )
            both = self._extract_source_count(
                self._run_hook(tmp, {"AUTH_TOKEN": "test_auth", "CT0": "test_ct0"}).stdout
            )
            self.assertEqual(
                both, neither + 1,
                "X should add 1 source when both AUTH_TOKEN and CT0 are set",
            )

    def test_hook_shows_last_run_when_json_exists(self):
        """Script exits 0 and shows last-run summary when last-run.json exists."""
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "custom-config"
            config_dir.mkdir()
            (config_dir / "last-run.json").write_text(
                json.dumps(
                    {
                        "topic": "prior research",
                        "timestamp": "2026-06-01T12:00:00+00:00",
                        "sources": {"reddit": 5},
                        "total": 5,
                    }
                )
            )
            env = os.environ.copy()
            env["HOME"] = str(Path(tmp) / "home")
            env["SETUP_COMPLETE"] = "true"
            env["ENV_SCRAPECREATORS_API_KEY"] = "sk-test"
            env["LAST30DAYS_CONFIG_DIR"] = str(config_dir)

            result = subprocess.run(
                ["bash", "hooks/scripts/check-config.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('Last run: "prior research"', result.stdout)


class TestSkillMdFirstRunReference(unittest.TestCase):
    """Verifies SKILL.md references that exist in the CLI."""

    def test_nux_wizard_not_referenced(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn(
            "nux-wizard.md", content,
            "SKILL.md should not reference the missing nux-wizard.md file",
        )

    def test_skill_md_references_setup_command(self):
        content = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn(
            "last30days.py setup", content,
            "SKILL.md should reference the Python setup subcommand",
        )

    def test_setup_subcommand_dispatches(self):
        """topic 'setup' must reach setup_wizard, not be swallowed by argparse."""
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch("lib.setup_wizard.run_auto_setup", return_value={"cookies_found": {}}) as mock_setup, \
             mock.patch("lib.setup_wizard.write_setup_config") as mock_write, \
             mock.patch("lib.setup_wizard.get_setup_status_text", return_value="ok"), \
             mock.patch.object(sys, "argv", ["last30days.py", "setup"]):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = cli.main()
        self.assertEqual(0, rc)
        mock_setup.assert_called_once()
        mock_write.assert_called_once()


if __name__ == "__main__":
    unittest.main()
