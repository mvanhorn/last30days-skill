"""Tests for Truth Social source module."""
import unittest
from unittest.mock import patch, MagicMock

from lib import truthsocial


class TestStripHtml(unittest.TestCase):
    """Test HTML tag stripping."""

    def test_basic_paragraph(self):
        self.assertEqual(truthsocial._strip_html("<p>Hello world</p>"), "Hello world")

    def test_br_tags(self):
        self.assertEqual(truthsocial._strip_html("Line 1<br>Line 2"), "Line 1\nLine 2")
        self.assertEqual(truthsocial._strip_html("Line 1<br/>Line 2"), "Line 1\nLine 2")
        self.assertEqual(truthsocial._strip_html("Line 1<br />Line 2"), "Line 1\nLine 2")

    def test_nested_tags(self):
        self.assertEqual(truthsocial._strip_html("<p>Hello <a href='#'>world</a></p>"), "Hello world")

    def test_empty_string(self):
        self.assertEqual(truthsocial._strip_html(""), "")

    def test_no_tags(self):
        self.assertEqual(truthsocial._strip_html("plain text"), "plain text")

    def test_entities_preserved(self):
        self.assertEqual(truthsocial._strip_html("<p>&amp; test</p>"), "&amp; test")


class TestExtractCoreSubject(unittest.TestCase):
    """Test query preprocessing."""

    def test_strips_question_prefix(self):
        self.assertEqual(truthsocial._extract_core_subject("what are people saying about tariffs"), "tariffs")

    def test_strips_noise_words(self):
        self.assertEqual(truthsocial._extract_core_subject("latest trending crypto news"), "crypto")

    def test_preserves_core_topic(self):
        self.assertEqual(truthsocial._extract_core_subject("tariffs"), "tariffs")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(truthsocial._extract_core_subject("what is bitcoin?"), "bitcoin")


class TestParseDate(unittest.TestCase):
    """Test date parsing from Mastodon status."""

    def test_iso_date(self):
        self.assertEqual(truthsocial._parse_date({"created_at": "2026-03-09T12:00:00.000Z"}), "2026-03-09")

    def test_missing_date(self):
        self.assertIsNone(truthsocial._parse_date({}))

    def test_short_date(self):
        self.assertIsNone(truthsocial._parse_date({"created_at": "short"}))

    def test_none_value(self):
        self.assertIsNone(truthsocial._parse_date({"created_at": None}))


class TestDepthConfig(unittest.TestCase):
    """Test depth configuration."""

    def test_all_depths_exist(self):
        self.assertIn("quick", truthsocial.DEPTH_CONFIG)
        self.assertIn("default", truthsocial.DEPTH_CONFIG)
        self.assertIn("deep", truthsocial.DEPTH_CONFIG)

    def test_depth_ordering(self):
        self.assertLess(truthsocial.DEPTH_CONFIG["quick"], truthsocial.DEPTH_CONFIG["default"])
        self.assertLess(truthsocial.DEPTH_CONFIG["default"], truthsocial.DEPTH_CONFIG["deep"])


