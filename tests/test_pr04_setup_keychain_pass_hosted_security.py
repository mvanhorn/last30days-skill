"""Security hardening regression tests for PR04 (HIGH-063, HIGH-065, HIGH-068, HIGH-078, HIGH-085)."""

from __future__ import annotations

import ast
import http.server as stdlib_http
import io
import json
import os
import shlex
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

from lib import hosted, http, setup_wizard


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
SETUP_KEYCHAIN_SH = SCRIPTS_DIR / "setup-keychain.sh"
SETUP_PASS_SH = SCRIPTS_DIR / "setup-pass.sh"


@pytest.fixture
def serve():
    running = []

    def start(*, payload=None, redirect_to=None, status=200, raw_body=None):
        requests = []

        class Handler(stdlib_http.BaseHTTPRequestHandler):
            def do_POST(self):
                self._respond()

            def do_GET(self):
                self._respond()

            def _respond(self):
                length = int(self.headers.get("Content-Length", "0"))
                body_in = self.rfile.read(length) if length else b""
                requests.append(
                    (
                        self.command,
                        self.path,
                        {key.lower(): value for key, value in self.headers.items()},
                        body_in,
                    )
                )
                if redirect_to is not None:
                    self.send_response(302)
                    self.send_header("Location", redirect_to)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if raw_body is not None:
                    data = raw_body if isinstance(raw_body, bytes) else raw_body.encode("utf-8")
                else:
                    data = json.dumps(payload if payload is not None else {"ok": True}).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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


def _enable_proxy(monkeypatch, proxy_url: str) -> None:
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setattr(http, "_opener", http._build_http_opener(bypass_proxy=False))
    monkeypatch.setattr(http, "_direct_opener", http._build_http_opener(bypass_proxy=True))


# ---------------------------------------------------------------------------
# HIGH-063: hosted.py loopback proxy bypass, header sanitization, URL hygiene
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_hosted_submit_and_poll_bypass_http_proxy_on_loopback(monkeypatch, serve, host):
    endpoint, endpoint_requests = serve(
        payload={"search_id": "abc-123", "status": "complete", "synthesis_text": "direct"}
    )
    proxy_url, proxy_requests = serve(payload={"search_id": "proxied", "status": "error"})
    _enable_proxy(monkeypatch, proxy_url)
    monkeypatch.setenv("LAST30DAYS_API_BASE", endpoint.replace("127.0.0.1", host, 1) + "/api/v1")
    monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_loopback_secret_0001")

    sub = hosted.submit("topic", "quick")
    polled = hosted.poll("abc-123")

    assert sub["search_id"] == "abc-123"
    assert polled["status"] == "complete"
    assert proxy_requests == []
    assert len(endpoint_requests) == 2
    assert endpoint_requests[0][0:2] == ("POST", "/api/v1/search")
    assert endpoint_requests[1][0:2] == ("GET", "/api/v1/search?id=abc-123")
    for _, _, headers, _ in endpoint_requests:
        assert headers["authorization"] == "Bearer sk_live_loopback_secret_0001"


@pytest.mark.parametrize("raw_key", ["sk_live_secret_trail\n", "  sk_live_secret_trail\r\n  "])
def test_hosted_strips_trailing_newline_from_api_key(monkeypatch, serve, raw_key):
    endpoint, endpoint_requests = serve(payload={"search_id": "ok-1", "status": "running"})
    monkeypatch.setenv("LAST30DAYS_API_BASE", f"  {endpoint}/api/v1/  ")
    monkeypatch.setenv("LAST30DAYS_API_KEY", raw_key)

    res = hosted.submit("topic", "quick")

    assert res["search_id"] == "ok-1"
    assert endpoint_requests[0][2]["authorization"] == "Bearer sk_live_secret_trail"


