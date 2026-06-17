import unittest
from unittest.mock import patch

from lib.xquik import (
    DEPTH_CONFIG,
    _parse_tweet,
    _safe_int,
    expand_xquik_queries,
    parse_xquik_response,
    search_xquik,
)


class TestExpandXquikQueries(unittest.TestCase):
    def test_quick_returns_one_query(self):
        queries = expand_xquik_queries("latest trends in AI agents", "quick")
        self.assertEqual(len(queries), 1)

    def test_default_returns_up_to_two_queries(self):
        queries = expand_xquik_queries("multi-agent systems research", "default")
        self.assertLessEqual(len(queries), 2)
        self.assertGreaterEqual(len(queries), 1)

    def test_deep_returns_up_to_three_queries(self):
        queries = expand_xquik_queries("best AI coding assistants 2026", "deep")
        self.assertLessEqual(len(queries), 3)
        self.assertGreaterEqual(len(queries), 1)

    def test_single_word_topic(self):
        queries = expand_xquik_queries("Bitcoin", "quick")
        self.assertEqual(len(queries), 1)
        self.assertIn("bitcoin", queries[0].lower())


class TestParseTweet(unittest.TestCase):
    def test_valid_tweet(self):
        tweet = {
            "id": "123456",
            "text": "This is a test tweet about AI agents",
            "createdAt": "2026-03-15T12:00:00Z",
            "likeCount": 42,
            "retweetCount": 10,
            "replyCount": 5,
            "quoteCount": 2,
            "viewCount": 5000,
            "bookmarkCount": 8,
            "author": {"username": "testuser", "name": "Test User"},
        }
        item = _parse_tweet(tweet, 0, "AI agents")
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], "XQ1")
        self.assertEqual(item["url"], "https://x.com/testuser/status/123456")
        self.assertEqual(item["author_handle"], "testuser")
        self.assertEqual(item["date"], "2026-03-15")
        self.assertEqual(item["engagement"]["likes"], 42)
        self.assertEqual(item["engagement"]["reposts"], 10)
        self.assertEqual(item["engagement"]["replies"], 5)
        self.assertEqual(item["engagement"]["quotes"], 2)
        self.assertEqual(item["engagement"]["views"], 5000)
        self.assertEqual(item["engagement"]["bookmarks"], 8)
        self.assertGreater(item["relevance"], 0)

    def test_missing_author_returns_none(self):
        tweet = {"id": "123", "text": "test"}
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNone(item)

    def test_at_prefix_stripped(self):
        tweet = {
            "id": "456",
            "text": "hello",
            "author": {"username": "@someone"},
        }
        item = _parse_tweet(tweet, 0, "hello")
        self.assertIsNotNone(item)
        self.assertEqual(item["author_handle"], "someone")

    def test_zero_engagement_preserved(self):
        tweet = {
            "id": "789",
            "text": "zero likes tweet",
            "author": {"username": "user"},
            "likeCount": 0,
            "retweetCount": 0,
            "replyCount": 0,
            "quoteCount": 0,
            "viewCount": 0,
            "bookmarkCount": 0,
        }
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNotNone(item)
        self.assertEqual(item["engagement"]["likes"], 0)
        self.assertEqual(item["engagement"]["reposts"], 0)
        self.assertEqual(item["engagement"]["views"], 0)

    def test_none_engagement_values(self):
        tweet = {
            "id": "101",
            "text": "minimal tweet",
            "author": {"username": "user"},
        }
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNotNone(item)
        self.assertIsNone(item["engagement"]["likes"])
        self.assertIsNone(item["engagement"]["views"])

    def test_text_truncated_at_500(self):
        tweet = {
            "id": "102",
            "text": "x" * 600,
            "author": {"username": "user"},
        }
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNotNone(item)
        self.assertEqual(len(item["text"]), 500)

    def test_twitter_date_format(self):
        tweet = {
            "id": "103",
            "text": "old format",
            "createdAt": "Wed Jan 15 14:30:00 +0000 2026",
            "author": {"username": "user"},
        }
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNotNone(item)
        self.assertEqual(item["date"], "2026-01-15")

    def test_invalid_date_graceful(self):
        tweet = {
            "id": "104",
            "text": "bad date",
            "createdAt": "not-a-date",
            "author": {"username": "user"},
        }
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNotNone(item)
        self.assertIsNone(item["date"])

    def test_empty_author_dict(self):
        tweet = {"id": "105", "text": "test", "author": {}}
        item = _parse_tweet(tweet, 0, "test")
        self.assertIsNone(item)

    def test_index_offset(self):
        tweet = {
            "id": "106",
            "text": "test",
            "author": {"username": "user"},
        }
        item = _parse_tweet(tweet, 4, "test")
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], "XQ5")


