"""Static provider catalog and runtime client implementations."""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from . import env, http, schema

GEMINI_FLASH_LITE = "gemini-3.1-flash-lite"
GEMINI_PRO = "gemini-3.1-pro-preview"
OPENAI_DEFAULT = "gpt-5.4-nano"
XAI_DEFAULT = "grok-4-1-fast"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# OpenRouter routes the Gemini Flash Lite tier as the -preview slug; that is the
# stable form on that routing layer even though native Gemini's GEMINI_FLASH_LITE
# constant is suffix-free. If GEMINI_FLASH_LITE moves to a non-preview stable ID,
# double-check that OpenRouter's slug still maps to the same upstream model.
OPENROUTER_DEFAULT = "google/gemini-3.1-flash-lite-preview"


_ENDPOINT_PATHS = {
    OPENAI_RESPONSES_URL: "/responses",
    XAI_RESPONSES_URL: "/responses",
    OPENROUTER_URL: "/chat/completions",
}


def resolve_endpoint(env_var: str, default_url: str) -> str:
    """Resolve a ``*_BASE_URL`` override into a full endpoint URL.

    By the convention every OpenAI-compatible provider documents, ``*_BASE_URL``
    names the API root (``https://host/v1``) and the client appends the endpoint
    path. This module historically required the full endpoint URL instead, so a
    value copied from a provider's setup guide POSTed to the API root and failed.

    Accept both forms: an API root gets the endpoint path appended, and a value
    that already ends with the endpoint path is used unchanged.
    """
    override = os.environ.get(env_var, "").strip()
    if not override:
        return default_url
    override = override.rstrip("/")
    path = _ENDPOINT_PATHS[default_url]
    if override.endswith(path):
        return override
    return override + path


