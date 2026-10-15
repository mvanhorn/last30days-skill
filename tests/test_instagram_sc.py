"""Tests for instagram.py — ScrapeCreators Instagram search module."""

import os
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

# Add lib to path

from lib import instagram
from lib.relevance import tokenize as _tokenize


class TestTokenize(unittest.TestCase):
    """Tests for tokenize() from relevance module."""

    def test_strips_stopwords(self):
        tokens = _tokenize("how to use the AI tools")
        self.assertNotIn("how", tokens)
        self.assertNotIn("the", tokens)
        self.assertNotIn("to", tokens)

    def test_expands_synonyms(self):
        tokens = _tokenize("ai tools")
        self.assertTrue("artificial" in tokens or "intelligence" in tokens)

    def test_removes_single_char(self):
        tokens = _tokenize("a b c python")
        self.assertNotIn("a", tokens)
        self.assertNotIn("b", tokens)
        self.assertIn("python", tokens)

    def test_lowercases(self):
        tokens = _tokenize("Python REACT")
        self.assertIn("python", tokens)
        self.assertIn("react", tokens)

    def test_strips_punctuation(self):
        tokens = _tokenize("hello, world!")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)


class TestComputeRelevance(unittest.TestCase):
    """Tests for _compute_relevance()."""

    def test_exact_match_high(self):
        rel = instagram._compute_relevance("claude code", "Claude Code tricks and tips")
        self.assertGreaterEqual(rel, 0.8)

    def test_partial_match_lower(self):
        rel = instagram._compute_relevance("claude code tips", "Best AI tools for coding")
        self.assertLess(rel, 0.5)

    def test_hashtag_boost(self):
        base = instagram._compute_relevance("claude code", "random video about stuff")
        boosted = instagram._compute_relevance("claude code", "random video about stuff", ["claudecode", "ai"])
        self.assertGreater(boosted, base)

    def test_no_match_returns_zero(self):
        rel = instagram._compute_relevance("quantum physics", "cat dancing video")
        self.assertEqual(rel, 0.0)

    def test_empty_query_returns_default(self):
        rel = instagram._compute_relevance("", "Some video title")
        self.assertEqual(rel, 0.5)


class TestInstagramDepthConfig(unittest.TestCase):
    """Tests for DEPTH_CONFIG."""

    def test_all_depths_exist(self):
        for depth in ("quick", "default", "deep"):
            self.assertIn(depth, instagram.DEPTH_CONFIG)

    def test_required_keys(self):
        for depth, config in instagram.DEPTH_CONFIG.items():
            self.assertIn("results_per_page", config)
            self.assertIn("max_captions", config)

    def test_deep_has_more_results(self):
        self.assertGreater(
            instagram.DEPTH_CONFIG["deep"]["results_per_page"],
            instagram.DEPTH_CONFIG["quick"]["results_per_page"],
        )


class TestHashtagFormCollapse(unittest.TestCase):
    """Tests for _to_hashtag_form() — the multi-word retry workaround."""

    def test_collapses_spaces(self):
        self.assertEqual(instagram._to_hashtag_form("toronto real estate"), "torontorealestate")

    def test_lowercases(self):
        self.assertEqual(instagram._to_hashtag_form("Toronto REAL Estate"), "torontorealestate")

    def test_idempotent_on_single_word(self):
        self.assertEqual(instagram._to_hashtag_form("ozempic"), "ozempic")

    def test_handles_extra_whitespace(self):
        self.assertEqual(instagram._to_hashtag_form("  toronto   real  estate  "), "torontorealestate")


