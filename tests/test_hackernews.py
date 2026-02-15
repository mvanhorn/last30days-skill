"""Tests for hackernews module."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import hackernews, normalize, schema, score, dedupe


class TestSearchHN(unittest.TestCase):
    def test_mock_response(self):
        mock = {
            "hits": [
                {
                    "title": "Show HN: Test Project",
                    "objectID": "12345",
                    "url": "https://example.com/test",
                    "author": "testuser",
                    "created_at_i": int(datetime.now(timezone.utc).timestamp()),
                    "points": 150,
                    "num_comments": 42,
                }
            ],
            "nbHits": 1,
        }

        result = hackernews.search_hn(
            "test", "2026-01-01", "2026-02-14",
            mock_response=mock,
        )

        self.assertEqual(result, mock)


class TestParseHNResponse(unittest.TestCase):
    def test_parses_basic_hit(self):
        response = {
            "hits": [
                {
                    "title": "Show HN: Cool Project",
                    "objectID": "12345",
                    "url": "https://example.com/cool",
                    "author": "hacker",
                    "created_at_i": 1706745600,  # 2024-02-01
                    "points": 200,
                    "num_comments": 80,
                }
            ]
        }

        items = hackernews.parse_hn_response(response)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "HN1")
        self.assertEqual(items[0]["title"], "Show HN: Cool Project")
        self.assertEqual(items[0]["url"], "https://example.com/cool")
        self.assertEqual(items[0]["hn_url"], "https://news.ycombinator.com/item?id=12345")
        self.assertEqual(items[0]["author"], "hacker")
        self.assertEqual(items[0]["engagement"]["points"], 200)
        self.assertEqual(items[0]["engagement"]["num_comments"], 80)

    def test_empty_response(self):
        items = hackernews.parse_hn_response({})
        self.assertEqual(items, [])

    def test_error_response(self):
        items = hackernews.parse_hn_response({"error": "bad request"})
        self.assertEqual(items, [])

    def test_skips_items_without_title(self):
        response = {
            "hits": [
                {"objectID": "1", "url": "https://example.com"},
                {"title": "Valid", "objectID": "2"},
            ]
        }

        items = hackernews.parse_hn_response(response)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Valid")

    def test_hn_url_fallback(self):
        """Items without external URL use HN discussion URL."""
        response = {
            "hits": [
                {
                    "title": "Ask HN: Something",
                    "objectID": "99999",
                    "author": "asker",
                    "points": 50,
                    "num_comments": 30,
                }
            ]
        }

        items = hackernews.parse_hn_response(response)

        self.assertEqual(items[0]["url"], "https://news.ycombinator.com/item?id=99999")


class TestNormalizeHNItems(unittest.TestCase):
    def test_normalizes_basic_item(self):
        items = [
            {
                "id": "HN1",
                "title": "Test Story",
                "url": "https://example.com",
                "hn_url": "https://news.ycombinator.com/item?id=123",
                "author": "testuser",
                "date": "2026-01-15",
                "engagement": {"points": 100, "num_comments": 50},
                "relevance": 0.85,
                "why_relevant": "Relevant story",
            }
        ]

        result = normalize.normalize_hn_items(items, "2026-01-01", "2026-01-31")

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], schema.HNItem)
        self.assertEqual(result[0].id, "HN1")
        self.assertEqual(result[0].title, "Test Story")
        self.assertEqual(result[0].date_confidence, "high")
        self.assertEqual(result[0].engagement.points, 100)


class TestScoreHNItems(unittest.TestCase):
    def test_scores_items(self):
        today = datetime.now(timezone.utc).date().isoformat()
        items = [
            schema.HNItem(
                id="HN1",
                title="High engagement",
                url="https://example.com",
                hn_url="https://news.ycombinator.com/item?id=1",
                author="user1",
                date=today,
                date_confidence="high",
                engagement=schema.Engagement(points=500, num_comments=200),
                relevance=0.9,
            ),
            schema.HNItem(
                id="HN2",
                title="Low engagement",
                url="https://example.com/2",
                hn_url="https://news.ycombinator.com/item?id=2",
                author="user2",
                date=today,
                date_confidence="high",
                engagement=schema.Engagement(points=10, num_comments=3),
                relevance=0.5,
            ),
        ]

        result = score.score_hn_items(items)

        self.assertEqual(len(result), 2)
        self.assertGreater(result[0].score, 0)
        self.assertGreater(result[1].score, 0)
        # Higher relevance and engagement should score higher
        self.assertGreater(result[0].score, result[1].score)

    def test_empty_list(self):
        result = score.score_hn_items([])
        self.assertEqual(result, [])


class TestComputeHNEngagementRaw(unittest.TestCase):
    def test_with_engagement(self):
        eng = schema.Engagement(points=100, num_comments=50)
        result = score.compute_hn_engagement_raw(eng)
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_without_engagement(self):
        result = score.compute_hn_engagement_raw(None)
        self.assertIsNone(result)


class TestDedupeHN(unittest.TestCase):
    def test_dedupes_similar_titles(self):
        items = [
            schema.HNItem(
                id="HN1", title="Best practices for machine learning",
                url="", hn_url="", author="", score=90,
            ),
            schema.HNItem(
                id="HN2", title="Best practices for machine learning guide",
                url="", hn_url="", author="", score=50,
            ),
        ]
        result = dedupe.dedupe_hn(items, threshold=0.6)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, "HN1")

    def test_keeps_unique(self):
        items = [
            schema.HNItem(
                id="HN1", title="Python web frameworks",
                url="", hn_url="", author="", score=90,
            ),
            schema.HNItem(
                id="HN2", title="Rust memory safety",
                url="", hn_url="", author="", score=50,
            ),
        ]
        result = dedupe.dedupe_hn(items)
        self.assertEqual(len(result), 2)


class TestEstimateRelevance(unittest.TestCase):
    def test_first_result_high_relevance(self):
        hit = {"points": 100, "num_comments": 50}
        result = hackernews._estimate_relevance(hit, 0)
        self.assertGreaterEqual(result, 0.9)

    def test_later_results_lower_relevance(self):
        hit = {"points": 5, "num_comments": 1}
        result = hackernews._estimate_relevance(hit, 20)
        self.assertLess(result, 0.7)

    def test_high_engagement_boost(self):
        hit = {"points": 600, "num_comments": 300}
        result = hackernews._estimate_relevance(hit, 10)
        # Should get engagement boost
        self.assertGreater(result, 0.8)


if __name__ == "__main__":
    unittest.main()
