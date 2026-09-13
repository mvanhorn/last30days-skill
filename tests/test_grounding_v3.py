import io
import json
import threading
import unittest
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from lib import grounding, parallel_mcp


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


class KeenableSearchTests(unittest.TestCase):
    def test_keenable_search_reads_snippet_and_filters_to_in_range_items(self):
        mock_response = {
            "results": [
                {
                    "title": "Keenable Result",
                    "url": "https://example.com/keenable",
                    "description": "",
                    "snippet": "line one\n\nline two",
                    "published_at": "2026-03-15T08:00:00Z",
                },
                {
                    "title": "Old Result",
                    "url": "https://example.com/old",
                    "snippet": "Should be filtered",
                    "published_at": "2026-01-15T08:00:00Z",
                },
                {
                    "title": "No URL",
                    "snippet": "Should be skipped",
                    "published_at": "2026-03-15T08:00:00Z",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response) as mock_req:
            items, artifact = grounding.keenable_search("test", ("2026-02-25", "2026-03-27"))
        self.assertEqual(1, len(items))
        self.assertEqual("line one line two", items[0]["snippet"])
        self.assertEqual("2026-03-15", items[0]["date"])
        self.assertEqual("keenable", artifact["label"])
        self.assertEqual("https://api.keenable.ai/v1/search/public", mock_req.call_args.args[1])

    def test_keenable_search_caps_page_text_and_falls_back_to_description(self):
        mock_response = {
            "results": [
                {
                    "title": "Long Page",
                    "url": "https://example.com/long",
                    "snippet": "word " * 400,
                    "published_at": "2026-03-15",
                },
                {
                    "title": "Meta Only",
                    "url": "https://example.com/meta",
                    "description": "only a meta description",
                    "published_at": "2026-03-15",
                },
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response):
            items, _ = grounding.keenable_search("test", ("2026-02-25", "2026-03-27"))
        self.assertEqual(2, len(items))
        self.assertEqual(500, len(items[0]["snippet"]))
        self.assertEqual("only a meta description", items[1]["snippet"])

    def test_keenable_search_uses_the_keyed_endpoint_when_a_key_is_set(self):
        with patch("lib.grounding.http.request", return_value={"results": []}) as mock_req:
            grounding.keenable_search("test", ("2026-02-25", "2026-03-27"), "a-key")
        self.assertEqual("https://api.keenable.ai/v1/search", mock_req.call_args.args[1])
        self.assertEqual("a-key", mock_req.call_args.kwargs["headers"]["X-API-Key"])


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

    def test_auto_returns_empty_when_no_keys_and_native_search(self):
        # On a native-search host (signal set) with no paid key, the engine
        # leaves general web to the model's own search and returns nothing.
        config = {"LAST30DAYS_NATIVE_SEARCH": "1"}
        items, artifact = grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
        self.assertEqual([], items)
        self.assertEqual({}, artifact)

    def test_auto_selects_keenable_when_no_keys_and_no_native_search(self):
        # No paid key and no native search -> keenable (a real keyless API) is
        # the preferred keyless default over the degraded DDG/SearXNG floor.
        with patch("lib.grounding.keenable_search",
                   return_value=([], {"label": "keenable"})) as mock_keenable, \
             patch("lib.grounding.web_search_keyless.keyless_search",
                   return_value=([], {"label": "keyless"})) as mock_keyless:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="auto")
        mock_keenable.assert_called_once()
        mock_keyless.assert_not_called()

    def test_auto_selects_keenable_when_keenable_key_present(self):
        config = {"KEENABLE_API_KEY": "test-key"}
        with patch("lib.grounding.keenable_search", return_value=([], {})) as mock:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock.assert_called_once()

    def test_auto_suppresses_keenable_on_native_host_even_with_key(self):
        # Keenable is keyless-tier: a KEENABLE_API_KEY only lifts the rate limit,
        # it does not promote keenable past the native-search suppression guard.
        config = {"KEENABLE_API_KEY": "test-key", "LAST30DAYS_NATIVE_SEARCH": "1"}
        with patch("lib.grounding.keenable_search", return_value=([], {})) as mock_keenable:
            items, artifact = grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
        mock_keenable.assert_not_called()
        self.assertEqual([], items)
        self.assertEqual({}, artifact)

    def test_auto_prefers_parallel_over_keenable(self):
        config = {"PARALLEL_API_KEY": "parallel-key", "KEENABLE_API_KEY": "keenable-key"}
        with patch("lib.grounding.parallel_search", return_value=([], {})) as mock_parallel, \
             patch("lib.grounding.keenable_search", return_value=([], {})) as mock_keenable:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), config, backend="auto")
            mock_parallel.assert_called_once()
            mock_keenable.assert_not_called()

    def test_explicit_keenable_backend_invokes_keenable(self):
        with patch("lib.grounding.keenable_search", return_value=([], {})) as mock_keenable:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="keenable")
        mock_keenable.assert_called_once()

    def test_explicit_keyless_backend_invokes_keyless(self):
        with patch("lib.grounding.web_search_keyless.keyless_search",
                   return_value=([], {"label": "keyless"})) as mock_keyless:
            grounding.web_search("test", ("2026-02-25", "2026-03-27"), {}, backend="keyless")
        mock_keyless.assert_called_once()

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

