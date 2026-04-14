import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = ROOT / ".claude-plugin" / "plugin.json"
ROOT_SKILL = ROOT / "SKILL.md"
PLUGIN_RUNTIME_SKILL = ROOT / "skills" / "last30days-plugin" / "SKILL.md"


class TestClaudePluginManifest(unittest.TestCase):
    def test_plugin_points_to_plugin_local_runtime_skill_dir(self) -> None:
        manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(["./skills/last30days-plugin"], manifest.get("skills"))

    def test_plugin_runtime_skill_matches_root_runtime_skill(self) -> None:
        self.assertTrue(PLUGIN_RUNTIME_SKILL.exists(), str(PLUGIN_RUNTIME_SKILL))
        self.assertEqual(
            ROOT_SKILL.read_text(encoding="utf-8"),
            PLUGIN_RUNTIME_SKILL.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
