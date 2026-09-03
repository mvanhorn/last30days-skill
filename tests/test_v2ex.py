"""Tests for the v2ex module."""

import unittest
from unittest.mock import patch

from lib import v2ex


class TestTsToDate(unittest.TestCase):
    def test_valid_timestamp(self):
        # 2024-06-15T00:00:00Z
        self.assertEqual(v2ex._ts_to_date(1718409600), "2024-06-15")

    def test_none(self):
        self.assertIsNone(v2ex._ts_to_date(None))

    def test_invalid(self):
        self.assertIsNone(v2ex._ts_to_date("not-a-number"))

    def test_zero(self):
        self.assertIsNone(v2ex._ts_to_date(0))


class TestInWindow(unittest.TestCase):
    def test_within(self):
        self.assertTrue(v2ex._in_window("2024-06-15", "2024-06-01", "2024-06-30"))

    def test_before_from(self):
        self.assertFalse(v2ex._in_window("2024-05-15", "2024-06-01", "2024-06-30"))

    def test_after_to(self):
        self.assertFalse(v2ex._in_window("2024-07-15", "2024-06-01", "2024-06-30"))

    def test_unknown_date_kept(self):
        self.assertTrue(v2ex._in_window(None, "2024-06-01", "2024-06-30"))


class TestTopicOverlap(unittest.TestCase):
    def test_direct_match(self):
        score = v2ex._topic_overlap("python", "How do I learn Python quickly")
        self.assertGreater(score, 0)

    def test_no_match(self):
        score = v2ex._topic_overlap("baking bread", "A guide to Vim plugins")
        self.assertEqual(score, 0.0)


class TestNormalizeTopic(unittest.TestCase):
    def test_basic(self):
        raw = {
            "id": 123,
            "title": "Hello V2EX",
            "content": "Some body <b>text</b>",
            "created": 1718409600,
            "replies": 5,
            "url": "https://www.v2ex.com/t/123",
            "node": {"name": "tech", "title": "技术"},
            "member": {"username": "tester"},
        }
        item = v2ex._normalize_topic(raw)
        self.assertEqual(item["title"], "Hello V2EX")
        self.assertEqual(item["date"], "2024-06-15")
        self.assertEqual(item["engagement"], {"replies": 5})
        self.assertEqual(item["node_name"], "tech")
        self.assertEqual(item["author"], "tester")


class TestSearchV2ex(unittest.TestCase):
    @patch("lib.v2ex._fetch_hot_topics")
    @patch("lib.v2ex._fetch_node_topics")
    def test_relevant_only(self, mock_node, mock_hot):
        mock_hot.return_value = [
            {
                "id": 1,
                "title": "Best practices for Python packaging",
                "content": "pyproject discussion",
                "created": 1718409600,
                "replies": 3,
                "url": "https://www.v2ex.com/t/1",
                "node": {"name": "python", "title": "Python"},
                "member": {"username": "a"},
            },
            {
                "id": 2,
                "title": "My cat is sleeping",
                "content": "cute photo",
                "created": 1718409600,
                "replies": 1,
                "url": "https://www.v2ex.com/t/2",
                "node": {"name": "python", "title": "Python"},
                "member": {"username": "b"},
            },
        ]
        mock_node.return_value = []

        result = v2ex.search_v2ex("python", "2024-06-01", "2024-06-30", depth="quick")
        results = result["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "1")

    @patch("lib.v2ex._fetch_hot_topics")
    @patch("lib.v2ex._fetch_node_topics")
    def test_date_filter_applies(self, mock_node, mock_hot):
        mock_hot.return_value = [
            {
                "id": 1,
                "title": "Python tips",
                "content": "python",
                "created": 1717200000,  # 2024-06-01
                "replies": 0,
                "url": "https://www.v2ex.com/t/1",
                "node": {"name": "python", "title": "Python"},
                "member": {"username": "a"},
            },
            {
                "id": 2,
                "title": "Python tips 2",
                "content": "python",
                "created": 1720000000,  # 2024-07-03 (after to_date)
                "replies": 0,
                "url": "https://www.v2ex.com/t/2",
                "node": {"name": "python", "title": "Python"},
                "member": {"username": "a"},
            },
        ]
        mock_node.return_value = []

        result = v2ex.search_v2ex("python", "2024-06-01", "2024-06-30", depth="quick")
        results = result["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "1")

    @patch("lib.v2ex._fetch_hot_topics")
    @patch("lib.v2ex._fetch_node_topics")
    def test_error_envelope(self, mock_node, mock_hot):
        mock_hot.side_effect = v2ex.http.HTTPError("boom")
        mock_node.return_value = []
        result = v2ex.search_v2ex("python", "2024-06-01", "2024-06-30")
        self.assertEqual(result["results"], [])
        self.assertIn("boom", result["error"])

    def test_empty_topic(self):
        result = v2ex.search_v2ex("", "2024-06-01", "2024-06-30")
        self.assertEqual(result, {"results": []})


class TestParseV2exResponse(unittest.TestCase):
    def test_parses_items(self):
        result = {
            "results": [
                {
                    "id": "1",
                    "title": "T",
                    "url": "https://www.v2ex.com/t/1",
                    "author": "a",
                    "snippet": "s",
                    "body": "b",
                    "date": "2024-06-15",
                    "date_confidence": "high",
                    "relevance": 0.8,
                    "why_relevant": "overlap",
                    "engagement": {"replies": 3},
                    "node_name": "tech",
                    "node_title": "技术",
                }
            ]
        }
        items = v2ex.parse_v2ex_response(result)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["container"], "技术")
        self.assertEqual(items[0]["engagement"]["replies"], 3)

    def test_empty(self):
        self.assertEqual(v2ex.parse_v2ex_response({"results": []}), [])
        self.assertEqual(v2ex.parse_v2ex_response(None), [])


if __name__ == "__main__":
    unittest.main()
