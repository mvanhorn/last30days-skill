"""Regression tests for PR06: HTTP response bounds, entity/keyless SSRF, and robots ReDoS."""

from __future__ import annotations

import http.client as http_client
import io
import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.request import Request

import pytest

from lib import bounded_get, company_resolve, http, public_host, resolve, subproc, web_fetch_keyless
from lib.job_boards import robots


# ---------------------------------------------------------------------------
# HIGH-061: Bounded reads in http.py and bounded_get.py + 3xx no-drain
# ---------------------------------------------------------------------------


def _start_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(2)


def test_http_request_rejects_oversized_body_by_content_length_without_retrying():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            hits.append(self.path)
            payload = b"A" * 4096
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server, thread, base = _start_server(Handler)
    try:
        with pytest.raises(http.ResponseTooLarge) as exc_info:
            http.request("GET", f"{base}/big", raw=True, retries=3, max_bytes=1024)
        assert isinstance(exc_info.value, http.HTTPError)
        assert len(hits) == 1
        assert http.get_text(f"{base}/big", retries=2, max_bytes=1024) is None
    finally:
        _stop_server(server, thread)


def test_http_request_rejects_oversized_chunked_stream_without_reading_all():
    bytes_sent = [0]

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk = b"x" * 1024
            try:
                for _ in range(512):
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
                    bytes_sent[0] += len(chunk)
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

    server, thread, base = _start_server(Handler)
    try:
        with pytest.raises(http.ResponseTooLarge):
            http.request("GET", f"{base}/stream", raw=True, retries=3, max_bytes=2048)
    finally:
        _stop_server(server, thread)


def test_http_request_passes_bounded_read_size_and_never_falls_back_unbounded():
    observed_sizes: list[int] = []

    class SizedResp:
        status = 200
        headers = {}

        def __init__(self, payload: bytes):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size=-1):
            observed_sizes.append(size)
            return self._payload[:size] if size >= 0 else self._payload

    with patch("lib.http.urllib.request.urlopen", return_value=SizedResp(b"hello")):
        assert http.request("GET", "https://example.com/ok", raw=True, retries=1, max_bytes=10) == "hello"
    assert observed_sizes == [11]

    with patch("lib.http.urllib.request.urlopen", return_value=SizedResp(b"0123456789X")):
        with pytest.raises(http.ResponseTooLarge):
            http.request("GET", "https://example.com/big", raw=True, retries=3, max_bytes=10)

    class ZeroArgResp:
        status = 200
        headers = {}
        unbounded_called = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            self.unbounded_called = True
            return b"should-not-run"

    zero_arg = ZeroArgResp()
    with patch("lib.http.urllib.request.urlopen", return_value=zero_arg):
        with pytest.raises(TypeError):
            http.request("GET", "https://example.com/zero", raw=True, retries=1, max_bytes=10)
    assert zero_arg.unbounded_called is False


def test_http_head_request_allows_large_content_length_header():
    class HeadHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", "999999999")
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

    server, thread, base = _start_server(HeadHandler)
    try:
        body, headers = http.request(
            "HEAD", f"{base}/large.bin", raw=True, retries=1, max_bytes=1024, return_headers=True,
        )
        assert body == ""
        assert headers.get("content-length") == "999999999"
    finally:
        _stop_server(server, thread)


def test_http_request_truncates_oversized_error_body_and_preserves_status():
    huge_error = b"E" * (http.MAX_ERROR_BODY_BYTES + 20_000)
    err = urllib.error.HTTPError(
        "https://example.com/fail", 500, "Server Error", {}, io.BytesIO(huge_error),
    )
    with patch("lib.http.urllib.request.urlopen", side_effect=err):
        with pytest.raises(http.HTTPError) as exc_info:
            http.request("GET", "https://example.com/fail", retries=1)
    assert exc_info.value.status_code == 500
    assert exc_info.value.body is not None
    assert len(exc_info.value.body.encode("utf-8")) == http.MAX_ERROR_BODY_BYTES


