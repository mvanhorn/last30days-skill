"""Owned GET transport for operation deadlines, including DNS and body reads."""

from __future__ import annotations

import base64
from email.message import Message
import http.client as http_client
import io
import json
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
from typing import Any, Callable
import urllib.error
from urllib.parse import urlsplit
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import subproc

# Reserve TERM and KILL/reap time inside the caller's operation budget.
CLEANUP_GRACE_SECONDS = 0.1
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_META_BYTES = 256 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024
_MAX_HEADER_COUNT = 256
_MAX_HEADER_BYTES = 64 * 1024
_REDIRECT_POLICY_ATTR = "_last30days_redirect_policy"
_STRICT_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PRIORITY_RESPONSE_HEADERS = frozenset({
    "retry-after",
    "x-l30d-charge-credits",
    "x-l30d-session",
    "x-l30d-deadline",
    "x-rate-limit-retry-after-seconds",
    "x-ratelimit-reset",
    "x-ratelimit-remaining",
    "x-ratelimit-used",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "content-type",
    "content-length",
    "content-encoding",
    "location",
    "etag",
    "last-modified",
    "mcp-session-id",
})


class GetTimeout(TimeoutError):
    def __init__(self, message, *, started):
        super().__init__(message)
        self.started = started


class GetLaunchError(OSError):
    """GET worker launch failed before the worker received a request."""


class ResponseTooLarge(OSError):
    """Raised when an upstream response body exceeds the configured byte limit."""


def _error_record(exc):
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return {"kind": "URLError", "reason": _error_record(reason) if isinstance(reason, BaseException) else str(reason)}
    if isinstance(exc, http_client.IncompleteRead):
        return {"kind": "IncompleteRead", "message": str(exc), "expected": exc.expected}
    return {"kind": type(exc).__name__, "message": str(exc), "errno": getattr(exc, "errno", None)}


def _error_from_record(record):
    from lib import public_host

    if record["kind"] == "URLError":
        reason = record["reason"]
        return urllib.error.URLError(_error_from_record(reason) if isinstance(reason, dict) else reason)
    if record["kind"] == "IncompleteRead":
        return http_client.IncompleteRead(b"", record.get("expected"))
    kinds = {
        "gaierror": socket.gaierror,
        "TimeoutError": TimeoutError,
        "ConnectionResetError": ConnectionResetError,
        "ConnectionRefusedError": ConnectionRefusedError,
        "NonPublicAddressError": public_host.NonPublicAddressError,
        "OSError": OSError,
        "ResponseTooLarge": ResponseTooLarge,
        "ValueError": ValueError,
        "SSLError": ssl.SSLError,
        "SSLCertVerificationError": ssl.SSLCertVerificationError,
    }
    kind = kinds.get(record["kind"], OSError)
    if record.get("errno") is not None:
        return kind(record["errno"], record["message"])
    return kind(record["message"])


