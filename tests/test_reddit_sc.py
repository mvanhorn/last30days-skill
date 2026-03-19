"""Tests for reddit.py — ScrapeCreators Reddit search module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import reddit


class TestExtractCoreSubject(unittest.TestCase):
    """Tests for _extract_core_subject()."""

    def test_strips_what_are_prefix(self):
        self.assertEqual(reddit._extract_core_subject("what are the best AI tools"), "ai tools")

    def test_strips_how_to_prefix(self):
        self.assertEqual(reddit._extract_core_subject("how to use cursor IDE"), "cursor ide")

    def test_strips_noise_words(self):
        result = reddit._extract_core_subject("latest trending updates")
        self.assertEqual(result, "latest trending updates")

    def test_preserves_product_name(self):
        self.assertEqual(reddit._extract_core_subject("cursor IDE"), "cursor ide")

    def test_strips_trailing_punctuation(self):
        result = reddit._extract_core_subject("what is Claude?")
        self.assertFalse(result.endswith("?"))

    def test_empty_string(self):
        result = reddit._extract_core_subject("")
        self.assertEqual(result, "")

    def test_strips_what_do_people_think(self):
        result = reddit._extract_core_subject("what do people think about React Server Components")
        self.assertEqual(result, "react server components")


class TestInferQueryIntent(unittest.TestCase):
    """Tests for _infer_query_intent()."""

    def test_product_best_budget(self):
        result = reddit._infer_query_intent("best budget noise cancelling headphones 2026")
        self.assertEqual(result, "product")

    def test_product_recommend(self):
        result = reddit._infer_query_intent("recommend a good laptop for coding")
        self.assertEqual(result, "product")

    def test_product_review(self):
        result = reddit._infer_query_intent("Sony WH-1000XM5 review")
        self.assertEqual(result, "product")

    def test_product_alternative(self):
        result = reddit._infer_query_intent("cheap alternative to AirPods Pro")
        self.assertEqual(result, "product")

    def test_comparison_vs(self):
        result = reddit._infer_query_intent("iPhone 16 vs Pixel 9")
        self.assertEqual(result, "comparison")

    def test_comparison_versus(self):
        result = reddit._infer_query_intent("React versus Vue for new projects")
        self.assertEqual(result, "comparison")

    def test_comparison_compared_to(self):
        result = reddit._infer_query_intent("M4 MacBook compared to M3")
        self.assertEqual(result, "comparison")

    def test_how_to(self):
        result = reddit._infer_query_intent("how to set up a home server")
        self.assertEqual(result, "how_to")

    def test_opinion(self):
        result = reddit._infer_query_intent("what do you think about Rust")
        self.assertEqual(result, "opinion")

    def test_factual_default(self):
        result = reddit._infer_query_intent("Claude 4 release date")
        self.assertEqual(result, "factual")


class TestExpandRedditQueries(unittest.TestCase):
    """Tests for expand_reddit_queries()."""

    def test_quick_returns_one_query(self):
        queries = reddit.expand_reddit_queries("cursor IDE", "quick")
        self.assertGreaterEqual(len(queries), 1)

    def test_default_includes_review_variant(self):
        queries = reddit.expand_reddit_queries("cursor IDE", "default")
        self.assertTrue(any("worth it" in q or "review" in q for q in queries))

    def test_deep_includes_issues_variant(self):
        queries = reddit.expand_reddit_queries("cursor IDE", "deep")
        self.assertTrue(any("issues" in q or "problems" in q for q in queries))

    def test_deep_has_more_queries_than_quick(self):
        quick = reddit.expand_reddit_queries("cursor IDE", "quick")
        deep = reddit.expand_reddit_queries("cursor IDE", "deep")
        self.assertGreater(len(deep), len(quick))

    def test_product_always_includes_review_variant(self):
        """Product queries include review-oriented variant at all depths."""
        for depth in ("quick", "default", "deep"):
            queries = reddit.expand_reddit_queries(
                "best budget noise cancelling headphones 2026", depth
            )
            has_review = any(
                "review" in q or "recommendation" in q for q in queries
            )
            self.assertTrue(
                has_review,
                f"Product query at depth={depth} missing review variant: {queries}"
            )

    def test_product_quick_includes_opinion_variant(self):
        """Product queries include 'worth it OR thoughts' even at quick depth."""
        queries = reddit.expand_reddit_queries(
            "best budget noise cancelling headphones 2026", "quick"
        )
        has_opinion = any("worth it" in q or "thoughts" in q for q in queries)
        self.assertTrue(
            has_opinion,
            f"Product query at quick depth missing opinion variant: {queries}"
        )

    def test_comparison_includes_vs_variant(self):
        """Comparison queries include 'vs OR compared' variant."""
        queries = reddit.expand_reddit_queries("iPhone 16 vs Pixel 9", "default")
        has_vs = any("vs" in q or "compared" in q for q in queries)
        self.assertTrue(
            has_vs,
            f"Comparison query missing vs variant: {queries}"
        )

    def test_factual_query_minimal_variants(self):
        """Factual queries don't get product/comparison variants."""
        queries = reddit.expand_reddit_queries("Claude 4 release date", "quick")
        has_review = any("review" in q or "recommendation" in q for q in queries)
        has_vs = any("vs OR compared" in q for q in queries)
        self.assertFalse(has_review, f"Factual query got review variant: {queries}")
        self.assertFalse(has_vs, f"Factual query got vs variant: {queries}")


