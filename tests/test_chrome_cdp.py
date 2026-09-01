"""U6/U5: live Chrome cookie reader over CDP (lib/chrome_cdp.py).

Unit tests for port derivation and cookie filtering, plus an end-to-end test
against a FAKE CDP server (stdlib socket) that exercises the real HTTP target
lookup and the hand-rolled RFC 6455 websocket client. Only obvious dummy
cookie values are used (test-auth-token / test-ct0).
"""

import base64
import hashlib
import json
import socket
import struct
import threading
from unittest import mock

from lib import chrome_cdp

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# --- Unit: endpoint derivation + cookie filtering --------------------------


def test_display_number_parsing():
    with mock.patch.dict("os.environ", {"DISPLAY": ":99"}, clear=False):
        assert chrome_cdp._display_number() == 99
    with mock.patch.dict("os.environ", {"DISPLAY": "localhost:10.0"}, clear=False):
        assert chrome_cdp._display_number() == 10


def test_candidate_endpoints_default_order_18800_then_display():
    with mock.patch.dict("os.environ", {"DISPLAY": ":7"}, clear=False):
        # No BROWSER_CDP_URL in env for this check.
        with mock.patch.dict("os.environ", {"BROWSER_CDP_URL": ""}, clear=False):
            endpoints = chrome_cdp.candidate_endpoints({})
    assert endpoints == ["http://127.0.0.1:18800", "http://127.0.0.1:9229"]


def test_candidate_endpoints_prefers_browser_cdp_url_exclusively():
    endpoints = chrome_cdp.candidate_endpoints({"BROWSER_CDP_URL": "http://127.0.0.1:5555"})
    assert endpoints == ["http://127.0.0.1:5555"]


