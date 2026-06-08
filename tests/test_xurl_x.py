"""Tests for xurl_x module."""

import json
import unittest
from unittest import mock

from lib import xurl_x

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_api_response(tweets=None, users=None):
    """Build a minimal X API v2 search/recent response."""
    tweets = tweets or []
    users = users or []
    resp = {"data": tweets}
    if users:
        resp["includes"] = {"users": users}
    return resp

# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable(unittest.TestCase):
    def test_returns_true_when_xurl_authenticated(self):
        completed = mock.Mock(returncode=0, stdout='{"username": "testuser"}')
        with mock.patch("subprocess.run", return_value=completed):
            self.assertTrue(xurl_x.is_available())

    def test_returns_false_when_not_authenticated(self):
        completed = mock.Mock(returncode=1, stdout="")
        with mock.patch("subprocess.run", return_value=completed):
            self.assertFalse(xurl_x.is_available())

    def test_returns_false_when_not_installed(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(xurl_x.is_available())

    def test_returns_false_on_permission_error(self):
        # WSL hits this when a Windows-mounted PATH entry points at an
        # exec-blocked shim (e.g. WindowsApps), which raises PermissionError
        # before any other PATH candidate is tried.
        with mock.patch("subprocess.run", side_effect=PermissionError(13, "Permission denied", "xurl")):
            self.assertFalse(xurl_x.is_available())

    def test_returns_false_on_timeout(self):
        import subprocess
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xurl", 10)):
            self.assertFalse(xurl_x.is_available())

    def test_returns_false_when_no_username_in_output(self):
        # returncode=0 but output does not contain '"username"'
        completed = mock.Mock(returncode=0, stdout='{"id": "123"}')
        with mock.patch("subprocess.run", return_value=completed):
            self.assertFalse(xurl_x.is_available())

# ---------------------------------------------------------------------------
# search_x
# ---------------------------------------------------------------------------


class TestSearchX(unittest.TestCase):
    def test_returns_parsed_json_on_success(self):
        payload = {"data": [{"id": "1", "text": "hello world", "author_id": "u1"}]}
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload))
        with mock.patch("subprocess.run", return_value=completed):
            result = xurl_x.search_x("hello world")
        self.assertEqual(result["data"][0]["id"], "1")

    def test_returns_error_on_non_zero_exit(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="rate limit exceeded")
        with mock.patch("subprocess.run", return_value=completed):
            result = xurl_x.search_x("test")
        self.assertIn("error", result)
        self.assertIn("rate limit exceeded", result["error"])

    def test_returns_error_on_invalid_json(self):
        completed = mock.Mock(returncode=0, stdout="NOT JSON")
        with mock.patch("subprocess.run", return_value=completed):
            result = xurl_x.search_x("test")
        self.assertIn("error", result)
        self.assertIn("Invalid JSON", result["error"])

    def test_returns_error_when_not_installed(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            result = xurl_x.search_x("test")
        self.assertIn("error", result)
        self.assertIn("not found", result["error"])

    def test_returns_error_on_timeout(self):
        import subprocess
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("xurl", 30)):
            result = xurl_x.search_x("test")
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])

    def test_max_results_clamped_to_100(self):
        # DEPTH_CONFIG["deep"] = 60, should stay at 60 (within 10-100 range)
        completed = mock.Mock(returncode=0, stdout=json.dumps({}))
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            xurl_x.search_x("test", depth="deep")
        call_args = run_mock.call_args[0][0]
        n_idx = call_args.index("-n")
        self.assertLessEqual(int(call_args[n_idx + 1]), 100)

    def test_max_results_at_least_10(self):
        completed = mock.Mock(returncode=0, stdout=json.dumps({}))
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            xurl_x.search_x("test", depth="quick")
        call_args = run_mock.call_args[0][0]
        n_idx = call_args.index("-n")
        self.assertGreaterEqual(int(call_args[n_idx + 1]), 10)

    def test_unknown_depth_falls_back_to_default(self):
        completed = mock.Mock(returncode=0, stdout=json.dumps({}))
        with mock.patch("subprocess.run", return_value=completed) as run_mock:
            xurl_x.search_x("test", depth="nonexistent")
        call_args = run_mock.call_args[0][0]
        n_idx = call_args.index("-n")
        self.assertEqual(int(call_args[n_idx + 1]), xurl_x.DEPTH_CONFIG["default"])

# ---------------------------------------------------------------------------
# parse_x_response
# ---------------------------------------------------------------------------