def test_http_redirect_handler_closes_without_draining_3xx_body():
    class TrapFP:
        def __init__(self):
            self.closed = False

        def read(self, *_args, **_kwargs):
            raise AssertionError("redirect handler must not call fp.read()")

        def close(self):
            self.closed = True

    handler = http._StripAuthOnCrossOriginRedirect()
    parent = MagicMock()
    parent.open.return_value = "redirected-response"
    handler.parent = parent

    for code in (301, 302, 303, 307, 308):
        fp = TrapFP()
        req = Request("https://example.com/start")
        req.timeout = 5
        method = getattr(handler, f"http_error_{code}")
        out = method(req, fp, code, "Redirect", {"location": "https://example.com/dest"})
        assert out == "redirected-response"
        assert fp.closed is True


def test_http_redirect_live_server_does_not_hang_on_stalled_302_body():
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "/final")
                self.send_header("Content-Length", "104857600")
                self.end_headers()
                release.wait(5)
                return
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server, thread, base = _start_server(Handler)
    try:
        started = time.monotonic()
        assert http.get(f"{base}/redir", timeout=3, retries=1) == {"ok": True}
        assert time.monotonic() - started < 1.5
    finally:
        release.set()
        _stop_server(server, thread)


def test_http_and_bounded_get_reject_non_positive_max_bytes():
    with pytest.raises(ValueError):
        http.request("GET", "https://example.com/", max_bytes=0)
    with pytest.raises(ValueError):
        http.request("GET", "https://example.com/", max_bytes=-5)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        with pytest.raises(ValueError):
            bounded_get.get(
                Request(f"http://127.0.0.1:{port}/"),
                timeout=1,
                deadline_monotonic=time.monotonic() + 2,
                max_bytes=0,
            )


def test_http_and_bounded_get_allow_exact_max_bytes_boundary():
    exact = b"X" * 1024

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(exact)))
            self.end_headers()
            self.wfile.write(exact)

    server, thread, base = _start_server(Handler)
    try:
        assert http.request("GET", f"{base}/exact", raw=True, retries=1, max_bytes=1024) == exact.decode()
        status, body, err = bounded_get.get(
            Request(f"{base}/exact"),
            timeout=3,
            deadline_monotonic=time.monotonic() + 5,
            max_bytes=1024,
        )
        assert (status, body, err) == (200, exact, None)
    finally:
        _stop_server(server, thread)


