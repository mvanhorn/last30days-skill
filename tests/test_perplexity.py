import unittest
from unittest.mock import patch

from lib import perplexity


class PerplexityProviderTests(unittest.TestCase):
    def test_direct_perplexity_key_wins_and_parses_search_results(self):
        response = {
            "choices": [{"message": {"content": "Direct synthesis"}}],
            "citations": ["https://example.com/a"],
            "search_results": [
                {
                    "title": "Example A",
                    "url": "https://example.com/a",
                    "date": "2026-06-01",
                    "snippet": "Direct source snippet",
                    "source": "web",
                }
            ],
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "OPENROUTER_API_KEY": "or-test",
                },
            )

        url, payload = post.call_args.args[:2]
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(perplexity.PERPLEXITY_URL, url)
        self.assertEqual("Bearer pplx-test", headers["Authorization"])
        self.assertEqual("sonar-pro", payload["model"])
        self.assertEqual(
            "05/01/2026",
            payload["web_search_options"]["search_after_date_filter"],
        )
        self.assertEqual(
            "06/01/2026",
            payload["web_search_options"]["search_before_date_filter"],
        )
        self.assertEqual("perplexity", artifact["provider"])
        self.assertEqual("sonar-pro", artifact["model"])
        self.assertEqual("Example A", items[1]["title"])
        self.assertEqual("Direct source snippet", items[1]["snippet"])

    def test_direct_model_config_selects_supported_sonar_model(self):
        response = {
            "choices": [{"message": {"content": "Reasoned synthesis"}}],
            "citations": [],
            "search_results": [],
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODEL": "sonar-reasoning-pro",
                    "LAST30DAYS_PERPLEXITY_REASONING_EFFORT": "high",
                },
            )

        payload = post.call_args.args[1]
        self.assertEqual("sonar-reasoning-pro", payload["model"])
        self.assertEqual("high", payload["reasoning_effort"])
        self.assertEqual("sonar-reasoning-pro", artifact["model"])

    def test_openrouter_fallback_uses_openrouter_models_and_annotations(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": "OpenRouter synthesis",
                        "annotations": [
                            {
                                "url_citation": {
                                    "url": "https://example.com/b",
                                    "title": "Example B",
                                }
                            }
                        ],
                    }
                }
            ],
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"OPENROUTER_API_KEY": "or-test"},
                deep=True,
            )

        url, payload = post.call_args.args[:2]
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(perplexity.OPENROUTER_URL, url)
        self.assertEqual("Bearer or-test", headers["Authorization"])
        self.assertEqual("perplexity/sonar-deep-research", payload["model"])
        self.assertEqual(120, post.call_args.kwargs["timeout"])
        self.assertEqual("openrouter", artifact["provider"])
        self.assertEqual("perplexity/sonar-deep-research", artifact["model"])
        self.assertEqual("Example B", items[1]["title"])

    def test_search_api_mode_returns_ranked_rows_with_filters(self):
        response = {
            "id": "search-1",
            "server_time": "2026-06-01T00:00:00Z",
            "results": [
                {
                    "title": "Ranked result",
                    "url": "https://example.com/ranked",
                    "snippet": "Search API snippet",
                    "date": "2026-05-15",
                    "last_updated": "2026-05-20",
                }
            ],
        }
        config = {
            "PERPLEXITY_API_KEY": "pplx-test",
            "LAST30DAYS_PERPLEXITY_MODE": "search",
            "LAST30DAYS_PERPLEXITY_MAX_RESULTS": "3",
            "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE": "low",
            "LAST30DAYS_PERPLEXITY_COUNTRY": "us",
            "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER": "example.com,example.org",
            "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER": "en",
            "LAST30DAYS_PERPLEXITY_RECENCY_FILTER": "year",
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                config,
            )

        url, payload = post.call_args.args[:2]
        self.assertEqual(perplexity.PERPLEXITY_SEARCH_URL, url)
        self.assertEqual("test topic", payload["query"])
        self.assertEqual(3, payload["max_results"])
        self.assertEqual("low", payload["search_context_size"])
        self.assertEqual("US", payload["country"])
        self.assertEqual(["example.com", "example.org"], payload["search_domain_filter"])
        self.assertEqual("05/01/2026", payload["search_after_date_filter"])
        self.assertEqual("06/01/2026", payload["search_before_date_filter"])
        self.assertNotIn("search_recency_filter", payload)
        self.assertEqual("search", artifact["mode"])
        self.assertEqual("Ranked result", items[0]["title"])
        self.assertEqual("2026-05-20", items[0]["metadata"]["last_updated"])

    def test_search_api_keeps_recency_filter_when_no_exact_dates_are_available(self):
        payload = perplexity._build_search_payload(
            "test topic",
            ("not-a-date", "also-not-a-date"),
            {"LAST30DAYS_PERPLEXITY_RECENCY_FILTER": "week"},
        )

        self.assertEqual("week", payload["search_recency_filter"])
        self.assertNotIn("search_after_date_filter", payload)
        self.assertNotIn("search_before_date_filter", payload)

    def test_both_mode_keeps_synthesis_and_dedupes_raw_rows(self):
        search_response = {
            "id": "search-1",
            "results": [
                {
                    "title": "Duplicate ranked result",
                    "url": "https://example.com/a",
                    "snippet": "Raw row",
                    "date": "2026-05-15",
                },
                {
                    "title": "Unique ranked result",
                    "url": "https://example.com/unique",
                    "snippet": "Unique raw row",
                    "date": "2026-05-16",
                },
            ],
        }
        sonar_response = {
            "choices": [{"message": {"content": "Sonar synthesis"}}],
            "citations": ["https://example.com/a"],
            "search_results": [
                {
                    "title": "Citation result",
                    "url": "https://example.com/a",
                    "snippet": "Citation row",
                    "date": "2026-05-15",
                }
            ],
        }
        with patch(
            "lib.perplexity.http.post",
            side_effect=[search_response, sonar_response],
        ) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODE": "both",
                },
            )

        self.assertEqual(perplexity.PERPLEXITY_SEARCH_URL, post.call_args_list[0].args[0])
        self.assertEqual(perplexity.PERPLEXITY_URL, post.call_args_list[1].args[0])
        self.assertEqual("both", artifact["mode"])
        self.assertEqual(3, artifact["itemCount"])
        self.assertEqual("perplexity.ai", items[0]["source_domain"])
        urls = [item["url"] for item in items if item["url"]]
        self.assertEqual(["https://example.com/a", "https://example.com/unique"], urls)

    def test_both_mode_keeps_search_rows_when_sonar_leg_fails(self):
        search_response = {
            "id": "search-1",
            "results": [
                {
                    "title": "Raw result",
                    "url": "https://example.com/raw",
                    "snippet": "Raw row",
                    "date": "2026-05-15",
                },
            ],
        }
        with patch(
            "lib.perplexity.http.post",
            side_effect=[
                search_response,
                perplexity.http.HTTPError("HTTP 500: Server Error", status_code=500),
            ],
        ):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODE": "both",
                },
            )

        self.assertEqual("Raw result", items[0]["title"])
        self.assertEqual(1, artifact["itemCount"])
        self.assertEqual("HTTPError", artifact["sonar"]["error"])
        self.assertEqual(500, artifact["sonar"]["statusCode"])

    def test_direct_deep_research_uses_async_api_and_wall_timeout_config(self):
        create_response = {"id": "async-1", "status": "CREATED", "created_at": 123}
        complete_response = {
            "id": "async-1",
            "status": "COMPLETED",
            "created_at": 123,
            "started_at": 124,
            "completed_at": 130,
            "response": {
                "choices": [{"message": {"content": "Deep synthesis"}}],
                "citations": ["https://example.com/deep"],
                "search_results": [
                    {
                        "title": "Deep citation",
                        "url": "https://example.com/deep",
                        "snippet": "Deep snippet",
                    }
                ],
                "usage": {
                    "total_tokens": 123,
                    "cost": {"total_cost": 0.12},
                },
            },
        }
        with patch("lib.perplexity.http.post", return_value=create_response) as post, \
             patch("lib.perplexity.http.get", return_value=complete_response) as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "300",
                },
                deep=True,
            )

        self.assertEqual(perplexity.PERPLEXITY_ASYNC_URL, post.call_args.args[0])
        self.assertEqual(
            perplexity.PERPLEXITY_ASYNC_URL,
            perplexity._provider({"PERPLEXITY_API_KEY": "pplx-test"}, deep=True)[2],
        )
        create_payload = post.call_args.args[1]
        self.assertEqual("sonar-deep-research", create_payload["request"]["model"])
        self.assertTrue(create_payload["idempotency_key"].startswith("last30days:"))
        self.assertEqual(f"{perplexity.PERPLEXITY_ASYNC_URL}/async-1", get.call_args.args[0])
        self.assertEqual("async-sonar", artifact["endpoint"])
        self.assertEqual(True, artifact["async"])
        self.assertEqual(300, artifact["asyncTimeoutSeconds"])
        self.assertEqual(create_payload["idempotency_key"], artifact["asyncIdempotencyKey"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(123, artifact["asyncCreatedAt"])
        self.assertEqual(124, artifact["asyncStartedAt"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(123, items[0]["metadata"]["usage"]["total_tokens"])

    def test_direct_deep_research_timeout_returns_empty_result(self):
        with patch("lib.perplexity.http.post", return_value={"id": "async-1", "status": "CREATED", "created_at": 123}), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "IN_PROGRESS",
                 "created_at": 123,
                 "started_at": 124,
             }), \
             patch("lib.perplexity.time.monotonic", side_effect=[0, 0, 2, 2]), \
             patch("lib.perplexity.time.sleep"):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "1",
                },
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("timeout", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("IN_PROGRESS", artifact["asyncStatus"])
        self.assertEqual(1, artifact["asyncTimeoutSeconds"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual("PENDING_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(123, artifact["asyncCreatedAt"])
        self.assertEqual(124, artifact["asyncStartedAt"])

    def test_direct_deep_research_failed_status_returns_failure_artifact(self):
        with patch("lib.perplexity.http.post", return_value={"id": "async-1", "status": "CREATED"}), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "FAILED",
                 "failed_at": 130,
                 "error_message": "provider failure",
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("failed", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("FAILED", artifact["asyncStatus"])
        self.assertEqual("FAILED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(130, artifact["asyncFailedAt"])
        self.assertEqual("provider failure", artifact["asyncErrorMessage"])

    def test_direct_deep_research_poll_error_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "async-1",
            "status": "CREATED",
            "created_at": 123,
        }), \
             patch("lib.perplexity.http.get", side_effect=perplexity.http.HTTPError(
                 "HTTP 429: Too Many Requests",
                 status_code=429,
             )):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("poll_error", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("CREATED", artifact["asyncStatus"])
        self.assertEqual("POLL_ERROR", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(429, artifact["asyncPollStatusCode"])

    def test_direct_deep_research_malformed_completed_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "async-1",
            "status": "CREATED",
            "created_at": 123,
        }), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "COMPLETED",
                 "created_at": 123,
                 "completed_at": 130,
                 "response": None,
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("failed", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("COMPLETED", artifact["asyncStatus"])
        self.assertEqual("FAILED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(
            "Async Deep Research completed without response",
            artifact["asyncErrorMessage"],
        )

    def test_direct_deep_research_empty_choices_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "async-1",
            "status": "CREATED",
            "created_at": 123,
        }) as post, \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "COMPLETED",
                 "created_at": 123,
                 "completed_at": 130,
                 "response": {
                     "choices": [],
                     "usage": {"total_tokens": 321},
                 },
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("empty_choices", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("COMPLETED", artifact["asyncStatus"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(
            post.call_args.args[1]["idempotency_key"],
            artifact["asyncIdempotencyKey"],
        )
        self.assertEqual(321, artifact["usage"]["total_tokens"])
        self.assertEqual(
            "Async Deep Research completed without choices",
            artifact["asyncErrorMessage"],
        )

    def test_direct_deep_research_empty_synthesis_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "async-1",
            "status": "CREATED",
            "created_at": 123,
        }) as post, \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "COMPLETED",
                 "created_at": 123,
                 "completed_at": 130,
                 "response": {
                     "choices": [{"message": {"content": ""}}],
                 },
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("empty_synthesis", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("COMPLETED", artifact["asyncStatus"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(
            post.call_args.args[1]["idempotency_key"],
            artifact["asyncIdempotencyKey"],
        )
        self.assertEqual(
            "Async Deep Research completed with empty synthesis",
            artifact["asyncErrorMessage"],
        )

    def test_direct_deep_research_malformed_choice_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "async-1",
            "status": "CREATED",
            "created_at": 123,
        }) as post, \
             patch("lib.perplexity.http.get", return_value={
                 "id": "async-1",
                 "status": "COMPLETED",
                 "created_at": 123,
                 "completed_at": 130,
                 "response": {
                     "choices": [None],
                 },
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("empty_synthesis", artifact["error"])
        self.assertEqual("async-1", artifact["asyncRequestId"])
        self.assertEqual("COMPLETED", artifact["asyncStatus"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(
            post.call_args.args[1]["idempotency_key"],
            artifact["asyncIdempotencyKey"],
        )

    def test_missing_keys_skip_without_http(self):
        with patch("lib.perplexity.http.post") as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {},
            )

        post.assert_not_called()
        self.assertEqual([], items)
        self.assertEqual({}, artifact)
