"""Regression tests for PR 17: keyless web search, entity resolve, and Parallel MCP hardening.

Covers:
- HIGH-091: O(N) HTMLParser chrome-region link parsing & bounded body size in resolve.py
- HIGH-202: Linear-time _BRAVE_AGE_RE, _strip_html, and SERP parsers in web_search_keyless.py
- HIGH-203: LAST30DAYS_SEARXNG_URL scheme/host/metadata validation & redirect_guard on HTML rungs/SearXNG
- HIGH-204: Bounded SERP/SearXNG reads and errors="replace" decoding without UnicodeDecodeError
- HIGH-205: Parallel MCP SSE wall-clock deadline, cancellation, line/event/request caps, and short DELETE cleanup
"""

from __future__ import annotations

import datetime as _dt
import http.client as http_client
import io
import json
import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

import pytest

from lib import grounding, health, http, parallel_mcp, pipeline, resolve, web_fetch_keyless, web_search_keyless


def _start_server(handler_cls):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def _stop_server(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(2)


# ---------------------------------------------------------------------------
# HIGH-091: resolve.py HTMLParser chrome region extraction & body cap
# ---------------------------------------------------------------------------


def test_resolve_removes_quadratic_chrome_region_regex():
    assert not hasattr(resolve, "_CHROME_REGION")


@pytest.mark.parametrize(
    "adversarial_html",
    [
        "<nav>" * 20_000,
        "<header>" * 20_000,
        "<footer>" * 20_000,
        "<nav>" * 15_000 + "<footer><a href='https://x.com/acme'>X</a></footer>",
        "<header " * 20_000,
        "<nav>" * 20_000 + "</nav>",
    ],
)
def test_resolve_official_site_socials_is_linear_on_adversarial_html(adversarial_html: str):
    started = time.monotonic()
    with patch.object(resolve.http, "get_text", return_value=adversarial_html):
        socials = resolve.official_site_socials("acme.com")
    elapsed = time.monotonic() - started
    assert elapsed < 0.25, f"official_site_socials took {elapsed:.4f}s (possible ReDoS)"
    if "x.com/acme" in adversarial_html:
        assert socials["x_handle"] == "acme"
    else:
        assert socials == {"x_handle": "", "github_user": ""}


def test_resolve_html_parser_tracks_nested_chrome_ignores_comments_and_scripts():
    page = """
    <html><body>
      <!-- <a href="https://x.com/commentedout">Ignored</a> -->
      <script>const link = '<a href="https://x.com/inscript">Ignored</a>';</script>
      <nav/>
      <a href="https://x.com/customer1">Customer</a>
      <a href="https://x.com/customer2">Customer 2</a>
      <header><nav><a href="https://x.com/BrandHQ?ref=top&amp;src=1">X</a></nav></header>
      </footer>
      <footer><a href="https://github.com/brand-hq">GitHub</a></footer>
    </body></html>
    """
    with patch.object(resolve.http, "get_text", return_value=page):
        socials = resolve.official_site_socials("brand.example")
    assert socials == {"x_handle": "BrandHQ", "github_user": "brand-hq"}


def test_resolve_official_site_socials_bounds_response_size_and_decodes_invalid_utf8():
    with patch.object(resolve.http, "get_text", return_value="<html></html>") as mock_get:
        resolve.official_site_socials("typesafe.ai")
    assert mock_get.call_args.kwargs.get("max_bytes") == resolve._MAX_OFFICIAL_SITE_HTML_BYTES
    assert mock_get.call_args.kwargs.get("decode_errors") == "replace"

    # A homepage with non-UTF-8 bytes (e.g. Latin-1 accent) still decodes with replacement
    # instead of raising UnicodeDecodeError, and extracts the brand's footer handle.
    latin1_page = (
        b"<html><head><title>Caf\xe9 Corp</title></head>"
        b"<body><footer><a href='https://x.com/cafecorp'>X</a></footer></body></html>"
    )
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.status = 200
    resp.headers = {}
    resp.read.return_value = latin1_page
    with patch.object(resolve.http, "_open_request", return_value=resp):
        assert resolve.official_site_socials("cafecorp.example") == {
            "x_handle": "cafecorp",
            "github_user": "",
        }

    # Oversized HTML returned by a mocked get_text is sliced to _MAX_OFFICIAL_SITE_HTML_BYTES.
    huge_html = (" " * (resolve._MAX_OFFICIAL_SITE_HTML_BYTES + 1024)) + (
        '<footer><a href="https://x.com/pastlimit">X</a></footer>'
    )
    with patch.object(resolve.http, "get_text", return_value=huge_html):
        assert resolve.official_site_socials("example.com") == {"x_handle": "", "github_user": ""}


# ---------------------------------------------------------------------------
# HIGH-202: web_search_keyless.py ReDoS elimination
# ---------------------------------------------------------------------------


def test_brave_age_re_is_linear_on_adversarial_unclosed_spans():
    adversarial_cases = [
        '<span class="t-secondary">' + (" " * 100_000),
        '<span class="t-secondary">' + ("- " * 50_000),
        '<span class="t-secondary">' * 20_000,
        ('<span class="t-secondary">' + (" " * 50)) * 2_000,
    ]
    started = time.monotonic()
    for payload in adversarial_cases:
        assert web_search_keyless._BRAVE_AGE_RE.search(payload) is None
    elapsed = time.monotonic() - started
    assert elapsed < 0.1, f"_BRAVE_AGE_RE took {elapsed:.4f}s (possible ReDoS)"

    # Valid Brave age spans still match and parse identically.
    now = _dt.datetime(2026, 11, 15, tzinfo=_dt.timezone.utc)
    for raw, expected in (
        ('<span class="t-secondary"> 2 weeks ago - </span>', "2026-11-01"),
        ('<span class="t-secondary">Nov 3, 2026 -</span>', "2026-11-03"),
        ('<span class="t-secondary">3 days ago</span>', "2026-11-12"),
    ):
        match = web_search_keyless._BRAVE_AGE_RE.search(raw)
        assert match is not None
        assert web_search_keyless._brave_date(match.group("age"), now) == expected


def test_strip_html_and_serp_parsers_are_linear_on_adversarial_inputs():
    assert not hasattr(web_search_keyless, "_STYLE_SCRIPT_RE")

    started = time.monotonic()
    assert web_search_keyless._strip_html("<script>" * 20_000) == ""
    assert web_search_keyless._strip_html("<style>" * 20_000) == ""
    assert web_search_keyless._strip_html("<script>" * 15_000 + "</script>ok") == "ok"
    assert web_search_keyless._strip_html(
        "<style>.a{color:red}</style><script>alert(1)</script>Hello &amp; <b>World</b>"
    ) == "Hello & World"

    # Unclosed and single-trailing-close SERP inputs across all three HTML parsers.
    ddg_unclosed = '<a class="result__a" href="https://example.com/a">' * 5_000
    ddg_trailing = ddg_unclosed + "</a>"
    sp_unclosed = '<a class="result-title" href="https://example.com/a">' * 5_000
    sp_trailing = sp_unclosed + "</a>"
    brave_hostile = (
        '<div class="snippet" data-type="web"><a href="https://example.com/a">x</a>'
        + ('<div class="search-snippet-title">' * 5_000)
        + "</div>"
        + '<span class="t-secondary">'
        + (" " * 50_000)
    )

    assert web_search_keyless.parse_ddg(ddg_unclosed, 10) == []
    web_search_keyless.parse_ddg(ddg_trailing, 10)
    assert web_search_keyless.parse_startpage(sp_unclosed, 10) == []
    web_search_keyless.parse_startpage(sp_trailing, 10)
    assert web_search_keyless.parse_brave(brave_hostile, 10) == []

    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"SERP parsers took {elapsed:.4f}s (possible ReDoS)"

    # Real Brave fixture still parses cleanly.
    fixture = (
        Path(__file__).resolve().parent.parent / "fixtures" / "brave_reddit_serp.html"
    ).read_text(encoding="utf-8")
    now = _dt.datetime(2026, 11, 15, tzinfo=_dt.timezone.utc)
    items = web_search_keyless.parse_brave(fixture, 20, now=now)
    assert len(items) >= 10
    assert any("1xattdz" in item["url"] and item["date"] == "2026-10-16" for item in items)


# ---------------------------------------------------------------------------
# HIGH-203: LAST30DAYS_SEARXNG_URL validation & redirect_guard on rungs/SearXNG
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "file:///etc/passwd",
        "ftp://searxng.example/search",
        "data:text/html,hello",
        "javascript:alert(1)",
        "gopher://127.0.0.1:70/_",
        "http://[::1",
        "http://searxng.example:99999",
        "http:///no-host",
        "https://user:pass@searxng.example",
        "https://searxng.example\r\nX-Injected: 1",
        "https://searxng.example\\evil",
        "https://searxng.example?injected=1",
        "https://searxng.example#frag",
        "http://169.254.169.254",
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.1.1:8080",
        "http://2852039166/",
        "http://0251.0376.0251.0376/",
        "http://[::ffff:169.254.169.254]/",
        "http://[64:ff9b::169.254.169.254]/",
        "http://[fe80::1]:8080/",
        "http://[fd00:ec2::254]/",
        "http://100.100.100.200/",
        "http://metadata.google.internal/",
        "http://metadata.goog/",
        "http://0.0.0.0:8080/",
        "http://[::]:8080/",
        "http://169\uff0e254\uff0e169\uff0e254",
        "http://169\u3002254\u3002169\u3002254/",
        "http://\uff11\uff16\uff19\uff0e\uff12\uff15\uff14\uff0e\uff11\uff16\uff19\uff0e\uff12\uff15\uff14/",
        "http://\uff4d\uff45\uff54\uff41\uff44\uff41\uff54\uff41.google.internal/",
        "http://\uff4d\uff45\uff54\uff41\uff44\uff41\uff54\uff41.goog/",
        "http://169.254.169.254%25eth0/",
    ],
)
def test_searxng_rejects_unsafe_or_malformed_urls_without_raising(bad_url: str):
    assert web_search_keyless._validate_searxng_url(bad_url) is None
    with patch.object(web_search_keyless.http, "get_text", return_value=None), \
         patch.object(web_search_keyless.http, "get") as mock_get:
        items, artifact = web_search_keyless.keyless_search(
            "topic", ("2026-10-01", "2026-11-01"), {"LAST30DAYS_SEARXNG_URL": bad_url}
        )
    assert items == []
    assert artifact["reason"] == "keyless-search-unavailable"
    mock_get.assert_not_called()


