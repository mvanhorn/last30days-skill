"""Cross-origin redirects must drop credential headers (#1062)."""

import http.server
import json
import socketserver
import threading
from urllib.request import Request

from lib.http import _StripAuthOnCrossOriginRedirect, get


class _CaptureHandler(http.server.BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):
        type(self).captured = {k.lower(): v for k, v in self.headers.items()}
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def _serve(handler):
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _redirected_headers(src: str, dest: str, extra_headers: dict) -> dict:
    handler = _StripAuthOnCrossOriginRedirect()
    req = Request(src, headers=extra_headers)
    new = handler.redirect_request(req, None, 302, "Found", {}, dest)
    merged = {}
    for store in (new.headers, getattr(new, "unredirected_hdrs", {})):
        for name, value in store.items():
            merged[name.lower()] = value
    return merged


def test_cross_origin_redirect_strips_authorization():
    class Victim(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            loc = f"http://127.0.0.1:{attacker.server_address[1]}/steal"
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    attacker, _ap = _serve(_CaptureHandler)
    victim = socketserver.TCPServer(("127.0.0.1", 0), Victim)
    threading.Thread(target=victim.serve_forever, daemon=True).start()
    vp = victim.server_address[1]

    _CaptureHandler.captured = {}
    result = get(
        f"http://localhost:{vp}/v1/search",
        headers={
            "Authorization": "Bearer SENTINEL",
            "X-Api-Key": "SENTINEL-KEY",
            "X-Subscription-Token": "BRAVE-KEY",
        },
        timeout=5,
    )
    assert result == {"ok": True}
    seen = {k.lower(): v for k, v in _CaptureHandler.captured.items()}
    assert "authorization" not in seen
    assert "x-api-key" not in seen
    assert "x-subscription-token" not in seen

    attacker.shutdown()
    victim.shutdown()


def test_same_origin_redirect_keeps_authorization():
    class SameOrigin(http.server.BaseHTTPRequestHandler):
        captured = {}

        def do_GET(self):
            if self.path == "/start":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/ok")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            type(self).captured = {k.lower(): v for k, v in self.headers.items()}
            body = json.dumps({"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd, port = _serve(SameOrigin)
    SameOrigin.captured = {}
    result = get(
        f"http://127.0.0.1:{port}/start",
        headers={"Authorization": "Bearer KEEP"},
        timeout=5,
    )
    assert result == {"ok": True}
    assert SameOrigin.captured.get("authorization") == "Bearer KEEP"
    httpd.shutdown()


def test_https_to_http_same_host_strips_credentials():
    headers = _redirected_headers(
        "https://api.example.com/v1",
        "http://api.example.com/v1",
        {
            "Authorization": "Bearer SECRET",
            "X-Api-Key": "KEY",
            "X-Subscription-Token": "BRAVE",
        },
    )
    assert "authorization" not in headers
    assert "x-api-key" not in headers
    assert "x-subscription-token" not in headers


def test_http_to_https_same_host_strips_credentials():
    headers = _redirected_headers(
        "http://api.example.com/v1",
        "https://api.example.com/v1",
        {"Authorization": "Bearer SECRET"},
    )
    assert "authorization" not in headers


def test_implicit_https_port_keeps_credentials():
    headers = _redirected_headers(
        "https://api.example.com/v1",
        "https://api.example.com:443/v1",
        {"Authorization": "Bearer KEEP"},
    )
    assert headers.get("authorization") == "Bearer KEEP"


def test_non_default_https_port_strips_credentials():
    headers = _redirected_headers(
        "https://api.example.com/v1",
        "https://api.example.com:8443/v1",
        {"Authorization": "Bearer SECRET"},
    )
    assert "authorization" not in headers
