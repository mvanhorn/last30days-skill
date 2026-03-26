"""Tests for Hacker News source module."""

import sys
import unittest
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import hackernews, normalize, schema, score


class TestDateToUnix(unittest.TestCase):
    def test_known_date(self):
        # 2026-01-01 00:00:00 UTC
        result = hackernews._date_to_unix("2026-01-01")
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)

    def test_roundtrip(self):
        ts = hackernews._date_to_unix("2026-02-15")
        back = hackernews._unix_to_date(ts)
        self.assertEqual(back, "2026-02-15")


class TestStripHtml(unittest.TestCase):
    def test_basic_html(self):
        result = hackernews._strip_html("<p>Hello <b>world</b></p>")
        self.assertIn("Hello", result)
        self.assertIn("world", result)
        self.assertNotIn("<", result)

    def test_html_entities(self):
        result = hackernews._strip_html("&amp; test")
        self.assertIn("&", result)
        self.assertIn("test", result)

    def test_empty(self):
        result = hackernews._strip_html("")
        self.assertEqual(result, "")


class TestParseHackernewsResponse(unittest.TestCase):
    SAMPLE_RESPONSE = {
        "hits": [
            {
                "objectID": "12345",
                "title": "Show HN: A new AI coding assistant",
                "url": "https://example.com/article",
                "author": "pg",
                "points": 350,
                "num_comments": 127,
                "created_at_i": 1739836800,  # 2025-02-18
            },
            {
                "objectID": "12346",
                "title": "Ask HN: Best practices for LLM apps?",
                "url": "",
                "author": "dang",
                "points": 80,
                "num_comments": 45,
                "created_at_i": 1739750400,  # 2025-02-17
            },
        ],
    }

    def test_parses_hits(self):
        items = hackernews.parse_hackernews_response(self.SAMPLE_RESPONSE)
        self.assertEqual(len(items), 2)

    def test_item_fields(self):
        items = hackernews.parse_hackernews_response(self.SAMPLE_RESPONSE)
        item = items[0]
        self.assertEqual(item["object_id"], "12345")
        self.assertEqual(item["title"], "Show HN: A new AI coding assistant")
        self.assertEqual(item["url"], "https://example.com/article")
        self.assertEqual(item["hn_url"], "https://news.ycombinator.com/item?id=12345")
        self.assertEqual(item["author"], "pg")
        self.assertEqual(item["engagement"]["points"], 350)
        self.assertEqual(item["engagement"]["num_comments"], 127)

    def test_date_conversion(self):
        items = hackernews.parse_hackernews_response(self.SAMPLE_RESPONSE)
        self.assertIsNotNone(items[0]["date"])
        # Should be a valid YYYY-MM-DD string
        self.assertRegex(items[0]["date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_hn_url_for_askhn(self):
        """Ask HN posts have no article URL, but should have hn_url."""
        items = hackernews.parse_hackernews_response(self.SAMPLE_RESPONSE)
        ask_hn = items[1]
        self.assertEqual(ask_hn["url"], "")
        self.assertIn("news.ycombinator.com", ask_hn["hn_url"])

    def test_relevance_range(self):
        items = hackernews.parse_hackernews_response(self.SAMPLE_RESPONSE)
        for item in items:
            self.assertGreaterEqual(item["relevance"], 0.0)
            self.assertLessEqual(item["relevance"], 1.0)

    def test_empty_response(self):
        items = hackernews.parse_hackernews_response({"hits": []})
        self.assertEqual(items, [])

    def test_missing_hits(self):
        items = hackernews.parse_hackernews_response({})
        self.assertEqual(items, [])


class TestNormalizeHackernewsItems(unittest.TestCase):
    def test_normalize(self):
        raw_items = [
            {
                "object_id": "99999",
                "title": "Test Story",
                "url": "https://example.com",
                "hn_url": "https://news.ycombinator.com/item?id=99999",
                "author": "testuser",
                "date": "2026-02-15",
                "engagement": {"points": 100, "num_comments": 50},
                "relevance": 0.8,
                "why_relevant": "Test",
            }
        ]
        result = normalize.normalize_hackernews_items(raw_items, "2026-01-01", "2026-03-01")
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.HackerNewsItem)
        self.assertEqual(result[0].id, "HN1")
        self.assertEqual(result[0].title, "Test Story")
        self.assertEqual(result[0].date_confidence, "high")
        self.assertEqual(result[0].engagement.score, 100)
        self.assertEqual(result[0].engagement.num_comments, 50)

    def test_normalize_with_comments(self):
        raw_items = [
            {
                "object_id": "99999",
                "title": "Test",
                "url": "",
                "hn_url": "",
                "author": "user",
                "date": "2026-02-15",
                "engagement": {"points": 10, "num_comments": 5},
                "relevance": 0.5,
                "why_relevant": "Test",
                "top_comments": [
                    {"author": "commenter", "text": "Great post!", "points": 5},
                ],
                "comment_insights": ["Great post!"],
            }
        ]
        result = normalize.normalize_hackernews_items(raw_items, "2026-01-01", "2026-03-01")
        self.assertEqual(len(result[0].top_comments), 1)
        self.assertEqual(result[0].top_comments[0].author, "commenter")
        self.assertEqual(len(result[0].comment_insights), 1)


class TestScoreHackernewsItems(unittest.TestCase):
    def test_score_items(self):
        items = [
            schema.HackerNewsItem(
                id="HN1", title="High engagement", url="", hn_url="",
                author="user1", date="2026-02-20",
                engagement=schema.Engagement(score=500, num_comments=200),
                relevance=0.9,
            ),
            schema.HackerNewsItem(
                id="HN2", title="Low engagement", url="", hn_url="",
                author="user2", date="2026-02-18",
                engagement=schema.Engagement(score=10, num_comments=3),
                relevance=0.5,
            ),
        ]
        scored = score.score_hackernews_items(items)
        self.assertEqual(len(scored), 2)
        # High engagement + high relevance should score higher
        self.assertGreater(scored[0].score, scored[1].score)

    def test_score_empty(self):
        result = score.score_hackernews_items([])
        self.assertEqual(result, [])

    def test_engagement_formula(self):
        eng = schema.Engagement(score=100, num_comments=50)
        result = score.compute_hackernews_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_engagement_none(self):
        result = score.compute_hackernews_engagement_raw(None)
        self.assertIsNone(result)

    def test_engagement_empty(self):
        eng = schema.Engagement()
        result = score.compute_hackernews_engagement_raw(eng)
        self.assertIsNone(result)


class TestMergeResults(unittest.TestCase):
    """Tests for merge_results — deduplication of keyword + trending items."""

    def _make_item(self, object_id, title, points=100, trending=False):
        return {
            "object_id": object_id,
            "title": title,
            "url": "",
            "hn_url": f"https://news.ycombinator.com/item?id={object_id}",
            "author": "user",
            "date": "2026-03-24",
            "engagement": {"points": points, "num_comments": 10},
            "relevance": 0.8,
            "why_relevant": f"HN story about {title}",
        }

    def test_no_overlap(self):
        keyword = [self._make_item("1", "AI story")]
        trending = [self._make_item("2", "Supply chain attack", points=900)]
        merged = hackernews.merge_results(keyword, trending)
        self.assertEqual(len(merged), 2)
        self.assertFalse(merged[0].get("trending"))
        self.assertTrue(merged[1].get("trending"))

    def test_full_overlap(self):
        keyword = [self._make_item("1", "Same story")]
        trending = [self._make_item("1", "Same story")]
        merged = hackernews.merge_results(keyword, trending)
        self.assertEqual(len(merged), 1)
        # Keyword version takes priority (no trending flag)
        self.assertFalse(merged[0].get("trending"))

    def test_partial_overlap(self):
        keyword = [
            self._make_item("1", "Keyword only"),
            self._make_item("2", "Both keyword and trending"),
        ]
        trending = [
            self._make_item("2", "Both keyword and trending"),
            self._make_item("3", "Trending only", points=920),
        ]
        merged = hackernews.merge_results(keyword, trending)
        self.assertEqual(len(merged), 3)
        ids = [i["object_id"] for i in merged]
        self.assertEqual(ids, ["1", "2", "3"])
        # Only item 3 should be marked trending
        self.assertTrue(merged[2].get("trending"))

    def test_empty_trending(self):
        keyword = [self._make_item("1", "Keyword")]
        merged = hackernews.merge_results(keyword, [])
        self.assertEqual(len(merged), 1)

    def test_empty_keyword(self):
        trending = [self._make_item("1", "Trending", points=500)]
        merged = hackernews.merge_results([], trending)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0].get("trending"))

    def test_trending_item_has_why_relevant(self):
        merged = hackernews.merge_results(
            [], [self._make_item("1", "LiteLLM compromised", points=920)]
        )
        self.assertIn("Trending on HN", merged[0]["why_relevant"])
        self.assertIn("920", merged[0]["why_relevant"])


