import json
import re
import tomllib
import unittest
from pathlib import Path

from lib.skill_meta import read_skill_version

ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "skills" / "last30days"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _skill_version() -> str:
    version = read_skill_version(SKILL_ROOT / "SKILL.md")
    if not version:
        raise AssertionError("SKILL.md version frontmatter not found")
    return version


class TestPluginContract(unittest.TestCase):
    def test_codex_plugin_scaffold_stays_removed(self) -> None:
        # .codex-plugin/ was removed in the resolver-collapse refactor; Codex users
        # install via `npx skills add` or `~/.codex/skills/`. A reintroduction would
        # silently fork the install surface.
        self.assertFalse((ROOT / ".codex-plugin").exists())

    def test_versions_match_across_manifests(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = pyproject["project"]["version"]

        self.assertEqual(version, _skill_version())
        self.assertEqual(version, _json(ROOT / ".claude-plugin" / "plugin.json")["version"])
        self.assertEqual(version, _json(ROOT / "gemini-extension.json")["version"])

        marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
        plugins = marketplace.get("plugins") or []
        self.assertEqual(1, len(plugins))
        self.assertEqual(version, plugins[0]["version"])

    def test_claude_marketplace_has_current_schema_shape(self) -> None:
        marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")

        self.assertNotIn("$schema", marketplace)
        self.assertNotIn("description", marketplace)
        self.assertIn("metadata", marketplace)
        self.assertIn("description", marketplace["metadata"])

    def test_workflows_do_not_reference_removed_root_scripts_dir(self) -> None:
        offenders = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "scripts/" in line and "skills/last30days/scripts/" not in line:
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

        self.assertEqual([], offenders)

    def test_skill_reference_split_keeps_hot_path_contract_reachable(self) -> None:
        skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        refs = sorted(set(re.findall(r"`(references/[^`]+)`", skill_text)))
        missing_refs = [ref for ref in refs if not (SKILL_ROOT / ref).exists()]
        self.assertEqual([], missing_refs)

        troubleshooting = (SKILL_ROOT / "references" / "troubleshooting.md").read_text(encoding="utf-8")
        self.assertIn("marketplaces", troubleshooting)
        self.assertIn("CLAUDE_CACHE_SKILL_MD", troubleshooting)
        self.assertIn("scripts/lib/setup_wizard.py", troubleshooting)
        self.assertNotIn("skills/last30days/nux-wizard.md", troubleshooting)

        self.assertIn("CLAUDE_CACHE_SKILL_MD", skill_text)
        self.assertIn("${LAST30DAYS_PYTHON}", skill_text)
        self.assertNotIn("python3 \"$SKILL_ROOT/scripts/last30days.py\"", skill_text)

        nux = (SKILL_ROOT / "references" / "nux-wizard.md").read_text(encoding="utf-8")
        self.assertTrue(nux.lstrip().startswith("# Post-Research Invitations"))
        self.assertIn("First-run setup lives in `references/troubleshooting.md`", nux)

if __name__ == "__main__":
    unittest.main()