@pytest.mark.parametrize(
    "good_url",
    [
        "http://127.0.0.1:8080",
        "http://localhost:8888",
        "http://[::1]:8080",
        "http://192.168.1.50:8080",
        "http://10.0.0.5:8080/searx",
        "https://searxng.example",
        "https://search.example.org/searxng/",
    ],
)
def test_searxng_allows_self_hosted_loopback_lan_and_public_urls(good_url: str):
    validated = web_search_keyless._validate_searxng_url(good_url)
    assert validated is not None
    normalized, origin = validated
    assert normalized.startswith(("http://", "https://"))
    assert origin[0] in ("http", "https")


def test_fetch_page_attaches_public_only_strict_redirect_guard_and_bounds():
    with patch.object(web_search_keyless.http, "get_text", return_value="") as mock_get:
        web_search_keyless._fetch_page("ddg", "https://html.duckduckgo.com/html/?q=test")
    kwargs = mock_get.call_args.kwargs
    assert kwargs["public_only"] is True
    assert kwargs["max_bytes"] == web_search_keyless._MAX_SERP_BYTES
    assert kwargs["decode_errors"] == "replace"
    guard = kwargs["redirect_guard"]
    assert callable(guard)
    assert guard("https://html.duckduckgo.com/html/?q=test2") is True
    assert guard("http://html.duckduckgo.com/html/?q=test2") is False
    assert guard("https://evil.example/phish") is False
    assert guard("http://127.0.0.1:8080/admin") is False
    assert guard("http://169.254.169.254/latest/meta-data/") is False
    assert guard("file:///etc/passwd") is False

    # Verify http redirect handler blocks private/off-host redirects with this guard.
    handler = http._StripAuthOnCrossOriginRedirect()
    req = Request("https://html.duckduckgo.com/html/?q=test")
    setattr(req, http._PUBLIC_ONLY_ATTR, True)
    setattr(req, http._REDIRECT_GUARD_ATTR, guard)
    for target in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "http://html.duckduckgo.com/downgrade",
        "https://evil.example/redirect",
    ):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(req, None, 302, "Found", {}, target)
        assert exc_info.value.code == 403


