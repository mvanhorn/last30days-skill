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


def _run_hook_with_env_file(env_lines: str) -> subprocess.CompletedProcess[str]:
    """Run check-config.sh against a temp global .env and return the result.

    Returns the CompletedProcess; callers inspect stdout for the
    "N sources active" line. HOME is redirected so the hook reads the
    synthetic ~/.config/last30days/.env rather than the developer's real one.
    """
    tmp = tempfile.mkdtemp()
    config_dir = Path(tmp) / ".config" / "last30days"
    config_dir.mkdir(parents=True)
    (config_dir / ".env").write_text(env_lines)
    env = os.environ.copy()
    env["HOME"] = tmp
    # Drop any X creds inherited from the developer's shell so the count is
    # driven solely by the synthetic .env file.
    for key in ("AUTH_TOKEN", "CT0", "XAI_API_KEY", "SCRAPECREATORS_API_KEY"):
        env.pop(key, None)
    return subprocess.run(
        ["bash", "hooks/scripts/check-config.sh"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class SourceCountTests(unittest.TestCase):
    """check-config.sh must count X as active only when it can actually search.

    X cookie auth needs both AUTH_TOKEN and CT0 (issue #396); AUTH_TOKEN alone
    is a dead source. The baseline without X is 3 (HN + Polymarket + Reddit).
    """

    def _source_count(self, env_lines: str) -> int:
        result = _run_hook_with_env_file(env_lines)
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            if "sources active" in line:
                return int(line.split("Ready —")[1].split("sources")[0].strip())
        self.fail(f"no 'sources active' line in output:\n{result.stdout}")

    def test_auth_token_without_ct0_does_not_count_x(self):
        self.assertEqual(3, self._source_count("SETUP_COMPLETE=1\nAUTH_TOKEN=abc\n"))

    def test_auth_token_with_ct0_counts_x(self):
        self.assertEqual(4, self._source_count("SETUP_COMPLETE=1\nAUTH_TOKEN=abc\nCT0=def\n"))

    def test_ct0_without_auth_token_does_not_count_x(self):
        self.assertEqual(3, self._source_count("SETUP_COMPLETE=1\nCT0=def\n"))

    def test_xai_api_key_counts_x_without_cookies(self):
        self.assertEqual(4, self._source_count("SETUP_COMPLETE=1\nXAI_API_KEY=grok\n"))


if __name__ == "__main__":
    unittest.main()
