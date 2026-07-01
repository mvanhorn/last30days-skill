import json
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
    def test_codex_plugin_manifest_uses_repo_skill_root(self) -> None:
        manifest = _json(ROOT / ".codex-plugin" / "plugin.json")

        self.assertEqual("last30days", manifest["name"])
        self.assertEqual("./skills/", manifest["skills"])
        self.assertEqual("last30days", manifest["interface"]["displayName"])

    def test_codex_marketplace_points_at_repo_root_plugin(self) -> None:
        marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
        plugins = marketplace.get("plugins") or []
        plugin_by_name = {plugin["name"]: plugin for plugin in plugins}

        self.assertEqual("last30days-skill", marketplace["name"])
        self.assertIn("last30days", plugin_by_name)
        plugin = plugin_by_name["last30days"]
        self.assertEqual(
            {
                "source": "url",
                "url": "https://github.com/mvanhorn/last30days-skill.git",
            },
            plugin["source"],
        )

    def test_versions_match_across_manifests(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = pyproject["project"]["version"]

        self.assertEqual(version, _skill_version())
        self.assertEqual(version, _json(ROOT / ".claude-plugin" / "plugin.json")["version"])
        self.assertEqual(version, _json(ROOT / ".codex-plugin" / "plugin.json")["version"])
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
        # The root-level scripts/ directory was removed; workflows must not
        # reference it. Subdirectory scripts/ paths (skills/last30days/scripts/
        # for the Code-skill build, mcp/scripts/ for the .mcpb build) are
        # the legitimate replacements.
        allowed_prefixes = (
            "skills/last30days/scripts/",
            "mcp/scripts/",
        )
        offenders = []
        for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "scripts/" not in line:
                    continue
                if any(prefix in line for prefix in allowed_prefixes):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")

        self.assertEqual([], offenders)

if __name__ == "__main__":
    unittest.main()
