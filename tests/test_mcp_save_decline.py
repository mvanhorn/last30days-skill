# ruff: noqa: E402
"""MCP save=false decline contract (CR-003).

The MCP research tool translates save=false into an explicit empty
`--save-dir ""` (see mcp/internal/tools/research.go::researchRunArgs).
The empty value must skip the `LAST30DAYS_MEMORY_DIR` fallback (`is None`
check) and stay falsy at the save gate, so declining to save writes
nothing even when the env var is set. Omitting the flag instead would
fall back to the env var and write a file after an explicit decline.
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


def _run_engine(topic: str, extra_argv: list[str], env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"), *extra_argv, "--", topic]
    base = {k: v for k, v in os.environ.items() if k != "LAST30DAYS_MEMORY_DIR"}
    env = {**base, "LAST30DAYS_SKIP_PREFLIGHT": "1", **env_overrides}
    return subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace", check=False)


class McpSaveDeclineTests(unittest.TestCase):
    """save=false (explicit empty --save-dir) writes nothing with MEMORY_DIR set."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-mcp-decline-"))
        self.memory = self.tmp / "Last30Days"
        self.memory.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_decline_writes_nothing_despite_memory_dir(self) -> None:
        # Exact argv shape the MCP layer sends on save=false (options first,
        # `--` separator, positional topic last).
        result = _run_engine(
            topic="OpenAI",
            extra_argv=["--mock", "--emit=md", "--save-dir", ""],
            env_overrides={
                "LAST30DAYS_CONFIG_DIR": "",
                "LAST30DAYS_MEMORY_DIR": str(self.memory),
            },
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(
            sorted(self.memory.glob("*.md")), [],
            msg=f"save=false wrote files after explicit decline. stderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
