import unittest
from unittest.mock import patch

from lib import perplexity


def _agent_response(
    text: str = "Agent synthesis",
    *,
    response_id: str = "agent-1",
    status: str = "completed",
    model: str = "perplexity/sonar",
    citations: list[dict] | None = None,
    include_output_text: bool = True,
) -> dict:
    citation_parts = [
        {
            "type": "output_text",
            "text": text,
            "annotations": [
                {
                    "url_citation": {
                        "url": citation["url"],
                        "title": citation.get("title", ""),
                    }
                }
                for citation in citations or []
            ],
        }
    ]
    response = {
        "id": response_id,
        "status": status,
        "model": model,
        "output": [
            {"type": "message", "content": citation_parts},
            {"type": "search_results", "results": citations or []},
        ],
        "usage": {"total_tokens": 123},
    }
    if include_output_text:
        response["output_text"] = text
    return response


class PerplexityAgentTests(unittest.TestCase):
    def test_controlled_agent_uses_direct_key_and_explicit_web_search(self):
        citations = [
            {
                "title": "Example A",
                "url": "https://example.com/a",
                "date": "2026-06-01",
                "snippet": "Direct source snippet",
            }
        ]
        response = _agent_response("Direct synthesis", citations=citations)
        config = {
            "PERPLEXITY_API_KEY": "pplx-test",
            "OPENROUTER_API_KEY": "or-test",
            "LAST30DAYS_PERPLEXITY_MAX_RESULTS": "3",
            "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE": "low",
            "LAST30DAYS_PERPLEXITY_COUNTRY": "us",
            "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER": "example.com,example.org",
            "LAST30DAYS_PERPLEXITY_RECENCY_FILTER": "year",
            "LAST30DAYS_PERPLEXITY_REASONING_EFFORT": "high",
            "LAST30DAYS_PERPLEXITY_AGENT_MAX_STEPS": "4",
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                config,
            )

        url, payload = post.call_args.args[:2]
        headers = post.call_args.kwargs["headers"]
        tool = payload["tools"][0]
        self.assertEqual(perplexity.PERPLEXITY_AGENT_URL, url)
        self.assertEqual("Bearer pplx-test", headers["Authorization"])
        self.assertEqual(1, post.call_args.kwargs["retries"])
        self.assertEqual("perplexity/sonar", payload["model"])
        self.assertIn("input", payload)
        self.assertIn("instructions", payload)
        self.assertEqual(4, payload["max_steps"])
        self.assertEqual({"effort": "high"}, payload["reasoning"])
        self.assertEqual({"type": "web_search"}, payload["tool_choice"])
        self.assertEqual("web_search", tool["type"])
        self.assertEqual(3, tool["max_results"])
        self.assertEqual("low", tool["search_context_size"])
        self.assertEqual({"country": "US"}, tool["user_location"])
        self.assertEqual(
            ["example.com", "example.org"],
            tool["filters"]["search_domain_filter"],
        )
        self.assertEqual("05/01/2026", tool["filters"]["search_after_date_filter"])
        self.assertEqual("06/01/2026", tool["filters"]["search_before_date_filter"])
        self.assertNotIn("search_recency_filter", tool["filters"])
        self.assertNotIn("preset", payload)

        self.assertEqual("perplexity", artifact["provider"])
        self.assertEqual("agent", artifact["mode"])
        self.assertEqual("agent", artifact["endpoint"])
        self.assertEqual("perplexity/sonar", artifact["model"])
        self.assertEqual(perplexity.PERPLEXITY_CONTROLLED_PROFILE, artifact["profile"])
        self.assertFalse(artifact["dynamicPreset"])
        self.assertEqual("agent-1", artifact["responseId"])
        self.assertEqual("perplexity/sonar", artifact["servedModel"])
        self.assertEqual(["message", "search_results"], artifact["outputTypes"])
        self.assertNotIn("input", artifact["request"])
        self.assertNotIn("instructions", artifact["request"])
        self.assertEqual({"effort": "high"}, artifact["request"]["reasoning"])
        self.assertEqual(
            {"type": "web_search"},
            artifact["request"]["tool_choice"],
        )
        self.assertEqual("Example A", items[1]["title"])
        self.assertEqual("Direct source snippet", items[1]["snippet"])

    def test_legacy_sonar_mode_is_an_agent_alias(self):
        response = _agent_response()
        with patch("lib.perplexity.http.post", return_value=response) as post:
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_MODE": "sonar",
                    "LAST30DAYS_PERPLEXITY_MODEL": "sonar-pro",
                },
            )

        self.assertEqual(perplexity.PERPLEXITY_AGENT_URL, post.call_args.args[0])
        self.assertEqual(1, post.call_args.kwargs["retries"])
        self.assertEqual("perplexity/sonar", post.call_args.args[1]["model"])
        self.assertEqual("agent", artifact["mode"])

    def test_controlled_agent_max_steps_clamps_to_local_safety_limit(self):
        payload, _ = perplexity._build_agent_payload(
            "test topic",
            ("2026-05-01", "2026-06-01"),
            {"LAST30DAYS_PERPLEXITY_AGENT_MAX_STEPS": "100"},
            deep=False,
        )

        self.assertEqual(15, payload["max_steps"])

    def test_explicit_anthropic_model_gets_required_output_token_budget(self):
        payload, selection = perplexity._build_agent_payload(
            "test topic",
            ("2026-05-01", "2026-06-01"),
            {
                "LAST30DAYS_PERPLEXITY_AGENT_MODEL": "anthropic/claude-sonnet-4-6",
                "LAST30DAYS_PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS": "8192",
            },
            deep=False,
        )

        self.assertEqual(8192, payload["max_output_tokens"])
        self.assertEqual(8192, selection["request"]["max_output_tokens"])

    def test_non_anthropic_model_omits_output_token_override(self):
        payload, _ = perplexity._build_agent_payload(
            "test topic",
            ("2026-05-01", "2026-06-01"),
            {},
            deep=False,
        )

        self.assertNotIn("max_output_tokens", payload)

    def test_explicit_agent_preset_is_marked_dynamic(self):
        response = _agent_response(model="openai/gpt-5.6-luna")
        with patch("lib.perplexity.http.post", return_value=response) as post:
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_AGENT_PRESET": "low",
                },
            )

        payload = post.call_args.args[1]
        self.assertEqual("low", payload["preset"])
        self.assertIn("input", payload)
        self.assertNotIn("model", payload)
        self.assertEqual("web_search", payload["tools"][0]["type"])
        self.assertEqual(10, payload["tools"][0]["max_results"])
        self.assertTrue(artifact["dynamicPreset"])
        self.assertEqual("low", artifact["preset"])
        self.assertEqual("openai/gpt-5.6-luna", artifact["servedModel"])
        self.assertEqual(perplexity.PERPLEXITY_PRESET_PROFILE, artifact["profile"])

    def test_agent_falls_back_to_message_output_when_output_text_is_missing(self):
        citations = [
            {
                "title": "Example A",
                "url": "https://example.com/a",
                "snippet": "Direct source snippet",
            }
        ]
        response = _agent_response(
            "Message-only synthesis",
            citations=citations,
            include_output_text=False,
        )
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual("Message-only synthesis", items[0]["snippet"])
        self.assertEqual(1, artifact["citationCount"])

    def test_malformed_agent_output_returns_safe_empty_artifact(self):
        response = {
            "id": "agent-empty",
            "status": "completed",
            "model": "perplexity/sonar",
            "output": [{"type": "function_call", "arguments": "untrusted trace"}],
            "usage": {"total_tokens": 2},
        }
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual([], items)
        self.assertEqual("empty_synthesis", artifact["error"])
        self.assertEqual("perplexity/sonar", artifact["servedModel"])
        self.assertEqual(["function_call"], artifact["outputTypes"])
        self.assertNotIn("arguments", artifact)

    def test_sync_terminal_failure_keeps_safe_response_metadata(self):
        response = {
            "id": "agent-failed",
            "status": "failed",
            "error": {"message": "Provider safely rejected this request"},
            "output": [{"type": "function_call", "arguments": "raw tool trace"}],
            "usage": {"total_tokens": 7},
        }
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual([], items)
        self.assertEqual("failed", artifact["error"])
        self.assertEqual("agent-failed", artifact["responseId"])
        self.assertEqual("failed", artifact["status"])
        self.assertEqual("Provider safely rejected this request", artifact["agentErrorMessage"])
        self.assertEqual({"total_tokens": 7}, artifact["usage"])
        self.assertEqual(["function_call"], artifact["outputTypes"])
        self.assertNotIn("arguments", artifact)

    def test_sync_incomplete_response_keeps_safe_reason(self):
        response = {
            "id": "agent-incomplete",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [],
            "usage": {"total_tokens": 11},
        }
        with patch("lib.perplexity.http.post", return_value=response):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
            )

        self.assertEqual([], items)
        self.assertEqual("incomplete", artifact["error"])
        self.assertEqual("max_output_tokens", artifact["incompleteReason"])
        self.assertNotIn("incomplete_details", artifact)

    def test_openrouter_only_config_uses_sonar_fallback(self):
        response = {
            "id": "openrouter-1",
            "model": "perplexity/sonar-pro",
            "choices": [
                {
                    "message": {
                        "content": "OpenRouter fallback synthesis",
                        "annotations": [
                            {
                                "url_citation": {
                                    "url": "https://example.com/openrouter",
                                    "title": "OpenRouter citation",
                                }
                            }
                        ],
                    }
                }
            ],
            "usage": {"total_tokens": 42},
        }
        with patch("lib.perplexity.http.post", return_value=response) as post, patch(
            "lib.perplexity.http.get"
        ) as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"OPENROUTER_API_KEY": "or-test"},
            )

        url, payload = post.call_args.args[:2]
        self.assertEqual(perplexity.OPENROUTER_URL, url)
        self.assertEqual("perplexity/sonar-pro", payload["model"])
        self.assertEqual("Bearer or-test", post.call_args.kwargs["headers"]["Authorization"])
        self.assertEqual(1, post.call_args.kwargs["retries"])
        get.assert_not_called()
        self.assertEqual("openrouter", artifact["provider"])
        self.assertEqual("sonar", artifact["mode"])
        self.assertEqual("openrouter-chat-completions", artifact["endpoint"])
        self.assertEqual("OpenRouter fallback synthesis", items[0]["snippet"])
        self.assertEqual("OpenRouter citation", items[1]["title"])

    def test_openrouter_search_mode_degrades_to_sonar_fallback(self):
        response = {
            "id": "openrouter-2",
            "model": "perplexity/sonar-pro",
            "choices": [{"message": {"content": "Fallback synthesis"}}],
        }
        with patch("lib.perplexity.http.post", return_value=response) as post:
            _, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "OPENROUTER_API_KEY": "or-test",
                    "LAST30DAYS_PERPLEXITY_MODE": "search",
                },
            )

        self.assertEqual(perplexity.OPENROUTER_URL, post.call_args.args[0])
        self.assertEqual("openrouter", artifact["provider"])
        self.assertEqual("sonar", artifact["mode"])

    def test_openrouter_deep_research_keeps_synchronous_legacy_fallback(self):
        response = {
            "id": "openrouter-deep-1",
            "model": "perplexity/sonar-deep-research",
            "choices": [{"message": {"content": "Deep fallback synthesis"}}],
        }
        with patch("lib.perplexity.http.post", return_value=response) as post, patch(
            "lib.perplexity.http.get"
        ) as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"OPENROUTER_API_KEY": "or-test"},
                deep=True,
            )

        self.assertEqual(perplexity.OPENROUTER_URL, post.call_args.args[0])
        self.assertEqual(
            "perplexity/sonar-deep-research",
            post.call_args.args[1]["model"],
        )
        get.assert_not_called()
        self.assertEqual("Deep fallback synthesis", items[0]["snippet"])
        self.assertTrue(artifact["deep"])
        self.assertEqual("openrouter", artifact["provider"])

    def test_openrouter_failure_keeps_typed_source_receipt(self):
        with patch(
            "lib.perplexity.http.post",
            side_effect=perplexity.http.HTTPError(
                "HTTP 429: Too Many Requests",
                status_code=429,
            ),
        ):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"OPENROUTER_API_KEY": "or-test"},
            )

        self.assertEqual([], items)
        self.assertEqual("openrouter", artifact["provider"])
        self.assertEqual("openrouter-chat-completions", artifact["endpoint"])
        self.assertEqual(429, artifact["statusCode"])

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
        self.assertEqual(1, post.call_args.kwargs["retries"])
        self.assertEqual("test topic", payload["query"])
        self.assertEqual(3, payload["max_results"])
        self.assertEqual("low", payload["search_context_size"])
        self.assertEqual("US", payload["country"])
        self.assertEqual(["example.com", "example.org"], payload["search_domain_filter"])
        self.assertEqual(["en"], payload["search_language_filter"])
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
            citations=[
                {
                    "title": "Citation result",
                    "url": "https://example.com/a",
                    "snippet": "Citation row",
                    "date": "2026-05-15",
                }
            ]
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
        self.assertEqual(perplexity.PERPLEXITY_AGENT_URL, post.call_args_list[1].args[0])
        self.assertEqual("both", artifact["mode"])
        self.assertEqual(3, artifact["itemCount"])
        self.assertEqual("perplexity.ai", items[0]["source_domain"])
        urls = [item["url"] for item in items if item["url"]]
        self.assertEqual(["https://example.com/a", "https://example.com/unique"], urls)
        self.assertIn("agent", artifact)
        self.assertNotIn("sonar", artifact)

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
        self.assertEqual("HTTPError", artifact["agent"]["error"])
        self.assertEqual(500, artifact["agent"]["statusCode"])

    def test_deep_research_uses_agent_background_high_preset(self):
        created = {"id": "agent-deep-1", "status": "queued"}
        completed = _agent_response(
            "Deep synthesis",
            response_id="agent-deep-1",
            citations=[
                {
                    "title": "Deep citation",
                    "url": "https://example.com/deep",
                    "snippet": "Deep snippet",
                }
            ],
        )
        with patch("lib.perplexity.http.post", return_value=created) as post, patch(
            "lib.perplexity.http.get",
            return_value=completed,
        ) as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "300",
                },
                deep=True,
            )

        self.assertEqual(perplexity.PERPLEXITY_AGENT_URL, post.call_args.args[0])
        payload = post.call_args.args[1]
        self.assertEqual(1, post.call_args.kwargs["retries"])
        self.assertEqual("high", payload["preset"])
        self.assertTrue(payload["background"])
        self.assertNotIn("model", payload)
        tool = payload["tools"][0]
        self.assertEqual("web_search", tool["type"])
        self.assertEqual(10, tool["max_results"])
        self.assertEqual("05/01/2026", tool["filters"]["search_after_date_filter"])
        self.assertEqual("06/01/2026", tool["filters"]["search_before_date_filter"])
        self.assertEqual(
            f"{perplexity.PERPLEXITY_AGENT_URL}/agent-deep-1",
            get.call_args.args[0],
        )
        self.assertEqual("agent-background", artifact["endpoint"])
        self.assertTrue(artifact["background"])
        self.assertEqual(300, artifact["backgroundTimeoutSeconds"])
        self.assertEqual(1, artifact["backgroundPollCount"])
        self.assertEqual("COMPLETED_REMOTE", artifact["backgroundLocalStatus"])
        self.assertEqual("high", artifact["preset"])
        self.assertTrue(artifact["dynamicPreset"])
        self.assertEqual(123, items[0]["metadata"]["usage"]["total_tokens"])

    def test_deep_research_timeout_preserves_background_response_id(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ) as post, patch(
            "lib.perplexity.time.monotonic",
            side_effect=[0, 1],
        ), patch("lib.perplexity.http.get") as get:
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "1",
                },
                deep=True,
            )

        post.assert_called_once()
        get.assert_not_called()
        self.assertEqual([], items)
        self.assertEqual("timeout", artifact["error"])
        self.assertEqual("agent-deep-1", artifact["responseId"])
        self.assertEqual("queued", artifact["backgroundStatus"])
        self.assertEqual(1, artifact["backgroundTimeoutSeconds"])
        self.assertEqual(0, artifact["backgroundPollCount"])
        self.assertEqual("PENDING_REMOTE", artifact["backgroundLocalStatus"])

    def test_deep_research_nonterminal_poll_after_deadline_is_timeout(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ), patch(
            "lib.perplexity.http.get",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ) as get, patch(
            "lib.perplexity.time.monotonic",
            side_effect=[0.0, 0.5, 1.5],
        ):
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
        self.assertEqual("PENDING_REMOTE", artifact["backgroundLocalStatus"])
        self.assertEqual(1, artifact["backgroundPollCount"])
        self.assertEqual(1.0, get.call_args.kwargs["deadline_monotonic"])

    def test_deep_research_poll_retry_deadline_is_timeout_not_poll_error(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ), patch(
            "lib.perplexity.http.get",
            side_effect=perplexity.http.DeadlineExceeded(),
        ), patch(
            "lib.perplexity.time.monotonic",
            side_effect=[0.0, 0.5],
        ):
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
        self.assertEqual("PENDING_REMOTE", artifact["backgroundLocalStatus"])
        self.assertEqual(1, artifact["backgroundPollCount"])

    def test_deep_research_early_transport_timeout_is_poll_error(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ), patch(
            "lib.perplexity.http.get",
            side_effect=perplexity.http.HTTPError(
                "URL Error: timed out",
                outcome_state=perplexity.health.TIMEOUT,
            ),
        ), patch(
            "lib.perplexity.time.monotonic",
            side_effect=[0.0, 10.0, 10.0],
        ):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {
                    "PERPLEXITY_API_KEY": "pplx-test",
                    "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "600",
                },
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("poll_error", artifact["error"])
        self.assertEqual("POLL_ERROR", artifact["backgroundLocalStatus"])

    def test_deep_research_terminal_poll_after_deadline_is_timeout(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ), patch(
            "lib.perplexity.http.get",
            return_value={"id": "agent-deep-1", "status": "completed"},
        ), patch(
            "lib.perplexity.time.monotonic",
            side_effect=[0.0, 0.5, 1.5],
        ):
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
        self.assertEqual("completed", artifact["backgroundStatus"])
        self.assertEqual("PENDING_REMOTE", artifact["backgroundLocalStatus"])

    def test_deep_research_terminal_failures_are_recorded(self):
        for status in ("failed", "cancelled", "incomplete"):
            response = {
                "id": "agent-deep-1",
                "status": status,
                "model": "openai/gpt-5.6-sol",
                "usage": {"total_tokens": 17},
                "output": [{"type": "message", "content": []}],
            }
            if status == "incomplete":
                response["incomplete_details"] = {"reason": "max_output_tokens"}
            with self.subTest(status=status), patch(
                "lib.perplexity.http.post",
                return_value=response,
            ) as post, patch("lib.perplexity.http.get") as get:
                items, artifact = perplexity.search(
                    "test topic",
                    ("2026-05-01", "2026-06-01"),
                    {"PERPLEXITY_API_KEY": "pplx-test"},
                    deep=True,
                )

            post.assert_called_once()
            get.assert_not_called()
            self.assertEqual([], items)
            self.assertEqual("failed", artifact["error"])
            self.assertEqual(status, artifact["backgroundStatus"])
            self.assertEqual("TERMINAL_REMOTE", artifact["backgroundLocalStatus"])
            self.assertEqual("openai/gpt-5.6-sol", artifact["servedModel"])
            self.assertEqual({"total_tokens": 17}, artifact["usage"])
            self.assertEqual(["message"], artifact["outputTypes"])
            if status == "incomplete":
                self.assertEqual("max_output_tokens", artifact["incompleteReason"])

    def test_deep_research_logs_safe_receipt_for_cli_consumers(self):
        response = {
            "id": "agent-deep-1",
            "status": "incomplete",
            "model": "openai/gpt-5.6-sol",
            "incomplete_details": {"reason": "max_output_tokens"},
        }
        with patch("lib.perplexity.http.post", return_value=response), patch(
            "lib.perplexity._log",
        ) as logger:
            perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        receipt = next(
            call.args[0]
            for call in logger.call_args_list
            if call.args and call.args[0].startswith("Deep Research receipt:")
        )
        self.assertIn("model=openai/gpt-5.6-sol", receipt)
        self.assertIn("response_id=agent-deep-1", receipt)
        self.assertIn("provider_status=incomplete", receipt)
        self.assertIn("incomplete_reason=max_output_tokens", receipt)

    def test_deep_research_poll_error_preserves_response_id(self):
        with patch(
            "lib.perplexity.http.post",
            return_value={"id": "agent-deep-1", "status": "queued"},
        ), patch(
            "lib.perplexity.http.get",
            side_effect=perplexity.http.HTTPError(
                "HTTP 429: Too Many Requests",
                status_code=429,
            ),
        ):
            items, artifact = perplexity.search(
                "test topic",
                ("2026-05-01", "2026-06-01"),
                {"PERPLEXITY_API_KEY": "pplx-test"},
                deep=True,
            )

        self.assertEqual([], items)
        self.assertEqual("poll_error", artifact["error"])
        self.assertEqual("agent-deep-1", artifact["responseId"])
        self.assertEqual("queued", artifact["backgroundStatus"])
        self.assertEqual("POLL_ERROR", artifact["backgroundLocalStatus"])
        self.assertEqual(1, artifact["backgroundPollCount"])
        self.assertEqual(429, artifact["backgroundPollStatusCode"])

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


if __name__ == "__main__":
    unittest.main()
