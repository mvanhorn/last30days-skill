import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAST30DAYS_SCRIPT = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


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


if __name__ == "__main__":
    unittest.main()
