"""Tests for query type detection and source tiering."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib.query_type import (
    detect_query_type,
    is_source_enabled,
    WEBSEARCH_PENALTY_BY_TYPE,
    TIEBREAKER_BY_TYPE,
    SOURCE_TIERS,
)


class TestDetectQueryType(unittest.TestCase):

    def test_product_queries(self):
        self.assertEqual(detect_query_type("cursor IDE pricing"), "product")
        self.assertEqual(detect_query_type("is Claude Pro worth the cost"), "product")
        self.assertEqual(detect_query_type("best free tier LLM API"), "product")

    def test_concept_queries(self):
        self.assertEqual(detect_query_type("what is WebTransport"), "concept")
        self.assertEqual(detect_query_type("explain React Server Components"), "concept")
        self.assertEqual(detect_query_type("how does MCP protocol work"), "concept")

    def test_opinion_queries(self):
        self.assertEqual(detect_query_type("is cursor worth it"), "opinion")
        self.assertEqual(detect_query_type("thoughts on Claude Code"), "opinion")
        self.assertEqual(detect_query_type("should i switch to Neovim"), "opinion")

    def test_howto_queries(self):
        self.assertEqual(detect_query_type("how to deploy on Vercel"), "how_to")
        self.assertEqual(detect_query_type("tutorial for building MCP servers"), "how_to")
        self.assertEqual(detect_query_type("step by step Kubernetes setup"), "how_to")
        self.assertEqual(detect_query_type("nano banana pro prompting"), "how_to")
        self.assertEqual(detect_query_type("remotion animations for Claude Code"), "how_to")

    def test_comparison_queries(self):
        self.assertEqual(detect_query_type("cursor vs windsurf"), "comparison")
        self.assertEqual(detect_query_type("Claude compared to GPT-5"), "comparison")
        self.assertEqual(detect_query_type("difference between React and Vue"), "comparison")

    def test_breaking_news_queries(self):
        self.assertEqual(detect_query_type("latest AI funding rounds"), "breaking_news")
        self.assertEqual(detect_query_type("OpenAI just announced GPT-6"), "breaking_news")

    def test_prediction_queries(self):
        self.assertEqual(detect_query_type("odds of Fed rate cut"), "prediction")
        self.assertEqual(detect_query_type("predict the next recession"), "prediction")
        self.assertEqual(detect_query_type("election outcome 2028"), "prediction")

    def test_default_is_breaking_news(self):
        self.assertEqual(detect_query_type("tariffs"), "breaking_news")
        self.assertEqual(detect_query_type("AI agents"), "breaking_news")

    def test_comparison_beats_product(self):
        """Comparison is more specific than product."""
        self.assertEqual(detect_query_type("cursor vs windsurf pricing"), "comparison")

    def test_howto_beats_concept(self):
        """How-to is more specific than concept."""
        self.assertEqual(detect_query_type("how to explain transformers"), "how_to")

    def test_will_alone_not_prediction(self):
        """Bare 'will' should not trigger prediction classification."""
        self.assertNotEqual(detect_query_type("Will React 19 support concurrent mode"), "prediction")

    def test_or_for_not_comparison(self):
        """'or X for Y' should not trigger comparison classification."""
        self.assertNotEqual(detect_query_type("best tools or libraries for Python"), "comparison")


class TestIsSourceEnabled(unittest.TestCase):

    def test_truthsocial_always_opt_in(self):
        for qt in ["product", "concept", "opinion", "breaking_news", "prediction"]:
            self.assertFalse(is_source_enabled("truthsocial", qt))
        self.assertTrue(is_source_enabled("truthsocial", "breaking_news", explicitly_requested=True))

    def test_tier1_sources_enabled(self):
        self.assertTrue(is_source_enabled("reddit", "product"))
        self.assertTrue(is_source_enabled("youtube", "how_to"))
        self.assertTrue(is_source_enabled("polymarket", "prediction"))
        self.assertTrue(is_source_enabled("x", "breaking_news"))

    def test_tier2_sources_enabled(self):
        self.assertTrue(is_source_enabled("web", "product"))
        self.assertTrue(is_source_enabled("bluesky", "opinion"))
        self.assertTrue(is_source_enabled("x", "how_to"))
        self.assertTrue(is_source_enabled("youtube", "breaking_news"))
        self.assertTrue(is_source_enabled("hn", "prediction"))

    def test_tier3_sources_disabled_by_default(self):
        self.assertFalse(is_source_enabled("instagram", "concept"))
        self.assertFalse(is_source_enabled("tiktok", "comparison"))
        self.assertFalse(is_source_enabled("bluesky", "product"))

    def test_explicit_request_overrides_tier(self):
        self.assertTrue(is_source_enabled("instagram", "concept", explicitly_requested=True))
        self.assertTrue(is_source_enabled("tiktok", "comparison", explicitly_requested=True))


class TestWebSearchPenalty(unittest.TestCase):

    def test_concept_has_zero_penalty(self):
        self.assertEqual(WEBSEARCH_PENALTY_BY_TYPE["concept"], 0)

    def test_product_has_full_penalty(self):
        self.assertEqual(WEBSEARCH_PENALTY_BY_TYPE["product"], 15)

    def test_howto_has_reduced_penalty(self):
        self.assertLess(WEBSEARCH_PENALTY_BY_TYPE["how_to"], WEBSEARCH_PENALTY_BY_TYPE["product"])

    def test_all_query_types_have_penalty(self):
        for qt in ["product", "concept", "opinion", "how_to", "comparison", "breaking_news", "prediction"]:
            self.assertIn(qt, WEBSEARCH_PENALTY_BY_TYPE)


class TestTiebreakerPriority(unittest.TestCase):

    def test_youtube_highest_for_howto(self):
        self.assertEqual(TIEBREAKER_BY_TYPE["how_to"]["youtube"], 0)

    def test_x_highest_for_breaking_news(self):
        self.assertEqual(TIEBREAKER_BY_TYPE["breaking_news"]["x"], 0)

    def test_polymarket_highest_for_prediction(self):
        self.assertEqual(TIEBREAKER_BY_TYPE["prediction"]["polymarket"], 0)

    def test_hn_highest_for_concept(self):
        self.assertEqual(TIEBREAKER_BY_TYPE["concept"]["hn"], 0)

    def test_all_query_types_have_tiebreakers(self):
        for qt in ["product", "concept", "opinion", "how_to", "comparison", "breaking_news", "prediction"]:
            self.assertIn(qt, TIEBREAKER_BY_TYPE)


class TestSourceTiers(unittest.TestCase):

    def test_all_query_types_have_tiers(self):
        for qt in ["product", "concept", "opinion", "how_to", "comparison", "breaking_news", "prediction"]:
            self.assertIn(qt, SOURCE_TIERS)
            self.assertIn("tier1", SOURCE_TIERS[qt])
            self.assertIn("tier2", SOURCE_TIERS[qt])

    def test_truthsocial_not_in_any_tier(self):
        for qt, tiers in SOURCE_TIERS.items():
            self.assertNotIn("truthsocial", tiers["tier1"], f"truthsocial in tier1 for {qt}")
            self.assertNotIn("truthsocial", tiers["tier2"], f"truthsocial in tier2 for {qt}")


if __name__ == "__main__":
    unittest.main()
