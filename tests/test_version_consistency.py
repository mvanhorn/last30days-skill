import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _skill_version() -> str:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r'^version:\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if not match:
        raise AssertionError("SKILL.md version frontmatter not found")
    return match.group(1)


class TestVersionConsistency(unittest.TestCase):
    def test_root_skill_header_matches_frontmatter_version(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        version = _skill_version()
        self.assertIn(f"# last30days v{version}:", text)

    def test_sync_cache_path_uses_skill_version(self) -> None:
        sync_text = (ROOT / "scripts" / "sync.sh").read_text(encoding="utf-8")
        version = _skill_version()
        self.assertIn(f'last30days-3/{version}"', sync_text)

    def test_memory_save_dir_uses_single_env_variable(self) -> None:
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        compare_text = (ROOT / "scripts" / "compare.sh").read_text(encoding="utf-8")
        default_assignment = 'LAST30DAYS_MEMORY_DIR="${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"'

        self.assertIn(default_assignment, skill_text)
        self.assertIn(default_assignment, compare_text)
        self.assertNotIn("--save-dir=~/Documents/Last30Days", skill_text)
        self.assertIn('--save-dir="${LAST30DAYS_MEMORY_DIR}"', skill_text)

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