class TestSearchRetryOn500(unittest.TestCase):
    """Tests for the multi-word -> hashtag retry on SC's flaky 500 path.

    SC's /v2/instagram/reels/search wraps Google Search and is documented
    to be unreliable on multi-token queries. The retry collapses to a
    hashtag form which hits the stable hashtag-page lookup path.
    """

    def test_multiword_500_triggers_retry_with_hashtag_form(self):
        """Multi-word query 500 -> retry with collapsed hashtag form."""
        from lib import http as http_module
        first_error = http_module.HTTPError("HTTP 500: Server Error", 500, "")
        second_payload = {
            "reels": [{"id": "retry-result", "taken_at": "2026-04-15T12:00:00Z"}]
        }
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.side_effect = [first_error, second_payload]
            instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 2)
            # First call: original multi-word query
            first_params = mock_http_get.call_args_list[0].kwargs["params"]
            self.assertEqual(first_params["query"], "toronto real estate")
            # Second call: collapsed hashtag form
            second_params = mock_http_get.call_args_list[1].kwargs["params"]
            self.assertEqual(second_params["query"], "torontorealestate")

    def test_singleword_500_does_not_retry(self):
        """Single-word query 500 has no spaces to collapse - no retry."""
        from lib import http as http_module
        only_error = http_module.HTTPError("HTTP 500: Server Error", 500, "")
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.side_effect = only_error
            result = instagram.search_instagram(
                "ozempic", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 1)
            self.assertIn("error", result)
            self.assertEqual(result["items"], [])

    def test_first_call_succeeds_no_retry(self):
        """200 on first call -> retry path is never entered."""
        from lib import http as http_module
        ok_payload = {
            "reels": [{"id": "search-result", "taken_at": "2026-04-15T12:00:00Z"}]
        }
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = ok_payload
            instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token="fake-token",
            )
            self.assertEqual(mock_http_get.call_count, 1)

    def test_no_token_short_circuits(self):
        """No SCRAPECREATORS_API_KEY -> error returned without HTTP call."""
        from lib import http as http_module
        with patch.object(http_module, "get") as mock_http_get:
            result = instagram.search_instagram(
                "toronto real estate", "2026-04-01", "2026-05-04",
                depth="default", token=None,
            )
            mock_http_get.assert_not_called()
            self.assertIn("error", result)
            self.assertIn("SCRAPECREATORS_API_KEY", result["error"])


