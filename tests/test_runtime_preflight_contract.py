"""Contract tests for the SKILL.md runtime preflight snippet."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = ROOT / "skills" / "last30days" / "SKILL.md"


class RuntimePreflightContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill_md = SKILL_MD.read_text(encoding="utf-8")

    def test_windows_localappdata_python_install_dir_is_scanned_first(self) -> None:
        scan_command = 'find "$windows_python_root" -maxdepth 2 -type f -iname python.exe'

        self.assertIn('windows_path_to_unix "$LOCALAPPDATA")/Programs/Python', self.skill_md)
        self.assertIn('windows_path_to_unix "$ProgramFiles"', self.skill_md)
        self.assertIn("printenv 'ProgramFiles(x86)'", self.skill_md)
        self.assertIn("cygpath -u", self.skill_md)
        self.assertIn(scan_command, self.skill_md)
        self.assertNotIn("/c/Users/", self.skill_md)
        self.assertNotIn("Python314/python.exe", self.skill_md)
        self.assertLess(self.skill_md.index(scan_command), self.skill_md.index("python3.14"))

    def test_candidate_selection_accepts_any_python_3_12_or_newer(self) -> None:
        self.assertIn("try_last30days_python()", self.skill_md)
        self.assertIn("sys.version_info >= (3, 12)", self.skill_md)

    def test_preflight_allows_explicit_interpreter_override(self) -> None:
        self.assertIn('if [ -z "${LAST30DAYS_PYTHON:-}" ]; then', self.skill_md)
        self.assertIn('ERROR: LAST30DAYS_PYTHON must point to Python 3.12+.', self.skill_md)


if __name__ == "__main__":
    unittest.main()