def test_bounded_get_streams_body_via_temp_file_caps_size_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/ok":
                body = b'{"payload":"' + (b"x" * 2048) + b'"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/too-big-cl":
                body = b"B" * 8192
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if self.path == "/too-big-chunked":
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunk = b"C" * 1024
                try:
                    for _ in range(16):
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                return
            if self.path == "/huge-error":
                # Split a 3-byte UTF-8 character across the 64 KiB truncation boundary.
                body = (b"Z" * (bounded_get.MAX_ERROR_BODY_BYTES - 1)) + "€".encode("utf-8") + (b"Z" * 1024)
                self.send_response(403)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if self.path == "/truncated-error":
                self.send_response(500)
                self.send_header("Content-Length", "500")
                self.end_headers()
                self.wfile.write(b"partial-err")
                self.close_connection = True
                return

    server, thread, base = _start_server(Handler)
    capture_limits = []
    stdout_sizes = []
    real_run = subproc.run_with_timeout

    def spy_run(*args, **kwargs):
        capture_limits.append(kwargs.get("capture_limit_bytes"))
        res = real_run(*args, **kwargs)
        stdout_sizes.append(len(res.stdout))
        return res

    try:
        with patch.object(bounded_get.subproc, "run_with_timeout", side_effect=spy_run):
            status, body, err = bounded_get.get(
                Request(f"{base}/ok"),
                timeout=3,
                deadline_monotonic=time.monotonic() + 5,
                max_bytes=4096,
            )
            assert status == 200
            assert err is None
            assert body is not None and len(body) > 2048
            assert capture_limits[-1] == bounded_get.MAX_META_BYTES
            # Metadata JSON on stdout must not embed the 2 KiB+ body.
            assert stdout_sizes[-1] < 512

            with pytest.raises(bounded_get.ResponseTooLarge):
                bounded_get.get(
                    Request(f"{base}/too-big-cl"),
                    timeout=3,
                    deadline_monotonic=time.monotonic() + 5,
                    max_bytes=1024,
                )

            with pytest.raises(bounded_get.ResponseTooLarge):
                bounded_get.get(
                    Request(f"{base}/too-big-chunked"),
                    timeout=3,
                    deadline_monotonic=time.monotonic() + 5,
                    max_bytes=1024,
                )

            # Owned GET through http.request fails fast with ResponseTooLarge without retrying.
            with pytest.raises(http.ResponseTooLarge):
                http.get(
                    f"{base}/too-big-chunked",
                    retries=3,
                    deadline_monotonic=time.monotonic() + 5,
                    owned_get=True,
                    max_bytes=1024,
                )

            # Oversized error bodies are truncated to MAX_ERROR_BODY_BYTES, preserving status
            # and decoding cleanly with replacement even if truncated mid-UTF-8 codepoint.
            status, err_body, http_err = bounded_get.get(
                Request(f"{base}/huge-error"),
                timeout=3,
                deadline_monotonic=time.monotonic() + 5,
            )
            assert status == 403
            assert isinstance(http_err, urllib.error.HTTPError)
            assert err_body is not None
            assert len(err_body) == bounded_get.MAX_ERROR_BODY_BYTES

            with pytest.raises(http.HTTPError) as owned_err:
                http.get(
                    f"{base}/huge-error",
                    retries=1,
                    deadline_monotonic=time.monotonic() + 5,
                    owned_get=True,
                )
            assert owned_err.value.status_code == 403
            assert owned_err.value.body is not None
            assert owned_err.value.body.startswith("ZZZ")

            # Truncated 500 error body preserves HTTP 500 rather than failing as OSError.
            status, trunc_body, trunc_err = bounded_get.get(
                Request(f"{base}/truncated-error"),
                timeout=3,
                deadline_monotonic=time.monotonic() + 5,
            )
            assert status == 500
            assert isinstance(trunc_err, urllib.error.HTTPError)
            assert trunc_body == b"partial-err"

        assert list(tmp_path.iterdir()) == []
    finally:
        _stop_server(server, thread)


@pytest.mark.parametrize(
    "declared_len,sent_len",
    [
        (100, 0),
        (bounded_get._STREAM_CHUNK_BYTES + 4096, bounded_get._STREAM_CHUNK_BYTES),
    ],
)
def test_premature_eof_raises_incomplete_read_for_http_and_bounded_get(
    declared_len: int, sent_len: int, tmp_path, monkeypatch,
):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    class TruncatingHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(declared_len))
            self.end_headers()
            if sent_len:
                self.wfile.write(b"A" * sent_len)
                self.wfile.flush()
            self.close_connection = True

    server, thread, base = _start_server(TruncatingHandler)
    try:
        with pytest.raises(http.HTTPError) as exc_info:
            http.request("GET", f"{base}/eof", raw=True, retries=1)
        assert "IncompleteRead" in str(exc_info.value)

        with pytest.raises(http_client.IncompleteRead):
            bounded_get.get(
                Request(f"{base}/eof"),
                timeout=3,
                deadline_monotonic=time.monotonic() + 5,
            )
        assert list(tmp_path.iterdir()) == []
    finally:
        _stop_server(server, thread)


def test_bounded_get_serialize_headers_caps_control_char_expansion():
    hostile = {"X-Evil": "\x00" * 40_000, "X-Other": "\x01" * 40_000}
    serialized = bounded_get._serialize_headers(hostile)
    encoded = json.dumps({"headers": serialized})
    assert encoded.isascii()
    assert len(encoded) < bounded_get.MAX_META_BYTES


def test_bounded_get_serialize_headers_preserves_priority_headers_after_junk():
    raw_headers = {f"X-Junk-{i}": "J" * 1024 for i in range(300)}
    raw_headers["Retry-After"] = "42"
    raw_headers["X-L30D-Charge-Credits"] = "1.5"
    raw_headers["X-RateLimit-Reset"] = "30"
    serialized = dict(bounded_get._serialize_headers(raw_headers))
    assert serialized.get("Retry-After") == "42"
    assert serialized.get("X-L30D-Charge-Credits") == "1.5"
    assert serialized.get("X-RateLimit-Reset") == "30"
    assert len(serialized) <= bounded_get._MAX_HEADER_COUNT
    assert len(json.dumps(list(serialized.items()))) <= bounded_get._MAX_HEADER_BYTES + 16