def test_searxng_live_server_allows_same_origin_and_blocks_cross_origin_redirects():
    target_hits: list[str] = []

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            target_hits.append(self.path)
            payload = json.dumps({"results": [{"url": "https://secret.example/1", "title": "Leaked", "content": "x"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    target_srv, target_t, target_base = _start_server(TargetHandler)

    class SearxHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if "evil_redirect" in self.path:
                self.send_response(302)
                self.send_header("Location", f"{target_base}/secret")
                self.end_headers()
                return
            if "meta_redirect" in self.path:
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            if "same_origin_redir" in self.path:
                self.send_response(302)
                self.send_header("Location", "/search?q=final&format=json")
                self.end_headers()
                return
            payload = json.dumps({
                "results": [{"url": "https://docs.example.org/post", "title": "OK", "content": "snippet"}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    searx_srv, searx_t, searx_base = _start_server(SearxHandler)
    try:
        # Legitimate local self-hosted SearXNG works, including same-origin redirect.
        items = web_search_keyless._search_searxng("same_origin_redir", 5, searx_base)
        assert len(items) == 1
        assert items[0]["url"] == "https://docs.example.org/post"

        # Cross-port localhost redirect is blocked before connecting to target_srv.
        assert web_search_keyless._search_searxng("evil_redirect", 5, searx_base) == []
        assert target_hits == []

        # Redirect to cloud metadata IP is blocked cleanly without raising.
        assert web_search_keyless._search_searxng("meta_redirect", 5, searx_base) == []
    finally:
        _stop_server(searx_srv, searx_t)
        _stop_server(target_srv, target_t)


def test_searxng_idna_normalizes_valid_idn_and_blocks_fullwidth_redirects():
    validated = web_search_keyless._validate_searxng_url("https://caf\u00e9.example:8443/searx/")
    assert validated is not None
    normalized, origin = validated
    assert normalized == "https://xn--caf-dma.example:8443/searx"
    assert origin == ("https", "xn--caf-dma.example", 8443)

    guard = web_search_keyless._searxng_redirect_guard(origin)
    assert guard("http://169\uff0e254\uff0e169\uff0e254/latest") is False
    assert guard("http://\uff4d\uff45\uff54\uff41\uff44\uff41\uff54\uff41.google.internal/") is False
    assert guard("https://caf\u00e9.example:8443/search?q=1") is True


@pytest.mark.parametrize(
    "resolved_addrs",
    [
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 80))],
        [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("fe80::1", 80, 0, 0))],
        [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("fd00:ec2::254", 80, 0, 0))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("100.100.100.200", 80))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("0.0.0.0", 80))],
        [(socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::", 80, 0, 0))],
        [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("224.0.0.1", 80))],
        [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", 80)),
        ],
    ],
)
def test_searxng_blocks_dns_rebinding_to_imds_and_disallowed_ips_at_connect(
    monkeypatch, resolved_addrs,
):
    connect_calls: list[object] = []

    def fail_connect(self, addr):
        connect_calls.append(addr)
        raise AssertionError(f"socket.connect should never be called for blocked IP {addr}")

    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *_a, **_kw: resolved_addrs)
    monkeypatch.setattr(socket.socket, "connect", fail_connect)

    items = web_search_keyless._search_searxng("topic", 5, "http://169.254.169.254.nip.io")
    assert items == []
    assert connect_calls == []


