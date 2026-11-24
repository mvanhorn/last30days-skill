"""Static provider catalog and runtime client implementations."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import sys
import time
from threading import Event
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from . import env, http, proxy, schema

GEMINI_FLASH_LITE = "gemini-3.1-flash-lite"
GEMINI_PRO = "gemini-3.1-pro-preview"
OPENAI_DEFAULT = "gpt-5.4-nano"
XAI_DEFAULT = "grok-4.20-0309-non-reasoning"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_MODEL_RE = re.compile(r"gemini-([0-9]+)(?:\.([0-9]+))?(?:-[A-Za-z0-9._-]+)?", re.ASCII)
_GEMINI_MODEL_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# OpenRouter routes the Gemini Flash Lite tier as the -preview slug; that is the
# stable form on that routing layer even though native Gemini's GEMINI_FLASH_LITE
# constant is suffix-free. If GEMINI_FLASH_LITE moves to a non-preview stable ID,
# double-check that OpenRouter's slug still maps to the same upstream model.
OPENROUTER_DEFAULT = "google/gemini-3.1-flash-lite-preview"
PROVIDER_BASE_URL_KEYS = frozenset({"OPENAI_BASE_URL", "XAI_BASE_URL", "OPENROUTER_BASE_URL", "SCRAPECREATORS_BASE_URL"})


def _is_loopback(host: str) -> bool:
    """True for hosts that never leave the machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def allowed_base_url_override(value: str) -> bool:
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return False
    return bool(host) and (
        parts.scheme == "https" or (parts.scheme == "http" and _is_loopback(host))
    )


def is_loopback_http_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and _is_loopback(parts.hostname or "")


