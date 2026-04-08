"""Tests for Exa Search module."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Ensure scripts/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

# Force clean config mode for tests
os.environ['LAST30DAYS_CONFIG_DIR'] = ''

from lib.exa_search import search_web, _normalize_results, _parse_exa_date, EXCLUDED_DOMAINS
from lib import http


class TestNormalizeResults(unittest.TestCase):
    """Test result normalization from Exa API responses."""

    def test_valid_results(self):
        response = {
            "results": [
                {
                    "title": "AI Trends in 2026",
                    "url": "https://blog.example.com/ai-trends",
                    "text": "This article covers the latest AI trends...",
                    "publishedDate": "2026-03-15T00:00:00.000Z",
                    "score": 0.85,
                },
                {
                    "title": "Machine Learning Updates",
                    "url": "https://ml.example.com/updates",
                    "text": "New ML frameworks released this month...",
                    "publishedDate": "2026-03-10T12:30:00.000Z",
                    "score": 0.72,
                },
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(len(items), 2)

        # Check first item structure
        item = items[0]
        self.assertEqual(item["title"], "AI Trends in 2026")
        self.assertEqual(item["url"], "https://blog.example.com/ai-trends")
        self.assertIn("AI trends", item["snippet"])
        self.assertEqual(item["date"], "2026-03-15")
        self.assertEqual(item["date_confidence"], "med")
        self.assertAlmostEqual(item["relevance"], 0.85)
        self.assertEqual(item["id"], "W1")
        self.assertEqual(item["source_domain"], "blog.example.com")

    def test_excludes_reddit_and_x(self):
        response = {
            "results": [
                {"title": "Reddit post", "url": "https://www.reddit.com/r/test/123", "text": "Post"},
                {"title": "X post", "url": "https://x.com/user/status/456", "text": "Tweet"},
                {"title": "Good result", "url": "https://example.com/article", "text": "Content"},
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Good result")

    def test_empty_results(self):
        items = _normalize_results({"results": []})
        self.assertEqual(items, [])

    def test_missing_results_key(self):
        items = _normalize_results({})
        self.assertEqual(items, [])

    def test_skips_items_without_url(self):
        response = {
            "results": [
                {"title": "No URL", "text": "Content"},
                {"title": "Has URL", "url": "https://example.com/a", "text": "Content"},
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(len(items), 1)

    def test_skips_items_without_title_and_snippet(self):
        response = {
            "results": [
                {"url": "https://example.com/a", "title": "", "text": ""},
                {"url": "https://example.com/b", "title": "Valid", "text": "Content"},
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(len(items), 1)

    def test_no_date_gives_low_confidence(self):
        response = {
            "results": [
                {"title": "No date", "url": "https://example.com/a", "text": "Content"},
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(items[0]["date"], None)
        self.assertEqual(items[0]["date_confidence"], "low")

    def test_truncates_long_fields(self):
        response = {
            "results": [
                {
                    "title": "T" * 300,
                    "url": "https://example.com/a",
                    "text": "S" * 1000,
                },
            ]
        }
        items = _normalize_results(response)
        self.assertLessEqual(len(items[0]["title"]), 200)
        self.assertLessEqual(len(items[0]["snippet"]), 500)

    def test_relevance_clamped(self):
        response = {
            "results": [
                {"title": "High", "url": "https://example.com/a", "text": "C", "score": 1.5},
                {"title": "Low", "url": "https://example.com/b", "text": "C", "score": -0.5},
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(items[0]["relevance"], 1.0)
        self.assertEqual(items[1]["relevance"], 0.0)


class TestParseExaDate(unittest.TestCase):
    def test_iso_datetime(self):
        self.assertEqual(_parse_exa_date("2026-03-15T00:00:00.000Z"), "2026-03-15")

    def test_date_only(self):
        self.assertEqual(_parse_exa_date("2026-03-15"), "2026-03-15")

    def test_none(self):
        self.assertIsNone(_parse_exa_date(None))

    def test_empty_string(self):
        self.assertIsNone(_parse_exa_date(""))


class TestSearchWebIntegration(unittest.TestCase):
    """Test search_web function with mocked HTTP calls."""

    @patch("lib.exa_search.http.post")
    def test_valid_search(self, mock_post):
        mock_post.return_value = {
            "results": [
                {
                    "title": "Test Result",
                    "url": "https://example.com/test",
                    "text": "Test content here",
                    "publishedDate": "2026-03-20T00:00:00.000Z",
                    "score": 0.9,
                },
            ]
        }
        results = search_web("AI news", "2026-03-01", "2026-03-29", "test-api-key")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Test Result")
        self.assertEqual(results[0]["url"], "https://example.com/test")
        self.assertEqual(results[0]["snippet"], "Test content here")

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[0][0], "https://api.exa.ai/search")
        self.assertEqual(call_args[1]["headers"]["x-api-key"], "test-api-key")

    @patch("lib.exa_search.http.post")
    def test_401_invalid_key(self, mock_post):
        mock_post.side_effect = http.HTTPError("HTTP 401: Unauthorized", status_code=401)
        results = search_web("AI news", "2026-03-01", "2026-03-29", "bad-key")
        self.assertEqual(results, [])

    @patch("lib.exa_search.http.post")
    def test_429_rate_limit(self, mock_post):
        mock_post.side_effect = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
        results = search_web("AI news", "2026-03-01", "2026-03-29", "test-key")
        self.assertEqual(results, [])

    @patch("lib.exa_search.http.post")
    def test_empty_results(self, mock_post):
        mock_post.return_value = {"results": []}
        results = search_web("obscure query", "2026-03-01", "2026-03-29", "test-key")
        self.assertEqual(results, [])

    @patch("lib.exa_search.http.post")
    def test_network_timeout(self, mock_post):
        mock_post.side_effect = http.HTTPError("Connection error: TimeoutError: timed out")
        results = search_web("AI news", "2026-03-01", "2026-03-29", "test-key")
        self.assertEqual(results, [])

    @patch("lib.exa_search.http.post")
    def test_generic_exception(self, mock_post):
        mock_post.side_effect = Exception("Something unexpected")
        results = search_web("AI news", "2026-03-01", "2026-03-29", "test-key")
        self.assertEqual(results, [])


class TestExaNormalizationV3Keys(unittest.TestCase):
    """Test that Exa results include v3 normalization keys."""

    def test_results_include_engagement_and_metadata(self):
        response = {
            "results": [
                {
                    "title": "Test Article",
                    "url": "https://example.com/test",
                    "text": "Content here",
                    "publishedDate": "2026-03-15T00:00:00.000Z",
                    "score": 0.8,
                },
            ]
        }
        items = _normalize_results(response)
        self.assertEqual(len(items), 1)
        self.assertIn("engagement", items[0])
        self.assertIn("metadata", items[0])
        self.assertEqual(items[0]["engagement"], {})
        self.assertEqual(items[0]["metadata"], {})


if __name__ == "__main__":
    unittest.main()
