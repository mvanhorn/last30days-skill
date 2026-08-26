"""Opt-in Parallel Search MCP adapter using stdlib Streamable HTTP."""

from __future__ import annotations

import json
import urllib.request
from typing import Any
from urllib.parse import urlparse

from . import http

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
_PROTOCOL_VERSION = "2025-03-26"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _decode_response(content_type: str, payload: bytes) -> dict[str, Any]:
    text = payload.decode("utf-8")
    if "text/event-stream" in content_type:
        messages = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if data:
                messages.append(json.loads(data))
        if not messages:
            raise RuntimeError("Parallel MCP returned an empty event stream")
        return messages[-1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise RuntimeError("Parallel MCP returned an invalid JSON-RPC response")
    return value


def _post(
    message: dict[str, Any], api_key: str | None, session_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": http.USER_AGENT,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        PARALLEL_MCP_URL,
        data=json.dumps(message, separators=(",", ":")).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=http.DEFAULT_TIMEOUT) as response:
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Parallel MCP response exceeded 4 MiB")
        result = _decode_response(response.headers.get("Content-Type", ""), payload) if payload else {}
        return result, response.headers.get("Mcp-Session-Id") or session_id


def _result(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("error"):
        error = response["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(f"Parallel MCP error: {detail}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Parallel MCP response is missing its result")
    return result


def _search_rows(tool_result: dict[str, Any]) -> list[dict[str, Any]]:
    structured = tool_result.get("structuredContent")
    candidates: Any = structured
    if isinstance(candidates, dict):
        candidates = candidates.get("results") or candidates.get("data") or candidates
    if isinstance(candidates, dict):
        candidates = candidates.get("results")
    if not isinstance(candidates, list):
        for content in tool_result.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "text":
                continue
            try:
                decoded = json.loads(content.get("text") or "")
            except (TypeError, ValueError):
                continue
            candidates = decoded.get("results") if isinstance(decoded, dict) else decoded
            if isinstance(candidates, list):
                break
    return [row for row in candidates or [] if isinstance(row, dict)]


def search(
    query: str, date_range: tuple[str, str], api_key: str | None = None, count: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover and invoke hosted ``web_search`` after explicit dispatcher opt-in."""
    initialized, session_id = _post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "last30days-skill", "version": "3"},
            },
        },
        api_key,
    )
    _result(initialized)
    _post(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        api_key,
        session_id,
    )
    tools_response, session_id = _post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        api_key,
        session_id,
    )
    tools = _result(tools_response).get("tools") or []
    if not any(isinstance(tool, dict) and tool.get("name") == "web_search" for tool in tools):
        raise RuntimeError("Parallel MCP did not advertise web_search")
    objective = f"Find useful public web evidence about {query} from {date_range[0]} through {date_range[1]}."
    called, _ = _post(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "web_search",
                "arguments": {"objective": objective, "search_queries": [query]},
            },
        },
        api_key,
        session_id,
    )
    tool_result = _result(called)
    if tool_result.get("isError"):
        raise RuntimeError("Parallel MCP web_search reported an error")
    items = []
    for row in _search_rows(tool_result)[:count]:
        url = str(row.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        excerpts = row.get("excerpts") or []
        if isinstance(excerpts, str):
            excerpts = [excerpts]
        snippet = "\n".join(str(value) for value in excerpts if value)
        items.append({
            "id": f"WPM{len(items) + 1}",
            "title": row.get("title") or urlparse(url).netloc,
            "url": url,
            "source_domain": urlparse(url).netloc.strip().lower(),
            "snippet": snippet[:500],
            "date": None,
            "relevance": 0.8,
            "why_relevant": "Parallel Search MCP result",
        })
    return items, {
        "label": "parallel-mcp",
        "webSearchQueries": [query],
        "resultCount": len(items),
    }
