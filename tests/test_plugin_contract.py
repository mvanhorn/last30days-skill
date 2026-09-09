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

    def test_session_start_hook_uses_bare_claude_plugin_root(self) -> None:
        # Claude Code's missing-plugin-root guard matches the exact literal
        # ${CLAUDE_PLUGIN_ROOT}. Defaults like ${CLAUDE_PLUGIN_ROOT:-.} slip past
        # that check and resolve relative to the session cwd (issue #1074).
        hooks = _json(ROOT / "hooks" / "hooks.json")
        command = hooks["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", command)
        self.assertNotIn(":-", command)
        self.assertNotIn("extensionPath", command)
        self.assertNotIn(":-.}", command)

    def test_mcp_manifest_credential_entries_are_sensitive(self) -> None:
        # Every user_config entry that names a credential (title ends in _KEY
        # or _TOKEN, or _PASSWORD) must be marked sensitive so Claude Desktop
        # stores it in the OS keychain instead of plain config.
        manifest = _json(ROOT / "mcp" / "manifest.json")
        user_config = manifest["user_config"]
        self.assertTrue(user_config)
        offenders = [
            key
            for key, entry in user_config.items()
            if str(entry.get("title", "")).endswith(("_KEY", "_TOKEN", "_PASSWORD"))
            and entry.get("sensitive") is not True
        ]
        self.assertEqual([], offenders)

    def test_mcp_manifest_declares_x_bearer_token(self) -> None:
        # U6/R17: the X API bearer is installable through the MCP bundle like
        # every other credential, wired user_config -> env under the
        # lowercased-env-var convention mcp/internal/manifest enforces.
        manifest = _json(ROOT / "mcp" / "manifest.json")
        entry = manifest["user_config"]["x_bearer_token"]
        self.assertEqual("X_BEARER_TOKEN", entry["title"])
        self.assertEqual("string", entry["type"])
        self.assertIs(True, entry["sensitive"])
        self.assertIs(False, entry["required"])
        self.assertIn("X API v2", entry["description"])
        self.assertIn("bearer", entry["description"].lower())
        self.assertIn("developer.x.com", entry["description"])
        env_map = manifest["server"]["mcp_config"]["env"]
        self.assertEqual("${user_config.x_bearer_token}", env_map["X_BEARER_TOKEN"])

    def test_mcp_manifest_env_and_user_config_cross_reference(self) -> None:
        # Python-side mirror of mcp/internal/manifest/manifest_test.go so the
        # invariant is checked by `uv run pytest` too.
        manifest = _json(ROOT / "mcp" / "manifest.json")
        env_map = manifest["server"]["mcp_config"]["env"]
        user_config = manifest["user_config"]
        for env_name, value in env_map.items():
            self.assertEqual(f"${{user_config.{env_name.lower()}}}", value)
            self.assertIn(env_name.lower(), user_config)
        for key in user_config:
            self.assertIn(key.upper(), env_map)

if __name__ == "__main__":
    unittest.main()