class TestGoogleSearchFallback(unittest.TestCase):
    """The Google-indexed post lane recovers when reels search is empty."""

    def _post_payload(self):
        return {
            "data": {
                "xdt_shortcode_media": {
                    "id": "post-1020",
                    "shortcode": "ABC1020",
                    "taken_at_timestamp": 1786900800,
                    "edge_media_to_caption": {
                        "edges": [{"node": {"text": "AI automation for small business"}}]
                    },
                    "video_play_count": 900,
                    "edge_media_preview_like": {"count": 80},
                    "edge_media_to_parent_comment": {"count": 12},
                    "owner": {"username": "creator1020"},
                }
            }
        }

    def test_404_search_falls_back_to_google_reel_urls_and_post_details(self):
        from lib import http as http_module

        not_found = http_module.HTTPError(
            'HTTP 404: No scrapeable reels found for that query', 404, ""
        )
        with patch.object(
            http_module,
            "get",
            side_effect=[
                not_found,
                {
                    "results": [
                        {"url": "https://www.instagram.com/reel/ABC1020/"},
                        {"url": "https://www.instagram.com/reel/ABC1020/"},
                    ]
                },
                self._post_payload(),
            ],
        ) as mock_http_get:
            result = instagram.search_instagram(
                "AI automation for small business",
                "2026-08-01",
                "2026-08-31",
                depth="quick",
                token="fake-token",
            )

        self.assertNotIn("error", result)
        self.assertEqual(1, len(result["items"]))
        item = result["items"][0]
        self.assertEqual("post-1020", item["video_id"])
        self.assertEqual("creator1020", item["author_name"])
        self.assertEqual("AI automation for small business", item["text"])
        self.assertEqual("2026-08-16", item["date"])
        self.assertEqual(
            {"views": 900, "likes": 80, "comments": 12}, item["engagement"]
        )
        self.assertEqual(3, mock_http_get.call_count)
        google_call = mock_http_get.call_args_list[1]
        self.assertEqual(
            "site:instagram.com/reel ai automation for small business "
            "after:2026-08-01 before:2026-09-01",
            google_call.kwargs["params"]["query"],
        )
        self.assertNotIn("date_posted", google_call.kwargs["params"])
        self.assertEqual(1, google_call.kwargs["params"]["page"])
        post_call = mock_http_get.call_args_list[2]
        self.assertEqual(
            "https://www.instagram.com/reel/ABC1020/",
            post_call.kwargs["params"]["url"],
        )
        self.assertEqual(1, post_call.kwargs["retries"])

    def test_404_is_preserved_when_google_fallback_has_no_results(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        with patch.object(
            http_module, "get", side_effect=[not_found, {"results": []}]
        ):
            result = instagram.search_instagram(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="quick", token="fake-token",
            )

        self.assertEqual([], result["items"])
        self.assertIn("404", result["error"])

    def test_404_is_preserved_when_google_search_fails(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        google_error = http_module.HTTPError("HTTP 503: unavailable", 503, "")
        with patch.object(
            http_module, "get", side_effect=[not_found, google_error]
        ):
            result = instagram.search_instagram(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="quick", token="fake-token",
            )

        self.assertEqual([], result["items"])
        self.assertIn("404", result["error"])

    def test_google_fallback_rejects_non_instagram_and_non_reel_urls(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        results = {
            "results": [
                {"url": "https://example.com/reel/EXAMPLE/"},
                {"url": "http://instagram.com/reel/HTTP/"},
                {"url": "https://instagram.com.example.org/reel/LOOKALIKE/"},
                {"url": "https://instagram.com/p/POST/"},
                {"url": "https://instagram.com/not/reel/PREFIX/"},
                {"url": "https://[invalid/reel/MALFORMED/"},
            ]
        }
        with patch.object(
            http_module, "get", side_effect=[not_found, results]
        ) as mock_http_get:
            result = instagram.search_instagram(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="quick", token="fake-token",
            )

        self.assertEqual([], result["items"])
        self.assertIn("404", result["error"])
        self.assertEqual(2, mock_http_get.call_count)

    def test_google_fallback_drops_posts_outside_requested_date_range(self):
        from lib import http as http_module

        with patch.object(
            http_module,
            "get",
            side_effect=[
                {"reels": []},
                {"results": [{"url": "https://www.instagram.com/reel/ABC1020/"}]},
                self._post_payload(),
            ],
        ):
            result = instagram.search_instagram(
                "AI automation", "2025-08-01", "2025-08-31",
                depth="quick", token="fake-token",
            )

        self.assertEqual([], result["items"])
        self.assertNotIn("error", result)

    def test_empty_reels_search_uses_google_fallback(self):
        from lib import http as http_module

        with patch.object(
            http_module,
            "get",
            side_effect=[
                {"reels": []},
                {"results": [{"url": "https://www.instagram.com/reel/ABC1020/"}]},
                self._post_payload(),
            ],
        ) as mock_http_get:
            result = instagram.search_instagram(
                "AI automation for small business",
                "2026-08-01",
                "2026-08-31",
                depth="quick",
                token="fake-token",
            )

        self.assertEqual(1, len(result["items"]))
        self.assertEqual(3, mock_http_get.call_count)

    def test_default_depth_fallback_searches_the_second_page(self):
        from lib import http as http_module

        with patch.object(
            http_module,
            "get",
            side_effect=[
                {"reels": []},
                {"results": []},
                {"results": [{"url": "https://www.instagram.com/reel/ABC1020/"}]},
                self._post_payload(),
            ],
        ) as mock_http_get:
            result = instagram.search_instagram(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="default", token="fake-token",
            )

        self.assertEqual(1, len(result["items"]))
        google_calls = [
            call for call in mock_http_get.call_args_list
            if call.args[0].endswith("/v1/google/search")
        ]
        self.assertEqual(
            [1, 2], [call.kwargs["params"]["page"] for call in google_calls]
        )

    def test_expanded_queries_share_the_depth_fallback_budget(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        with (
            patch.object(
                instagram,
                "expand_instagram_queries",
                return_value=["first", "second"],
            ),
            patch.object(instagram, "fetch_captions", return_value={}),
            patch.object(
                http_module,
                "get",
                side_effect=[
                    not_found,
                    {"results": []},
                    not_found,
                    {"results": []},
                ],
            ) as mock_http_get,
        ):
            result = instagram.search_and_enrich(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="default", token="fake-token",
            )

        self.assertEqual([], result["items"])
        google_calls = [
            call for call in mock_http_get.call_args_list
            if call.args[0].endswith("/v1/google/search")
        ]
        self.assertEqual(2, len(google_calls))
        self.assertEqual(
            [1, 1], [call.kwargs["params"]["page"] for call in google_calls]
        )

    def test_expanded_queries_fetch_each_reel_shortcode_once(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        with (
            patch.object(
                instagram,
                "expand_instagram_queries",
                return_value=["first", "second"],
            ),
            patch.object(instagram, "fetch_captions", return_value={}),
            patch.object(
                http_module,
                "get",
                side_effect=[
                    not_found,
                    {"results": [{"url": "https://www.instagram.com/reel/ABC1020/"}]},
                    self._post_payload(),
                    not_found,
                    {"results": [{"url": "https://instagram.com/reel/ABC1020"}]},
                ],
            ) as mock_http_get,
        ):
            result = instagram.search_and_enrich(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="default", token="fake-token",
            )

        detail_calls = [
            call for call in mock_http_get.call_args_list
            if call.args[0].endswith("/v1/instagram/post")
        ]
        self.assertEqual(1, len(result["items"]))
        self.assertEqual(1, len(detail_calls))

    def test_expanded_queries_retry_a_failed_detail_within_the_budget(self):
        from lib import http as http_module

        not_found = http_module.HTTPError("HTTP 404: no reels", 404, "")
        temporary_error = http_module.HTTPError("HTTP 503: unavailable", 503, "")
        with (
            patch.object(
                instagram,
                "expand_instagram_queries",
                return_value=["first", "second"],
            ),
            patch.object(instagram, "fetch_captions", return_value={}),
            patch.object(
                http_module,
                "get",
                side_effect=[
                    not_found,
                    {"results": [{"url": "https://www.instagram.com/reel/ABC1020/"}]},
                    temporary_error,
                    not_found,
                    {"results": [{"url": "https://instagram.com/reel/ABC1020"}]},
                    self._post_payload(),
                ],
            ) as mock_http_get,
        ):
            result = instagram.search_and_enrich(
                "AI automation", "2026-08-01", "2026-08-31",
                depth="default", token="fake-token",
            )

        detail_calls = [
            call for call in mock_http_get.call_args_list
            if call.args[0].endswith("/v1/instagram/post")
        ]
        self.assertEqual(1, len(result["items"]))
        self.assertEqual(2, len(detail_calls))


class TestTranscriptTimeoutConfig(unittest.TestCase):
    """Tests for LAST30DAYS_TRANSCRIPT_TIMEOUT configuration.

    SC's /v2/instagram/media/transcript endpoint regularly takes >15s,
    so the timeout must be configurable. Default is DEFAULT_TRANSCRIPT_TIMEOUT
    (30s); the env var or per-call kwarg overrides it.
    """

    def setUp(self):
        # Snapshot any pre-existing env so we don't leak across tests
        self._saved_env = os.environ.pop("LAST30DAYS_TRANSCRIPT_TIMEOUT", None)

    def tearDown(self):
        os.environ.pop("LAST30DAYS_TRANSCRIPT_TIMEOUT", None)
        if self._saved_env is not None:
            os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = self._saved_env

    def _ok_payload(self):
        return {"transcripts": [{"text": "hello world"}]}

    def _video_item(self, vid="abc123"):
        return {
            "video_id": vid,
            "url": f"https://www.instagram.com/reel/{vid}/",
            "text": "",
        }

    def test_default_timeout_is_30s_when_nothing_set(self):
        """No env var, no kwarg -> request uses 30s, not the legacy 15s."""
        from lib import http as http_module
        items = [self._video_item()]
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = self._ok_payload()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_http_get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 30.0)

    def test_env_var_override(self):
        """LAST30DAYS_TRANSCRIPT_TIMEOUT='60' -> request uses 60s."""
        from lib import http as http_module
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "60"
        items = [self._video_item()]
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = self._ok_payload()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_http_get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 60.0)

    def test_explicit_timeout_kwarg_wins_over_env(self):
        """Explicit timeout= kwarg trumps the env var."""
        from lib import http as http_module
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "60"
        items = [self._video_item()]
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = self._ok_payload()
            instagram.fetch_captions(items, token="fake-token", timeout=10)
            kwargs = mock_http_get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 10.0)

    def test_config_dict_fallback_when_env_unset(self):
        """config={'LAST30DAYS_TRANSCRIPT_TIMEOUT': '45'} -> request uses 45s."""
        from lib import http as http_module
        items = [self._video_item()]
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = self._ok_payload()
            instagram.fetch_captions(
                items,
                token="fake-token",
                config={"LAST30DAYS_TRANSCRIPT_TIMEOUT": "45"},
            )
            kwargs = mock_http_get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 45.0)

    def test_invalid_env_value_falls_back_to_default(self):
        """Garbage env var doesn't crash; falls back to 30s."""
        from lib import http as http_module
        os.environ["LAST30DAYS_TRANSCRIPT_TIMEOUT"] = "not-a-number"
        items = [self._video_item()]
        with patch.object(http_module, "get") as mock_http_get:
            mock_http_get.return_value = self._ok_payload()
            instagram.fetch_captions(items, token="fake-token")
            kwargs = mock_http_get.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 30.0)

class TestInstagramArabicTopicEncoding(unittest.TestCase):
    """Regression for issue #817 across the Instagram search path."""

    def _sent_url(self, mock_urlopen) -> str:
        return mock_urlopen.call_args[0][0].full_url

    @patch("lib.http.urllib.request.urlopen")
    def test_arabic_topic_returns_success_with_ascii_encoded_url(self, mock_urlopen):
        resp = MagicMock()
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = (
            b'{"reels": [{"id": "arabic-result", '
            b'"taken_at": "2026-06-10T00:00:00Z"}]}'
        )
        resp.status = 200
        mock_urlopen.return_value = resp

        topic = "اسعار التمريض المنزلي السعودية"
        result = instagram.search_instagram(
            topic,
            "2026-06-01",
            "2026-07-13",
            depth="quick",
            token="dummy-key",
        )
        self.assertNotIn("error", result)
        sent_url = self._sent_url(mock_urlopen)
        sent_url.encode("ascii")
        self.assertEqual(parse_qs(urlsplit(sent_url).query)["query"], [topic])


if __name__ == "__main__":
    unittest.main()