def _url_matches_policy(url: str, policy: dict[str, Any]) -> bool:
    """Validate ``url`` against a declarative JSON-serializable redirect policy."""
    from lib import public_host

    if not isinstance(url, str) or not url or url != url.strip():
        return False
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    require_public = bool(policy.get("require_public_syntax", True))
    if require_public and not public_host.is_public_address_syntax(url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    scheme = parts.scheme.lower()
    https_only = bool(policy.get("https_only", False))
    if https_only:
        if scheme != "https":
            return False
    elif scheme not in ("http", "https"):
        return False
    if parts.username is not None or parts.password is not None or "\\" in parts.netloc:
        return False
    allowed_ports_raw = policy.get("allowed_ports")
    if allowed_ports_raw is not None:
        allowed_ports = frozenset(int(p) for p in allowed_ports_raw)
        effective_port = port if port is not None else (443 if scheme == "https" else 80)
        if effective_port not in allowed_ports:
            return False
    raw_host = parts.hostname or ""
    if not raw_host or not raw_host.isascii():
        return False
    host = raw_host[:-1].lower() if raw_host.endswith(".") else raw_host.lower()
    exact_hosts = frozenset(str(h).lower() for h in (policy.get("exact_hosts") or ()))
    suffix_hosts = tuple(str(s).lower() for s in (policy.get("suffix_hosts") or ()))
    if exact_hosts or suffix_hosts:
        if require_public:
            if len(host) > 253 or not _STRICT_HOSTNAME_RE.fullmatch(host):
                return False
            if public_host._parse_ip_literal(host) is not None:
                return False
        return host in exact_hosts or host.endswith(suffix_hosts)
    return True


def _build_redirect_guard(policy: dict[str, Any]):
    """Build a redirect-guard callable from a declarative redirect policy."""
    allow_redirects = bool(policy.get("allow_redirects", True))

    def _guard(newurl: str) -> bool:
        if not allow_redirects:
            return False
        return _url_matches_policy(newurl, policy)

    return _guard


def _serialize_headers(raw_headers) -> list[list[str]]:
    if not raw_headers:
        return []
    priority: list[tuple[str, str]] = []
    other: list[tuple[str, str]] = []
    for key, value in raw_headers.items():
        k = str(key)
        v = str(value)
        if k.lower() in _PRIORITY_RESPONSE_HEADERS:
            priority.append((k, v))
        else:
            other.append((k, v))
    pairs: list[list[str]] = []
    used = 0
    for k, v in (*priority, *other):
        if len(pairs) >= _MAX_HEADER_COUNT:
            break
        pair = [k, v]
        # Measure actual ASCII JSON-encoded length (ensure_ascii=True) so
        # \uXXXX escaping cannot expand metadata past MAX_META_BYTES and
        # stdout stays pure ASCII on Windows cp1252 under python -I.
        entry_len = len(json.dumps(pair)) + 2
        if used + entry_len > _MAX_HEADER_BYTES:
            if k.lower() in _PRIORITY_RESPONSE_HEADERS:
                continue
            break
        used += entry_len
        pairs.append(pair)
    return pairs


def _declared_content_length(response) -> int | None:
    if isinstance(response, http_client.HTTPResponse):
        length = response.length
        return length if isinstance(length, int) and not isinstance(length, bool) and length >= 0 else None
    headers = getattr(response, "headers", None)
    if not headers or not hasattr(headers, "get"):
        return None
    raw = headers.get("Content-Length")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _check_incomplete_read(response, *, truncate: bool) -> None:
    if (
        not truncate
        and isinstance(response, http_client.HTTPResponse)
        and not response.chunked
        and getattr(response, "_method", None) != "HEAD"
        and isinstance(response.length, int)
        and response.length > 0
    ):
        raise http_client.IncompleteRead(b"", response.length)


def _stream_body_to_file(response, body_path: str, limit: int, *, truncate: bool) -> None:
    if not truncate:
        declared = _declared_content_length(response)
        if declared is not None and declared > limit:
            raise ResponseTooLarge(
                f"Response Content-Length {declared} exceeds limit of {limit} bytes"
            )
    budget = limit if truncate else limit + 1
    total = 0
    write_flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(body_path, write_flags)
    with os.fdopen(fd, "wb") as out:
        while total < budget:
            to_read = min(_STREAM_CHUNK_BYTES, budget - total)
            chunk = response.read(to_read)
            if not chunk:
                break
            if not truncate and total + len(chunk) > limit:
                raise ResponseTooLarge(f"Response body exceeded limit of {limit} bytes")
            if truncate and total + len(chunk) > limit:
                chunk = chunk[: limit - total]
            out.write(chunk)
            total += len(chunk)
    _check_incomplete_read(response, truncate=truncate)


def _read_body_inline(response, limit: int, *, truncate: bool) -> bytes:
    if not truncate:
        declared = _declared_content_length(response)
        if declared is not None and declared > limit:
            raise ResponseTooLarge(
                f"Response Content-Length {declared} exceeds limit of {limit} bytes"
            )
    read_size = limit if truncate else limit + 1
    data = response.read(read_size)
    if data is None:
        return b""
    if truncate:
        return data[:limit]
    if len(data) > limit:
        raise ResponseTooLarge(f"Response body exceeded limit of {limit} bytes")
    _check_incomplete_read(response, truncate=truncate)
    return data


def get(
    req: urllib.request.Request,
    *,
    timeout: float,
    deadline_monotonic: float,
    cancel: threading.Event | None = None,
    read_body: bool = True,
    read_error_body: bool = True,
    with_headers: bool = False,
    max_bytes: int = MAX_RESPONSE_BYTES,
    public_only: bool | None = None,
    redirect_policy: dict[str, Any] | None = None,
    bypass_proxy: bool = False,
    on_start: Callable[[], None] | None = None,
):
    """Run one GET in a child process killed at the deadline.

    Returns ``(status, body, error)``, or ``(status, body, error, headers)``
    with ``with_headers`` (the response headers as name/value pairs).
    """
    from lib import http

    if req.get_method() != "GET":
        raise ValueError("bounded transport supports GET only")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    effective_public_only = (
        bool(getattr(req, http._PUBLIC_ONLY_ATTR, False))
        if public_only is None
        else bool(public_only)
    )
    effective_redirect_policy = (
        getattr(req, _REDIRECT_POLICY_ATTR, None)
        if redirect_policy is None
        else redirect_policy
    )
    body_dir: str | None = None
    body_path: str | None = None
    try:
        if read_body or read_error_body:
            body_dir = tempfile.mkdtemp(prefix="last30days-bget-")
            try:
                os.chmod(body_dir, 0o700)
            except OSError:
                pass
            body_path = os.path.join(body_dir, "body.bin")
            create_flags = (
                os.O_CREAT
                | os.O_EXCL
                | os.O_WRONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_BINARY", 0)
            )
            fd = os.open(body_path, create_flags, 0o600)
            os.close(fd)
        payload: dict[str, Any] = {
            "url": req.full_url,
            "headers": req.headers,
            "unredirected_headers": req.unredirected_hdrs,
            "origin_req_host": req.origin_req_host,
            "timeout": timeout,
            "read_body": read_body,
            "read_error_body": read_error_body,
            "with_headers": with_headers,
            "max_bytes": max_bytes,
            "body_path": body_path,
            "public_only": effective_public_only,
            "redirect_policy": effective_redirect_policy,
            "bypass_proxy": bool(bypass_proxy),
        }
        transport_deadline = deadline_monotonic - 2 * CLEANUP_GRACE_SECONDS
        try:
            result = subproc.run_with_timeout(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve())],
                timeout=max(0, transport_deadline - time.monotonic()),
                deadline_monotonic=transport_deadline,
                input_text=json.dumps(payload),
                cancel=cancel,
                cleanup_grace=CLEANUP_GRACE_SECONDS,
                capture_limit_bytes=MAX_META_BYTES,
                on_pid=(lambda _pid: on_start()) if on_start is not None else None,
            )
        except subproc.SubprocTimeout as exc:
            raise GetTimeout("GET exceeded operation deadline or was cancelled", started=exc.started) from exc
        except OSError as exc:
            if not getattr(exc, "_last30days_subproc_launch_failed", False):
                raise
            raise GetLaunchError(*exc.args) from exc
        if result.returncode != 0:
            raise OSError("bounded GET worker failed")
        try:
            record = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise OSError("bounded GET worker returned invalid metadata") from exc
        if "error" in record:
            raise _error_from_record(record["error"])
        body: bytes | None = None
        if record.get("has_body") and body_path is not None:
            read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            fd = os.open(body_path, read_flags)
            with os.fdopen(fd, "rb") as fh:
                body = fh.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResponseTooLarge(f"Response body exceeded limit of {max_bytes} bytes")
        elif record.get("body") is not None:
            body = base64.b64decode(record["body"])
        error = None
        if record.get("http_error") is not None:
            headers = Message()
            for key, value in record.get("headers") or []:
                headers[key] = value
            error = urllib.error.HTTPError(
                record["url"], record["status"], record["http_error"],
                headers, io.BytesIO(body or b""),
            )
        if with_headers:
            return record["status"], body, error, list(record.get("headers") or [])
        return record["status"], body, error
    finally:
        if body_dir is not None:
            shutil.rmtree(body_dir, ignore_errors=True)


