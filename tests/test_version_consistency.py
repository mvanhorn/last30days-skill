import re
import unittest
from pathlib import Path

from lib.skill_meta import read_skill_version

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "last30days"


def _skill_version() -> str:
    version = read_skill_version(SKILL_ROOT / "SKILL.md")
    if not version:
        raise AssertionError("SKILL.md version frontmatter not found")
    return version


class TestVersionConsistency(unittest.TestCase):
    def test_skill_md_uses_double_quoted_version(self) -> None:
        # The shared VERSION_RE in skill_meta.py accepts double-quoted,
        # single-quoted, and unquoted YAML version scalars. This repo's
        # SKILL.md must use the double-quoted form so the badge string stays
        # deterministic and contributors don't accidentally introduce a
        # quoting style that's harder for downstream tooling to parse.
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            re.compile(r'^version:\s*"[^"]+"\s*$', re.MULTILINE),
            msg="SKILL.md frontmatter version must use double-quoted form",
        )

    def test_root_skill_header_matches_frontmatter_version(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        version = _skill_version()
        self.assertIn(f"# last30days v{version}:", text)

    def test_memory_save_dir_uses_single_env_variable(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        compare_text = (SKILL_ROOT / "scripts" / "compare.sh").read_text(encoding="utf-8")
        default_assignment = 'LAST30DAYS_MEMORY_DIR="${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"'

        self.assertIn(default_assignment, skill_text)
        self.assertIn(default_assignment, compare_text)
        self.assertNotIn("--save-dir=~/Documents/Last30Days", skill_text)
        self.assertIn('--save-dir="${LAST30DAYS_MEMORY_DIR}"', skill_text)

    def test_compare_script_does_not_skip_permissions(self) -> None:
        compare_text = (SKILL_ROOT / "scripts" / "compare.sh").read_text(encoding="utf-8")

        self.assertNotIn("--dangerously-skip-permissions", compare_text)

    def test_no_stray_hardcoded_memory_dir_paths(self) -> None:
        allowed_suffixes = {".md", ".py", ".sh", ".txt", ".yml", ".yaml", ".json"}
        skip_dirs = {".git", "assets", "fixtures", "docs"}
        offenders = []

        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in allowed_suffixes:
                continue
            if skip_dirs.intersection(path.relative_to(ROOT).parts):
                continue
            if path.relative_to(ROOT) == Path("tests/test_version_consistency.py"):
                continue

            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for line_number, line in enumerate(lines, start=1):
                if "~/Documents/Last30Days" not in line and "$HOME/Documents/Last30Days" not in line:
                    continue
                allowed_default = (
                    "LAST30DAYS_MEMORY_DIR" in line
                    and ("defaults to" in line or "${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}" in line)
                )
                if not allowed_default:
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

        self.assertEqual([], offenders)

if __name__ == "__main__":
    unittest.main()
