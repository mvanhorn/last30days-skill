"""Owned GET transport for operation deadlines, including DNS and body reads."""

from __future__ import annotations

import base64
from email.message import Message
import io
import json
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import subproc

# Reserve TERM and KILL/reap time inside the caller's operation budget.
CLEANUP_GRACE_SECONDS = 0.1


class GetTimeout(TimeoutError):
    def __init__(self, message, *, started):
        super().__init__(message)
        self.started = started


def _error_record(exc):
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return {"kind": "URLError", "reason": _error_record(reason) if isinstance(reason, BaseException) else str(reason)}
    return {"kind": type(exc).__name__, "message": str(exc), "errno": getattr(exc, "errno", None)}


def _error_from_record(record):
    if record["kind"] == "URLError":
        reason = record["reason"]
        return urllib.error.URLError(_error_from_record(reason) if isinstance(reason, dict) else reason)
    kinds = {
        "gaierror": socket.gaierror,
        "TimeoutError": TimeoutError,
        "ConnectionResetError": ConnectionResetError,
        "ConnectionRefusedError": ConnectionRefusedError,
        "OSError": OSError,
        "SSLError": ssl.SSLError,
        "SSLCertVerificationError": ssl.SSLCertVerificationError,
    }
    kind = kinds.get(record["kind"], OSError)
    if record.get("errno") is not None:
        return kind(record["errno"], record["message"])
    return kind(record["message"])


def get(
    req: urllib.request.Request,
    *,
    timeout: float,
    deadline_monotonic: float,
    cancel: threading.Event | None = None,
    read_body: bool = True,
    read_error_body: bool = True,
):
    if req.get_method() != "GET":
        raise ValueError("bounded transport supports GET only")
    payload = {
        "url": req.full_url,
        "headers": req.headers,
        "unredirected_headers": req.unredirected_hdrs,
        "origin_req_host": req.origin_req_host,
        "timeout": timeout,
        "read_body": read_body,
        "read_error_body": read_error_body,
    }
    transport_deadline = deadline_monotonic - 2 * CLEANUP_GRACE_SECONDS
    try:
        result = subproc.run_with_timeout(
            [sys.executable, "-I", str(Path(__file__).resolve())],
            timeout=max(0, transport_deadline - time.monotonic()),
            deadline_monotonic=transport_deadline,
            input_text=json.dumps(payload),
            cancel=cancel,
            cleanup_grace=CLEANUP_GRACE_SECONDS,
        )
    except subproc.SubprocTimeout as exc:
        raise GetTimeout("GET exceeded operation deadline or was cancelled", started=exc.started) from exc
    if result.returncode != 0:
        raise OSError("bounded GET worker failed")
    record = json.loads(result.stdout)
    if "error" in record:
        raise _error_from_record(record["error"])
    body = base64.b64decode(record["body"]) if record["body"] is not None else None
    error = None
    if record.get("http_error") is not None:
        headers = Message()
        for key, value in record["headers"]:
            headers[key] = value
        error = urllib.error.HTTPError(
            record["url"], record["status"], record["http_error"],
            headers, io.BytesIO(body or b""),
        )
    return record["status"], body, error


def _main():
    from lib import http

    payload = json.load(sys.stdin)
    req = urllib.request.Request(
        payload["url"], headers=payload["headers"], method="GET",
        origin_req_host=payload["origin_req_host"],
    )
    for key, value in payload["unredirected_headers"].items():
        req.add_unredirected_header(key, value)
    error_reason = None
    try:
        try:
            response = http.open_request(req, payload["timeout"])
        except urllib.error.HTTPError as exc:
            response = exc
            error_reason = str(exc.reason)
        with response:
            body = None
            read_body = payload["read_error_body"] if error_reason is not None else payload["read_body"]
            if read_body:
                try:
                    body = response.read()
                except OSError:
                    if error_reason is None:
                        raise
            result = {
                "status": getattr(response, "status", 200) or 200,
                "body": base64.b64encode(body).decode("ascii") if body is not None else None,
                "headers": list(response.headers.items()),
                "url": response.geturl(),
                "http_error": error_reason,
            }
    except Exception as exc:
        result = {"error": _error_record(exc)}
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    _main()