class TestTrendingFlagSurvivesNormalization(unittest.TestCase):
    """Verify trending flag persists through normalize → to_dict → from_dict."""

    def test_trending_survives_normalization(self):
        raw_items = [
            {
                "object_id": "42",
                "title": "Supply chain attack",
                "url": "https://example.com",
                "hn_url": "https://news.ycombinator.com/item?id=42",
                "author": "user",
                "date": "2026-03-24",
                "engagement": {"points": 920, "num_comments": 300},
                "trending": True,
                "relevance": 0.8,
                "why_relevant": "Trending on HN (920 pts)",
            },
            {
                "object_id": "43",
                "title": "Normal keyword match",
                "url": "https://example.com/2",
                "hn_url": "https://news.ycombinator.com/item?id=43",
                "author": "user2",
                "date": "2026-03-24",
                "engagement": {"points": 50, "num_comments": 10},
                "relevance": 0.7,
                "why_relevant": "HN story",
            },
        ]
        normalized = normalize.normalize_hackernews_items(raw_items, "2026-03-20", "2026-03-27")
        self.assertTrue(normalized[0].trending)
        self.assertFalse(normalized[1].trending)

        # Verify it survives to_dict serialization
        d = normalized[0].to_dict()
        self.assertTrue(d["trending"])

        d2 = normalized[1].to_dict()
        self.assertFalse(d2["trending"])


class TestSortItemsWithHN(unittest.TestCase):
    def test_hn_priority_after_youtube(self):
        """HN should sort after YouTube at same score."""
        x_item = schema.XItem(id="X1", text="test", url="", author_handle="user")
        x_item.score = 50

        hn_item = schema.HackerNewsItem(id="HN1", title="test", url="", hn_url="", author="user")
        hn_item.score = 50

        yt_item = schema.YouTubeItem(id="YT1", title="test", url="", channel_name="ch")
        yt_item.score = 50

        sorted_items = score.sort_items([yt_item, hn_item, x_item])
        # Same score, so sorted by source priority: X > YouTube > HN
        self.assertIsInstance(sorted_items[0], schema.XItem)
        self.assertIsInstance(sorted_items[1], schema.YouTubeItem)
        self.assertIsInstance(sorted_items[2], schema.HackerNewsItem)


if __name__ == "__main__":
    unittest.main()
