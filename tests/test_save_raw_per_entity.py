# ruff: noqa: E402
"""Tests for per-entity save files when running vs-mode or --competitors.

Each entity's sub-run produces its own {entity-slug}-raw.md. Single-entity
runs unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _engine_path() -> Path:
    return REPO_ROOT / "scripts" / "last30days.py"


class PerEntitySaveFilesTests(unittest.TestCase):
    def _run(self, *argv: str, topic: str) -> tuple[subprocess.CompletedProcess, Path]:
        save_dir = Path(tempfile.mkdtemp(prefix="last30days-test-"))
        cmd = [
            sys.executable,
            str(_engine_path()),
            topic,
            "--mock",
            "--emit=md",
            "--save-dir", str(save_dir),
            *argv,
        ]
        env = {**os.environ, "LAST30DAYS_SKIP_PREFLIGHT": "1"}
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        return result, save_dir

    def test_vs_mode_produces_per_entity_files(self):
        result, save_dir = self._run(topic="Kanye West vs Drake vs Kendrick Lamar")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(save_dir.glob("*-raw.md"))
        names = [f.name for f in files]
        # Each entity slug should produce a file
        self.assertIn("kanye-west-raw.md", names)
        self.assertIn("drake-raw.md", names)
        self.assertIn("kendrick-lamar-raw.md", names)

    def test_competitors_list_produces_per_entity_files(self):
        result, save_dir = self._run(
            "--competitors-list", "Anthropic,xAI",
            topic="OpenAI",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(save_dir.glob("*-raw.md"))
        names = [f.name for f in files]
        self.assertIn("openai-raw.md", names)
        self.assertIn("anthropic-raw.md", names)
        self.assertIn("xai-raw.md", names)

    def test_single_entity_run_produces_one_file(self):
        result, save_dir = self._run(topic="OpenAI")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        files = sorted(save_dir.glob("*-raw.md"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "openai-raw.md")

    def test_per_entity_file_has_resolved_block(self):
        result, save_dir = self._run(
            "--competitors-list", "Anthropic",
            topic="OpenAI",
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        anthropic_file = save_dir / "anthropic-raw.md"
        self.assertTrue(anthropic_file.exists())
        content = anthropic_file.read_text()
        self.assertIn("## Resolved Entities", content)
        self.assertIn("**Anthropic**", content)


if __name__ == "__main__":
    unittest.main()