def test_searxng_connect_time_dns_validation_allows_hostname_resolving_to_loopback(monkeypatch):
    class LoopbackSearxHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            payload = json.dumps({
                "results": [{"url": "https://example.org/local", "title": "Local OK", "content": "hi"}]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv, thread, _base = _start_server(LoopbackSearxHandler)
    port = srv.server_port
    try:
        monkeypatch.setattr(
            http.socket,
            "getaddrinfo",
            lambda host, p, *_a, **_kw: [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", int(p or port)))
            ],
        )
        items = web_search_keyless._search_searxng("topic", 5, f"http://local-searx.example:{port}")
        assert len(items) == 1
        assert items[0]["url"] == "https://example.org/local"
    finally:
        _stop_server(srv, thread)


# ---------------------------------------------------------------------------
# HIGH-204: Bounded reads & errors="replace" decoding without UnicodeDecodeError
# ---------------------------------------------------------------------------


def test_keyless_search_bounds_reads_and_decodes_invalid_utf8_without_raising(monkeypatch):
    class MixedHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if "oversized" in self.path:
                body = b'{"results":[' + (b" " * 4096) + b"]}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
                return
            if "latin1_json" in self.path:
                body = b'{"results":[{"url":"https://example.com/cafe","title":"Caf\xe9","content":"ok"}]}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if "latin1_html" in self.path:
                body = (
                    b'<a class="result__a" href="https://example.com/cafe">Caf\xe9 Guide</a>'
                    b'<a class="result__snippet" href="//x">Best caf\xe9 spots</a>'
                )
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if "bad_shape" in self.path:
                body = b'{"results": 42}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    srv, thread, base = _start_server(MixedHandler)
    try:
        # 1. Oversized SearXNG response rejected by max_bytes without raising.
        monkeypatch.setattr(web_search_keyless, "_MAX_SEARXNG_BYTES", 512)
        assert web_search_keyless._search_searxng("oversized", 5, base) == []

        # 2. Non-UTF-8 byte in SearXNG JSON decoded with replacement, no UnicodeDecodeError.
        items = web_search_keyless._search_searxng("latin1_json", 5, base)
        assert len(items) == 1
        assert items[0]["url"] == "https://example.com/cafe"
        assert items[0]["title"] == "Caf\ufffd"

        # 3. Non-UTF-8 byte in HTML fetched with http.get_text(..., decode_errors="replace").
        html_text = http.get_text(
            f"{base}/latin1_html", retries=1, max_bytes=4096, decode_errors="replace",
        )
        assert html_text is not None
        parsed = web_search_keyless.parse_ddg(html_text, 5)
        assert len(parsed) == 1
        assert parsed[0]["title"] == "Caf\ufffd Guide"
        assert parsed[0]["snippet"] == "Best caf\ufffd spots"

        # 4. Hostile JSON shape degrades cleanly.
        assert web_search_keyless._search_searxng("bad_shape", 5, base) == []

        # 5. Even if a mocked transport raises UnicodeDecodeError or ValueError,
        # _fetch_page and keyless_search never raise.
        with patch.object(
            web_search_keyless.http,
            "get_text",
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte"),
        ):
            items, artifact = web_search_keyless.keyless_search(
                "topic", ("2026-10-01", "2026-11-01"), {}
            )
        assert items == []
        assert artifact["reason"] == "keyless-search-unavailable"
        assert artifact["keyless_rungs"]["ddg"] == web_search_keyless.ERROR
    finally:
        _stop_server(srv, thread)


def test_web_fetch_keyless_enforces_4mib_bound_and_replace_decoding(monkeypatch):
    with patch.object(web_fetch_keyless.http, "get_text", return_value="# OK") as mock_get:
        res = web_fetch_keyless.fetch_markdown("https://example.com/article")
    assert res.ok is True
    assert mock_get.call_args.kwargs.get("max_bytes") == 4 * 1024 * 1024
    assert mock_get.call_args.kwargs.get("decode_errors") == "replace"

    # Non-UTF-8 byte in Jina markdown decodes cleanly with replacement instead of crashing.
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.status = 200
    resp.headers = {}
    resp.read.return_value = b"# Caf\xe9 Notes\n\nBody text."
    with patch.object(web_fetch_keyless.http, "_open_request", return_value=resp):
        res = web_fetch_keyless.fetch_markdown("https://example.com/cafe")
    assert res.ok is True
    assert "Caf\ufffd Notes" in res.markdown

    # Oversized reader response fails cleanly without raising.
    monkeypatch.setattr(web_fetch_keyless, "_MAX_MARKDOWN_BYTES", 64)
    big_resp = MagicMock()
    big_resp.__enter__.return_value = big_resp
    big_resp.__exit__.return_value = False
    big_resp.status = 200
    big_resp.headers = {"Content-Length": "2048"}
    big_resp.read.return_value = b"A" * 2048
    with patch.object(web_fetch_keyless.http, "_open_request", return_value=big_resp):
        res = web_fetch_keyless.fetch_markdown("https://example.com/huge")
    assert res.ok is False
    assert res.reason == "fetch-failed"


# ---------------------------------------------------------------------------
# HIGH-205: Parallel MCP SSE slow-loris deadline, cancel, caps & DELETE cleanup
# ---------------------------------------------------------------------------


def test_parallel_mcp_sse_slow_loris_heartbeats_and_trickle_hit_wall_deadline(monkeypatch):
    stop_server_writes = threading.Event()

    class SlowSSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            chunk = b":\n" if "lines" in self.path else b":"
            while not stop_server_writes.is_set():
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except OSError:
                    break
                stop_server_writes.wait(0.04)

    srv, thread, base = _start_server(SlowSSEHandler)
    try:
        for mode in ("lines", "bytes"):
            monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/{mode}")
            started = time.monotonic()
            with pytest.raises(http.DeadlineExceeded):
                parallel_mcp._request(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    None,
                    deadline_monotonic=started + 0.25,
                )
            elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"slow-loris ({mode}) stalled for {elapsed:.2f}s"
    finally:
        stop_server_writes.set()
        _stop_server(srv, thread)


def test_parallel_mcp_cancel_event_aborts_in_flight_sse_promptly(monkeypatch):
    release = threading.Event()

    class HangingSSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b": heartbeat\n\n")
            self.wfile.flush()
            release.wait(10)

    srv, thread, base = _start_server(HangingSSEHandler)
    try:
        monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/mcp")
        cancel = threading.Event()
        threading.Timer(0.15, cancel.set).start()
        started = time.monotonic()
        with pytest.raises(http.DeadlineExceeded):
            parallel_mcp.search(
                "topic",
                ("2026-10-01", "2026-11-01"),
                deadline_monotonic=started + 10.0,
                cancel=cancel,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"cancel took {elapsed:.2f}s"
    finally:
        release.set()
        _stop_server(srv, thread)


def test_parallel_mcp_sse_enforces_line_and_event_caps(monkeypatch):
    class FakeSSEResp:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self, raw: bytes):
            self._bio = io.BytesIO(raw)

        def readline(self, size=-1):
            return self._bio.readline(size)

    monkeypatch.setattr(parallel_mcp, "_MAX_SSE_LINES", 20)
    with pytest.raises(RuntimeError, match="line limit"):
        parallel_mcp._read_response(FakeSSEResp(b":\n" * 25), 3)

    monkeypatch.setattr(parallel_mcp, "_MAX_SSE_LINES", 500)
    monkeypatch.setattr(parallel_mcp, "_MAX_SSE_EVENTS", 5)
    notif = b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
    with pytest.raises(RuntimeError, match="event limit"):
        parallel_mcp._read_response(FakeSSEResp(notif * 10), 3)


def test_parallel_mcp_bounds_tools_list_pagination_and_total_requests():
    calls: list[dict | None] = []

    def fake_request(message, _api_key, _session_id=None):
        calls.append(message)
        if message is None:
            return {}, None
        method = message.get("method")
        if method == "initialize":
            return {"result": {"protocolVersion": parallel_mcp._PROTOCOL_VERSION}}, "sess-1"
        if method == "notifications/initialized":
            return {}, "sess-1"
        if method == "tools/list":
            idx = len(calls)
            return {"result": {"tools": [{"name": "other"}], "nextCursor": f"cursor-{idx}"}}, "sess-1"
        raise AssertionError(f"unexpected method {method}")

    with patch.object(parallel_mcp, "_request", side_effect=fake_request):
        with pytest.raises(RuntimeError, match="did not advertise web_search"):
            parallel_mcp.search("topic", ("2026-10-01", "2026-11-01"))

    tools_list_calls = [m for m in calls if m is not None and m.get("method") == "tools/list"]
    assert len(tools_list_calls) == parallel_mcp._MAX_TOOLS_LIST_PAGES
    assert len(calls) <= parallel_mcp._MAX_SESSION_REQUESTS
    assert calls[-1] is None  # DELETE cleanup still ran within budget


def test_parallel_mcp_cleanup_delete_is_short_bounded_and_skipped_after_deadline(monkeypatch):
    delete_seen: list[str] = []
    release_delete = threading.Event()

    class HangingDeleteHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            method = body.get("method")
            if method == "initialize":
                reply = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": parallel_mcp._PROTOCOL_VERSION}}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "sess-hang")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "tools/list":
                if "slow_tools" in self.path:
                    release_delete.wait(5)
                    return
                reply = {"jsonrpc": "2.0", "id": body["id"], "error": {"message": "upstream boom"}}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

        def do_DELETE(self):
            delete_seen.append(self.path)
            release_delete.wait(10)

    srv, thread, base = _start_server(HangingDeleteHandler)
    try:
        # Case 1: Upstream fails before deadline, DELETE hangs -> capped at _CLEANUP_TIMEOUT.
        monkeypatch.setattr(parallel_mcp, "_CLEANUP_TIMEOUT", 0.25)
        monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/fast_fail")
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="upstream boom"):
            parallel_mcp.search(
                "topic", ("2026-10-01", "2026-11-01"), deadline_monotonic=started + 5.0,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"hanging DELETE stalled for {elapsed:.2f}s"
        assert len(delete_seen) == 1

        # Case 2: Search itself exceeds deadline -> DELETE is skipped completely.
        delete_seen.clear()
        monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/slow_tools")
        started = time.monotonic()
        with pytest.raises(http.DeadlineExceeded):
            parallel_mcp.search(
                "topic", ("2026-10-01", "2026-11-01"), deadline_monotonic=started + 0.25,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"expired deadline search stalled for {elapsed:.2f}s"
        assert delete_seen == []
    finally:
        release_delete.set()
        _stop_server(srv, thread)


def test_parallel_mcp_scrubs_api_key_in_errors_and_rejects_bad_utf8_json():
    with pytest.raises(RuntimeError) as exc_info:
        parallel_mcp._result(
            {"error": {"message": "Invalid key secret-parallel-token-999"}},
            redactions=frozenset({"secret-parallel-token-999"}),
        )
    assert "secret-parallel-token-999" not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)

    with pytest.raises(RuntimeError, match="invalid JSON-RPC response"):
        parallel_mcp._matching_response(b"\xff\xfe", 1)


def test_parallel_mcp_cleanup_swallows_http_client_bad_status_line():
    responses = [
        ({"result": {"protocolVersion": parallel_mcp._PROTOCOL_VERSION}}, "sess-bad-delete"),
        ({}, "sess-bad-delete"),
        ({"result": {"tools": [{"name": "web_search"}]}}, "sess-bad-delete"),
        (
            {
                "result": {
                    "structuredContent": {
                        "results": [
                            {
                                "url": "https://example.com/kept",
                                "title": "Kept Result",
                                "publish_date": "2026-10-15",
                                "excerpts": ["Still returned after DELETE BadStatusLine"],
                            }
                        ]
                    }
                }
            },
            "sess-bad-delete",
        ),
        http_client.BadStatusLine("MALFORMED-DELETE-REPLY"),
    ]
    with patch.object(parallel_mcp, "_request", side_effect=responses):
        items, artifact = parallel_mcp.search("topic", ("2026-10-01", "2026-11-01"))
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/kept"
    assert artifact["resultCount"] == 1


def test_parallel_mcp_cleanup_swallows_live_malformed_delete_response(monkeypatch):
    class MalformedDeleteHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            method = body.get("method")
            if method == "initialize":
                reply = {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": parallel_mcp._PROTOCOL_VERSION}}
                raw = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Mcp-Session-Id", "sess-live-bad")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if method == "tools/list":
                reply = {"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "web_search"}]}}
            else:
                reply = {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {
                        "structuredContent": {
                            "results": [
                                {
                                    "url": "https://example.com/live-kept",
                                    "title": "Live Kept",
                                    "publish_date": "2026-10-20",
                                    "excerpts": ["Survives malformed DELETE"],
                                }
                            ]
                        }
                    },
                }
            raw = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_DELETE(self):
            # Send invalid HTTP status line directly on the socket to trigger BadStatusLine.
            self.wfile.write(b"NOT-AN-HTTP-STATUS-LINE\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True

    srv, thread, base = _start_server(MalformedDeleteHandler)
    try:
        monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/mcp")
        items, artifact = parallel_mcp.search("topic", ("2026-10-01", "2026-11-01"))
        assert len(items) == 1
        assert items[0]["url"] == "https://example.com/live-kept"
        assert artifact["resultCount"] == 1
    finally:
        _stop_server(srv, thread)


def test_parallel_mcp_abort_handles_socket_without_shutdown_and_early_wake_jitter():
    class MinimalSock:
        pass

    class CustomResp:
        _sock = MinimalSock()

    # Must not raise AttributeError when _sock lacks shutdown().
    parallel_mcp._abort_response(CustomResp())

    # Early timer wake within _DEADLINE_JITTER_SECONDS converts TimeoutError to DeadlineExceeded.
    class EarlyTimeoutSSEResp:
        headers = {"Content-Type": "text/event-stream"}

        def readline(self, _size=-1):
            raise TimeoutError("timed out slightly early")

    deadline = time.monotonic() + 0.01  # 10ms remaining (< 50ms jitter tolerance)
    with pytest.raises(http.DeadlineExceeded):
        parallel_mcp._read_response(EarlyTimeoutSSEResp(), 1, deadline_monotonic=deadline)


def test_parallel_mcp_aborts_slow_stalled_headers_and_exits_worker_thread(monkeypatch):
    stop_headers = threading.Event()
    started_threads: list[threading.Thread] = []
    real_thread_cls = threading.Thread

    class TrackingThread(real_thread_cls):
        def start(self):
            if self.name == "parallel-mcp-request":
                started_threads.append(self)
            return super().start()

    class SlowHeadersHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            # Send status line and drip partial headers without ever sending \r\n\r\n.
            try:
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n")
                self.wfile.flush()
                counter = 0
                while not stop_headers.is_set():
                    counter += 1
                    self.wfile.write(f"X-Slow-{counter}: 1\r\n".encode())
                    self.wfile.flush()
                    stop_headers.wait(0.04)
            except OSError:
                pass

    srv, srv_thread, base = _start_server(SlowHeadersHandler)
    try:
        monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/slow-headers")
        monkeypatch.setattr(parallel_mcp.threading, "Thread", TrackingThread)
        started = time.monotonic()
        with pytest.raises(http.DeadlineExceeded):
            parallel_mcp.search(
                "topic",
                ("2026-10-01", "2026-11-01"),
                deadline_monotonic=started + 0.25,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"stalled headers took {elapsed:.2f}s"
        assert len(started_threads) == 1
        started_threads[0].join(timeout=1.0)
        assert started_threads[0].is_alive() is False, "worker thread leaked after stalled headers abort"
    finally:
        stop_headers.set()
        _stop_server(srv, srv_thread)


def test_grounding_threads_deadline_and_cancel_to_parallel_mcp(monkeypatch):
    seen_requests: list[object] = []

    def fail_open(*_args, **_kwargs):
        seen_requests.append(_args)
        raise AssertionError("No HTTP requests should be issued when deadline expired or cancelled")

    monkeypatch.setattr(parallel_mcp.urllib.request.OpenerDirector, "open", fail_open)

    # 1. Expired deadline via pipeline config key `_research_deadline`.
    with pytest.raises(http.DeadlineExceeded):
        grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {"PARALLEL_API_KEY": "secret-key-999", "_research_deadline": time.monotonic() - 1.0},
            backend="parallel-mcp",
        )

    # 2. Triggered cancel event via pipeline config key `_enrich_cancel`.
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(http.DeadlineExceeded):
        grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {"PARALLEL_API_KEY": "secret-key-999", "_enrich_cancel": cancel},
            backend="parallel-mcp",
        )

    # 3. Direct keyword arguments on grounding.web_search().
    with pytest.raises(http.DeadlineExceeded):
        grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {},
            backend="parallel-mcp",
            deadline_monotonic=time.monotonic() - 1.0,
        )

    assert seen_requests == []


def test_parallel_mcp_set_response_timeout_clamps_to_min_request_timeout_and_remaining():
    class RecordingSock:
        def __init__(self):
            self.timeouts: list[float] = []

        def settimeout(self, val):
            self.timeouts.append(val)

    class FakeResp:
        def __init__(self):
            self._sock = RecordingSock()

    resp = FakeResp()
    clamped = parallel_mcp._set_response_timeout(resp, 55.0, request_timeout=30.0)
    assert resp._sock.timeouts[-1] == 30.0
    assert clamped is False

    clamped = parallel_mcp._set_response_timeout(resp, 5.0, request_timeout=30.0)
    assert resp._sock.timeouts[-1] == 5.0
    assert clamped is True


def test_parallel_mcp_sse_long_line_in_small_chunks_is_linear():
    # 512 KiB single SSE data line arriving in 64-byte chunks (~8,192 chunks).
    # Quadratic buf.find(b"\n") rescanning would scan ~2 GiB; linear scan_pos scans 512 KiB.
    padding = "a" * (512 * 1024)
    line = (
        b'data: {"jsonrpc":"2.0","id":7,"result":{"pad":"'
        + padding.encode("ascii")
        + b'"}}\n\n'
    )

    class SmallChunkSSEResp:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self, raw: bytes, chunk_size: int = 64):
            self._raw = raw
            self._pos = 0
            self._chunk_size = chunk_size

        def read1(self, size=-1):
            if self._pos >= len(self._raw):
                return b""
            n = self._chunk_size if size < 0 else min(self._chunk_size, size)
            out = self._raw[self._pos:self._pos + n]
            self._pos += len(out)
            return out

    started = time.monotonic()
    result = parallel_mcp._read_response(SmallChunkSSEResp(line, chunk_size=64), 7)
    elapsed = time.monotonic() - started
    assert result["id"] == 7
    assert len(result["result"]["pad"]) == len(padding)
    assert elapsed < 0.5, f"chunked long SSE line took {elapsed:.4f}s (possible O(N^2) rescan)"


def test_parallel_mcp_scrubs_bearer_key_from_http_error_reason(monkeypatch):
    secret = "pl_live_super_secret_token_987654"

    class SecretEchoErrorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            status = 401 if "401" in self.path else 500
            self.send_response(
                status,
                f"Rejected Bearer {secret} for key {secret} via https://u:{secret}@proxy.internal",
            )
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv, thread, base = _start_server(SecretEchoErrorHandler)
    try:
        for status in (401, 500):
            monkeypatch.setattr(parallel_mcp, "PARALLEL_MCP_URL", f"{base}/{status}")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                parallel_mcp._request(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    secret,
                )
            err = exc_info.value
            try:
                assert err.code == status
                assert secret not in str(err)
                assert secret not in str(err.reason)
                assert "<redacted>" in str(err.reason)
            finally:
                err.close()
    finally:
        _stop_server(srv, thread)


def test_parallel_mcp_run_deadline_only_shortens_never_extends_60s_cap():
    observed_deadlines: list[float] = []

    def capture_budget(message, _api_key, _session_id=None):
        budget = parallel_mcp._REQUEST_BUDGET.get()
        assert budget is not None and budget[0] is not None
        observed_deadlines.append(budget[0])
        if message is None:
            return {}, None
        method = message.get("method")
        if method == "initialize":
            return {"result": {"protocolVersion": parallel_mcp._PROTOCOL_VERSION}}, "sess-cap"
        if method == "notifications/initialized":
            return {}, "sess-cap"
        if method == "tools/list":
            return {"result": {"tools": [{"name": "web_search"}]}}, "sess-cap"
        if method == "tools/call":
            return {"result": {"structuredContent": {"results": []}}}, "sess-cap"
        raise AssertionError(f"unexpected method {method}")

    # Direct parallel_mcp.search() with 180s deadline must still cap at <= 60s.
    now = time.monotonic()
    with patch.object(parallel_mcp, "_request", side_effect=capture_budget):
        parallel_mcp.search(
            "topic", ("2026-10-01", "2026-11-01"), deadline_monotonic=now + 180.0,
        )
    assert observed_deadlines
    assert observed_deadlines[0] <= time.monotonic() + parallel_mcp._DEFAULT_SEARCH_TIMEOUT + 1.0
    assert observed_deadlines[0] < now + 100.0

    # Via grounding.web_search() with 180s pipeline `_research_deadline`.
    observed_deadlines.clear()
    now = time.monotonic()
    with patch.object(parallel_mcp, "_request", side_effect=capture_budget):
        grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {"_research_deadline": now + 180.0},
            backend="parallel-mcp",
        )
    assert observed_deadlines
    assert observed_deadlines[0] <= time.monotonic() + parallel_mcp._DEFAULT_SEARCH_TIMEOUT + 1.0
    assert observed_deadlines[0] < now + 100.0

    # Shorter caller deadline (e.g. 5s) IS respected and shortens the 60s cap.
    observed_deadlines.clear()
    now = time.monotonic()
    short_deadline = now + 5.0
    with patch.object(parallel_mcp, "_request", side_effect=capture_budget):
        parallel_mcp.search(
            "topic", ("2026-10-01", "2026-11-01"), deadline_monotonic=short_deadline,
        )
    assert observed_deadlines
    assert abs(observed_deadlines[0] - short_deadline) < 0.01


