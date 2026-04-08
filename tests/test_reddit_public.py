"""Tests for scripts/lib/reddit_public.py — standalone Reddit public JSON search."""

import json
import urllib.error
from unittest import mock

import pytest
import sys
import os

# Ensure lib is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from lib import reddit_public


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_reddit_listing(posts):
    """Build a Reddit listing JSON structure from a list of post dicts."""
    children = []
    for p in posts:
        children.append({
            "kind": "t3",
            "data": {
                "title": p.get("title", "Test Post"),
                "permalink": p.get("permalink", "/r/test/comments/abc123/test_post/"),
                "subreddit": p.get("subreddit", "test"),
                "score": p.get("score", 42),
                "num_comments": p.get("num_comments", 10),
                "created_utc": p.get("created_utc", 1711670400),  # 2024-03-29
                "author": p.get("author", "testuser"),
                "selftext": p.get("selftext", "Some body text"),
                "upvote_ratio": p.get("upvote_ratio", 0.95),
            },
        })
    return {"data": {"children": children}}


SAMPLE_LISTING = _make_reddit_listing([
    {
        "title": "Claude Code is amazing",
        "permalink": "/r/ClaudeAI/comments/abc123/claude_code_is_amazing/",
        "subreddit": "ClaudeAI",
        "score": 250,
        "num_comments": 45,
        "created_utc": 1711670400,
        "author": "ai_fan",
        "selftext": "I've been using Claude Code for a week and it changed my workflow.",
    },
    {
        "title": "Tips for Claude Code prompting",
        "permalink": "/r/ClaudeAI/comments/def456/tips_for_claude_code/",
        "subreddit": "ClaudeAI",
        "score": 120,
        "num_comments": 22,
        "created_utc": 1711584000,
        "author": "prompt_engineer",
        "selftext": "Here are my top tips for getting the most out of Claude Code.",
    },
])