def test_pair_from_cookies_requires_both_and_filters_domain():
    cookies = [
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
        {"name": "auth_token", "value": "someone-else", "domain": ".example.com"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert pair == {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def test_pair_from_cookies_half_pair_is_incomplete():
    cookies = [{"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"}]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert "ct0" not in pair


def test_pair_from_cookies_never_mixes_hosts():
    """A lookalike host (notx.com) must not contribute; the later same-host
    x.com pair wins, never a cross-host mix (P1)."""
    cookies = [
        {"name": "auth_token", "value": "notx-token", "domain": "notx.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert pair == {"auth_token": "test-auth-token", "ct0": "test-ct0"}
    assert pair["auth_token"] != "notx-token"


def test_pair_from_cookies_prefers_x_com_over_twitter():
    cookies = [
        {"name": "auth_token", "value": "tw-token", "domain": ".twitter.com"},
        {"name": "ct0", "value": "tw-ct0", "domain": ".twitter.com"},
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert pair == {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def test_pair_from_cookies_skips_partial_host_for_complete_one():
    """x.com has only auth_token; twitter.com has both -> the twitter pair wins
    (same-host), never x.com's auth_token merged with twitter's ct0."""
    cookies = [
        {"name": "auth_token", "value": "x-only-token", "domain": ".x.com"},
        {"name": "auth_token", "value": "test-auth-token", "domain": ".twitter.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".twitter.com"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert pair == {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def test_pair_from_cookies_does_not_mix_across_paths():
    """auth_token on path / and ct0 on path /i are different scopes -> no pair."""
    cookies = [
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com", "path": "/i"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert set(pair) != {"auth_token", "ct0"}  # never a cross-path pair


def test_pair_from_cookies_does_not_mix_across_partitions():
    """Same host+path but different partitionKey -> different scopes -> no pair."""
    cookies = [
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com",
         "path": "/", "partitionKey": "https://a.example"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com",
         "path": "/", "partitionKey": "https://b.example"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert set(pair) != {"auth_token", "ct0"}


def test_pair_from_cookies_prefers_unpartitioned_root_path_scope():
    """A complete unpartitioned path-/ pair wins over later partitioned or
    other-path complete pairs (object-form partitionKey must not crash)."""
    cookies = [
        # other-path complete pair
        {"name": "auth_token", "value": "ipath-token", "domain": ".x.com", "path": "/i"},
        {"name": "ct0", "value": "ipath-ct0", "domain": ".x.com", "path": "/i"},
        # partitioned complete pair (object form)
        {"name": "auth_token", "value": "part-token", "domain": ".x.com", "path": "/",
         "partitionKey": {"topLevelSite": "https://x.com", "hasCrossSiteAncestor": True}},
        {"name": "ct0", "value": "part-ct0", "domain": ".x.com", "path": "/",
         "partitionKey": {"topLevelSite": "https://x.com", "hasCrossSiteAncestor": True}},
        # the winner: unpartitioned, path /
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com", "path": "/"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com", "path": "/"},
    ]
    pair = chrome_cdp._pair_from_cookies(cookies)
    assert pair == {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def test_from_browser_off_skips_endpoints():
    with mock.patch.object(
        chrome_cdp, "candidate_endpoints", side_effect=AssertionError("must not probe")
    ):
        assert chrome_cdp.read_x_cookies({"FROM_BROWSER": "off"}) is None


# --- Fake CDP server (stdlib socket) ---------------------------------------


class _FakeCDPServer:
    """Minimal Chrome-debug endpoint: GET /json/version + /json + a CDP websocket."""

    def __init__(self, cookies, browser="Chrome/120.0.0.0"):
        self._cookies = cookies
        self._browser = browser
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(5)
        self.port = self._sock.getsockname()[1]
        self._stop = False
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            try:
                self._handle(conn)
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _read_request(self, conn):
        buf = b""
        conn.settimeout(5)
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return None, b""
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return head.decode("latin-1"), rest

    def _handle(self, conn):
        head, rest = self._read_request(conn)
        if head is None:
            return  # bare reachability probe (connect then close)
        if "upgrade: websocket" in head.lower():
            self._handle_ws(conn, head, rest)
            return
        request_line = head.splitlines()[0]
        if request_line.startswith("GET /json/version"):
            body = json.dumps({"Browser": self._browser, "Protocol-Version": "1.3"}).encode()
            self._send_http_json(conn, body)
        elif request_line.startswith("GET /json"):
            body = json.dumps([
                {
                    "type": "page",
                    "webSocketDebuggerUrl": f"ws://127.0.0.1:{self.port}/devtools/page/ABC",
                }
            ]).encode()
            self._send_http_json(conn, body)

    def _send_http_json(self, conn, body):
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n" + body
        )

    def _handle_ws(self, conn, head, rest):
        key = ""
        for line in head.splitlines():
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()
        ).decode()
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Accept: "
            + accept.encode() + b"\r\n\r\n"
        )
        buf = bytearray(rest)
        # Respond to each client command until getAllCookies has been answered.
        for _ in range(4):
            msg, buf = self._read_frame(conn, buf)
            if msg is None:
                return
            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue
            mid = data.get("id")
            if data.get("method") == "Network.getAllCookies":
                self._send_frame(conn, json.dumps(
                    {"id": mid, "result": {"cookies": self._cookies}}
                ))
                return
            self._send_frame(conn, json.dumps({"id": mid, "result": {}}))

    def _read_frame(self, conn, buf):
        def need(n):
            nonlocal buf
            while len(buf) < n:
                chunk = conn.recv(4096)
                if not chunk:
                    return False
                buf += chunk
            return True

        if not need(2):
            return None, buf
        length = buf[1] & 0x7F
        masked = buf[1] & 0x80
        idx = 2
        if length == 126:
            if not need(4):
                return None, buf
            length = struct.unpack(">H", buf[2:4])[0]
            idx = 4
        elif length == 127:
            if not need(10):
                return None, buf
            length = struct.unpack(">Q", buf[2:10])[0]
            idx = 10
        mask = b""
        if masked:
            if not need(idx + 4):
                return None, buf
            mask = bytes(buf[idx:idx + 4])
            idx += 4
        if not need(idx + length):
            return None, buf
        payload = bytes(buf[idx:idx + length])
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        del buf[:idx + length]
        return payload.decode("utf-8"), buf

    def _send_frame(self, conn, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])  # FIN + text, unmasked (server)
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        conn.sendall(bytes(header) + payload)


def test_read_x_cookies_via_fake_cdp():
    cookies = [
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
        {"name": "guest_id", "value": "irrelevant", "domain": ".x.com"},
    ]
    with _FakeCDPServer(cookies) as server:
        config = {"BROWSER_CDP_URL": f"http://127.0.0.1:{server.port}"}
        result = chrome_cdp.read_x_cookies(config)
    assert result == {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def test_read_x_cookies_incomplete_pair_returns_none():
    cookies = [{"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"}]
    with _FakeCDPServer(cookies) as server:
        config = {"BROWSER_CDP_URL": f"http://127.0.0.1:{server.port}"}
        result = chrome_cdp.read_x_cookies(config)
    assert result is None


def test_read_x_cookies_rejects_node_inspector():
    """A Node --inspect endpoint (Browser=node.js/...) is not Chrome -> None."""
    cookies = [
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
    ]
    with _FakeCDPServer(cookies, browser="node.js/v20.0.0") as server:
        config = {"BROWSER_CDP_URL": f"http://127.0.0.1:{server.port}"}
        result = chrome_cdp.read_x_cookies(config)
    assert result is None


def test_read_x_cookies_from_browser_off_opens_no_socket():
    with mock.patch("socket.create_connection", side_effect=AssertionError("no socket")):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no http")):
            assert chrome_cdp.read_x_cookies({"FROM_BROWSER": "off"}) is None


def test_read_x_cookies_rejects_wss_without_plaintext_connect():
    """wss:// (TLS) is refused; the plaintext client must not connect (P2)."""
    with (
        mock.patch("socket.create_connection", side_effect=AssertionError("no plaintext connect to TLS endpoint")),
        mock.patch("urllib.request.urlopen", side_effect=AssertionError("no http probe of TLS endpoint")),
    ):
        assert chrome_cdp.read_x_cookies({"BROWSER_CDP_URL": "wss://127.0.0.1:9222/devtools/page/ABC"}) is None


def test_read_x_cookies_rejects_https_base_without_connect():
    """An https:// debug base (would yield wss) is refused without probing."""
    with (
        mock.patch("socket.create_connection", side_effect=AssertionError("no connect")),
        mock.patch("urllib.request.urlopen", side_effect=AssertionError("no TLS http probe")),
    ):
        assert chrome_cdp.read_x_cookies({"BROWSER_CDP_URL": "https://127.0.0.1:18800"}) is None


def test_wsconn_connect_refuses_wss():
    assert chrome_cdp._WSConn.connect("wss://127.0.0.1:9222/devtools/page/ABC", 1.0) is None


def test_read_x_cookies_no_reachable_endpoint_returns_none():
    # A port with nothing listening: connection refused, returns None.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    config = {"BROWSER_CDP_URL": f"http://127.0.0.1:{dead_port}"}
    assert chrome_cdp.read_x_cookies(config) is None
