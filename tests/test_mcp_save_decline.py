# ruff: noqa: E402
"""Exercise local research persistence for the MCP save control."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


ENGINE = REPO_ROOT / "skills" / "last30days" / "scripts" / "last30days.py"


class McpSaveDeclineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-mcp-decline-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.memory = self.tmp / "Last30Days"
        self.memory.mkdir()
        self.config = self.tmp / "config"
        self.config.mkdir()
        (self.config / ".env").write_text("LAST30DAYS_STORE=1\n", encoding="utf-8")
        self.corpus = self.tmp / "corpus"
        self.corpus.mkdir()
        (self.corpus / "OpenAI release.txt").write_text(
            "OpenAI announced improvements to the OpenAI platform.", encoding="utf-8",
        )
        self.global_store = self.tmp / ".local" / "share" / "last30days" / "research.db"

    def run_research(self, save_dir: str, *options: str) -> str:
        result = subprocess.run(
            [
                sys.executable, "-I", str(ENGINE), "--research-only",
                "--mock", "--quick", "--search=reddit,corpus", "--emit=compact",
                "--no-browser-cookies", "--corpus", str(self.corpus),
                "--save-dir", save_dir, *options, "--", "OpenAI",
            ],
            cwd=self.tmp,
            env={
                "PATH": str(self.tmp / "empty-bin"),
                "HOME": str(self.tmp),
                "USERPROFILE": str(self.tmp),
                "LAST30DAYS_SKIP_KEYCHAIN": "1",
                "LAST30DAYS_SKIP_PREFLIGHT": "1",
                "LAST30DAYS_CONFIG_DIR": str(self.config),
                "LAST30DAYS_MEMORY_DIR": str(self.memory),
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("OpenAI", result.stdout)
        self.assertIn("OpenAI release", result.stdout)
        return result.stdout

    def assert_research_persisted(self, store_path: Path) -> None:
        for name in ("last-run.json", "last-report.json"):
            state = json.loads((self.config / name).read_text(encoding="utf-8"))
            self.assertEqual(state["topic"], "OpenAI")
        corpus_cache = (self.config / "corpus-cache.json").read_text(encoding="utf-8")
        self.assertIn("improvements to the OpenAI platform", corpus_cache)
        self.assertTrue(store_path.is_file())
        with closing(sqlite3.connect(store_path)) as connection:
            self.assertEqual(connection.execute("SELECT name FROM topics").fetchall(), [("OpenAI",)])
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0], 0)

    def test_decline_writes_nothing_despite_memory_dir(self) -> None:
        self.run_research("", "--no-research-persistence")
        self.assertEqual(list(self.memory.iterdir()), [])
        self.assertEqual(sorted(path.name for path in self.config.iterdir()), [".env"])
        self.assertFalse(self.global_store.exists())

    def test_save_dir_alone_only_controls_document_export(self) -> None:
        self.run_research("")
        self.assertEqual(list(self.memory.iterdir()), [])
        self.assert_research_persisted(self.global_store)

    def test_save_true_keeps_all_configured_research_writes(self) -> None:
        stdout = self.run_research(str(self.memory))
        self.assertIn("openai-raw.md", stdout)
        self.assertTrue((self.memory / "openai-raw.md").is_file())
        self.assert_research_persisted(self.memory / "research.db")


if __name__ == "__main__":
    unittest.main()
