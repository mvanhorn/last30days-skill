import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_mock_json(topic: str) -> dict:
    result = subprocess.run(
        [sys.executable, "scripts/last30days.py", topic, "--mock", "--emit=json"],
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

    def test_openclaw_three_way_comparison_preserves_entities(self):
        payload = run_mock_json("openclaw vs. nanoclaw vs. ironclaw")
        self.assert_common_shape(payload)
        plan = payload["query_plan"]
        self.assertEqual("comparison", plan["intent"])
        joined_queries = "\n".join(subquery["search_query"] for subquery in plan["subqueries"]).lower()
        self.assertIn("openclaw", joined_queries)
        self.assertIn("nanoclaw", joined_queries)
        self.assertIn("ironclaw", joined_queries)
        self.assertNotIn("corsair", joined_queries)
        self.assertNotIn("mouse", joined_queries)
        for subquery in plan["subqueries"]:
            self.assertGreaterEqual(len(subquery["sources"]), 4)

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
        self.assert_common_shape(payload)
        plan = payload["query_plan"]
        self.assertEqual("comparison", plan["intent"])
        joined_queries = "\n".join(subquery["search_query"] for subquery in plan["subqueries"]).lower()
        self.assertIn("deepseek r1", joined_queries)
        self.assertIn("gpt-5", joined_queries)
        self.assertNotIn("corsair", joined_queries)


if __name__ == "__main__":
    unittest.main()
