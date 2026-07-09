# ruff: noqa: E402
"""Tests for slug truncation on long topics.

`save_output()` slugifies the entire query/topic string into the save
filename with no truncation. On macOS (and most filesystems), filenames
are capped at 255 bytes; a topic longer than ~230 characters produced a
slug that exceeded that limit, and `Path.write_text()` raised
`OSError: [Errno 63] File name too long` *after* research had already
completed, discarding the gathered results.

`slugify()` now truncates long slugs to a safe length and appends a short
hash of the full value so distinct long topics still map to distinct,
deterministic filenames.
"""

from __future__ import annotations

import importlib.util
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


def _load_engine_module():
    spec = importlib.util.spec_from_file_location("last30days_engine", _engine_path())
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    return module


class SlugifyUnitTests(unittest.TestCase):
    """Pin the truncation/hash contract directly on `slugify()`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _load_engine_module()

    def test_short_topic_is_unchanged(self) -> None:
        self.assertEqual(self.engine.slugify("OpenAI"), "openai")
        self.assertEqual(self.engine.slugify("hello world"), "hello-world")

    def test_long_topic_slug_fits_filesystem_limit(self) -> None:
        long_topic = "multi model comparison " * 25  # ~575 chars
        slug = self.engine.slugify(long_topic)
        filename = f"{slug}-raw.md"
        self.assertLess(
            len(filename.encode("utf-8")), 255,
            msg=f"Filename {len(filename.encode('utf-8'))} bytes — exceeds macOS 255-byte limit",
        )

    def test_long_topic_slug_is_deterministic(self) -> None:
        long_topic = "multi model comparison " * 25
        self.assertEqual(self.engine.slugify(long_topic), self.engine.slugify(long_topic))

    def test_distinct_long_topics_produce_distinct_slugs(self) -> None:
        base = "multi model comparison " * 25
        self.assertNotEqual(self.engine.slugify(base), self.engine.slugify(base + "extra"))

    def test_long_slug_actually_writable_on_disk(self) -> None:
        long_topic = "multi model comparison " * 25
        slug = self.engine.slugify(long_topic)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{slug}-raw.md"
            path.write_text("test", encoding="utf-8")
            self.assertTrue(path.exists())


class SaveOutputLongTopicIntegrationTests(unittest.TestCase):
    """End-to-end: a long topic must not crash the save path after research completes."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="l30d-slug-length-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_long_topic_saves_without_oserror(self) -> None:
        long_topic = "multi model comparison across providers and pricing tiers " * 8  # ~480 chars
        cmd = [
            sys.executable,
            str(_engine_path()),
            long_topic,
            "--mock",
            "--emit=md",
            "--save-dir",
            str(self.tmp),
        ]
        env = {**os.environ, "LAST30DAYS_SKIP_PREFLIGHT": "1"}
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertNotIn("File name too long", result.stderr)
        files = sorted(self.tmp.glob("*.md"))
        self.assertGreaterEqual(
            len(files), 1,
            msg=f"No file saved for long topic. stderr: {result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
