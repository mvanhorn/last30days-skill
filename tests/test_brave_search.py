"""Tests for Brave Search module, including LLM Context endpoint."""

import sys
import os
import unittest

# Ensure scripts/ is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from lib.brave_search import (
    _normalize_results,
    _normalize_llm_context,
    _days_between,
    _brave_freshness,
    _parse_brave_date,
)


class TestDaysBetween(unittest.TestCase):
    def test_same_day(self):
        self.assertEqual(_days_between("2026-03-01", "2026-03-01"), 1)

    def test_one_week(self):
        self.assertEqual(_days_between("2026-03-01", "2026-03-08"), 7)

    def test_invalid_dates(self):
        self.assertEqual(_days_between("bad", "dates"), 30)


class TestBraveFreshness(unittest.TestCase):
    def test_one_day(self):
        self.assertEqual(_brave_freshness(1), "pd")

    def test_one_week(self):
        self.assertEqual(_brave_freshness(7), "pw")

    def test_one_month(self):
        self.assertEqual(_brave_freshness(31), "pm")

    def test_longer_returns_range(self):
        result = _brave_freshness(60)
        self.assertIn("to", result)

    def test_none(self):
        self.assertIsNone(_brave_freshness(None))


class TestParseBraveDate(unittest.TestCase):
    def test_hours_ago(self):
        result = _parse_brave_date("3 hours ago", None)
        self.assertIsNotNone(result)
        self.assertRegex(result, r"\d{4}-\d{2}-\d{2}")

    def test_days_ago(self):
        result = _parse_brave_date("5 days ago", None)
        self.assertIsNotNone(result)

    def test_weeks_ago(self):
        result = _parse_brave_date("2 weeks ago", None)
        self.assertIsNotNone(result)

    def test_iso_date(self):
        self.assertEqual(_parse_brave_date("2026-03-10T12:00:00", None), "2026-03-10")

    def test_none(self):
        self.assertIsNone(_parse_brave_date(None, None))


class TestNormalizeResults(unittest.TestCase):
    def test_merges_news_and_web(self):
        response = {
            "news": {"results": [
                {"url": "https://news.example.com/a", "title": "News A", "description": "News desc"},
            ]},
            "web": {"results": [
                {"url": "https://blog.example.com/b", "title": "Blog B", "description": "Blog desc"},
            ]},
        }
        items = _normalize_results(response, "2026-03-01", "2026-03-10")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "News A")
        self.assertEqual(items[1]["title"], "Blog B")

    def test_excludes_reddit_and_x(self):
        response = {
            "web": {"results": [
                {"url": "https://www.reddit.com/r/test/123", "title": "Reddit", "description": "text"},
                {"url": "https://x.com/user/status/1", "title": "X post", "description": "text"},
                {"url": "https://example.com/ok", "title": "OK", "description": "text"},
            ]},
        }
        items = _normalize_results(response, "2026-03-01", "2026-03-10")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "OK")

    def test_default_relevance(self):
        response = {"web": {"results": [
            {"url": "https://a.com", "title": "A", "description": "desc"},
        ]}}
        items = _normalize_results(response, "2026-03-01", "2026-03-10")
        self.assertEqual(items[0]["relevance"], 0.6)


class TestNormalizeLlmContext(unittest.TestCase):
    def _make_response(self, generic=None, sources=None):
        return {
            "grounding": {"generic": generic or []},
            "sources": sources or {},
        }

    def test_basic_result(self):
        resp = self._make_response(
            generic=[{
                "url": "https://docs.example.com/page",
                "title": "Example Page",
                "snippets": ["First chunk of text.", "Second chunk of text."],
            }],
            sources={
                "https://docs.example.com/page": {
                    "title": "Example Page",
                    "hostname": "docs.example.com",
                    "age": ["2026-03-05", "5 days ago"],
                }
            },
        )
        items = _normalize_llm_context(resp)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "Example Page")
        self.assertEqual(item["url"], "https://docs.example.com/page")
        self.assertIn("First chunk", item["snippet"])
        self.assertIn("Second chunk", item["snippet"])
        self.assertEqual(item["date"], "2026-03-05")
        self.assertEqual(item["date_confidence"], "med")
        self.assertEqual(item["relevance"], 0.7)
        self.assertEqual(item["source_domain"], "docs.example.com")

    def test_excludes_reddit(self):
        resp = self._make_response(
            generic=[
                {"url": "https://www.reddit.com/r/test", "title": "Reddit", "snippets": ["text"]},
                {"url": "https://example.com", "title": "OK", "snippets": ["text"]},
            ],
        )
        items = _normalize_llm_context(resp)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "OK")

    def test_empty_grounding(self):
        resp = self._make_response()
        items = _normalize_llm_context(resp)
        self.assertEqual(items, [])

    def test_snippet_truncation(self):
        long_snippet = "x" * 2000
        resp = self._make_response(
            generic=[{"url": "https://a.com", "title": "A", "snippets": [long_snippet]}],
        )
        items = _normalize_llm_context(resp)
        self.assertLessEqual(len(items[0]["snippet"]), 1500)

    def test_no_date_gives_low_confidence(self):
        resp = self._make_response(
            generic=[{"url": "https://a.com", "title": "A", "snippets": ["text"]}],
            sources={"https://a.com": {"hostname": "a.com", "age": None}},
        )
        items = _normalize_llm_context(resp)
        self.assertIsNone(items[0]["date"])
        self.assertEqual(items[0]["date_confidence"], "low")

    def test_multiple_age_entries_picks_first_valid(self):
        resp = self._make_response(
            generic=[{"url": "https://a.com", "title": "A", "snippets": ["text"]}],
            sources={"https://a.com": {
                "hostname": "a.com",
                "age": ["Monday, March 10, 2026", "2026-03-10", "1 day ago"],
            }},
        )
        items = _normalize_llm_context(resp)
        self.assertEqual(items[0]["date"], "2026-03-10")

    def test_ids_are_sequential(self):
        resp = self._make_response(
            generic=[
                {"url": "https://a.com", "title": "A", "snippets": ["a"]},
                {"url": "https://b.com", "title": "B", "snippets": ["b"]},
                {"url": "https://c.com", "title": "C", "snippets": ["c"]},
            ],
        )
        items = _normalize_llm_context(resp)
        ids = [item["id"] for item in items]
        self.assertEqual(ids, ["W1", "W2", "W3"])


if __name__ == "__main__":
    unittest.main()
