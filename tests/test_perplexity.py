import unittest
from unittest.mock import patch

from lib import perplexity


def _agent_response(text="Direct synthesis", search_result_items=None, **extra):
    """Build an Agent API response: a typed output array, not choices."""
    output = []
    for results in search_result_items or []:
        output.append({"type": "search_results", "queries": ["q"], "results": results})
    if text is not None:
        output.append({
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        })
    response = {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "model": "openai/gpt-5.6-luna",
        "output": output,
    }
    response.update(extra)
    return response


class PerplexityProviderTests(unittest.TestCase):
    def test_direct_perplexity_key_wins_and_parses_search_results(self):
        response = _agent_response(
            text="Direct synthesis",
            search_result_items=[[
                {
                    "id": 1,
                    "title": "Example A",
                    "url": "https://example.com/a",
                    "date": "2026-06-01",
                    "snippet": "Direct source snippet",
                    "source": "web",
                }
            ]],
        )
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
        # Agent API takes preset + input, not model + messages.
        self.assertEqual("low", payload["preset"])
        self.assertIn("test topic", payload["input"])
        self.assertNotIn("model", payload)
        self.assertNotIn("messages", payload)
        # Search controls live on the web_search tool's filters.
        web_search = payload["tools"][0]
        self.assertEqual("web_search", web_search["type"])
        self.assertEqual("05/01/2026", web_search["filters"]["search_after_date_filter"])
        self.assertEqual("06/01/2026", web_search["filters"]["search_before_date_filter"])
        self.assertEqual("perplexity", artifact["provider"])
        self.assertEqual("agent", artifact["endpoint"])
        self.assertEqual("low", artifact["preset"])
        # The preset picks the model, so the artifact reports what actually ran.
        self.assertEqual("openai/gpt-5.6-luna", artifact["model"])
        self.assertEqual("Example A", items[1]["title"])
        self.assertEqual("Direct source snippet", items[1]["snippet"])

    def test_direct_model_config_maps_sonar_model_to_preset(self):
        response = _agent_response(text="Reasoned synthesis", search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODEL": "sonar-reasoning-pro",
                },
            )

        payload = post.call_args.args[1]
        self.assertEqual("medium", payload["preset"])
        self.assertEqual("medium", artifact["preset"])
        self.assertEqual("Reasoned synthesis", items[0]["snippet"])

    def test_explicit_preset_config_overrides_model_mapping(self):
        response = _agent_response(search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response) as post:
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODEL": "sonar",
                    "LAST30DAYS_PERPLEXITY_PRESET": "xhigh",
                },
            )

        self.assertEqual("xhigh", post.call_args.args[1]["preset"])
        self.assertEqual("xhigh", artifact["preset"])

    def test_unsupported_preset_config_falls_back_to_model_mapping(self):
        response = _agent_response(search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response) as post:
            perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_PRESET": "turbo",
                },
            )

        self.assertEqual("low", post.call_args.args[1]["preset"])

    def test_agent_merges_and_dedupes_results_across_every_search_step(self):
        """The agentic loop emits one search_results item per web_search call."""
        response = _agent_response(
            text="Multi-step synthesis",
            search_result_items=[
                [
                    {"id": 1, "title": "First", "url": "https://example.com/a", "date": "2026-05-10"},
                    {"id": 2, "title": "Second", "url": "https://example.com/b", "date": "2026-05-11"},
                ],
                [
                    {"id": 3, "title": "Dupe of first", "url": "https://example.com/a", "date": "2026-05-10"},
                    {"id": 4, "title": "Third", "url": "https://example.com/c", "date": "2026-05-12"},
                ],
            ],
        )
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        urls = [item["url"] for item in items if item["url"]]
        self.assertEqual(
            ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
            urls,
        )
        self.assertEqual(3, artifact["citationCount"])

    def test_agent_records_api_reported_cost(self):
        response = _agent_response(
            search_result_items=[[]],
            usage={"total_tokens": 40340, "cost": {"total_cost": 0.04645}},
        )
        with patch("lib.perplexity.http.post", return_value=response):
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual(0.04645, artifact["totalCostUsd"])
        self.assertEqual(40340, artifact["usage"]["total_tokens"])

    def test_agent_unsupported_sonar_filters_are_not_sent(self):
        response = _agent_response(search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response) as post:
            perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_SEARCH_MODE": "academic",
                    "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER": "en",
                },
            )

        payload = post.call_args.args[1]
        filters = payload["tools"][0]["filters"]
        self.assertNotIn("search_mode", filters)
        self.assertNotIn("search_language_filter", filters)
        self.assertNotIn("web_search_options", payload)

    def test_agent_puts_context_size_on_tool_and_effort_under_reasoning(self):
        response = _agent_response(search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response) as post:
            perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE": "high",
                    "LAST30DAYS_PERPLEXITY_REASONING_EFFORT": "low",
                },
            )

        payload = post.call_args.args[1]
        web_search = payload["tools"][0]
        self.assertEqual("high", web_search["search_context_size"])
        self.assertNotIn("search_context_size", web_search["filters"])
        self.assertEqual({"effort": "low"}, payload["reasoning"])

    def test_agent_empty_output_returns_empty_synthesis(self):
        response = _agent_response(text=None, search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual([], items)
        self.assertEqual({}, artifact)

    def test_openrouter_fallback_uses_openrouter_models_and_annotations(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": "Fallback synthesis",
                        "annotations": [
                            {
                                "type": "url_citation",
                                "url_citation": {
                                    "url": "https://example.com/or",
                                    "title": "OpenRouter citation",
                                },
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
        self.assertEqual(perplexity.OPENROUTER_URL, url)
        # The OpenRouter fallback still speaks Sonar chat completions.
        self.assertEqual("perplexity/sonar-deep-research", payload["model"])
        self.assertIn("messages", payload)
        self.assertEqual("openrouter", artifact["provider"])
        self.assertEqual("sonar", artifact["endpoint"])
        self.assertEqual("perplexity/sonar-deep-research", artifact["model"])
        self.assertNotIn("preset", artifact)
        self.assertEqual("OpenRouter citation", items[1]["title"])

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
        agent_response = _agent_response(
            text="Agent synthesis",
            search_result_items=[[
                {
                    "id": 1,
                    "title": "Citation result",
                    "url": "https://example.com/a",
                    "snippet": "Citation row",
                    "date": "2026-05-15",
                }
            ]],
        )
        with patch(
            "lib.perplexity.http.post",
            side_effect=[search_response, agent_response],
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

    def test_both_mode_keeps_search_rows_when_agent_leg_fails(self):
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

    def test_deep_research_uses_background_run_and_wall_timeout_config(self):
        create_response = {"id": "resp_1", "status": "queued", "created_at": 123}
        complete_response = _agent_response(
            text="Deep synthesis",
            search_result_items=[[
                {
                    "id": 1,
                    "title": "Deep citation",
                    "url": "https://example.com/deep",
                    "snippet": "Deep snippet",
                }
            ]],
            created_at=123,
            completed_at=130,
            usage={"total_tokens": 123, "cost": {"total_cost": 0.12}},
        )
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

        # Background runs use the same endpoint as sync ones.
        self.assertEqual(perplexity.PERPLEXITY_URL, post.call_args.args[0])
        self.assertEqual(
            perplexity.PERPLEXITY_URL,
            perplexity._provider({"PERPLEXITY_API_KEY": "pplx-test"}, deep=True)[2],
        )
        create_payload = post.call_args.args[1]
        self.assertEqual("high", create_payload["preset"])
        self.assertIs(True, create_payload["background"])
        self.assertEqual(f"{perplexity.PERPLEXITY_URL}/resp_1", get.call_args.args[0])
        self.assertEqual("agent-background", artifact["endpoint"])
        self.assertEqual(True, artifact["async"])
        self.assertEqual(300, artifact["asyncTimeoutSeconds"])
        self.assertTrue(artifact["asyncIdempotencyKey"].startswith("last30days:"))
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(123, artifact["asyncCreatedAt"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(0.12, artifact["totalCostUsd"])
        self.assertEqual(123, items[0]["metadata"]["usage"]["total_tokens"])
        self.assertEqual("Deep citation", items[1]["title"])

    def test_deep_research_terminal_on_submit_skips_polling(self):
        complete_response = _agent_response(text="Immediate synthesis", search_result_items=[[]])
        with patch("lib.perplexity.http.post", return_value=complete_response), \
             patch("lib.perplexity.http.get") as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        get.assert_not_called()
        self.assertEqual(0, artifact["asyncPollCount"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual("Immediate synthesis", items[0]["snippet"])

    def test_deep_research_timeout_returns_empty_result(self):
        with patch("lib.perplexity.http.post", return_value={"id": "resp_1", "status": "queued", "created_at": 123}), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "resp_1",
                 "status": "in_progress",
                 "created_at": 123,
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
        self.assertEqual("resp_1", artifact["asyncRequestId"])
        self.assertEqual("in_progress", artifact["asyncStatus"])
        self.assertEqual(1, artifact["asyncTimeoutSeconds"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual("PENDING_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(123, artifact["asyncCreatedAt"])

    def test_deep_research_failed_status_returns_failure_artifact(self):
        with patch("lib.perplexity.http.post", return_value={"id": "resp_1", "status": "queued"}), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "resp_1",
                 "status": "failed",
                 "error": "provider failure",
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("failed", artifact["error"])
        self.assertEqual("resp_1", artifact["asyncRequestId"])
        self.assertEqual("failed", artifact["asyncStatus"])
        self.assertEqual("FAILED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual("provider failure", artifact["asyncErrorMessage"])

    def test_deep_research_cancelled_and_incomplete_are_terminal_failures(self):
        for status in ("cancelled", "incomplete"):
            with self.subTest(status=status):
                with patch("lib.perplexity.http.post", return_value={"id": "resp_1", "status": "queued"}), \
                     patch("lib.perplexity.http.get", return_value={"id": "resp_1", "status": status}):
                    items, artifact = perplexity.search(
                        "test topic",
                        ("2026-05-01", "2026-06-01"),
                        {"PERPLEXITY_API_KEY": "pplx-test"},
                        deep=True,
                    )

                self.assertEqual([], items)
                self.assertEqual("failed", artifact["error"])
                self.assertEqual(status, artifact["asyncStatus"])
                self.assertEqual("FAILED_REMOTE", artifact["asyncLocalStatus"])

    def test_deep_research_poll_error_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "resp_1",
            "status": "queued",
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
        self.assertEqual("resp_1", artifact["asyncRequestId"])
        self.assertEqual("queued", artifact["asyncStatus"])
        self.assertEqual("POLL_ERROR", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(429, artifact["asyncPollStatusCode"])

    def test_deep_research_missing_id_is_an_http_error(self):
        with patch("lib.perplexity.http.post", return_value={"status": "queued"}), \
             patch("lib.perplexity.http.get") as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        # A malformed submit surfaces as HTTPError, which search() swallows into
        # an empty result rather than a partial artifact.
        get.assert_not_called()
        self.assertEqual([], items)
        self.assertEqual({}, artifact)

    def test_deep_research_empty_synthesis_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "resp_1",
            "status": "queued",
            "created_at": 123,
        }) as post, \
             patch("lib.perplexity.http.get", return_value=_agent_response(
                 text=None,
                 search_result_items=[[]],
                 created_at=123,
                 completed_at=130,
                 usage={"total_tokens": 321},
             )):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("empty_synthesis", artifact["error"])
        self.assertEqual("resp_1", artifact["asyncRequestId"])
        self.assertEqual("completed", artifact["asyncStatus"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])
        self.assertEqual(1, artifact["asyncPollCount"])
        self.assertEqual(130, artifact["asyncCompletedAt"])
        self.assertEqual(321, artifact["usage"]["total_tokens"])
        self.assertTrue(artifact["asyncIdempotencyKey"].startswith("last30days:"))
        self.assertEqual(
            "Async Deep Research completed with empty synthesis",
            artifact["asyncErrorMessage"],
        )
        self.assertIs(True, post.call_args.args[1]["background"])

    def test_deep_research_malformed_output_preserves_async_id(self):
        with patch("lib.perplexity.http.post", return_value={
            "id": "resp_1",
            "status": "queued",
            "created_at": 123,
        }), \
             patch("lib.perplexity.http.get", return_value={
                 "id": "resp_1",
                 "status": "completed",
                 "created_at": 123,
                 "completed_at": 130,
                 "output": [None, {"type": "message"}],
             }):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("empty_synthesis", artifact["error"])
        self.assertEqual("resp_1", artifact["asyncRequestId"])
        self.assertEqual("COMPLETED_REMOTE", artifact["asyncLocalStatus"])

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