class TestParseXResponse(unittest.TestCase):
    def _tweet(self, id_, text, author_id, created_at=None, metrics=None):
        t = {"id": id_, "text": text, "author_id": author_id}
        if created_at:
            t["created_at"] = created_at
        if metrics:
            t["public_metrics"] = metrics
        return t

    def _user(self, id_, username):
        return {"id": id_, "username": username}

    def test_empty_response_returns_empty_list(self):
        self.assertEqual(xurl_x.parse_x_response({}), [])

    def test_error_response_returns_empty_list(self):
        self.assertEqual(xurl_x.parse_x_response({"error": "oops"}), [])

    def test_parses_basic_tweet(self):
        resp = _make_api_response(
            tweets=[self._tweet("111", "Hello AI", "u1")],
            users=[self._user("u1", "alice")],
        )
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "Hello AI")
        self.assertEqual(items[0]["author_handle"], "alice")
        self.assertIn("alice", items[0]["url"])
        self.assertIn("111", items[0]["url"])

    def test_parses_date_from_iso(self):
        resp = _make_api_response(
            tweets=[self._tweet("1", "text", "u1", created_at="2024-06-15T12:00:00Z")],
        )
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(items[0]["date"], "2024-06-15")

    def test_date_none_when_missing(self):
        resp = _make_api_response(tweets=[self._tweet("1", "text", "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertIsNone(items[0]["date"])

    def test_parses_engagement_metrics(self):
        metrics = {
            "like_count": 42,
            "retweet_count": 10,
            "reply_count": 5,
            "quote_count": 2,
        }
        resp = _make_api_response(
            tweets=[self._tweet("1", "text", "u1", metrics=metrics)],
        )
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(items[0]["engagement"]["likes"], 42)
        self.assertEqual(items[0]["engagement"]["reposts"], 10)
        self.assertEqual(items[0]["engagement"]["replies"], 5)
        self.assertEqual(items[0]["engagement"]["quotes"], 2)

    def test_engagement_none_when_no_metrics(self):
        resp = _make_api_response(tweets=[self._tweet("1", "text", "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertIsNone(items[0]["engagement"])

    def test_text_truncated_to_500_chars(self):
        long_text = "x" * 600
        resp = _make_api_response(tweets=[self._tweet("1", long_text, "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertLessEqual(len(items[0]["text"]), 500)

    def test_id_prefixed_with_xurl(self):
        resp = _make_api_response(tweets=[self._tweet("1", "text", "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertTrue(items[0]["id"].startswith("XURL"))

    def test_relevance_computed_when_topic_given(self):
        resp = _make_api_response(
            tweets=[self._tweet("1", "Claude Code is great for AI coding", "u1")],
        )
        items = xurl_x.parse_x_response(resp, topic="Claude Code")
        self.assertGreater(items[0]["relevance"], 0.5)

    def test_relevance_neutral_when_no_topic(self):
        resp = _make_api_response(tweets=[self._tweet("1", "some text", "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(items[0]["relevance"], 0.5)

    def test_url_empty_when_no_username(self):
        # author_id not in includes.users → username=""
        resp = _make_api_response(tweets=[self._tweet("999", "text", "unknown_uid")])
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(items[0]["url"], "")

    def test_multiple_tweets_parsed(self):
        tweets = [self._tweet(str(i), f"tweet {i}", "u1") for i in range(5)]
        resp = _make_api_response(tweets=tweets, users=[self._user("u1", "bob")])
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(len(items), 5)

    def test_empty_data_list(self):
        resp = _make_api_response(tweets=[])
        self.assertEqual(xurl_x.parse_x_response(resp), [])

    def test_why_relevant_is_empty_string(self):
        # xurl doesn't provide LLM-generated why_relevant (unlike xai_x)
        resp = _make_api_response(tweets=[self._tweet("1", "text", "u1")])
        items = xurl_x.parse_x_response(resp)
        self.assertEqual(items[0]["why_relevant"], "")

# ---------------------------------------------------------------------------
# DEPTH_CONFIG
# ---------------------------------------------------------------------------


class TestDepthConfig(unittest.TestCase):
    def test_all_standard_depths_present(self):
        for depth in ("quick", "default", "deep"):
            self.assertIn(depth, xurl_x.DEPTH_CONFIG)

    def test_deep_greater_than_quick(self):
        self.assertGreater(
            xurl_x.DEPTH_CONFIG["deep"],
            xurl_x.DEPTH_CONFIG["quick"],
        )

if __name__ == "__main__":
    unittest.main()
