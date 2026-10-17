"""Tests for lib/trendshift.py — Trendshift trending repos source."""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "last30days", "scripts"))

import lib.trendshift as trendshift


_SAMPLE_REPO = {
    "full_name": "owner/ai-agent-framework",
    "description": "An open-source AI agent framework for building intelligent assistants",
    "url": "https://github.com/owner/ai-agent-framework",
    "stargazers_count": 4200,
    "forks_count": 310,
    "language": "Python",
    "pushed_at": "2026-09-15T10:00:00Z",
}

_UNRELATED_REPO = {
    "full_name": "owner/cookiecutter",
    "description": "A command-line utility for creating project templates",
    "url": "https://github.com/owner/cookiecutter",
    "stargazers_count": 1800,
    "forks_count": 120,
    "language": "Python",
    "pushed_at": "2026-08-01T10:00:00Z",
}


class TestParseRepo(unittest.TestCase):
    def test_full_name_becomes_title_and_url(self):
        item = trendshift._parse_repo(_SAMPLE_REPO, "ai agent")
        self.assertEqual(item["title"], "owner/ai-agent-framework")
        self.assertEqual(item["url"], "https://github.com/owner/ai-agent-framework")

    def test_description_becomes_text(self):
        item = trendshift._parse_repo(_SAMPLE_REPO, "ai agent")
        self.assertIn("AI agent", item["text"])

    def test_stars_in_engagement(self):
        item = trendshift._parse_repo(_SAMPLE_REPO, "ai agent")
        self.assertEqual(item["engagement"]["stars"], 4200)

    def test_date_extracted_from_pushed_at(self):
        item = trendshift._parse_repo(_SAMPLE_REPO, "ai agent")
        self.assertEqual(item["date"], "2026-09-15")

    def test_url_inferred_from_full_name_when_missing(self):
        raw = dict(_SAMPLE_REPO)
        del raw["url"]
        item = trendshift._parse_repo(raw, "ai agent")
        self.assertIn("github.com/owner/ai-agent-framework", item["url"])

    def test_source_is_trendshift(self):
        item = trendshift._parse_repo(_SAMPLE_REPO, "ai agent")
        self.assertEqual(item["source"], "trendshift")


class TestSearchTrendshift(unittest.TestCase):
    def test_returns_items_on_success(self):
        from lib import http as http_module
        payload = [_SAMPLE_REPO]
        with patch.object(http_module, "get", return_value=payload):
            result = trendshift.search_trendshift(
                "ai agent", "2026-09-01", "2026-09-30"
            )
        self.assertIn("items", result)
        self.assertTrue(len(result["items"]) > 0)

    def test_filters_out_irrelevant_repos(self):
        from lib import http as http_module
        payload = [_UNRELATED_REPO]
        with patch.object(http_module, "get", return_value=payload):
            result = trendshift.search_trendshift(
                "ai agent framework", "2026-09-01", "2026-09-30"
            )
        # cookiecutter has very low overlap with "ai agent framework"
        self.assertEqual(result["items"], [])

    def test_api_error_returns_error(self):
        from lib import http as http_module
        with patch.object(http_module, "get", side_effect=Exception("connection refused")):
            result = trendshift.search_trendshift(
                "ai agent", "2026-09-01", "2026-09-30"
            )
        self.assertEqual(result["items"], [])
        self.assertIn("error", result)

    def test_wrapped_response_key_repositories(self):
        from lib import http as http_module
        payload = {"repositories": [_SAMPLE_REPO]}
        with patch.object(http_module, "get", return_value=payload):
            result = trendshift.search_trendshift(
                "ai agent", "2026-09-01", "2026-09-30"
            )
        self.assertTrue(len(result["items"]) > 0)

    def test_items_sorted_by_stars_descending(self):
        from lib import http as http_module
        low_star = dict(_SAMPLE_REPO)
        low_star["stargazers_count"] = 100
        low_star["full_name"] = "owner/low-star-ai"
        high_star = dict(_SAMPLE_REPO)
        high_star["stargazers_count"] = 9000
        high_star["full_name"] = "owner/high-star-ai"
        payload = [low_star, high_star]
        with patch.object(http_module, "get", return_value=payload):
            result = trendshift.search_trendshift(
                "ai", "2026-09-01", "2026-09-30"
            )
        stars = [i["engagement"]["stars"] for i in result["items"]]
        self.assertEqual(stars, sorted(stars, reverse=True))


class TestAvailableSources(unittest.TestCase):
    """Trendshift appears in available_sources() only when opted in."""

    def _available(self, include_sources="", requested=None):
        from lib.pipeline import available_sources
        config = {
            "INCLUDE_SOURCES": include_sources,
            "CLAUDECODE": "1",
            "_CORPUS_DIRS": None,
            "LAST30DAYS_CORPUS_DIRS": None,
        }
        return available_sources(config, requested_sources=requested or [])

    def test_trendshift_absent_by_default(self):
        sources = self._available()
        self.assertNotIn("trendshift", sources)

    def test_trendshift_present_when_include_sources_set(self):
        sources = self._available(include_sources="trendshift")
        self.assertIn("trendshift", sources)

    def test_trendshift_present_when_requested_sources_set(self):
        sources = self._available(requested=["trendshift"])
        self.assertIn("trendshift", sources)
