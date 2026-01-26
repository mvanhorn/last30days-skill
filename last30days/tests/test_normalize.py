"""Tests for normalize module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import normalize, schema


class TestNormalizeRedditItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "id": "R1",
                "title": "Test Thread",
                "url": "https://reddit.com/r/test/1",
                "subreddit": "test",
                "date": "2026-01-15",
                "why_relevant": "Relevant because...",
                "relevance": 0.85,
            }
        ]

        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.RedditItem)
        self.assertEqual(result[0].id, "R1")
        self.assertEqual(result[0].title, "Test Thread")
        self.assertEqual(result[0].date_confidence, "high")

    def test_sets_low_confidence_for_old_date(self):
        items = [
            {
                "id": "R1",
                "title": "Old Thread",
                "url": "https://reddit.com/r/test/1",
                "subreddit": "test",
                "date": "2025-12-01",  # Before range
                "relevance": 0.5,
            }
        ]

        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")

        self.assertEqual(result[0].date_confidence, "low")

    def test_handles_engagement(self):
        items = [
            {
                "id": "R1",
                "title": "Thread with engagement",
                "url": "https://reddit.com/r/test/1",
                "subreddit": "test",
                "engagement": {
                    "score": 100,
                    "num_comments": 50,
                    "upvote_ratio": 0.9,
                },
                "relevance": 0.5,
            }
        ]

        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")

        self.assertIsNotNone(result[0].engagement)
        self.assertEqual(result[0].engagement.score, 100)
        self.assertEqual(result[0].engagement.num_comments, 50)


class TestNormalizeXItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "id": "X1",
                "text": "Test post content",
                "url": "https://x.com/user/status/123",
                "author_handle": "testuser",
                "date": "2026-01-15",
                "why_relevant": "Relevant because...",
                "relevance": 0.9,
            }
        ]

        result = normalize.normalize_x_items(items, "2026-01-01", "2026-01-31")

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.XItem)
        self.assertEqual(result[0].id, "X1")
        self.assertEqual(result[0].author_handle, "testuser")

    def test_handles_x_engagement(self):
        items = [
            {
                "id": "X1",
                "text": "Post with engagement",
                "url": "https://x.com/user/status/123",
                "author_handle": "user",
                "engagement": {
                    "likes": 100,
                    "reposts": 25,
                    "replies": 15,
                    "quotes": 5,
                },
                "relevance": 0.5,
            }
        ]

        result = normalize.normalize_x_items(items, "2026-01-01", "2026-01-31")

        self.assertIsNotNone(result[0].engagement)
        self.assertEqual(result[0].engagement.likes, 100)
        self.assertEqual(result[0].engagement.reposts, 25)


class TestItemsToDicts(unittest.TestCase):
    def test_converts_items(self):
        items = [
            schema.RedditItem(
                id="R1",
                title="Test",
                url="https://reddit.com/r/test/1",
                subreddit="test",
            )
        ]

        result = normalize.items_to_dicts(items)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], "R1")


if __name__ == "__main__":
    unittest.main()
