"""Loopback provider endpoints must not send bearer tokens through a proxy."""

from __future__ import annotations

import http.server as stdlib_http
import json
import threading
import urllib.request

import pytest

from lib import http, providers


@pytest.fixture
def serve():
    running = []

    def start(*, reply="direct", redirect_to=None):
        requests = []

        class Handler(stdlib_http.BaseHTTPRequestHandler):
            def do_POST(self):
                self._respond()

            def do_GET(self):
                self._respond()

            def _respond(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                requests.append(
                    (self.command, self.path, {key.lower(): value for key, value in self.headers.items()})
                )
                if redirect_to:
                    self.send_response(302)
                    self.send_header("Location", redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"output_text": reply}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = stdlib_http.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        return f"http://127.0.0.1:{server.server_port}", requests

    yield start

    for server, thread in reversed(running):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _enable_proxy(monkeypatch, proxy_url):
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(
        http, "_opener", urllib.request.build_opener(http._StripAuthOnCrossOriginRedirect)
    )


@pytest.mark.parametrize(
    "key,client,route,host",
    [
        ("OPENAI_BASE_URL", providers.OpenAIClient, "/v1/responses", "127.0.0.1"),
        ("OPENAI_BASE_URL", providers.OpenAIClient, "/v1/responses", "localhost"),
        ("XAI_BASE_URL", providers.XAIClient, "/v1/responses", "127.0.0.1"),
        ("OPENROUTER_BASE_URL", providers.OpenRouterClient, "/v1/chat/completions", "127.0.0.1"),
    ],
)
def test_loopback_provider_request_bypasses_proxy(key, client, route, host, monkeypatch, serve):
    endpoint, endpoint_requests = serve()
    proxy, proxy_requests = serve(reply="proxy")
    _enable_proxy(monkeypatch, proxy)
    monkeypatch.setenv(key, endpoint.replace("127.0.0.1", host, 1) + "/v1")

    assert client("dummy-key").generate_text("model", "prompt") == "direct"
    assert proxy_requests == []
    assert len(endpoint_requests) == 1
    method, path, headers = endpoint_requests[0]
    assert (method, path) == ("POST", route)
    assert headers["authorization"] == "Bearer dummy-key"


def test_loopback_redirect_strips_bearer_and_stays_off_proxy(monkeypatch, serve):
    target, target_requests = serve()
    source, source_requests = serve(redirect_to=target + "/result")
    proxy, proxy_requests = serve(reply="proxy")
    _enable_proxy(monkeypatch, proxy)
    monkeypatch.setenv("OPENAI_BASE_URL", source + "/v1")

    assert providers.OpenAIClient("dummy-key").generate_text("model", "prompt") == "direct"
    assert proxy_requests == []
    assert len(source_requests) == 1
    assert source_requests[0][2]["authorization"] == "Bearer dummy-key"
    assert len(target_requests) == 1
    assert target_requests[0][1] == "/result"
    assert "authorization" not in target_requests[0][2]


def test_loopback_provider_request_does_not_change_other_proxy_routes(monkeypatch, serve):
    endpoint, endpoint_requests = serve()
    proxy, proxy_requests = serve(reply="proxy")
    _enable_proxy(monkeypatch, proxy)
    monkeypatch.setenv("OPENAI_BASE_URL", endpoint + "/v1")

    assert providers.OpenAIClient("dummy-key").generate_text("model", "prompt") == "direct"
    assert proxy_requests == []
    assert http.get(endpoint + "/status", retries=1) == {"output_text": "proxy"}
    assert len(endpoint_requests) == 1
    assert len(proxy_requests) == 1
    assert "authorization" not in proxy_requests[0][2]