def test_keyless_search_reports_timeout_when_cancelled_or_expired_and_keeps_found_results():
    # (a1) Pre-cancelled keyless_search with 0 results reports timeout, not unavailable.
    cancel = threading.Event()
    cancel.set()
    with patch.object(web_search_keyless.http, "get_text") as mock_get:
        items, artifact = web_search_keyless.keyless_search(
            "topic", ("2026-10-01", "2026-11-01"), {}, cancel=cancel,
        )
    assert items == []
    mock_get.assert_not_called()
    assert artifact["reason"] == "keyless-search-timeout"
    assert artifact["reason"] != "keyless-search-unavailable"
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT
    assert artifact["_source_outcome"]["attempted"] is False

    # (a2) Via grounding.web_search() with expired `_research_deadline`: pipeline maps to TIMEOUT.
    with patch.object(web_search_keyless.http, "get_text") as mock_get:
        items, artifact = grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {"_research_deadline": time.monotonic() - 1.0},
            backend="keyless",
        )
    assert items == []
    mock_get.assert_not_called()
    assert artifact["reason"] == "keyless-search-timeout"
    resolved = pipeline._resolve_stream_outcome("grounding", artifact, [])
    assert resolved is not None
    assert resolved["state"] == health.TIMEOUT

    # (b) Cancellation after rung 1 already found results preserves rung 1's results.
    cancel_after_rung1 = threading.Event()
    ddg_html = (
        '<a class="result__a" href="https://example.com/kept">Kept Title</a>'
        '<a class="result__snippet" href="//x">Kept snippet</a>'
    )

    def ddg_then_cancel(url, *args, **kwargs):
        cancel_after_rung1.set()
        return ddg_html

    with patch.object(web_search_keyless.http, "get_text", side_effect=ddg_then_cancel):
        items, artifact = grounding.web_search(
            "topic",
            ("2026-10-01", "2026-11-01"),
            {"_enrich_cancel": cancel_after_rung1},
            backend="keyless",
        )
    assert len(items) == 1
    assert items[0]["url"] == "https://example.com/kept"
    assert "reason" not in artifact
    assert "_source_outcome" not in artifact


