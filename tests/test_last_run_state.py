import io
import json
import os
import subprocess
import sys
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
