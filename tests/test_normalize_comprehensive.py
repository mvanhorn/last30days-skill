"""
Comprehensive tests for normalize module.

This test file adds extensive coverage for:
- Normalization of all item types (Reddit, X, YouTube, TikTok, Instagram, HN, Bluesky, TruthSocial, Polymarket, WebSearch)
- Date filtering edge cases
- Items_to_dicts conversion for all item types
- Engagement parsing for different platforms
"""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import normalize, schema


class TestFilterByDateRange(unittest.TestCase):
    """Tests for filter_by_date_range function."""

    def test_keeps_items_in_range(self):
        """Items within date range should be kept."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date="2026-01-15"),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-20"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 2)

    def test_removes_items_before_range(self):
        """Items before date range should be removed."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date="2025-12-15"),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "R2")

    def test_removes_items_after_range(self):
        """Items after date range should be removed."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date="2026-02-15"),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "R2")

    def test_keeps_items_without_date_by_default(self):
        """Items without date should be kept by default."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date=None),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 2)

    def test_removes_items_without_date_when_required(self):
        """Items without date should be removed when require_date=True."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date=None),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31", require_date=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "R2")

    def test_boundary_dates(self):
        """Exact boundary dates should be included."""
        items = [
            schema.RedditItem(id="R1", title="Test", url="", subreddit="", date="2026-01-01"),
            schema.RedditItem(id="R2", title="Test", url="", subreddit="", date="2026-01-31"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 2)

    def test_empty_list(self):
        """Empty list should return empty."""
        result = normalize.filter_by_date_range([], "2026-01-01", "2026-01-31")
        self.assertEqual(result, [])

    def test_works_with_all_item_types(self):
        """Should work with all item types that have a date field."""
        items = [
            schema.XItem(id="X1", text="Test", url="", author_handle="user", date="2026-01-15"),
            schema.YouTubeItem(id="YT1", title="Test", url="", channel_name="ch", date="2026-01-15"),
            schema.TikTokItem(id="TK1", text="Test", url="", author_name="user", date="2026-01-15"),
            schema.InstagramItem(id="IG1", text="Test", url="", author_name="user", date="2026-01-15"),
            schema.HackerNewsItem(id="HN1", title="Test", url="", hn_url="", author="user", date="2026-01-15"),
            schema.BlueskyItem(id="BS1", text="Test", url="", author_handle="user.bsky.social", display_name="User", date="2026-01-15"),
            schema.TruthSocialItem(id="TS1", text="Test", url="", author_handle="@user", display_name="User", date="2026-01-15"),
            schema.PolymarketItem(id="PM1", title="Test", question="?", url="", date="2026-01-15"),
            schema.WebSearchItem(id="WEB1", title="Test", url="", source_domain="example.com", snippet="", date="2026-01-15"),
        ]
        result = normalize.filter_by_date_range(items, "2026-01-01", "2026-01-31")
        self.assertEqual(len(result), 9)


class TestNormalizeRedditItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_reddit_items."""

    def test_normalizes_complete_item(self):
        """Test with all fields populated."""
        items = [{
            "id": "R1",
            "title": "Complete Reddit Post",
            "url": "https://reddit.com/r/test/comments/abc",
            "subreddit": "test",
            "date": "2026-01-15",
            "engagement": {
                "score": 1000,
                "num_comments": 500,
                "upvote_ratio": 0.95,
            },
            "top_comments": [
                {"score": 500, "date": "2026-01-15", "author": "commenter1", "excerpt": "Great post!", "url": "https://reddit.com/r/test/comments/abc/def"}
            ],
            "comment_insights": ["This is a key insight"],
            "relevance": 0.9,
            "why_relevant": "Highly relevant to topic",
        }]
        
        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.RedditItem)
        self.assertEqual(item.id, "R1")
        self.assertEqual(item.title, "Complete Reddit Post")
        self.assertEqual(item.subreddit, "test")
        self.assertEqual(item.date, "2026-01-15")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.score, 1000)
        self.assertEqual(item.engagement.num_comments, 500)
        self.assertEqual(item.engagement.upvote_ratio, 0.95)
        self.assertEqual(len(item.top_comments), 1)
        self.assertEqual(item.top_comments[0].score, 500)
        self.assertEqual(len(item.comment_insights), 1)
        self.assertEqual(item.relevance, 0.9)

    def test_normalizes_minimal_item(self):
        """Test with only required fields."""
        items = [{
            "id": "R1",
            "title": "Minimal Post",
            "url": "https://reddit.com/r/test/1",
            "subreddit": "test",
        }]
        
        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertEqual(item.id, "R1")
        self.assertIsNone(item.date)
        self.assertEqual(item.date_confidence, "low")
        self.assertIsNone(item.engagement)
        self.assertEqual(item.relevance, 0.5)  # Default

    def test_handles_missing_engagement_fields(self):
        """Test with partial engagement."""
        items = [{
            "id": "R1",
            "title": "Test",
            "url": "https://reddit.com/r/test/1",
            "subreddit": "test",
            "engagement": {"score": 100},  # Missing num_comments and upvote_ratio
        }]
        
        result = normalize.normalize_reddit_items(items, "2026-01-01", "2026-01-31")
        
        self.assertIsNotNone(result[0].engagement)
        self.assertEqual(result[0].engagement.score, 100)
        self.assertIsNone(result[0].engagement.num_comments)
        self.assertIsNone(result[0].engagement.upvote_ratio)

    def test_empty_list(self):
        """Empty input should return empty output."""
        result = normalize.normalize_reddit_items([], "2026-01-01", "2026-01-31")
        self.assertEqual(result, [])


class TestNormalizeXItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_x_items."""

    def test_normalizes_complete_x_item(self):
        """Test with all X fields populated."""
        items = [{
            "id": "X1",
            "text": "This is a tweet about AI tools",
            "url": "https://x.com/user/status/123456",
            "author_handle": "testuser",
            "date": "2026-01-15",
            "engagement": {
                "likes": 5000,
                "reposts": 1000,
                "replies": 500,
                "quotes": 100,
            },
            "relevance": 0.85,
            "why_relevant": "Discusses AI tools",
        }]
        
        result = normalize.normalize_x_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.XItem)
        self.assertEqual(item.id, "X1")
        self.assertEqual(item.text, "This is a tweet about AI tools")
        self.assertEqual(item.author_handle, "testuser")
        self.assertEqual(item.date, "2026-01-15")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.likes, 5000)
        self.assertEqual(item.engagement.reposts, 1000)
        self.assertEqual(item.engagement.replies, 500)
        self.assertEqual(item.engagement.quotes, 100)

    def test_normalizes_minimal_x_item(self):
        """Test with minimal X fields."""
        items = [{
            "id": "X1",
            "text": "Simple tweet",
            "url": "https://x.com/user/status/123",
            "author_handle": "user",
        }]
        
        result = normalize.normalize_x_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].date)
        self.assertEqual(result[0].date_confidence, "low")
        self.assertIsNone(result[0].engagement)


class TestNormalizeYouTubeItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_youtube_items."""

    def test_normalizes_complete_youtube_item(self):
        """Test with all YouTube fields populated."""
        items = [{
            "video_id": "abc123",
            "title": "AI Tools Tutorial",
            "url": "https://youtube.com/watch?v=abc123",
            "channel_name": "TechChannel",
            "date": "2026-01-15",
            "engagement": {
                "views": 50000,
                "likes": 2000,
                "comments": 500,
            },
            "transcript_snippet": "In this video we discuss...",
            "transcript_highlights": ["Key point 1", "Key point 2"],
            "relevance": 0.9,
        }]
        
        result = normalize.normalize_youtube_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.YouTubeItem)
        self.assertEqual(item.id, "abc123")
        self.assertEqual(item.title, "AI Tools Tutorial")
        self.assertEqual(item.channel_name, "TechChannel")
        self.assertEqual(item.date, "2026-01-15")
        self.assertEqual(item.date_confidence, "high")  # YouTube dates are always high
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.views, 50000)
        self.assertEqual(item.engagement.likes, 2000)
        self.assertEqual(item.transcript_snippet, "In this video we discuss...")
        self.assertEqual(len(item.transcript_highlights), 2)

    def test_youtube_date_always_high_confidence(self):
        """YouTube dates should always be high confidence."""
        items = [{
            "video_id": "abc123",
            "title": "Test",
            "url": "https://youtube.com/watch?v=abc123",
            "channel_name": "Channel",
            "date": "2026-01-15",
        }]
        
        result = normalize.normalize_youtube_items(items, "2025-01-01", "2025-12-31")
        
        # Even if date is outside range, YouTube dates are still "high" confidence
        # (the date is reliable, even if not in range)
        self.assertEqual(result[0].date_confidence, "high")


class TestNormalizeTikTokItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_tiktok_items."""

    def test_normalizes_complete_tiktok_item(self):
        """Test with all TikTok fields populated."""
        items = [{
            "text": "Check out this AI tool! #ai #tech",
            "url": "https://tiktok.com/@user/video/123456",
            "author_name": "techcreator",
            "date": "2026-01-15",
            "engagement": {
                "views": 100000,
                "likes": 10000,
                "comments": 500,
                "shares": 1000,
            },
            "caption_snippet": "In this video I show...",
            "hashtags": ["ai", "tech", "tools"],
            "relevance": 0.85,
        }]
        
        result = normalize.normalize_tiktok_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.TikTokItem)
        self.assertEqual(item.id, "TK1")
        self.assertEqual(item.text, "Check out this AI tool! #ai #tech")
        self.assertEqual(item.author_name, "techcreator")
        self.assertEqual(item.date, "2026-01-15")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.views, 100000)
        self.assertEqual(item.engagement.likes, 10000)
        self.assertEqual(item.engagement.shares, 1000)
        self.assertEqual(len(item.hashtags), 3)


class TestNormalizeInstagramItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_instagram_items."""

    def test_normalizes_complete_instagram_item(self):
        """Test with all Instagram fields populated."""
        items = [{
            "text": "New product launch! Check it out",
            "url": "https://instagram.com/reel/ABC123",
            "author_name": "brand_official",
            "date": "2026-01-15",
            "engagement": {
                "views": 500000,
                "likes": 25000,
                "comments": 1500,
            },
            "caption_snippet": "We're excited to announce...",
            "hashtags": ["newproduct", "launch"],
            "relevance": 0.75,
        }]
        
        result = normalize.normalize_instagram_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.InstagramItem)
        self.assertEqual(item.id, "IG1")
        self.assertEqual(item.author_name, "brand_official")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.views, 500000)
        self.assertEqual(item.engagement.likes, 25000)


class TestNormalizeHackerNewsItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_hackernews_items."""

    def test_normalizes_complete_hn_item(self):
        """Test with all HN fields populated."""
        items = [{
            "object_id": "12345",
            "title": "Show HN: I built an AI coding assistant",
            "url": "https://github.com/user/ai-assistant",
            "hn_url": "https://news.ycombinator.com/item?id=12345",
            "author": "pg",
            "date": "2026-01-15",
            "engagement": {
                "points": 500,
                "num_comments": 200,
            },
            "top_comments": [
                {"author": "dang", "text": "Great project!", "points": 50},
            ],
            "comment_insights": ["Looks promising for productivity"],
            "relevance": 0.9,
        }]
        
        result = normalize.normalize_hackernews_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.HackerNewsItem)
        self.assertEqual(item.id, "HN1")
        self.assertEqual(item.title, "Show HN: I built an AI coding assistant")
        self.assertEqual(item.author, "pg")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.score, 500)
        self.assertEqual(item.engagement.num_comments, 200)
        self.assertEqual(len(item.top_comments), 1)


class TestNormalizeBlueskyItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_bluesky_items."""

    def test_normalizes_complete_bluesky_item(self):
        """Test with all Bluesky fields populated."""
        items = [{
            "text": "Just published my new article on AT Protocol",
            "url": "https://bsky.app/profile/user.bsky.social/post/123",
            "handle": "user.bsky.social",
            "display_name": "User Name",
            "date": "2026-01-15",
            "engagement": {
                "likes": 200,
                "reposts": 50,
                "replies": 25,
                "quotes": 10,
            },
            "relevance": 0.8,
        }]
        
        result = normalize.normalize_bluesky_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.BlueskyItem)
        self.assertEqual(item.id, "BS1")
        self.assertEqual(item.author_handle, "user.bsky.social")
        self.assertEqual(item.display_name, "User Name")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.likes, 200)
        self.assertEqual(item.engagement.reposts, 50)


class TestNormalizeTruthSocialItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_truthsocial_items."""

    def test_normalizes_complete_truthsocial_item(self):
        """Test with all Truth Social fields populated."""
        items = [{
            "text": "Important announcement about our platform",
            "url": "https://truthsocial.com/@user/posts/123",
            "handle": "@user",
            "display_name": "User Name",
            "date": "2026-01-15",
            "engagement": {
                "likes": 1000,
                "reposts": 500,
                "replies": 200,
            },
            "relevance": 0.7,
        }]
        
        result = normalize.normalize_truthsocial_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.TruthSocialItem)
        self.assertEqual(item.id, "TS1")
        self.assertEqual(item.author_handle, "@user")
        self.assertEqual(item.display_name, "User Name")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.likes, 1000)
        self.assertEqual(item.engagement.reposts, 500)


class TestNormalizePolymarketItemsComprehensive(unittest.TestCase):
    """Comprehensive tests for normalize_polymarket_items."""

    def test_normalizes_complete_polymarket_item(self):
        """Test with all Polymarket fields populated."""
        items = [{
            "title": "NCAA Tournament Winner 2026",
            "question": "Will Arizona win the NCAA Tournament?",
            "url": "https://polymarket.com/event/ncaa-tournament",
            "outcome_prices": [("Arizona", 0.12), ("Duke", 0.18)],
            "outcomes_remaining": 64,
            "price_movement": "up 5% this week",
            "date": "2026-01-15",
            "volume1mo": 5000000,
            "liquidity": 500000,
            "end_date": "2026-04-15",
            "relevance": 0.85,
        }]
        
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(len(result), 1)
        item = result[0]
        self.assertIsInstance(item, schema.PolymarketItem)
        self.assertEqual(item.id, "PM1")
        self.assertEqual(item.title, "NCAA Tournament Winner 2026")
        self.assertEqual(item.question, "Will Arizona win the NCAA Tournament?")
        self.assertEqual(item.date_confidence, "high")
        self.assertIsNotNone(item.engagement)
        self.assertEqual(item.engagement.volume, 5000000)
        self.assertEqual(item.engagement.liquidity, 500000)
        self.assertEqual(item.end_date, "2026-04-15")
        self.assertEqual(len(item.outcome_prices), 2)

    def test_uses_volume1mo_over_volume24hr(self):
        """Should prefer volume1mo over volume24hr."""
        items = [{
            "title": "Test Market",
            "question": "Test?",
            "url": "https://polymarket.com/event/test",
            "volume1mo": 1000000,
            "volume24hr": 100000,
            "liquidity": 50000,
        }]
        
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(result[0].engagement.volume, 1000000)

    def test_falls_back_to_volume24hr(self):
        """Should fall back to volume24hr if volume1mo not available."""
        items = [{
            "title": "Test Market",
            "question": "Test?",
            "url": "https://polymarket.com/event/test",
            "volume24hr": 100000,
            "liquidity": 50000,
        }]
        
        result = normalize.normalize_polymarket_items(items, "2026-01-01", "2026-01-31")
        
        self.assertEqual(result[0].engagement.volume, 100000)


class TestItemsToDictsComprehensive(unittest.TestCase):
    """Comprehensive tests for items_to_dicts function."""

    def test_converts_reddit_items(self):
        items = [
            schema.RedditItem(
                id="R1",
                title="Test",
                url="https://reddit.com/r/test/1",
                subreddit="test",
                date="2026-01-15",
                engagement=schema.Engagement(score=100, num_comments=50),
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], "R1")
        self.assertEqual(result[0]["title"], "Test")
        self.assertIsNotNone(result[0]["engagement"])

    def test_converts_x_items(self):
        items = [
            schema.XItem(
                id="X1",
                text="Test tweet",
                url="https://x.com/user/status/1",
                author_handle="user",
                engagement=schema.Engagement(likes=100),
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "X1")
        self.assertEqual(result[0]["text"], "Test tweet")

    def test_converts_youtube_items(self):
        items = [
            schema.YouTubeItem(
                id="YT1",
                title="Test Video",
                url="https://youtube.com/watch?v=test",
                channel_name="Channel",
                engagement=schema.Engagement(views=10000),
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "YT1")
        self.assertEqual(result[0]["channel_name"], "Channel")

    def test_converts_tiktok_items(self):
        items = [
            schema.TikTokItem(
                id="TK1",
                text="Test TikTok",
                url="https://tiktok.com/@user/video/1",
                author_name="user",
                hashtags=["test", "tiktok"],
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "TK1")
        self.assertEqual(result[0]["hashtags"], ["test", "tiktok"])

    def test_converts_instagram_items(self):
        items = [
            schema.InstagramItem(
                id="IG1",
                text="Test Instagram",
                url="https://instagram.com/reel/abc",
                author_name="user",
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "IG1")

    def test_converts_hackernews_items(self):
        items = [
            schema.HackerNewsItem(
                id="HN1",
                title="Test HN",
                url="https://example.com",
                hn_url="https://news.ycombinator.com/item?id=1",
                author="user",
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "HN1")
        self.assertEqual(result[0]["hn_url"], "https://news.ycombinator.com/item?id=1")

    def test_converts_bluesky_items(self):
        items = [
            schema.BlueskyItem(
                id="BS1",
                text="Test Bluesky",
                url="https://bsky.app/profile/user/post/1",
                author_handle="user.bsky.social",
                display_name="User",
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "BS1")

    def test_converts_truthsocial_items(self):
        items = [
            schema.TruthSocialItem(
                id="TS1",
                text="Test TruthSocial",
                url="https://truthsocial.com/@user/posts/1",
                author_handle="@user",
                display_name="User",
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "TS1")

    def test_converts_polymarket_items(self):
        items = [
            schema.PolymarketItem(
                id="PM1",
                title="Test Market",
                question="Test?",
                url="https://polymarket.com/event/test",
                outcome_prices=[("Yes", 0.5), ("No", 0.5)],
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "PM1")
        self.assertEqual(result[0]["outcome_prices"], [("Yes", 0.5), ("No", 0.5)])

    def test_converts_websearch_items(self):
        items = [
            schema.WebSearchItem(
                id="WEB1",
                title="Test Article",
                url="https://example.com/article",
                source_domain="example.com",
                snippet="Test snippet",
            )
        ]
        result = normalize.items_to_dicts(items)
        self.assertEqual(result[0]["id"], "WEB1")
        self.assertEqual(result[0]["source_domain"], "example.com")

    def test_empty_list(self):
        result = normalize.items_to_dicts([])
        self.assertEqual(result, [])


class TestCrossTypeNormalization(unittest.TestCase):
    """Tests that verify normalization works consistently across item types."""

    def test_all_item_types_have_date_confidence(self):
        """All item types should have date_confidence field."""
        today = "2026-01-15"
        
        reddit = normalize.normalize_reddit_items([{"id": "R1", "title": "Test", "url": "", "subreddit": "", "date": today}], "2026-01-01", "2026-01-31")
        x = normalize.normalize_x_items([{"id": "X1", "text": "", "url": "", "author_handle": "", "date": today}], "2026-01-01", "2026-01-31")
        yt = normalize.normalize_youtube_items([{"video_id": "YT1", "title": "", "url": "", "channel_name": "", "date": today}], "2026-01-01", "2026-01-31")
        tk = normalize.normalize_tiktok_items([{"text": "", "url": "", "author_name": "", "date": today}], "2026-01-01", "2026-01-31")
        ig = normalize.normalize_instagram_items([{"text": "", "url": "", "author_name": "", "date": today}], "2026-01-01", "2026-01-31")
        hn = normalize.normalize_hackernews_items([{"object_id": "HN1", "title": "", "url": "", "hn_url": "", "author": "", "date": today}], "2026-01-01", "2026-01-31")
        bs = normalize.normalize_bluesky_items([{"text": "", "url": "", "handle": "", "display_name": "", "date": today}], "2026-01-01", "2026-01-31")
        ts = normalize.normalize_truthsocial_items([{"text": "", "url": "", "handle": "", "display_name": "", "date": today}], "2026-01-01", "2026-01-31")
        pm = normalize.normalize_polymarket_items([{"title": "", "question": "", "url": "", "date": today}], "2026-01-01", "2026-01-31")
        
        # All should have date_confidence
        self.assertEqual(reddit[0].date_confidence, "high")
        self.assertEqual(x[0].date_confidence, "high")
        self.assertEqual(yt[0].date_confidence, "high")
        self.assertEqual(tk[0].date_confidence, "high")
        self.assertEqual(ig[0].date_confidence, "high")
        self.assertEqual(hn[0].date_confidence, "high")
        self.assertEqual(bs[0].date_confidence, "high")
        self.assertEqual(ts[0].date_confidence, "high")
        self.assertEqual(pm[0].date_confidence, "high")


if __name__ == "__main__":
    unittest.main()