def test_grounding_enrich_reddit_items_forwards_deadline_and_preserves_items_on_timeout():
    from lib import reddit_hydrate

    web_items = [
        {
            "url": "https://www.reddit.com/r/python/comments/abc123/test_post/",
            "title": "Original Web Title",
            "snippet": "Original web snippet",
        },
        {
            "url": "https://example.com/non-reddit",
            "title": "Example Article",
            "snippet": "Non-reddit snippet",
        },
    ]
    seen_deadlines: list[float | None] = []

    def timeout_hydrate(refs, **kwargs):
        seen_deadlines.append(kwargs.get("deadline"))
        raise http.DeadlineExceeded()

    deadline = time.monotonic() + 30.0
    expected_enrich_deadline = deadline - grounding._REDDIT_ENRICH_HEADROOM_SECONDS
    with patch.object(reddit_hydrate, "hydrate", side_effect=timeout_hydrate):
        kept = grounding._enrich_reddit_items(list(web_items), deadline_monotonic=deadline)

    assert len(seen_deadlines) == 1
    assert seen_deadlines[0] is not None
    assert seen_deadlines[0] <= expected_enrich_deadline
    assert seen_deadlines[0] == pytest.approx(expected_enrich_deadline)
    assert kept == web_items

    # Live path through grounding.web_search() forwards headroom-trimmed deadline into hydrate()
    # and keeps web results intact when optional Reddit enrichment times out.
    seen_deadlines.clear()
    with patch.object(web_search_keyless, "keyless_search", return_value=(list(web_items), {"keyless_backend": "ddg"})), \
         patch.object(reddit_hydrate, "hydrate", side_effect=timeout_hydrate):
        items, artifact = grounding.web_search(
            "python",
            ("2026-10-01", "2026-11-01"),
            {"_research_deadline": deadline},
            backend="keyless",
        )
    assert len(seen_deadlines) == 1
    assert seen_deadlines[0] is not None
    assert seen_deadlines[0] <= expected_enrich_deadline
    assert seen_deadlines[0] == pytest.approx(expected_enrich_deadline)
    assert items == web_items
    assert artifact["keyless_backend"] == "ddg"

    # When remaining run budget is within headroom (e.g. 1.0s < 3.0s), skip Reddit enrichment
    # completely and return the retrieved web items intact without calling hydrate().
    tight_deadline = time.monotonic() + 1.0
    with patch.object(web_search_keyless, "keyless_search", return_value=(list(web_items), {"keyless_backend": "ddg"})), \
         patch.object(reddit_hydrate, "hydrate") as mock_hydrate:
        items, artifact = grounding.web_search(
            "python",
            ("2026-10-01", "2026-11-01"),
            {"_research_deadline": tight_deadline},
            backend="keyless",
        )
    mock_hydrate.assert_not_called()
    assert items == web_items
    assert artifact["keyless_backend"] == "ddg"

    # Direct _enrich_reddit_items() with tight deadline also skips hydrate() cleanly.
    with patch.object(reddit_hydrate, "hydrate") as mock_hydrate:
        kept_tight = grounding._enrich_reddit_items(list(web_items), deadline_monotonic=tight_deadline)
    mock_hydrate.assert_not_called()
    assert kept_tight == web_items


