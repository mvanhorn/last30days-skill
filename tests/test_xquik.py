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
    def test_unpaid_402_surfaces_error_on_real_path(self, mock_get):
        # An unpaid key (402) must surface an error on the normal search path,
        # not settle silently empty — diagnose is opt-in.
        from lib import http as http_mod
        mock_get.side_effect = http_mod.HTTPError("Payment Required", status_code=402)
        result = search_xquik("test", "2026-01-01", "2026-03-01", token="unpaid-key")
        self.assertEqual(result["items"], [])
        self.assertIn("unpaid", result.get("error", "").lower())

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

class TestIsOwn(unittest.TestCase):
    def test_own_tweet_detected(self):
        from lib.xquik import _is_own
        self.assertTrue(_is_own("https://x.com/elonmusk/status/123", "elonmusk"))
        self.assertTrue(_is_own("https://twitter.com/elonmusk/status/123", "@elonmusk"))

    def test_other_author_not_own(self):
        from lib.xquik import _is_own
        self.assertFalse(_is_own("https://x.com/someoneelse/status/123", "elonmusk"))

    def test_empty_handle_or_url(self):
        from lib.xquik import _is_own
        self.assertFalse(_is_own("", "elonmusk"))
        self.assertFalse(_is_own("https://x.com/a/status/1", ""))


class TestFromLane(unittest.TestCase):
    def _resp(self, username, tid="1"):
        return {"tweets": [{
            "id": tid, "text": "anything", "createdAt": "2026-06-15T12:00:00Z",
            "likeCount": 10, "author": {"username": username},
        }]}

    def test_from_query_shape_and_no_topic_anded(self):
        from lib import xquik
        with patch("lib.xquik.http.get", return_value=self._resp("elonmusk")) as m:
            items = xquik.search_handles(["@elonmusk"], "Grok 4", "2026-05-19", "2026-06-18",
                                         count_per=8, token="k")
        url = m.call_args[0][0]
        self.assertIn("from%3Aelonmusk", url)        # from:elonmusk url-encoded
        self.assertIn("since%3A2026-05-19", url)
        self.assertNotIn("Grok", url)                 # topic must NOT be AND'd into query
        self.assertEqual(1, len(items))
        self.assertEqual("XF1", items[0]["id"])       # FROM-lane id prefix

    def test_no_token_or_no_handles_returns_empty(self):
        from lib import xquik
        self.assertEqual([], xquik.search_handles(["@x"], "t", "a", "b", token=""))
        self.assertEqual([], xquik.search_handles([], "t", "a", "b", token="k"))

    def test_item_ids_unique_across_handles(self):
        # Different tweets across two handles must not collide on item id.
        from lib import xquik
        responses = [
            {"tweets": [{"id": "1", "text": "a", "createdAt": "2026-06-15T12:00:00Z",
                         "author": {"username": "h1"}}]},
            {"tweets": [{"id": "2", "text": "b", "createdAt": "2026-06-15T12:00:00Z",
                         "author": {"username": "h2"}}]},
        ]
        with patch("lib.xquik.http.get", side_effect=responses):
            items = xquik.search_handles(["h1", "h2"], "topic", "2026-05-19", "2026-06-18", token="k")
        ids = [it["id"] for it in items]
        self.assertEqual(len(ids), len(set(ids)))


class TestAboutLane(unittest.TestCase):
    def test_mentions_drop_own_tweets(self):
        from lib import xquik
        resp = {"tweets": [
            {"id": "1", "text": "@elonmusk nice", "createdAt": "2026-06-15T12:00:00Z",
             "author": {"username": "fan"}},
            {"id": "2", "text": "my own post", "createdAt": "2026-06-15T12:00:00Z",
             "author": {"username": "elonmusk"}},
        ]}
        with patch("lib.xquik.http.get", return_value=resp) as m:
            items = xquik.search_mentions(["elonmusk"], "2026-05-19", "2026-06-18",
                                          topic="Grok 4", count_per=5, token="k")
        url = m.call_args[0][0]
        self.assertIn("%40elonmusk", url)             # @elonmusk url-encoded
        authors = {it["author_handle"] for it in items}
        self.assertIn("fan", authors)
        self.assertNotIn("elonmusk", authors)         # own tweet dropped


class TestExpandGuard(unittest.TestCase):
    @patch("lib.xquik._extract_core_subject", return_value="news")
    def test_bare_generic_core_falls_back_to_topic(self, _m):
        # #607: a single bare generic core must not be the query for a
        # multi-word topic — fall back to the full topic.
        qs = expand_xquik_queries("Grok 4 news", "quick")
        self.assertEqual(["Grok 4 news"], qs)

    @patch("lib.xquik._extract_core_subject", return_value="Grok 4")
    def test_multiword_core_kept(self, _m):
        qs = expand_xquik_queries("Grok 4 latest", "quick")
        self.assertEqual(["Grok 4"], qs)


