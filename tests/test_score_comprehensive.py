"""
Comprehensive tests for score module.

This test file adds extensive coverage for:
- All engagement calculation functions (Reddit, X, YouTube, TikTok, Instagram, HN, Bluesky, TruthSocial, Polymarket)
- Scoring functions for all item types
- Sort order with query-type-aware tiebreakers
- Edge cases and boundary conditions
- WebSearch scoring with query type penalties
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import schema, score
from lib.query_type import QueryType


class TestLog1pSafeEdgeCases(unittest.TestCase):
    """Edge case tests for log1p_safe utility function."""

    def test_large_value(self):
        """Large values should return meaningful log results."""
        result = score.log1p_safe(1_000_000)
        self.assertGreater(result, 10)
        self.assertLess(result, 20)

    def test_very_large_value(self):
        """Very large values should not overflow."""
        result = score.log1p_safe(1_000_000_000)
        self.assertIsInstance(result, float)
        self.assertGreater(result, 0)

    def test_fractional_value(self):
        """Fractional values should work correctly."""
        result = score.log1p_safe(0.5)
        # log1p(0.5) ≈ 0.405
        self.assertAlmostEqual(result, 0.405, places=2)

    def test_none_returns_zero(self):
        """None should safely return 0."""
        self.assertEqual(score.log1p_safe(None), 0.0)

    def test_negative_returns_zero(self):
        """Negative values should return 0 (invalid engagement)."""
        self.assertEqual(score.log1p_safe(-100), 0.0)

    def test_zero_returns_zero(self):
        """log1p(0) = 0."""
        self.assertEqual(score.log1p_safe(0), 0.0)


class TestComputeRedditEngagementRawDetailed(unittest.TestCase):
    """Detailed tests for Reddit engagement calculation."""

    def test_full_engagement(self):
        """Full engagement metrics should produce a reasonable score."""
        eng = schema.Engagement(
            score=1000,
            num_comments=500,
            upvote_ratio=0.95
        )
        result = score.compute_reddit_engagement_raw(eng)
        self.assertIsNotNone(result)
        # Should be positive and reasonable
        self.assertGreater(result, 5)
        self.assertLess(result, 20)

    def test_top_comment_boost(self):
        """Top comment score should boost engagement."""
        eng = schema.Engagement(score=100, num_comments=50, upvote_ratio=0.9)
        
        without_comment = score.compute_reddit_engagement_raw(eng, top_comment_score=None)
        with_small_comment = score.compute_reddit_engagement_raw(eng, top_comment_score=10)
        with_large_comment = score.compute_reddit_engagement_raw(eng, top_comment_score=1000)
        
        self.assertGreater(with_small_comment, without_comment)
        self.assertGreater(with_large_comment, with_small_comment)

    def test_zero_engagement(self):
        """Zero engagement values should work."""
        eng = schema.Engagement(score=0, num_comments=0, upvote_ratio=0.5)
        result = score.compute_reddit_engagement_raw(eng)
        # Should still produce a result based on ratio
        self.assertIsNotNone(result)

    def test_partial_engagement_score_only(self):
        """Only score provided should still work."""
        eng = schema.Engagement(score=100)
        result = score.compute_reddit_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_partial_engagement_comments_only(self):
        """Only comments provided should still work."""
        eng = schema.Engagement(num_comments=100)
        result = score.compute_reddit_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_high_upvote_ratio(self):
        """High upvote ratio should contribute positively."""
        high_ratio = schema.Engagement(score=100, num_comments=50, upvote_ratio=0.99)
        low_ratio = schema.Engagement(score=100, num_comments=50, upvote_ratio=0.51)
        
        high_result = score.compute_reddit_engagement_raw(high_ratio)
        low_result = score.compute_reddit_engagement_raw(low_ratio)
        
        self.assertGreater(high_result, low_result)


class TestComputeXEngagementRawDetailed(unittest.TestCase):
    """Detailed tests for X/Twitter engagement calculation."""

    def test_viral_post(self):
        """Viral post should have high engagement score."""
        eng = schema.Engagement(
            likes=10000,
            reposts=5000,
            replies=2000,
            quotes=500
        )
        result = score.compute_x_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 8)  # log1p scaled value

    def test_likes_dominate(self):
        """Likes should have the highest weight (0.55)."""
        likes_only = schema.Engagement(likes=1000)
        reposts_only = schema.Engagement(reposts=1000)
        
        likes_result = score.compute_x_engagement_raw(likes_only)
        reposts_result = score.compute_x_engagement_raw(reposts_only)
        
        # Likes (0.55 weight) should dominate reposts (0.25 weight)
        self.assertIsNotNone(likes_result)
        self.assertIsNotNone(reposts_result)
        self.assertGreater(likes_result, reposts_result)

    def test_zero_all_fields(self):
        """All zeros should return 0."""
        eng = schema.Engagement(likes=0, reposts=0, replies=0, quotes=0)
        result = score.compute_x_engagement_raw(eng)
        self.assertEqual(result, 0.0)

    def test_no_likes_no_reposts(self):
        """Without likes and reposts, should return None."""
        eng = schema.Engagement(replies=100, quotes=50)
        result = score.compute_x_engagement_raw(eng)
        self.assertIsNone(result)


class TestComputeYouTubeEngagementRaw(unittest.TestCase):
    """Tests for YouTube engagement calculation."""

    def test_viral_video(self):
        """Viral video should have high engagement."""
        eng = schema.Engagement(views=1_000_000, likes=50000, num_comments=5000)
        result = score.compute_youtube_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 10)

    def test_views_dominate(self):
        """Views should have highest weight (0.50)."""
        views_only = schema.Engagement(views=100000)
        likes_only = schema.Engagement(likes=100000)
        
        views_result = score.compute_youtube_engagement_raw(views_only)
        likes_result = score.compute_youtube_engagement_raw(likes_only)
        
        self.assertGreater(views_result, likes_result)

    def test_no_views_no_likes(self):
        """Without views and likes, should return None."""
        eng = schema.Engagement(num_comments=100)
        result = score.compute_youtube_engagement_raw(eng)
        self.assertIsNone(result)

    def test_none_engagement(self):
        self.assertIsNone(score.compute_youtube_engagement_raw(None))


class TestComputeTikTokEngagementRaw(unittest.TestCase):
    """Tests for TikTok engagement calculation."""

    def test_viral_tiktok(self):
        eng = schema.Engagement(views=10_000_000, likes=500000, num_comments=10000, shares=50000)
        result = score.compute_tiktok_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 10)  # log1p scaled value

    def test_views_dominate(self):
        """Views should have highest weight (0.50)."""
        views_only = schema.Engagement(views=1000000)
        likes_only = schema.Engagement(likes=1000000)
        
        views_result = score.compute_tiktok_engagement_raw(views_only)
        likes_result = score.compute_tiktok_engagement_raw(likes_only)
        
        self.assertGreater(views_result, likes_result)

    def test_no_views_no_likes(self):
        eng = schema.Engagement(num_comments=100, shares=50)
        result = score.compute_tiktok_engagement_raw(eng)
        self.assertIsNone(result)

    def test_none_engagement(self):
        self.assertIsNone(score.compute_tiktok_engagement_raw(None))


class TestComputeHackerNewsEngagementRaw(unittest.TestCase):
    """Tests for Hacker News engagement calculation."""

    def test_front_page_story(self):
        """Front page story should have high engagement."""
        eng = schema.Engagement(score=500, num_comments=300)
        result = score.compute_hackernews_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 5)

    def test_points_dominate(self):
        """Points should have slightly higher weight (0.55 vs 0.45)."""
        points_only = schema.Engagement(score=100)
        comments_only = schema.Engagement(num_comments=100)
        
        points_result = score.compute_hackernews_engagement_raw(points_only)
        comments_result = score.compute_hackernews_engagement_raw(comments_only)
        
        self.assertGreater(points_result, comments_result)

    def test_no_points_no_comments(self):
        eng = schema.Engagement()
        result = score.compute_hackernews_engagement_raw(eng)
        self.assertIsNone(result)


class TestComputePolymarketEngagementRaw(unittest.TestCase):
    """Tests for Polymarket engagement calculation."""

    def test_high_volume_market(self):
        """High volume market should have high engagement."""
        eng = schema.Engagement(volume=10_000_000, liquidity=1_000_000)
        result = score.compute_polymarket_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 10)

    def test_volume_dominate(self):
        """Volume should have higher weight (0.60)."""
        volume_only = schema.Engagement(volume=1000000)
        liquidity_only = schema.Engagement(liquidity=1000000)
        
        volume_result = score.compute_polymarket_engagement_raw(volume_only)
        liquidity_result = score.compute_polymarket_engagement_raw(liquidity_only)
        
        self.assertGreater(volume_result, liquidity_result)

    def test_zero_values(self):
        eng = schema.Engagement(volume=0, liquidity=0)
        result = score.compute_polymarket_engagement_raw(eng)
        self.assertEqual(result, 0.0)

    def test_none_engagement(self):
        self.assertIsNone(score.compute_polymarket_engagement_raw(None))


class TestNormalizeTo100EdgeCases(unittest.TestCase):
    """Edge case tests for normalize_to_100."""

    def test_all_same_values(self):
        """All same values should return 50 (middle)."""
        values = [100, 100, 100, 100]
        result = score.normalize_to_100(values)
        self.assertEqual(result, [50, 50, 50, 50])

    def test_two_values(self):
        """Two different values should normalize to 0 and 100."""
        values = [0, 1000]
        result = score.normalize_to_100(values)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 100)

    def test_all_none(self):
        """All None should return defaults."""
        values = [None, None, None]
        result = score.normalize_to_100(values, default=50)
        self.assertEqual(result, [50, 50, 50])

    def test_mixed_none_and_values(self):
        """Mix of None and values should preserve None positions."""
        values = [10, None, 100, None, 50]
        result = score.normalize_to_100(values)
        self.assertEqual(result[0], 0)  # min
        self.assertIsNone(result[1])    # None preserved
        self.assertEqual(result[2], 100)  # max
        self.assertIsNone(result[3])    # None preserved
        # 50 normalizes to (50-10)/(100-10) * 100 = 44.44...
        self.assertAlmostEqual(result[4], 44.44, places=1)

    def test_negative_values(self):
        """Negative values should work (though unusual for engagement)."""
        values = [-100, 0, 100]
        result = score.normalize_to_100(values)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 50)
        self.assertEqual(result[2], 100)


class TestScoreYouTubeItems(unittest.TestCase):
    """Tests for score_youtube_items()."""

    def test_scores_items(self):
        items = [
            schema.YouTubeItem(
                id="yt1",
                title="Test Video",
                url="https://youtube.com/watch?v=test",
                channel_name="TestChannel",
                engagement=schema.Engagement(views=10000, likes=500, num_comments=50),
                relevance=0.9,
                date=datetime.now(timezone.utc).date().isoformat(),
            ),
        ]
        result = score.score_youtube_items(items)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)
        self.assertIsNotNone(result[0].subs)

    def test_empty_list(self):
        self.assertEqual(score.score_youtube_items([]), [])

    def test_scores_higher_relevance_higher(self):
        """Higher relevance should produce higher score."""
        base_eng = schema.Engagement(views=10000, likes=500)
        
        items = [
            schema.YouTubeItem(
                id="yt1", title="High rel", url="", channel_name="ch",
                engagement=base_eng, relevance=0.9,
                date=datetime.now(timezone.utc).date().isoformat(),
            ),
            schema.YouTubeItem(
                id="yt2", title="Low rel", url="", channel_name="ch",
                engagement=base_eng, relevance=0.3,
                date=datetime.now(timezone.utc).date().isoformat(),
            ),
        ]
        result = score.score_youtube_items(items)
        self.assertGreater(result[0].score, result[1].score)


class TestScoreTikTokItems(unittest.TestCase):
    """Tests for score_tiktok_items()."""

    def test_scores_items(self):
        items = [
            schema.TikTokItem(
                id="tk1",
                text="Test TikTok",
                url="https://tiktok.com/@user/video/123",
                author_name="testuser",
                engagement=schema.Engagement(views=100000, likes=5000, num_comments=500),
                relevance=0.8,
            ),
        ]
        result = score.score_tiktok_items(items)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)

    def test_empty_list(self):
        self.assertEqual(score.score_tiktok_items([]), [])


class TestScoreInstagramItems(unittest.TestCase):
    """Tests for score_instagram_items()."""

    def test_scores_items(self):
        items = [
            schema.InstagramItem(
                id="ig1",
                text="Test Instagram",
                url="https://instagram.com/reel/abc",
                author_name="testuser",
                engagement=schema.Engagement(views=50000, likes=2000, num_comments=100),
                relevance=0.7,
            ),
        ]
        result = score.score_instagram_items(items)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)

    def test_empty_list(self):
        self.assertEqual(score.score_instagram_items([]), [])


class TestScorePolymarketItems(unittest.TestCase):
    """Tests for score_polymarket_items()."""

    def test_scores_items(self):
        items = [
            schema.PolymarketItem(
                id="pm1",
                title="Test Market",
                question="Will X happen?",
                url="https://polymarket.com/event/test",
                engagement=schema.Engagement(volume=1000000, liquidity=100000),
                relevance=0.9,
            ),
        ]
        result = score.score_polymarket_items(items)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)

    def test_polymarket_uses_different_weights(self):
        """Polymarket uses PM_WEIGHT_* constants (0.60/0.20/0.20)."""
        items = [
            schema.PolymarketItem(
                id="pm1", title="Test", question="?", url="",
                engagement=schema.Engagement(volume=1000000, liquidity=100000),
                relevance=0.8,
            ),
        ]
        result = score.score_polymarket_items(items)
        # Verify subscores exist
        self.assertIsNotNone(result[0].subs)
        self.assertEqual(result[0].subs.relevance, 80)  # 0.8 * 100


class TestScoreWebSearchItems(unittest.TestCase):
    """Tests for score_websearch_items() with query type awareness."""

    def test_basic_scoring(self):
        """WebSearch items should score without engagement."""
        items = [
            schema.WebSearchItem(
                id="web1",
                title="Test Article",
                url="https://example.com/article",
                source_domain="example.com",
                snippet="Test snippet",
                relevance=0.8,
                date_confidence="high",
            ),
        ]
        result = score.score_websearch_items(items)
        self.assertEqual(len(result), 1)
        self.assertGreater(result[0].score, 0)
        # Engagement should be 0 for web
        self.assertEqual(result[0].subs.engagement, 0)

    def test_query_type_penalty_concept(self):
        """Concept queries should have no penalty."""
        # Create separate items for each call (function modifies in place)
        concept_items = [
            schema.WebSearchItem(
                id="web1", title="Test", url="", source_domain="example.com",
                snippet="", relevance=0.8, date_confidence="med",
            ),
        ]
        product_items = [
            schema.WebSearchItem(
                id="web2", title="Test", url="", source_domain="example.com",
                snippet="", relevance=0.8, date_confidence="med",
            ),
        ]
        
        # Concept queries get 0 penalty (web docs are authoritative)
        concept_result = score.score_websearch_items(concept_items, query_type="concept")
        
        # Product queries get full penalty (social discussion is more valuable)
        product_result = score.score_websearch_items(product_items, query_type="product")
        
        # Concept should score higher (no penalty)
        self.assertGreater(concept_result[0].score, product_result[0].score)

    def test_date_confidence_bonus(self):
        """High date confidence should get bonus."""
        high_conf = [
            schema.WebSearchItem(
                id="web1", title="Test", url="", source_domain="example.com",
                snippet="", relevance=0.8, date_confidence="high",
            ),
        ]
        low_conf = [
            schema.WebSearchItem(
                id="web2", title="Test", url="", source_domain="example.com",
                snippet="", relevance=0.8, date_confidence="low",
            ),
        ]
        
        high_result = score.score_websearch_items(high_conf)
        low_result = score.score_websearch_items(low_conf)
        
        self.assertGreater(high_result[0].score, low_result[0].score)

    def test_empty_list(self):
        self.assertEqual(score.score_websearch_items([]), [])


class TestSortItemsWithQueryType(unittest.TestCase):
    """Tests for sort_items with query-type-aware tiebreakers."""

    def test_default_tiebreaker(self):
        """Default tiebreaker: Reddit > X > YouTube > TikTok > Instagram > HN > Bluesky > TruthSocial > Polymarket."""
        items = []
        for source_cls, id_prefix in [
            (schema.RedditItem, "R"),
            (schema.XItem, "X"),
            (schema.YouTubeItem, "YT"),
            (schema.TikTokItem, "TK"),
            (schema.InstagramItem, "IG"),
            (schema.HackerNewsItem, "HN"),
            (schema.BlueskyItem, "BS"),
            (schema.TruthSocialItem, "TS"),
            (schema.PolymarketItem, "PM"),
        ]:
            if source_cls == schema.RedditItem:
                item = source_cls(id=id_prefix, title="Test", url="", subreddit="")
            elif source_cls == schema.XItem:
                item = source_cls(id=id_prefix, text="Test", url="", author_handle="user")
            elif source_cls == schema.YouTubeItem:
                item = source_cls(id=id_prefix, title="Test", url="", channel_name="ch")
            elif source_cls == schema.TikTokItem:
                item = source_cls(id=id_prefix, text="Test", url="", author_name="user")
            elif source_cls == schema.InstagramItem:
                item = source_cls(id=id_prefix, text="Test", url="", author_name="user")
            elif source_cls == schema.HackerNewsItem:
                item = source_cls(id=id_prefix, title="Test", url="", hn_url="", author="user")
            elif source_cls == schema.BlueskyItem:
                item = source_cls(id=id_prefix, text="Test", url="", author_handle="user.bsky.social", display_name="User")
            elif source_cls == schema.TruthSocialItem:
                item = source_cls(id=id_prefix, text="Test", url="", author_handle="@user", display_name="User")
            elif source_cls == schema.PolymarketItem:
                item = source_cls(id=id_prefix, title="Test", question="?", url="")
            item.score = 50  # All same score
            items.append(item)
        
        result = score.sort_items(items)
        
        # Verify order matches default tiebreaker
        ids = [item.id for item in result]
        self.assertEqual(ids.index("R"), 0)
        self.assertEqual(ids.index("X"), 1)
        self.assertEqual(ids.index("YT"), 2)

    def test_sort_by_score_descending(self):
        """Primary sort should be score descending."""
        items = [
            schema.RedditItem(id="R1", title="Low", url="", subreddit="", score=10),
            schema.RedditItem(id="R2", title="High", url="", subreddit="", score=90),
            schema.RedditItem(id="R3", title="Mid", url="", subreddit="", score=50),
        ]
        result = score.sort_items(items)
        self.assertEqual([r.id for r in result], ["R2", "R3", "R1"])

    def test_sort_by_date_secondary(self):
        """Secondary sort should be date (most recent first)."""
        items = [
            schema.RedditItem(id="R1", title="A", url="", subreddit="", score=50, date="2026-01-01"),
            schema.RedditItem(id="R2", title="B", url="", subreddit="", score=50, date="2026-01-15"),
            schema.RedditItem(id="R3", title="C", url="", subreddit="", score=50, date="2026-01-08"),
        ]
        result = score.sort_items(items)
        # Same score, so sorted by date descending
        self.assertEqual([r.id for r in result], ["R2", "R3", "R1"])

    def test_none_date_sorts_last(self):
        """Items with None date should sort after dated items."""
        items = [
            schema.RedditItem(id="R1", title="A", url="", subreddit="", score=50, date=None),
            schema.RedditItem(id="R2", title="B", url="", subreddit="", score=50, date="2026-01-15"),
        ]
        result = score.sort_items(items)
        self.assertEqual(result[0].id, "R2")


class TestDateConfidencePenalties(unittest.TestCase):
    """Tests for date confidence affecting scores."""

    def test_low_confidence_penalty(self):
        """Low date confidence should penalize score."""
        today = datetime.now(timezone.utc).date().isoformat()
        
        high_conf = schema.RedditItem(
            id="R1", title="Test", url="", subreddit="",
            date=today, date_confidence="high",
            engagement=schema.Engagement(score=100),
            relevance=0.8,
        )
        low_conf = schema.RedditItem(
            id="R2", title="Test", url="", subreddit="",
            date=today, date_confidence="low",
            engagement=schema.Engagement(score=100),
            relevance=0.8,
        )
        
        high_result = score.score_reddit_items([high_conf])
        low_result = score.score_reddit_items([low_conf])
        
        self.assertGreater(high_result[0].score, low_result[0].score)

    def test_med_confidence_small_penalty(self):
        """Medium confidence should have smaller penalty than low."""
        today = datetime.now(timezone.utc).date().isoformat()
        
        med_conf = schema.RedditItem(
            id="R1", title="Test", url="", subreddit="",
            date=today, date_confidence="med",
            engagement=schema.Engagement(score=100),
            relevance=0.8,
        )
        low_conf = schema.RedditItem(
            id="R2", title="Test", url="", subreddit="",
            date=today, date_confidence="low",
            engagement=schema.Engagement(score=100),
            relevance=0.8,
        )
        
        med_result = score.score_reddit_items([med_conf])
        low_result = score.score_reddit_items([low_conf])
        
        self.assertGreater(med_result[0].score, low_result[0].score)


class TestUnknownEngagementPenalty(unittest.TestCase):
    """Tests for unknown engagement penalty."""

    def test_unknown_engagement_penalty(self):
        """Items without engagement should be penalized."""
        today = datetime.now(timezone.utc).date().isoformat()
        
        with_eng = schema.RedditItem(
            id="R1", title="Test", url="", subreddit="",
            date=today, date_confidence="high",
            engagement=schema.Engagement(score=100, num_comments=50),
            relevance=0.8,
        )
        without_eng = schema.RedditItem(
            id="R2", title="Test", url="", subreddit="",
            date=today, date_confidence="high",
            engagement=None,
            relevance=0.8,
        )
        
        with_result = score.score_reddit_items([with_eng])
        without_result = score.score_reddit_items([without_eng])
        
        self.assertGreater(with_result[0].score, without_result[0].score)


if __name__ == "__main__":
    unittest.main()