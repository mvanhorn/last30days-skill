import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.reddit import (
    _extract_date,
    _extract_score,
    _extract_subreddit_name,
    _normalize_reddit_id,
    _total_engagement,
    enrich_with_comments,
)


class TestExtractSubredditName(unittest.TestCase):
    def test_from_string(self):
        self.assertEqual("openclaw", _extract_subreddit_name("openclaw"))

    def test_from_dict_with_name(self):
        self.assertEqual(
            "openclaw",
            _extract_subreddit_name({"id": "t5_ghydwa", "name": "openclaw"}),
        )

    def test_from_dict_with_display_name(self):
        self.assertEqual(
            "LocalLLM",
            _extract_subreddit_name({"display_name": "LocalLLM"}),
        )

    def test_from_dict_name_preferred_over_display_name(self):
        self.assertEqual(
            "name_wins",
            _extract_subreddit_name({"name": "name_wins", "display_name": "display"}),
        )

    def test_empty_string(self):
        self.assertEqual("", _extract_subreddit_name(""))

    def test_empty_dict(self):
        self.assertEqual("", _extract_subreddit_name({}))

    def test_strips_whitespace(self):
        self.assertEqual("test", _extract_subreddit_name("  test  "))


class TestExtractScore(unittest.TestCase):
    def test_ups(self):
        self.assertEqual(42, _extract_score({"ups": 42}))

    def test_score_field(self):
        self.assertEqual(77, _extract_score({"score": 77}))

    def test_votes(self):
        self.assertEqual(99, _extract_score({"votes": 99}))

    def test_ups_preferred_over_votes(self):
        self.assertEqual(10, _extract_score({"ups": 10, "votes": 99}))

    def test_missing(self):
        self.assertEqual(0, _extract_score({}))

    def test_zero_preserved(self):
        self.assertEqual(0, _extract_score({"ups": 0}))

    def test_zero_ups_does_not_fall_through(self):
        # ups=0 should be returned, not fall through to score
        self.assertEqual(0, _extract_score({"ups": 0, "score": 5}))


class TestExtractDate(unittest.TestCase):
    def test_unix_timestamp(self):
        self.assertEqual("2024-05-03", _extract_date({"created_utc": 1714694957}))

    def test_iso_string(self):
        result = _extract_date({"created_at": "2024-05-03T01:09:17.620000+0000"})
        self.assertEqual("2024-05-03", result)

    def test_iso_with_z_suffix(self):
        result = _extract_date({"created_at": "2024-05-03T01:09:17Z"})
        self.assertEqual("2024-05-03", result)

    def test_created_utc_preferred(self):
        result = _extract_date({"created_utc": 1714694957, "created_at": "2025-01-01T00:00:00Z"})
        self.assertEqual("2024-05-03", result)

    def test_missing(self):
        self.assertIsNone(_extract_date({}))


class TestNormalizeRedditId(unittest.TestCase):
    def test_strips_t3_prefix(self):
        self.assertEqual("abc123", _normalize_reddit_id("t3_abc123"))

    def test_no_prefix(self):
        self.assertEqual("abc123", _normalize_reddit_id("abc123"))

    def test_empty(self):
        self.assertEqual("", _normalize_reddit_id(""))

    def test_none(self):
        self.assertEqual("", _normalize_reddit_id(None))


class TestTotalEngagement(unittest.TestCase):
    def test_score_plus_comments(self):
        item = {"engagement": {"score": 100, "num_comments": 50}}
        self.assertEqual(150, _total_engagement(item))

    def test_high_comments_low_score(self):
        item = {"engagement": {"score": 1, "num_comments": 1387}}
        self.assertEqual(1388, _total_engagement(item))

    def test_missing_engagement(self):
        self.assertEqual(0, _total_engagement({}))

    def test_none_values(self):
        item = {"engagement": {"score": None, "num_comments": None}}
        self.assertEqual(0, _total_engagement(item))

    def test_score_only(self):
        item = {"engagement": {"score": 42}}
        self.assertEqual(42, _total_engagement(item))


