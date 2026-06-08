"""Tests for reddit.py — ScrapeCreators Reddit search module."""

import unittest

# Add lib to path

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


class TestExpandRedditQueries(unittest.TestCase):
    """Tests for expand_reddit_queries()."""

    def test_quick_returns_one_query(self):
        queries = reddit.expand_reddit_queries("cursor IDE", "quick")
        self.assertGreaterEqual(len(queries), 1)

    def test_default_includes_review_variant(self):
        queries = reddit.expand_reddit_queries("cursor IDE pricing", "default")
        self.assertTrue(any("worth it" in q or "review" in q for q in queries))

    def test_default_skips_review_variant_for_prediction(self):
        queries = reddit.expand_reddit_queries("anthropic odds", "default")
        self.assertFalse(any("worth it" in q or "review" in q for q in queries))

    def test_default_skips_review_variant_for_breaking_news(self):
        queries = reddit.expand_reddit_queries("kanye west", "default")
        self.assertFalse(any("worth it" in q or "review" in q for q in queries))

    def test_deep_includes_issues_variant(self):
        queries = reddit.expand_reddit_queries("cursor IDE pricing", "deep")
        self.assertTrue(any("issues" in q or "problems" in q for q in queries))

    def test_deep_skips_issues_variant_for_prediction(self):
        queries = reddit.expand_reddit_queries("anthropic odds", "deep")
        self.assertFalse(any("issues" in q or "problems" in q for q in queries))

    def test_deep_has_more_queries_than_quick(self):
        quick = reddit.expand_reddit_queries("cursor IDE pricing", "quick")
        deep = reddit.expand_reddit_queries("cursor IDE pricing", "deep")
        self.assertGreater(len(deep), len(quick))


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


class TestPostRelevance(unittest.TestCase):
    def test_body_cannot_rescue_weak_title_too_far(self):
        score = reddit._compute_post_relevance(
            "anthropic odds",
            "President Trump orders agencies to stop using Anthropic technology",
            "Long body text eventually mentions odds and other tangential details.",
        )
        self.assertLess(score, 0.7)
        self.assertGreaterEqual(score, 0.5)

    def test_exact_title_match_stays_high(self):
        score = reddit._compute_post_relevance(
            "claude code tips",
            "Claude Code tips for faster workflows",
            "",
        )
        self.assertGreater(score, 0.7)

if __name__ == "__main__":
    unittest.main()
