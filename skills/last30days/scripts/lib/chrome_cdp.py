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
* **Explicit consent required.** ``BROWSER_CONSENT=true`` or an explicit
  Chromium/``auto`` selection permits CDP. ``FROM_BROWSER=off`` or a recorded
  refusal blocks it, including when an endpoint is configured.
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
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import log

X_COOKIE_NAMES = ("auth_token", "ct0")
_BASE_DEBUG_PORT = 9222
# The last30days extras NUX convention port: the agent launches the throwaway
# login Chrome with SAND_CHROME_REMOTE_DEBUG_PORT=18800 so this lookup finds it.
# NOT box-chrome's built-in default (which is 9222 + the X display number).
_BOX_CHROME_PORT = 18800

_HTTP_TIMEOUT = 1.5      # /json and /json/version fetches
_WS_TIMEOUT = 3.0        # websocket exchange
_MAX_HTTP_JSON_BYTES = 4 * 1024 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_WS_TARGET_RE = re.compile(r"/[A-Za-z0-9._~:/?\[\]@!$&'()*+,;=%-]+")
_STDLIB_URLOPEN = urllib.request.urlopen


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse HTTP 3xx redirects on loopback CDP requests."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, f"CDP redirects are refused: {msg}", headers, fp
        )


def _build_cdp_opener() -> urllib.request.OpenerDirector:
    """Build an explicit HTTP-only, no-proxy, no-redirect opener for CDP."""
    opener = urllib.request.OpenerDirector()
    opener.add_handler(urllib.request.ProxyHandler({}))
    opener.add_handler(urllib.request.HTTPHandler())
    opener.add_handler(_NoRedirectHandler())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    return opener


_CDP_OPENER = _build_cdp_opener()


def _format_netloc(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _parse_cdp_url(
    raw: str,
    *,
    allowed_schemes: Tuple[str, ...],
    allow_implicit_http: bool = False,
    require_base_path: bool = False,
) -> Optional[Tuple[str, str, int, str]]:
    """Strictly validate a loopback CDP URL and return ``(scheme, host, port, target)``."""
    if not isinstance(raw, str):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw) or not raw.isascii():
        return None
    candidate = raw.strip(" ") if allow_implicit_http else raw
    if not candidate or any(ch.isspace() for ch in candidate):
        return None
    if allow_implicit_http and "://" not in candidate:
        candidate = f"http://{candidate}"
    try:
        parts = urllib.parse.urlsplit(candidate)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if parts.fragment or "#" in candidate:
        return None
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or not (1 <= port <= 65535):
        return None

    target = parts.path + (f"?{parts.query}" if parts.query else "")
    if scheme == "ws":
        if not _WS_TARGET_RE.fullmatch(target):
            return None
        return (scheme, host, port, target)

    if require_base_path:
        if parts.query or parts.path not in ("", "/"):
            return None
        return (scheme, host, port, "")

    if target and target != "/" and not _WS_TARGET_RE.fullmatch(target):
        return None
    return (scheme, host, port, target or "/")


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


def _normalize_base(url: str) -> Optional[str]:
    """Return a validated canonical loopback ``http://host:port`` or ``ws://...`` endpoint."""
    parsed = _parse_cdp_url(
        url,
        allowed_schemes=("http", "ws"),
        allow_implicit_http=True,
        require_base_path=True,
    )
    if parsed is None:
        return None
    scheme, host, port, target = parsed
    netloc = _format_netloc(host, port)
    if scheme == "ws":
        return f"ws://{netloc}{target}"
    return f"http://{netloc}"


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
        raw_cfg = config.get("BROWSER_CDP_URL")
        if raw_cfg is not None:
            explicit = str(raw_cfg).strip(" ")
    if not explicit:
        explicit = (os.environ.get("BROWSER_CDP_URL") or "").strip(" ")
    if explicit:
        normalized = _normalize_base(explicit)
        return [normalized] if normalized is not None else []

    endpoints = [f"http://127.0.0.1:{_BOX_CHROME_PORT}"]
    display = _display_number()
    display_port = _BASE_DEBUG_PORT + (display or 0)
    if 1 <= display_port <= 65535:
        endpoints.append(f"http://127.0.0.1:{display_port}")
    return endpoints