def _mock_urlopen_ok(listing_data):
    """Return a context-manager mock for urllib.request.urlopen that returns listing_data."""
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(listing_data).encode("utf-8")
    resp.headers = {"Content-Type": "application/json"}
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSearchReturnsCorrectFields:
    """Search query returns parsed results with correct fields."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_search_returns_parsed_results(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        results = reddit_public.search("Claude Code", depth="quick")

        assert len(results) == 2
        first = results[0]

        # Check all required fields exist
        assert "id" in first
        assert "title" in first
        assert "url" in first
        assert "score" in first
        assert "num_comments" in first
        assert "subreddit" in first
        assert "created_utc" in first
        assert "author" in first
        assert "selftext" in first

        # Check values
        assert first["title"] == "Claude Code is amazing"
        assert first["subreddit"] == "ClaudeAI"
        assert first["score"] == 250
        assert first["num_comments"] == 45
        assert first["author"] == "ai_fan"
        assert "/comments/" in first["url"]
        assert first["id"] == "R1"

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_search_includes_normalized_fields(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        results = reddit_public.search("Claude Code")
        first = results[0]

        # Normalized fields matching ScrapeCreators format
        assert "date" in first
        assert "engagement" in first
        assert "relevance" in first
        assert "why_relevant" in first

        assert isinstance(first["engagement"], dict)
        assert "score" in first["engagement"]
        assert "num_comments" in first["engagement"]


class TestSubredditScopedSearch:
    """Subreddit-scoped search works."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_subreddit_search_builds_correct_url(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        reddit_public.search("Claude Code", subreddit="ClaudeAI")

        # Check the URL passed to urlopen
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # First positional arg is the Request object
        assert "/r/ClaudeAI/search.json" in req.full_url
        assert "restrict_sr=on" in req.full_url

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_subreddit_search_strips_prefix(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        reddit_public.search("Claude Code", subreddit="r/ClaudeAI")

        req = mock_urlopen.call_args[0][0]
        # Should strip the r/ prefix, not double it
        assert "/r/ClaudeAI/search.json" in req.full_url
        assert "/r/r/" not in req.full_url


class TestRetryOn429:
    """429 response triggers retries, eventually returns partial results."""

    @mock.patch("lib.reddit_public.time.sleep")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_429_retries_then_returns_empty(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            "https://reddit.com/search.json", 429, "Too Many Requests",
            {"Retry-After": "1"}, None,
        )
        mock_urlopen.side_effect = error

        results = reddit_public.search("test query")

        assert results == []
        # Should have retried (MAX_RETRIES = 3, sleeps happen between attempts)
        assert mock_sleep.call_count == reddit_public.MAX_RETRIES - 1

    @mock.patch("lib.reddit_public.time.sleep")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_429_then_success(self, mock_urlopen, mock_sleep):
        error = urllib.error.HTTPError(
            "https://reddit.com/search.json", 429, "Too Many Requests",
            {}, None,
        )
        success_resp = _mock_urlopen_ok(SAMPLE_LISTING)

        mock_urlopen.side_effect = [error, success_resp]

        results = reddit_public.search("test query")

        assert len(results) == 2
        assert mock_sleep.call_count == 1


class TestHtmlAntiBot:
    """HTML response (anti-bot) is detected, returns empty."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_html_response_returns_empty(self, mock_urlopen):
        resp = mock.MagicMock()
        resp.read.return_value = b"<html><body>Please verify you are human</body></html>"
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        results = reddit_public.search("test query")

        assert results == []


class TestNetworkTimeout:
    """Network timeout returns empty."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_timeout_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("Connection timed out")

        results = reddit_public.search("test query")

        assert results == []

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_url_error_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        results = reddit_public.search("test query")

        assert results == []


class TestNormalizationMatchesScrapeCreators:
    """Results normalize to same schema as ScrapeCreators (field names match)."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_field_names_match_scrapecreators(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        results = reddit_public.search("Claude Code")
        assert len(results) > 0
        item = results[0]

        # These fields must exist to match ScrapeCreators _normalize_post output
        sc_required_fields = {"id", "title", "url", "subreddit", "date",
                              "engagement", "relevance", "why_relevant"}
        actual_fields = set(item.keys())
        assert sc_required_fields.issubset(actual_fields), (
            f"Missing fields: {sc_required_fields - actual_fields}"
        )

        # Engagement sub-fields
        eng = item["engagement"]
        assert "score" in eng
        assert "num_comments" in eng
        assert "upvote_ratio" in eng


class TestDepthLimits:
    """Depth-aware limits are respected."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_quick_limit(self, mock_urlopen):
        # Create more posts than the quick limit
        many_posts = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/"}
            for i in range(20)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(many_posts)

        results = reddit_public.search("test", depth="quick")
        assert len(results) <= 10

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_default_limit(self, mock_urlopen):
        many_posts = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/"}
            for i in range(50)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(many_posts)

        results = reddit_public.search("test", depth="default")
        assert len(results) <= 25


class TestSearchRedditPublicHighLevel:
    """Test the high-level search_reddit_public interface."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_date_filtering(self, mock_urlopen):
        listing = _make_reddit_listing([
            {
                "title": "In range",
                "permalink": "/r/test/comments/aaa/in_range/",
                "created_utc": 1711670400,  # 2024-03-29
            },
            {
                "title": "Out of range",
                "permalink": "/r/test/comments/bbb/out_of_range/",
                "created_utc": 1609459200,  # 2021-01-01
            },
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(listing)

        results = reddit_public.search_reddit_public(
            "test", "2024-03-01", "2024-03-31"
        )

        titles = [r["title"] for r in results]
        assert "In range" in titles
        assert "Out of range" not in titles

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_user_agent_header(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_ok(SAMPLE_LISTING)

        reddit_public.search("test")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("User-agent") == "last30days/3.0 (research tool)"


class TestMissingSubreddit:
    """Subreddit doesn't exist returns empty."""

    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_404_subreddit_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://reddit.com/r/nonexistent/search.json",
            404, "Not Found", {}, None,
        )

        results = reddit_public.search("test", subreddit="nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# Tests for comment enrichment (Unit 2)
# ---------------------------------------------------------------------------

class TestEnrichmentIntegration:
    """search_reddit_public enriches top posts with comments."""

    @mock.patch("lib.reddit_public._enrich_post")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_search_enriches_top_5_by_default(self, mock_urlopen, mock_enrich):
        listing = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/",
             "score": 100 - i, "created_utc": 1711670400}
            for i in range(10)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(listing)
        mock_enrich.side_effect = lambda item, timeout=10: item  # pass-through

        results = reddit_public.search_reddit_public("test", "2024-03-01", "2024-03-31")

        assert len(results) == 10
        # Default depth enriches top 5
        assert mock_enrich.call_count == 5

    @mock.patch("lib.reddit_public._enrich_post")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_enrichment_timeout_keeps_posts(self, mock_urlopen, mock_enrich):
        listing = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/",
             "score": 100 - i, "created_utc": 1711670400}
            for i in range(10)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(listing)

        # Some enrichments raise, some succeed
        call_count = {"n": 0}
        def _side_effect(item, timeout=10):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                raise TimeoutError("enrichment timed out")
            return item
        mock_enrich.side_effect = _side_effect

        results = reddit_public.search_reddit_public("test", "2024-03-01", "2024-03-31")

        # All 10 posts should still be returned
        assert len(results) == 10

    @mock.patch("lib.reddit_public._enrich_post")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_all_enrichment_fails_all_posts_returned(self, mock_urlopen, mock_enrich):
        listing = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/",
             "score": 100 - i, "created_utc": 1711670400}
            for i in range(10)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(listing)
        mock_enrich.side_effect = Exception("total failure")

        results = reddit_public.search_reddit_public("test", "2024-03-01", "2024-03-31")

        # All posts returned despite enrichment failure
        assert len(results) == 10

    @mock.patch("lib.reddit_public._enrich_post")
    @mock.patch("lib.reddit_public.urllib.request.urlopen")
    def test_quick_depth_enriches_top_3(self, mock_urlopen, mock_enrich):
        listing = _make_reddit_listing([
            {"title": f"Post {i}", "permalink": f"/r/test/comments/{i:06d}/post_{i}/",
             "score": 100 - i, "created_utc": 1711670400}
            for i in range(10)
        ])
        mock_urlopen.return_value = _mock_urlopen_ok(listing)
        mock_enrich.side_effect = lambda item, timeout=10: item

        results = reddit_public.search_reddit_public("test", "2024-03-01", "2024-03-31", depth="quick")

        assert len(results) == 10
        # Quick depth enriches only top 3
        assert mock_enrich.call_count == 3
