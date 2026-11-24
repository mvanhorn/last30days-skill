"""Opt-in Parallel Search MCP adapter using stdlib Streamable HTTP."""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlparse

from . import dates, http, usage

PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"
_PROTOCOL_VERSION = "2025-03-26"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_SSE_LINES = 10_000
_MAX_SSE_EVENTS = 1_000
_MAX_TOOLS_LIST_PAGES = 5
_MAX_SESSION_REQUESTS = 9
_DEFAULT_SEARCH_TIMEOUT = 60.0
_CLEANUP_TIMEOUT = 3.0
_READ_CHUNK_BYTES = 65_536

# Threads the shared search deadline/cancel/timeout budget into _request without
# adding extra kwargs to search()'s _request calls (preserving 3-arg test mocks).
_REQUEST_BUDGET: ContextVar[tuple[float | None, threading.Event | None, float | None] | None] = ContextVar(
    "last30days_parallel_mcp_budget",
    default=None,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # The endpoint is fixed. Never forward credentials, session IDs, or
        # search data to a redirect target, including an HTTPS downgrade.
        return None


def _remaining_seconds(
    deadline_monotonic: float | None,
    cancel: threading.Event | None,
) -> float | None:
    if cancel is not None and cancel.is_set():
        raise http.DeadlineExceeded()
    if deadline_monotonic is None:
        return None
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise http.DeadlineExceeded()
    return remaining


def _response_socket(response: Any) -> Any:
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    return getattr(raw, "_sock", None) or getattr(fp, "_sock", None) or getattr(response, "_sock", None)


def _set_response_timeout(response: Any, remaining: float | None) -> None:
    if remaining is None:
        return
    sock = _response_socket(response)
    if sock is not None and hasattr(sock, "settimeout"):
        try:
            sock.settimeout(max(0.001, remaining))
        except OSError:
            pass


def _abort_response(response: Any) -> None:
    """Unblock a worker thread waiting in recv() without taking BufferedReader's lock."""
    if response is None:
        return
    sock = _response_socket(response)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _matching_response(payload: bytes, request_id: int | None) -> dict[str, Any] | None:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Parallel MCP returned an invalid JSON-RPC response") from exc
    # The negotiated 2025-03-26 protocol permits batched SSE messages.
    for message in value if isinstance(value, list) else [value]:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise RuntimeError("Parallel MCP returned an invalid JSON-RPC response")
        if message.get("id") == request_id and ("result" in message or "error" in message):
            return message
    return None


def _read_response(
    response: Any,
    request_id: int | None,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        remaining = _remaining_seconds(deadline_monotonic, cancel)
        _set_response_timeout(response, remaining)
        if hasattr(response, "read1"):
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = _remaining_seconds(deadline_monotonic, cancel)
                _set_response_timeout(response, remaining)
                to_read = min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total + 1)
                try:
                    chunk = response.read1(to_read)
                except TimeoutError as exc:
                    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                        raise http.DeadlineExceeded() from exc
                    raise
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise RuntimeError("Parallel MCP response exceeded 4 MiB")
                chunks.append(chunk)
            payload = b"".join(chunks)
        else:
            try:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
            except TimeoutError as exc:
                if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                    raise http.DeadlineExceeded() from exc
                raise
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise RuntimeError("Parallel MCP response exceeded 4 MiB")
        _remaining_seconds(deadline_monotonic, cancel)
        if request_id is None and not payload:
            return {}
        result = _matching_response(payload, request_id) if payload else None
        if result is not None:
            return result
    else:
        data: list[bytes] = []
        total = 0
        lines_seen = 0
        events_seen = 0

        def _process_line(raw_line: bytes) -> dict[str, Any] | None:
            nonlocal lines_seen, events_seen, data
            lines_seen += 1
            if lines_seen > _MAX_SSE_LINES:
                raise RuntimeError("Parallel MCP SSE stream exceeded line limit")
            line = raw_line.rstrip(b"\r\n")
            if not line and data:
                events_seen += 1
                if events_seen > _MAX_SSE_EVENTS:
                    raise RuntimeError("Parallel MCP SSE stream exceeded event limit")
                result = _matching_response(b"\n".join(data), request_id)
                if result is not None:
                    # A valid response completes the request, even if the
                    # server keeps the stream open or sends more notifications.
                    return result
                data = []
            elif line.startswith(b"data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(b" ") else value)
            return None

        if hasattr(response, "read1"):
            buf = bytearray()
            while True:
                while True:
                    nl = buf.find(b"\n")
                    if nl < 0:
                        break
                    raw_line = bytes(buf[:nl + 1])
                    del buf[:nl + 1]
                    result = _process_line(raw_line)
                    if result is not None:
                        return result
                remaining = _remaining_seconds(deadline_monotonic, cancel)
                _set_response_timeout(response, remaining)
                to_read = min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total + 1)
                try:
                    chunk = response.read1(to_read)
                except TimeoutError as exc:
                    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                        raise http.DeadlineExceeded() from exc
                    raise
                if not chunk:
                    if buf:
                        result = _process_line(bytes(buf))
                        if result is not None:
                            return result
                    break
                total += len(chunk)
                if total > _MAX_RESPONSE_BYTES:
                    raise RuntimeError("Parallel MCP response exceeded 4 MiB")
                buf.extend(chunk)
        else:
            while True:
                remaining = _remaining_seconds(deadline_monotonic, cancel)
                _set_response_timeout(response, remaining)
                try:
                    raw_line = response.readline(_MAX_RESPONSE_BYTES - total + 1)
                except TimeoutError as exc:
                    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                        raise http.DeadlineExceeded() from exc
                    raise
                total += len(raw_line)
                if total > _MAX_RESPONSE_BYTES:
                    raise RuntimeError("Parallel MCP response exceeded 4 MiB")
                if not raw_line:
                    break
                result = _process_line(raw_line)
                if result is not None:
                    return result
    raise RuntimeError("Parallel MCP response is missing the requested JSON-RPC result")


def _request(
    message: dict[str, Any] | None,
    api_key: str | None,
    session_id: str | None = None,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
    timeout: float | None = None,
) -> tuple[dict[str, Any], str | None]:
    budget = _REQUEST_BUDGET.get()
    if budget is not None:
        if deadline_monotonic is None:
            deadline_monotonic = budget[0]
        if cancel is None:
            cancel = budget[1]
        if timeout is None:
            timeout = budget[2]
    http._check_http_scheme(PARALLEL_MCP_URL)
    remaining = _remaining_seconds(deadline_monotonic, cancel)
    effective_timeout = float(http.DEFAULT_TIMEOUT if timeout is None else timeout)
    if remaining is not None:
        effective_timeout = min(effective_timeout, remaining)

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
    if api_key and message is not None and message.get("method") == "tools/call":
        usage.begin("parallel")

    def _do_request(
        active_box: list[Any], lock: threading.Lock,
    ) -> tuple[dict[str, Any], str | None]:
        try:
            with urllib.request.build_opener(_NoRedirect()).open(
                request, timeout=max(0.001, effective_timeout),
            ) as response:
                with lock:
                    active_box[0] = response
                try:
                    if cancel is not None and cancel.is_set():
                        raise http.DeadlineExceeded()
                    result = (
                        _read_response(
                            response,
                            message.get("id"),
                            deadline_monotonic=deadline_monotonic,
                            cancel=cancel,
                        )
                        if message is not None
                        else {}
                    )
                    return result, response.headers.get("Mcp-Session-Id") or session_id
                finally:
                    with lock:
                        active_box[0] = None
        except TimeoutError as exc:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                raise http.DeadlineExceeded() from exc
            raise
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if (
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
                and (isinstance(reason, TimeoutError) or "timed out" in str(reason).lower())
            ):
                raise http.DeadlineExceeded() from exc
            raise

    if deadline_monotonic is None and cancel is None:
        return _do_request([None], threading.Lock())

    active_box: list[Any] = [None]
    lock = threading.Lock()
    future: Future = Future()

    def _worker() -> None:
        try:
            if cancel is not None and cancel.is_set():
                raise http.DeadlineExceeded()
            future.set_result(_do_request(active_box, lock))
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=_worker, daemon=True).start()
    wait_deadline = (
        deadline_monotonic
        if deadline_monotonic is not None
        else (time.monotonic() + effective_timeout)
    )
    while True:
        if cancel is not None and cancel.is_set():
            with lock:
                _abort_response(active_box[0])
            raise http.DeadlineExceeded()
        rem = wait_deadline - time.monotonic()
        if rem <= 0:
            if future.done():
                return future.result()
            with lock:
                _abort_response(active_box[0])
            raise http.DeadlineExceeded()
        poll = min(rem, 0.05) if cancel is not None else rem
        try:
            return future.result(timeout=poll)
        except TimeoutError:
            if future.done():
                return future.result()
            if cancel is None:
                with lock:
                    _abort_response(active_box[0])
                raise http.DeadlineExceeded()