def test_keyless_search_counts_searxng_attempt_when_html_rungs_skipped_and_searxng_times_out():
    web_search_keyless.reset_rung_health()
    try:
        web_search_keyless.seed_rung_health({"ddg": "blocked", "startpage": "throttled"})

        def searxng_timeout(*_args, **_kwargs):
            err = http.DeadlineExceeded()
            http._record_failure(err)
            raise err

        with patch.object(web_search_keyless.http, "get_text") as mock_get_text, \
             patch.object(web_search_keyless.http, "get", side_effect=searxng_timeout) as mock_get:
            items, artifact = web_search_keyless.keyless_search(
                "topic",
                ("2026-10-01", "2026-11-01"),
                {"LAST30DAYS_SEARXNG_URL": "https://searxng.example"},
                deadline_monotonic=time.monotonic() + 5.0,
            )
        mock_get_text.assert_not_called()
        mock_get.assert_called_once()
        assert items == []
        assert artifact["keyless_backend"] == "searxng"
        assert artifact["keyless_rungs"] == {"ddg": "skipped", "startpage": "skipped"}
        assert artifact["reason"] == "keyless-search-timeout"
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert artifact["_source_outcome"]["attempted"] is True
    finally:
        web_search_keyless.reset_rung_health()


def test_rate_limiter_acquire_respects_deadline_and_cancel():
    # Drained slow limiter (10s to refill): fails fast before deadline instead of sleeping 10s.
    limiter = http.RateLimiter(rate_per_sec=0.1, burst=1)
    limiter.acquire()  # drain single token

    t0 = time.monotonic()
    with pytest.raises(http.DeadlineExceeded):
        limiter.acquire(deadline_monotonic=t0 + 0.2)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5
    assert limiter.waiting == 0

    # Expired deadline raises without consuming an available token.
    fresh = http.RateLimiter(rate_per_sec=0.1, burst=1)
    with pytest.raises(http.DeadlineExceeded):
        fresh.acquire(deadline_monotonic=time.monotonic() - 0.01)
    assert fresh._tokens >= 1.0
    assert fresh.waiting == 0

    # Cancel event wakes a waiting acquirer promptly and cleans up waiting count.
    cancel = threading.Event()
    slow = http.RateLimiter(rate_per_sec=0.1, burst=1)
    slow.acquire()

    def trigger_cancel():
        time.sleep(0.05)
        cancel.set()

    canceller = threading.Thread(target=trigger_cancel, daemon=True)
    canceller.start()
    t1 = time.monotonic()
    with pytest.raises(http.DeadlineExceeded):
        slow.acquire(deadline_monotonic=t1 + 20.0, cancel=cancel)
    elapsed_cancel = time.monotonic() - t1
    canceller.join(1.0)
    assert elapsed_cancel < 1.0
    assert slow.waiting == 0


