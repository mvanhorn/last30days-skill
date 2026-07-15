"""Perplexity Sonar, Search API, and Deep Research.

Direct Perplexity keys are preferred so the source can use first-party Search
API results and async Deep Research. OpenRouter remains a Sonar compatibility
fallback when no direct Perplexity key is configured.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import time
from datetime import datetime
from urllib.parse import urlparse

from . import http, log


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PERPLEXITY_URL = "https://api.perplexity.ai/v1/sonar"
PERPLEXITY_SEARCH_URL = "https://api.perplexity.ai/search"
PERPLEXITY_ASYNC_URL = "https://api.perplexity.ai/v1/async/sonar"

OPENROUTER_MODEL_SONAR_PRO = "perplexity/sonar-pro"
OPENROUTER_MODEL_DEEP_RESEARCH = "perplexity/sonar-deep-research"
PERPLEXITY_MODEL_SONAR = "sonar"
PERPLEXITY_MODEL_SONAR_PRO = "sonar-pro"
PERPLEXITY_MODEL_REASONING_PRO = "sonar-reasoning-pro"
PERPLEXITY_MODEL_DEEP_RESEARCH = "sonar-deep-research"
PERPLEXITY_MODE_SONAR = "sonar"
PERPLEXITY_MODE_SEARCH = "search"
PERPLEXITY_MODE_BOTH = "both"
PERPLEXITY_DEFAULT_DEEP_TIMEOUT_SECONDS = 600
PERPLEXITY_DEEP_INITIAL_POLL_DELAY_SECONDS = 5.0
PERPLEXITY_DEEP_MAX_POLL_DELAY_SECONDS = 60.0

DIRECT_MODELS = {
    PERPLEXITY_MODEL_SONAR,
    PERPLEXITY_MODEL_SONAR_PRO,
    PERPLEXITY_MODEL_REASONING_PRO,
    PERPLEXITY_MODEL_DEEP_RESEARCH,
}
DIRECT_MODES = {
    PERPLEXITY_MODE_SONAR,
    PERPLEXITY_MODE_SEARCH,
    PERPLEXITY_MODE_BOTH,
}
SEARCH_CONTEXT_SIZES = {"low", "medium", "high"}
SEARCH_RECENCY_FILTERS = {"hour", "day", "week", "month", "year"}
SONAR_SEARCH_MODES = {"web", "academic", "sec"}
REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


class AsyncDeepResearchTimeout(TimeoutError):
    def __init__(self, metadata: dict):
        timeout_seconds = metadata.get("asyncTimeoutSeconds") or "unknown"
        super().__init__(f"Async Deep Research exceeded {timeout_seconds}s wall timeout")
        self.metadata = metadata


class AsyncDeepResearchFailed(RuntimeError):
    def __init__(self, metadata: dict):
        message = metadata.get("asyncErrorMessage") or "Async Deep Research failed"
        super().__init__(str(message))
        self.metadata = metadata


class AsyncDeepResearchPollError(RuntimeError):
    def __init__(self, metadata: dict):
        message = metadata.get("asyncPollError") or "Async Deep Research poll failed"
        super().__init__(str(message))
        self.metadata = metadata


def _log(msg: str):
    log.source_log("Perplexity", msg, tty_only=False)


def _domain(url: str) -> str:
    return urlparse(url).netloc.strip().lower()


def _provider(config: dict, deep: bool) -> tuple[str, str, str, str] | None:
    """Return (provider, api_key, url, model), preferring direct Perplexity."""
    if config.get("PERPLEXITY_API_KEY"):
        model = _direct_model(config, deep)
        url = PERPLEXITY_ASYNC_URL if deep else PERPLEXITY_URL
        return "perplexity", config["PERPLEXITY_API_KEY"], url, model
    if config.get("OPENROUTER_API_KEY"):
        model = OPENROUTER_MODEL_DEEP_RESEARCH if deep else OPENROUTER_MODEL_SONAR_PRO
        return "openrouter", config["OPENROUTER_API_KEY"], OPENROUTER_URL, model
    return None


def _config_text(config: dict, key: str) -> str:
    return str(config.get(key) or "").strip()


def _csv_values(raw: str, limit: int | None = None) -> list[str]:
    values = [part.strip() for part in raw.split(",") if part.strip()]
    # values[:None] already returns the whole list, so no None guard is needed.
    return values[:limit]


def _direct_model(config: dict, deep: bool) -> str:
    if deep:
        return PERPLEXITY_MODEL_DEEP_RESEARCH
    model = _config_text(config, "LAST30DAYS_PERPLEXITY_MODEL") or PERPLEXITY_MODEL_SONAR_PRO
    if model not in DIRECT_MODELS:
        _log(f"Unsupported LAST30DAYS_PERPLEXITY_MODEL={model!r}; using sonar-pro")
        return PERPLEXITY_MODEL_SONAR_PRO
    if model == PERPLEXITY_MODEL_DEEP_RESEARCH:
        return PERPLEXITY_MODEL_SONAR_PRO
    return model


def _mode(config: dict, provider: str, deep: bool) -> str:
    if deep:
        return PERPLEXITY_MODE_SONAR
    mode = (_config_text(config, "LAST30DAYS_PERPLEXITY_MODE") or PERPLEXITY_MODE_SONAR).lower()
    if mode not in DIRECT_MODES:
        _log(f"Unsupported LAST30DAYS_PERPLEXITY_MODE={mode!r}; using sonar")
        return PERPLEXITY_MODE_SONAR
    if provider != "perplexity" and mode != PERPLEXITY_MODE_SONAR:
        _log("Search API modes require PERPLEXITY_API_KEY; using OpenRouter Sonar fallback")
        return PERPLEXITY_MODE_SONAR
    return mode


def _positive_int(raw: object, default: int, min_value: int, max_value: int | None = None) -> int:
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


def _usage(data: dict) -> dict:
    usage = data.get("usage")
    return usage if isinstance(usage, dict) else {}


def _idempotency_key(json_data: dict) -> str:
    payload = json.dumps(json_data, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"last30days:{digest}"


def _async_metadata(
    data: dict,
    request_id: str,
    timeout_seconds: int,
    idempotency_key: str,
    poll_count: int,
    local_status: str,
) -> dict:
    metadata = {
        "async": True,
        "asyncRequestId": request_id,
        "asyncStatus": data.get("status"),
        "asyncTimeoutSeconds": timeout_seconds,
        "asyncIdempotencyKey": idempotency_key,
        "asyncPollCount": poll_count,
        "asyncLocalStatus": local_status,
        "asyncCreatedAt": data.get("created_at"),
        "asyncStartedAt": data.get("started_at"),
        "asyncCompletedAt": data.get("completed_at"),
        "asyncFailedAt": data.get("failed_at"),
        "asyncErrorMessage": data.get("error_message"),
    }
    return {k: v for k, v in metadata.items() if v is not None}


def _error_artifact(exc: Exception) -> dict:
    artifact = {
        "error": type(exc).__name__,
        "message": str(exc)[:200],
    }
    if isinstance(exc, http.HTTPError):
        artifact["statusCode"] = exc.status_code
    return artifact


def _empty_async_sonar_artifact(
    provider: str,
    model: str,
    deep: bool,
    query: str,
    data: dict,
    async_artifact: dict,
    error: str,
    message: str,
) -> dict:
    if not async_artifact:
        return {}
    artifact = {
        "label": "perplexity",
        "provider": provider,
        "mode": PERPLEXITY_MODE_SONAR,
        "endpoint": "async-sonar",
        "model": model,
        "deep": deep,
        "query": query,
        "error": error,
        "synthesisLength": 0,
        "citationCount": 0,
        "usage": _usage(data),
        **async_artifact,
    }
    if not artifact.get("asyncErrorMessage"):
        artifact["asyncErrorMessage"] = message
    return artifact


def _build_sonar_payload(prompt: str, model: str, date_range: tuple[str, str], config: dict) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    from_date, to_date = date_range
    web_options: dict[str, object] = {}
    search_mode = _config_text(config, "LAST30DAYS_PERPLEXITY_SEARCH_MODE").lower()
    if search_mode in SONAR_SEARCH_MODES:
        web_options["search_mode"] = search_mode

    domains = _csv_values(_config_text(config, "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER"), limit=20)
    if domains:
        web_options["search_domain_filter"] = domains

    languages = _csv_values(_config_text(config, "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER"), limit=20)
    if languages:
        web_options["search_language_filter"] = languages

    recency = _config_text(config, "LAST30DAYS_PERPLEXITY_RECENCY_FILTER").lower()
    if recency in SEARCH_RECENCY_FILTERS:
        web_options["search_recency_filter"] = recency

    after = _mmddyyyy(from_date)
    before = _mmddyyyy(to_date)
    if after:
        web_options["search_after_date_filter"] = after
    if before:
        web_options["search_before_date_filter"] = before

    if web_options:
        payload["web_search_options"] = web_options

    effort = _config_text(config, "LAST30DAYS_PERPLEXITY_REASONING_EFFORT").lower()
    if effort in REASONING_EFFORTS:
        payload["reasoning_effort"] = effort

    return payload


def _build_search_payload(query: str, date_range: tuple[str, str], config: dict) -> dict:
    from_date, to_date = date_range
    payload: dict[str, object] = {
        "query": query,
        "max_results": _positive_int(config.get("LAST30DAYS_PERPLEXITY_MAX_RESULTS"), 10, 1, 20),
    }

    context_size = _config_text(config, "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE").lower()
    if context_size in SEARCH_CONTEXT_SIZES:
        payload["search_context_size"] = context_size

    country = _config_text(config, "LAST30DAYS_PERPLEXITY_COUNTRY").upper()
    if len(country) == 2:
        payload["country"] = country

    domains = _csv_values(_config_text(config, "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER"), limit=20)
    if domains:
        payload["search_domain_filter"] = domains

    languages = _csv_values(_config_text(config, "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER"), limit=20)
    if languages:
        payload["search_language_filter"] = languages

    after = _mmddyyyy(from_date)
    before = _mmddyyyy(to_date)
    if after:
        payload["search_after_date_filter"] = after
    if before:
        payload["search_before_date_filter"] = before

    # Perplexity Search API rejects search_recency_filter when explicit
    # published-date filters are present. last30days already passes an exact
    # date range, so prefer that and keep recency only for undated callers.
    recency = _config_text(config, "LAST30DAYS_PERPLEXITY_RECENCY_FILTER").lower()
    if recency in SEARCH_RECENCY_FILTERS and not (after or before):
        payload["search_recency_filter"] = recency

    return payload


def _append_citation(citations: list[dict], seen_urls: set[str], citation: dict) -> None:
    url = (citation.get("url") or "").strip()
    if not url or url in seen_urls:
        return
    seen_urls.add(url)
    citations.append({
        "url": url,
        "title": citation.get("title") or "",
        "snippet": citation.get("snippet") or "",
        "date": citation.get("date"),
    })


def _extract_citations(data: dict, choice: dict) -> list[dict]:
    """Extract citations from direct Perplexity and OpenRouter response shapes."""
    citations: list[dict] = []
    seen_urls: set[str] = set()

    search_results_by_url: dict[str, dict] = {}
    for result in data.get("search_results") or []:
        if not isinstance(result, dict):
            continue
        url = (result.get("url") or "").strip()
        if not url:
            continue
        search_results_by_url[url] = result
        _append_citation(citations, seen_urls, result)

    for url in data.get("citations") or []:
        if not isinstance(url, str):
            continue
        result = search_results_by_url.get(url, {})
        _append_citation(citations, seen_urls, {
            "url": url,
            "title": result.get("title") or _domain(url),
            "snippet": result.get("snippet") or "",
            "date": result.get("date"),
        })

    annotations = choice.get("message", {}).get("annotations", [])
    for ann in annotations or []:
        if not isinstance(ann, dict):
            continue
        url_citation = ann.get("url_citation", {})
        if not isinstance(url_citation, dict):
            continue
        _append_citation(citations, seen_urls, {
            "url": url_citation.get("url") or "",
            "title": url_citation.get("title") or "",
        })

    return citations


def _poll_async_sonar(json_data: dict, headers: dict, config: dict) -> tuple[dict, dict]:
    timeout_seconds = _positive_int(
        config.get("LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS"),
        PERPLEXITY_DEFAULT_DEEP_TIMEOUT_SECONDS,
        1,
        None,
    )
    idempotency_key = _idempotency_key(json_data)
    created = http.post(
        PERPLEXITY_ASYNC_URL,
        {"request": json_data, "idempotency_key": idempotency_key},
        headers=headers,
        timeout=30,
        retries=2,
    )
    request_id = created.get("id")
    if not request_id:
        raise http.HTTPError("Async Deep Research response missing id")

    deadline = time.monotonic() + timeout_seconds
    poll_url = f"{PERPLEXITY_ASYNC_URL}/{request_id}"
    delay = PERPLEXITY_DEEP_INITIAL_POLL_DELAY_SECONDS
    last_status = created.get("status")
    poll_count = 0
    last_data = created
    if last_status:
        _log(f"Deep Research async status: {last_status}")

    while time.monotonic() < deadline:
        try:
            data = http.get(poll_url, headers=headers, timeout=30, retries=2)
        except http.HTTPError as e:
            metadata = _async_metadata(
                last_data, request_id, timeout_seconds, idempotency_key, poll_count + 1,
                "POLL_ERROR",
            )
            metadata["asyncPollError"] = str(e)
            if e.status_code is not None:
                metadata["asyncPollStatusCode"] = e.status_code
            raise AsyncDeepResearchPollError(metadata)
        poll_count += 1
        last_data = data
        status = data.get("status")
        if status and status != last_status:
            _log(f"Deep Research async status: {status}")
            last_status = status
        if status == "COMPLETED":
            response = data.get("response")
            if not isinstance(response, dict):
                metadata = _async_metadata(
                    data, request_id, timeout_seconds, idempotency_key, poll_count,
                    "FAILED_REMOTE",
                )
                metadata["asyncErrorMessage"] = "Async Deep Research completed without response"
                raise AsyncDeepResearchFailed(metadata)
            return response, _async_metadata(
                data, request_id, timeout_seconds, idempotency_key, poll_count,
                "COMPLETED_REMOTE",
            )
        if status == "FAILED":
            raise AsyncDeepResearchFailed(_async_metadata(
                data, request_id, timeout_seconds, idempotency_key, poll_count,
                "FAILED_REMOTE",
            ))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        jitter = random.uniform(0, 2)
        time.sleep(min(delay + jitter, max(0.1, remaining)))
        delay = min(delay * 1.5, PERPLEXITY_DEEP_MAX_POLL_DELAY_SECONDS)

    raise AsyncDeepResearchTimeout(_async_metadata(
        last_data, request_id, timeout_seconds, idempotency_key, poll_count,
        "PENDING_REMOTE",
    ))


def _search_api(
    query: str,
    date_range: tuple[str, str],
    config: dict,
    api_key: str,
) -> tuple[list[dict], dict]:
    from_date, to_date = date_range
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = _build_search_payload(query, date_range, config)
    _log(f"Querying Perplexity Search API for '{query}' ({from_date} to {to_date})")

    data = http.post(PERPLEXITY_SEARCH_URL, payload, headers=headers, timeout=30)
    results = data.get("results") or []
    if not isinstance(results, list):
        results = []

    items = []
    for i, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        url = (result.get("url") or "").strip()
        if not url:
            continue
        items.append({
            "id": f"PXS{i + 1}",
            "title": result.get("title") or _domain(url),
            "url": url,
            "source_domain": _domain(url),
            "snippet": result.get("snippet") or "",
            "date": result.get("date"),
            "relevance": max(0.55, 0.85 - (i * 0.03)),
            "why_relevant": f"Ranked by Perplexity Search API for '{query}'",
            "engagement": {},
            "metadata": {
                "last_updated": result.get("last_updated"),
                "perplexity_search_id": data.get("id"),
            },
        })

    artifact = {
        "label": "perplexity",
        "provider": "perplexity",
        "mode": PERPLEXITY_MODE_SEARCH,
        "endpoint": "search",
        "query": query,
        "resultCount": len(items),
        "request": {
            k: v
            for k, v in payload.items()
            if k not in {"query"}
        },
        "responseId": data.get("id"),
        "serverTime": data.get("server_time"),
    }
    _log(f"Got {len(items)} Search API results")
    return items, artifact


def _sonar_search(
    query: str,
    date_range: tuple[str, str],
    config: dict,
    provider: str,
    api_key: str,
    url: str,
    model: str,
    deep: bool,
) -> tuple[list[dict], dict]:
    from_date, to_date = date_range
    timeout = 120 if deep else 30

    if deep:
        print("[Perplexity] Using Deep Research (~$0.90/query)", file=sys.stderr)

    prompt = (
        f"What has been happening with {query} between {from_date} and {to_date}? "
        "Include specific dates, names, numbers, and sources."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    json_data = _build_sonar_payload(prompt, model, date_range, config)
    if provider != "perplexity":
        json_data.pop("web_search_options", None)
        json_data.pop("reasoning_effort", None)

    _log(f"Querying {provider} {model} for '{query}' ({from_date} to {to_date})")

    async_artifact = {}
    if provider == "perplexity" and deep:
        data, async_artifact = _poll_async_sonar(json_data, headers, config)
    else:
        data = http.post(url, json_data, headers=headers, timeout=timeout)

    # Parse response
    choices = data.get("choices", [])
    if not choices:
        _log("No choices in response")
        return [], _empty_async_sonar_artifact(
            provider, model, deep, query, data, async_artifact,
            "empty_choices",
            "Async Deep Research completed without choices",
        )

    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    synthesis = message.get("content") or ""
    if not isinstance(synthesis, str):
        synthesis = ""
    if not synthesis:
        _log("Empty synthesis content")
        return [], _empty_async_sonar_artifact(
            provider, model, deep, query, data, async_artifact,
            "empty_synthesis",
            "Async Deep Research completed with empty synthesis",
        )

    citations = _extract_citations(data, choice)

    _log(f"Got synthesis ({len(synthesis)} chars) with {len(citations)} citations")

    # Build items list
    items = []

    # Primary item: the synthesis itself
    snippet = synthesis[:2000]
    items.append({
        "id": "PX1",
        "title": f"Perplexity {'Deep Research' if deep else 'Sonar'}: {query}",
        "url": "",
        "source_domain": "perplexity.ai",
        "snippet": snippet,
        "date": to_date,
        "relevance": 0.9,
        "why_relevant": f"AI synthesis of recent activity for '{query}'",
        "engagement": {"citations": len(citations)},
        "metadata": {
            "citations": citations,
            "usage": _usage(data),
            **async_artifact,
        },
    })

    # Individual items for each citation
    for i, cit in enumerate(citations):
        items.append({
            "id": f"PX{i + 2}",
            "title": cit["title"] or _domain(cit["url"]),
            "url": cit["url"],
            "source_domain": _domain(cit["url"]),
            "snippet": cit.get("snippet") or "",
            "date": cit.get("date"),
            "relevance": 0.7,
            "why_relevant": f"Cited in Perplexity synthesis for '{query}'",
            "engagement": {"citations": 1},
            "metadata": {"citations": [cit]},
        })

    artifact = {
        "label": "perplexity",
        "provider": provider,
        "mode": PERPLEXITY_MODE_SONAR,
        "endpoint": "async-sonar" if async_artifact else "sonar",
        "model": model,
        "deep": deep,
        "query": query,
        "synthesisLength": len(synthesis),
        "citationCount": len(citations),
        "usage": _usage(data),
        **async_artifact,
    }

    return items, artifact


def _merge_sonar_and_search(sonar_items: list[dict], search_items: list[dict]) -> list[dict]:
    if not sonar_items:
        return search_items
    merged = sonar_items[:1]
    seen_urls = {item.get("url") for item in merged if item.get("url")}
    for item in [*search_items, *sonar_items[1:]]:
        url = item.get("url")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        merged.append(item)
    return merged


def search(
    query: str,
    date_range: tuple[str, str],
    config: dict,
    deep: bool = False,
) -> tuple[list[dict], dict]:
    """Search via Perplexity Sonar Pro or Deep Research.

    Args:
        query: Search topic
        date_range: (from_date, to_date) as YYYY-MM-DD strings
        config: Must contain PERPLEXITY_API_KEY or OPENROUTER_API_KEY
        deep: Use Deep Research model (~$0.90/query) instead of Sonar Pro

    Returns:
        Tuple of (items list, artifact dict).
    """
    resolved = _provider(config, deep)
    if not resolved:
        _log("No PERPLEXITY_API_KEY or OPENROUTER_API_KEY configured, skipping")
        return [], {}
    provider, api_key, url, model = resolved
    mode = _mode(config, provider, deep)

    try:
        if mode == PERPLEXITY_MODE_SEARCH:
            return _search_api(query, date_range, config, api_key)
        if mode == PERPLEXITY_MODE_BOTH:
            search_items: list[dict] = []
            sonar_items: list[dict] = []
            search_artifact: dict = {}
            sonar_artifact: dict = {}
            try:
                search_items, search_artifact = _search_api(query, date_range, config, api_key)
            except Exception as e:
                _log(f"Search API leg failed in both mode: {e}")
                search_artifact = _error_artifact(e)
            try:
                sonar_items, sonar_artifact = _sonar_search(
                    query, date_range, config, provider, api_key, url, model, deep
                )
            except Exception as e:
                _log(f"Sonar leg failed in both mode: {e}")
                sonar_artifact = _error_artifact(e)
            items = _merge_sonar_and_search(sonar_items, search_items)
            return items, {
                "label": "perplexity",
                "provider": "perplexity",
                "mode": PERPLEXITY_MODE_BOTH,
                "query": query,
                "search": search_artifact,
                "sonar": sonar_artifact,
                "itemCount": len(items),
            }
        return _sonar_search(query, date_range, config, provider, api_key, url, model, deep)
    except http.HTTPError as e:
        if e.status_code == 401:
            _log(f"Invalid {provider} API key (401)")
        elif e.status_code == 429:
            _log(f"Rate limited by {provider} (429)")
        else:
            _log(f"HTTP error: {e}")
        return [], {}
    except AsyncDeepResearchTimeout as e:
        _log(f"Request timed out: {e}")
        return [], {
            "label": "perplexity",
            "provider": provider,
            "mode": PERPLEXITY_MODE_SONAR,
            "endpoint": "async-sonar",
            "model": model,
            "deep": deep,
            "query": query,
            "error": "timeout",
            **e.metadata,
        }
    except AsyncDeepResearchFailed as e:
        _log(f"Deep Research failed: {e}")
        return [], {
            "label": "perplexity",
            "provider": provider,
            "mode": PERPLEXITY_MODE_SONAR,
            "endpoint": "async-sonar",
            "model": model,
            "deep": deep,
            "query": query,
            "error": "failed",
            **e.metadata,
        }
    except AsyncDeepResearchPollError as e:
        _log(f"Deep Research poll failed: {e}")
        return [], {
            "label": "perplexity",
            "provider": provider,
            "mode": PERPLEXITY_MODE_SONAR,
            "endpoint": "async-sonar",
            "model": model,
            "deep": deep,
            "query": query,
            "error": "poll_error",
            **e.metadata,
        }
    except TimeoutError as e:
        _log(f"Request timed out: {e}")
        return [], {"label": "perplexity", "provider": provider, "error": "timeout"}
    except Exception as e:
        _log(f"Request failed: {e}")
        return [], {}