def _result(response: dict[str, Any], *, redactions: frozenset[str] = frozenset()) -> dict[str, Any]:
    if response.get("error"):
        error = response["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        safe_detail = http._scrub_fixture_value(str(detail or ""), redactions=redactions)[:500]
        raise RuntimeError(f"Parallel MCP error: {safe_detail}")
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
    query: str,
    date_range: tuple[str, str],
    api_key: str | None = None,
    count: int = 5,
    *,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Discover and invoke hosted ``web_search`` after explicit dispatcher opt-in."""
    effective_deadline = (
        deadline_monotonic
        if deadline_monotonic is not None
        else (time.monotonic() + _DEFAULT_SEARCH_TIMEOUT)
    )
    redactions = frozenset({api_key}) if isinstance(api_key, str) and len(api_key) >= 4 else frozenset()
    requests_issued = 0

    def _issue(message: dict[str, Any], session_id: str | None = None) -> tuple[dict[str, Any], str | None]:
        nonlocal requests_issued
        _remaining_seconds(effective_deadline, cancel)
        if requests_issued >= _MAX_SESSION_REQUESTS - 1:
            raise RuntimeError("Parallel MCP session request limit exceeded")
        requests_issued += 1
        if session_id is None and message.get("method") == "initialize":
            return _request(message, api_key)
        return _request(message, api_key, session_id)

    token = _REQUEST_BUDGET.set((effective_deadline, cancel, None))
    session_id: str | None = None
    try:
        initialized, session_id = _issue(
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
        )
        if _result(initialized, redactions=redactions).get("protocolVersion") != _PROTOCOL_VERSION:
            raise RuntimeError("Parallel MCP negotiated an unsupported protocol version")
        _issue(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            session_id,
        )
        request_id = 2
        params: dict[str, Any] = {}
        seen_cursors: set[str] = set()
        pages_fetched = 0
        while True:
            tools_response, _ = _issue(
                {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params},
                session_id,
            )
            request_id += 1
            pages_fetched += 1
            page = _result(tools_response, redactions=redactions)
            tools = page.get("tools") or []
            if any(isinstance(tool, dict) and tool.get("name") == "web_search" for tool in tools):
                break
            cursor = page.get("nextCursor")
            if (
                not isinstance(cursor, str)
                or not cursor
                or cursor in seen_cursors
                or pages_fetched >= _MAX_TOOLS_LIST_PAGES
            ):
                raise RuntimeError("Parallel MCP did not advertise web_search")
            seen_cursors.add(cursor)
            params = {"cursor": cursor}
        objective = f"Find useful public web evidence about {query} from {date_range[0]} through {date_range[1]}."
        called, _ = _issue(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {
                    "name": "web_search",
                    "arguments": {"objective": objective, "search_queries": [query]},
                },
            },
            session_id,
        )
    finally:
        try:
            if session_id and (cancel is None or not cancel.is_set()):
                rem = effective_deadline - time.monotonic()
                if rem > 0:
                    cleanup_timeout = min(_CLEANUP_TIMEOUT, rem)
                    cleanup_token = _REQUEST_BUDGET.set(
                        (time.monotonic() + cleanup_timeout, cancel, cleanup_timeout)
                    )
                    try:
                        _request(None, api_key, session_id)
                    except urllib.error.HTTPError as error:
                        error.close()
                    except (OSError, RuntimeError, ValueError, http.HTTPError):
                        # Cleanup is best-effort: unsupported DELETEs or a network
                        # failure must not hide search results or the original error.
                        pass
                    finally:
                        _REQUEST_BUDGET.reset(cleanup_token)
        finally:
            _REQUEST_BUDGET.reset(token)

    tool_result = _result(called, redactions=redactions)
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
