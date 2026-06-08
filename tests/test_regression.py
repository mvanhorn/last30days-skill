import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_mock_json(topic: str) -> dict:
    result = subprocess.run(
        [sys.executable, "skills/last30days/scripts/last30days.py", topic, "--mock", "--emit=json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"mock CLI failed for {topic!r}: {result.stderr}")
    return json.loads(result.stdout)


class RegressionTests(unittest.TestCase):
    def assert_common_shape(self, payload: dict) -> None:
        self.assertIn("topic", payload)
        self.assertIn("query_plan", payload)
        self.assertIn("ranked_candidates", payload)
        self.assertIn("clusters", payload)
        self.assertIn("items_by_source", payload)

    def assert_comparison_shape(self, payload: dict) -> None:
        """Post-3.0.13: vs-topics produce N full passes, merged output has
        comparison=True + entities list + per-entity report wrapper."""
        self.assertTrue(payload.get("comparison"))
        self.assertIn("entities", payload)
        self.assertIn("reports", payload)
        self.assertEqual(len(payload["entities"]), len(payload["reports"]))
        # Each report entry wraps a single-topic report
        for entry in payload["reports"]:
            self.assertIn("entity", entry)
            self.assertIn("report", entry)
            # Inner report still has the single-topic shape
            inner = entry["report"]
            self.assertIn("topic", inner)
            self.assertIn("query_plan", inner)
            self.assertIn("clusters", inner)

    def test_openclaw_three_way_comparison_preserves_entities(self):
        payload = run_mock_json("openclaw vs. nanoclaw vs. ironclaw")
        self.assert_comparison_shape(payload)
        entities = [e.lower() for e in payload["entities"]]
        self.assertIn("openclaw", entities)
        self.assertIn("nanoclaw", entities)
        self.assertIn("ironclaw", entities)
        # No cross-entity keyword pollution in any per-entity report's plan
        for entry in payload["reports"]:
            plan = entry["report"]["query_plan"]
            joined = "\n".join(
                sq["search_query"] for sq in plan["subqueries"]
            ).lower()
            self.assertNotIn("corsair", joined)
            self.assertNotIn("mouse", joined)

    def test_how_to_keeps_web_video_and_discussion_sources(self):
        payload = run_mock_json("how to deploy on Fly.io")
        self.assert_common_shape(payload)
        plan = payload["query_plan"]
        self.assertEqual("how_to", plan["intent"])
        sources = set(plan["subqueries"][0]["sources"])
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)
        self.assertGreaterEqual(len(sources), 2)

    def test_breaking_news_query_keeps_expected_shape(self):
        payload = run_mock_json("latest news about React 20")
        self.assert_common_shape(payload)
        plan = payload["query_plan"]
        self.assertEqual("breaking_news", plan["intent"])
        joined_queries = "\n".join(subquery["search_query"] for subquery in plan["subqueries"]).lower()
        self.assertIn("react 20", joined_queries)
        self.assertGreaterEqual(len(plan["subqueries"][0]["sources"]), 2)

    def test_two_way_comparison_preserves_exact_strings(self):
        payload = run_mock_json("DeepSeek R1 vs GPT-5")
        self.assert_comparison_shape(payload)
        entities_lower = [e.lower() for e in payload["entities"]]
        self.assertIn("deepseek r1", entities_lower)
        self.assertIn("gpt-5", entities_lower)
        # Each per-entity pass has its own entity in its plan
        topics_by_entity = {
            entry["entity"].lower(): entry["report"]["topic"].lower()
            for entry in payload["reports"]
        }
        self.assertEqual(topics_by_entity["deepseek r1"], "deepseek r1")
        self.assertEqual(topics_by_entity["gpt-5"], "gpt-5")
        # No cross-entity pollution
        for entry in payload["reports"]:
            plan = entry["report"]["query_plan"]
            joined = "\n".join(
                sq["search_query"] for sq in plan["subqueries"]
            ).lower()
            self.assertNotIn("corsair", joined)


if __name__ == "__main__":
    unittest.main()
