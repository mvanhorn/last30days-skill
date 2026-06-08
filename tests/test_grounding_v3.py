import unittest
from unittest.mock import patch

from lib import grounding


class BraveSearchTests(unittest.TestCase):
    def test_brave_search_applies_freshness_and_filters_to_in_range_dated_items(self):
        mock_response = {
            "web": {
                "results": [
                    {
                        "title": "Test Article",
                        "url": "https://example.com/article",
                        "description": "A test snippet",
                        "page_age": "2026-03-10T00:00:00",
                    },
                    {
                        "title": "Old Article",
                        "url": "https://example.com/old",
                        "description": "Should be filtered",
                        "page_age": "2025-12-10T00:00:00",
                    },
                    {
                        "title": "Undated Article",
                        "url": "https://example.com/undated",
                        "description": "Should also be filtered",
                    }
                ]
            }
        }
        with patch("lib.grounding.http.request", return_value=mock_response) as mock_req:
            items, artifact = grounding.brave_search("test", ("2026-02-25", "2026-03-27"), "fake-key")
            self.assertEqual(1, len(items))
            self.assertEqual("Test Article", items[0]["title"])
            self.assertEqual("https://example.com/article", items[0]["url"])
            self.assertEqual("2026-03-10", items[0]["date"])
            self.assertEqual("brave", artifact["label"])
            call_url = mock_req.call_args.args[1]
            self.assertIn("freshness=2026-02-25to2026-03-27", call_url)


