import json
import unittest
from typing import get_args, get_type_hints
from unittest.mock import patch

from lib import env
from lib import providers
from lib import schema


class ProvidersV3Tests(unittest.TestCase):
    def test_auto_prefers_gemini_with_google_key(self):
        runtime, client = providers.resolve_runtime(
            {"GOOGLE_API_KEY": "test", "LAST30DAYS_REASONING_PROVIDER": "auto"},
            depth="default",
        )
        self.assertEqual("gemini", runtime.reasoning_provider)
        self.assertEqual("gemini", client.name)
        self.assertEqual("gemini-3.5-flash-lite", runtime.planner_model)

    def test_deep_gemini_uses_current_flash_for_reranking(self):
        runtime, _ = providers.resolve_runtime(
            {"GOOGLE_API_KEY": "test", "LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="deep",
        )
        self.assertEqual("gemini-3.5-flash-lite", runtime.planner_model)
        self.assertEqual("gemini-3.6-flash", runtime.rerank_model)

    def test_gemini_model_pins_accept_current_and_older_families(self):
        runtime = providers.mock_runtime(
            {
                "LAST30DAYS_REASONING_PROVIDER": "gemini",
                "LAST30DAYS_PLANNER_MODEL": "gemini-3.6-flash",
                "LAST30DAYS_RERANK_MODEL": "gemini-2.5-flash",
            },
            depth="default",
        )
        self.assertEqual("gemini-3.6-flash", runtime.planner_model)
        self.assertEqual("gemini-2.5-flash", runtime.rerank_model)

    def test_gemini_model_pins_reject_cross_provider_models(self):
        with self.assertRaisesRegex(RuntimeError, "planner must use a Gemini model"):
            providers.mock_runtime(
                {
                    "LAST30DAYS_REASONING_PROVIDER": "gemini",
                    "LAST30DAYS_PLANNER_MODEL": "gpt-5.6-luna",
                },
                depth="default",
            )

    def test_auto_falls_back_to_openai(self):
        runtime, client = providers.resolve_runtime(
            {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_AUTH_STATUS": "ok",
                "LAST30DAYS_REASONING_PROVIDER": "auto",
            },
            depth="default",
        )
        self.assertEqual("openai", runtime.reasoning_provider)
        self.assertEqual("gpt-5.6-luna", runtime.planner_model)

    def test_auto_falls_back_to_xai(self):
        runtime, client = providers.resolve_runtime(
            {"XAI_API_KEY": "test-key", "LAST30DAYS_REASONING_PROVIDER": "auto"},
            depth="default",
        )
        self.assertEqual("xai", runtime.reasoning_provider)
        self.assertEqual("grok-4.5", runtime.planner_model)

    def test_auto_returns_local_runtime_when_no_keys(self):
        runtime, client = providers.resolve_runtime(
            {"LAST30DAYS_REASONING_PROVIDER": "auto"},
            depth="default",
        )
        self.assertEqual("local", runtime.reasoning_provider)
        self.assertEqual("deterministic", runtime.planner_model)
        self.assertEqual("local-score", runtime.rerank_model)
        self.assertIsNone(client)

    def test_explicit_gemini_without_key_still_raises(self):
        with self.assertRaises(RuntimeError):
            providers.resolve_runtime(
                {"LAST30DAYS_REASONING_PROVIDER": "gemini"},
                depth="default",
            )

    def test_explicit_openai_without_key_still_raises(self):
        with self.assertRaises(RuntimeError):
            providers.resolve_runtime(
                {"LAST30DAYS_REASONING_PROVIDER": "openai"},
                depth="default",
            )

    def test_explicit_xai_without_key_still_raises(self):
        with self.assertRaises(RuntimeError):
            providers.resolve_runtime(
                {"LAST30DAYS_REASONING_PROVIDER": "xai"},
                depth="default",
            )

    def test_codex_auth_is_not_supported_as_openai_provider_auth(self):
        self.assertNotIn("codex", get_args(env.AuthSource))
        self.assertFalse(hasattr(env, "AUTH_SOURCE_CODEX"))

    def test_openai_provider_has_no_chatgpt_backend_route(self):
        self.assertFalse(hasattr(providers, "CODEX_RESPONSES_URL"))
        with self.assertRaises(TypeError):
            providers.OpenAIClient("token", "codex", "acct")

    def test_current_gemini_omits_deprecated_sampling_parameters(self):
        response = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        for model in ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-4.0-flash"):
            with self.subTest(model=model):
                with patch("lib.providers.http.post", return_value=response) as post:
                    text = providers.GeminiClient("key").generate_text(
                        model,
                        "prompt",
                        response_mime_type="application/json",
                    )
                body = post.call_args.args[1]
                self.assertEqual("ok", text)
                self.assertEqual(
                    {"responseMimeType": "application/json"},
                    body["generationConfig"],
                )
                self.assertNotIn("temperature", body["generationConfig"])

    def test_older_gemini_pin_keeps_temperature_zero(self):
        response = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        with patch("lib.providers.http.post", return_value=response) as post:
            providers.GeminiClient("key").generate_text("gemini-3.1-flash-lite", "prompt")
        self.assertEqual({"temperature": 0}, post.call_args.args[1]["generationConfig"])

    def test_openai_56_preserves_low_latency_reasoning_contract(self):
        with patch("lib.providers.http.post", return_value={"output_text": "ok"}) as post:
            text = providers.OpenAIClient("token").generate_text("gpt-5.6-luna", "prompt")
        self.assertEqual("ok", text)
        payload = post.call_args.args[1]
        self.assertEqual({"effort": "none"}, payload["reasoning"])
        self.assertEqual(0, payload["temperature"])

    def test_older_openai_pin_does_not_receive_56_only_fields(self):
        with patch("lib.providers.http.post", return_value={"output_text": "ok"}) as post:
            providers.OpenAIClient("token").generate_text("gpt-4.1-mini", "prompt")
        self.assertNotIn("reasoning", post.call_args.args[1])

    def test_grok_45_uses_low_reasoning_for_planning_and_reranking(self):
        with patch("lib.providers.http.post", return_value={"output_text": "ok"}) as post:
            text = providers.XAIClient("token").generate_text("grok-4.5", "prompt")
        self.assertEqual("ok", text)
        self.assertEqual({"effort": "low"}, post.call_args.args[1]["reasoning"])

    def test_older_xai_pin_does_not_receive_45_only_fields(self):
        with patch("lib.providers.http.post", return_value={"output_text": "ok"}) as post:
            providers.XAIClient("token").generate_text("grok-4.3", "prompt")
        self.assertNotIn("reasoning", post.call_args.args[1])

    def test_openrouter_default_tracks_current_flash_lite(self):
        runtime = providers.mock_runtime(
            {"LAST30DAYS_REASONING_PROVIDER": "openrouter"},
            depth="default",
        )
        self.assertEqual("google/gemini-3.5-flash-lite", runtime.planner_model)

    def test_provider_runtime_type_contract_includes_openrouter(self):
        provider_type = get_type_hints(schema.ProviderRuntime)["reasoning_provider"]
        self.assertIn("openrouter", get_args(provider_type))


class TestExtractJson(unittest.TestCase):
    def test_direct_json(self):
        result = providers.extract_json('{"scores": [1, 2]}')
        self.assertEqual(result, {"scores": [1, 2]})

    def test_json_in_markdown_fences(self):
        text = '```json\n{"scores": [1, 2]}\n```'
        result = providers.extract_json(text)
        self.assertEqual(result, {"scores": [1, 2]})

    def test_json_with_surrounding_text(self):
        text = 'Here is the result:\n{"scores": [1]}\nDone.'
        result = providers.extract_json(text)
        self.assertEqual(result, {"scores": [1]})

    def test_empty_text_raises(self):
        with self.assertRaises(ValueError):
            providers.extract_json("")

    def test_no_json_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            providers.extract_json("no json here at all")


class TestExtractOpenAIText(unittest.TestCase):
    def test_output_text_field(self):
        self.assertEqual("hello", providers.extract_openai_text({"output_text": "hello"}))

    def test_choices_message_content(self):
        payload = {"choices": [{"message": {"content": "world"}}]}
        self.assertEqual("world", providers.extract_openai_text(payload))

    def test_output_list_text(self):
        payload = {"output": [{"text": "foo"}]}
        self.assertEqual("foo", providers.extract_openai_text(payload))

    def test_output_content_output_text_type(self):
        payload = {"output": [{"content": [{"type": "output_text", "text": "bar"}]}]}
        self.assertEqual("bar", providers.extract_openai_text(payload))

    def test_output_string_item(self):
        payload = {"output": ["direct string"]}
        self.assertEqual("direct string", providers.extract_openai_text(payload))

    def test_empty_payload_returns_empty(self):
        self.assertEqual("", providers.extract_openai_text({}))


class TestExtractGeminiText(unittest.TestCase):
    def test_standard_response(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "gemini says"}]}}]}
        self.assertEqual("gemini says", providers.extract_gemini_text(payload))

    def test_empty_candidates(self):
        self.assertEqual("", providers.extract_gemini_text({"candidates": []}))

    def test_empty_payload(self):
        self.assertEqual("", providers.extract_gemini_text({}))


if __name__ == "__main__":
    unittest.main()