def test_bounded_get_main_writes_pure_ascii_json_safe_for_windows_cp1252(monkeypatch):
    class NonCp1252Response:
        status = 200
        headers = {"X-Raw": "ok\x81\x8d\x8f\x90\x9d", "X-Uni": "snow-\u2603"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://example.com/\u2603"

        def read(self, *_args):
            return b"ok"

    payload = {
        "url": "https://example.com/\u2603",
        "headers": {},
        "unredirected_headers": {},
        "origin_req_host": "example.com",
        "timeout": 2,
        "read_body": False,
        "read_error_body": False,
        "with_headers": True,
        "max_bytes": 1024,
        "body_path": None,
    }
    raw_out = io.BytesIO()
    cp1252_stdout = io.TextIOWrapper(raw_out, encoding="cp1252")
    monkeypatch.setattr(bounded_get.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(bounded_get.sys, "stdout", cp1252_stdout)
    with patch.object(http, "open_request", return_value=NonCp1252Response()):
        bounded_get._main()
    cp1252_stdout.flush()
    written = raw_out.getvalue()
    assert written.isascii()
    assert len(written) <= bounded_get.MAX_META_BYTES
    parsed = json.loads(written.decode("ascii"))
    assert parsed["status"] == 200
    assert dict(parsed["headers"])["X-Raw"] == "ok\x81\x8d\x8f\x90\x9d"
    assert dict(parsed["headers"])["X-Uni"] == "snow-\u2603"


# ---------------------------------------------------------------------------
# HIGH-086: resolve.py official-site SSRF, ccSLD, redirects & decode safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_site",
    [
        "127.0.0.1",
        "127.0.0.1:8080",
        "http://127.0.0.1:8080/",
        "169.254.169.254",
        "http://169.254.169.254/latest/meta-data/",
        "10.0.0.1",
        "8.8.8.8",
        "http://[::1]/",
        "http://0177.0.0.1/",
        "http://2130706433/",
        "localhost",
        "stripe.localhost",
        "https://stripe.localhost/",
        "printer.local",
        "metadata.google.internal",
        "service.localdomain",
        "1.0.0.127.in-addr.arpa",
        "foo.com:99999",
        "foo.com:8080",
        "https://foo.com:8443/",
        "https://foo.com:80/",
        "https://user:pass@stripe.com/",
        "https://stripe.com@evil.com/",
        "ftp://stripe.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,stripe.com",
        "https://stripe.com/\r\nX-Injected: 1",
        "//stripe.com",
    ],
)
def test_resolve_official_site_url_rejects_unsafe_inputs(bad_site: str):
    assert resolve._official_site_url(bad_site) == ""


@pytest.mark.parametrize(
    "good_site,expected",
    [
        ("typesafe.ai", "https://typesafe.ai/"),
        ("https://www.TypeSafe.ai/pricing?x=1", "https://www.typesafe.ai/"),
        ("https://example.com:443/about", "https://example.com/"),
        ("http://example.com:80/about", "https://example.com/"),
        ("stripe.co.uk", "https://stripe.co.uk/"),
    ],
)
def test_resolve_official_site_url_accepts_valid_domains(good_site: str, expected: str):
    assert resolve._official_site_url(good_site) == expected


def test_resolve_extract_official_domain_rejects_internal_spoofed_and_non_cctld():
    bad_items = [
        {"url": "https://stripe.localhost/", "title": "", "snippet": ""},
        {"url": "https://stripe.local/", "title": "", "snippet": ""},
        {"url": "https://stripe.internal/", "title": "", "snippet": ""},
        {"url": "https://stripe.co.evil/", "title": "", "snippet": ""},
        {"url": "https://stripe.co.com/", "title": "", "snippet": ""},
        {"url": "https://stripe.com.evil/", "title": "", "snippet": ""},
        {"url": "https://stripe.com@attacker.com/", "title": "", "snippet": ""},
        {"url": "https://stripe.com:8080/", "title": "", "snippet": ""},
        {"url": "http://127.0.0.1/", "title": "", "snippet": ""},
    ]
    assert resolve._extract_official_domain("Stripe", bad_items) == ""

    good_uk = [{"url": "https://www.stripe.co.uk/pricing", "title": "", "snippet": ""}]
    assert resolve._extract_official_domain("Stripe", good_uk) == "stripe.co.uk"


def test_resolve_official_site_socials_uses_public_only_redirect_guard_and_handles_decode():
    with patch.object(resolve.http, "get_text", return_value="<html></html>") as mock_get:
        resolve.official_site_socials("typesafe.ai")
    assert mock_get.call_args.kwargs.get("public_only") is True
    guard = mock_get.call_args.kwargs.get("redirect_guard")
    assert callable(guard)
    assert guard("https://www.typesafe.ai/home") is True
    assert guard("http://169.254.169.254/latest/meta-data/") is False
    assert guard("http://127.0.0.1:8080/") is False
    assert guard("http://metadata.google.internal/") is False
    assert guard("https://user:pass@typesafe.ai/") is False
    assert guard("file:///etc/passwd") is False

    # Non-UTF-8 homepage bytes or ValueError must return empty socials, not crash.
    bad_resp = MagicMock()
    bad_resp.__enter__.return_value = bad_resp
    bad_resp.__exit__.return_value = False
    bad_resp.status = 200
    bad_resp.headers = {}
    bad_resp.read.return_value = b"\xff\xfe\x80\x81"
    with patch.object(resolve.http, "_open_request", return_value=bad_resp):
        assert resolve.official_site_socials("typesafe.ai") == {"x_handle": "", "github_user": ""}

    with patch.object(resolve.http, "get_text", side_effect=ValueError("invalid port")):
        assert resolve.official_site_socials("typesafe.ai") == {"x_handle": "", "github_user": ""}


def test_resolve_official_site_socials_blocks_private_dns_and_private_redirect(monkeypatch):
    def fake_getaddrinfo(name, port, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", port or 443))]

    monkeypatch.setattr(public_host.socket, "getaddrinfo", fake_getaddrinfo)
    assert resolve.official_site_socials("stripe.xyz") == {"x_handle": "", "github_user": ""}

    # Verify redirect handler refuses private/metadata targets when public_only + guard are attached.
    handler = http._StripAuthOnCrossOriginRedirect()
    req = Request("https://stripe.xyz/")
    setattr(req, http._PUBLIC_ONLY_ATTR, True)
    setattr(req, http._REDIRECT_GUARD_ATTR, resolve._official_site_redirect_guard)
    for bad_target in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "http://metadata.google.internal/computeMetadata/v1/",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(req, None, 302, "Found", {}, bad_target)
        assert exc_info.value.code == 403


# ---------------------------------------------------------------------------
# HIGH-087: company_resolve.py _site_host, _registrable_label, _official_site
# ---------------------------------------------------------------------------


def test_company_resolve_site_host_blocks_query_fragment_userinfo_ssrf_spoofing():
    assert company_resolve._site_host("https://attacker.com?x=.stripe.com") == "attacker.com"
    assert company_resolve._site_host("https://attacker.com#.stripe.com") == "attacker.com"
    assert company_resolve._site_host("https://linkedin.com?ref=.stripe.com") == "linkedin.com"
    assert company_resolve._site_host("https://stripe.com?utm=1") == "stripe.com"
    assert company_resolve._exa_homepage("https://stripe.com?utm=1") == "https://stripe.com/"

    for bad_url in (
        "https://127.0.0.1?x=.stripe.com",
        "http://169.254.169.254/latest/meta-data/",
        "https://[::1]/",
        "https://0x7f.0.0.1/",
        "https://stripe.com@attacker.com/",
        "https://user:pass@stripe.com/",
        "https://stripe.localhost/",
        "https://stripe.local/",
        "https://stripe.internal/",
        "https://stripe.com:8080/",
        "https://www.com/",
        "ftp://stripe.com/",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://stripe.com/\r\nBad: 1",
    ):
        assert company_resolve._site_host(bad_url) == "", f"expected rejection for {bad_url}"

    for spoofed in (
        "https://attacker.com?x=.stripe.com",
        "https://127.0.0.1?x=.stripe.com",
        "https://linkedin.com?ref=.stripe.com",
        "https://attacker.com#.stripe.com",
        "https://stripe.com@attacker.com/",
        "https://stripe.co.com/",
        "https://stripe.com.evil/",
    ):
        assert company_resolve._exa_site_matches_topic("Stripe", spoofed) is False

    assert company_resolve._exa_site_matches_topic("Stripe", "https://stripe.co.uk/") is True
    assert company_resolve._exa_site_matches_topic("Stripe", "https://stripe.com?utm=1") is True


def test_company_resolve_official_site_validates_single_wikidata_p856():
    def stmt(val):
        return {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": val}}}

    for bad_p856 in (
        "javascript:alert(1)",
        "ftp://stripe.com/",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "https://user:pass@stripe.com/",
        "https://stripe.localhost/",
        "https://stripe.local/",
        "https://metadata.google.internal/",
        "https://stripe.com:8443/",
        "https://stripe.com/\r\nX: 1",
        "https://www.com/",
    ):
        assert company_resolve._official_site([stmt(bad_p856)]) == "", f"expected rejection for {bad_p856}"

    assert company_resolve._official_site([stmt("https://stripe.com/en")]) == "https://stripe.com/en"
    assert company_resolve._official_site([stmt("https://www.com/a"), stmt("https://www.com/b")]) == ""
    # Poisoned private entries must not tip a minority valid host into a majority.
    assert (
        company_resolve._official_site([
            stmt("http://127.0.0.1/"),
            stmt("http://169.254.169.254/"),
            stmt("https://stripe.com/"),
        ])
        == ""
    )


# ---------------------------------------------------------------------------
# HIGH-170: robots.py linear-time wildcard matching & parser bounds
# ---------------------------------------------------------------------------


def test_robots_wildcard_matcher_is_linear_on_adversarial_redos_input():
    evil_pattern = "/" + "a*" * 35 + "z"
    evil_anchored = evil_pattern + "$"
    evil_path = "/" + "a" * 40

    started = time.monotonic()
    assert robots._matches(evil_pattern, evil_path) is False
    assert robots._matches(evil_anchored, evil_path) is False
    rules = robots.Rules(f"User-agent: *\nDisallow: {evil_pattern}\nDisallow: {evil_anchored}\n")
    assert rules.allows(evil_path) is True
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, f"robots wildcard matching took {elapsed:.4f}s (possible ReDoS)"


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("/", "/anything", True),
        ("/jobs", "/jobs/123", True),
        ("/jobs$", "/jobs", True),
        ("/jobs$", "/jobs/", False),
        ("/*?q=", "/en/jobs?q=engineer", True),
        ("/*?q=", "/en/jobs", False),
        ("/a*a$", "/a", False),
        ("/a*a$", "/aa", True),
        ("/a*a$", "/abca", True),
        ("/a*a$", "/abcb", False),
        ("/a**b*c$", "/axxxbyyyc", True),
        ("/a**b*c$", "/axxxbyyy", False),
        ("/*$", "/", True),
        ("/*$", "/foo/bar", True),
        ("/a*b*", "/a123b456", True),
        ("/a*b*", "/a123", False),
        ("$", "", True),
        ("$", "/", False),
    ],
)
def test_robots_matches_semantics(pattern: str, path: str, expected: bool):
    assert robots._matches(pattern, path) is expected


def test_robots_parser_enforces_rule_sitemap_and_line_length_caps():
    long_comment_rule = "Disallow: /private # " + ("c" * (robots.MAX_LINE_LENGTH + 500))
    overlong = "Disallow: /" + ("x" * (robots.MAX_LINE_LENGTH + 50))
    many_rules = "\n".join(f"Disallow: /p{i}" for i in range(robots.MAX_RULES + 250))
    many_sitemaps = "\n".join(
        f"Sitemap: https://example.com/s{i}.xml" for i in range(robots.MAX_SITEMAPS + 50)
    )
    text = f"User-agent: *\n{long_comment_rule}\n{overlong}\n{many_rules}\n{many_sitemaps}\n"
    rules = robots.Rules(text)
    assert rules.allows("/private/data") is False
    assert len(rules.rules) <= robots.MAX_RULES
    assert len(rules.paths) <= robots.MAX_PATHS
    assert len(rules.sitemaps) <= robots.MAX_SITEMAPS
    assert all(len(pat) <= robots.MAX_LINE_LENGTH for _, pat in rules.rules)


def test_robots_parser_discards_incomplete_trailing_line_cut_by_max_chars(monkeypatch):
    # Cut lands right in the middle of "Disallow: /private/secret" -> ".../pri".
    # The incomplete line must be discarded rather than blocking "/pricing".
    prefix = "User-agent: *\nDisallow: /admin\n"
    cut_line = "Disallow: /private/secret\n"
    monkeypatch.setattr(robots, "MAX_ROBOTS_CHARS", len(prefix) + len("Disallow: /pri"))
    rules = robots.Rules(prefix + cut_line)
    assert rules.allows("/admin/panel") is False
    assert rules.allows("/pricing") is True
    assert all(pat != "/pri" for _, pat in rules.rules)

    # A complete line ending right at MAX_ROBOTS_CHARS (next char is '\n') is kept.
    monkeypatch.setattr(robots, "MAX_ROBOTS_CHARS", len(prefix) + len("Disallow: /private/secret"))
    exact_rules = robots.Rules(prefix + cut_line)
    assert exact_rules.allows("/private/secret") is False
    assert exact_rules.allows("/pricing") is True


# ---------------------------------------------------------------------------
# HIGH-201: web_fetch_keyless.py SSRF, userinfo, redirect_guard, decode safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/secret",
        "http://127.0.0.1:8080/admin",
        "http://localhost:3000/",
        "http://sub.localhost/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://2130706433/",
        "http://[64:ff9b::127.0.0.1]/",
        "https://93.184.216.34/",
        "https://169.254.169.254\\x.com/",
        "http://localhost\\.example.com/",
        "http://example.com%2f127.0.0.1/",
        "https://example.com:8080/",
        "https://example.com:80/",
        "http://example.com:443/",
        "https://exämple.com/",
        "http://printer.local/status",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://intranet/secret",
        "https://user:pass@example.com/private",
        "https://example.com/\r\nX-Injected: 1",
        "https://example.com/with space",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "javascript:alert(1)",
    ],
)
def test_web_fetch_keyless_rejects_private_internal_userinfo_and_non_http(bad_url: str):
    with patch.object(web_fetch_keyless.http, "get_text") as mock_get:
        res = web_fetch_keyless.fetch_markdown(bad_url)
    assert res.ok is False
    assert res.reason == "invalid-url"
    mock_get.assert_not_called()


