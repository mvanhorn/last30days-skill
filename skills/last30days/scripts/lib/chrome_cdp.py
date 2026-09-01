"""Live Chrome cookie reader over the DevTools Protocol (CDP).

When a Chrome/Chromium instance is running with a remote-debugging port open
and the user is signed into x.com in it, that live session holds the
``auth_token`` + ``ct0`` cookies the bird backend needs — even on Linux, where
the on-disk cookie store cannot be decrypted by this engine. This module talks
to that debug port and pulls the pair via ``Network.getAllCookies``.

Deliberate constraints (see docs/plans/2026-08-31 X plan):

* **Port discovery is a scan, not a hardcode.** We derive the usual box-Chrome
  port (``9222`` + the X display number) and probe a small contiguous range;
  no specific port (e.g. agentcookie's) is special-cased.
* **``FROM_BROWSER=off`` skips CDP** (and the in-process browser extractor),
  matching the browser-read opt-out.
* Stdlib only: a tiny RFC 6455 websocket client, no third-party dependency.
* Cookie **values are never logged** — only counts and ports.
* First complete pair wins: both ``auth_token`` and ``ct0`` must be present.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import struct
import urllib.request
from typing import Any, Dict, List, Optional

from . import log

X_COOKIE_NAMES = ("auth_token", "ct0")
_BASE_DEBUG_PORT = 9222
# How far above the base port to scan. A modest window covers the common
# "9222 + display number" and manual `--remote-debugging-port` choices without
# hammering a wide range of localhost ports.
_PORT_SCAN_SPAN = 11

_CONNECT_TIMEOUT = 0.3   # TCP reachability probe per port
_HTTP_TIMEOUT = 1.5      # /json target list fetch
_WS_TIMEOUT = 3.0        # websocket exchange


def _log(msg: str) -> None:
    log.source_log("chrome-cdp", msg, tty_only=False)


def _display_number() -> Optional[int]:
    """Parse the X display number from ``$DISPLAY`` (e.g. ``:99`` -> 99)."""
    disp = os.environ.get("DISPLAY") or ""
    match = re.search(r":(\d+)", disp)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def candidate_ports() -> List[int]:
    """Debug ports to try, most-likely first.

    The usual box-Chrome port is ``9222`` plus the display number; we lead with
    that, then a contiguous range from ``9222`` for manually chosen ports.
    """
    ports: List[int] = []
    display = _display_number()
    if display is not None:
        ports.append(_BASE_DEBUG_PORT + display)
    for port in range(_BASE_DEBUG_PORT, _BASE_DEBUG_PORT + _PORT_SCAN_SPAN):
        if port not in ports:
            ports.append(port)
    return ports


def _port_reachable(port: int) -> bool:
    """Fast TCP-connect reachability probe so dead ports are skipped cheaply."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _http_get_json(port: int, path: str) -> Optional[Any]:
    """GET ``http://127.0.0.1:<port><path>`` and parse JSON, or None."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read()
    except (OSError, ValueError):
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _page_ws_url(port: int) -> Optional[str]:
    """Find a page target's webSocketDebuggerUrl on this debug port."""
    targets = _http_get_json(port, "/json")
    if not isinstance(targets, list):
        return None
    # Prefer a real page; fall back to any target that exposes a ws URL.
    for want_page in (True, False):
        for target in targets:
            if not isinstance(target, dict):
                continue
            if want_page and target.get("type") != "page":
                continue
            ws_url = target.get("webSocketDebuggerUrl")
            if isinstance(ws_url, str) and ws_url.startswith("ws://"):
                return ws_url
    return None