def _http_get_json(url: str) -> Optional[Any]:
    """GET loopback CDP ``url`` (no proxy, no redirects) and parse JSON, or None."""
    parsed = _parse_cdp_url(url, allowed_schemes=("http",), allow_implicit_http=False)
    if parsed is None:
        return None
    scheme, host, port, target = parsed
    safe_url = f"{scheme}://{_format_netloc(host, port)}{target}"
    try:
        # Preserve test monkeypatches of urllib.request.urlopen while using the
        # hardened HTTP-only, proxy-free, no-redirect opener in production.
        if urllib.request.urlopen is not _STDLIB_URLOPEN:
            with urllib.request.urlopen(safe_url, timeout=_HTTP_TIMEOUT) as resp:
                body = resp.read(_MAX_HTTP_JSON_BYTES)
        else:
            with _CDP_OPENER.open(safe_url, timeout=_HTTP_TIMEOUT) as resp:
                body = resp.read(_MAX_HTTP_JSON_BYTES)
    except (OSError, ValueError, OverflowError, UnicodeEncodeError):
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
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
    base_parsed = _parse_cdp_url(
        base, allowed_schemes=("http",), allow_implicit_http=False, require_base_path=True
    )
    if base_parsed is None:
        return None
    _, base_host, base_port, _ = base_parsed
    targets = _http_get_json(f"{base}/json")
    if not isinstance(targets, list):
        return None
    for target in targets:
        if not isinstance(target, dict):
            continue
        if target.get("type") != "page":
            continue
        ws_url = target.get("webSocketDebuggerUrl")
        if not isinstance(ws_url, str):
            continue
        ws_parsed = _parse_cdp_url(ws_url, allowed_schemes=("ws",), allow_implicit_http=False)
        if ws_parsed is None:
            continue
        _, ws_host, ws_port, ws_target = ws_parsed
        if ws_host != base_host or ws_port != base_port:
            _log(
                f"refusing cross-endpoint webSocketDebuggerUrl host/port "
                f"({ws_host}:{ws_port} != {base_host}:{base_port})"
            )
            continue
        return f"ws://{_format_netloc(ws_host, ws_port)}{ws_target}"
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
        # Plaintext loopback ws:// only. A wss:// URL would need real TLS;
        # refuse non-ws, non-loopback, malformed, or control-char URLs before
        # opening any socket.
        parsed = _parse_cdp_url(ws_url, allowed_schemes=("ws",), allow_implicit_http=False)
        if parsed is None:
            return None
        _, host, port, path = parsed
        host_header = _format_netloc(host, port)
        sock: Optional[socket.socket] = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.settimeout(timeout)
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host_header}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(handshake.encode("ascii"))
            conn = cls(sock)
            header = conn._read_http_headers()
            if header is None or b" 101 " not in header.split(b"\r\n", 1)[0]:
                sock.close()
                return None
            return conn
        except (OSError, ValueError, OverflowError, UnicodeEncodeError):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            return None

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


def cookie_access_allowed(config: Optional[Dict[str, Any]] = None) -> bool:
    from . import env

    config = config or {}
    if env.cookie_access_denied(config):
        return False
    from_browser = str(config.get("FROM_BROWSER") or "").strip().lower()
    consent = config.get("BROWSER_CONSENT")
    if consent is not None:
        return str(consent).strip().lower() in {"1", "true", "yes", "on"}
    requested = {browser.strip() for browser in from_browser.split(",")}
    return bool(requested & {"auto", "chrome", "brave", "edge", "vivaldi", "opera", "arc", "dia", "chromium"})


def read_x_cookies(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """Return the complete X cookie pair from a live Chrome session, or None.

    Resolves the debug endpoint (BROWSER_CDP_URL, else 18800 if Chrome, else
    9222+$DISPLAY), requires a Chrome page target, and calls
    ``Network.getAllCookies``. Returns ``{"auth_token", "ct0"}`` only when BOTH
    cookies are found (no half-pair). Without explicit consent, returns None
    before resolving endpoints or opening a socket. Any failure returns None
    so the caller falls through.
    Never raises.

    Host gating (extras-only) lives in the caller (``env.x_extras_enabled``);
    on a plain MacBook this function is never invoked, so no socket is opened.
    """
    if not cookie_access_allowed(config):
        return None

    for base in candidate_endpoints(config):
        # Plaintext loopback http:// and ws:// only. Defense in depth even if
        # candidate_endpoints is overridden: refuse TLS, remote hosts, userinfo,
        # fragments, invalid ports, or control characters before any network I/O.
        parsed = _parse_cdp_url(
            base,
            allowed_schemes=("http", "ws"),
            allow_implicit_http=False,
            require_base_path=True,
        )
        if parsed is None:
            _log(f"refusing unsupported or non-loopback CDP endpoint {base!r}")
            continue
        scheme, host, port, target = parsed
        netloc = _format_netloc(host, port)
        # ws:// endpoints (rare, explicit) connect directly; http bases are
        # validated as Chrome and asked for a page target.
        if scheme == "ws":
            ws_url = f"ws://{netloc}{target}"
        else:
            http_base = f"http://{netloc}"
            if not _is_chrome_endpoint(http_base):
                continue
            ws_url = _page_ws_url(http_base)
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