@pytest.mark.parametrize(
    "bad_key",
    [
        "sk_live_SPLIT_SECRET\r\nX-Injected: 1",
        "sk_live_SPLIT_SECRET\nsecond-line",
        "sk_live_SPLIT_SECRET\x00tail",
        "sk_live_SPLIT_SECRET\ttab",
        "sk_live_SPLIT_SECRET_🔑",
        "   \n\r  ",
    ],
)
def test_hosted_rejects_invalid_api_key_without_leaking_secret(monkeypatch, capsys, bad_key):
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
    if "\x00" in bad_key:
        monkeypatch.setattr(
            hosted.env,
            "read_secret_env",
            lambda name, default=None: bad_key if name == "LAST30DAYS_API_KEY" else default,
        )
    else:
        monkeypatch.setenv("LAST30DAYS_API_KEY", bad_key)

    with mock.patch.object(hosted.http, "post") as post, mock.patch.object(hosted.http, "get") as get:
        with pytest.raises(ValueError) as sub_exc:
            hosted.submit("topic", "quick")
        with pytest.raises(ValueError) as poll_exc:
            hosted.poll("id-1")
        rc = hosted.run_hosted("topic", "quick")

    captured = capsys.readouterr()
    post.assert_not_called()
    get.assert_not_called()
    assert rc == 1
    for blob in (str(sub_exc.value), str(poll_exc.value), captured.out, captured.err):
        assert "sk_live_SPLIT_SECRET" not in blob


def test_hosted_catches_putheader_value_error_without_echoing_secret(monkeypatch, capsys):
    secret = "sk_live_NEVER_ECHO_98765"
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
    monkeypatch.setenv("LAST30DAYS_API_KEY", secret)

    def boom(*_args, **_kwargs):
        raise ValueError(f"Invalid header value b'Bearer {secret}\\n'")

    monkeypatch.setattr(hosted.http, "post", boom)
    with pytest.raises(ValueError) as exc_info:
        hosted.submit("topic", "quick")
    assert secret not in str(exc_info.value)

    rc = hosted.run_hosted("topic", "quick")
    captured = capsys.readouterr()
    assert rc == 1
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize(
    "bad_base,secret_marker",
    [
        ("https://alice:topsecretpass@api.example.test/api/v1", "topsecretpass"),
        ("https://alice:p@ss@word@api.example.test/api/v1", "p@ss@word"),
        ("https://tokenonly@api.example.test/api/v1", "tokenonly"),
        ("https://api.example.test/api/v1?secret=qval", "qval"),
        ("https://api.example.test/api/v1#fragsecret", "fragsecret"),
        ("https://api.example.test/api/v1\r\nX-Injected: 1", "X-Injected"),
        ("https://api.example.test/api/v1 bad", "bad"),
        ("https://evil.example\\@good.example/api/v1", "evil.example"),
        ("https://api.example.test:99999/api/v1", "99999"),
    ],
)
def test_hosted_api_base_rejects_userinfo_query_fragment_and_control_chars(
    monkeypatch, capsys, bad_base, secret_marker
):
    monkeypatch.setenv("LAST30DAYS_API_BASE", bad_base)
    monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_dummy_key")

    with mock.patch.object(hosted.http, "post") as post:
        rc = hosted.run_hosted("topic", "quick")

    captured = capsys.readouterr()
    post.assert_not_called()
    assert rc == 1
    assert secret_marker not in captured.out + captured.err


def test_hosted_scrub_url_userinfo_handles_ipv6_at_passwords_and_server_errors(monkeypatch, capsys):
    assert hosted._scrub_url_userinfo("https://u:p@api.example.test:8443/api/v1") == "https://api.example.test:8443/api/v1"
    assert hosted._scrub_url_userinfo("https://u:p@ss@word@api.example.test/api/v1") == "https://api.example.test/api/v1"
    assert hosted._scrub_url_userinfo("http://u:p@[::1]:8080/api/v1") == "http://[::1]:8080/api/v1"
    assert hosted._scrub_url_userinfo("https://api.example.test/api/v1") == "https://api.example.test/api/v1"

    # Whitespace-only LAST30DAYS_API_KEY must not redact ordinary spaces.
    monkeypatch.setenv("LAST30DAYS_API_KEY", "   ")
    assert hosted._scrub_error_text("hello world https://u:p@ss@api.example.test/v1") == "hello world https://api.example.test/v1"

    # Defense-in-depth: even if _api_base is monkeypatched to include userinfo, logs scrub it,
    # and server-returned balance/needed, unexpected submit payloads, and error rows scrub the key.
    secret = "sk_live_HOSTED_ECHO_9999"
    monkeypatch.setenv("LAST30DAYS_API_KEY", secret)
    monkeypatch.setattr(hosted, "_api_base", lambda: "https://alice:leakpw@api.example.test/api/v1")
    monkeypatch.setattr(
        hosted.http,
        "post",
        mock.Mock(
            side_effect=http.HTTPError(
                "HTTP 402",
                402,
                json.dumps({"error": f"No credits for {secret}", "balance": secret, "needed": secret}),
            )
        ),
    )
    rc = hosted.run_hosted("topic", "quick")
    captured = capsys.readouterr()
    assert rc == 1
    assert "leakpw" not in captured.err
    assert "alice@" not in captured.err
    assert secret not in captured.err
    assert "https://api.example.test/dashboard/billing" in captured.err

    # Unexpected response without search_id scrubs API key.
    monkeypatch.setattr(hosted.http, "post", mock.Mock(return_value={"oops": secret}))
    assert hosted.run_hosted("topic", "quick") == 1
    assert secret not in capsys.readouterr().err

    # Terminal error row scrubs API key.
    monkeypatch.setattr(hosted.time, "sleep", lambda _s: None)
    monkeypatch.setattr(hosted.http, "post", mock.Mock(return_value={"search_id": "sid-1"}))
    monkeypatch.setattr(hosted.http, "get", mock.Mock(return_value={"status": "error", "error": f"bad {secret}"}))
    assert hosted.run_hosted("topic", "quick") == 1
    assert secret not in capsys.readouterr().err