class _WSConn:
    """Minimal RFC 6455 websocket client (text frames only) over a TCP socket."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buf = b""

    def _fill(self, n: int) -> Optional[bytes]:
        while len(self._buf) < n:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    @classmethod
    def connect(cls, ws_url: str, timeout: float) -> Optional["_WSConn"]:
        match = re.match(r"ws://([^:/]+):(\d+)(/.*)$", ws_url)
        if not match:
            return None
        host, port, path = match.group(1), int(match.group(2)), match.group(3)
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError:
            return None
        sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        try:
            sock.sendall(handshake.encode("ascii"))
        except OSError:
            sock.close()
            return None
        conn = cls(sock)
        header = conn._read_http_headers()
        if header is None or b" 101 " not in header.split(b"\r\n", 1)[0]:
            sock.close()
            return None
        return conn

    def _read_http_headers(self) -> Optional[bytes]:
        while b"\r\n\r\n" not in self._buf:
            try:
                chunk = self._sock.recv(65536)
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        head, _, rest = self._buf.partition(b"\r\n\r\n")
        self._buf = rest  # any bytes after the header belong to the frame stream
        return head

    def send_text(self, payload: bytes) -> bool:
        header = bytearray([0x81])  # FIN + text opcode
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self._sock.sendall(bytes(header) + masked)
            return True
        except OSError:
            return False

    def recv_message(self) -> Optional[bytes]:
        """Read one (possibly fragmented) data message; skip control frames."""
        message = b""
        while True:
            first = self._fill(2)
            if first is None:
                return None
            fin = first[0] & 0x80
            opcode = first[0] & 0x0F
            length = first[1] & 0x7F
            masked = first[1] & 0x80
            if length == 126:
                ext = self._fill(2)
                if ext is None:
                    return None
                length = struct.unpack(">H", ext)[0]
            elif length == 127:
                ext = self._fill(8)
                if ext is None:
                    return None
                length = struct.unpack(">Q", ext)[0]
            mask = self._fill(4) if masked else b""
            payload = self._fill(length) if length else b""
            if length and payload is None:
                return None
            if masked and payload:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:  # close
                return None
            if opcode in (0x9, 0xA):  # ping / pong — ignore
                continue
            message += payload or b""
            if fin:
                return message

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _get_all_cookies(ws_url: str) -> Optional[List[Dict[str, Any]]]:
    """Run Network.enable then Network.getAllCookies over one CDP websocket."""
    conn = _WSConn.connect(ws_url, _WS_TIMEOUT)
    if conn is None:
        return None
    try:
        if not conn.send_text(json.dumps({"id": 1, "method": "Network.enable"}).encode("utf-8")):
            return None
        if not conn.send_text(json.dumps({"id": 2, "method": "Network.getAllCookies"}).encode("utf-8")):
            return None
        # Read frames until the id=2 response arrives (skipping enable's ack and
        # any Network.* events the browser pushes after enable).
        for _ in range(200):
            raw = conn.recv_message()
            if raw is None:
                return None
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(msg, dict) and msg.get("id") == 2:
                result = msg.get("result")
                if isinstance(result, dict) and isinstance(result.get("cookies"), list):
                    return result["cookies"]
                return None
        return None
    finally:
        conn.close()


def _pair_from_cookies(cookies: List[Dict[str, Any]]) -> Dict[str, str]:
    """Extract the X cookie pair from a CDP cookie list (x.com domain only)."""
    found: Dict[str, str] = {}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if name in X_COOKIE_NAMES and isinstance(value, str) and value:
            if domain.endswith("x.com") or domain.endswith("twitter.com"):
                found.setdefault(name, value)
    return found


def read_x_cookies(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """Return the complete X cookie pair from a live Chrome debug session, or None.

    Scans reachable localhost debug ports, and for each opens a CDP websocket
    and calls ``Network.getAllCookies``. Returns ``{"auth_token", "ct0"}`` only
    when BOTH cookies are found (no half-pair). ``FROM_BROWSER=off`` skips the
    whole scan. Any failure returns None so the caller falls through. Never
    raises.
    """
    from_browser = ""
    if config is not None:
        from_browser = (config.get("FROM_BROWSER") or "").strip().lower()
    if from_browser == "off":
        return None

    for port in candidate_ports():
        if not _port_reachable(port):
            continue
        ws_url = _page_ws_url(port)
        if not ws_url:
            continue
        cookies = _get_all_cookies(ws_url)
        if not cookies:
            continue
        found = _pair_from_cookies(cookies)
        if all(name in found for name in X_COOKIE_NAMES):
            _log(f"read a complete X cookie pair from a live Chrome session on port {port}")
            return {name: found[name] for name in X_COOKIE_NAMES}
        if found:
            _log(
                f"live Chrome on port {port} had an incomplete pair "
                f"({sorted(found)}); ignoring per no-half-pair rule"
            )
    return None
