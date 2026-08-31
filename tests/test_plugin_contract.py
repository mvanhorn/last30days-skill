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

    def test_grok_plugin_manifest_uses_repo_skill_root(self) -> None:
        manifest = _json(ROOT / ".grok-plugin" / "plugin.json")

        self.assertEqual("last30days", manifest["name"])
        self.assertEqual("./skills/", manifest["skills"])

    def test_grok_marketplace_points_at_repo_root_plugin(self) -> None:
        marketplace = _json(ROOT / ".grok-plugin" / "marketplace.json")
        plugins = marketplace.get("plugins") or []
        plugin_by_name = {plugin["name"]: plugin for plugin in plugins}

        self.assertEqual("last30days-skill", marketplace["name"])
        self.assertIn("last30days", plugin_by_name)
        plugin = plugin_by_name["last30days"]
        # Exact dict equality locks the bare Git URL source (anti-self-referential-local).
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
        self.assertEqual(version, _json(ROOT / ".grok-plugin" / "plugin.json")["version"])
        self.assertEqual(version, _json(ROOT / "gemini-extension.json")["version"])

        marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
        plugins = marketplace.get("plugins") or []
        self.assertEqual(1, len(plugins))
        self.assertEqual(version, plugins[0]["version"])

        grok_marketplace = _json(ROOT / ".grok-plugin" / "marketplace.json")
        grok_plugins = grok_marketplace.get("plugins") or []
        self.assertEqual(1, len(grok_plugins))
        self.assertEqual(version, grok_plugins[0]["version"])

    def test_claude_marketplace_has_current_schema_shape(self) -> None:
        marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")

        self.assertNotIn("$schema", marketplace)
        self.assertNotIn("description", marketplace)
        self.assertIn("metadata", marketplace)
        self.assertIn("description", marketplace["metadata"])

    def test_grok_marketplace_has_current_schema_shape(self) -> None:
        marketplace = _json(ROOT / ".grok-plugin" / "marketplace.json")

        self.assertNotIn("$schema", marketplace)
        self.assertNotIn("metadata", marketplace)
        self.assertIsInstance(marketplace["description"], str)
        self.assertIn("name", marketplace)
        self.assertIn("owner", marketplace)
        self.assertIn("plugins", marketplace)

    def test_hooks_json_supports_grok_plugin_root(self) -> None:
        hooks = _json(ROOT / "hooks" / "hooks.json")
        session_start = hooks["hooks"]["SessionStart"]
        self.assertTrue(len(session_start) >= 1)
        hook_cmd = session_start[0]["hooks"][0]["command"]
        # Should reference GROK_PLUGIN_ROOT first, then CLAUDE_PLUGIN_ROOT, then extensionPath
        self.assertIn("GROK_PLUGIN_ROOT", hook_cmd)
        self.assertIn("CLAUDE_PLUGIN_ROOT", hook_cmd)
        self.assertIn("extensionPath", hook_cmd)
        # Order should be GROK first, then CLAUDE, then extensionPath
        grok_idx = hook_cmd.index("GROK_PLUGIN_ROOT")
        claude_idx = hook_cmd.index("CLAUDE_PLUGIN_ROOT")
        ext_idx = hook_cmd.index("extensionPath")
        self.assertLess(grok_idx, claude_idx)
        self.assertLess(claude_idx, ext_idx)

    def test_check_config_sh_resolves_grok_plugin_root(self) -> None:
        check_config = (ROOT / "hooks" / "scripts" / "check-config.sh").read_text(encoding="utf-8")
        # Should resolve plugin root with Grok priority
        self.assertIn("GROK_PLUGIN_ROOT", check_config)
        self.assertIn("CLAUDE_PLUGIN_ROOT", check_config)
        self.assertIn("extensionPath", check_config)
        # Order: GROK first
        grok_idx = check_config.index("GROK_PLUGIN_ROOT")
        claude_idx = check_config.index("CLAUDE_PLUGIN_ROOT")
        self.assertLess(grok_idx, claude_idx)
        # Should use the resolved PLUGIN_ROOT for mkdir
        self.assertIn("PLUGIN_ROOT", check_config)

    def test_skill_mentions_grok_in_non_modal_prose_flow(self) -> None:
        skill_md = (ROOT / "skills" / "last30days" / "SKILL.md").read_text(encoding="utf-8")
        # Non-Modal Prose Flow host list should include Grok (xAI Build CLI)
        self.assertIn("Grok (xAI Build CLI)", skill_md)
        # LAW 8 visible-URL host list should include Grok (xAI Build CLI)
        self.assertIn("Grok (xAI Build CLI)", skill_md)
        # Platform gate in Step 0.55 should mention Grok
        self.assertIn("Grok (xAI Build CLI)", skill_md)
        # Platform split should mention Grok (xAI Build CLI)
        self.assertIn("Grok (xAI Build CLI)", skill_md)

    def test_skill_has_grok_tool_equivalents(self) -> None:
        skill_md = (ROOT / "skills" / "last30days" / "SKILL.md").read_text(encoding="utf-8")
        # Should have Grok Build (xAI) Tool Equivalents section
        self.assertIn("Grok Build (xAI) Tool Equivalents", skill_md)
        # Should map Bash to shell tool
        self.assertIn("`Bash`", skill_md)
        self.assertIn("`shell` tool", skill_md)
        # Should map WebSearch to host web search
        self.assertIn("`WebSearch`", skill_md)
        self.assertIn("Host web search", skill_md)
        # Should map AskUserQuestion to host question UI
        self.assertIn("`AskUserQuestion`", skill_md)
        self.assertIn("Host question UI", skill_md)
        # Should recommend XAI_API_KEY for X backend on Grok
        self.assertIn("XAI_API_KEY", skill_md)
        self.assertIn("api.x.ai", skill_md)

    def test_workflows_do_not_reference_removed_root_scripts_dir(self) -> None:
        # The historical root-level scripts/ directory was removed; workflows must not
        # reference a bare `scripts/` path. Allowed replacements:
        # skills/last30days/scripts/ (engine), mcp/scripts/ (.mcpb), .github/scripts/
        # (release automation).
        allowed_prefixes = (
            "skills/last30days/scripts/",
            "mcp/scripts/",
            ".github/scripts/",
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