@pytest.mark.parametrize("code", [302, 307, 308])
def test_hosted_submit_and_poll_refuse_cross_origin_redirects(monkeypatch, serve, code):
    attacker_url, attacker_requests = serve(payload={"search_id": "stolen", "status": "complete"})

    class RedirectHandler(stdlib_http.BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self._redirect()

        def do_GET(self):
            self._redirect()

        def _redirect(self):
            self.send_response(code)
            self.send_header("Location", f"{attacker_url}/steal")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = stdlib_http.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("LAST30DAYS_API_BASE", f"http://127.0.0.1:{server.server_port}/api/v1")
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_never_redirect_key")
        with pytest.raises(http.HTTPError) as sub_err:
            hosted.submit("sensitive query", "quick")
        assert sub_err.value.status_code == 403
        with pytest.raises(http.HTTPError) as poll_err:
            hosted.poll("sid-1")
        assert poll_err.value.status_code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert attacker_requests == []


# ---------------------------------------------------------------------------
# HIGH-065: setup_wizard.py verification_uri validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "uri",
    [
        "https://github.com/login/device",
        "https://github.com/login/device/",
        "https://github.com/login/device/confirmation",
        "https://github.com:443/login/device",
    ],
)
def test_verification_uri_validator_accepts_legitimate_github_uris(uri):
    assert setup_wizard._is_valid_verification_uri(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "ms-msdt:/id PCWDiagnostic",
        "smb://attacker.example/share",
        "javascript:alert(1)",
        "http://github.com/login/device",
        "https://evil.example/login/device",
        "https://enterprise.github.com/login/device",
        "https://gist.github.com/login/device",
        "https://evil.github.com/login/device",
        "https://github.com.evil.example/login/device",
        "https://evilgithub.com/login/device",
        "https://.github.com/login/device",
        "https://github.com./login/device",
        "https://user:pass@github.com/login/device",
        "https://github.com:8443/login/device",
        "https://github.com/evil",
        "https://github.com/login/device?user_code=ABCD-1234",
        "https://github.com/login/device?return_to=https://evil.example",
        "https://github.com/login/device#frag",
        "https://github.com/login/device-evil",
        "https://github.com/login/device/../../evil",
        "https://github.com/login/device/%2e%2e/evil",
        "https://github.com/login/device\\evil",
        "https://github.com/login/device\r\n",
        " https://github.com/login/device",
        "   ",
        12345,
        ["https://github.com/login/device"],
    ],
)
def test_verification_uri_validator_rejects_unsafe_uris(uri):
    assert setup_wizard._is_valid_verification_uri(uri) is False


@pytest.mark.parametrize(
    "bad_uri",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://github.com/login/device",
        "https://phishing.example/login/device",
        "https://user:pass@github.com/login/device",
        "   ",
    ],
)
def test_start_device_flow_aborts_before_browser_clipboard_or_stdout_on_bad_uri(bad_uri):
    with mock.patch("lib.setup_wizard._existing_scrapecreators_key", return_value=None), \
         mock.patch("lib.setup_wizard.run_device_auth", return_value=("dev-1", "ABCD-1234", bad_uri, 5)), \
         mock.patch("lib.setup_wizard.subprocess.run") as mock_pbcopy, \
         mock.patch("webbrowser.open") as mock_browser:
        out = io.StringIO()
        with redirect_stdout(out):
            public, handle = setup_wizard._start_device_flow()

    assert public["status"] == "error"
    assert handle is None
    mock_browser.assert_not_called()
    mock_pbcopy.assert_not_called()
    assert "device_code_ready" not in out.getvalue()
    assert bad_uri.strip() not in out.getvalue() or not bad_uri.strip()