class TestSafeInt(unittest.TestCase):
    def test_int_passthrough(self):
        self.assertEqual(_safe_int(42), 42)

    def test_string_int(self):
        self.assertEqual(_safe_int("100"), 100)

    def test_none_returns_none(self):
        self.assertIsNone(_safe_int(None))

    def test_invalid_string(self):
        self.assertIsNone(_safe_int("abc"))

    def test_zero(self):
        self.assertEqual(_safe_int(0), 0)

    def test_float_truncates(self):
        self.assertEqual(_safe_int(3.7), 3)


class TestParseXquikResponse(unittest.TestCase):
    def test_extracts_items(self):
        response = {"items": [{"id": "1"}, {"id": "2"}]}
        items = parse_xquik_response(response)
        self.assertEqual(len(items), 2)

    def test_empty_response(self):
        self.assertEqual(parse_xquik_response({}), [])

    def test_error_response(self):
        response = {"items": [], "error": "something went wrong"}
        self.assertEqual(parse_xquik_response(response), [])


class TestSearchXquik(unittest.TestCase):
    def test_no_token_returns_error(self):
        result = search_xquik("test", "2026-01-01", "2026-03-01", token="")
        self.assertEqual(result["items"], [])
        self.assertIn("XQUIK_API_KEY", result["error"])

    @patch("lib.xquik.http.get")
    def test_successful_search(self, mock_get):
        mock_get.return_value = {
            "tweets": [
                {
                    "id": "111",
                    "text": "AI agents are amazing",
                    "createdAt": "2026-02-15T10:00:00Z",
                    "likeCount": 50,
                    "retweetCount": 12,
                    "replyCount": 3,
                    "quoteCount": 1,
                    "viewCount": 2000,
                    "bookmarkCount": 5,
                    "author": {"username": "aidev"},
                },
            ],
            "has_next_page": False,
        }
        result = search_xquik("AI agents", "2026-02-01", "2026-03-01", token="test-key")
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["author_handle"], "aidev")
        self.assertEqual(result["items"][0]["engagement"]["likes"], 50)
        self.assertNotIn("error", result)

    @patch("lib.xquik.http.get")
    def test_deduplicates_across_queries(self, mock_get):
        tweet = {
            "id": "222",
            "text": "duplicate tweet",
            "author": {"username": "user"},
        }
        mock_get.return_value = {"tweets": [tweet]}
        result = search_xquik("test topic", "2026-01-01", "2026-03-01", depth="default", token="key")
        # Even with multiple queries, same tweet ID should appear only once
        ids = [item.get("id") for item in result["items"]]
        # All items should have unique XQ ids (deduped by tweet ID)
        self.assertEqual(len(ids), len(set(ids)))

    @patch("lib.xquik.http.get")
    def test_auth_error_returns_error(self, mock_get):
        from lib import http as http_mod
        mock_get.side_effect = http_mod.HTTPError("Unauthorized", status_code=401)
        result = search_xquik("test", "2026-01-01", "2026-03-01", token="bad-key")
        self.assertEqual(result["items"], [])
        self.assertIn("auth failed", result.get("error", ""))

    @patch("lib.xquik.http.get")
    def test_empty_tweets_list(self, mock_get):
        mock_get.return_value = {"tweets": []}
        result = search_xquik("obscure topic", "2026-01-01", "2026-03-01", token="key")
        self.assertEqual(result["items"], [])
        self.assertNotIn("error", result)

    @patch("lib.xquik.http.get")
    def test_non_list_tweets_skipped(self, mock_get):
        mock_get.return_value = {"tweets": "not a list"}
        result = search_xquik("test", "2026-01-01", "2026-03-01", token="key")
        self.assertEqual(result["items"], [])


class TestDepthConfig(unittest.TestCase):
    def test_all_depths_have_limit_and_queries(self):
        for depth_name, cfg in DEPTH_CONFIG.items():
            self.assertIn("limit", cfg, f"{depth_name} missing 'limit'")
            self.assertIn("queries", cfg, f"{depth_name} missing 'queries'")

    def test_deep_has_highest_limit(self):
        self.assertGreater(DEPTH_CONFIG["deep"]["limit"], DEPTH_CONFIG["default"]["limit"])
        self.assertGreater(DEPTH_CONFIG["default"]["limit"], DEPTH_CONFIG["quick"]["limit"])

    def test_deep_has_most_queries(self):
        self.assertGreater(DEPTH_CONFIG["deep"]["queries"], DEPTH_CONFIG["quick"]["queries"])

if __name__ == "__main__":
    unittest.main()