class SerperSearchTests(unittest.TestCase):
    def test_serper_search_filters_to_in_range_dated_items(self):
        mock_response = {
            "organic": [
                {
                    "title": "Serper Result",
                    "link": "https://example.com/serper",
                    "snippet": "A serper snippet",
                    "date": "Mar 15, 2026",
                },
                {
                    "title": "Old Result",
                    "link": "https://example.com/old",
                    "snippet": "Should be filtered",
                    "date": "Jan 15, 2026",
                },
                {
                    "title": "Undated Result",
                    "link": "https://example.com/undated",
                    "snippet": "Should also be filtered",
                }
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response):
            items, artifact = grounding.serper_search("test", ("2026-02-25", "2026-03-27"), "fake-key")
            self.assertEqual(1, len(items))
            self.assertEqual("Serper Result", items[0]["title"])
            self.assertEqual("2026-03-15", items[0]["date"])
            self.assertEqual("serper", artifact["label"])


class ExaSearchTests(unittest.TestCase):
    def test_exa_search_filters_to_in_range_dated_items(self):
        mock_response = {
            "results": [
                {
                    "title": "Exa Result",
                    "url": "https://example.com/exa",
                    "text": "An exa snippet about AI trends",
                    "publishedDate": "2026-03-15T00:00:00.000Z",
                    "score": 0.85,
                },
                {
                    "title": "Old Exa Result",
                    "url": "https://example.com/old-exa",
                    "text": "Should be filtered out",
                    "publishedDate": "2025-12-01T00:00:00.000Z",
                    "score": 0.7,
                },
                {
                    "title": "Undated Exa Result",
                    "url": "https://example.com/undated-exa",
                    "text": "No date means filtered",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response) as mock_req:
            items, artifact = grounding.exa_search("test", ("2026-02-25", "2026-03-27"), "fake-exa-key")
            self.assertEqual(1, len(items))
            self.assertEqual("Exa Result", items[0]["title"])
            self.assertEqual("https://example.com/exa", items[0]["url"])
            self.assertEqual("2026-03-15", items[0]["date"])
            self.assertTrue(items[0]["id"].startswith("WE"))
            self.assertEqual("exa", artifact["label"])
            self.assertEqual(1, artifact["resultCount"])
            # Verify API call
            call_args = mock_req.call_args
            self.assertEqual("POST", call_args.args[0])
            self.assertEqual("https://api.exa.ai/search", call_args.args[1])
            self.assertEqual("fake-exa-key", call_args.kwargs["headers"]["x-api-key"])

    def test_exa_search_returns_empty_for_no_results(self):
        with patch("lib.grounding.http.request", return_value={"results": []}):
            items, artifact = grounding.exa_search("test", ("2026-02-25", "2026-03-27"), "key")
            self.assertEqual([], items)
            self.assertEqual(0, artifact["resultCount"])


class ParallelSearchTests(unittest.TestCase):
    def test_parallel_search_filters_to_in_range_dated_items(self):
        mock_response = {
            "results": [
                {
                    "title": "Parallel Result",
                    "url": "https://example.com/parallel",
                    "snippet": "A parallel snippet",
                    "publish_date": "2026-03-15T00:00:00Z",
                },
                {
                    "title": "Old Parallel Result",
                    "url": "https://example.com/old-parallel",
                    "snippet": "Should be filtered",
                    "publish_date": "2025-12-01T00:00:00Z",
                },
                {
                    "title": "Undated Parallel Result",
                    "url": "https://example.com/undated-parallel",
                    "snippet": "Should also be filtered",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response) as mock_req:
            items, artifact = grounding.parallel_search(
                "test", ("2026-02-25", "2026-03-27"), "fake-parallel-key"
            )
            self.assertEqual(1, len(items))
            self.assertEqual("Parallel Result", items[0]["title"])
            self.assertEqual("https://example.com/parallel", items[0]["url"])
            self.assertEqual("2026-03-15", items[0]["date"])
            self.assertTrue(items[0]["id"].startswith("WP"))
            self.assertEqual("parallel", artifact["label"])
            self.assertEqual(1, artifact["resultCount"])
            self.assertEqual("POST", mock_req.call_args.args[0])
            self.assertEqual("https://api.parallel.ai/v1/search", mock_req.call_args.args[1])
            self.assertEqual(
                "Bearer fake-parallel-key",
                mock_req.call_args.kwargs["headers"]["Authorization"],
            )

    def test_parallel_search_returns_empty_for_no_results(self):
        with patch("lib.grounding.http.request", return_value={"results": []}):
            items, artifact = grounding.parallel_search("test", ("2026-02-25", "2026-03-27"), "key")
            self.assertEqual([], items)
            self.assertEqual(0, artifact["resultCount"])


class WebSearchDispatchTests(unittest.TestCase):
    def test_auto_selects_brave_when_key_present(self):
        config = {"BRAVE_API_KEY": "test-key"}
        with patch("lib.grounding.brave_search", return_value=([], {})) as mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock.assert_called_once()

    def test_auto_selects_exa_when_only_exa_key(self):
        config = {"EXA_API_KEY": "test-key"}
        with patch("lib.grounding.exa_search", return_value=([], {})) as mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock.assert_called_once()

    def test_auto_selects_serper_when_only_serper_key(self):
        config = {"SERPER_API_KEY": "test-key"}
        with patch("lib.grounding.serper_search", return_value=([], {})) as mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock.assert_called_once()

    def test_auto_selects_parallel_when_only_parallel_key(self):
        config = {"PARALLEL_API_KEY": "test-key"}
        with patch("lib.grounding.parallel_search", return_value=([], {})) as mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock.assert_called_once()

    def test_auto_returns_empty_when_no_keys(self):
        items, artifact = grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="auto")
        self.assertEqual([], items)
        self.assertEqual({}, artifact)

    def test_none_returns_empty(self):
        config = {"BRAVE_API_KEY": "test-key"}
        items, artifact = grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="none")
        self.assertEqual([], items)

    def test_auto_prefers_brave_over_exa(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key"}
        with patch("lib.grounding.brave_search", return_value=([], {})) as mock_brave, \
             patch("lib.grounding.exa_search", return_value=([], {})) as mock_exa:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock_brave.assert_called_once()
            mock_exa.assert_not_called()

    def test_auto_prefers_exa_over_serper(self):
        config = {"EXA_API_KEY": "exa-key", "SERPER_API_KEY": "serper-key"}
        with patch("lib.grounding.exa_search", return_value=([], {})) as mock_exa, \
             patch("lib.grounding.serper_search", return_value=([], {})) as mock_serper:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock_exa.assert_called_once()
            mock_serper.assert_not_called()

    def test_auto_prefers_serper_over_parallel(self):
        config = {"SERPER_API_KEY": "serper-key", "PARALLEL_API_KEY": "parallel-key"}
        with patch("lib.grounding.serper_search", return_value=([], {})) as mock_serper, \
             patch("lib.grounding.parallel_search", return_value=([], {})) as mock_parallel:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock_serper.assert_called_once()
            mock_parallel.assert_not_called()

    def test_auto_prefers_brave_when_all_keys_present(self):
        config = {"BRAVE_API_KEY": "brave-key", "EXA_API_KEY": "exa-key", "SERPER_API_KEY": "serper-key"}
        with patch("lib.grounding.brave_search", return_value=([], {})) as mock_brave, \
             patch("lib.grounding.exa_search", return_value=([], {})) as mock_exa, \
             patch("lib.grounding.serper_search", return_value=([], {})) as mock_serper:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock_brave.assert_called_once()
            mock_exa.assert_not_called()
            mock_serper.assert_not_called()

    def test_explicit_exa_without_key_raises(self):
        with self.assertRaises(RuntimeError):
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="exa")

    def test_explicit_brave_without_key_raises(self):
        with self.assertRaises(RuntimeError):
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="brave")

    def test_explicit_parallel_without_key_raises(self):
        with self.assertRaises(RuntimeError):
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="parallel")

    def test_unsupported_backend_raises(self):
        with self.assertRaises(ValueError):
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="google")


