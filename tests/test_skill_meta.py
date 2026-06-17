"""Direct unit tests for skill_meta.read_skill_version.

Covers the helper's own contract independent of render._skill_version which
exercises it transitively. Without these, regressions in error handling or
regex coverage inside the helper could pass CI because render.py's fallback
to "?" swallows the signal.
"""

import tempfile
import unittest
from pathlib import Path

from lib.skill_meta import read_skill_version

ROOT = Path(__file__).resolve().parents[1]


class ReadSkillVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_skill_md(self, body: str) -> Path:
        path = self.tmp_path / "SKILL.md"
        path.write_text(body)
        return path

    def test_double_quoted_version(self) -> None:
        path = self._write_skill_md('---\nname: x\nversion: "9.9.9"\n---\n')
        self.assertEqual("9.9.9", read_skill_version(path))

    def test_single_quoted_version(self) -> None:
        path = self._write_skill_md("---\nname: x\nversion: '8.8.8'\n---\n")
        self.assertEqual("8.8.8", read_skill_version(path))

    def test_unquoted_version(self) -> None:
        path = self._write_skill_md("---\nname: x\nversion: 7.7.7\n---\n")
        self.assertEqual("7.7.7", read_skill_version(path))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(read_skill_version(self.tmp_path / "does-not-exist.md"))

    def test_no_version_line_returns_none(self) -> None:
        path = self._write_skill_md("---\nname: x\n---\n# body without version\n")
        self.assertIsNone(read_skill_version(path))

    def test_undecodable_bytes_returns_none(self) -> None:
        # Bytes 128-255 don't form valid UTF-8 sequences; read_text() raises
        # UnicodeDecodeError which the helper must catch.
        path = self.tmp_path / "SKILL.md"
        path.write_bytes(bytes(range(128, 256)))
        self.assertIsNone(read_skill_version(path))

if __name__ == "__main__":
    unittest.main()