def _main():
    from lib import http

    payload = json.load(sys.stdin)
    req = urllib.request.Request(
        payload["url"], headers=payload["headers"], method="GET",
        origin_req_host=payload["origin_req_host"],
    )
    for key, value in payload["unredirected_headers"].items():
        req.add_unredirected_header(key, value)
    if payload.get("public_only"):
        setattr(req, http._PUBLIC_ONLY_ATTR, True)
    redirect_policy = payload.get("redirect_policy")
    max_bytes = int(payload.get("max_bytes") or MAX_RESPONSE_BYTES)
    if max_bytes <= 0:
        max_bytes = MAX_RESPONSE_BYTES
    body_path = payload.get("body_path")
    want_headers = bool(payload.get("with_headers", True))
    error_reason = None
    try:
        if isinstance(redirect_policy, dict):
            if not _url_matches_policy(req.full_url, redirect_policy):
                raise urllib.error.URLError("refused URL by redirect policy")
            setattr(req, http._REDIRECT_GUARD_ATTR, _build_redirect_guard(redirect_policy))
        try:
            response = http.open_request(
                req, payload["timeout"], bypass_proxy=bool(payload.get("bypass_proxy"))
            )
        except urllib.error.HTTPError as exc:
            response = exc
            error_reason = str(exc.reason)
        with response:
            body = None
            has_body = False
            read_body = payload["read_error_body"] if error_reason is not None else payload["read_body"]
            is_error = error_reason is not None
            limit = min(max_bytes, MAX_ERROR_BODY_BYTES) if is_error else max_bytes
            if read_body:
                try:
                    if body_path:
                        _stream_body_to_file(response, body_path, limit, truncate=is_error)
                        has_body = True
                    else:
                        body = _read_body_inline(response, limit, truncate=is_error)
                except (OSError, http_client.HTTPException):
                    if not is_error:
                        raise
            result = {
                "status": getattr(response, "status", 200) or 200,
                "body": base64.b64encode(body).decode("ascii") if body is not None else None,
                "has_body": has_body,
                "headers": _serialize_headers(response.headers) if (want_headers or is_error) else [],
                "url": str(response.geturl())[:4096],
                "http_error": error_reason[:512] if error_reason is not None else None,
            }
    except Exception as exc:
        result = {"error": _error_record(exc)}
    encoded = json.dumps(result)
    if len(encoded) > MAX_META_BYTES and "headers" in result:
        result["headers"] = [
            pair for pair in result["headers"]
            if pair[0].lower() in _PRIORITY_RESPONSE_HEADERS
        ]
        encoded = json.dumps(result)
        if len(encoded) > MAX_META_BYTES:
            result["headers"] = []
            encoded = json.dumps(result)
    sys.stdout.write(encoded)


if __name__ == "__main__":
    _main()
