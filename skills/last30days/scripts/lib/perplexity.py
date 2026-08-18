"""Perplexity Agent API, Search API, and OpenRouter compatibility integration.

The Perplexity source is paid and opt-in. A direct key uses a controlled Agent
API request with only web search enabled. Direct Deep Research uses an Agent
API background run with the dynamic high preset. When no direct Perplexity key
exists, OpenRouter preserves the legacy synchronous Sonar fallback.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from . import health, http, log


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PERPLEXITY_AGENT_URL = "https://api.perplexity.ai/v1/agent"
PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"

PERPLEXITY_MODE_AGENT = "agent"
PERPLEXITY_MODE_SONAR = "sonar"  # Direct-key alias and OpenRouter artifact mode.
PERPLEXITY_MODE_SEARCH = "search"
PERPLEXITY_MODE_BOTH = "both"
PERPLEXITY_DEFAULT_AGENT_TIMEOUT_SECONDS = 120
PERPLEXITY_DEFAULT_DEEP_TIMEOUT_SECONDS = 600
PERPLEXITY_DEEP_INITIAL_POLL_DELAY_SECONDS = 5.0
PERPLEXITY_DEEP_MAX_POLL_DELAY_SECONDS = 60.0

PERPLEXITY_DEFAULT_AGENT_MODEL = "perplexity/sonar"
OPENROUTER_MODEL_SONAR_PRO = "perplexity/sonar-pro"
OPENROUTER_MODEL_DEEP_RESEARCH = "perplexity/sonar-deep-research"
PERPLEXITY_DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS = 4096
PERPLEXITY_CONTROLLED_PROFILE = "last30days-controlled-web-search/v1"
PERPLEXITY_PRESET_PROFILE = "perplexity-agent-preset"
AGENT_PRESETS = {"fast", "low", "medium", "high"}
DIRECT_MODES = {
    PERPLEXITY_MODE_AGENT,
    PERPLEXITY_MODE_SEARCH,
    PERPLEXITY_MODE_BOTH,
}
SEARCH_CONTEXT_SIZES = {"low", "medium", "high"}
SEARCH_RECENCY_FILTERS = {"hour", "day", "week", "month", "year"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high"}

CONTROLLED_AGENT_INSTRUCTIONS = (
    "Use the supplied web search tool for current, source-grounded research. "
    "Keep the answer concise. Cite source-backed claims in the answer. "
    "Do not use tools other than those supplied in this request."
)


class AgentBackgroundTimeout(TimeoutError):
    def __init__(self, metadata: dict[str, Any]):
        timeout_seconds = metadata.get("backgroundTimeoutSeconds") or "unknown"
        super().__init__(f"Agent background run exceeded {timeout_seconds}s wall timeout")
        self.metadata = metadata


class AgentBackgroundFailed(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        detail = metadata.get("backgroundErrorMessage") or "Agent background run failed"
        super().__init__(str(detail))
        self.metadata = metadata


class AgentBackgroundPollError(RuntimeError):
    def __init__(self, metadata: dict[str, Any]):
        detail = metadata.get("backgroundPollError") or "Agent background poll failed"
        super().__init__(str(detail))
        self.metadata = metadata


def _log(message: str) -> None:
    log.source_log("Perplexity", message, tty_only=False)


def _domain(url: str) -> str:
    return urlparse(url).netloc.strip().lower()


def _config_text(config: dict[str, Any], key: str) -> str:
    return str(config.get(key) or "").strip()


def _csv_values(raw: str, limit: int | None = None) -> list[str]:
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return values[:limit]


def _positive_int(
    raw: object,
    default: int,
    min_value: int,
    max_value: int | None = None,
) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _mmddyyyy(date: str | None) -> str | None:
    if not date:
        return None
    try:
        return datetime.strptime(date, "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return None


def _usage(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage")
    return usage if isinstance(usage, dict) else {}


def _error_artifact(exc: Exception) -> dict[str, Any]:
    artifact: dict[str, Any] = {
        "error": type(exc).__name__,
        "message": str(exc)[:200],
    }
    if isinstance(exc, http.HTTPError):
        artifact["statusCode"] = exc.status_code
    return artifact


def _provider(config: dict[str, Any]) -> tuple[str, str] | None:
    """Prefer direct Perplexity, then preserve the OpenRouter Sonar fallback."""
    api_key = _config_text(config, "PERPLEXITY_API_KEY")
    if api_key:
        return "perplexity", api_key
    openrouter_key = _config_text(config, "OPENROUTER_API_KEY")
    if openrouter_key:
        return "openrouter", openrouter_key
    return None


def _mode(config: dict[str, Any], deep: bool, provider: str) -> str:
    if deep:
        return (
            PERPLEXITY_MODE_AGENT
            if provider == "perplexity"
            else PERPLEXITY_MODE_SONAR
        )

    mode = (
        _config_text(config, "LAST30DAYS_PERPLEXITY_MODE")
        or PERPLEXITY_MODE_AGENT
    ).lower()
    if provider == "openrouter":
        if mode in {PERPLEXITY_MODE_SEARCH, PERPLEXITY_MODE_BOTH}:
            _log(
                f"LAST30DAYS_PERPLEXITY_MODE={mode} requires PERPLEXITY_API_KEY; "
                "using the OpenRouter Sonar fallback"
            )
        return PERPLEXITY_MODE_SONAR
    if mode == PERPLEXITY_MODE_SONAR:
        _log(
            "LAST30DAYS_PERPLEXITY_MODE=sonar is deprecated; "
            "using the Agent API controlled profile"
        )
        return PERPLEXITY_MODE_AGENT
    if mode not in DIRECT_MODES:
        _log(f"Unsupported LAST30DAYS_PERPLEXITY_MODE={mode!r}; using agent")
        return PERPLEXITY_MODE_AGENT
    return mode


def _agent_preset(config: dict[str, Any], deep: bool) -> str | None:
    if deep:
        return "high"

    preset = _config_text(config, "LAST30DAYS_PERPLEXITY_AGENT_PRESET").lower()
    if not preset:
        return None
    if preset in AGENT_PRESETS:
        return preset
    _log(
        "Unsupported LAST30DAYS_PERPLEXITY_AGENT_PRESET="
        f"{preset!r}; using the controlled profile"
    )
    return None


def _agent_model(config: dict[str, Any]) -> str:
    legacy_model = _config_text(config, "LAST30DAYS_PERPLEXITY_MODEL")
    if legacy_model:
        _log(
            "LAST30DAYS_PERPLEXITY_MODEL is a legacy Sonar setting and is "
            "ignored by the Agent API; set LAST30DAYS_PERPLEXITY_AGENT_MODEL "
            "for an explicit Agent model"
        )
    return (
        _config_text(config, "LAST30DAYS_PERPLEXITY_AGENT_MODEL")
        or PERPLEXITY_DEFAULT_AGENT_MODEL
    )


def _agent_timeout(config: dict[str, Any]) -> int:
    return _positive_int(
        config.get("LAST30DAYS_PERPLEXITY_AGENT_TIMEOUT_SECONDS"),
        PERPLEXITY_DEFAULT_AGENT_TIMEOUT_SECONDS,
        1,
        600,
    )


def _safe_error_message(data: dict[str, Any]) -> str | None:
    error = data.get("error")
    if isinstance(error, str):
        return error[:200]
    if isinstance(error, dict):
        for key in ("message", "detail", "code"):
            value = error.get(key)
            if isinstance(value, str) and value:
                return value[:200]
    return None


def _safe_incomplete_reason(data: dict[str, Any]) -> str | None:
    details = data.get("incomplete_details")
    if not isinstance(details, dict):
        return None
    reason = details.get("reason")
    return reason[:100] if isinstance(reason, str) and reason else None


def _build_search_payload(
    query: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    from_date, to_date = date_range
    payload: dict[str, Any] = {
        "query": query,
        "max_results": _positive_int(
            config.get("LAST30DAYS_PERPLEXITY_MAX_RESULTS"),
            10,
            1,
            20,
        ),
    }

    context_size = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE",
    ).lower()
    if context_size in SEARCH_CONTEXT_SIZES:
        payload["search_context_size"] = context_size

    country = _config_text(config, "LAST30DAYS_PERPLEXITY_COUNTRY").upper()
    if len(country) == 2:
        payload["country"] = country

    domains = _csv_values(
        _config_text(config, "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER"),
        limit=20,
    )
    if domains:
        payload["search_domain_filter"] = domains

    languages = _csv_values(
        _config_text(config, "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER"),
        limit=20,
    )
    if languages:
        payload["search_language_filter"] = languages

    after = _mmddyyyy(from_date)
    before = _mmddyyyy(to_date)
    if after:
        payload["search_after_date_filter"] = after
    if before:
        payload["search_before_date_filter"] = before

    # Perplexity Search API rejects search_recency_filter when explicit
    # published-date filters are present. Preserve the upstream fix.
    recency = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_RECENCY_FILTER",
    ).lower()
    if recency in SEARCH_RECENCY_FILTERS and not (after or before):
        payload["search_recency_filter"] = recency

    return payload


def _build_web_search_tool(
    date_range: tuple[str, str],
    config: dict[str, Any],
) -> dict[str, Any]:
    from_date, to_date = date_range
    tool: dict[str, Any] = {
        "type": "web_search",
        "max_results": _positive_int(
            config.get("LAST30DAYS_PERPLEXITY_MAX_RESULTS"),
            10,
            1,
            20,
        ),
    }

    context_size = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE",
    ).lower()
    if context_size in SEARCH_CONTEXT_SIZES:
        tool["search_context_size"] = context_size

    country = _config_text(config, "LAST30DAYS_PERPLEXITY_COUNTRY").upper()
    if len(country) == 2:
        tool["user_location"] = {"country": country}

    filters: dict[str, Any] = {}
    domains = _csv_values(
        _config_text(config, "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER"),
        limit=20,
    )
    if domains:
        filters["search_domain_filter"] = domains

    after = _mmddyyyy(from_date)
    before = _mmddyyyy(to_date)
    if after:
        filters["search_after_date_filter"] = after
    if before:
        filters["search_before_date_filter"] = before

    recency = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_RECENCY_FILTER",
    ).lower()
    if recency in SEARCH_RECENCY_FILTERS and not (after or before):
        filters["search_recency_filter"] = recency
    if filters:
        tool["filters"] = filters

    language_filter = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER",
    )
    if language_filter:
        _log(
            "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER has no Agent API "
            "equivalent and applies only to Search API mode"
        )
    search_mode = _config_text(config, "LAST30DAYS_PERPLEXITY_SEARCH_MODE").lower()
    if search_mode and search_mode != "web":
        _log(
            "LAST30DAYS_PERPLEXITY_SEARCH_MODE has no Agent API equivalent; "
            "using web search"
        )

    return tool


def _safe_request(payload: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {}
    for key in (
        "model",
        "preset",
        "max_steps",
        "max_output_tokens",
        "background",
    ):
        if key in payload:
            request[key] = payload[key]
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        request["reasoning"] = {"effort": reasoning["effort"]}
    tool_choice = payload.get("tool_choice")
    if tool_choice == {"type": "web_search"}:
        request["tool_choice"] = tool_choice
    tools = payload.get("tools")
    if isinstance(tools, list):
        request["tools"] = [
            {
                key: tool[key]
                for key in (
                    "type",
                    "max_results",
                    "search_context_size",
                    "user_location",
                    "filters",
                )
                if key in tool
            }
            for tool in tools
            if isinstance(tool, dict) and tool.get("type") == "web_search"
        ]
    return request


def _build_agent_payload(
    prompt: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    deep: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    preset = _agent_preset(config, deep)
    if preset:
        payload = {
            "preset": preset,
            "input": prompt,
            # A supplied web_search tool merges with a preset's tools. This
            # preserves the user's date, domain, location, and result bounds
            # without claiming to disable any other dynamic-preset tools.
            "tools": [_build_web_search_tool(date_range, config)],
        }
        if deep:
            payload["background"] = True
        return payload, {
            "profile": PERPLEXITY_PRESET_PROFILE,
            "preset": preset,
            "dynamicPreset": True,
            "request": _safe_request(payload),
        }

    payload: dict[str, Any] = {
        "model": _agent_model(config),
        "instructions": CONTROLLED_AGENT_INSTRUCTIONS,
        "input": prompt,
        "tools": [_build_web_search_tool(date_range, config)],
        "tool_choice": {"type": "web_search"},
        "max_steps": _positive_int(
            config.get("LAST30DAYS_PERPLEXITY_AGENT_MAX_STEPS"),
            5,
            1,
            15,
        ),
    }
    if str(payload["model"]).lower().startswith("anthropic/"):
        payload["max_output_tokens"] = _positive_int(
            config.get("LAST30DAYS_PERPLEXITY_AGENT_MAX_OUTPUT_TOKENS"),
            PERPLEXITY_DEFAULT_ANTHROPIC_MAX_OUTPUT_TOKENS,
            1,
            32768,
        )
    effort = _config_text(
        config,
        "LAST30DAYS_PERPLEXITY_REASONING_EFFORT",
    ).lower()
    if effort in REASONING_EFFORTS:
        payload["reasoning"] = {"effort": effort}
    return payload, {
        "profile": PERPLEXITY_CONTROLLED_PROFILE,
        "model": payload["model"],
        "dynamicPreset": False,
        "request": _safe_request(payload),
    }


def _append_citation(
    citations: list[dict[str, Any]],
    seen_urls: set[str],
    citation: dict[str, Any],
) -> None:
    url = str(citation.get("url") or "").strip()
    if not url:
        return
    if url in seen_urls:
        # Message annotations commonly carry only a URL and title. Merge the
        # later search_results item instead of discarding its snippet/date.
        for existing in citations:
            if existing.get("url") != url:
                continue
            for key in ("title", "snippet", "date"):
                if not existing.get(key) and citation.get(key):
                    existing[key] = citation[key]
            return
    seen_urls.add(url)
    citations.append(
        {
            "url": url,
            "title": citation.get("title") or "",
            "snippet": citation.get("snippet") or "",
            "date": citation.get("date"),
        }
    )


def _append_annotations(
    citations: list[dict[str, Any]],
    seen_urls: set[str],
    annotations: Any,
) -> None:
    if not isinstance(annotations, list):
        return
    for annotation in annotations:
        if not isinstance(annotation, dict):
            continue
        citation = annotation.get("url_citation")
        if not isinstance(citation, dict):
            citation = annotation
        _append_citation(citations, seen_urls, citation)


def _extract_agent_citations(data: dict[str, Any]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for result in data.get("results") or []:
        if isinstance(result, dict):
            _append_citation(citations, seen_urls, result)

    output = data.get("output")
    if not isinstance(output, list):
        return citations

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "search_results":
            for result in item.get("results") or []:
                if isinstance(result, dict):
                    _append_citation(citations, seen_urls, result)
            continue
        if item_type != "message":
            continue
        _append_annotations(citations, seen_urls, item.get("annotations"))
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict):
                _append_annotations(citations, seen_urls, part.get("annotations"))

    return citations


def _extract_openrouter_citations(
    data: dict[str, Any],
    choice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Read the legacy OpenAI-compatible Sonar citation shapes."""
    citations: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    search_results: dict[str, dict[str, Any]] = {}
    for result in data.get("search_results") or []:
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        if not url:
            continue
        search_results[url] = result
        _append_citation(citations, seen_urls, result)

    for url in data.get("citations") or []:
        if not isinstance(url, str):
            continue
        result = search_results.get(url, {})
        _append_citation(
            citations,
            seen_urls,
            {
                "url": url,
                "title": result.get("title") or _domain(url),
                "snippet": result.get("snippet") or "",
                "date": result.get("date"),
            },
        )

    message = choice.get("message")
    if isinstance(message, dict):
        _append_annotations(citations, seen_urls, message.get("annotations"))
    return citations