class RedditEnrichmentIsolationTests(unittest.TestCase):
    def test_enrichment_http_failure_does_not_poison_web_source(self):
        """A reddit.com enrichment fetch failure (e.g. a 403 on a datacenter IP)
        is a secondary operation on already-retrieved web results; it must not be
        attributed to the web/grounding source and discard those results."""
        from lib import http

        retrieved = [
            {"url": "https://www.reddit.com/r/x/comments/1/abc/", "title": "T", "snippet": "s"},
        ]

        def fake_enrich(items):
            # The enricher swallows the error, but the http layer records the
            # terminal failure into whatever capture sink is currently active.
            http._record_failure(http.HTTPError("Blocked", status_code=403))
            return items

        with http.capture_failures() as source_sink:
            with patch.object(grounding, "web_search_keyless") as wsk, \
                 patch.object(grounding, "_enrich_reddit_items", side_effect=fake_enrich):
                wsk.keyless_search.return_value = (list(retrieved), {"keyless_backend": "startpage"})
                items, _ = grounding.web_search(
                    "q", ("2026-02-25", "2026-03-27"), {}, backend="keyless")

        self.assertEqual(len(items), 1)
        # The enrichment 403 is isolated in its own sink; the source's sink is clean.
        self.assertEqual(source_sink, [])


