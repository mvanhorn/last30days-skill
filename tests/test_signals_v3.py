import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import schema, signals
from lib.hackernews import parse_hackernews_response


class SignalsV3Tests(unittest.TestCase):
    def test_reddit_engagement_uses_source_specific_formula(self):
        item = schema.SourceItem(
            item_id="r1",
            source="reddit",
            title="Title",
            body="Body",
            url="https://example.com",
            engagement={"score": 99, "num_comments": 20, "upvote_ratio": 0.8},
            metadata={"top_comments": [{"score": 10}]},
        )
        expected = (
            0.50 * math.log1p(99)
            + 0.35 * math.log1p(20)
            + 0.05 * (0.8 * 10.0)
            + 0.10 * math.log1p(10)
        )
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_youtube_engagement_adds_top_comment_slot(self):
        with_comment = schema.SourceItem(
            item_id="yt1",
            source="youtube",
            title="Title",
            body="Body",
            url="https://youtube.com/watch?v=a",
            engagement={"views": 10000, "likes": 500, "comments": 30},
            metadata={"top_comments": [{"score": 500}]},
        )
        without = schema.SourceItem(
            item_id="yt2",
            source="youtube",
            title="Title",
            body="Body",
            url="https://youtube.com/watch?v=b",
            engagement={"views": 10000, "likes": 500, "comments": 30},
            metadata={"top_comments": []},
        )
        with_score = signals.engagement_raw(with_comment)
        without_score = signals.engagement_raw(without)
        self.assertIsNotNone(with_score)
        self.assertIsNotNone(without_score)
        self.assertGreater(with_score, without_score)
        expected = (
            0.45 * math.log1p(10000)
            + 0.32 * math.log1p(500)
            + 0.13 * math.log1p(30)
            + 0.10 * math.log1p(500)
        )
        self.assertAlmostEqual(expected, with_score, places=6)

    def test_youtube_engagement_empty_returns_none(self):
        item = schema.SourceItem(
            item_id="yt-empty",
            source="youtube",
            title="Title",
            body="Body",
            url="https://youtube.com/watch?v=e",
            engagement={},
            metadata={"top_comments": []},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_tiktok_engagement_adds_top_comment_slot(self):
        item = schema.SourceItem(
            item_id="tt1",
            source="tiktok",
            title="Title",
            body="Body",
            url="https://tiktok.com/@u/video/1",
            engagement={"views": 100000, "likes": 5000, "comments": 500},
            metadata={"top_comments": [{"score": 1200}]},
        )
        expected = (
            0.45 * math.log1p(100000)
            + 0.27 * math.log1p(5000)
            + 0.18 * math.log1p(500)
            + 0.10 * math.log1p(1200)
        )
        self.assertAlmostEqual(expected, signals.engagement_raw(item), places=6)

    def test_youtube_ranking_promotes_viral_comment_thread(self):
        """A moderately-viewed YouTube video with a 10k-like comment should
        outrank a slightly-higher-viewed video with no high-signal comments."""
        viral_comment = schema.SourceItem(
            item_id="yt-with-viral-comment",
            source="youtube",
            title="Deploy to Fly.io",
            body="Deploy to Fly.io walkthrough",
            url="https://youtube.com/watch?v=x",
            published_at="2026-03-15",
            engagement={"views": 5000, "likes": 200, "comments": 50},
            metadata={"top_comments": [{"score": 10000}]},
        )
        higher_views = schema.SourceItem(
            item_id="yt-higher-views-no-comment",
            source="youtube",
            title="Deploy to Fly.io",
            body="Deploy to Fly.io walkthrough",
            url="https://youtube.com/watch?v=y",
            published_at="2026-03-15",
            engagement={"views": 8000, "likes": 300, "comments": 60},
            metadata={"top_comments": []},
        )
        ranked = signals.annotate_stream(
            [higher_views, viral_comment],
            ranking_query="How do I deploy on Fly.io?",
            freshness_mode="balanced_recent",
        )
        self.assertEqual("yt-with-viral-comment", ranked[0].item_id)

    def test_polymarket_engagement_uses_market_fields(self):
        item = schema.SourceItem(
            item_id="pm1",
            source="polymarket",
            title="Title",
            body="Body",
            url="https://example.com",
            engagement={"volume": 1000, "liquidity": 250},
        )
        expected = (0.60 * math.log1p(1000)) + (0.40 * math.log1p(250))
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_grounding_uses_generic_fallback(self):
        item = schema.SourceItem(
            item_id="g1",
            source="grounding",
            title="Title",
            body="Body",
            url="https://example.com",
            engagement={"shares": 10, "reads": 100},
        )
        expected = (math.log1p(10) + math.log1p(100)) / 2
        self.assertAlmostEqual(expected, signals.engagement_raw(item))

    def test_annotate_stream_sorts_by_source_specific_reddit_engagement(self):
        higher = schema.SourceItem(
            item_id="r-high",
            source="reddit",
            title="High signal",
            body="claude code skill",
            url="https://example.com/high",
            published_at="2026-03-15",
            engagement={"score": 120, "num_comments": 40, "upvote_ratio": 0.9},
            metadata={"top_comments": [{"score": 15}]},
        )
        lower = schema.SourceItem(
            item_id="r-low",
            source="reddit",
            title="Lower signal",
            body="claude code skill",
            url="https://example.com/low",
            published_at="2026-03-15",
            engagement={"score": 4, "num_comments": 1, "upvote_ratio": 0.5},
            metadata={"top_comments": [{"score": 1}]},
        )
        ranked = signals.annotate_stream(
            [lower, higher],
            ranking_query="What recent evidence matters for claude code skill?",
            freshness_mode="balanced_recent",
        )
        self.assertEqual(["r-high", "r-low"], [item.item_id for item in ranked])

    def test_local_relevance_dominates_over_high_engagement_noise(self):
        relevant = schema.SourceItem(
            item_id="relevant",
            source="reddit",
            title="Deploy to Fly.io with MCP in 60 seconds",
            body="Deploy to Fly.io guide with concrete steps.",
            url="https://example.com/relevant",
            published_at="2026-03-15",
            engagement={"score": 2, "num_comments": 0, "upvote_ratio": 0.8},
            metadata={"top_comments": []},
        )
        noisy = schema.SourceItem(
            item_id="noisy",
            source="reddit",
            title="BATTLEFIELD 6 GAME UPDATE 1.2.2.0",
            body="Patch notes and gameplay discussion.",
            url="https://example.com/noisy",
            published_at="2026-03-15",
            engagement={"score": 5000, "num_comments": 1200, "upvote_ratio": 0.95},
            metadata={"top_comments": [{"score": 400}]},
        )
        ranked = signals.annotate_stream(
            [noisy, relevant],
            ranking_query="How do I deploy on Fly.io?",
            freshness_mode="evergreen_ok",
        )
        self.assertEqual("relevant", ranked[0].item_id)

    def test_prune_low_relevance_keeps_stronger_matches(self):
        strong = schema.SourceItem(
            item_id="strong",
            source="reddit",
            title="Deploy to Fly.io",
            body="Step-by-step Fly.io deploy guide.",
            url="https://example.com/strong",
            local_relevance=0.3,
        )
        weak = schema.SourceItem(
            item_id="weak",
            source="reddit",
            title="Battlefield update",
            body="Patch notes.",
            url="https://example.com/weak",
            local_relevance=0.0,
        )
        pruned = signals.prune_low_relevance([strong, weak], minimum=0.1)
        self.assertEqual(["strong"], [item.item_id for item in pruned])

    def test_prune_low_relevance_falls_back_when_all_are_weak(self):
        weak = schema.SourceItem(
            item_id="weak",
            source="reddit",
            title="Generic post",
            body="Generic body.",
            url="https://example.com/weak",
            metadata={"local_relevance": 0.02},
        )
        pruned = signals.prune_low_relevance([weak], minimum=0.1)
        self.assertEqual(["weak"], [item.item_id for item in pruned])


    # -- Iteration 1: HN engagement bug --

    def test_hackernews_parse_emits_comments_key(self):
        """parse_hackernews_response must emit 'comments' (not 'num_comments')."""
        response = {
            "hits": [
                {
                    "objectID": "123",
                    "title": "Show HN: Something Cool",
                    "url": "https://example.com",
                    "author": "pg",
                    "points": 150,
                    "num_comments": 45,
                    "created_at_i": 1710720000,
                },
            ],
        }
        items = parse_hackernews_response(response, query="something cool")
        self.assertIn("comments", items[0]["engagement"])
        self.assertNotIn("num_comments", items[0]["engagement"])
        self.assertEqual(items[0]["engagement"]["comments"], 45)

    def test_hackernews_engagement_raw_uses_both_fields(self):
        """engagement_raw for HN must weight both points and comments."""
        item = schema.SourceItem(
            item_id="hn1",
            source="hackernews",
            title="Show HN: Something",
            body="Description",
            url="https://example.com",
            engagement={"points": 150, "comments": 45},
        )
        expected = 0.55 * math.log1p(150) + 0.45 * math.log1p(45)
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(expected, result)
        # Verify comments actually contributed (not just points)
        points_only = 0.55 * math.log1p(150)
        self.assertGreater(result, points_only)

    # -- Iteration 4: Missing engagement formula tests --

    def test_x_engagement_dominant_weight(self):
        """X: likes at 0.55 should dominate over quotes at 0.05."""
        item = schema.SourceItem(
            item_id="x1", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 100, "reposts": 100, "replies": 100, "quotes": 100},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.55 * math.log1p(100)
            + 0.25 * math.log1p(100)
            + 0.15 * math.log1p(100)
            + 0.05 * math.log1p(100)
        )
        self.assertAlmostEqual(expected, result)

    def test_x_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="x2", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 0, "reposts": 0, "replies": 0, "quotes": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_x_engagement_missing_fields(self):
        """Missing fields default to 0, no crash."""
        item = schema.SourceItem(
            item_id="x3", source="x", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 50},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.55 * math.log1p(50)
        self.assertAlmostEqual(expected, result)

    def test_youtube_engagement_dominant_weight(self):
        """YouTube: views at 0.45 should dominate. With no top-comment data,
        the remaining 0.90 of weight is split views/likes/comments 0.45/0.32/0.13."""
        item = schema.SourceItem(
            item_id="yt1", source="youtube", title="T", body="B",
            url="https://example.com",
            engagement={"views": 10000, "likes": 500, "comments": 80},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.45 * math.log1p(10000)
            + 0.32 * math.log1p(500)
            + 0.13 * math.log1p(80)
        )
        self.assertAlmostEqual(expected, result)

    def test_youtube_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="yt2", source="youtube", title="T", body="B",
            url="https://example.com",
            engagement={"views": 0, "likes": 0, "comments": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_youtube_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="yt3", source="youtube", title="T", body="B",
            url="https://example.com",
            engagement={"views": 5000},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.45 * math.log1p(5000)
        self.assertAlmostEqual(expected, result)

    def test_tiktok_engagement_dominant_weight(self):
        item = schema.SourceItem(
            item_id="tt1", source="tiktok", title="T", body="B",
            url="https://example.com",
            engagement={"views": 50000, "likes": 3000, "comments": 200},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.45 * math.log1p(50000)
            + 0.27 * math.log1p(3000)
            + 0.18 * math.log1p(200)
        )
        self.assertAlmostEqual(expected, result)

    def test_tiktok_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="tt2", source="tiktok", title="T", body="B",
            url="https://example.com",
            engagement={"views": 0, "likes": 0, "comments": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_tiktok_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="tt3", source="tiktok", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 1000},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.27 * math.log1p(1000)
        self.assertAlmostEqual(expected, result)

    def test_instagram_engagement_dominant_weight(self):
        item = schema.SourceItem(
            item_id="ig1", source="instagram", title="T", body="B",
            url="https://example.com",
            engagement={"views": 8000, "likes": 1500, "comments": 100},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.50 * math.log1p(8000)
            + 0.30 * math.log1p(1500)
            + 0.20 * math.log1p(100)
        )
        self.assertAlmostEqual(expected, result)

    def test_instagram_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="ig2", source="instagram", title="T", body="B",
            url="https://example.com",
            engagement={"views": 0, "likes": 0, "comments": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_instagram_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="ig3", source="instagram", title="T", body="B",
            url="https://example.com",
            engagement={"comments": 50},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.20 * math.log1p(50)
        self.assertAlmostEqual(expected, result)

    def test_hackernews_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="hn2", source="hackernews", title="T", body="B",
            url="https://example.com",
            engagement={"points": 0, "comments": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_hackernews_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="hn3", source="hackernews", title="T", body="B",
            url="https://example.com",
            engagement={"points": 75},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.55 * math.log1p(75)
        self.assertAlmostEqual(expected, result)

    def test_bluesky_engagement_dominant_weight(self):
        """Bluesky: likes at 0.40 should dominate over quotes at 0.10."""
        item = schema.SourceItem(
            item_id="bs1", source="bluesky", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 200, "reposts": 50, "replies": 30, "quotes": 10},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.40 * math.log1p(200)
            + 0.30 * math.log1p(50)
            + 0.20 * math.log1p(30)
            + 0.10 * math.log1p(10)
        )
        self.assertAlmostEqual(expected, result)

    def test_bluesky_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="bs2", source="bluesky", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 0, "reposts": 0, "replies": 0, "quotes": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_bluesky_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="bs3", source="bluesky", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 100, "replies": 20},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.40 * math.log1p(100) + 0.20 * math.log1p(20)
        self.assertAlmostEqual(expected, result)

    def test_truthsocial_engagement_dominant_weight(self):
        """Truth Social: likes at 0.45 should dominate over replies at 0.25."""
        item = schema.SourceItem(
            item_id="ts1", source="truthsocial", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 500, "reposts": 100, "replies": 50},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = (
            0.45 * math.log1p(500)
            + 0.30 * math.log1p(100)
            + 0.25 * math.log1p(50)
        )
        self.assertAlmostEqual(expected, result)

    def test_truthsocial_engagement_all_zero_returns_none(self):
        item = schema.SourceItem(
            item_id="ts2", source="truthsocial", title="T", body="B",
            url="https://example.com",
            engagement={"likes": 0, "reposts": 0, "replies": 0},
        )
        self.assertIsNone(signals.engagement_raw(item))

    def test_truthsocial_engagement_missing_fields(self):
        item = schema.SourceItem(
            item_id="ts3", source="truthsocial", title="T", body="B",
            url="https://example.com",
            engagement={"reposts": 80},
        )
        result = signals.engagement_raw(item)
        self.assertIsNotNone(result)
        expected = 0.30 * math.log1p(80)
        self.assertAlmostEqual(expected, result)

    # -- Fix 5: Rebalance engagement weight --

    def test_engagement_weight_meaningful_for_social_ranking(self):
        """Engagement must have enough weight to differentiate otherwise-equal items."""
        high_engagement = schema.SourceItem(
            item_id="viral",
            source="x",
            title="Trending topic discussion",
            body="Popular social post",
            url="https://example.com/viral",
            published_at="2026-03-15",
            engagement={"likes": 50000, "reposts": 5000, "replies": 2000, "quotes": 500},
        )
        low_engagement = schema.SourceItem(
            item_id="quiet",
            source="x",
            title="Trending topic discussion",
            body="Popular social post",
            url="https://example.com/quiet",
            published_at="2026-03-15",
            engagement={"likes": 10, "reposts": 1, "replies": 0, "quotes": 0},
        )
        ranked = signals.annotate_stream(
            [low_engagement, high_engagement],
            ranking_query="trending topic discussion",
            freshness_mode="balanced_recent",
        )
        high_score = ranked[0].local_rank_score
        low_score = ranked[1].local_rank_score
        gap = high_score - low_score
        # With 10% engagement weight, the gap should be >= 0.06
        # With 5% weight, gap would be ~0.04
        self.assertGreaterEqual(gap, 0.06,
                                f"Engagement gap should be >= 0.06 with 10% weight, got {gap:.4f}")

    # -- Fix 4: Lower prune threshold for social media --

    def test_prune_keeps_social_items_above_003(self):
        """Social media items with low but non-trivial relevance should survive pruning."""
        social = schema.SourceItem(
            item_id="social",
            source="x",
            title="Viral tweet about topic",
            body="Short social post",
            url="https://example.com/social",
            metadata={"local_relevance": 0.05},
        )
        strong = schema.SourceItem(
            item_id="strong",
            source="grounding",
            title="Detailed article about topic",
            body="In-depth analysis",
            url="https://example.com/strong",
            metadata={"local_relevance": 0.4},
        )
        pruned = signals.prune_low_relevance([strong, social])
        ids = [item.item_id for item in pruned]
        self.assertIn("social", ids, "Item with relevance 0.05 should survive pruning")
        self.assertIn("strong", ids)


    # -- Unit 3: YouTube high-engagement relevance floor --

    def test_youtube_high_engagement_gets_relevance_floor(self):
        """YouTube video with >100K views gets at least 0.3 relevance even with low text overlap."""
        item = schema.SourceItem(
            item_id="yt-official",
            source="youtube",
            title="YE - FATHER (feat. TRAVIS SCOTT)",
            body="Official music video",
            url="https://youtube.com/watch?v=abc",
            engagement={"views": 8_000_000, "likes": 422_000, "comments": 5000},
        )
        rel = signals.local_relevance(item, "kanye west")
        self.assertGreaterEqual(rel, 0.3, f"High-engagement YouTube should get >= 0.3 relevance, got {rel}")

    def test_youtube_low_engagement_no_floor(self):
        """YouTube video with <100K views does NOT get the relevance floor."""
        item = schema.SourceItem(
            item_id="yt-small",
            source="youtube",
            title="Random unrelated video title",
            body="Nothing relevant here",
            url="https://youtube.com/watch?v=xyz",
            engagement={"views": 500, "likes": 10, "comments": 1},
        )
        rel = signals.local_relevance(item, "kanye west")
        self.assertLess(rel, 0.3, f"Low-engagement YouTube should not get floor, got {rel}")

    def test_non_youtube_high_engagement_no_floor(self):
        """Non-YouTube items with high engagement do NOT get the YouTube floor."""
        item = schema.SourceItem(
            item_id="reddit-viral",
            source="reddit",
            title="Completely unrelated post",
            body="Nothing about the topic",
            url="https://reddit.com/r/test",
            engagement={"score": 50000, "num_comments": 3000},
        )
        rel = signals.local_relevance(item, "kanye west")
        self.assertLess(rel, 0.3, f"Non-YouTube item should not get YouTube floor, got {rel}")


    # -- Unit 8: Engagement floor for TikTok/Instagram --

    def test_tiktok_below_1000_views_pruned(self):
        """TikTok items with <1000 views should be pruned when other sources exist."""
        spam = schema.SourceItem(
            item_id="tt-spam", source="tiktok", title="AI news clip", body="Generic",
            url="https://tiktok.com/spam",
            local_relevance=0.4, engagement={"views": 500, "likes": 10, "comments": 1},
        )
        good = schema.SourceItem(
            item_id="r-good", source="reddit", title="Good discussion", body="Quality",
            url="https://reddit.com/good",
            local_relevance=0.5, engagement_score=50,
        )
        pruned = signals.prune_low_relevance([good, spam])
        ids = [item.item_id for item in pruned]
        self.assertNotIn("tt-spam", ids, "TikTok with 500 views should be pruned")
        self.assertIn("r-good", ids)

    def test_instagram_below_1000_views_pruned(self):
        """Instagram items with <1000 views should be pruned when other sources exist."""
        spam = schema.SourceItem(
            item_id="ig-spam", source="instagram", title="Repost clip", body="Generic",
            url="https://instagram.com/spam",
            local_relevance=0.4, engagement={"views": 200, "likes": 5, "comments": 0},
        )
        good = schema.SourceItem(
            item_id="x-good", source="x", title="Good tweet", body="Quality",
            url="https://x.com/good",
            local_relevance=0.5, engagement_score=50,
        )
        pruned = signals.prune_low_relevance([good, spam])
        ids = [item.item_id for item in pruned]
        self.assertNotIn("ig-spam", ids, "Instagram with 200 views should be pruned")

    def test_tiktok_above_1000_views_kept(self):
        """TikTok items with >=1000 views should survive pruning."""
        good_tt = schema.SourceItem(
            item_id="tt-good", source="tiktok", title="Popular clip", body="Relevant",
            url="https://tiktok.com/good",
            local_relevance=0.4, engagement={"views": 5000, "likes": 200, "comments": 30},
        )
        other = schema.SourceItem(
            item_id="r-other", source="reddit", title="Reddit post", body="Relevant",
            url="https://reddit.com/other",
            local_relevance=0.5, engagement_score=50,
        )
        pruned = signals.prune_low_relevance([other, good_tt])
        ids = [item.item_id for item in pruned]
        self.assertIn("tt-good", ids, "TikTok with 5000 views should be kept")

    def test_tiktok_sole_source_not_pruned(self):
        """When TikTok is the only source, low-view items should NOT be pruned."""
        items = [
            schema.SourceItem(
                item_id=f"tt-{i}", source="tiktok", title=f"Clip {i}", body="Content",
                url=f"https://tiktok.com/{i}",
                local_relevance=0.4, engagement={"views": 300, "likes": 5, "comments": 0},
            )
            for i in range(3)
        ]
        pruned = signals.prune_low_relevance(items)
        self.assertEqual(len(pruned), 3, "Sole-source TikTok items should all survive")

    def test_non_video_sources_unaffected_by_floor(self):
        """Reddit/X items should not be affected by the video engagement floor."""
        low_eng_x = schema.SourceItem(
            item_id="x-low", source="x", title="Tweet", body="Topic discussion",
            url="https://x.com/low",
            local_relevance=0.5, engagement={"likes": 2, "reposts": 0},
            engagement_score=5,
        )
        other = schema.SourceItem(
            item_id="r-other", source="reddit", title="Post", body="Topic",
            url="https://reddit.com/other",
            local_relevance=0.5, engagement_score=50,
        )
        pruned = signals.prune_low_relevance([other, low_eng_x])
        ids = [item.item_id for item in pruned]
        self.assertIn("x-low", ids, "X items should not be affected by video floor")

    def test_aspiresnippets_scenario(self):
        """@aspiresnippets scenario: 5 TikTok items with 200-700 views all pruned."""
        spam_items = [
            schema.SourceItem(
                item_id=f"aspire-{i}", source="tiktok", title=f"AI news {i}", body="Generic clip",
                url=f"https://tiktok.com/aspire/{i}",
                local_relevance=0.3, engagement={"views": 200 + i * 100, "likes": 5, "comments": 0},
            )
            for i in range(5)
        ]
        good = schema.SourceItem(
            item_id="good-yt", source="youtube", title="In-depth analysis", body="Quality content",
            url="https://youtube.com/good",
            local_relevance=0.6, engagement_score=70,
        )
        pruned = signals.prune_low_relevance([good] + spam_items)
        aspire_ids = [item.item_id for item in pruned if item.item_id.startswith("aspire")]
        self.assertEqual(len(aspire_ids), 0, f"All @aspiresnippets items should be pruned, got {aspire_ids}")


if __name__ == "__main__":
    unittest.main()