def test_run_device_auth_rejects_untrusted_verification_uri_response():
    payload = json.dumps(
        {
            "device_code": "dc-123",
            "user_code": "ABCD-1234",
            "verification_uri": "file:///etc/passwd",
            "interval": 5,
        }
    ).encode("utf-8")
    resp = mock.MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    with mock.patch("lib.setup_wizard.urlopen", return_value=resp):
        assert setup_wizard.run_device_auth() is None


# ---------------------------------------------------------------------------
# HIGH-068: setup_wizard.py no-redirect http.open_request & error scrubbing
# ---------------------------------------------------------------------------


def test_setup_wizard_has_no_direct_urllib_urlopen_calls():
    source = Path(setup_wizard.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "urllib.request":
            names = {alias.name for alias in node.names}
            assert "urlopen" not in names


def test_fetch_api_key_refuses_cross_origin_redirect_and_never_leaks_token(monkeypatch, serve):
    attacker_url, attacker_requests = serve(payload={"api_key": "sc_forged_attacker_key"})
    victim_url, victim_requests = serve(redirect_to=f"{attacker_url}/steal")
    monkeypatch.setattr(setup_wizard, "_DEVICE_BASE", f"{victim_url}/v1/github/device")

    result = setup_wizard.fetch_api_key("gho_top_secret_oauth_token_12345")

    assert result["ok"] is False
    assert result["reason"] == "http_error"
    assert result["http_status"] == 403
    assert attacker_requests == []
    assert len(victim_requests) == 1
    assert victim_requests[0][2]["authorization"] == "Bearer gho_top_secret_oauth_token_12345"
    assert "gho_top_secret_oauth_token_12345" not in json.dumps(result)


def test_run_device_auth_and_poll_refuse_redirects(monkeypatch, serve):
    attacker_url, attacker_requests = serve(payload={"access_token": "forged"})
    victim_url, _victim_requests = serve(redirect_to=f"{attacker_url}/steal")
    monkeypatch.setattr(setup_wizard, "_DEVICE_BASE", f"{victim_url}/v1/github/device")

    assert setup_wizard.run_device_auth() is None
    assert attacker_requests == []

    # poll_device_auth fails fast on a refused redirect instead of looping until timeout.
    with mock.patch("lib.setup_wizard.time.sleep") as mock_sleep, \
         mock.patch("lib.setup_wizard.time.time", return_value=0):
        assert setup_wizard.poll_device_auth("dc-secret-123", interval=1, timeout=300) is None
    mock_sleep.assert_called_once_with(1)
    assert attacker_requests == []


def test_device_auth_refuses_redirects_and_ignores_wrapped_urllib_urlopen(monkeypatch, serve):
    attacker_url, attacker_requests = serve(payload={"api_key": "sc_stolen"})
    victim_url, _victim_requests = serve(redirect_to=f"{attacker_url}/steal")
    monkeypatch.setattr(setup_wizard, "_DEVICE_BASE", f"{victim_url}/v1/github/device")

    def forbidden_urlopen(*_args, **_kwargs):
        raise AssertionError("setup_wizard must not delegate through urllib.request.urlopen")

    with mock.patch("urllib.request.urlopen", side_effect=forbidden_urlopen):
        res = setup_wizard.fetch_api_key("gho_secret_oauth_token_wrapped")
        assert res["ok"] is False
        assert res["http_status"] == 403
        assert setup_wizard.run_device_auth() is None
        with mock.patch("lib.setup_wizard.time.sleep"), \
             mock.patch("lib.setup_wizard.time.time", return_value=0):
            assert setup_wizard.poll_device_auth("dc-123", interval=1, timeout=300) is None

    assert attacker_requests == []


def test_device_opener_enforces_https_only_and_drops_non_https_handlers(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy.example.test:8443")
    opener = setup_wizard._build_device_opener(loopback_http=False)
    for scheme in ("http", "file", "ftp", "data"):
        assert scheme not in opener.handle_open
    assert "https" in opener.handle_open
    proxy_handlers = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert set(proxy_handlers[0].proxies.keys()) <= {"https"}

    for bad_url in ("http://remote.example.test/v1/github/device/profile", "file:///etc/passwd", "ftp://example.test/x"):
        with pytest.raises(urllib.error.URLError):
            setup_wizard._default_urlopen(urllib.request.Request(bad_url), timeout=1)


def test_device_auth_bounds_response_size_validates_json_and_closes_http_errors():
    oversized = b'{"api_key":"' + (b"a" * (setup_wizard._MAX_DEVICE_RESPONSE_BYTES + 10)) + b'"}'
    for bad_payload in (oversized, b"not-json", b'["not-a-dict"]'):
        resp = mock.MagicMock()
        resp.read.side_effect = lambda n=-1, data=bad_payload: data[:n] if n >= 0 else data
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with mock.patch("lib.setup_wizard.urlopen", return_value=resp):
            assert setup_wizard.run_device_auth() is None
            res = setup_wizard.fetch_api_key("gho_valid_tok")
            assert res["ok"] is False and res["reason"] == "request_failed"
            with mock.patch("lib.setup_wizard.time.sleep"), \
                 mock.patch("lib.setup_wizard.time.time", return_value=0):
                assert setup_wizard.poll_device_auth("dc-1", interval=1, timeout=300) is None

    # Verify HTTPError streams are always closed across all three callers.
    closed_flags = []

    class TrackingBytesIO(io.BytesIO):
        def close(self):
            closed_flags.append(True)
            super().close()

    def make_err(code=401):
        return urllib.error.HTTPError(
            "https://api.scrapecreators.com/v1/github/device/profile",
            code,
            "Err",
            {},
            TrackingBytesIO(b'{"error":"fail"}'),
        )

    with mock.patch("lib.setup_wizard.urlopen", side_effect=lambda *a, **k: (_ for _ in ()).throw(make_err(401))):
        setup_wizard.run_device_auth()
        setup_wizard.fetch_api_key("gho_valid_tok")
        with mock.patch("lib.setup_wizard.time.sleep"), \
             mock.patch("lib.setup_wizard.time.time", return_value=0):
            setup_wizard.poll_device_auth("dc-1", interval=1, timeout=300)

    assert len(closed_flags) >= 3


def test_setup_wizard_scrub_handles_at_password_and_whitespace_secret():
    assert setup_wizard._scrub_error_text(
        "https://alice:p@ss@word@api.scrapecreators.com/profile", secrets=("   ",)
    ) == "https://api.scrapecreators.com/profile"
    assert setup_wizard._scrub_error_text("plain message with spaces", secrets=("   ",)) == "plain message with spaces"


def test_fetch_api_key_scrubs_access_token_and_secrets_from_http_error_bodies(caplog):
    opaque_token = "opaque_tok_9876543210_SECRET"
    # Place token across the 200-char boundary to prove scrubbing happens before truncation.
    prefix = "x" * 190
    body = (
        f"{prefix}{opaque_token} "
        'Authorization: Bearer bearer_leaked_abc123 '
        'gho_github_leaked_token_999 '
        '{"api_key":"sc_leaked_999","access_token":"tok_leaked_888"} '
        "https://bob:pwsecret@api.scrapecreators.com/profile"
    ).encode("utf-8")

    def fake_error(_req, timeout=15):
        raise urllib.error.HTTPError(
            "https://api.scrapecreators.com/v1/github/device/profile",
            401,
            f"Unauthorized for {opaque_token}",
            {},
            io.BytesIO(body),
        )

    with caplog.at_level("WARNING", logger=setup_wizard.logger.name), \
         mock.patch("lib.setup_wizard.urlopen", side_effect=fake_error):
        result = setup_wizard.fetch_api_key(opaque_token)

    assert result["ok"] is False
    assert result["reason"] == "http_error"
    assert result["http_status"] == 401
    combined = json.dumps(result) + "\n" + caplog.text
    for leaked in (
        opaque_token,
        "opaque_tok_9876",
        "bearer_leaked_abc123",
        "gho_github_leaked_token_999",
        "sc_leaked_999",
        "tok_leaked_888",
        "pwsecret",
    ):
        assert leaked not in combined
    assert "[REDACTED]" in result["detail"]


def test_fetch_api_key_rejects_control_chars_in_access_token_without_echoing():
    bad_token = "gho_bad_token_SECRET\r\nX-Injected: 1"
    with mock.patch("lib.setup_wizard.urlopen") as mock_urlopen:
        result = setup_wizard.fetch_api_key(bad_token)

    mock_urlopen.assert_not_called()
    assert result["ok"] is False
    assert result["reason"] == "request_failed"
    assert "gho_bad_token_SECRET" not in json.dumps(result)


# ---------------------------------------------------------------------------
# HIGH-078: setup-keychain.sh pins security binary & passes secret via stdin
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
def test_setup_keychain_bash_syntax_and_no_argv_secret_flag():
    check = subprocess.run(["bash", "-n", str(SETUP_KEYCHAIN_SH)], capture_output=True, text=True, timeout=10)
    assert check.returncode == 0, check.stderr
    script = SETUP_KEYCHAIN_SH.read_text(encoding="utf-8")
    assert '-w "$value"' not in script
    assert "command -v security" not in script
    assert 'if [[ -x /usr/bin/security ]]; then\n  SECURITY_BIN="/usr/bin/security"' in script
    assert 'SECURITY_BIN="${SECURITY_BIN:-' not in script


@pytest.mark.skipif(
    os.name == "nt" or Path("/usr/bin/security").exists(),
    reason="POSIX non-macOS shell fixture (macOS pins /usr/bin/security unconditionally)",
)
def test_setup_keychain_ignores_path_shadow_and_passes_hex_via_stdin(tmp_path):
    # 1. A shadowed `security` on PATH (or SECURITY_BIN env var) must never be executed.
    rogue_dir = tmp_path / "rogue"
    rogue_dir.mkdir()
    rogue = rogue_dir / "security"
    rogue_marker = tmp_path / "rogue-ran"
    rogue.write_text(f"#!/bin/sh\necho PWNED > {shlex.quote(str(rogue_marker))}\nexit 0\n", encoding="utf-8")
    rogue.chmod(0o755)
    res = subprocess.run(
        ["/bin/bash", str(SETUP_KEYCHAIN_SH), "OPENAI_API_KEY"],
        input="dummy-secret\n",
        text=True,
        capture_output=True,
        timeout=10,
        env={"PATH": str(rogue_dir), "SECURITY_BIN": str(rogue), "USER": "alice", "OSTYPE": "darwin"},
    )
    assert res.returncode != 0
    assert not rogue_marker.exists()

    # 2. With LAST30DAYS_TEST_SECURITY_BIN stubbed, secret with quotes/spaces/backslashes/$/!/UTF-8
    #    never appears in argv and decodes identically from `-X <hex>` on stdin.
    records = tmp_path / "security-records.jsonl"
    stub = tmp_path / "security-stub"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "stdin_data = sys.stdin.read() if argv == ['-i'] else ''\n"
        "with open(os.environ['RECORDS'], 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'argv': argv, 'stdin': stdin_data}) + '\\n')\n"
        "if argv and argv[0] == 'find-generic-password':\n"
        "    sys.exit(44)\n"
        "if argv == ['-i']:\n"
        "    sys.exit(0)\n"
        "if argv and argv[0] == 'delete-generic-password':\n"
        "    sys.exit(0)\n"
        "sys.exit(99)\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    tricky_secret = """sk-live-'single' "double" \\slash $var !bang café🔑"""
    res = subprocess.run(
        ["/bin/bash", str(SETUP_KEYCHAIN_SH), "OPENAI_API_KEY"],
        input=tricky_secret + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        env={
            "PATH": str(tmp_path),
            "LAST30DAYS_TEST_SECURITY_BIN": str(stub),
            "USER": "alice",
            "OSTYPE": "darwin",
            "RECORDS": str(records),
        },
    )
    assert res.returncode == 0, res.stderr
    assert "added=1 replaced=0 skipped=0" in res.stdout
    assert tricky_secret not in res.stdout + res.stderr

    calls = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    assert calls[0]["argv"] == ["find-generic-password", "-a", "alice", "-s", "last30days-OPENAI_API_KEY"]
    assert calls[1]["argv"] == ["-i"]
    assert tricky_secret not in json.dumps(calls[1]["argv"])

    tokens = shlex.split(calls[1]["stdin"].strip())
    assert tokens[:6] == [
        "add-generic-password",
        "-U",
        "-a",
        "alice",
        "-s",
        "last30days-OPENAI_API_KEY",
    ]
    assert tokens[6] == "-X"
    decoded = bytes.fromhex(tokens[7]).decode("utf-8")
    assert decoded == tricky_secret

    # 3. EOF without trailing newline still stores value; empty EOF skips cleanly under set -e.
    records.unlink()
    eof_val = subprocess.run(
        ["/bin/bash", str(SETUP_KEYCHAIN_SH), "OPENAI_API_KEY"],
        input="eof-no-newline-secret",
        text=True,
        capture_output=True,
        timeout=10,
        env={"PATH": str(tmp_path), "LAST30DAYS_TEST_SECURITY_BIN": str(stub), "USER": "alice", "OSTYPE": "darwin", "RECORDS": str(records)},
    )
    assert eof_val.returncode == 0, eof_val.stderr
    assert "added=1 replaced=0 skipped=0" in eof_val.stdout
    eof_calls = [json.loads(line) for line in records.read_text(encoding="utf-8").splitlines()]
    assert bytes.fromhex(shlex.split(eof_calls[1]["stdin"].strip())[7]).decode("utf-8") == "eof-no-newline-secret"

    records.unlink()
    eof_empty = subprocess.run(
        ["/bin/bash", str(SETUP_KEYCHAIN_SH), "OPENAI_API_KEY"],
        input="",
        text=True,
        capture_output=True,
        timeout=10,
        env={"PATH": str(tmp_path), "LAST30DAYS_TEST_SECURITY_BIN": str(stub), "USER": "alice", "OSTYPE": "darwin", "RECORDS": str(records)},
    )
    assert eof_empty.returncode == 0, eof_empty.stderr
    assert "added=0 replaced=0 skipped=1" in eof_empty.stdout

    # 4. Unknown / injection keys are rejected with exit 2 before calling security.
    records.unlink()
    bad_res = subprocess.run(
        ["/bin/bash", str(SETUP_KEYCHAIN_SH), "--delete", "../evil"],
        text=True,
        capture_output=True,
        timeout=10,
        env={"PATH": str(tmp_path), "LAST30DAYS_TEST_SECURITY_BIN": str(stub), "USER": "alice", "OSTYPE": "darwin", "RECORDS": str(records)},
    )
    assert bad_res.returncode == 2
    assert not records.exists()


# ---------------------------------------------------------------------------
# HIGH-085: setup-pass.sh prefix validation, key allowlist & `--` markers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
def test_setup_pass_bash_syntax_and_bash32_safe_regexes():
    check = subprocess.run(["bash", "-n", str(SETUP_PASS_SH)], capture_output=True, text=True, timeout=10)
    assert check.returncode == 0, check.stderr
    for script_path in (SETUP_PASS_SH, SETUP_KEYCHAIN_SH):
        text = script_path.read_text(encoding="utf-8")
        # Ensure every `=~` uses a variable RHS (Bash 3.2 rejects inline parentheses).
        for line in text.splitlines():
            stripped = line.strip()
            if "=~" in stripped and not stripped.startswith("#"):
                assert "=~ $" in stripped, f"inline regex in {script_path.name}: {line}"


@pytest.fixture
def mock_pass_env(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls_file = tmp_path / "pass-calls.jsonl"
    pass_bin = bin_dir / "pass"
    pass_bin.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['PASS_CALLS'], 'a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps(args) + '\\n')\n"
        "if args and args[0] == 'insert':\n"
        "    target = pathlib.Path(os.environ['PASSWORD_STORE_DIR']) / (args[-1] + '.gpg')\n"
        "    target.parent.mkdir(parents=True, exist_ok=True)\n"
        "    target.write_text(sys.stdin.read(), encoding='utf-8')\n"
        "    sys.exit(0)\n"
        "if args and args[0] == 'show':\n"
        "    target = pathlib.Path(os.environ['PASSWORD_STORE_DIR']) / (args[-1] + '.gpg')\n"
        "    sys.exit(0 if target.exists() else 1)\n"
        "if args and args[0] == 'rm':\n"
        "    target = pathlib.Path(os.environ['PASSWORD_STORE_DIR']) / (args[-1] + '.gpg')\n"
        "    if target.exists():\n"
        "        target.unlink()\n"
        "        sys.exit(0)\n"
        "    sys.exit(1)\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    pass_bin.chmod(0o755)
    base_env = {
        "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
        "PASSWORD_STORE_DIR": str(store),
        "PASS_CALLS": str(calls_file),
    }
    return store, calls_file, base_env


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
@pytest.mark.parametrize(
    "bad_prefix",
    [
        "-r",
        "-c/",
        "/",
        "/etc/pass/",
        "..",
        "../",
        "last30days/../other/",
        ".",
        "./",
        "last30days/./other/",
        ".git/",
        ".gpg-id/",
        "last30days//sub/",
        "last30days/-r/",
        "last 30 days/",
        "last30days\n-r/",
        "last30days/\n",
        "   ",
    ],
)
def test_setup_pass_rejects_unsafe_prefix(mock_pass_env, bad_prefix):
    _store, calls_file, base_env = mock_pass_env
    env_vars = {**base_env, "LAST30DAYS_PASS_PREFIX": bad_prefix}

    for args in (["OPENAI_API_KEY"], ["--list"], ["--delete", "OPENAI_API_KEY"]):
        res = subprocess.run(
            ["bash", str(SETUP_PASS_SH), *args],
            input="secret\n",
            text=True,
            capture_output=True,
            timeout=10,
            env=env_vars,
        )
        assert res.returncode == 2, f"expected exit 2 for prefix={bad_prefix!r} args={args}"
    assert not calls_file.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
@pytest.mark.parametrize(
    "bad_key",
    [
        "../other",
        "sub/OPENAI_API_KEY",
        "OPENAI_API_KEY.gpg",
        "NOT_A_VALID_KEY",
        "-r",
        "foo bar",
        "OPENAI_API_KEY\n",
    ],
)
def test_setup_pass_rejects_unlisted_or_traversal_keys(mock_pass_env, bad_key):
    _store, calls_file, base_env = mock_pass_env
    for args in (["--", bad_key], ["--delete", "--", bad_key]):
        res = subprocess.run(
            ["bash", str(SETUP_PASS_SH), *args],
            input="secret\n",
            text=True,
            capture_output=True,
            timeout=10,
            env=base_env,
        )
        assert res.returncode == 2, f"expected exit 2 for key={bad_key!r} args={args}"
    assert not calls_file.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell script")
def test_setup_pass_uses_end_of_options_and_normalizes_prefix(mock_pass_env):
    store, calls_file, base_env = mock_pass_env
    env_vars = {**base_env, "LAST30DAYS_PASS_PREFIX": "secrets/l30"}

    # Insert (normalizes prefix to secrets/l30/)
    ins = subprocess.run(
        ["bash", str(SETUP_PASS_SH), "OPENAI_API_KEY"],
        input="sk-dummy-1\n",
        text=True,
        capture_output=True,
        timeout=10,
        env=env_vars,
    )
    assert ins.returncode == 0, ins.stderr
    assert (store / "secrets" / "l30" / "OPENAI_API_KEY.gpg").read_text(encoding="utf-8") == "sk-dummy-1\n"

    # List
    lst = subprocess.run(
        ["bash", str(SETUP_PASS_SH), "--list"],
        text=True,
        capture_output=True,
        timeout=10,
        env=env_vars,
    )
    assert lst.returncode == 0, lst.stderr
    assert "OPENAI_API_KEY" in lst.stdout

    # Delete
    rm = subprocess.run(
        ["bash", str(SETUP_PASS_SH), "--delete", "OPENAI_API_KEY"],
        text=True,
        capture_output=True,
        timeout=10,
        env=env_vars,
    )
    assert rm.returncode == 0, rm.stderr
    assert "deleted: OPENAI_API_KEY" in rm.stdout

    calls = [json.loads(line) for line in calls_file.read_text(encoding="utf-8").splitlines()]
    assert ["insert", "-m", "--", "secrets/l30/OPENAI_API_KEY"] in calls
    assert ["show", "--", "secrets/l30/OPENAI_API_KEY"] in calls
    assert ["rm", "-f", "--", "secrets/l30/OPENAI_API_KEY"] in calls

    # EOF without trailing newline stores value cleanly; empty EOF skips without crashing.
    eof_ins = subprocess.run(
        ["bash", str(SETUP_PASS_SH), "XAI_API_KEY"],
        input="xai-no-newline",
        text=True,
        capture_output=True,
        timeout=10,
        env=env_vars,
    )
    assert eof_ins.returncode == 0, eof_ins.stderr
    assert (store / "secrets" / "l30" / "XAI_API_KEY.gpg").read_text(encoding="utf-8") == "xai-no-newline\n"

    eof_empty = subprocess.run(
        ["bash", str(SETUP_PASS_SH), "BRAVE_API_KEY"],
        input="",
        text=True,
        capture_output=True,
        timeout=10,
        env=env_vars,
    )
    assert eof_empty.returncode == 0, eof_empty.stderr
    assert "added=0 replaced=0 skipped=1" in eof_empty.stdout