class ParallelMCPRuntimeTests(unittest.TestCase):
    class _Response:
        def __init__(self, payload, session=None, content_type="application/json"):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else b""
            self.body = io.BytesIO(raw)
            self.headers = {"Content-Type": content_type}
            if session:
                self.headers["Mcp-Session-Id"] = session
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, size=-1):
            return self.body.read(size)
        def readline(self, size=-1):
            return self.body.readline(size)

    def test_explicit_parallel_mcp_discovers_invokes_and_maps_anonymous_result(self):
        responses = iter([
            self._Response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}, "session-1"),
            self._Response(None),
            self._Response({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "web_search"}, {"name": "web_fetch"}]}}),
            self._Response({"jsonrpc": "2.0", "id": 3, "result": {"structuredContent": {"results": [{"url": "https://example.com/evidence", "title": None, "publish_date": "2026-08-01", "excerpts": ["Useful evidence"]}]}}}),
            self._Response(None),
        ])
        requests = []
        def open_response(request, timeout):
            requests.append(request)
            return next(responses)
        with patch("lib.parallel_mcp.urllib.request.OpenerDirector.open", side_effect=open_response):
            items, artifact = grounding.web_search(
                "agent runtimes", ("2026-07-27", "2026-08-26"), {}, backend="parallel-mcp"
            )
        self.assertEqual("https://example.com/evidence", items[0]["url"])
        self.assertEqual("Useful evidence", items[0]["snippet"])
        self.assertEqual("2026-08-01", items[0]["date"])
        self.assertEqual("parallel-mcp", artifact["label"])
        self.assertTrue(all(request.get_header("Authorization") is None for request in requests))
        self.assertTrue(all(request.get_header("Mcp-session-id") == "session-1" for request in requests[1:]))
        self.assertTrue(all(request.get_header("Mcp-protocol-version") == "2025-03-26" for request in requests[1:]))
        self.assertEqual("DELETE", requests[-1].method)
        messages = [json.loads(request.data) for request in requests if request.data]
        self.assertEqual(["initialize", "notifications/initialized", "tools/list", "tools/call"], [message["method"] for message in messages])
        self.assertEqual("web_search", messages[-1]["params"]["name"])
        self.assertEqual(["agent runtimes"], messages[-1]["params"]["arguments"]["search_queries"])
        self.assertNotIn("max_results", messages[-1]["params"]["arguments"])

    def test_parallel_mcp_rejects_oversized_response(self):
        class OversizedResponse(self._Response):
            def read(self, size=-1):
                self.requested_size = size
                return b"x" * size
        response = OversizedResponse(None)
        with patch("lib.parallel_mcp.urllib.request.OpenerDirector.open", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "exceeded 4 MiB"):
                grounding.web_search(
                    "test", ("2026-07-27", "2026-08-26"), {}, backend="parallel-mcp"
                )
        self.assertEqual(4 * 1024 * 1024 + 1, response.requested_size)

    def test_auto_without_opt_in_does_not_contact_parallel_mcp(self):
        with patch("lib.grounding.parallel_mcp.search") as mcp_search, \
             patch("lib.grounding.web_search_keyless.keyless_search", return_value=([], {})):
            grounding.web_search("test", ("2026-07-27", "2026-08-26"), {}, backend="auto")
        mcp_search.assert_not_called()

    def test_explicit_parallel_mcp_preserves_optional_bearer_auth(self):
        with patch("lib.grounding.parallel_mcp.search", return_value=([], {})) as mcp_search:
            grounding.web_search(
                "test", ("2026-07-27", "2026-08-26"),
                {"PARALLEL_API_KEY": "test-key"}, backend="parallel-mcp",
            )
        mcp_search.assert_called_once_with("test", ("2026-07-27", "2026-08-26"), "test-key")

    @contextmanager
    def _server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_redirects_never_forward_auth_session_or_search_data(self):
        target_requests = []
        original_requests = []

        class Target(BaseHTTPRequestHandler):
            def do_GET(self):
                target_requests.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
            do_POST = do_GET
            def log_message(self, *_args):
                pass

        class Redirect(Target):
            def do_POST(self):
                original_requests.append(dict(self.headers))
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(int(self.path.strip("/")))
                self.send_header("Location", target_url)
                self.end_headers()

        with self._server(Target) as target_url, self._server(Redirect) as source_url:
            for status in (301, 302, 303, 307, 308):
                with self.subTest(status=status), patch.object(parallel_mcp, "PARALLEL_MCP_URL", f"{source_url}/{status}"):
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        parallel_mcp._request(
                            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                            "test-key", "test-session",
                        )
                    self.assertEqual(status, raised.exception.code)
                    raised.exception.close()
        self.assertEqual([], target_requests)
        self.assertEqual(5, len(original_requests))
        self.assertTrue(all(headers["Authorization"] == "Bearer test-key" for headers in original_requests))
        self.assertTrue(all(headers["Mcp-Session-Id"] == "test-session" for headers in original_requests))

    def test_sse_matches_request_and_returns_without_waiting_for_eof(self):
        class OpenStream(self._Response):
            def read(self, size=-1):
                raise AssertionError("SSE must not wait for the whole body")
            def readline(self, size=-1):
                line = super().readline(size)
                if not line:
                    raise AssertionError("SSE must stop at the matching response")
                return line

        response = OpenStream(
            b': heartbeat\r\n\r\n'
            b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n'
            b'data: {"jsonrpc":"2.0","id":99,"result":{}}\r\n\r\n'
            b'event: message\r\ndata: {"jsonrpc":"2.0",\r\ndata: "id":3,"result":{"content":[]}}\r\n\r\n',
            content_type="text/event-stream; charset=utf-8",
        )
        self.assertEqual({"content": []}, parallel_mcp._read_response(response, 3)["result"])

    def test_sse_supports_batched_messages(self):
        payload = b'data: [{"jsonrpc":"2.0","method":"notifications/progress"},{"jsonrpc":"2.0","id":3,"result":{}}]\n\n'
        response = self._Response(payload, content_type="text/event-stream")
        self.assertEqual(3, parallel_mcp._read_response(response, 3)["id"])

    def test_sse_response_cap_includes_heartbeats_and_all_events(self):
        for payload in (b":" + b"x" * 80, b": heartbeat\n\n" * 10):
            with self.subTest(payload=payload), patch.object(parallel_mcp, "_MAX_RESPONSE_BYTES", 64):
                response = self._Response(payload, content_type="text/event-stream")
                with self.assertRaisesRegex(RuntimeError, "exceeded 4 MiB"):
                    parallel_mcp._read_response(response, 3)

    def test_rejects_missing_or_mismatched_rpc_response(self):
        for payload, content_type in (
            ({"jsonrpc": "2.0", "id": 99, "result": {}}, "application/json"),
            (None, "application/json"),
            (b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n', "text/event-stream"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(RuntimeError, "missing the requested"):
                    parallel_mcp._read_response(self._Response(payload, content_type=content_type), 3)

    def test_filters_dates_and_invalid_urls_before_applying_result_limit(self):
        rows = [
            {"url": "https://example.com/old", "publish_date": "2026-07-26"},
            {"url": "https://example.com/future", "publish_date": "2026-08-27"},
            {"url": "https://example.com/undated"},
            {"url": "https://example.com/invalid-date", "publish_date": "not-a-date"},
            {"url": "https:///missing-host", "publish_date": "2026-08-01"},
            {"url": "https://[invalid", "publish_date": "2026-08-01"},
            {"url": "https://example.com/start", "publish_date": "2026-07-27T12:00:00Z", "excerpts": "Start evidence"},
            {"url": "https://example.com/end", "publish_date": "2026-08-26", "excerpts": ["End evidence"]},
            {"url": "https://example.com/extra", "publish_date": "2026-08-01"},
        ]
        responses = [
            ({"result": {"protocolVersion": "2025-03-26"}}, None),
            ({}, None),
            ({"result": {"tools": [{"name": "web_search"}]}}, None),
            ({"result": {"content": [{"type": "text", "text": json.dumps({"results": rows})}]}}, None),
        ]
        with patch.object(parallel_mcp, "_request", side_effect=responses):
            items, artifact = parallel_mcp.search("test", ("2026-07-27", "2026-08-26"), count=2)
        self.assertEqual(["2026-07-27", "2026-08-26"], [item["date"] for item in items])
        self.assertEqual(["Start evidence", "End evidence"], [item["snippet"] for item in items])
        self.assertEqual(2, artifact["resultCount"])

    def test_empty_results_are_distinct_from_malformed_payloads(self):
        self.assertEqual([], parallel_mcp._search_rows({"structuredContent": {"results": []}}))
        for value in (42, "invalid", {"results": 42}):
            with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "no results array"):
                parallel_mcp._search_rows({"structuredContent": value})

    def test_discovers_web_search_on_later_page_and_cleanup_failure_is_nonfatal(self):
        responses = [
            ({"result": {"protocolVersion": "2025-03-26"}}, "session-1"),
            ({}, "session-1"),
            ({"result": {"tools": [{"name": "web_fetch"}], "nextCursor": "page-2"}}, "session-1"),
            ({"result": {"tools": [{"name": "web_search"}]}}, "session-1"),
            ({"result": {"structuredContent": {"results": []}}}, "session-1"),
            urllib.error.HTTPError(parallel_mcp.PARALLEL_MCP_URL, 405, "Not allowed", {}, None),
        ]
        with patch.object(parallel_mcp, "_request", side_effect=responses) as request:
            items, _ = parallel_mcp.search("test", ("2026-07-27", "2026-08-26"))
        self.assertEqual([], items)
        self.assertEqual({"cursor": "page-2"}, request.call_args_list[3].args[0]["params"])
        self.assertEqual(4, request.call_args_list[4].args[0]["id"])
        self.assertEqual((None, None, "session-1"), request.call_args_list[-1].args)

    def test_failed_discovery_still_terminates_session_and_preserves_error(self):
        responses = [
            ({"result": {"protocolVersion": "2025-03-26"}}, "session-1"),
            ({}, "session-1"),
            ({"error": {"message": "Discovery failed"}}, "session-1"),
            OSError("Cleanup failed"),
        ]
        with patch.object(parallel_mcp, "_request", side_effect=responses) as request:
            with self.assertRaisesRegex(RuntimeError, "Discovery failed"):
                parallel_mcp.search("test", ("2026-07-27", "2026-08-26"))
        self.assertIsNone(request.call_args.args[0])

    def test_unsupported_protocol_is_rejected_before_search(self):
        responses = [({"result": {"protocolVersion": "unsupported"}}, "session-1"), ({}, None)]
        with patch.object(parallel_mcp, "_request", side_effect=responses) as request:
            with self.assertRaisesRegex(RuntimeError, "unsupported protocol version"):
                parallel_mcp.search("test", ("2026-07-27", "2026-08-26"))
        self.assertEqual(2, request.call_count)
        self.assertIsNone(request.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
