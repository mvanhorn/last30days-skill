"""Opt-in Parallel Search MCP adapter using stdlib Streamable HTTP."""

from __future__ import annotations

import http.client as http_client
import json
import re
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
_DEADLINE_JITTER_SECONDS = 0.05

_BEARER_TOKEN_RE = re.compile(r"\b(Bearer\s+)\S+", re.IGNORECASE)

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


def _scrub_secret_text(text: Any, redactions: frozenset[str] = frozenset()) -> str:
    cleaned = str(text or "")
    cleaned = http._URL_USERINFO_RE.sub(r"\1", cleaned)
    cleaned = _BEARER_TOKEN_RE.sub(r"\1<redacted>", cleaned)
    if redactions:
        cleaned = http._scrub_fixture_value(cleaned, redactions=redactions)
    return cleaned[:500]


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


def _shutdown_socket(sock: Any) -> None:
    if sock is not None and hasattr(sock, "shutdown"):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def _effective_socket_timeout(
    request_timeout: float | None,
    remaining: float | None,
) -> tuple[float | None, bool]:
    """Return ``(socket_timeout, clamped_to_deadline)`` bounded by both limits."""
    if request_timeout is None and remaining is None:
        return None, False
    if request_timeout is None:
        return max(0.001, remaining), True
    if remaining is None:
        return max(0.001, request_timeout), False
    clamped = remaining <= request_timeout
    return max(0.001, min(request_timeout, remaining)), clamped


def _set_response_timeout(
    response: Any,
    remaining: float | None = None,
    *,
    request_timeout: float | None = None,
) -> bool:
    """Clamp response socket timeout to ``min(request_timeout, remaining)``."""
    timeout_val, clamped = _effective_socket_timeout(request_timeout, remaining)
    if timeout_val is None:
        return False
    sock = _response_socket(response)
    if sock is not None and hasattr(sock, "settimeout"):
        try:
            sock.settimeout(timeout_val)
        except OSError:
            pass
    return clamped


def _is_deadline_timeout(
    deadline_monotonic: float | None,
    cancel: threading.Event | None,
    *,
    clamped_to_deadline: bool = True,
) -> bool:
    if cancel is not None and cancel.is_set():
        return True
    if deadline_monotonic is None:
        return False
    now = time.monotonic()
    if now >= deadline_monotonic:
        return True
    return clamped_to_deadline and now >= (deadline_monotonic - _DEADLINE_JITTER_SECONDS)


def _abort_response(response: Any) -> None:
    """Unblock a worker thread waiting in recv() without taking BufferedReader's lock."""
    if response is None:
        return
    _shutdown_socket(_response_socket(response))