class TestDiscoverSubreddits(unittest.TestCase):
    """Tests for discover_subreddits()."""

    def test_ranks_by_frequency(self):
        results = [
            {"subreddit": "programming", "score": 10},
            {"subreddit": "programming", "score": 20},
            {"subreddit": "python", "score": 5},
        ]
        subs = reddit.discover_subreddits(results, max_subs=5)
        self.assertEqual(subs[0], "programming")

    def test_utility_sub_penalty(self):
        results = [
            {"subreddit": "tipofmytongue", "score": 100},
            {"subreddit": "tipofmytongue", "score": 100},
            {"subreddit": "python", "score": 10},
        ]
        subs = reddit.discover_subreddits(results, topic="python", max_subs=5)
        self.assertEqual(subs[0], "python")

    def test_topic_name_bonus(self):
        results = [
            {"subreddit": "reactjs", "score": 10},
            {"subreddit": "webdev", "score": 10},
        ]
        subs = reddit.discover_subreddits(results, topic="react hooks", max_subs=5)
        self.assertEqual(subs[0], "reactjs")

    def test_engagement_bonus(self):
        results = [
            {"subreddit": "AIsub", "ups": 500},
            {"subreddit": "OtherSub", "ups": 5},
        ]
        subs = reddit.discover_subreddits(results, max_subs=5)
        self.assertEqual(subs[0], "AIsub")

    def test_max_subs_limit(self):
        results = [{"subreddit": f"sub{i}"} for i in range(20)]
        subs = reddit.discover_subreddits(results, max_subs=3)
        self.assertLessEqual(len(subs), 3)

    def test_empty_results(self):
        self.assertEqual(reddit.discover_subreddits([]), [])

    def test_missing_subreddit_field(self):
        results = [{"title": "no sub field"}]
        self.assertEqual(reddit.discover_subreddits(results), [])


class TestParseDate(unittest.TestCase):
    """Tests for _parse_date()."""

    def test_valid_timestamp(self):
        self.assertEqual(reddit._parse_date(1705363200), "2024-01-16")

    def test_string_timestamp(self):
        self.assertEqual(reddit._parse_date("1705363200"), "2024-01-16")

    def test_none_returns_none(self):
        self.assertIsNone(reddit._parse_date(None))

    def test_zero_returns_none(self):
        self.assertIsNone(reddit._parse_date(0))


class TestDepthConfig(unittest.TestCase):
    """Tests for DEPTH_CONFIG structure."""

    def test_all_depths_exist(self):
        for depth in ("quick", "default", "deep"):
            self.assertIn(depth, reddit.DEPTH_CONFIG)

    def test_required_keys(self):
        required = {"global_searches", "subreddit_searches", "comment_enrichments", "timeframe"}
        for depth, config in reddit.DEPTH_CONFIG.items():
            self.assertTrue(required.issubset(config.keys()),
                            f"Missing keys in {depth}: {required - config.keys()}")

    def test_deep_has_more_searches(self):
        self.assertGreater(
            reddit.DEPTH_CONFIG["deep"]["global_searches"],
            reddit.DEPTH_CONFIG["quick"]["global_searches"],
        )


if __name__ == "__main__":
    unittest.main()
