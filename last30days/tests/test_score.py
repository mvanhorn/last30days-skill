"""Tests for score module."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import schema, score


class TestLog1pSafe(unittest.TestCase):
    def test_positive_value(self):
        result = score.log1p_safe(100)
        self.assertGreater(result, 0)

    def test_zero(self):
        result = score.log1p_safe(0)
        self.assertEqual(result, 0)

    def test_none(self):
        result = score.log1p_safe(None)
        self.assertEqual(result, 0)

    def test_negative(self):
        result = score.log1p_safe(-5)
        self.assertEqual(result, 0)


class TestComputeRedditEngagementRaw(unittest.TestCase):
    def test_with_engagement(self):
        eng = schema.Engagement(score=100, num_comments=50, upvote_ratio=0.9)
        result = score.compute_reddit_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_without_engagement(self):
        result = score.compute_reddit_engagement_raw(None)
        self.assertIsNone(result)

    def test_empty_engagement(self):
        eng = schema.Engagement()
        result = score.compute_reddit_engagement_raw(eng)
        self.assertIsNone(result)


class TestComputeXEngagementRaw(unittest.TestCase):
    def test_with_engagement(self):
        eng = schema.Engagement(likes=100, reposts=25, replies=15, quotes=5)
        result = score.compute_x_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_without_engagement(self):
        result = score.compute_x_engagement_raw(None)
        self.assertIsNone(result)


class TestNormalizeTo100(unittest.TestCase):
    def test_normalizes_values(self):
        values = [0, 50, 100]
        result = score.normalize_to_100(values)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 50)
        self.assertEqual(result[2], 100)

    def test_handles_none(self):
        values = [0, None, 100]
        result = score.normalize_to_100(values)
        self.assertIsNone(result[1])

    def test_single_value(self):
        values = [50]
        result = score.normalize_to_100(values)
        self.assertEqual(result[0], 50)


class TestScoreRedditItems(unittest.TestCase):
    def test_scores_items(self):
        today = datetime.now(timezone.utc).date().isoformat()
        items = [
            schema.RedditItem(
                id="R1",
                title="Test",
                url="https://reddit.com/r/test/1",
                subreddit="test",
                date=today,
                date_confidence="high",
                engagement=schema.Engagement(score=100, num_comments=50, upvote_ratio=0.9),
                relevance=0.9,
            ),
            schema.RedditItem(
                id="R2",
                title="Test 2",
                url="https://reddit.com/r/test/2",
                subreddit="test",
                date=today,
                date_confidence="high",
                engagement=schema.Engagement(score=10, num_comments=5, upvote_ratio=0.8),
                relevance=0.5,
            ),
        ]

        result = score.score_reddit_items(items)

        self.assertEqual(len(result), 2)
        self.assertGreater(result[0].score, 0)
        self.assertGreater(result[1].score, 0)
        # Higher relevance and engagement should score higher
        self.assertGreater(result[0].score, result[1].score)

    def test_empty_list(self):
        result = score.score_reddit_items([])
        self.assertEqual(result, [])


class TestScoreXItems(unittest.TestCase):
    def test_scores_items(self):
        today = datetime.now(timezone.utc).date().isoformat()
        items = [
            schema.XItem(
                id="X1",
                text="Test post",
                url="https://x.com/user/1",
                author_handle="user1",
                date=today,
                date_confidence="high",
                engagement=schema.Engagement(likes=100, reposts=25, replies=15, quotes=5),
                relevance=0.9,
            ),
        ]

        result = score.score_x_items(items)

        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)


class TestSortItems(unittest.TestCase):
    def test_sorts_by_score_descending(self):
        items = [
            schema.RedditItem(id="R1", title="Low", url="", subreddit="", score=30),
            schema.RedditItem(id="R2", title="High", url="", subreddit="", score=90),
            schema.RedditItem(id="R3", title="Mid", url="", subreddit="", score=60),
        ]

        result = score.sort_items(items)

        self.assertEqual(result[0].id, "R2")
        self.assertEqual(result[1].id, "R3")
        self.assertEqual(result[2].id, "R1")

    def test_stable_sort(self):
        items = [
            schema.RedditItem(id="R1", title="A", url="", subreddit="", score=50),
            schema.RedditItem(id="R2", title="B", url="", subreddit="", score=50),
        ]

        result = score.sort_items(items)

        # Both have same score, should maintain order by title
        self.assertEqual(len(result), 2)


if __name__ == "__main__":
    unittest.main()
