import json
import unittest
from typing import get_args

from lib import env
from lib import providers


class ProvidersV3Tests(unittest.TestCase):
    def test_auto_prefers_gemini_with_google_key(self):
        runtime, client = providers.resolve_runtime(
            {"GOOGLE_API_KEY": "test", "LAST30DAYS_REASONING_PROVIDER": "auto"},
            depth="default",
        )
        self.assertEqual("gemini", runtime.reasoning_provider)
        self.assertEqual("gemini", client.name)
        self.assertTrue(runtime.planner_model.startswith("gemini-3.1-"))

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

    def test_auto_falls_back_to_xai(self):
        runtime, client = providers.resolve_runtime(
            {"XAI_API_KEY": "test-key", "LAST30DAYS_REASONING_PROVIDER": "auto"},
            depth="default",
        )
        self.assertEqual("xai", runtime.reasoning_provider)

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
