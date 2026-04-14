import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATHS = (
    ROOT / "SKILL.md",
    ROOT / ".agents" / "skills" / "last30days" / "SKILL.md",
)

SETUP_NON_WEBSEARCH_LINE = (
    "**If you do NOT have WebSearch capability in this session "
    "(for example, OpenClaw or a raw CLI session):** Run the OpenClaw setup flow below."
)
SETUP_WEBSEARCH_LINE = (
    "**If you DO have WebSearch capability in this session "
    "(for example, Claude Code or Codex with search enabled):** Run the standard setup flow below."
)
AUTO_RESOLVE_LINE = (
    "**If you skipped Steps 0.55 and 0.75 because WebSearch is unavailable in this session, add:**"
)
PLAN_LINE = (
    "**If you ran Steps 0.55 and 0.75 with WebSearch, do NOT add `--auto-resolve`.** "
    "The Python engine will use your `--plan` and resolved targeting flags."
)


class TestSkillRouting(unittest.TestCase):
    def test_shipped_skill_docs_route_by_capability_not_product_name(self) -> None:
        for path in SKILL_PATHS:
            text = path.read_text(encoding="utf-8")
            self.assertIn(SETUP_NON_WEBSEARCH_LINE, text, str(path))
            self.assertIn(SETUP_WEBSEARCH_LINE, text, str(path))
            self.assertIn(AUTO_RESOLVE_LINE, text, str(path))
            self.assertIn(PLAN_LINE, text, str(path))
            self.assertNotIn("OpenClaw, Codex, raw CLI", text, str(path))
            self.assertNotIn("no WebSearch -- OpenClaw, Codex, etc.", text, str(path))


if __name__ == "__main__":
    unittest.main()