def _output_types(data: dict[str, Any]) -> list[str]:
    output = data.get("output")
    if not isinstance(output, list):
        return []
    return [
        item["type"]
        for item in output
        if isinstance(item, dict) and isinstance(item.get("type"), str)
    ]


def _output_text(data: dict[str, Any]) -> str:
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts: list[str] = []
    output = data.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") not in {"output_text", "text"}:
                continue
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _background_metadata(
    data: dict[str, Any],
    response_id: str,
    timeout_seconds: int,
    poll_count: int,
    local_status: str,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "background": True,
        "responseId": response_id,
        "backgroundStatus": data.get("status"),
        "servedModel": data.get("model"),
        "usage": _usage(data),
        "outputTypes": _output_types(data),
        "backgroundTimeoutSeconds": timeout_seconds,
        "backgroundPollCount": poll_count,
        "backgroundLocalStatus": local_status,
    }
    message = _safe_error_message(data)
    if message:
        metadata["backgroundErrorMessage"] = message
    incomplete_reason = _safe_incomplete_reason(data)
    if incomplete_reason:
        metadata["incompleteReason"] = incomplete_reason
    return {key: value for key, value in metadata.items() if value is not None}


def _log_deep_receipt(artifact: dict[str, Any]) -> None:
    """Expose the safe Deep receipt to slash-command and CLI consumers."""
    fields = (
        ("model", artifact.get("servedModel")),
        ("response_id", artifact.get("responseId")),
        ("provider_status", artifact.get("backgroundStatus") or artifact.get("status")),
        ("local_status", artifact.get("backgroundLocalStatus")),
        ("incomplete_reason", artifact.get("incompleteReason")),
        ("polls", artifact.get("backgroundPollCount")),
        ("timeout_seconds", artifact.get("backgroundTimeoutSeconds")),
    )
    rendered = " ".join(
        f"{key}={value}"
        for key, value in fields
        if value is not None
    )
    _log(f"Deep Research receipt: {rendered or 'unavailable'}")