class _AbortState:
    """Tracks active socket/response across TCP connect, headers, and body reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._aborted = False
        self._sock: Any = None
        self._response: Any = None

    def register_socket(self, sock: Any) -> None:
        with self._lock:
            self._sock = sock
            aborted = self._aborted
        if aborted:
            _shutdown_socket(sock)
            raise http.DeadlineExceeded()

    def register_response(self, response: Any) -> None:
        with self._lock:
            self._response = response
            sock = _response_socket(response)
            if sock is not None:
                self._sock = sock
            aborted = self._aborted
        if aborted:
            _abort_response(response)
            _shutdown_socket(sock)
            raise http.DeadlineExceeded()

    def clear(self) -> None:
        with self._lock:
            self._response = None
            self._sock = None

    def abort(self) -> None:
        with self._lock:
            self._aborted = True
            sock = self._sock
            response = self._response
        _shutdown_socket(sock)
        if response is not None:
            _abort_response(response)


def _make_abortable_handlers(abort_state: _AbortState) -> list[Any]:
    def _create_tracked_connection(
        address: tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: Any = None,
    ) -> socket.socket:
        sock = socket.create_connection(address, timeout, source_address)
        try:
            abort_state.register_socket(sock)
        except BaseException:
            sock.close()
            raise
        return sock

    class _AbortableHTTPConnection(http_client.HTTPConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = _create_tracked_connection

        def connect(self) -> None:
            super().connect()
            abort_state.register_socket(self.sock)

    class _AbortableHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_AbortableHTTPConnection, req)

    handlers: list[Any] = [_NoRedirect(), _AbortableHTTPHandler()]
    if hasattr(http_client, "HTTPSConnection"):
        class _AbortableHTTPSConnection(http_client.HTTPSConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._create_connection = _create_tracked_connection

            def connect(self) -> None:
                super().connect()
                abort_state.register_socket(self.sock)

        class _AbortableHTTPSHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                return self.do_open(_AbortableHTTPSConnection, req, context=self._context)

        handlers.append(_AbortableHTTPSHandler())
    return handlers


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
    request_timeout: float | None = None,
) -> dict[str, Any]:
    if "text/event-stream" not in response.headers.get("Content-Type", "").lower():
        remaining = _remaining_seconds(deadline_monotonic, cancel)
        clamped = _set_response_timeout(response, remaining, request_timeout=request_timeout)
        if hasattr(response, "read1"):
            chunks: list[bytes] = []
            total = 0
            while True:
                remaining = _remaining_seconds(deadline_monotonic, cancel)
                clamped = _set_response_timeout(response, remaining, request_timeout=request_timeout)
                to_read = min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total + 1)
                try:
                    chunk = response.read1(to_read)
                except TimeoutError as exc:
                    if _is_deadline_timeout(deadline_monotonic, cancel, clamped_to_deadline=clamped):
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
                if _is_deadline_timeout(deadline_monotonic, cancel, clamped_to_deadline=clamped):
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
            scan_pos = 0
            while True:
                line_start = 0
                while True:
                    nl = buf.find(b"\n", scan_pos)
                    if nl < 0:
                        if line_start:
                            del buf[:line_start]
                        scan_pos = len(buf)
                        break
                    raw_line = bytes(buf[line_start:nl + 1])
                    line_start = nl + 1
                    scan_pos = line_start
                    result = _process_line(raw_line)
                    if result is not None:
                        return result
                remaining = _remaining_seconds(deadline_monotonic, cancel)
                clamped = _set_response_timeout(
                    response, remaining, request_timeout=request_timeout,
                )
                to_read = min(_READ_CHUNK_BYTES, _MAX_RESPONSE_BYTES - total + 1)
                try:
                    chunk = response.read1(to_read)
                except TimeoutError as exc:
                    if _is_deadline_timeout(
                        deadline_monotonic, cancel, clamped_to_deadline=clamped,
                    ):
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
                clamped = _set_response_timeout(
                    response, remaining, request_timeout=request_timeout,
                )
                try:
                    raw_line = response.readline(_MAX_RESPONSE_BYTES - total + 1)
                except TimeoutError as exc:
                    if _is_deadline_timeout(
                        deadline_monotonic, cancel, clamped_to_deadline=clamped,
                    ):
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
    base_timeout = float(http.DEFAULT_TIMEOUT if timeout is None else timeout)
    effective_timeout, clamped_to_deadline = _effective_socket_timeout(base_timeout, remaining)
    redactions = frozenset({api_key}) if isinstance(api_key, str) and len(api_key) >= 4 else frozenset()

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

    def _do_request(abort_state: _AbortState) -> tuple[dict[str, Any], str | None]:
        try:
            with urllib.request.build_opener(*_make_abortable_handlers(abort_state)).open(
                request, timeout=max(0.001, effective_timeout or base_timeout),
            ) as response:
                abort_state.register_response(response)
                try:
                    if cancel is not None and cancel.is_set():
                        raise http.DeadlineExceeded()
                    result = (
                        _read_response(
                            response,
                            message.get("id"),
                            deadline_monotonic=deadline_monotonic,
                            cancel=cancel,
                            request_timeout=base_timeout,
                        )
                        if message is not None
                        else {}
                    )
                    return result, response.headers.get("Mcp-Session-Id") or session_id
                finally:
                    abort_state.clear()
        except urllib.error.HTTPError as exc:
            clean_reason = _scrub_secret_text(exc.reason, redactions)
            clean_url = _scrub_secret_text(exc.url, redactions)
            if clean_reason != str(exc.reason or "") or clean_url != str(exc.url or ""):
                try:
                    exc.close()
                except Exception:
                    pass
                raise urllib.error.HTTPError(
                    clean_url, exc.code, clean_reason, exc.headers, None,
                ) from None
            raise
        except TimeoutError as exc:
            if _is_deadline_timeout(
                deadline_monotonic, cancel, clamped_to_deadline=clamped_to_deadline,
            ):
                raise http.DeadlineExceeded() from exc
            raise
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if (
                _is_deadline_timeout(
                    deadline_monotonic, cancel, clamped_to_deadline=clamped_to_deadline,
                )
                and (isinstance(reason, TimeoutError) or "timed out" in str(reason).lower())
            ):
                raise http.DeadlineExceeded() from exc
            clean_reason = _scrub_secret_text(reason, redactions)
            if clean_reason != str(reason or ""):
                raise urllib.error.URLError(clean_reason) from None
            raise
        finally:
            abort_state.clear()

    if deadline_monotonic is None and cancel is None:
        return _do_request(_AbortState())

    abort_state = _AbortState()
    future: Future = Future()

    def _worker() -> None:
        try:
            if cancel is not None and cancel.is_set():
                raise http.DeadlineExceeded()
            future.set_result(_do_request(abort_state))
        except BaseException as exc:
            future.set_exception(exc)

    threading.Thread(target=_worker, name="parallel-mcp-request", daemon=True).start()
    wait_deadline = (
        deadline_monotonic
        if deadline_monotonic is not None
        else (time.monotonic() + (effective_timeout or base_timeout))
    )
    while True:
        if cancel is not None and cancel.is_set():
            abort_state.abort()
            raise http.DeadlineExceeded()
        rem = wait_deadline - time.monotonic()
        if rem <= 0:
            if future.done():
                return future.result()
            abort_state.abort()
            raise http.DeadlineExceeded()
        poll = min(rem, 0.05) if cancel is not None else rem
        try:
            return future.result(timeout=poll)
        except TimeoutError:
            if future.done():
                return future.result()
            if cancel is None:
                abort_state.abort()
                raise http.DeadlineExceeded()


def _result(response: dict[str, Any], *, redactions: frozenset[str] = frozenset()) -> dict[str, Any]:
    if response.get("error"):
        error = response["error"]
        detail = error.get("message") if isinstance(error, dict) else str(error)
        safe_detail = _scrub_secret_text(detail, redactions)
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
                    except (OSError, RuntimeError, ValueError, http.HTTPError, http_client.HTTPException):
                        # Cleanup is best-effort: unsupported DELETEs, malformed responses,
                        # or a network failure must not hide search results or the original error.
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