class TestEnrichSelectsTopEngagement(unittest.TestCase):
    """Verify enrich_with_comments picks threads by total engagement, not list order."""

    def test_high_comment_thread_enriched_over_low_engagement(self):
        """A thread with 1387 comments but score:1 should be enriched before
        a thread with score:5 and 0 comments."""
        from unittest.mock import patch

        items = [
            # Low engagement thread (first in list)
            {
                "id": "R1",
                "url": "https://www.reddit.com/r/test/comments/low",
                "engagement": {"score": 5, "num_comments": 0},
            },
            # High engagement thread (second in list)
            {
                "id": "R2",
                "url": "https://www.reddit.com/r/test/comments/high",
                "engagement": {"score": 1, "num_comments": 1387},
            },
            # Medium engagement
            {
                "id": "R3",
                "url": "https://www.reddit.com/r/test/comments/med",
                "engagement": {"score": 50, "num_comments": 10},
            },
        ]

        enriched_urls = []

        def mock_fetch_comments(url, token):
            enriched_urls.append(url)
            return [{"body": "Great thread!", "ups": 10, "author": "testuser"}]

        # Only allow 1 enrichment to prove selection order matters
        with patch("lib.reddit.fetch_post_comments", side_effect=mock_fetch_comments):
            result = enrich_with_comments(items, token="fake", depth="quick")

        # With quick depth (3 enrichments), all 3 should be enriched.
        # But the key assertion: the high-comment thread (R2) must be included.
        self.assertIn(
            "https://www.reddit.com/r/test/comments/high",
            enriched_urls,
            "High-comment thread should always be selected for enrichment",
        )

    def test_enrichment_order_by_engagement(self):
        """With a budget of 1, only the highest-engagement thread gets enriched."""
        from unittest.mock import patch

        items = [
            {
                "id": "R1",
                "url": "https://www.reddit.com/r/test/comments/a",
                "engagement": {"score": 200, "num_comments": 5},
            },
            {
                "id": "R2",
                "url": "https://www.reddit.com/r/test/comments/b",
                "engagement": {"score": 1, "num_comments": 1500},
            },
        ]

        enriched_urls = []

        def mock_fetch_comments(url, token):
            enriched_urls.append(url)
            return [{"body": "Comment", "ups": 5, "author": "user"}]

        # Override DEPTH_CONFIG to allow only 1 enrichment
        custom_config = {"comment_enrichments": 1}
        with patch("lib.reddit.DEPTH_CONFIG", {"test": custom_config, "default": custom_config}), \
             patch("lib.reddit.fetch_post_comments", side_effect=mock_fetch_comments):
            enrich_with_comments(items, token="fake", depth="test")

        # R2 has 1501 total engagement vs R1's 205 -- R2 should be picked
        self.assertEqual(len(enriched_urls), 1)
        self.assertEqual(
            enriched_urls[0],
            "https://www.reddit.com/r/test/comments/b",
        )


class TestEnrichmentBudget(unittest.TestCase):
    """Tests for the enrichment time budget in enrich_with_comments()."""

    def _make_items(self, n):
        return [
            {"url": f"https://reddit.com/r/test/comments/{i}/post", "score": 100 - i, "num_comments": 50,
             "engagement": {"score": 100 - i, "num_comments": 50}}
            for i in range(n)
        ]

    def test_all_complete_within_budget(self):
        """When enrichment is fast, all items get comments."""
        from unittest.mock import patch
        items = self._make_items(3)
        fast_comments = [{"body": "Great post!", "score": 42, "author": "user1"}]

        with patch("lib.reddit.fetch_post_comments", return_value=fast_comments):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=60)

        enriched = [i for i in result if i.get("top_comments")]
        self.assertEqual(len(enriched), 3)

    def test_budget_zero_returns_items_unenriched(self):
        """With budget=0, items are returned without enrichment (not discarded)."""
        import time as _time
        from unittest.mock import patch

        items = self._make_items(3)

        def slow_fetch(url, token):
            _time.sleep(2)
            return [{"body": "comment", "score": 10, "author": "u"}]

        with patch("lib.reddit.fetch_post_comments", side_effect=slow_fetch):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=0)

        # All 3 items returned (not discarded)
        self.assertEqual(len(result), 3)

    def test_empty_items_returns_immediately(self):
        result = enrich_with_comments([], "fake-token", depth="default", budget_seconds=60)
        self.assertEqual(result, [])

    def test_exceptions_dont_crash(self):
        """If enrichment raises, items are returned without comments."""
        from unittest.mock import patch
        items = self._make_items(3)

        with patch("lib.reddit.fetch_post_comments", side_effect=ConnectionError("boom")):
            result = enrich_with_comments(items, "fake-token", depth="quick", budget_seconds=60)

        self.assertEqual(len(result), 3)
        enriched = [i for i in result if i.get("top_comments")]
        self.assertEqual(len(enriched), 0)


if __name__ == "__main__":
    unittest.main()