class ReasoningClient:
    """Shared interface for planner and rerank providers."""

    name: str

    def __init__(self) -> None:
        self._usage_calls = 0
        self._usage_prompt_tokens = 0
        self._usage_completion_tokens = 0
        self._usage_complete = True

    @staticmethod
    def _valid_token_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def record_usage(
        self,
        prompt_tokens: Any,
        completion_tokens: Any,
        total_tokens: Any = None,
    ) -> None:
        self._usage_calls += 1
        if not self._valid_token_count(prompt_tokens):
            self._usage_complete = False
            return
        if total_tokens is not None:
            if (
                not self._valid_token_count(total_tokens)
                or total_tokens < prompt_tokens
                or (
                    completion_tokens is not None
                    and (
                        not self._valid_token_count(completion_tokens)
                        or total_tokens < prompt_tokens + completion_tokens
                    )
                )
            ):
                self._usage_complete = False
                return
            # Reported totals can include reasoning tokens absent from completion counts.
            completion_tokens = total_tokens - prompt_tokens
        elif not self._valid_token_count(completion_tokens):
            self._usage_complete = False
            return
        self._usage_prompt_tokens += prompt_tokens
        self._usage_completion_tokens += completion_tokens

    @property
    def total_usage(self) -> dict[str, int] | None:
        if not self._usage_calls or not self._usage_complete:
            return None
        return {
            "calls": self._usage_calls,
            "promptTokens": self._usage_prompt_tokens,
            "completionTokens": self._usage_completion_tokens,
            "totalTokens": self._usage_prompt_tokens + self._usage_completion_tokens,
        }

    def _mark_usage_incomplete(self) -> None:
        self._usage_complete = False

    def _post(self, url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        try:
            return http.post(url, payload, on_retry=self._mark_usage_incomplete, **kwargs)
        except Exception:
            self._mark_usage_incomplete()
            raise

    def _record_response_usage(
        self,
        response: dict[str, Any],
        *,
        metadata_key: str,
        prompt_key: str,
        completion_key: str,
        total_key: str,
        prompt_fallback_key: str | None = None,
        completion_fallback_key: str | None = None,
    ) -> None:
        usage = response.get(metadata_key)
        if not isinstance(usage, dict):
            usage = {}
        prompt_tokens = usage.get(prompt_key)
        completion_tokens = usage.get(completion_key)
        if prompt_tokens is None and prompt_fallback_key:
            prompt_tokens = usage.get(prompt_fallback_key)
        if completion_tokens is None and completion_fallback_key:
            completion_tokens = usage.get(completion_fallback_key)
        self.record_usage(
            prompt_tokens,
            completion_tokens,
            usage.get(total_key),
        )

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> str:
        raise NotImplementedError

    def generate_json(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        text = self.generate_text(model, prompt, tools=tools, response_mime_type="application/json")
        return extract_json(text)


class GeminiClient(ReasoningClient):
    name = "gemini"

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    def _generate_content(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }
        if response_mime_type:
            body["generationConfig"]["responseMimeType"] = response_mime_type
        if tools:
            body["tools"] = tools
        return self._post(
            GEMINI_URL.format(model=model, api_key=self.api_key),
            body,
            headers={"Content-Type": "application/json"},
            timeout=90,
        )

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> str:
        payload = self._generate_content(
            model,
            prompt,
            tools=tools,
            response_mime_type=response_mime_type,
        )
        self._record_response_usage(
            payload,
            metadata_key="usageMetadata",
            prompt_key="promptTokenCount",
            completion_key="candidatesTokenCount",
            total_key="totalTokenCount",
        )
        return extract_gemini_text(payload)

class OpenAIClient(ReasoningClient):
    name = "openai"

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> str:
        del tools, response_mime_type
        payload = {
            "model": model,
            "store": False,
            "input": prompt,
            "temperature": 0,
        }
        response = self._post(
            resolve_endpoint("OPENAI_BASE_URL", OPENAI_RESPONSES_URL),
            payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=90,
        )
        self._record_response_usage(
            response,
            metadata_key="usage",
            prompt_key="input_tokens",
            completion_key="output_tokens",
            total_key="total_tokens",
        )
        return extract_openai_text(response)


class XAIClient(ReasoningClient):
    name = "xai"

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> str:
        del tools, response_mime_type
        payload = {
            "model": model,
            "input": [{"role": "user", "content": prompt}],
        }
        response = self._post(
            resolve_endpoint("XAI_BASE_URL", XAI_RESPONSES_URL),
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
        )
        self._record_response_usage(
            response,
            metadata_key="usage",
            prompt_key="input_tokens",
            completion_key="output_tokens",
            total_key="total_tokens",
            prompt_fallback_key="prompt_tokens",
            completion_fallback_key="completion_tokens",
        )
        return extract_openai_text(response)


class OpenRouterClient(ReasoningClient):
    name = "openrouter"

    def __init__(self, api_key: str):
        super().__init__()
        self.api_key = api_key

    def generate_text(
        self,
        model: str,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        response_mime_type: str | None = None,
    ) -> str:
        del tools, response_mime_type
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        response = self._post(
            resolve_endpoint("OPENROUTER_BASE_URL", OPENROUTER_URL),
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
        )
        self._record_response_usage(
            response,
            metadata_key="usage",
            prompt_key="prompt_tokens",
            completion_key="completion_tokens",
            total_key="total_tokens",
        )
        return extract_openai_text(response)


_MODEL_DEFAULTS: dict[str, tuple[str, str]] = {
    "gemini": (GEMINI_FLASH_LITE, GEMINI_FLASH_LITE),
    "openai": (OPENAI_DEFAULT, OPENAI_DEFAULT),
    "xai": (XAI_DEFAULT, XAI_DEFAULT),
    "openrouter": (OPENROUTER_DEFAULT, OPENROUTER_DEFAULT),
}


def _resolve_model_pins(config: dict[str, Any], depth: str, provider_name: str) -> tuple[str, str, str]:
    """Resolve planner, rerank, and grounding model pins for a provider."""
    default_planner, default_rerank = _MODEL_DEFAULTS.get(provider_name, (GEMINI_FLASH_LITE, GEMINI_FLASH_LITE))
    if depth == "deep" and provider_name == "gemini":
        default_rerank = GEMINI_PRO

    planner_model = config.get("LAST30DAYS_PLANNER_MODEL") or default_planner
    rerank_model = config.get("LAST30DAYS_RERANK_MODEL") or default_rerank

    if provider_name == "gemini":
        _require_gemini_31(planner_model, role="planner")
        _require_gemini_31(rerank_model, role="rerank")

    return planner_model, rerank_model


def mock_runtime(config: dict[str, Any], depth: str) -> schema.ProviderRuntime:
    """Resolve model pins for mock mode without requiring live credentials."""
    provider_name = (config.get("LAST30DAYS_REASONING_PROVIDER") or "gemini").lower()
    if provider_name == "auto":
        provider_name = "gemini"
    if provider_name not in _MODEL_DEFAULTS:
        raise RuntimeError(f"Unsupported reasoning provider: {provider_name}")

    planner_model, rerank_model = _resolve_model_pins(config, depth, provider_name)
    return schema.ProviderRuntime(
        reasoning_provider=provider_name,
        planner_model=planner_model,
        rerank_model=rerank_model,

        x_search_backend=_resolve_x_backend(config),
    )


def resolve_runtime(config: dict[str, Any], depth: str) -> tuple[schema.ProviderRuntime, ReasoningClient | None]:
    """Resolve the reasoning provider and pinned models."""
    provider_name = (config.get("LAST30DAYS_REASONING_PROVIDER") or "auto").lower()
    google_key = config.get("GOOGLE_API_KEY") or config.get("GEMINI_API_KEY") or config.get("GOOGLE_GENAI_API_KEY")
    openai_token = config.get("OPENAI_API_KEY")
    xai_key = config.get("XAI_API_KEY")

    if provider_name == "auto":
        if google_key:
            provider_name = "gemini"
        elif openai_token and config.get("OPENAI_AUTH_STATUS") == env.AUTH_STATUS_OK:
            provider_name = "openai"
        elif xai_key:
            provider_name = "xai"
        elif config.get("OPENROUTER_API_KEY"):
            provider_name = "openrouter"
        else:
            return schema.ProviderRuntime(
                reasoning_provider="local",
                planner_model="deterministic",
                rerank_model="local-score",
                x_search_backend=_resolve_x_backend(config),
            ), None

    planner_model, rerank_model = _resolve_model_pins(config, depth, provider_name)

    if provider_name == "gemini":
        if not google_key:
            raise RuntimeError("Gemini selected but no Google API key is configured.")
        runtime = schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model=planner_model,
            rerank_model=rerank_model,
    
            x_search_backend=_resolve_x_backend(config),
        )
        return runtime, GeminiClient(google_key)

    if provider_name == "openai":
        if not openai_token or config.get("OPENAI_AUTH_STATUS") != env.AUTH_STATUS_OK:
            raise RuntimeError("OpenAI selected but no valid OpenAI auth is configured.")
        runtime = schema.ProviderRuntime(
            reasoning_provider="openai",
            planner_model=planner_model,
            rerank_model=rerank_model,
    
            x_search_backend=_resolve_x_backend(config),
        )
        return runtime, OpenAIClient(
            openai_token
        )

    if provider_name == "xai":
        if not xai_key:
            raise RuntimeError("xAI selected but XAI_API_KEY is not configured.")
        runtime = schema.ProviderRuntime(
            reasoning_provider="xai",
            planner_model=planner_model,
            rerank_model=rerank_model,
    
            x_search_backend=_resolve_x_backend(config),
        )
        return runtime, XAIClient(xai_key)

    if provider_name == "openrouter":
        openrouter_key = config.get("OPENROUTER_API_KEY")
        if not openrouter_key:
            raise RuntimeError("OpenRouter selected but OPENROUTER_API_KEY is not configured.")
        runtime = schema.ProviderRuntime(
            reasoning_provider="openrouter",
            planner_model=planner_model,
            rerank_model=rerank_model,
            x_search_backend=_resolve_x_backend(config),
        )
        return runtime, OpenRouterClient(openrouter_key)

    raise RuntimeError(f"Unsupported reasoning provider: {provider_name}")


def _resolve_x_backend(config: dict[str, Any]) -> str | None:
    """Resolve the X backend for runtime fetch.

    Delegates to env.get_x_source which handles:
    - Any known pin (X_BACKEND_KNOWN) exclusively: returns pin if available, None otherwise
    - Unpinned: walks auto-chain (X_BACKEND_ORDER) only, never auto-selects opt-in backends
    """
    return env.get_x_source(config)


def _require_gemini_31(model: str, *, role: str) -> None:
    if model.startswith("gemini-3.1-"):
        return
    raise RuntimeError(
        f"{role} must use a Gemini 3.1 model. Got: {model}"
    )


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    text = text.strip()
    if not text:
        raise ValueError("Expected JSON response, got empty text")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        return json.loads(match.group(0))


def extract_gemini_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates", []):
        content = candidate.get("content") or {}
        for part in content.get("parts", []):
            text = part.get("text")
            if text:
                return text
    if payload:
        print(f"[Providers] extract_gemini_text: no text in payload keys: {list(payload.keys())}", file=sys.stderr)
    return ""


def extract_openai_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    output = payload.get("output") or payload.get("choices") or []
    for item in output:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            if isinstance(item.get("text"), str):
                return item["text"]
            content = item.get("content") or []
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        return part["text"]
                    if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        return part["text"]
            message = item.get("message") or {}
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    if payload:
        print(f"[Providers] extract_openai_text: no text in payload keys: {list(payload.keys())}", file=sys.stderr)
    return ""