class RedditEnrichmentGateTests(unittest.TestCase):
    """EXCLUDE_SOURCES=reddit must suppress the web-search Reddit enrichment.

    Otherwise a user who explicitly excluded Reddit would still get Reddit
    content smuggled back in via web-search URLs that happen to point at
    reddit.com threads.
    """

    def test_reddit_excluded_via_exclude_sources_skips_enrichment(self):
        config = {"BRAVE_API_KEY": "k", "EXCLUDE_SOURCES": "reddit"}
        items = [{"url": "https://www.reddit.com/r/python/comments/abc/title/", "snippet": "original"}]
        with patch("lib.grounding.brave_search", return_value=(items, {})), \
             patch("lib.grounding._enrich_reddit_items") as enrich_mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            enrich_mock.assert_not_called()

    def test_reddit_excluded_case_insensitive(self):
        for value in ("REDDIT", "Reddit", " reddit ", "x,reddit,y"):
            config = {"BRAVE_API_KEY": "k", "EXCLUDE_SOURCES": value}
            self.assertTrue(
                grounding._reddit_excluded(config),
                msg=f"_reddit_excluded should be True for EXCLUDE_SOURCES={value!r}",
            )

    def test_reddit_not_excluded_when_other_sources_listed(self):
        config = {"EXCLUDE_SOURCES": "tiktok,instagram"}
        self.assertFalse(grounding._reddit_excluded(config))

    def test_enrichment_runs_when_reddit_not_excluded(self):
        config = {"BRAVE_API_KEY": "k"}
        items = [{"url": "https://www.reddit.com/r/python/comments/abc/title/", "snippet": "original"}]
        with patch("lib.grounding.brave_search", return_value=(items, {})), \
             patch("lib.grounding._enrich_reddit_items", return_value=items) as enrich_mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            enrich_mock.assert_called_once()


class RedditEnrichItemsTests(unittest.TestCase):
    """Direct tests for `_enrich_reddit_items` covering the selftext key path
    and the RedditRateLimitError early-exit behavior.
    """

    def test_selftext_under_submission_populates_snippet(self):
        from lib import reddit_enrich

        item = {
            "url": "https://www.reddit.com/r/python/comments/abc/title/",
            "snippet": "original",
        }
        parsed = {
            "submission": {"selftext": "thread body content"},
            "comments": [],
        }
        with patch.object(reddit_enrich, "fetch_thread_data", return_value={"raw": True}), \
             patch.object(reddit_enrich, "parse_thread_data", return_value=parsed):
            result = grounding._enrich_reddit_items([item])
        self.assertEqual("thread body content", result[0]["snippet"])
        self.assertEqual("reddit_json_api", result[0]["enriched_via"])

    def test_rate_limit_error_halts_iteration(self):
        from lib import reddit_enrich

        item1 = {"url": "https://www.reddit.com/r/python/comments/aaa/x/"}
        item2 = {"url": "https://www.reddit.com/r/python/comments/bbb/y/"}

        def fake_fetch(url, *args, **kwargs):
            raise reddit_enrich.RedditRateLimitError(f"429 for {url}")

        captured_stderr: list[str] = []

        with patch.object(reddit_enrich, "fetch_thread_data", side_effect=fake_fetch) as fetch_mock, \
             patch("lib.grounding.sys.stderr.write", side_effect=lambda s: captured_stderr.append(s)):
            grounding._enrich_reddit_items([item1, item2])

        # Only the first item should have triggered a fetch attempt
        self.assertEqual(1, fetch_mock.call_count)
        # A stderr message about the rate-limit halt should have been emitted
        self.assertTrue(
            any("rate-limited" in msg.lower() or "rate limited" in msg.lower() for msg in captured_stderr),
            msg=f"Expected a rate-limit stderr message, got: {captured_stderr!r}",
        )

if __name__ == "__main__":
    unittest.main()
