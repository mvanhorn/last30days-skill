import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR_ROOT = ROOT / "docs" / "adr"


class TestAdrDocs(unittest.TestCase):
    def test_adr_index_and_records_exist(self) -> None:
        expected = {
            "README.md",
            "001-multi-surface-packaging.md",
            "002-eval-not-in-ci.md",
        }

        self.assertTrue(ADR_ROOT.is_dir())
        self.assertEqual(expected, {path.name for path in ADR_ROOT.glob("*.md")})

    def test_adr_records_capture_required_decisions(self) -> None:
        packaging = (ADR_ROOT / "001-multi-surface-packaging.md").read_text(encoding="utf-8")
        eval_policy = (ADR_ROOT / "002-eval-not-in-ci.md").read_text(encoding="utf-8")

        self.assertIn("Status: Accepted", packaging)
        self.assertIn("skills/last30days/scripts/sync.sh", packaging)
        self.assertIn("multi-surface", packaging.lower())
        self.assertIn("lib/__init__.py", packaging)
        self.assertIn("bare package marker", packaging)

        self.assertIn("Status: Accepted", eval_policy)
        self.assertIn("skills/last30days/scripts/evaluate_search_quality.py", eval_policy)
        self.assertIn("not a CI gate", eval_policy)
        self.assertIn("workflow_dispatch", eval_policy)
        self.assertIn("live API", eval_policy)

    def test_repo_docs_link_to_adr_index(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("docs/adr/", readme)
        self.assertIn("docs/adr/", agents)


if __name__ == "__main__":
    unittest.main()
