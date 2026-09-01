"""Live Chrome cookie reader over the DevTools Protocol (CDP).

An EXTRA-host cookie lookup for the bird backend: when a Chrome/Chromium
instance is running with a remote-debugging endpoint and the user is signed
into x.com in it, that live session holds the ``auth_token`` + ``ct0`` cookies
bird needs — even on Linux, where the on-disk cookie store cannot be decrypted
here. This module talks to that endpoint and pulls the pair via
``Network.getAllCookies``.

Deliberate constraints (see docs/plans/2026-08-31 X plan):

* **Extras only.** The engine only calls this on extra hosts (Linux, Mac mini,
  Darwin agentcookie sink, or ``AGENTCOOKIE=on``); the gating lives in
  ``env.x_extras_enabled``. On a plain MacBook this is never called, so no
  socket is opened (AE8).
* **No port scan.** Endpoint resolution is: ``BROWSER_CDP_URL`` if set, else
  port ``18800`` (the box-Chrome default) if it answers as Chrome, else
  ``9222`` + the X display number. No 9222..9232 sweep.
* **Require a Chrome page target.** ``/json/version`` must report a Chrome /
  Chromium browser (a Node inspector is rejected) and ``/json`` must expose a
  ``page`` target.
* **``FROM_BROWSER=off`` skips CDP.**
* Stdlib only: a tiny RFC 6455 websocket client, no third-party dependency.
* Cookie **values are never logged** — only counts and endpoints.
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
# The box-Chrome remote-debugging port used by the extra-host launcher.
_BOX_CHROME_PORT = 18800

_HTTP_TIMEOUT = 1.5      # /json and /json/version fetches
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


def _normalize_base(url: str) -> str:
    """Return an ``http://host:port`` base for a user-supplied endpoint."""
    url = url.strip().rstrip("/")
    if url.startswith(("http://", "https://", "ws://", "wss://")):
        return url
    return f"http://{url}"


def candidate_endpoints(config: Optional[Dict[str, Any]] = None) -> List[str]:
    """Debug endpoints to try, most-specific first (no port scan).

    Order: an explicit ``BROWSER_CDP_URL`` (used exclusively when set), else the
    box-Chrome port ``18800``, then ``9222`` + the X display number.
    """
    explicit = ""
    if config is not None:
        explicit = (config.get("BROWSER_CDP_URL") or "").strip()
    explicit = explicit or (os.environ.get("BROWSER_CDP_URL") or "").strip()
    if explicit:
        return [_normalize_base(explicit)]

    endpoints = [f"http://127.0.0.1:{_BOX_CHROME_PORT}"]
    display = _display_number()
    endpoints.append(f"http://127.0.0.1:{_BASE_DEBUG_PORT + (display or 0)}")
    return endpoints


def _http_get_json(url: str) -> Optional[Any]:
    """GET ``url`` and parse JSON, or None (unreachable/non-JSON)."""
    try:
        with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT) as resp:
            body = resp.read()
    except (OSError, ValueError):
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_chrome_endpoint(base: str) -> bool:
    """True when ``base``/json/version reports a Chrome/Chromium browser.

    Rejects a Node ``--inspect`` endpoint (whose ``Browser`` is ``node.js/...``)
    so we never mistake an inspector for a browser.
    """
    version = _http_get_json(f"{base}/json/version")
    if not isinstance(version, dict):
        return False
    browser = str(version.get("Browser") or "").lower()
    return "chrome" in browser or "chromium" in browser


def _page_ws_url(base: str) -> Optional[str]:
    """Find a Chrome PAGE target's webSocketDebuggerUrl on ``base``."""
    targets = _http_get_json(f"{base}/json")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") != "page":
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
        match = re.match(r"wss?://([^:/]+):(\d+)(/.*)$", ws_url)
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
    """Return the complete X cookie pair from a live Chrome session, or None.

    Resolves the debug endpoint (BROWSER_CDP_URL, else 18800 if Chrome, else
    9222+$DISPLAY), requires a Chrome page target, and calls
    ``Network.getAllCookies``. Returns ``{"auth_token", "ct0"}`` only when BOTH
    cookies are found (no half-pair). ``FROM_BROWSER=off`` returns None without
    opening a socket. Any failure returns None so the caller falls through.
    Never raises.

    Host gating (extras-only) lives in the caller (``env.x_extras_enabled``);
    on a plain MacBook this function is never invoked, so no socket is opened.
    """
    from_browser = ""
    if config is not None:
        from_browser = (config.get("FROM_BROWSER") or "").strip().lower()
    if from_browser == "off":
        return None

    for base in candidate_endpoints(config):
        # ws:// endpoints (rare, explicit) connect directly; http(s) bases are
        # validated as Chrome and asked for a page target.
        if base.startswith(("ws://", "wss://")):
            ws_url = base
        else:
            if not _is_chrome_endpoint(base):
                continue
            ws_url = _page_ws_url(base)
            if not ws_url:
                continue
        cookies = _get_all_cookies(ws_url)
        if not cookies:
            continue
        found = _pair_from_cookies(cookies)
        if all(name in found for name in X_COOKIE_NAMES):
            _log(f"read a complete X cookie pair from a live Chrome session at {base}")
            return {name: found[name] for name in X_COOKIE_NAMES}
        if found:
            _log(
                f"live Chrome at {base} had an incomplete pair "
                f"({sorted(found)}); ignoring per no-half-pair rule"
            )
    return None