class TestProbeWorks(unittest.TestCase):
    """U5: honest diagnose probe — tri-state, surfaces the unpaid (402) case."""

    def setUp(self):
        import lib.xquik as xq
        xq._probe_cache = ("unset", "")

    @patch("lib.xquik.http.get")
    def test_funded_key_works(self, mock_get):
        from lib import xquik
        mock_get.return_value = {"tweets": [{"id": "1"}]}
        self.assertIs(True, xquik.probe_works("k"))
        self.assertEqual("ok", xquik.probe_reason())

    @patch("lib.xquik.http.get")
    def test_unpaid_402_is_false_with_reason(self, mock_get):
        from lib import xquik, http as http_mod
        mock_get.side_effect = http_mod.HTTPError("Payment Required", status_code=402)
        self.assertIs(False, xquik.probe_works("k"))
        self.assertIn("unpaid", xquik.probe_reason())

    @patch("lib.xquik.http.get")
    def test_auth_401_is_false(self, mock_get):
        from lib import xquik, http as http_mod
        mock_get.side_effect = http_mod.HTTPError("Unauthorized", status_code=401)
        self.assertIs(False, xquik.probe_works("k"))
        self.assertIn("auth failed", xquik.probe_reason())

    @patch("lib.xquik.http.get")
    def test_timeout_is_inconclusive_fail_open(self, mock_get):
        from lib import xquik
        mock_get.side_effect = TimeoutError("timed out")
        self.assertIsNone(xquik.probe_works("k"))

    def test_no_token_is_false(self):
        from lib import xquik
        self.assertIs(False, xquik.probe_works(""))
        self.assertIn("no XQUIK_API_KEY", xquik.probe_reason())

    @patch("lib.xquik.http.get")
    def test_result_is_cached(self, mock_get):
        from lib import xquik
        mock_get.return_value = {"tweets": [{"id": "1"}]}
        xquik.probe_works("k")
        xquik.probe_works("k")
        self.assertEqual(1, mock_get.call_count)


class TestDiagnoseSurfacesXquik(unittest.TestCase):
    """U5: get_x_source_status reports xquik as the active X source when bird/
    xAI/xurl are absent, and surfaces the probe reason."""

    def setUp(self):
        import lib.xquik as xq
        xq._probe_cache = ("unset", "")

    @patch("lib.xquik.http.get")
    @patch("lib.xurl_x.is_available", return_value=False)
    @patch("lib.bird_x.get_bird_status")
    def test_xquik_is_active_x_source_when_only_key(self, mock_bird, _xurl, mock_get):
        from lib import env
        mock_bird.return_value = {"installed": False, "authenticated": False,
                                  "username": "", "can_install": False}
        mock_get.return_value = {"tweets": [{"id": "1"}]}
        status = env.get_x_source_status({"XQUIK_API_KEY": "k"}, probe=True)
        self.assertEqual("xquik", status["source"])
        self.assertTrue(status["xquik_available"])
        self.assertIs(True, status["xquik_working"])

    @patch("lib.xurl_x.is_available", return_value=False)
    @patch("lib.bird_x.get_bird_status")
    def test_unpaid_xquik_not_active_source(self, mock_bird, _xurl):
        from lib import env, http as http_mod
        import lib.xquik as xq
        mock_bird.return_value = {"installed": False, "authenticated": False,
                                  "username": "", "can_install": False}
        with patch("lib.xquik.http.get", side_effect=http_mod.HTTPError("pay", status_code=402)):
            status = env.get_x_source_status({"XQUIK_API_KEY": "k"}, probe=True)
        self.assertIsNone(status["source"])  # unpaid key is not a usable X source
        self.assertIs(False, status["xquik_working"])
        self.assertIn("unpaid", status["xquik_status"])


class TestMentionedHandles(unittest.TestCase):
    """U3: xquik items carry leading-run @mentions so the first-party
    interaction signal fires (shared parser with bird)."""

    def _tweet(self, text):
        return {
            "id": "1", "text": text, "createdAt": "2026-06-15T12:00:00Z",
            "author": {"username": "subject"},
        }

    def test_leading_mentions_captured(self):
        item = _parse_tweet(self._tweet("@jack @pmarca thoughts on this"), 0, "topic")
        self.assertEqual(["jack", "pmarca"], item["mentioned_handles"])

    def test_midbody_mention_ignored(self):
        item = _parse_tweet(self._tweet("I think @jack is right"), 0, "topic")
        self.assertEqual([], item["mentioned_handles"])

    def test_no_mentions(self):
        item = _parse_tweet(self._tweet("Grok 4 just shipped"), 0, "topic")
        self.assertEqual([], item["mentioned_handles"])


class TestNormalizePropagatesMentions(unittest.TestCase):
    """U3: _normalize_x carries xquik mentioned_handles into metadata so rerank
    can read them."""

    def test_mentioned_handles_reach_metadata(self):
        from lib import normalize
        item = _parse_tweet(
            {"id": "1", "text": "@jack hi", "createdAt": "2026-06-15T12:00:00Z",
             "author": {"username": "subject"}}, 0, "topic")
        normalized = normalize.normalize_source_items("xquik", [item], "2026-05-19", "2026-06-18")
        self.assertEqual(["jack"], normalized[0].metadata.get("mentioned_handles"))


if __name__ == "__main__":
    unittest.main()
