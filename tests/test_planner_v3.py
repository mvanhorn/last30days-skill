import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import planner


class PlannerV3Tests(unittest.TestCase):
    def test_default_how_to_expands_past_llm_narrow_source_weights(self):
        raw = {
            "intent": "how_to",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "workflow",
            "source_weights": {"hackernews": 0.7, "reddit": 0.3},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "deploy app to Fly.io guide",
                    "ranking_query": "How do I deploy an app to Fly.io?",
                    "sources": ["hackernews"],
                    "weight": 1.0,
                }
            ],
        }
        plan = planner._sanitize_plan(
            raw,
            "how to deploy on Fly.io",
            ["reddit", "x", "youtube", "hackernews"],
            None,
            "default",
        )
        sources = plan.subqueries[0].sources
        # how_to capability routing selects video + discussion
        self.assertIn("reddit", sources)
        self.assertIn("youtube", sources)
        self.assertIn("reddit", plan.source_weights)
        self.assertIn("youtube", plan.source_weights)
        self.assertEqual("evergreen_ok", plan.freshness_mode)

    def test_comparison_uses_deterministic_plan_and_preserves_entities(self):
        plan = planner.plan_query(
            topic="openclaw vs nanoclaw vs ironclaw",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=object(),
            model="ignored",
        )
        self.assertEqual("comparison", plan.intent)
        self.assertEqual(["deterministic-comparison-plan"], plan.notes)
        self.assertEqual(4, len(plan.subqueries))
        joined_queries = "\n".join(subquery.search_query for subquery in plan.subqueries).lower()
        self.assertIn("openclaw", joined_queries)
        self.assertIn("nanoclaw", joined_queries)
        self.assertIn("ironclaw", joined_queries)

    def test_fallback_plan_emits_dual_query_fields(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        self.assertGreaterEqual(len(plan.subqueries), 2)
        for subquery in plan.subqueries:
            self.assertTrue(subquery.search_query)
            self.assertTrue(subquery.ranking_query)

    def test_factual_topic_uses_no_cluster_mode(self):
        plan = planner.plan_query(
            topic="what is the parameter count of claude code",
            available_sources=["reddit", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("factual", plan.intent)
        self.assertEqual("none", plan.cluster_mode)

    def test_quick_mode_collapses_fallback_to_single_subquery(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x"],
            requested_sources=None,
            depth="quick",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        self.assertEqual(1, len(plan.subqueries))
        self.assertEqual(["reddit", "x"], plan.subqueries[0].sources)

    def test_default_comparison_uses_all_capable_sources(self):
        plan = planner.plan_query(
            topic="codex vs claude code",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        for subquery in plan.subqueries:
            # Default depth should not artificially cap sources
            self.assertGreaterEqual(len(subquery.sources), 4)

    def test_default_how_to_keeps_youtube_in_source_mix(self):
        plan = planner.plan_query(
            topic="how to deploy remotion animations for claude code",
            available_sources=["reddit", "x", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)

    def test_how_to_sources_includes_capability_matched_extras(self):
        """how_to routing should include additional sources beyond the core ones."""
        plan = planner.plan_query(
            topic="how to deploy on Fly.io",
            available_sources=["reddit", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)
        # Additional capability-matched sources should also be included
        self.assertGreater(len(sources), 2,
                           f"how_to should include >2 sources, got {len(sources)}: {sources}")

    def test_ncaa_tournament_is_breaking_news(self):
        intent = planner._infer_intent("NCAA tournament brackets")
        self.assertEqual("breaking_news", intent)

    def test_march_madness_is_breaking_news(self):
        intent = planner._infer_intent("2026 March Madness")
        self.assertEqual("breaking_news", intent)

    def test_factual_plan_has_at_most_2_subqueries(self):
        plan = planner.plan_query(
            topic="who acquired Wiz",
            available_sources=["reddit", "x", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("factual", plan.intent)
        self.assertLessEqual(len(plan.subqueries), 2)

    def test_default_how_to_prefers_longform_video_over_shortform(self):
        plan = planner.plan_query(
            topic="how to deploy on Fly.io",
            available_sources=["reddit", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("how_to", plan.intent)
        sources = plan.subqueries[0].sources
        # how_to routing should include youtube (longform) over tiktok/instagram
        self.assertIn("youtube", sources)
        self.assertIn("reddit", sources)

    def test_prediction_includes_tiktok_and_instagram(self):
        """TikTok and Instagram are no longer excluded from prediction intent."""
        plan = planner.plan_query(
            topic="odds of US recession 2026",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("prediction", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)

    def test_opinion_includes_tiktok_and_instagram(self):
        """TikTok and Instagram are no longer excluded from opinion intent."""
        plan = planner.plan_query(
            topic="thoughts on OpenAI Codex pricing",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("opinion", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)

    def test_comparison_includes_polymarket(self):
        """Polymarket should not be excluded from comparison intent plans."""
        plan = planner.plan_query(
            topic="Sam Altman vs Dario Amodei",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("comparison", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("polymarket", all_sources)

    def test_polymarket_excluded_from_how_to_and_concept(self):
        """Polymarket should remain excluded from how_to and concept intents."""
        for topic, expected_intent in [
            ("how to deploy on Fly.io", "how_to"),
            ("explain transformer architecture", "concept"),
        ]:
            plan = planner.plan_query(
                topic=topic,
                available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
                requested_sources=None,
                depth="default",
                provider=None,
                model=None,
            )
            self.assertEqual(expected_intent, plan.intent)
            all_sources = set()
            for subquery in plan.subqueries:
                all_sources.update(subquery.sources)
            self.assertNotIn("polymarket", all_sources,
                             f"polymarket should be excluded from {expected_intent}")

    def test_opinion_includes_polymarket(self):
        """Polymarket should not be excluded from opinion intent plans."""
        plan = planner.plan_query(
            topic="thoughts on OpenAI future",
            available_sources=["reddit", "x", "youtube", "hackernews", "polymarket"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("opinion", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("polymarket", all_sources)

    def test_breaking_news_includes_tiktok_and_instagram(self):
        plan = planner.plan_query(
            topic="2026 March Madness",
            available_sources=["reddit", "x", "tiktok", "instagram", "youtube", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        self.assertEqual("breaking_news", plan.intent)
        all_sources = set()
        for subquery in plan.subqueries:
            all_sources.update(subquery.sources)
        self.assertIn("tiktok", all_sources)
        self.assertIn("instagram", all_sources)


if __name__ == "__main__":
    unittest.main()