def test_web_fetch_keyless_enforces_public_only_redirect_guard_and_decode_handling():
    with patch.object(web_fetch_keyless.http, "get_text", return_value="# OK") as mock_get:
        res = web_fetch_keyless.fetch_markdown("https://example.com/article")
    assert res.ok is True
    assert mock_get.call_args.kwargs.get("public_only") is True
    guard = mock_get.call_args.kwargs.get("redirect_guard")
    assert callable(guard)
    assert guard("https://r.jina.ai/https://example.com/article") is True
    assert guard("http://r.jina.ai/https://example.com/article") is False
    assert guard("http://127.0.0.1/secret") is False
    assert guard("http://169.254.169.254/latest/meta-data/") is False
    assert guard("https://evil.example/redirect") is False
    assert guard("file:///etc/passwd") is False

    # Redirect handler refuses private/off-host redirect with these guards.
    handler = http._StripAuthOnCrossOriginRedirect()
    req = Request("https://r.jina.ai/https://example.com/article")
    setattr(req, http._PUBLIC_ONLY_ATTR, True)
    setattr(req, http._REDIRECT_GUARD_ATTR, guard)
    for bad_target in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "https://evil.example/x",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(req, None, 302, "Found", {}, bad_target)
        assert exc_info.value.code == 403

    # Non-UTF-8 response from reader returns a clean failed result, never raises.
    with patch.object(
        web_fetch_keyless.http,
        "get_text",
        side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    ):
        res = web_fetch_keyless.fetch_markdown("https://example.com/binary")
    assert res.ok is False
    assert res.markdown == ""
    assert res.reason == "decode-error"
