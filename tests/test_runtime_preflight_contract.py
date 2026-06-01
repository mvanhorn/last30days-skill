"""Contract tests for the SKILL.md runtime preflight snippet."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


class RuntimePreflightContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_md = SKILL_MD.read_text(encoding="utf-8")

    def test_windows_localappdata_python_paths_are_checked_first(self) -> None:
        python314 = "${LOCALAPPDATA:-}/Programs/Python/Python314/python.exe"
        python313 = "${LOCALAPPDATA:-}/Programs/Python/Python313/python.exe"
        python312 = "${LOCALAPPDATA:-}/Programs/Python/Python312/python.exe"

        self.assertIn(python314, self.skill_md)
        self.assertIn(python313, self.skill_md)
        self.assertIn(python312, self.skill_md)
        self.assertLess(self.skill_md.index(python314), self.skill_md.index("python3.14"))

    def test_preflight_allows_explicit_interpreter_override(self) -> None:
        self.assertIn('if [ -z "${LAST30DAYS_PYTHON:-}" ]; then', self.skill_md)
        self.assertIn('ERROR: LAST30DAYS_PYTHON must point to Python 3.12+.', self.skill_md)


if __name__ == "__main__":
    unittest.main()