def base_url_override(key: str, default: str) -> str:
    """Resolve a provider base-URL override, refusing cleartext remote hosts.

    Every provider resolves its endpoint through here so one guard covers both
    ways a value arrives: the process environment, and
    ``_propagate_config_to_environ`` pushing a `.env` value into os.environ.

    A base-URL override redirects the request that carries the provider's bearer
    token, so an `http://` override to a remote host would put the API key on
    the wire in cleartext. That is refused and the built-in vendor endpoint is
    used instead - failing closed protects the credential, and the warning makes
    the drop visible instead of leaving the user to wonder why their gateway is
    being bypassed. Loopback is the one legitimate `http://` case (a local
    LiteLLM/Ollama gateway or an SSH tunnel never leaves the machine), so it is
    allowed.
    """
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    if allowed_base_url_override(raw):
        return raw
    sys.stderr.write(
        f"[last30days] WARNING: ignoring {key} - a provider endpoint override "
        "must be https:// (http:// is allowed only on localhost), otherwise the API "
        f"key would be sent in cleartext. Using {default} instead.\n"
    )
    sys.stderr.flush()
    return default


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

    Accept both forms: a host or versioned API root gets the endpoint path
    appended, and a complete gateway route is used unchanged. Query strings
    stay after the path in either form.
    """
    override = base_url_override(env_var, default_url)
    if override == default_url:
        return default_url
    parts = urlsplit(override)
    root_path = parts.path.rstrip("/")
    last_segment = root_path.rsplit("/", 1)[-1]
    if not root_path or re.fullmatch(r"v\d+(?:beta\d*)?", last_segment):
        return urlunsplit(parts._replace(path=root_path + _ENDPOINT_PATHS[default_url]))
    return override


class ReasoningClient:
    """Shared interface for planner and rerank providers."""

    name: str

    def __init__(self) -> None:
        self._usage_calls = 0
        self._usage_prompt_tokens = 0
        self._usage_completion_tokens = 0
        self._usage_complete = True
        self._request_deadline: float | None = None
        self._request_cancel: Event | None = None

    def set_request_budget(self, deadline: float, cancel: Event | None) -> None:
        self._request_deadline = deadline
        self._request_cancel = cancel

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
        if self._request_deadline is not None:
            now = time.monotonic()
            if now >= self._request_deadline or (
                self._request_cancel is not None and self._request_cancel.is_set()
            ):
                raise http.DeadlineExceeded()
            kwargs["deadline_monotonic"] = min(self._request_deadline, now + kwargs["timeout"])
            kwargs["cancel"] = self._request_cancel
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
        safe_model = _validate_gemini_model_segment(model)
        body: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0},
        }
        if response_mime_type:
            body["generationConfig"]["responseMimeType"] = response_mime_type
        if tools:
            body["tools"] = tools
        url = GEMINI_URL.format(model=safe_model)
        expected_origin = http._request_origin(url)

        def _same_origin_redirect(newurl: str) -> bool:
            try:
                return http._request_origin(newurl) == expected_origin
            except ValueError:
                return False

        return self._post(
            url,
            body,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
            timeout=90,
            redirect_guard=_same_origin_redirect,
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
        endpoint = resolve_endpoint("OPENAI_BASE_URL", OPENAI_RESPONSES_URL)
        response = self._post(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=90,
            bypass_proxy=is_loopback_http_endpoint(endpoint),
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
        endpoint = resolve_endpoint("XAI_BASE_URL", XAI_RESPONSES_URL)
        response = self._post(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
            bypass_proxy=is_loopback_http_endpoint(endpoint),
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
        endpoint = resolve_endpoint("OPENROUTER_BASE_URL", OPENROUTER_URL)
        response = self._post(
            endpoint,
            payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=90,
            bypass_proxy=is_loopback_http_endpoint(endpoint),
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
        if not _is_gemini_31_or_newer(planner_model):
            _warn_stale_gemini_pin(planner_model, default_planner, role="planner")
            planner_model = default_planner
        if not _is_gemini_31_or_newer(rerank_model):
            _warn_stale_gemini_pin(rerank_model, default_rerank, role="rerank")
            rerank_model = default_rerank

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
        # An xAI lane served by the API-key proxy ranks after every provider
        # the user holds a key for.
        xai_proxied = xai_key == proxy.SENTINEL
        if google_key:
            provider_name = "gemini"
        elif openai_token and config.get("OPENAI_AUTH_STATUS") == env.AUTH_STATUS_OK:
            provider_name = "openai"
        elif xai_key and not xai_proxied:
            provider_name = "xai"
        elif config.get("OPENROUTER_API_KEY"):
            provider_name = "openrouter"
        elif xai_key:
            provider_name = "xai"
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


def _validate_gemini_model_segment(model: str) -> str:
    """Validate and percent-encode a Gemini model name used as a URL path segment."""
    if (
        not isinstance(model, str)
        or not _GEMINI_MODEL_SEGMENT_RE.fullmatch(model)
        or model in {".", ".."}
        or ".." in model
    ):
        raise ValueError(f"Invalid Gemini model name: {model!r}")
    return quote(model, safe="")


def _is_gemini_31_or_newer(model: str) -> bool:
    if not isinstance(model, str) or ".." in model:
        return False
    match = _GEMINI_MODEL_RE.fullmatch(model)
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2) or 0)) >= (3, 1)


def _warn_stale_gemini_pin(model: str, default: str, *, role: str) -> None:
    print(
        f"[Providers] {role} model {model!r} is not Gemini 3.1 or newer; using {default}.",
        file=sys.stderr,
    )


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    text = text.strip()
    if not text:
        raise ValueError("Expected JSON response, got empty text")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            raise
        value = json.loads(match.group(0))
    # Valid JSON is not always an object (Gemini's JSON mode allows a
    # top-level array). Callers read it with .get(), so a list, null, or
    # number must fail here, where their ValueError fallback catches it.
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object, got {type(value).__name__}")
    return value


def extract_gemini_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            content = candidate.get("content")
            if not isinstance(content, dict):
                continue
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            texts = [
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and part.get("thought") is not True
                and isinstance(part.get("text"), str)
                and part["text"]
            ]
            joined = "".join(texts)
            if joined.strip():
                return joined
    if payload:
        print(f"[Providers] extract_gemini_text: no text in payload keys: {list(payload.keys())}", file=sys.stderr)
    return ""


_OPENAI_REASONING_PART_TYPES = frozenset({"reasoning", "reasoning_text", "summary_text", "thinking"})
_OPENAI_TEXT_PART_TYPES = frozenset({"output_text", "text"})


def _extract_openai_content_parts(content: list[Any]) -> str:
    """Extract joined non-reasoning text from a list of OpenAI/OpenRouter content parts."""
    preferred: list[str] = []
    fallback: list[str] = []
    for part in content:
        if isinstance(part, str):
            if part:
                fallback.append(part)
            continue
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type in _OPENAI_REASONING_PART_TYPES or part.get("thought") is True:
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text:
            continue
        if part_type in _OPENAI_TEXT_PART_TYPES:
            preferred.append(text)
        else:
            fallback.append(text)
    preferred_joined = "".join(preferred)
    if preferred_joined.strip():
        return preferred_joined
    fallback_joined = "".join(fallback)
    if fallback_joined.strip():
        return fallback_joined
    return ""


def extract_openai_text(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    containers: list[Any] = []
    for key in ("output", "choices"):
        seq = payload.get(key)
        if isinstance(seq, list):
            containers.extend(seq)
    for item in containers:
        if isinstance(item, str):
            if item.strip():
                return item
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning":
            continue
        item_text = item.get("text")
        if isinstance(item_text, str) and item_text.strip():
            return item_text
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            extracted = _extract_openai_content_parts(content)
            if extracted:
                return extracted
        message = item.get("message")
        if isinstance(message, dict):
            msg_content = message.get("content")
            if isinstance(msg_content, str) and msg_content.strip():
                return msg_content
            if isinstance(msg_content, list):
                extracted = _extract_openai_content_parts(msg_content)
                if extracted:
                    return extracted
    if payload:
        print(f"[Providers] extract_openai_text: no text in payload keys: {list(payload.keys())}", file=sys.stderr)
    return ""
