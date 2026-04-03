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


class TestFilterByDateRange(unittest.TestCase):
    def test_keeps_items_in_range(self):
        items = [
            schema.RedditItem(id="R1", title="In range", url="", subreddit="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)

    def test_drops_items_before_range(self):
        items = [
            schema.RedditItem(id="R1", title="Too old", url="", subreddit="", date="2025-12-31"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 0)

    def test_drops_items_after_range(self):
        items = [
            schema.RedditItem(id="R1", title="Future", url="", subreddit="", date="2026-02-01"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 0)

    def test_keeps_unknown_date_by_default(self):
        items = [
            schema.RedditItem(id="R1", title="No date", url="", subreddit="", date=None),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)

    def test_drops_unknown_date_when_required(self):
        items = [
            schema.RedditItem(id="R1", title="No date", url="", subreddit="", date=None),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31", require_date=True)
        self.assertEqual(len(result), 0)

    def test_boundary_dates_are_included(self):
        items = [
            schema.RedditItem(id="R1", title="Start", url="", subreddit="", date="2026-01-01"),
            schema.RedditItem(id="R2", title="End", url="", subreddit="", date="2026-01-31"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        result = normalize.filter_by_date_range([], "2026-01-01", "2026-01-31")
        self.assertEqual(result, [])


class TestNormalizeYouTubeItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "video_id": "abc123",
                "title": "How to use Claude Code",
                "url": "https://youtube.com/watch?v=abc123",
                "channel_name": "TechChannel",
                "date": "2026-01-15",
                "relevance": 0.8,
            }
        ]
        result = normalize.normalize_youtube_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.YouTubeItem)
        self.assertEqual(result[0].id, "abc123")
        self.assertEqual(result[0].channel_name, "TechChannel")
        self.assertEqual(result[0].date_confidence, "high")

    def test_handles_engagement(self):
        items = [
            {
                "video_id": "v1",
                "title": "Tutorial",
                "url": "https://youtube.com/watch?v=v1",
                "channel_name": "Chan",
                "engagement": {"views": 10000, "likes": 500, "comments": 50},
                "relevance": 0.7,
            }
        ]
        result = normalize.normalize_youtube_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.views, 10000)
        self.assertEqual(result[0].engagement.likes, 500)
        self.assertEqual(result[0].engagement.num_comments, 50)

    def test_missing_engagement_defaults_to_empty(self):
        items = [
            {
                "video_id": "v2",
                "title": "No engagement",
                "url": "https://youtube.com/watch?v=v2",
                "channel_name": "Chan",
                "relevance": 0.6,
            }
        ]
        result = normalize.normalize_youtube_items(items, "2026-01-01", "2026-01-31")
        self.assertIsNotNone(result[0].engagement)


class TestNormalizeTikTokItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "text": "Check out this AI tool #claudecode",
                "url": "https://tiktok.com/@user/video/1",
                "author_name": "techuser",
                "date": "2026-01-20",
                "relevance": 0.75,
            }
        ]
        result = normalize.normalize_tiktok_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.TikTokItem)
        self.assertEqual(result[0].id, "TK1")
        self.assertEqual(result[0].author_name, "techuser")
        self.assertEqual(result[0].date_confidence, "high")

    def test_ids_are_sequential(self):
        items = [
            {"text": "Post 1", "url": "https://tiktok.com/1", "author_name": "u1", "relevance": 0.5},
            {"text": "Post 2", "url": "https://tiktok.com/2", "author_name": "u2", "relevance": 0.5},
        ]
        result = normalize.normalize_tiktok_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].id, "TK1")
        self.assertEqual(result[1].id, "TK2")

    def test_handles_engagement(self):
        items = [
            {
                "text": "Viral post",
                "url": "https://tiktok.com/v",
                "author_name": "creator",
                "engagement": {"views": 500000, "likes": 20000, "comments": 1000, "shares": 5000},
                "relevance": 0.9,
            }
        ]
        result = normalize.normalize_tiktok_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.views, 500000)
        self.assertEqual(result[0].engagement.shares, 5000)


class TestNormalizeInstagramItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "text": "Latest AI tools reel",
                "url": "https://instagram.com/p/abc",
                "author_name": "techinfluencer",
                "date": "2026-01-18",
                "relevance": 0.7,
            }
        ]
        result = normalize.normalize_instagram_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.InstagramItem)
        self.assertEqual(result[0].id, "IG1")
        self.assertEqual(result[0].author_name, "techinfluencer")
        self.assertEqual(result[0].date_confidence, "high")

    def test_handles_engagement(self):
        items = [
            {
                "text": "Reel with engagement",
                "url": "https://instagram.com/p/xyz",
                "author_name": "user",
                "engagement": {"views": 80000, "likes": 3000, "comments": 200},
                "relevance": 0.8,
            }
        ]
        result = normalize.normalize_instagram_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.views, 80000)
        self.assertEqual(result[0].engagement.likes, 3000)


class TestNormalizeHackerNewsItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "title": "Show HN: New open source tool",
                "url": "https://github.com/user/tool",
                "hn_url": "https://news.ycombinator.com/item?id=12345",
                "author": "hnuser",
                "date": "2026-01-10",
                "relevance": 0.85,
            }
        ]
        result = normalize.normalize_hackernews_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.HackerNewsItem)
        self.assertEqual(result[0].id, "HN1")
        self.assertEqual(result[0].title, "Show HN: New open source tool")
        self.assertEqual(result[0].date_confidence, "high")

    def test_handles_engagement(self):
        items = [
            {
                "title": "Hot HN post",
                "url": "https://example.com",
                "hn_url": "https://news.ycombinator.com/item?id=99",
                "author": "author",
                "engagement": {"points": 350, "num_comments": 120},
                "relevance": 0.9,
            }
        ]
        result = normalize.normalize_hackernews_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.score, 350)
        self.assertEqual(result[0].engagement.num_comments, 120)

    def test_handles_top_comments(self):
        items = [
            {
                "title": "Post with comments",
                "url": "https://example.com",
                "hn_url": "https://news.ycombinator.com/item?id=55",
                "author": "author",
                "top_comments": [
                    {"author": "commenter1", "text": "Great post!", "points": 42},
                ],
                "relevance": 0.7,
            }
        ]
        result = normalize.normalize_hackernews_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result[0].top_comments), 1)
        self.assertEqual(result[0].top_comments[0].score, 42)
        self.assertEqual(result[0].top_comments[0].author, "commenter1")


class TestNormalizeBlueskyItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "text": "Interesting post about AI agents",
                "url": "https://bsky.app/profile/user.bsky.social/post/abc",
                "handle": "user.bsky.social",
                "display_name": "User Name",
                "date": "2026-01-22",
                "relevance": 0.65,
            }
        ]
        result = normalize.normalize_bluesky_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.BlueskyItem)
        self.assertEqual(result[0].id, "BS1")
        self.assertEqual(result[0].author_handle, "user.bsky.social")
        self.assertEqual(result[0].display_name, "User Name")
        self.assertEqual(result[0].date_confidence, "high")

    def test_handles_engagement(self):
        items = [
            {
                "text": "Viral bluesky post",
                "url": "https://bsky.app/profile/u/post/1",
                "handle": "u.bsky.social",
                "display_name": "U",
                "engagement": {"likes": 200, "reposts": 50, "replies": 30, "quotes": 10},
                "relevance": 0.8,
            }
        ]
        result = normalize.normalize_bluesky_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.likes, 200)
        self.assertEqual(result[0].engagement.reposts, 50)
        self.assertEqual(result[0].engagement.quotes, 10)


class TestNormalizeTruthSocialItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "text": "Truth Social post content",
                "url": "https://truthsocial.com/@user/posts/1",
                "handle": "@user",
                "display_name": "User",
                "date": "2026-01-25",
                "relevance": 0.6,
            }
        ]
        result = normalize.normalize_truthsocial_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.TruthSocialItem)
        self.assertEqual(result[0].id, "TS1")
        self.assertEqual(result[0].author_handle, "@user")
        self.assertEqual(result[0].date_confidence, "high")

    def test_handles_engagement(self):
        items = [
            {
                "text": "Popular post",
                "url": "https://truthsocial.com/@u/posts/2",
                "handle": "@u",
                "display_name": "U",
                "engagement": {"likes": 150, "reposts": 40, "replies": 20},
                "relevance": 0.7,
            }
        ]
        result = normalize.normalize_truthsocial_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.likes, 150)
        self.assertEqual(result[0].engagement.reposts, 40)
        self.assertEqual(result[0].engagement.replies, 20)


class TestNormalizePolymarketItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "title": "Will Fed cut rates in Q1 2026?",
                "question": "Will the Federal Reserve cut interest rates in Q1 2026?",
                "url": "https://polymarket.com/event/fed-rate-cut-q1-2026",
                "outcome_prices": [0.35, 0.65],
                "outcomes_remaining": 2,
                "date": "2026-01-01",
                "end_date": "2026-03-31",
                "volume1mo": 500000.0,
                "liquidity": 50000.0,
                "relevance": 0.9,
            }
        ]
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.PolymarketItem)
        self.assertEqual(result[0].id, "PM1")
        self.assertEqual(result[0].title, "Will Fed cut rates in Q1 2026?")
        self.assertEqual(result[0].end_date, "2026-03-31")
        self.assertEqual(result[0].date_confidence, "high")

    def test_prefers_volume1mo_over_volume24hr(self):
        items = [
            {
                "title": "Market",
                "question": "Will X happen?",
                "url": "https://polymarket.com/event/x",
                "volume1mo": 200000.0,
                "volume24hr": 5000.0,
                "liquidity": 10000.0,
                "relevance": 0.7,
            }
        ]
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.volume, 200000.0)

    def test_falls_back_to_volume24hr_when_no_volume1mo(self):
        items = [
            {
                "title": "Market",
                "question": "Will Y happen?",
                "url": "https://polymarket.com/event/y",
                "volume24hr": 3000.0,
                "liquidity": 8000.0,
                "relevance": 0.6,
            }
        ]
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        self.assertEqual(result[0].engagement.volume, 3000.0)


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
