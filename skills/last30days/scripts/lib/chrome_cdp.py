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
  port ``18800`` if it answers as Chrome, else ``9222`` + the X display number.
  No 9222..9232 sweep. ``18800`` is NOT box-chrome's built-in default (that is
  ``9222`` + the display number); it is the last30days extras NUX convention —
  the agent launches the throwaway login Chrome with
  ``SAND_CHROME_REMOTE_DEBUG_PORT=18800`` (see SKILL.md), so a leftover daily
  profile on ``9222``+display is not mistaken for the login session. If ``18800``
  answers with no complete pair we fall through; if it answers with a stale or
  wrong pair, pin ``BROWSER_CDP_URL`` after the NUX rather than scanning.
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
# The last30days extras NUX convention port: the agent launches the throwaway
# login Chrome with SAND_CHROME_REMOTE_DEBUG_PORT=18800 so this lookup finds it.
# NOT box-chrome's built-in default (which is 9222 + the X display number).
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
    last30days extras NUX port ``18800`` (where the agent launches the throwaway
    login Chrome via ``SAND_CHROME_REMOTE_DEBUG_PORT=18800``), then ``9222`` +
    the X display number (box-chrome's own built-in default). ``18800`` is tried
    first but read_x_cookies falls through when it yields no complete pair, so a
    logged-out Chrome there never shadows a logged-in daily profile.
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
        # Plaintext ws:// only. A wss:// URL would need real TLS
        # (ssl.wrap_socket); this client does not, so refuse it rather than
        # open a plaintext socket to a TLS endpoint. Defense in depth alongside
        # the scheme gate in read_x_cookies.
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


# Registrable X hosts we accept cookies from, in preference order. Matched
# EXACTLY after stripping a single leading dot (never endswith), so a lookalike
# like ``notx.com`` is not treated as x.com and cannot contribute a cookie.
_ALLOWED_X_HOSTS = ("x.com", "twitter.com")


def _canonical_partition(raw: Any) -> Optional[str]:
    """Canonicalize a CDP ``partitionKey`` to a hashable scope tag.

    CDP may send ``partitionKey`` as a string, as an object
    (``{"topLevelSite": ..., "hasCrossSiteAncestor": ...}``), or omit it for an
    unpartitioned cookie. Returns None for "unpartitioned" (so all unpartitioned
    cookies share one scope) and a stable string otherwise (so a partitioned
    cookie never shares a scope with an unpartitioned one, nor with a different
    partition).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        try:
            return json.dumps(raw, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return repr(sorted((str(k), str(v)) for k, v in raw.items()))
    return str(raw)


def _pair_from_cookies(cookies: List[Dict[str, Any]]) -> Dict[str, str]:
    """Extract a complete X cookie pair from ONE cookie scope.

    ``auth_token`` and ``ct0`` are only a usable pair when they share the SAME
    cookie scope — same registrable host AND same ``path`` AND same partition.
    Chrome can hold duplicate names across scopes (different ``path`` or
    ``partitionKey``), so pairing across the whole host jar could hand Bird a
    token from one session scope and a ct0 from another, and a valid login would
    look unauthorized. We therefore group by the full scope key
    ``(host, path, partition)`` and only ever pair WITHIN one scope.

    Host is matched EXACTLY against ``_ALLOWED_X_HOSTS`` after stripping one
    leading dot (never ``endswith``, so ``notx.com`` never counts). Missing
    ``path`` is treated as ``/``; ``partitionKey`` is canonicalized so
    unpartitioned cookies stay together. Preference order for the returned pair:
    host ``x.com`` before ``twitter.com``; unpartitioned before partitioned;
    path ``/`` before other paths. A scope with only one of the two cookies is
    skipped so a later complete scope still wins. When no scope has a complete
    pair, a single scope's partial is returned for the caller's incomplete-pair
    log — never a cross-scope mix.
    """
    # scopes[host][(path, partition)] -> {name: value}
    scopes: Dict[str, Dict[tuple, Dict[str, str]]] = {host: {} for host in _ALLOWED_X_HOSTS}
    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if name not in X_COOKIE_NAMES or not (isinstance(value, str) and value):
            continue
        host = str(cookie.get("domain") or "").lstrip(".").lower()
        if host not in scopes:
            continue
        path = cookie.get("path")
        if not isinstance(path, str) or not path:
            path = "/"
        scope_key = (path, _canonical_partition(cookie.get("partitionKey")))
        jar = scopes[host].setdefault(scope_key, {})
        # First value WITHIN this scope only — never across scopes.
        jar.setdefault(name, value)

    def _scope_rank(item: tuple) -> tuple:
        (path, partition), _jar = item
        # unpartitioned (None) before partitioned; path "/" before others.
        return (partition is not None, path != "/", path)

    for host in _ALLOWED_X_HOSTS:
        for _key, jar in sorted(scopes[host].items(), key=_scope_rank):
            if all(name in jar for name in X_COOKIE_NAMES):
                return {name: jar[name] for name in X_COOKIE_NAMES}

    for host in _ALLOWED_X_HOSTS:
        for _key, jar in sorted(scopes[host].items(), key=_scope_rank):
            if jar:
                return dict(jar)
    return {}


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
        # TLS CDP is NOT supported: the websocket client speaks plaintext only,
        # so a wss:// endpoint (or an https:// base, which would yield a wss://
        # page URL) must fail closed rather than be downgraded to a plaintext
        # connect. Local Chrome CDP is ws://http://.
        scheme = base.split("://", 1)[0].lower() if "://" in base else "http"
        if scheme in ("wss", "https"):
            _log(f"refusing TLS CDP endpoint {base!r}: only ws://http:// is supported (no TLS)")
            continue
        # ws:// endpoints (rare, explicit) connect directly; http bases are
        # validated as Chrome and asked for a page target.
        if base.startswith("ws://"):
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