def _poll_agent_background(
    payload: dict[str, Any],
    headers: dict[str, str],
    config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    timeout_seconds = _positive_int(
        config.get("LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS"),
        PERPLEXITY_DEFAULT_DEEP_TIMEOUT_SECONDS,
        1,
        None,
    )
    created = http.post(
        PERPLEXITY_AGENT_URL,
        payload,
        headers=headers,
        timeout=30,
        retries=1,
    )
    response_id = created.get("id")
    if not isinstance(response_id, str) or not response_id:
        raise http.HTTPError("Agent background response missing id")

    terminal = {"completed", "failed", "cancelled", "incomplete"}
    status = str(created.get("status") or "").lower()
    data = created
    poll_count = 0
    if status:
        _log(f"Agent background status: {status}")

    deadline = time.monotonic() + timeout_seconds
    delay = PERPLEXITY_DEEP_INITIAL_POLL_DELAY_SECONDS
    while status not in terminal:
        if time.monotonic() >= deadline:
            raise AgentBackgroundTimeout(
                _background_metadata(
                    data,
                    response_id,
                    timeout_seconds,
                    poll_count,
                    "PENDING_REMOTE",
                )
            )
        try:
            data = http.get(
                f"{PERPLEXITY_AGENT_URL}/{response_id}",
                headers=headers,
                timeout=30,
                retries=2,
                deadline_monotonic=deadline,
            )
        except http.HTTPError as exc:
            if isinstance(exc, http.DeadlineExceeded) or time.monotonic() >= deadline:
                raise AgentBackgroundTimeout(
                    _background_metadata(
                        data,
                        response_id,
                        timeout_seconds,
                        poll_count + 1,
                        "PENDING_REMOTE",
                    )
                ) from exc
            metadata = _background_metadata(
                data,
                response_id,
                timeout_seconds,
                poll_count + 1,
                "POLL_ERROR",
            )
            metadata["backgroundPollError"] = str(exc)[:200]
            if exc.status_code is not None:
                metadata["backgroundPollStatusCode"] = exc.status_code
            raise AgentBackgroundPollError(metadata) from exc

        poll_count += 1
        next_status = str(data.get("status") or "").lower()
        if next_status and next_status != status:
            _log(f"Agent background status: {next_status}")
        status = next_status
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentBackgroundTimeout(
                _background_metadata(
                    data,
                    response_id,
                    timeout_seconds,
                    poll_count,
                    "PENDING_REMOTE",
                )
            )
        if status in terminal:
            break
        time.sleep(min(delay, remaining))
        delay = min(delay * 1.5, PERPLEXITY_DEEP_MAX_POLL_DELAY_SECONDS)

    metadata = _background_metadata(
        data,
        response_id,
        timeout_seconds,
        poll_count,
        "COMPLETED_REMOTE" if status == "completed" else "TERMINAL_REMOTE",
    )
    if status == "completed":
        return data, metadata
    if not status:
        metadata["backgroundErrorMessage"] = "Agent background response has no status"
    raise AgentBackgroundFailed(metadata)


def _search_api(
    query: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    api_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from_date, to_date = date_range
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = _build_search_payload(query, date_range, config)
    _log(f"Querying Perplexity Search API for '{query}' ({from_date} to {to_date})")

    data = http.post(
        PERPLEXITY_SEARCH_URL,
        payload,
        headers=headers,
        timeout=30,
        retries=1,
    )
    results = data.get("results") or []
    if not isinstance(results, list):
        results = []

    items: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "").strip()
        if not url:
            continue
        items.append(
            {
                "id": f"PXS{index + 1}",
                "title": result.get("title") or _domain(url),
                "url": url,
                "source_domain": _domain(url),
                "snippet": result.get("snippet") or "",
                "date": result.get("date"),
                "relevance": max(0.55, 0.85 - (index * 0.03)),
                "why_relevant": f"Ranked by Perplexity Search API for '{query}'",
                "engagement": {},
                "metadata": {
                    "last_updated": result.get("last_updated"),
                    "perplexity_search_id": data.get("id"),
                },
            }
        )

    artifact = {
        "label": "perplexity",
        "provider": "perplexity",
        "mode": PERPLEXITY_MODE_SEARCH,
        "endpoint": "search",
        "query": query,
        "resultCount": len(items),
        "request": {key: value for key, value in payload.items() if key != "query"},
        "responseId": data.get("id"),
        "serverTime": data.get("server_time"),
    }
    _log(f"Got {len(items)} Search API results")
    return items, artifact


def _agent_failure_artifact(
    query: str,
    deep: bool,
    selection: dict[str, Any],
    *,
    error: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "label": "perplexity",
        "provider": "perplexity",
        "mode": PERPLEXITY_MODE_AGENT,
        "endpoint": "agent-background" if deep else "agent",
        "deep": deep,
        "query": query,
        "error": error,
        "synthesisLength": 0,
        "citationCount": 0,
        **selection,
        **(metadata or {}),
    }


def _agent_result(
    query: str,
    date_range: tuple[str, str],
    deep: bool,
    data: dict[str, Any],
    selection: dict[str, Any],
    background_metadata: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    _, to_date = date_range
    status = str(data.get("status") or "").lower()
    if status and status != "completed":
        metadata: dict[str, Any] = {
            "responseId": data.get("id"),
            "status": data.get("status"),
            "servedModel": data.get("model"),
            "usage": _usage(data),
            "outputTypes": _output_types(data),
        }
        message = _safe_error_message(data)
        if message:
            metadata["agentErrorMessage"] = message
        incomplete_reason = _safe_incomplete_reason(data)
        if incomplete_reason:
            metadata["incompleteReason"] = incomplete_reason
        if background_metadata:
            metadata.update(background_metadata)
        return [], _agent_failure_artifact(
            query,
            deep,
            selection,
            error=status,
            metadata=metadata,
        )

    synthesis = _output_text(data)
    citations = _extract_agent_citations(data)
    if not synthesis:
        _log("Empty Agent API synthesis")
        return [], _agent_failure_artifact(
            query,
            deep,
            selection,
            error="empty_synthesis",
            metadata={
                "responseId": data.get("id"),
                "status": data.get("status"),
                "servedModel": data.get("model"),
                "usage": _usage(data),
                "outputTypes": _output_types(data),
                **(background_metadata or {}),
            },
        )

    _log(f"Got Agent API synthesis ({len(synthesis)} chars) with {len(citations)} citations")
    title_mode = "Deep Research" if deep else "Agent"
    items: list[dict[str, Any]] = [
        {
            "id": "PX1",
            "title": f"Perplexity {title_mode}: {query}",
            "url": "",
            "source_domain": "perplexity.ai",
            "snippet": synthesis[:2000],
            "date": to_date,
            "relevance": 0.9,
            "why_relevant": f"AI synthesis of recent activity for '{query}'",
            "engagement": {"citations": len(citations)},
            "metadata": {
                "citations": citations,
                "usage": _usage(data),
                "perplexity_response_id": data.get("id"),
            },
        }
    ]
    for index, citation in enumerate(citations):
        items.append(
            {
                "id": f"PX{index + 2}",
                "title": citation["title"] or _domain(citation["url"]),
                "url": citation["url"],
                "source_domain": _domain(citation["url"]),
                "snippet": citation.get("snippet") or "",
                "date": citation.get("date"),
                "relevance": 0.7,
                "why_relevant": f"Cited in Perplexity synthesis for '{query}'",
                "engagement": {"citations": 1},
                "metadata": {"citations": [citation]},
            }
        )

    artifact: dict[str, Any] = {
        "label": "perplexity",
        "provider": "perplexity",
        "mode": PERPLEXITY_MODE_AGENT,
        "endpoint": "agent-background" if deep else "agent",
        "deep": deep,
        "query": query,
        "synthesisLength": len(synthesis),
        "citationCount": len(citations),
        "responseId": data.get("id"),
        "status": data.get("status"),
        "servedModel": data.get("model"),
        "usage": _usage(data),
        "outputTypes": _output_types(data),
        **selection,
    }
    if background_metadata:
        artifact.update(background_metadata)
    return items, artifact


def _agent_search(
    query: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    api_key: str,
    deep: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from_date, to_date = date_range
    prompt = (
        f"What has been happening with {query} between {from_date} and {to_date}? "
        "Include specific dates, names, numbers, and sources."
    )
    payload, selection = _build_agent_payload(prompt, date_range, config, deep)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _log(f"Querying Perplexity Agent API for '{query}' ({from_date} to {to_date})")

    try:
        if deep:
            data, background_metadata = _poll_agent_background(payload, headers, config)
        else:
            data = http.post(
                PERPLEXITY_AGENT_URL,
                payload,
                headers=headers,
                timeout=_agent_timeout(config),
                retries=1,
            )
            background_metadata = None
    except AgentBackgroundTimeout as exc:
        _log(f"Agent background request timed out: {exc}")
        return [], _agent_failure_artifact(
            query,
            deep,
            selection,
            error="timeout",
            metadata=exc.metadata,
        )
    except AgentBackgroundFailed as exc:
        _log(f"Agent background request failed: {exc}")
        return [], _agent_failure_artifact(
            query,
            deep,
            selection,
            error="failed",
            metadata=exc.metadata,
        )
    except AgentBackgroundPollError as exc:
        _log(f"Agent background poll failed: {exc}")
        return [], _agent_failure_artifact(
            query,
            deep,
            selection,
            error="poll_error",
            metadata=exc.metadata,
        )

    return _agent_result(
        query,
        date_range,
        deep,
        data,
        selection,
        background_metadata,
    )


def _openrouter_sonar_search(
    query: str,
    date_range: tuple[str, str],
    api_key: str,
    deep: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Preserve the pre-Agent OpenRouter Sonar compatibility path."""
    from_date, to_date = date_range
    model = (
        OPENROUTER_MODEL_DEEP_RESEARCH
        if deep
        else OPENROUTER_MODEL_SONAR_PRO
    )
    prompt = (
        f"What has been happening with {query} between {from_date} and {to_date}? "
        "Include specific dates, names, numbers, and sources."
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    _log(f"Querying OpenRouter {model} for '{query}' ({from_date} to {to_date})")
    data = http.post(
        OPENROUTER_URL,
        payload,
        headers=headers,
        timeout=120 if deep else 30,
        retries=1,
    )

    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return [], {
            "label": "perplexity",
            "provider": "openrouter",
            "mode": PERPLEXITY_MODE_SONAR,
            "endpoint": "openrouter-chat-completions",
            "model": model,
            "deep": deep,
            "query": query,
            "error": "empty_choices",
            "responseId": data.get("id"),
            "servedModel": data.get("model") or model,
            "usage": _usage(data),
        }

    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    synthesis = message.get("content")
    synthesis = synthesis if isinstance(synthesis, str) else ""
    if not synthesis:
        return [], {
            "label": "perplexity",
            "provider": "openrouter",
            "mode": PERPLEXITY_MODE_SONAR,
            "endpoint": "openrouter-chat-completions",
            "model": model,
            "deep": deep,
            "query": query,
            "error": "empty_synthesis",
            "responseId": data.get("id"),
            "servedModel": data.get("model") or model,
            "usage": _usage(data),
        }

    citations = _extract_openrouter_citations(data, choice)
    title_mode = "Deep Research" if deep else "Sonar"
    items: list[dict[str, Any]] = [
        {
            "id": "PX1",
            "title": f"Perplexity {title_mode}: {query}",
            "url": "",
            "source_domain": "perplexity.ai",
            "snippet": synthesis[:2000],
            "date": to_date,
            "relevance": 0.9,
            "why_relevant": f"AI synthesis of recent activity for '{query}'",
            "engagement": {"citations": len(citations)},
            "metadata": {
                "citations": citations,
                "usage": _usage(data),
                "openrouter_response_id": data.get("id"),
            },
        }
    ]
    for index, citation in enumerate(citations):
        items.append(
            {
                "id": f"PX{index + 2}",
                "title": citation["title"] or _domain(citation["url"]),
                "url": citation["url"],
                "source_domain": _domain(citation["url"]),
                "snippet": citation.get("snippet") or "",
                "date": citation.get("date"),
                "relevance": 0.7,
                "why_relevant": f"Cited in Perplexity synthesis for '{query}'",
                "engagement": {"citations": 1},
                "metadata": {"citations": [citation]},
            }
        )

    return items, {
        "label": "perplexity",
        "provider": "openrouter",
        "mode": PERPLEXITY_MODE_SONAR,
        "endpoint": "openrouter-chat-completions",
        "model": model,
        "deep": deep,
        "query": query,
        "synthesisLength": len(synthesis),
        "citationCount": len(citations),
        "responseId": data.get("id"),
        "servedModel": data.get("model") or model,
        "usage": _usage(data),
    }


def _merge_agent_and_search(
    agent_items: list[dict[str, Any]],
    search_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not agent_items:
        return search_items
    merged = agent_items[:1]
    seen_urls = {item.get("url") for item in merged if item.get("url")}
    for item in [*search_items, *agent_items[1:]]:
        url = item.get("url")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        merged.append(item)
    return merged


def _top_level_failure(
    query: str,
    mode: str,
    deep: bool,
    exc: Exception,
    provider: str = "perplexity",
) -> dict[str, Any]:
    artifact = _error_artifact(exc)
    if provider == "openrouter":
        endpoint = "openrouter-chat-completions"
    elif deep:
        endpoint = "agent-background"
    elif mode == PERPLEXITY_MODE_SEARCH:
        endpoint = "search"
    else:
        endpoint = "agent"
    artifact.update(
        {
            "label": "perplexity",
            "provider": provider,
            "mode": mode,
            "endpoint": endpoint,
            "deep": deep,
            "query": query,
        }
    )
    return artifact


def search(
    query: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    deep: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search through Perplexity's Agent API or raw Search API.

    Normal synthesis uses the controlled Agent profile. Search API mode remains
    available for raw ranked rows. Deep Research is a dynamic high-preset
    background run and requires an explicit --deep-research invocation.
    """
    resolved = _provider(config)
    if not resolved:
        _log(
            "No PERPLEXITY_API_KEY or OPENROUTER_API_KEY configured, skipping"
        )
        return [], {}
    provider, api_key = resolved

    mode = _mode(config, deep, provider)
    try:
        if provider == "openrouter":
            result = _openrouter_sonar_search(
                query,
                date_range,
                api_key,
                deep,
            )
            if deep:
                _log_deep_receipt(result[1])
            return result
        if mode == PERPLEXITY_MODE_SEARCH:
            return _search_api(query, date_range, config, api_key)
        if mode == PERPLEXITY_MODE_BOTH:
            search_items: list[dict[str, Any]] = []
            agent_items: list[dict[str, Any]] = []
            search_artifact: dict[str, Any] = {}
            agent_artifact: dict[str, Any] = {}
            try:
                search_items, search_artifact = _search_api(
                    query,
                    date_range,
                    config,
                    api_key,
                )
            except Exception as exc:
                _log(f"Search API leg failed in both mode: {exc}")
                search_artifact = _error_artifact(exc)
            try:
                agent_items, agent_artifact = _agent_search(
                    query,
                    date_range,
                    config,
                    api_key,
                    deep=False,
                )
            except Exception as exc:
                _log(f"Agent API leg failed in both mode: {exc}")
                agent_artifact = _error_artifact(exc)
            items = _merge_agent_and_search(agent_items, search_items)
            return items, {
                "label": "perplexity",
                "provider": "perplexity",
                "mode": PERPLEXITY_MODE_BOTH,
                "query": query,
                "search": search_artifact,
                "agent": agent_artifact,
                "itemCount": len(items),
            }
        result = _agent_search(query, date_range, config, api_key, deep)
        if deep:
            _log_deep_receipt(result[1])
        return result
    except http.HTTPError as exc:
        if exc.status_code == 401:
            _log(f"Invalid {provider} API key (401)")
        elif exc.status_code == 429:
            _log(f"Rate limited by {provider} (429)")
        else:
            _log(f"HTTP error: {exc}")
        artifact = _top_level_failure(
            query,
            mode,
            deep,
            exc,
            provider=provider,
        )
        if deep:
            _log_deep_receipt(artifact)
        return [], artifact
    except TimeoutError as exc:
        _log(f"Request timed out: {exc}")
        artifact = _top_level_failure(
            query,
            mode,
            deep,
            exc,
            provider=provider,
        )
        if deep:
            _log_deep_receipt(artifact)
        return [], artifact
    except Exception as exc:
        _log(f"Request failed: {exc}")
        artifact = _top_level_failure(
            query,
            mode,
            deep,
            exc,
            provider=provider,
        )
        if deep:
            _log_deep_receipt(artifact)
        return [], artifact