def test_grounding_reddit_enrichment_stops_waiting_on_busy_limiter_before_deadline():
    from lib import reddit_arctic, reddit_health

    web_items = [
        {
            "url": "https://www.reddit.com/r/python/comments/abc123/test_post/",
            "title": "Original Web Title",
            "snippet": "Original web snippet",
        },
        {
            "url": "https://example.com/non-reddit",
            "title": "Example Article",
            "snippet": "Non-reddit snippet",
        },
    ]

    busy_limiter = http.RateLimiter(rate_per_sec=0.05, burst=1)  # 20s per token
    busy_limiter.acquire()  # drain bucket so next token is 20s away

    reddit_arctic.reset_memo()
    with patch.object(http, "REDDIT_KEYLESS_LIMITER", busy_limiter), \
         patch.object(reddit_health, "skip_reason", return_value=None), \
         patch.object(web_search_keyless, "keyless_search", return_value=(list(web_items), {"keyless_backend": "ddg"})), \
         patch.object(reddit_arctic.http, "get", side_effect=AssertionError("should not send HTTP after limiter deadline")):
        t0 = time.monotonic()
        # 3.5s outer deadline => 0.5s enrichment budget after 3.0s headroom, well under 20s token wait.
        items, artifact = grounding.web_search(
            "python",
            ("2026-10-01", "2026-11-01"),
            {"_research_deadline": t0 + 3.5},
            backend="keyless",
        )
        elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert items == web_items
    assert artifact["keyless_backend"] == "ddg"
    assert busy_limiter.waiting == 0

    # Multi-batch fetch_posts_by_ids and fetch_comment_tree both fail fast on busy limiter.
    many_ids = [f"p{i:03d}" for i in range(150)]
    with patch.object(http, "REDDIT_KEYLESS_LIMITER", busy_limiter), \
         patch.object(reddit_arctic.http, "get", side_effect=AssertionError("should not send HTTP")):
        t1 = time.monotonic()
        found, state = reddit_arctic.fetch_posts_by_ids(many_ids, deadline=t1 + 0.2)
        elapsed_ids = time.monotonic() - t1
        assert elapsed_ids < 0.5
        assert found == {}
        assert state == "down"

        t2 = time.monotonic()
        tree = reddit_arctic.fetch_comment_tree("abc123", deadline=t2 + 1.5)
        elapsed_tree = time.monotonic() - t2
        assert elapsed_tree < 0.5
        assert tree is None
    assert busy_limiter.waiting == 0


def test_http_ip_validator_and_local_bridge_mutually_exclusive_and_trailing_dot_preserved():
    with pytest.raises(ValueError, match="mutually exclusive"):
        http.request(
            "GET",
            "http://127.0.0.1:8080/",
            local_bridge_only=True,
            ip_validator=lambda _ip: True,
        )

    seen_hosts: list[str] = []

    def fake_getaddrinfo(host, port, family=0, socktype=0):
        seen_hosts.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

    with patch.object(http.socket, "getaddrinfo", side_effect=fake_getaddrinfo):
        verified = http._resolve_validated_addresses("searx.example.", 443, lambda _ip: True)
    assert seen_hosts == ["searx.example."]
    assert len(verified) == 1





