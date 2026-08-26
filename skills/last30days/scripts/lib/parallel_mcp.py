"""Opt-in Parallel Search MCP adapter using stdlib Streamable HTTP."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from . import dates, http

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
_PROTOCOL_VERSION = "2025-03-26"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The endpoint is fixed. Never forward credentials, session IDs, or
        # search data to a redirect target, including an HTTPS downgrade.
        return None


def _matching_response(payload: bytes, request_id: int | None) -> dict[str, Any] | None:
    value = json.loads(payload)
    # The negotiated 2025-03-26 protocol permits batched SSE messages.
    for message in value if isinstance(value, list) else [value]:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("Parallel MCP returned an invalid JSON-RPC response")
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message
    return None


def _read_response(response: Any, request_id: int | None) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        payload = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("Parallel MCP response exceeded 4 MiB")
        if request_id is None and not payload:
            return {}
        result = _matching_response(payload, request_id) if payload else None
        if result is not None:
            return result
    else:
        data = []
        total = 0
        while True:
            line = response.readline(_MAX_RESPONSE_BYTES - total + 1)
            total += len(line)
            if total > _MAX_RESPONSE_BYTES:
                raise RuntimeError("Parallel MCP response exceeded 4 MiB")
            if not line:
                break
            line = line.rstrip(b"\r\n")
            if not line and data:
                result = _matching_response(b"\n".join(data), request_id)
                if result is not None:
                    # A valid response completes the request, even if the
                    # server keeps the stream open or sends more notifications.
                    return result
                data = []
            elif line.startswith(b"data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(b" ") else value)
    raise RuntimeError("Parallel MCP response is missing the requested JSON-RPC result")


def _request(
    message: dict[str, Any] | None, api_key: str | None, session_id: str | None = None,
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
    if message is None or message.get("method") != "initialize":
        headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
    request = urllib.request.Request(
        PARALLEL_MCP_URL,
        data=json.dumps(message, separators=(",", ":")).encode("utf-8") if message is not None else None,
        headers=headers,
        method="POST" if message is not None else "DELETE",
    )
    with urllib.request.build_opener(_NoRedirect()).open(request, timeout=http.DEFAULT_TIMEOUT) as response:
        result = _read_response(response, message.get("id")) if message is not None else {}
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
    payloads = [tool_result.get("structuredContent")]
    for content in tool_result.get("content") or []:
        if not isinstance(content, dict) or content.get("type") != "text":
            continue
        try:
            payloads.append(json.loads(content.get("text") or ""))
        except (TypeError, ValueError):
            continue
    for candidates in payloads:
        if isinstance(candidates, dict) and "data" in candidates and "results" not in candidates:
            candidates = candidates["data"]
        if isinstance(candidates, dict):
            candidates = candidates.get("results")
        if isinstance(candidates, list):
            return [row for row in candidates if isinstance(row, dict)]
    raise RuntimeError("Parallel MCP web_search returned no results array")


def search(
    query: str, date_range: tuple[str, str], api_key: str | None = None, count: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover and invoke hosted ``web_search`` after explicit dispatcher opt-in."""
    initialized, session_id = _request(
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
    try:
        if _result(initialized).get("protocolVersion") != _PROTOCOL_VERSION:
            raise RuntimeError("Parallel MCP negotiated an unsupported protocol version")
        _request(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            api_key,
            session_id,
        )
        request_id = 2
        params = {}
        seen_cursors = set()
        while True:
            tools_response, _ = _request(
                {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params},
                api_key,
                session_id,
            )
            request_id += 1
            page = _result(tools_response)
            tools = page.get("tools") or []
            if any(isinstance(tool, dict) and tool.get("name") == "web_search" for tool in tools):
                break
            cursor = page.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors or len(seen_cursors) >= 20:
                raise RuntimeError("Parallel MCP did not advertise web_search")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
        objective = f"Find useful public web evidence about {query} from {date_range[0]} through {date_range[1]}."
        called, _ = _request(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "web_search",
                    "arguments": {"objective": objective, "search_queries": [query]},
                },
            },
            api_key,
            session_id,
        )
    finally:
        if session_id:
            try:
                _request(None, api_key, session_id)
            except urllib.error.HTTPError as error:
                error.close()
            except (OSError, RuntimeError, ValueError):
                # Cleanup is best-effort: unsupported DELETEs or a network
                # failure must not hide search results or the original error.
                pass
    tool_result = _result(called)
    if tool_result.get("isError"):
        raise RuntimeError("Parallel MCP web_search reported an error")
    items = []
    for row in _search_rows(tool_result):
        if len(items) >= count:
            break
        url = str(row.get("url") or "")
        try:
            parsed_url = urlparse(url)
        except ValueError:
            continue
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue
        raw_date = row.get("publish_date")
        parsed_date = dates.parse_date(raw_date[:10]) if isinstance(raw_date, str) else None
        pub_date = parsed_date.date().isoformat() if parsed_date else None
        # Match the paid grounding backends: only dated evidence inside the
        # requested window qualifies, including when --as-of is historical.
        if not pub_date or not date_range[0] <= pub_date <= date_range[1]:
            continue
        excerpts = row.get("excerpts") or []
        if isinstance(excerpts, str):
            excerpts = [excerpts]
        snippet = "\n".join(str(value) for value in excerpts if value) if isinstance(excerpts, list) else ""
        items.append({
            "id": f"WPM{len(items) + 1}",
            "title": row.get("title") or parsed_url.netloc,
            "url": url,
            "source_domain": parsed_url.netloc.strip().lower(),
            "snippet": snippet[:500],
            "date": pub_date,
            "relevance": 0.8,
            "why_relevant": "Parallel Search MCP result",
        })
    return items, {
        "label": "parallel-mcp",
        "webSearchQueries": [query],
        "resultCount": len(items),
    }