class TestSearchTruthSocial(unittest.TestCase):
    """Test search function auth handling."""

    def test_no_config_returns_error(self):
        result = truthsocial.search_truthsocial("test", "2026-02-09", "2026-03-09")
        self.assertEqual(result["statuses"], [])
        self.assertIn("not configured", result["error"])

    def test_empty_token_returns_error(self):
        result = truthsocial.search_truthsocial(
            "test", "2026-02-09", "2026-03-09",
            config={"TRUTHSOCIAL_TOKEN": ""},
        )
        self.assertEqual(result["statuses"], [])
        self.assertIn("not configured", result["error"])

    @patch("lib.truthsocial.http.request")
    def test_401_returns_token_expired(self, mock_request):
        from lib.http import HTTPError
        mock_request.side_effect = HTTPError("Unauthorized", status_code=401)
        result = truthsocial.search_truthsocial(
            "test", "2026-02-09", "2026-03-09",
            config={"TRUTHSOCIAL_TOKEN": "expired_token"},
        )
        self.assertEqual(result["statuses"], [])
        self.assertIn("expired", result["error"])

    @patch("lib.truthsocial.http.request")
    def test_403_returns_access_denied(self, mock_request):
        from lib.http import HTTPError
        mock_request.side_effect = HTTPError("Forbidden", status_code=403)
        result = truthsocial.search_truthsocial(
            "test", "2026-02-09", "2026-03-09",
            config={"TRUTHSOCIAL_TOKEN": "blocked_token"},
        )
        self.assertEqual(result["statuses"], [])
        self.assertIn("Cloudflare", result["error"])

    @patch("lib.truthsocial.http.request")
    def test_429_returns_rate_limited(self, mock_request):
        from lib.http import HTTPError
        mock_request.side_effect = HTTPError("Too Many Requests", status_code=429)
        result = truthsocial.search_truthsocial(
            "test", "2026-02-09", "2026-03-09",
            config={"TRUTHSOCIAL_TOKEN": "rate_limited_token"},
        )
        self.assertEqual(result["statuses"], [])
        self.assertIn("rate limited", result["error"])

    @patch("lib.truthsocial.http.request")
    def test_successful_search(self, mock_request):
        mock_request.return_value = {
            "statuses": [
                {
                    "content": "<p>Test post about tariffs</p>",
                    "created_at": "2026-03-09T12:00:00.000Z",
                    "url": "https://truthsocial.com/@user/123",
                    "account": {"acct": "user", "display_name": "Test User"},
                    "favourites_count": 10,
                    "reblogs_count": 5,
                    "replies_count": 3,
                }
            ]
        }
        result = truthsocial.search_truthsocial(
            "tariffs", "2026-02-09", "2026-03-09",
            config={"TRUTHSOCIAL_TOKEN": "valid_token"},
        )
        self.assertEqual(len(result["statuses"]), 1)
        # Verify bearer token was passed
        call_args = mock_request.call_args
        self.assertEqual(call_args[1]["headers"]["Authorization"], "Bearer valid_token")


class TestParseTruthSocialResponse(unittest.TestCase):
    """Test response parsing."""

    def test_basic_post(self):
        response = {
            "statuses": [
                {
                    "content": "<p>Hello from Truth Social</p>",
                    "created_at": "2026-03-09T12:00:00.000Z",
                    "url": "https://truthsocial.com/@testuser/456",
                    "account": {"acct": "testuser", "display_name": "Test User"},
                    "favourites_count": 100,
                    "reblogs_count": 50,
                    "replies_count": 25,
                }
            ]
        }
        items = truthsocial.parse_truthsocial_response(response)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["handle"], "testuser")
        self.assertEqual(item["display_name"], "Test User")
        self.assertEqual(item["text"], "Hello from Truth Social")
        self.assertEqual(item["url"], "https://truthsocial.com/@testuser/456")
        self.assertEqual(item["date"], "2026-03-09")
        self.assertEqual(item["engagement"]["likes"], 100)
        self.assertEqual(item["engagement"]["reposts"], 50)
        self.assertEqual(item["engagement"]["replies"], 25)
        self.assertGreater(item["relevance"], 0)

    def test_empty_response(self):
        items = truthsocial.parse_truthsocial_response({"statuses": []})
        self.assertEqual(items, [])

    def test_missing_fields(self):
        response = {
            "statuses": [
                {
                    "content": "",
                    "account": {},
                }
            ]
        }
        items = truthsocial.parse_truthsocial_response(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["handle"], "")
        self.assertEqual(items[0]["text"], "")
        self.assertEqual(items[0]["engagement"]["likes"], 0)

    def test_relevance_ordering(self):
        response = {
            "statuses": [
                {"content": "<p>First</p>", "account": {"acct": "a"}, "favourites_count": 10, "reblogs_count": 0, "replies_count": 0},
                {"content": "<p>Second</p>", "account": {"acct": "b"}, "favourites_count": 5, "reblogs_count": 0, "replies_count": 0},
                {"content": "<p>Third</p>", "account": {"acct": "c"}, "favourites_count": 1, "reblogs_count": 0, "replies_count": 0},
            ]
        }
        items = truthsocial.parse_truthsocial_response(response)
        self.assertGreaterEqual(items[0]["relevance"], items[1]["relevance"])
        self.assertGreaterEqual(items[1]["relevance"], items[2]["relevance"])

    def test_html_stripping_in_parse(self):
        response = {
            "statuses": [
                {
                    "content": "<p>Hello <a href='https://example.com'>@user</a> check this out<br/>New line</p>",
                    "account": {"acct": "poster"},
                    "favourites_count": 0,
                    "reblogs_count": 0,
                    "replies_count": 0,
                }
            ]
        }
        items = truthsocial.parse_truthsocial_response(response)
        self.assertNotIn("<", items[0]["text"])
        self.assertNotIn(">", items[0]["text"])

if __name__ == "__main__":
    unittest.main()
