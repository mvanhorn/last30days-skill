"""HTTP utilities for last30days skill (stdlib only)."""

import http.client
import io
import ipaddress
import json
from collections import OrderedDict
import math
import os
import random
import re
import socket
import string
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunparse, urlunsplit, quote

from . import bounded_get, health, proxy, usage
from . import log as _log

DEFAULT_TIMEOUT = 30
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024


_URL_USERINFO_RE = re.compile(r"(https?://)[^\s/?#]+@", re.IGNORECASE)


def log(msg: str):
    """Log debug message to stderr with any embedded URL userinfo scrubbed."""
    _log.debug(_URL_USERINFO_RE.sub(r"\1", msg))


MAX_RETRIES = 5
MAX_429_RETRIES = 2
RETRY_DELAY = 2.0


# Longest a 429 retry may sleep on any host. Reddit's x-ratelimit-reset can say
# 540s and GitHub's is an epoch timestamp; neither is worth parking a worker
# (or the main thread) for. Past this bound the retry is not worth taking, so
# the caller's own fallback backoff applies and the request fails fast.
MAX_RETRY_DELAY_SECONDS = 60.0
# A reset value this large is an absolute epoch timestamp, not delta-seconds.
_EPOCH_RESET_THRESHOLD = 100_000_000.0


def retry_delay_from_headers(headers, fallback):
    """Seconds to wait after a 429, read from whichever header the host sent.

    ``Retry-After`` is the standard, but Reddit's keyless search endpoints answer an
    anonymous 429 with ``x-ratelimit-reset`` (seconds until the window rolls) and
    no ``Retry-After`` at all::

        HTTP/2 429
        x-ratelimit-used: 1
        x-ratelimit-remaining: 0.0
        x-ratelimit-reset: 42

    Reading only ``Retry-After`` means the caller falls back to exponential
    backoff -- 3s, 5s, 9s -- every one of which is shorter than the ~42s Reddit
    actually requires. Each retry re-429s, the budget drains, and the source is
    reported dead when it was merely early. Honouring the reset header turns a
    guaranteed zero into a result at the cost of one wait.

    USPTO Patent Public Search names its wait in
    ``x-rate-limit-retry-after-seconds`` instead.

    Returns ``fallback`` when none of these headers is present or parseable.
    """
    if not headers:
        return fallback
    for name in (
        "Retry-After",
        "x-rate-limit-retry-after-seconds",
        "x-ratelimit-reset",
    ):
        raw = headers.get(name)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= _EPOCH_RESET_THRESHOLD:
            # GitHub-style absolute reset time.
            value = value - time.time()
        if value > 0:
            return min(value, MAX_RETRY_DELAY_SECONDS)
    return fallback

# DNS resolution failures (gaierror) are transient — typically resolved by a
# brief backoff and retry. Use a dedicated minimum attempt count + exponential
# delays (1s, 2s, 4s) so callers that pass a small `retries` value still get a
# meaningful chance to recover from a transient resolution failure.
MIN_DNS_RETRIES = 3
USER_AGENT = "last30days-skill/3.0 (Assistant Skill)"

# urllib copies almost all headers across 3xx; strip credentials when origin changes (#1062).
_CROSS_ORIGIN_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "x-access-token",
        "x-api-key",
        "x-csrf-token",
        "x-goog-api-key",
        "x-subscription-token",
    }
)


def _request_origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port is not None:
        port = parts.port
    elif scheme == "https":
        port = 443
    elif scheme == "http":
        port = 80
    else:
        port = 0
    return scheme, host, port


# A request may carry a check on where it is allowed to be redirected: a
# caller that fetches an address from remote content (a sitemap) refuses a
# redirect to a private network before it is followed, not after.
_REDIRECT_GUARD_ATTR = "_last30days_redirect_guard"
_PUBLIC_ONLY_ATTR = "_last30days_public_only"
_LOCAL_BRIDGE_ONLY_ATTR = "_last30days_local_bridge_only"
_IP_VALIDATOR_ATTR = "_last30days_ip_validator"
_ALLOWED_HTTP_SCHEMES = frozenset({"http", "https"})

_LOCAL_BRIDGE_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
})
_LOCAL_BRIDGE_LAN_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv6Network("fc00::/7"),
)
_LOCAL_BRIDGE_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_LOCAL_BRIDGE_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")


def _is_local_bridge_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True only for loopback, RFC1918 LAN, or non-IMDS IPv6 ULA addresses."""
    if (
        ip in _LOCAL_BRIDGE_BLOCKED_IPS
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
    ):
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_local_bridge_ip(ip.ipv4_mapped)
        if (
            ip.sixtofour is not None
            or ip.teredo is not None
            or ip in _LOCAL_BRIDGE_NAT64_WELL_KNOWN
            or ip in _LOCAL_BRIDGE_NAT64_LOCAL
        ):
            return False
    return ip.is_loopback or any(ip in net for net in _LOCAL_BRIDGE_LAN_NETWORKS)


def _resolve_local_bridge_addresses(host: str, port: int) -> list[tuple]:
    """Resolve ``host`` fresh and allow only verified local/LAN bridge IPs."""
    from . import public_host

    raw_host = (host or "").strip()
    if raw_host.startswith("[") and raw_host.endswith("]"):
        raw_host = raw_host[1:-1]
    dns_host = raw_host.lower()
    bare = dns_host.removesuffix(".")
    if not bare or bare.startswith(".") or bare.endswith(".") or ".." in bare:
        raise public_host.NonPublicAddressError("refused disallowed local-bridge address")
    literal = public_host._parse_ip_literal(bare)
    if literal is not None:
        if not _is_local_bridge_ip(literal):
            raise public_host.NonPublicAddressError("refused disallowed local-bridge address")
        family = socket.AF_INET6 if isinstance(literal, ipaddress.IPv6Address) else socket.AF_INET
        sockaddr = (str(literal), port, 0, 0) if family == socket.AF_INET6 else (str(literal), port)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
    # Preserve any single trailing dot for DNS lookup so search-domain expansion cannot redirect it.
    answers = socket.getaddrinfo(dns_host, port, 0, socket.SOCK_STREAM)
    if not answers:
        raise public_host.NonPublicAddressError("refused disallowed local-bridge address")
    verified: list[tuple] = []
    for family, socktype, proto, canonname, sockaddr in answers:
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError as exc:
            raise public_host.NonPublicAddressError("refused disallowed local-bridge address") from exc
        if not _is_local_bridge_ip(ip):
            raise public_host.NonPublicAddressError("refused disallowed local-bridge address")
        verified.append((family, socktype, proto, canonname, sockaddr))
    return verified


def _create_pinned_connection(
    address: tuple[str, int],
    resolver: Callable[[str, int], list[tuple]],
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: Any = None,
) -> socket.socket:
    """Resolve ``address`` fresh via ``resolver`` and connect only to verified IPs."""
    from . import public_host

    host, port = address
    verified = resolver(host, port)
    last_err: Optional[OSError] = None
    for af, socktype, proto, _canonname, sockaddr in verified:
        sock = None
        try:
            sock = socket.socket(af, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_err = exc
            if sock is not None:
                sock.close()
    if last_err is not None:
        raise last_err
    raise public_host.NonPublicAddressError("refused disallowed address")


def _check_http_scheme(url: str) -> str:
    """Return the normalized scheme for an http(s) URL, or raise ValueError."""
    if not isinstance(url, str) or not url or url != url.strip():
        raise ValueError("unsupported URL scheme: <empty>")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise ValueError("unsupported URL control characters")
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_HTTP_SCHEMES:
        raise ValueError(f"unsupported URL scheme: {scheme or '<empty>'}")
    return scheme


def _create_public_connection(
    address: tuple[str, int],
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: Any = None,
) -> socket.socket:
    """Resolve ``address`` fresh once and connect only to verified public IPs."""
    from . import public_host

    return _create_pinned_connection(
        address, public_host.resolve_public_addresses, timeout=timeout, source_address=source_address,
    )


def _create_local_bridge_connection(
    address: tuple[str, int],
    timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: Any = None,
) -> socket.socket:
    """Resolve ``address`` fresh once and connect only to verified local/LAN bridge IPs."""
    return _create_pinned_connection(
        address, _resolve_local_bridge_addresses, timeout=timeout, source_address=source_address,
    )


class _PublicHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection pinned to the exact public IP resolved at connect time."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_public_connection

    def connect(self):
        if self._tunnel_host:
            from . import public_host

            raise public_host.NonPublicAddressError("refused proxied tunnel for public-only request")
        return super().connect()


class _LocalBridgeHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection pinned to verified loopback/RFC1918/ULA bridge IPs at connect time."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_local_bridge_connection

    def connect(self):
        if self._tunnel_host:
            from . import public_host

            raise public_host.NonPublicAddressError("refused proxied tunnel for local-bridge-only request")
        return super().connect()


if hasattr(http.client, "HTTPSConnection"):

    class _PublicHTTPSConnection(http.client.HTTPSConnection):
        """HTTPSConnection pinned to verified public IPs while preserving TLS SNI/hostname."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = _create_public_connection

        def connect(self):
            if self._tunnel_host:
                from . import public_host

                raise public_host.NonPublicAddressError("refused proxied tunnel for public-only request")
            return super().connect()

    class _LocalBridgeHTTPSConnection(http.client.HTTPSConnection):
        """HTTPSConnection pinned to verified local/LAN bridge IPs while preserving TLS SNI."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._create_connection = _create_local_bridge_connection

        def connect(self):
            if self._tunnel_host:
                from . import public_host

                raise public_host.NonPublicAddressError("refused proxied tunnel for local-bridge-only request")
            return super().connect()


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PublicHTTPConnection, req)


class _LocalBridgeHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_LocalBridgeHTTPConnection, req)


if hasattr(http.client, "HTTPSConnection"):

    class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_PublicHTTPSConnection, req, context=self._context)

    class _LocalBridgeHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_LocalBridgeHTTPSConnection, req, context=self._context)


def _resolve_validated_addresses(
    host: str,
    port: int,
    ip_validator: Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool],
) -> list[tuple]:
    """Resolve ``host`` fresh once and accept only when every resolved IP passes ``ip_validator``."""
    from . import public_host

    raw_host = (host or "").strip()
    if raw_host.startswith("[") and raw_host.endswith("]"):
        raw_host = raw_host[1:-1]
    dns_host = raw_host.lower()
    bare = dns_host.removesuffix(".")
    if not bare or bare.startswith(".") or bare.endswith(".") or ".." in bare:
        raise public_host.NonPublicAddressError("refused disallowed address")
    literal = public_host._parse_ip_literal(bare)
    if literal is not None:
        if not ip_validator(literal):
            raise public_host.NonPublicAddressError("refused disallowed address")
        family = socket.AF_INET6 if isinstance(literal, ipaddress.IPv6Address) else socket.AF_INET
        sockaddr = (str(literal), port, 0, 0) if family == socket.AF_INET6 else (str(literal), port)
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]
    # Preserve any single trailing dot for DNS lookup so search-domain expansion cannot redirect it.
    answers = socket.getaddrinfo(dns_host, port, 0, socket.SOCK_STREAM)
    if not answers:
        raise public_host.NonPublicAddressError("refused disallowed address")
    verified: list[tuple] = []
    for family, socktype, proto, canonname, sockaddr in answers:
        try:
            ip = ipaddress.ip_address(sockaddr[0].split("%", 1)[0])
        except ValueError as exc:
            raise public_host.NonPublicAddressError("refused disallowed address") from exc
        if not ip_validator(ip):
            raise public_host.NonPublicAddressError("refused disallowed address")
        verified.append((family, socktype, proto, canonname, sockaddr))
    return verified


def _make_validated_connection(
    ip_validator: Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool],
):
    def _create_validated_connection(
        address: tuple[str, int],
        timeout: Any = socket._GLOBAL_DEFAULT_TIMEOUT,
        source_address: Any = None,
    ) -> socket.socket:
        return _create_pinned_connection(
            address,
            lambda host, port: _resolve_validated_addresses(host, port, ip_validator),
            timeout=timeout,
            source_address=source_address,
        )

    return _create_validated_connection


class _ValidatedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(
        self,
        ip_validator: Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool],
    ):
        super().__init__()
        create_conn = _make_validated_connection(ip_validator)

        class _ValidatedHTTPConnection(http.client.HTTPConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._create_connection = create_conn

            def connect(self):
                if self._tunnel_host:
                    from . import public_host

                    raise public_host.NonPublicAddressError(
                        "refused proxied tunnel for IP-validated request"
                    )
                return super().connect()

        self._conn_cls = _ValidatedHTTPConnection

    def http_open(self, req):
        return self.do_open(self._conn_cls, req)


if hasattr(http.client, "HTTPSConnection"):

    class _ValidatedHTTPSHandler(urllib.request.HTTPSHandler):
        def __init__(
            self,
            ip_validator: Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool],
        ):
            super().__init__()
            create_conn = _make_validated_connection(ip_validator)

            class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._create_connection = create_conn

                def connect(self):
                    if self._tunnel_host:
                        from . import public_host

                        raise public_host.NonPublicAddressError(
                            "refused proxied tunnel for IP-validated request"
                        )
                    return super().connect()

            self._conn_cls = _ValidatedHTTPSConnection

        def https_open(self, req):
            return self.do_open(self._conn_cls, req, context=self._context)


def _build_http_opener(
    *,
    bypass_proxy: bool = False,
    public_only: bool = False,
    local_bridge_only: bool = False,
    ip_validator: Optional[Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool]] = None,
) -> urllib.request.OpenerDirector:
    """Build an OpenerDirector restricted strictly to HTTP(S) — omitting urllib's
    default FileHandler, FTPHandler, and DataHandler."""
    opener = urllib.request.OpenerDirector()
    pinned = public_only or local_bridge_only or ip_validator is not None
    if ip_validator is not None:
        http_handler = _ValidatedHTTPHandler(ip_validator)
    elif public_only:
        http_handler = _PublicHTTPHandler()
    elif local_bridge_only:
        http_handler = _LocalBridgeHTTPHandler()
    else:
        http_handler = urllib.request.HTTPHandler()
    handlers = [
        urllib.request.ProxyHandler({}) if (bypass_proxy or pinned) else urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        http_handler,
        urllib.request.HTTPDefaultErrorHandler(),
        _StripAuthOnCrossOriginRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ]
    if hasattr(http.client, "HTTPSConnection"):
        if ip_validator is not None:
            handlers.append(_ValidatedHTTPSHandler(ip_validator))
        elif public_only:
            handlers.append(_PublicHTTPSHandler())
        elif local_bridge_only:
            handlers.append(_LocalBridgeHTTPSHandler())
        else:
            handlers.append(urllib.request.HTTPSHandler())
    for handler in handlers:
        opener.add_handler(handler)
    return opener


class _StripAuthOnCrossOriginRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        if "location" in headers:
            newurl = headers["location"]
        elif "uri" in headers:
            newurl = headers["uri"]
        else:
            return
        urlparts = urlparse(newurl)
        if urlparts.scheme not in ("http", "https", ""):
            raise urllib.error.HTTPError(
                req.full_url,
                403,
                f"redirect refused: unsupported URL scheme: {urlparts.scheme}",
                headers,
                fp,
            )
        if not urlparts.path and urlparts.netloc:
            urlparts = list(urlparts)
            urlparts[2] = "/"
        newurl = urlunparse(urlparts)
        newurl = quote(newurl, encoding="iso-8859-1", safe=string.punctuation)
        newurl = urljoin(req.full_url, newurl)
        new = self.redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return
        if hasattr(req, "redirect_dict"):
            visited = new.redirect_dict = req.redirect_dict
            if (
                visited.get(newurl, 0) >= self.max_repeats
                or len(visited) >= self.max_redirections
            ):
                raise urllib.error.HTTPError(
                    req.full_url, code, self.inf_msg + msg, headers, fp
                )
        else:
            visited = new.redirect_dict = req.redirect_dict = {}
        visited[newurl] = visited.get(newurl, 0) + 1
        # Close without calling unbounded fp.read() on untrusted 3xx bodies.
        if fp is not None:
            fp.close()
        return self.parent.open(new, timeout=req.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            _check_http_scheme(newurl)
        except ValueError as exc:
            raise urllib.error.HTTPError(
                req.full_url, 403, f"redirect refused: {exc}", headers, fp,
            ) from exc
        public_only = bool(getattr(req, _PUBLIC_ONLY_ATTR, False))
        local_bridge_only = bool(getattr(req, _LOCAL_BRIDGE_ONLY_ATTR, False))
        if public_only:
            from . import public_host

            if not public_host.is_public_address_syntax(newurl):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "redirect refused: non-public address", headers, fp,
                )
        ip_validator = getattr(req, _IP_VALIDATOR_ATTR, None)
        if ip_validator is not None:
            from . import public_host

            target_host = (urlsplit(newurl).hostname or "").strip().rstrip(".").lower()
            literal = public_host._parse_ip_literal(target_host) if target_host else None
            if literal is not None and not ip_validator(literal):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "redirect refused: disallowed IP address", headers, fp,
                )
        guard = getattr(req, _REDIRECT_GUARD_ATTR, None)
        if guard is not None:
            req_scheme = (req.type or urlparse(req.full_url).scheme).lower()
            if req_scheme == "https" and urlparse(newurl).scheme.lower() != "https":
                raise urllib.error.HTTPError(
                    req.full_url, 403, "redirect refused: HTTPS downgrade", headers, fp,
                )
            if not guard(newurl):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "redirect refused: off the site or to a private address", headers, fp,
                )
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if guard is not None:
            setattr(new, _REDIRECT_GUARD_ATTR, guard)
        if public_only:
            setattr(new, _PUBLIC_ONLY_ATTR, True)
        if local_bridge_only:
            setattr(new, _LOCAL_BRIDGE_ONLY_ATTR, True)
        if ip_validator is not None:
            setattr(new, _IP_VALIDATOR_ATTR, ip_validator)
        if _request_origin(req.full_url) != _request_origin(new.full_url):
            for store in (new.headers, getattr(new, "unredirected_hdrs", None)):
                if not store:
                    continue
                for name in list(store):
                    if name.lower() in _CROSS_ORIGIN_AUTH_HEADERS:
                        del store[name]
        return new


_opener = _build_http_opener(bypass_proxy=False)
_direct_opener = _build_http_opener(bypass_proxy=True)
_public_opener = _build_http_opener(bypass_proxy=True, public_only=True)
_local_bridge_opener = _build_http_opener(bypass_proxy=True, local_bridge_only=True)
_DEFAULT_URLOPEN = urllib.request.urlopen


def _open_request(req, timeout, *, bypass_proxy=False):
    """Honor test patches of urllib.request.urlopen; otherwise use the strip opener."""
    url = getattr(req, "full_url", None) or str(req)
    try:
        _check_http_scheme(url)
    except ValueError as exc:
        raise urllib.error.URLError(str(exc)) from exc
    current = urllib.request.urlopen
    if current is not _DEFAULT_URLOPEN:
        return current(req, timeout=timeout)
    public_only = bool(getattr(req, _PUBLIC_ONLY_ATTR, False))
    local_bridge_only = bool(getattr(req, _LOCAL_BRIDGE_ONLY_ATTR, False))
    ip_validator = getattr(req, _IP_VALIDATOR_ATTR, None)
    if public_only and ip_validator is None:
        from . import public_host

        if not public_host.is_public_address_syntax(url):
            raise urllib.error.URLError(
                public_host.NonPublicAddressError("refused non-public address")
            )
        return _public_opener.open(req, timeout=timeout)
    if local_bridge_only and ip_validator is None:
        return _local_bridge_opener.open(req, timeout=timeout)
    if ip_validator is not None:
        if local_bridge_only:
            raise ValueError("local_bridge_only and ip_validator are mutually exclusive")
        effective_validator = ip_validator
        if public_only:
            from . import public_host

            if not public_host.is_public_address_syntax(url):
                raise urllib.error.URLError(
                    public_host.NonPublicAddressError("refused non-public address")
                )

            def _combined_validator(addr):
                return public_host._is_public_ip(addr) and bool(ip_validator(addr))

            effective_validator = _combined_validator
        return _build_http_opener(
            bypass_proxy=True,
            public_only=public_only,
            ip_validator=effective_validator,
        ).open(req, timeout=timeout)
    opener = _direct_opener if bypass_proxy else _opener
    return opener.open(req, timeout=timeout)


def open_request(req, timeout):
    """Open ``req`` with cross-origin credential stripping (see #1062).

    Public entry point for modules that build their own ``Request`` and would
    otherwise call ``urllib.request.urlopen`` directly, bypassing the redirect
    handler installed above.
    """
    return _open_request(req, timeout)


_failure_sink: ContextVar[Optional[list["HTTPError"]]] = ContextVar(
    "last30days_http_failure_sink",
    default=None,
)
_expected_miss_statuses: ContextVar[frozenset[int]] = ContextVar(
    "last30days_http_expected_miss_statuses",
    default=frozenset(),
)
_expected_no_host: ContextVar[Optional[list["HTTPError"]]] = ContextVar(
    "last30days_http_expected_no_host",
    default=None,
)

_FIXTURE_FORMAT = "last30days-http-fixture/v1"
_FIXTURE_SECRET_KEYS = frozenset(
    {
        "api_key", "apikey", "authorization", "cookie", "key", "secret", "token",
        "password", "passwd", "passphrase", "credential", "bearer", "jwt",
    }
)
# Suffixes are matched on the normalized key, where camelCase collapses without
# a separator ("accessJwt" -> "accessjwt"), so these are bare rather than
# underscore-prefixed. "key" is deliberately absent: it would redact "monkey".
_FIXTURE_SECRET_KEY_SUFFIXES = (
    "_api_key", "apikey", "_authorization", "_cookie", "_secret", "_token",
    "password", "passwd", "passphrase", "credential", "jwt",
)
_fixture_lock = threading.Lock()
_fixture_state: Optional[dict[str, Any]] = None
_NO_FIXTURE = object()
_fixture_module_capture: ContextVar[bool] = ContextVar(
    "last30days_fixture_module_capture",
    default=False,
)


def _is_secret_key(value: object) -> bool:
    key = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return (
        key in _FIXTURE_SECRET_KEYS
        or key.endswith(_FIXTURE_SECRET_KEY_SUFFIXES)
    )


def _scrub_fixture_value(
    value: Any,
    *,
    key: str = "",
    redactions: frozenset[str] = frozenset(),
) -> Any:
    """Remove credentials before a recorded exchange reaches disk."""
    if key and _is_secret_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _scrub_fixture_value(
                child_value,
                key=str(child_key),
                redactions=redactions,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_scrub_fixture_value(item, redactions=redactions) for item in value]
    if isinstance(value, str):
        scrubbed = value
        for secret in sorted(redactions, key=len, reverse=True):
            if len(secret) >= 4:
                scrubbed = scrubbed.replace(secret, "<redacted>")
        return scrubbed
    return value


_AUTH_SCHEME_RE = re.compile(r"^(?:bearer|basic|token)\s+(\S+)$", re.IGNORECASE)


def _collect_secret_values(value: Any, *, key: str = "") -> set[str]:
    values: set[str] = set()
    if key and _is_secret_key(key) and value not in (None, ""):
        text = str(value)
        values.add(text)
        # "Bearer <token>": the bare token is what a response body or an
        # adapter error message echoes, so redact it on its own too.
        scheme = _AUTH_SCHEME_RE.match(text.strip())
        if scheme:
            values.add(scheme.group(1))
        return values
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            values.update(_collect_secret_values(child_value, key=str(child_key)))
    elif isinstance(value, list):
        for child in value:
            values.update(_collect_secret_values(child))
    return values


def _fixture_redactions(
    url: str,
    headers: dict[str, str],
    json_data: Optional[Dict[str, Any]],
) -> frozenset[str]:
    values: set[str] = set()
    try:
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if _is_secret_key(key) and value:
                values.add(value)
    except ValueError:
        pass
    values.update(_collect_secret_values(headers))
    values.update(_collect_secret_values(json_data))
    # Session-wide secret values (process env plus resolved config) so a
    # bearer loaded from .env, Keychain, or pass is scrubbed at the HTTP
    # seam even when this request carried it only in a header.
    with _fixture_lock:
        state = _fixture_state
        if state is not None and state.get("redactions"):
            values.update(state["redactions"])
    return frozenset(values)


def config_secret_values(config: Dict[str, Any]) -> frozenset[str]:
    """Secret VALUES from a resolved config, for fixture redaction.

    Every key the Keychain/pass loader knows (``env.KEYCHAIN_KEYS``) plus any
    secret-named key is a credential regardless of which layer supplied it.
    """
    values: set[str] = set()
    try:
        from . import env as _env
        secret_keys = set(_env.KEYCHAIN_KEYS)
    except Exception:  # pragma: no cover - env is always importable
        secret_keys = set()
    for key, value in (config or {}).items():
        if not isinstance(value, str) or len(value) < 4:
            continue
        if "://" in value:
            # An endpoint (e.g. an API base URL) is an address, not a credential.
            continue
        if key in secret_keys or _is_secret_key(key):
            values.add(value)
    return frozenset(values)


def add_fixture_redactions(values) -> None:
    """Register secret values with the active recording session, if any.

    ``env.get_config`` calls this with the resolved config's secrets so a
    bearer loaded from a file or a credential store is redacted at both the
    HTTP seam and the module seam. A no-op outside a recording session.
    """
    extra = {v for v in values if isinstance(v, str) and len(v) >= 4}
    if not extra:
        return
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        state["redactions"] = frozenset(state.get("redactions") or frozenset()) | extra


def _scrub_fixture_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = urlencode(
            [
                (key, "<redacted>" if _is_secret_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except ValueError:
        return url


def _fixture_request(
    method: str,
    url: str,
    json_data: Optional[Dict[str, Any]],
    raw: bool,
) -> dict[str, Any]:
    request_data: dict[str, Any] = {
        "method": method.upper(),
        "url": _scrub_fixture_url(url),
        "raw": bool(raw),
    }
    if json_data is not None:
        request_data["json"] = _scrub_fixture_value(json_data)
    return request_data


def _fixture_key(request_data: dict[str, Any]) -> str:
    return json.dumps(request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@contextmanager
def recording_requests(path: str | Path):
    """Record scrubbed HTTP exchanges to ``path`` for offline eval replay.

    This process-global session is deliberate: source requests run in worker
    threads, so a ContextVar would not observe the complete pipeline fan-out.
    Nested or concurrent recording/replay sessions are rejected.
    """
    global _fixture_state
    target = Path(path).expanduser()
    if target.suffix.lower() != ".json":
        target = target / "http.json"
    with _fixture_lock:
        if _fixture_state is not None:
            raise RuntimeError("An HTTP fixture session is already active")
        _fixture_state = {
            "mode": "record",
            "path": target,
            "exchanges": [],
            "source_exchanges": [],
            # Secret VALUES from the environment, so module-seam recordings
            # scrub tokens echoed inside normal string fields (adapter error
            # messages, parsed item text), not just secret-named keys. The
            # resolved config's secrets join via add_fixture_redactions once
            # env.get_config runs inside the session.
            "redactions": frozenset(
                value
                for key, value in os.environ.items()
                if _is_secret_key(key) and isinstance(value, str) and len(value) >= 4
            ),
        }
    completed = False
    try:
        yield target
        completed = True
    finally:
        with _fixture_lock:
            state = _fixture_state
            _fixture_state = None
        if state is not None and completed:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": _FIXTURE_FORMAT,
                "exchanges": state["exchanges"],
                "source_exchanges": state["source_exchanges"],
            }
            # A recorded exchange is credential-adjacent by construction:
            # redaction is key-name driven, so an unrecognized key name leaves
            # a real value on disk. Create the temp file 0600 at open time
            # rather than chmod-ing after the write, or the credentials sit in
            # a world-readable file for the length of the write (the parent
            # directory is caller-supplied and not guaranteed private).
            # Mirrors last30days.save_output. Unlink first so a stale or
            # pre-planted temp file cannot be reused with its own wider mode --
            # O_CREAT does not alter the mode of an existing file.
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.unlink(missing_ok=True)
            fd = os.open(
                temporary,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
                )
            temporary.replace(target)


@contextmanager
def fixture_module_capture(enabled: bool):
    """Suppress nested HTTP recording when a whole adapter result is captured."""
    token = _fixture_module_capture.set(enabled)
    try:
        yield
    finally:
        _fixture_module_capture.reset(token)


@contextmanager
def replaying_requests(path: str | Path):
    """Replay recorded exchanges and fail closed on any unrecorded request."""
    global _fixture_state
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "http.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("format") != _FIXTURE_FORMAT:
        raise ValueError(f"Unsupported HTTP fixture format in {target}")
    queues: dict[str, list[dict[str, Any]]] = {}
    for exchange in payload.get("exchanges") or []:
        queues.setdefault(_fixture_key(exchange["request"]), []).append(exchange["response"])
    source_queues: dict[str, list[Any]] = {}
    for exchange in payload.get("source_exchanges") or []:
        source_queues.setdefault(_fixture_key(exchange["request"]), []).append(exchange)
    with _fixture_lock:
        if _fixture_state is not None:
            raise RuntimeError("An HTTP fixture session is already active")
        _fixture_state = {
            "mode": "replay",
            "path": target,
            "queues": queues,
            "source_queues": source_queues,
        }
    try:
        yield target
        with _fixture_lock:
            unused = sum(len(values) for values in queues.values()) + sum(
                len(values) for values in source_queues.values()
            )
        if unused:
            raise AssertionError(f"HTTP fixture replay left {unused} unused exchange(s): {target}")
    finally:
        with _fixture_lock:
            _fixture_state = None


class ResponseHeaders(dict):
    """Response headers keyed by lowercased name; lookups ignore case.

    ``url`` is the address that answered, after any redirects ('' when not
    known, as for a replayed fixture).
    """

    url = ""

    def __init__(self, pairs=()):
        super().__init__(
            (str(name).lower(), value) for name, value in dict(pairs).items()
        )

    def __getitem__(self, name):
        return super().__getitem__(str(name).lower())

    def __contains__(self, name):
        return super().__contains__(str(name).lower())

    def get(self, name, default=None):
        return super().get(str(name).lower(), default)


def _fixture_replay(request_data: dict[str, Any], *, return_headers: bool = False) -> Any:
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "replay":
            return _NO_FIXTURE
        queue = state["queues"].get(_fixture_key(request_data))
        if not queue:
            raise AssertionError(
                "Unrecorded HTTP request during fixture replay: "
                f"{request_data['method']} {request_data['url']}"
            )
        response = queue.pop(0)
    if response.get("error"):
        error = response["error"]
        recorded_error = HTTPError(
            str(error.get("message") or "Recorded HTTP error"),
            status_code=error.get("status_code"),
            body=error.get("body"),
            outcome_state=error.get("outcome_state"),
        )
        # Callers branch on this (a dead careers host is not asked for its
        # second page), so a replay without it makes requests the live run
        # never recorded.
        recorded_error.no_host = bool(error.get("no_host"))
        _raise(recorded_error)
    if return_headers:
        # An exchange recorded by a request that did not ask for headers has
        # none stored; it replays with an empty set.
        return response.get("value"), ResponseHeaders(response.get("headers") or {})
    return response.get("value")


def _fixture_record(
    request_data: dict[str, Any],
    *,
    value: Any = None,
    error: Optional["HTTPError"] = None,
    redactions: frozenset[str] = frozenset(),
    headers: Optional["ResponseHeaders"] = None,
) -> None:
    if _fixture_module_capture.get():
        return
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        # Union the session's env-derived secret VALUES in, so a credential
        # echoed back inside an ordinary response field is scrubbed on this
        # path too, not only on the source-record path. Response scrubbing does
        # not feed _fixture_key, so this cannot make a replay key
        # machine-dependent.
        redactions = redactions | (state.get("redactions") or frozenset())
        response: dict[str, Any]
        if error is None:
            response = {"value": _scrub_fixture_value(value, redactions=redactions)}
            if headers is not None:
                # Scrubbed like any other value: a secret-named header (a
                # session token, Set-Cookie) is stored as "<redacted>".
                response["headers"] = _scrub_fixture_value(
                    dict(headers), redactions=redactions
                )
        else:
            response = {
                "error": _scrub_fixture_value(
                    {
                        "message": str(error),
                        "status_code": error.status_code,
                        "body": error.body,
                        "outcome_state": error.outcome_state,
                    },
                    redactions=redactions,
                )
            }
            if error.no_host:
                response["error"]["no_host"] = True
        state["exchanges"].append({"request": request_data, "response": response})


def fixture_source_replay(request_data: dict[str, Any]) -> tuple[bool, Any]:
    """Return a recorded CLI-backed source result when replay is active."""
    scrubbed = _scrub_fixture_value(request_data)
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "replay":
            return False, None
        queue = state["source_queues"].get(_fixture_key(scrubbed))
        if not queue:
            raise AssertionError(
                "Unrecorded CLI-backed source request during fixture replay: "
                f"{request_data.get('source', 'unknown')}"
            )
        exchange = queue.pop(0)
    if exchange.get("type") == "error":
        error = exchange.get("error") or {}
        raise RecordedSourceError(
            str(error.get("message") or "Recorded source error"),
            exception_type=str(error.get("exception_type") or "Exception"),
            outcome_state=error.get("outcome_state"),
        )
    return True, exchange.get("value")


def fixture_source_record(request_data: dict[str, Any], value: Any) -> None:
    """Record the parsed output of a source adapter that bypasses http.py."""
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        session_redactions = state.get("redactions") or frozenset()
        state["source_exchanges"].append(
            {
                "request": _scrub_fixture_value(request_data, redactions=session_redactions),
                "value": _scrub_fixture_value(value, redactions=session_redactions),
            }
        )


def fixture_source_record_error(request_data: dict[str, Any], error: Exception) -> None:
    """Record a replayable failure from a source adapter that bypasses http.py."""
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        session_redactions = state.get("redactions") or frozenset()
        state["source_exchanges"].append(
            {
                "request": _scrub_fixture_value(request_data, redactions=session_redactions),
                "type": "error",
                "error": _scrub_fixture_value(
                    {
                        "exception_type": type(error).__name__,
                        "message": str(error),
                        "outcome_state": getattr(error, "outcome_state", None),
                    }
                , redactions=session_redactions),
            }
        )


class RecordedSourceError(RuntimeError):
    """Failure restored from a recorded module-backed source exchange."""

    def __init__(
        self,
        message: str,
        *,
        exception_type: str,
        outcome_state: Optional[str] = None,
    ):
        super().__init__(message)
        self.exception_type = exception_type
        self.outcome_state = outcome_state


def _is_dns_failure(err: urllib.error.URLError) -> bool:
    """Return True if a URLError was caused by DNS resolution (gaierror)."""
    return isinstance(getattr(err, "reason", None), socket.gaierror)


class HTTPError(Exception):
    """HTTP request error with status code."""
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        body: Optional[str] = None,
        outcome_state: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        # True when no host answered: the name did not resolve or the
        # connection was refused. See ``expected_no_host``.
        self.no_host = False
        self.outcome_state = outcome_state or classify_failure(
            status_code=status_code,
            message=message,
        )


class DeadlineExceeded(HTTPError):
    """The caller's shared wall deadline expired across request retries."""

    def __init__(self):
        super().__init__(
            "Request deadline exceeded",
            outcome_state=health.TIMEOUT,
        )


class ResponseTooLarge(HTTPError):
    """An upstream response exceeded the configured byte limit."""

    def __init__(self, message: str):
        super().__init__(message, outcome_state=health.ERROR)


def _content_length(headers) -> Optional[int]:
    if not headers or not hasattr(headers, "get"):
        return None
    raw = headers.get("Content-Length")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


_READ_CHUNK_BYTES = 65_536


def _read_bounded(
    stream,
    max_bytes: int,
    *,
    truncate: bool = False,
    cancel: Optional[threading.Event] = None,
) -> bytes:
    if cancel is not None and cancel.is_set():
        raise DeadlineExceeded()
    if not truncate:
        declared = (
            stream.length
            if isinstance(stream, http.client.HTTPResponse)
            else _content_length(getattr(stream, "headers", None))
        )
        if isinstance(declared, int) and not isinstance(declared, bool) and declared > max_bytes:
            raise ResponseTooLarge(
                f"Response Content-Length {declared} exceeds limit of {max_bytes} bytes"
            )
    limit = max_bytes if truncate else max_bytes + 1
    reader = (
        stream.fp
        if isinstance(stream, urllib.error.HTTPError)
        and isinstance(getattr(stream, "fp", None), io.IOBase)
        else stream
    )
    if cancel is not None and isinstance(reader, io.IOBase) and hasattr(reader, "read1"):
        chunks: list[bytes] = []
        total = 0
        while total < limit:
            if cancel.is_set():
                raise DeadlineExceeded()
            chunk = reader.read1(min(_READ_CHUNK_BYTES, limit - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if cancel.is_set():
            raise DeadlineExceeded()
        data = b"".join(chunks)
    else:
        data = stream.read(limit)
        if cancel is not None and cancel.is_set():
            raise DeadlineExceeded()
    if data is None:
        return b""
    if truncate:
        return data[:max_bytes]
    if len(data) > max_bytes:
        raise ResponseTooLarge(f"Response body exceeded limit of {max_bytes} bytes")
    if (
        isinstance(stream, http.client.HTTPResponse)
        and not stream.chunked
        and getattr(stream, "_method", None) != "HEAD"
        and isinstance(stream.length, int)
        and stream.length > 0
    ):
        raise http.client.IncompleteRead(data, stream.length)
    return data


@contextmanager
def capture_failures():
    """Capture terminal request failures in the current retrieval context.

    Source modules historically catch ``HTTPError`` and return an empty result.
    The context-local sink lets the pipeline retain that failure without shared
    mutable state across its worker threads.
    """
    failures: list[HTTPError] = []
    token = _failure_sink.set(failures)
    try:
        yield failures
    finally:
        _failure_sink.reset(token)


@contextmanager
def tee_failures():
    """Observe failures locally WITHOUT hiding them from the enclosing sink.

    ``capture_failures()`` *replaces* the context-local sink, so nesting it
    inside a retrieval context swallows the very failure the pipeline needs.
    This yields a local list and forwards its contents to the parent sink on
    exit, so a swallow site (``get_text`` returns None and drops the status)
    can recover what it lost while the pipeline still sees the failure.
    """
    parent = _failure_sink.get()
    local: list[HTTPError] = []
    token = _failure_sink.set(local)
    try:
        yield local
    finally:
        _failure_sink.reset(token)
        if parent is not None:
            parent.extend(local)


def other_host_form(host: str) -> str:
    """The ``www.`` form of a bare two-label host, or the bare form of a ``www.``
    host, or "" when the host has neither (a subdomain such as ``jobs.x.com``).

    Some sites answer on only one of the two: ``nvidia.com`` drops the
    connection while ``www.nvidia.com`` serves the page.
    """
    host = (host or "").strip().lower()
    if host.startswith("www."):
        bare = host[4:]
        return bare if "." in bare else ""
    return f"www.{host}" if host.count(".") == 1 else ""


def connection_dropped(failures: list[HTTPError]) -> bool:
    """True when a request reached no page because the connection was dropped:
    no HTTP status, not a timeout, and not a name that failed to resolve or a
    refused connection, which say nothing answers there at all."""
    return any(
        failure.status_code is None
        and failure.outcome_state != health.TIMEOUT
        and not failure.no_host
        for failure in failures
    )


def text_on_either_host_form(fetch: Callable[[str], Optional[str]], url: str) -> tuple[Optional[str], str]:
    """``fetch(url)``, retried once on the site's other host form when the
    connection was dropped. Returns the text and the URL that gave it.

    Only one failure is kept for the run's record: the first one when both
    forms fail, none when the other form answers.
    """
    parts = urlsplit(url)
    other = other_host_form(parts.hostname or "")
    with tee_failures() as failures:
        text = fetch(url)
        if text or not other or not connection_dropped(failures):
            return text, url
        first = list(failures)
        failures.clear()
        other_url = urlunsplit(parts._replace(netloc=other))
        text = fetch(other_url)
        if text:
            return text, other_url
        failures[:] = first
        return None, url


@contextmanager
def expected_misses(*status_codes: int):
    """Exclude adapter-declared probe misses from captured run failures."""
    token = _expected_miss_statuses.set(
        _expected_miss_statuses.get().union(status_codes)
    )
    try:
        yield
    finally:
        _expected_miss_statuses.reset(token)


@contextmanager
def expected_no_host():
    """Exclude "there is no such host" failures from captured run failures.

    For a caller that tries a host it only guessed: a name that does not
    resolve, or a connection the host refuses, means nothing is there, not
    that a source is down. Yields the failures left out, so the caller can
    stop asking that host. Timeouts and HTTP errors are still recorded.
    """
    dropped: list[HTTPError] = []
    token = _expected_no_host.set(dropped)
    try:
        yield dropped
    finally:
        _expected_no_host.reset(token)


def _is_no_host(error: BaseException) -> bool:
    """A name that did not resolve or a refused connection, bare or wrapped
    in a ``URLError``."""
    reason = getattr(error, "reason", error)
    return isinstance(reason, (socket.gaierror, ConnectionRefusedError))


def submit_with_context(executor, func, /, *args, **kwargs) -> Future:
    """Submit a worker with the caller's failure-capture context."""
    context = copy_context()
    return executor.submit(context.run, func, *args, **kwargs)


def _record_failure(error: HTTPError) -> None:
    if error.status_code in _expected_miss_statuses.get():
        return
    no_host = _expected_no_host.get()
    if no_host is not None and error.no_host:
        no_host.append(error)
        return
    sink = _failure_sink.get()
    if sink is not None:
        sink.append(error)


def _raise(error: HTTPError) -> None:
    _record_failure(error)
    raise error


def classify_failure(*, status_code: Optional[int] = None, message: str = "") -> str:
    """Map a request failure to the doctor-aligned per-run vocabulary."""
    text = message.lower()
    if status_code == 429 or any(
        marker in text for marker in ("http 429", "status 429", "rate limit", "too many requests")
    ):
        return health.RATE_LIMITED
    # Credit exhaustion is checked before the auth branch: a 402 (or a body
    # saying the account has no credits) asks the user to top up, not to
    # re-authenticate. Markers stay narrow on purpose: the bare word "credits"
    # is not one ("10,000 free credits" is onboarding copy, not a failure).
    if status_code == 402 or any(
        marker in text
        for marker in (
            "http 402",
            "status 402",
            "payment required",
            "insufficient credits",
            "does not have any credits",
            "out of credits",
        )
    ):
        return health.PAYMENT_REQUIRED
    if status_code in (401, 403) or any(
        marker in text
        for marker in (
            "http 401",
            "http 403",
            "status 401",
            "status 403",
            "unauthorized",
            "forbidden",
            "authentication failed",
            "expired token",
            "not signed in",
            "not logged in",
            "invalid_grant",
            "refresh token",
            "session expired",
            "grok session expired",
        )
    ):
        return health.AUTH_FAILED
    if status_code == 408 or "timed out" in text or "timeout" in text:
        return health.TIMEOUT
    if any(
        marker in text
        for marker in (
            "invalid json",
            "json decode",
            "schema",
            "interstitial",
            "non-json",
        )
    ):
        return health.SCHEMA_DRIFT
    if any(
        marker in text
        for marker in (
            "url error",
            "connection error",
            "connection refused",
            "connection reset",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
            "dns",
            "network is unreachable",
        )
    ):
        return health.UNREACHABLE
    return health.ERROR


def request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    max_429_retries: int = MAX_429_RETRIES,
    raw: bool = False,
    deadline_monotonic: float | None = None,
    cancel: threading.Event | None = None,
    owned_get: bool = False,
    on_retry: Callable[[], None] | None = None,
    bypass_proxy: bool = False,
    retry_dns: bool = True,
    return_headers: bool = False,
    redirect_guard: Callable[[str], bool] | None = None,
    sensitive_url: bool = False,
    public_only: bool = False,
    local_bridge_only: bool = False,
    max_bytes: Optional[int] = None,
    decode_errors: str = "strict",
    ip_validator: Optional[Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool]] = None,
) -> Union[Dict[str, Any], str, tuple[Union[Dict[str, Any], str], ResponseHeaders]]:
    """Make an HTTP request and return JSON response.

    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        headers: Optional headers dict
        json_data: Optional JSON body (for POST)
        params: Optional query-string params. Values are stringified. None values
            are dropped. If ``url`` already has a query string, ``params`` is appended.
        timeout: Request timeout in seconds
        retries: Maximum number of attempts (clamped to at least 1; passing
            retries=0 executes a single attempt with no retries, including on
            DNS failures, whereas retries>=1 honors ``retry_dns``)
        max_429_retries: Maximum 429 retries before giving up (separate cap)
        raw: If True, return raw response text instead of parsed JSON
        deadline_monotonic: Optional absolute monotonic deadline shared by all
            attempts and retry delays.
        owned_get: Run GET transport in a child that is killed and reaped at
            its deadline. Requires deadline_monotonic; other methods are refused.
        on_retry: Called before each transport attempt after the first.
        bypass_proxy: Use a direct connection for this request and its redirects.
        retry_dns: Widen the retry budget to MIN_DNS_RETRIES when the name does
            not resolve. Off for a host the caller only guessed, where a name
            that does not resolve is the answer.
        return_headers: Also return the response headers, for a host that
            answers in a header (a session token) rather than the body. Not
            available with owned_get, whose child process reports no headers.
        sensitive_url: Exclude credential-bearing URLs and their responses
            from request diagnostics and fixture capture.
        max_bytes: Maximum response body size in bytes (defaults to
            ``MAX_RESPONSE_BYTES``). Oversized responses fail fast with
            ``ResponseTooLarge``.

    Returns:
        Parsed JSON response as dict, or raw text string if raw=True. With
        return_headers=True, a ``(body, ResponseHeaders)`` tuple instead.

    Raises:
        HTTPError: On request failure
    """
    if max_bytes is not None and max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if decode_errors not in ("strict", "replace", "ignore"):
        raise ValueError(f"unsupported decode_errors mode: {decode_errors!r}")
    effective_max_bytes = MAX_RESPONSE_BYTES if max_bytes is None else max_bytes
    if retries <= 0:
        retry_dns = False
        retries = 1
    if owned_get and (method != "GET" or json_data is not None):
        raise ValueError("owned transport requires a bodyless GET")
    if owned_get and deadline_monotonic is None:
        raise ValueError("owned GET requires an operation deadline")
    if owned_get and bypass_proxy:
        raise ValueError("owned GET does not support proxy bypass")
    if owned_get and return_headers:
        raise ValueError("owned GET does not report response headers")
    if public_only and local_bridge_only:
        raise ValueError("public_only and local_bridge_only are mutually exclusive")
    if local_bridge_only and ip_validator is not None:
        raise ValueError("local_bridge_only and ip_validator are mutually exclusive")
    if owned_get and (redirect_guard is not None or public_only or local_bridge_only or ip_validator is not None):
        raise ValueError("owned GET does not support redirect_guard, public_only, local_bridge_only, or ip_validator")
    try:
        _check_http_scheme(url)
    except ValueError as exc:
        _raise(HTTPError(str(exc)))
    if public_only:
        from . import public_host

        if not public_host.is_public_address_syntax(url):
            _raise(HTTPError("Refused non-public URL"))
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    if params:
        filtered = {k: str(v) for k, v in params.items() if v is not None}
        if filtered:
            separator = "&" if ("?" in url) else "?"
            url = f"{url}{separator}{urlencode(filtered)}"
    # Encode any non-ASCII characters to prevent UnicodeEncodeError from
    # http.client.HTTPConnection.putrequest (which uses latin-1 internally).
    # Only encode path, query, and fragment — not the hostname (netloc), which
    # needs IDNA encoding instead of percent-encoding for non-ASCII domains.
    parts = urlsplit(url)
    safe = '/:@!$&\'()*+,;=-._~%?#[]=+'
    url = urlunsplit((
        parts.scheme,
        parts.netloc,
        quote(parts.path, safe=safe),
        quote(parts.query, safe=safe),
        quote(parts.fragment, safe=safe),
    ))

    # Proxy mode: a paid call backed by the proxy sentinel goes to the metered
    # proxy instead (lib/proxy.py). Rewritten before fixture capture so the
    # recorded request carries the proxy URL and the bearer key is redacted.
    orig_header_secrets = _collect_secret_values(headers)
    usage_url = url
    try:
        routed = proxy.route(method, url, headers)
    except proxy.ProxyUnavailable as exc:
        from . import auth_safety

        safe_proxy_err = auth_safety.scrub_secrets(str(exc), orig_header_secrets)
        unavailable = HTTPError(f"API key proxy: {safe_proxy_err}", exc.status_code)
        _raise(unavailable)
    call_key = None
    if routed is not None:
        url = routed.url
        headers = routed.headers
        call_key = proxy.new_idempotency_key()
        headers[proxy.IDEMPOTENCY_HEADER] = call_key

    fixture_request = None if sensitive_url else _fixture_request(method, url, json_data, raw)
    fixture_redactions = _fixture_redactions(url, headers, json_data) | orig_header_secrets
    replayed = _fixture_replay(
        fixture_request if fixture_request is not None else {"method": method, "url": "<redacted>"},
        return_headers=return_headers,
    )
    if replayed is not _NO_FIXTURE:
        return replayed

    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode('utf-8')
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    if redirect_guard is not None:
        setattr(req, _REDIRECT_GUARD_ATTR, redirect_guard)
    if public_only:
        setattr(req, _PUBLIC_ONLY_ATTR, True)
    if local_bridge_only:
        setattr(req, _LOCAL_BRIDGE_ONLY_ATTR, True)
    if ip_validator is not None:
        setattr(req, _IP_VALIDATOR_ATTR, ip_validator)

    from . import auth_safety

    def _scrub_error_text(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return auth_safety.scrub_secrets(
            _URL_USERINFO_RE.sub(r"\1", value),
            fixture_redactions,
        )

    def log_request(message: str) -> None:
        if not sensitive_url:
            log(
                auth_safety.scrub_secrets(
                    _scrub_fixture_value(message, redactions=fixture_redactions),
                    fixture_redactions,
                )
            )

    safe_url = re.sub(r'([?&])(key|api_key|token|secret)=[^&]*', r'\1\2=***', url)
    log_request(f"{method} {safe_url}")

    last_error = None
    rate_limit_count = 0
    # DNS failures get a dedicated minimum attempt count + exponential backoff.
    # `effective_retries` is the actual loop bound; we expand it on the first
    # gaierror if the caller passed a smaller `retries` value than MIN_DNS_RETRIES.
    effective_retries = retries
    dns_attempts = 0
    attempt = 0
    response_headers: Optional[ResponseHeaders] = None

    def raise_recorded(error: HTTPError) -> None:
        if fixture_request is not None:
            _fixture_record(fixture_request, error=error, redactions=fixture_redactions)
        _raise(error)

    def deadline_error() -> HTTPError:
        return DeadlineExceeded()

    def sleep_before_retry(delay: float) -> bool:
        """Sleep only when the full delay fits inside the caller's deadline."""
        nonlocal last_error
        if cancel is not None and cancel.is_set():
            last_error = deadline_error()
            return False
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0 or delay >= remaining:
                last_error = deadline_error()
                return False
        if cancel is not None:
            if cancel.wait(delay):
                last_error = deadline_error()
                return False
        else:
            time.sleep(delay)
        return True

    def open_and_read(
        request_timeout: float,
    ) -> tuple[int, str | None, urllib.error.HTTPError | None]:
        nonlocal response_headers
        try:
            transport = (
                _open_request(req, request_timeout, bypass_proxy=True)
                if bypass_proxy else _open_request(req, request_timeout)
            )
            with transport as response:
                if routed is not None:
                    proxy.record_charge(getattr(response, "headers", None), call_key=call_key)
                if return_headers:
                    response_headers = ResponseHeaders(response.headers.items())
                    final = response.geturl() if hasattr(response, "geturl") else ""
                    response_headers.url = final if isinstance(final, str) else ""
                return (
                    response.status,
                    _read_bounded(
                        response, effective_max_bytes, cancel=cancel,
                    ).decode('utf-8', errors=decode_errors),
                    None,
                )
        except urllib.error.HTTPError as error:
            # Error bodies can stall just like successful bodies. Read both
            # inside the same deadline-protected worker before classification.
            body = None
            try:
                raw_error = _read_bounded(
                    error, MAX_ERROR_BODY_BYTES, truncate=True, cancel=cancel,
                )
                body = raw_error.decode(
                    'utf-8',
                    errors='replace' if len(raw_error) == MAX_ERROR_BODY_BYTES else 'strict',
                )
            except (OSError, UnicodeDecodeError, ValueError):
                pass
            finally:
                if isinstance(getattr(error, "fp", None), http.client.HTTPResponse):
                    try:
                        error.close()
                    except Exception:
                        pass
            return error.code, body, error

    def open_and_read_before_deadline(
        request_timeout: float,
    ) -> tuple[int, str | None, urllib.error.HTTPError | None]:
        """Stop waiting at the wall deadline, even during DNS or body reads."""
        if owned_get:
            try:
                owned_kwargs: Dict[str, Any] = {}
                if routed is not None:
                    # Only a proxied call needs the charge header back.
                    owned_kwargs["with_headers"] = True
                if max_bytes is not None:
                    owned_kwargs["max_bytes"] = max_bytes
                owned = bounded_get.get(
                    req, timeout=request_timeout,
                    deadline_monotonic=deadline_monotonic, cancel=cancel,
                    **owned_kwargs,
                )
            except bounded_get.GetLaunchError:
                usage.cancel(charge)
                raise
            except bounded_get.GetTimeout as exc:
                if not exc.started:
                    usage.cancel(charge)
                raise deadline_error() from exc
            except bounded_get.ResponseTooLarge as exc:
                raise ResponseTooLarge(str(exc)) from exc
            if routed is not None:
                status, body, error, owned_headers = owned
                if error is None:
                    proxy.record_charge(dict(owned_headers), call_key=call_key)
            else:
                status, body, error = owned
            try:
                if body is None:
                    decoded = None
                elif error is not None:
                    decoded = body.decode("utf-8", errors="replace")
                else:
                    decoded = body.decode("utf-8", errors=decode_errors)
            except UnicodeDecodeError:
                if error is None:
                    raise
                decoded = None
            return status, decoded, error
        if deadline_monotonic is None and cancel is None:
            return open_and_read(request_timeout)
        wait_deadline = deadline_monotonic or (time.monotonic() + request_timeout)
        remaining = wait_deadline - time.monotonic()
        if remaining <= 0:
            raise deadline_error()
        future: Future = Future()

        def worker() -> None:
            try:
                if cancel is not None and cancel.is_set():
                    raise deadline_error()
                future.set_result(open_and_read(request_timeout))
            except BaseException as exc:
                future.set_exception(exc)

        threading.Thread(target=worker, daemon=True).start()
        if cancel is None:
            try:
                return future.result(timeout=remaining)
            except TimeoutError as exc:
                if future.done():
                    return future.result()
                raise deadline_error() from exc
        while True:
            if cancel.is_set():
                raise deadline_error()
            remaining = wait_deadline - time.monotonic()
            if remaining <= 0:
                raise deadline_error()
            try:
                return future.result(timeout=min(remaining, 0.1))
            except TimeoutError:
                # Preserve worker-side socket failures for transport classification.
                if future.done():
                    return future.result()

    while attempt < effective_retries:
        if cancel is not None and cancel.is_set():
            last_error = deadline_error()
            break
        request_timeout = timeout
        if deadline_monotonic is not None:
            remaining = deadline_monotonic - time.monotonic()
            if remaining <= 0:
                last_error = deadline_error()
                break
            request_timeout = min(timeout, remaining)
            usage_deadline = (
                deadline_monotonic
                if request_timeout == remaining
                else (deadline_monotonic - remaining) + request_timeout
            )
        else:
            usage_deadline = time.monotonic() + request_timeout
        try:
            if attempt > 0 and on_retry is not None:
                on_retry()
            if routed is not None:
                # Another call may have turned this lane off while we waited.
                try:
                    proxy.ensure_open(routed.provider)
                except proxy.ProxyUnavailable as exc:
                    raise_recorded(HTTPError(f"API key proxy: {exc}", exc.status_code))
                req.add_header(proxy.DEADLINE_HEADER, proxy.deadline_value(request_timeout))
            with usage.with_deadline(usage_deadline):
                charge = usage.begin_http(usage_url, method)
                response_status, body, response_error = open_and_read_before_deadline(request_timeout)
                if body:
                    try:
                        usage.finish(charge, json.loads(body))
                    except (ValueError, TypeError):
                        pass
            if (cancel is not None and cancel.is_set()) or (
                deadline_monotonic is not None
                and time.monotonic() >= deadline_monotonic
            ):
                raise_recorded(deadline_error())
            if response_error is not None:
                raise response_error
            body = body or ""
            log_request(f"Response: {response_status} ({len(body)} bytes)")
            value = body if raw else (json.loads(body) if body else {})
            if fixture_request is not None:
                _fixture_record(
                    fixture_request,
                    value=value,
                    redactions=fixture_redactions,
                    headers=response_headers,
                )
            if return_headers:
                return value, response_headers
            return value
        except (DeadlineExceeded, ResponseTooLarge) as exc:
            raise_recorded(exc)
        except urllib.error.HTTPError as e:
            safe_reason = _scrub_error_text(str(e.reason)) or ""
            safe_body = _scrub_error_text(body)
            log_request(f"HTTP Error {e.code}: {safe_reason}")
            if safe_body:
                snippet = " ".join(safe_body.split())
                log_request(f"Error body: {snippet[:200]}")
            last_error = HTTPError(f"HTTP {e.code}: {safe_reason}", e.code, safe_body)

            # A proxy error that ends the lane (out of credits, spend ceiling,
            # provider off, duplicate call) is never retried. Pass the original
            # body so JSON error codes survive secret-scrubbing truncation.
            if routed is not None and proxy.observe_error(
                routed.provider, e.code, body, getattr(e, "headers", None), call_key,
            ):
                raise_recorded(last_error)

            # Don't retry client errors (4xx) except rate limits
            if 400 <= e.code < 500 and e.code != 429:
                raise_recorded(last_error)

            # Cap 429 retries separately to avoid wasting latency
            if e.code == 429:
                rate_limit_count += 1
                if rate_limit_count >= max_429_retries:
                    raise_recorded(last_error)

            # HTTP errors respect the caller's original `retries`; only DNS
            # failures get the widened `effective_retries` budget.
            if attempt < retries - 1:
                if e.code == 429:
                    # Respect Retry-After or x-ratelimit-reset (Reddit sends the
                    # latter), falling back to exponential backoff: 3s, 5s, 9s...
                    delay = retry_delay_from_headers(
                        getattr(e, "headers", None),
                        RETRY_DELAY * (2 ** attempt) + 1,
                    )
                    log_request(f"Rate limited (429). Waiting {delay:.1f}s before retry {attempt + 2}/{retries}")
                else:
                    delay = RETRY_DELAY * (2 ** attempt)
                if not sleep_before_retry(delay):
                    break
            else:
                # Caller's original retry budget exhausted; an earlier DNS
                # failure may have widened `effective_retries`, but that
                # widening is DNS-only — don't grant extra HTTP attempts.
                break
        except urllib.error.URLError as e:
            safe_reason = _scrub_error_text(str(e.reason)) or ""
            log_request(f"URL Error: {safe_reason}")
            reason = getattr(e, "reason", None)
            # urllib commonly wraps socket.timeout (an alias of TimeoutError
            # since 3.10) in URLError; classify those as timeouts, not
            # unreachable hosts, so the recovery guidance is right.
            wrapped_timeout = isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()
            last_error = HTTPError(
                f"URL Error: {safe_reason}",
                outcome_state=health.TIMEOUT if wrapped_timeout else health.UNREACHABLE,
            )
            last_error.no_host = _is_no_host(e)
            from . import public_host

            if isinstance(reason, public_host.NonPublicAddressError):
                raise_recorded(last_error)
            if _is_dns_failure(e):
                # DNS resolution failures are transient; expand the retry budget
                # to MIN_DNS_RETRIES if the caller passed fewer, and use
                # exponential backoff (1s, 2s, 4s, ...) instead of the linear
                # default. Counts DNS attempts separately so other URLError
                # causes don't bypass the regular retry budget.
                dns_attempts += 1
                if retry_dns and effective_retries < MIN_DNS_RETRIES:
                    log_request(
                        f"DNS resolution failed; expanding retry budget from "
                        f"{effective_retries} to {MIN_DNS_RETRIES}"
                    )
                    effective_retries = MIN_DNS_RETRIES
                if attempt < effective_retries - 1:
                    delay = 2 ** (dns_attempts - 1)  # 1s, 2s, 4s, 8s, ...
                    log_request(
                        f"DNS resolution failure (attempt {dns_attempts}); "
                        f"retrying in {delay:.1f}s"
                    )
                    if not sleep_before_retry(delay):
                        break
            elif attempt < retries - 1:
                # Non-DNS URLError (e.g. ConnectionRefused) respects the
                # caller's original retry budget, not the DNS-widened bound.
                if not sleep_before_retry(RETRY_DELAY * (attempt + 1)):
                    break
            else:
                # Caller's original retry budget exhausted; an earlier DNS
                # failure widening `effective_retries` does not carry over
                # to non-DNS error paths.
                break
        except json.JSONDecodeError as e:
            safe_err = _scrub_error_text(str(e)) or ""
            log_request(f"JSON decode error: {safe_err}")
            last_error = HTTPError(
                f"Invalid JSON response: {safe_err}",
                outcome_state=health.SCHEMA_DRIFT,
            )
            raise_recorded(last_error)
        except (OSError, TimeoutError, ConnectionResetError, http.client.HTTPException) as e:
            # Handle socket-level errors (connection reset, timeout, etc.),
            # and a body cut off mid-read (``IncompleteRead``), which
            # ``http.client`` raises as an ``HTTPException``, not an OSError.
            safe_err = _scrub_error_text(str(e)) or ""
            log_request(f"Connection error: {type(e).__name__}: {safe_err}")
            state = health.TIMEOUT if isinstance(e, TimeoutError) else health.UNREACHABLE
            last_error = HTTPError(
                f"Connection error: {type(e).__name__}: {safe_err}",
                outcome_state=state,
            )
            last_error.no_host = _is_no_host(e)
            if attempt < retries - 1:
                # Socket errors respect the caller's original retry budget.
                if not sleep_before_retry(RETRY_DELAY * (attempt + 1)):
                    break
            else:
                # Original budget exhausted; DNS widening doesn't apply here.
                break

        attempt += 1

    if last_error:
        raise_recorded(last_error)
    error = HTTPError("Request failed with no error details")
    raise_recorded(error)


def get(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
    """Make a GET request."""
    return request("GET", url, headers=headers, **kwargs)


def post(url: str, json_data: Dict[str, Any], headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
    """Make a POST request with JSON body."""
    return request("POST", url, headers=headers, json_data=json_data, **kwargs)


def post_raw(
    url: str,
    json_data: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    decode_errors: str = "strict",
    **kwargs,
) -> str:
    """Make a POST request with JSON body and return raw text."""
    return request(
        "POST",
        url,
        headers=headers,
        json_data=json_data,
        raw=True,
        decode_errors=decode_errors,
        **kwargs,
    )


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def get_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
    accept: str = "*/*",
    headers: Optional[Dict[str, str]] = None,
    deadline_monotonic: float | None = None,
    cancel: Optional[threading.Event] = None,
    retry_dns: bool = True,
    landed: Optional[list] = None,
    redirect_guard: Optional[Callable[[str], bool]] = None,
    public_only: bool = False,
    max_bytes: Optional[int] = None,
    decode_errors: str = "strict",
    ip_validator: Optional[Callable[[Union[ipaddress.IPv4Address, ipaddress.IPv6Address]], bool]] = None,
) -> Optional[str]:
    """Fetch a URL and return decoded text, or None on any failure.

    Keyless helper for Reddit site search and shreddit HTML endpoints, the free path
    that replaced the now-403 ``.json`` endpoints. Sends a browser User-Agent
    and never raises: returns None on HTTP error, network failure, or timeout
    so tiered callers can fall through to the next source.

    Args:
        url: Request URL
        timeout: HTTP timeout per attempt in seconds
        retries: Number of retries on failure (kept low — these tiers fail fast)
        accept: Accept header value (e.g. "text/html")
        headers: Optional extra headers merged over the defaults
        deadline_monotonic: Optional absolute monotonic deadline for the call
        retry_dns: See ``request``
        landed: When given, the address that answered (after redirects) is
            appended to it on success, or ``url`` when that is not known.
        redirect_guard: When given, a redirect to an address it returns False
            for is refused before it is followed, and the call returns None.
        max_bytes: Optional response byte cap passed through to ``request``.

    Returns:
        Decoded response body as text, or None on failure.
    """
    merged = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        merged.update(headers)
    # Passed only when set, so the plain call stays what it always was.
    bounds: Dict[str, Any] = {}
    if deadline_monotonic is not None:
        bounds["deadline_monotonic"] = deadline_monotonic
    if cancel is not None:
        bounds["cancel"] = cancel
    if not retry_dns:
        bounds["retry_dns"] = False
    if landed is not None:
        bounds["return_headers"] = True
    if redirect_guard is not None:
        bounds["redirect_guard"] = redirect_guard
    if public_only:
        bounds["public_only"] = True
    if max_bytes is not None:
        bounds["max_bytes"] = max_bytes
    if decode_errors != "strict":
        bounds["decode_errors"] = decode_errors
    if ip_validator is not None:
        bounds["ip_validator"] = ip_validator
    try:
        answer = request(
            "GET", url, headers=merged, timeout=timeout, retries=retries, raw=True,
            **bounds,
        )
    except HTTPError as e:
        log(f"get_text failed ({e}): {url}")
        return None
    if landed is None:
        return answer
    body, response_headers = answer
    landed.append(getattr(response_headers, "url", "") or url)
    return body


class RateLimiter:
    """Thread-safe token-bucket throttle for an endpoint family.

    The keyless source tiers run under the pipeline's ThreadPoolExecutor, so a
    multi-subquery run can fire many requests at the same host at once. A bare
    per-request retry budget does not prevent that stampede — it only reacts
    after a 429. A token bucket bounds the *sustained* rate while still allowing
    a short burst, so legitimate parallelism is preserved (unlike a strict
    min-interval gate that would serialize every concurrent caller and could
    push later futures past their result timeouts).

    ``rate_per_sec`` tokens refill per second; ``burst`` is the bucket capacity
    (max simultaneous calls before throttling kicks in). The lock is released
    while sleeping so waiting threads don't serialize on each other.
    """

    def __init__(
        self,
        rate_per_sec: float,
        burst: int | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.rate = rate_per_sec
        # Injectable so tests never sleep (#1342); None means time.sleep.
        self._sleep = sleep
        self.capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()
        # Threads currently blocked in acquire(). Callers that wait on a batch
        # of throttled futures size their timeouts from this queue depth.
        self._waiting = 0

    @property
    def waiting(self) -> int:
        """Threads currently blocked in :meth:`acquire`."""
        with self._lock:
            return self._waiting

    def acquire(
        self,
        *,
        deadline_monotonic: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
    ) -> None:
        """Consume one token, blocking only when the bucket is empty."""
        queued = False
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DeadlineExceeded()
                with self._lock:
                    now = time.monotonic()
                    # Clamp elapsed to >= 0: a backward clock reading must never
                    # drive tokens negative (which would spin this loop forever).
                    elapsed = max(0.0, now - self._last)
                    self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                    self._last = now
                    if deadline_monotonic is not None and now >= deadline_monotonic:
                        raise DeadlineExceeded()
                    if self._tokens >= 1.0:
                        self._tokens -= 1.0
                        return
                    wait = (1.0 - self._tokens) / self.rate
                    if deadline_monotonic is not None:
                        remaining = deadline_monotonic - now
                        if remaining <= 0 or wait > remaining:
                            raise DeadlineExceeded()
                        sleep_for = min(wait, remaining)
                    else:
                        sleep_for = wait
                    if not queued:
                        self._waiting += 1
                        queued = True
                if self._sleep is not None:
                    self._sleep(min(sleep_for, 0.05) if cancel is not None else sleep_for)
                elif cancel is not None:
                    if cancel.wait(min(sleep_for, 0.05)):
                        raise DeadlineExceeded()
                else:
                    time.sleep(sleep_for)
        finally:
            if queued:
                with self._lock:
                    self._waiting -= 1


# Shared across all keyless Reddit tiers (site search, listing, shreddit) so their
# combined fan-out is throttled as one family. Burst lets the parallel
# enrichment workers proceed; sustained rate caps the stampede.
# 1 req/sec is slow enough that home IPs survive search + listing + shreddit
# fan-out; raise LAST30DAYS_REDDIT_KEYLESS_RATE to trade 429s for wall-clock.
REDDIT_KEYLESS_RATE_ENV = "LAST30DAYS_REDDIT_KEYLESS_RATE"
DEFAULT_REDDIT_KEYLESS_RATE = 1.0
DEFAULT_REDDIT_KEYLESS_BURST = 2
_REDDIT_429_RETRY_SLEEP_SEC = 1.0
_REDDIT_429_RETRY_JITTER_SEC = 0.5


def parse_reddit_keyless_rate(raw: Optional[str]) -> float:
    """Parse LAST30DAYS_REDDIT_KEYLESS_RATE; invalid/non-positive -> default."""
    text = (raw or "").strip()
    if not text:
        return DEFAULT_REDDIT_KEYLESS_RATE
    try:
        rate = float(text)
    except (TypeError, ValueError):
        return DEFAULT_REDDIT_KEYLESS_RATE
    if not math.isfinite(rate) or rate <= 0:
        return DEFAULT_REDDIT_KEYLESS_RATE
    return rate


def make_reddit_keyless_limiter(
    environ: Optional[Dict[str, str]] = None,
) -> RateLimiter:
    envmap = os.environ if environ is None else environ
    return RateLimiter(
        rate_per_sec=parse_reddit_keyless_rate(envmap.get(REDDIT_KEYLESS_RATE_ENV)),
        burst=DEFAULT_REDDIT_KEYLESS_BURST,
    )


REDDIT_KEYLESS_LIMITER = make_reddit_keyless_limiter()


def _sync_reddit_keyless_rate() -> None:
    """Apply a process-env override without resetting in-flight tokens."""
    rate = parse_reddit_keyless_rate(os.environ.get(REDDIT_KEYLESS_RATE_ENV))
    if REDDIT_KEYLESS_LIMITER.rate != rate:
        REDDIT_KEYLESS_LIMITER.rate = rate


def _failures_are_429(failures: list[HTTPError]) -> bool:
    if not failures:
        return False
    last = failures[-1]
    return last.status_code == 429 or last.outcome_state == health.RATE_LIMITED


def _sleep_reddit_429_retry() -> None:
    """Short jittered pause before the single in-lane 429 retry."""
    time.sleep(
        _REDDIT_429_RETRY_SLEEP_SEC
        + random.uniform(0.0, _REDDIT_429_RETRY_JITTER_SEC)
    )


# Run-scoped memo for keyless Reddit GETs. Subreddit listing partials, site
# search pages, arctic supplements, and shreddit comment pages depend only on
# the subreddit, sort, or raw topic, and the Reddit lane is dispatched with the
# raw topic for every subquery, so a four-subquery run requested each of them
# four times.
# Memoizing successful bodies for the life of one command turns ~184 requests
# into ~50 on the measured 2026-08-31 run shape. Concurrent requesters for the
# same URL wait on the first fetch instead of issuing their own (all four
# subquery streams start at once, so a result-only cache would miss).
REDDIT_KEYLESS_MEMO_MAX = 512
_REDDIT_KEYLESS_MEMO: "OrderedDict[str, str]" = OrderedDict()
_REDDIT_KEYLESS_INFLIGHT: Dict[str, threading.Event] = {}
_REDDIT_KEYLESS_MEMO_LOCK = threading.Lock()


# Queue depth only counts threads already blocked in acquire(). The other
# lanes' workers submit their requests as they go, so a batch's last fetch can
# start well after the depth seen at wait time. This flat allowance covers
# that (the 2026-08-31 smoke lost three feeds at ~35s with the depth term
# alone; a full run's ~50 distinct keyless requests take ~50s at 1 req/s).
REDDIT_KEYLESS_CONTENTION_SECONDS = 45.0


def reddit_keyless_wait_allowance(batch_size: int) -> float:
    """Seconds a batch of *batch_size* throttled fetches may spend waiting for tokens.

    Every keyless Reddit lane in a run shares one bucket, so a lane's futures
    can sit behind other lanes' requests before their own fetch starts. Size
    per-future result timeouts as ``base + this`` instead of a fixed number;
    at 1 req/s a fixed 20-second timeout expired on real runs while the fetch
    was still queued (issue #985 follow-up).
    """
    _sync_reddit_keyless_rate()
    limiter = REDDIT_KEYLESS_LIMITER
    rate = limiter.rate if limiter.rate > 0 else 1.0
    return (limiter.waiting + max(0, batch_size)) / rate + REDDIT_KEYLESS_CONTENTION_SECONDS


def reset_reddit_keyless_memo() -> None:
    """Forget memoized keyless Reddit bodies. Called once per command, and by tests."""
    with _REDDIT_KEYLESS_MEMO_LOCK:
        _REDDIT_KEYLESS_MEMO.clear()
        _REDDIT_KEYLESS_INFLIGHT.clear()


def _reddit_memo_get(url: str) -> Optional[str]:
    with _REDDIT_KEYLESS_MEMO_LOCK:
        text = _REDDIT_KEYLESS_MEMO.get(url)
        if text is not None:
            _REDDIT_KEYLESS_MEMO.move_to_end(url)
        return text


def _reddit_memo_put(url: str, text: str) -> None:
    with _REDDIT_KEYLESS_MEMO_LOCK:
        _REDDIT_KEYLESS_MEMO[url] = text
        _REDDIT_KEYLESS_MEMO.move_to_end(url)
        while len(_REDDIT_KEYLESS_MEMO) > REDDIT_KEYLESS_MEMO_MAX:
            _REDDIT_KEYLESS_MEMO.popitem(last=False)


def reddit_keyless_get_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
    accept: str = "*/*",
    headers: Optional[Dict[str, str]] = None,
    validate: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[str]:
    """get_text for the keyless Reddit tiers, memoized per run and throttled.

    Same contract as :func:`get_text` (returns None on any failure) but a URL
    already fetched this command is served from the run memo without spending
    a limiter token, concurrent requesters for one URL share the in-flight
    fetch, and cold fetches are spaced via :data:`REDDIT_KEYLESS_LIMITER` so a
    broad multi-query run does not stampede Reddit's keyless endpoints.

    ``validate`` lets a caller reject a fetched body before it is memoized. It
    returns None for a body the caller recognizes, or a short reason string.
    A rejected body is recorded into the failure sink as schema drift and the
    call returns None, so an HTTP 200 challenge page is neither reported as a
    clean empty result nor served from the memo to later streams.
    """
    cached = _reddit_memo_get(url)
    if cached is not None:
        return cached
    # Elect one owner per URL. A waiter whose owner failed re-enters the
    # election rather than fetching un-gated, so a failed fetch costs one
    # retry for the whole group, not one per waiter.
    for _round in range(3):
        with _REDDIT_KEYLESS_MEMO_LOCK:
            cached = _REDDIT_KEYLESS_MEMO.get(url)
            if cached is not None:
                return cached
            gate = _REDDIT_KEYLESS_INFLIGHT.get(url)
            owner = gate is None
            if owner:
                gate = threading.Event()
                _REDDIT_KEYLESS_INFLIGHT[url] = gate
        if owner:
            break
        # The owner may itself be queued in the shared bucket; wait for that
        # queue, not just for one socket timeout.
        gate.wait(
            timeout=timeout * max(1, retries) + reddit_keyless_wait_allowance(1)
        )
        cached = _reddit_memo_get(url)
        if cached is not None:
            return cached
    else:
        # Three failed owners in a row: give up quietly rather than pile on.
        return None
    try:
        _sync_reddit_keyless_rate()
        REDDIT_KEYLESS_LIMITER.acquire()
        text = get_text(url, timeout=timeout, retries=retries, accept=accept, headers=headers)
        if text is not None and validate is not None:
            problem = validate(text)
            if problem:
                _record_failure(HTTPError(
                    f"Unrecognized response ({problem}): {url}",
                    outcome_state=health.SCHEMA_DRIFT,
                ))
                return None
        if text is not None:
            _reddit_memo_put(url, text)
        return text
    finally:
        with _REDDIT_KEYLESS_MEMO_LOCK:
            _REDDIT_KEYLESS_INFLIGHT.pop(url, None)
        gate.set()


def reddit_keyless_get_text_retry_429(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    accept: str = "*/*",
    headers: Optional[Dict[str, str]] = None,
    validate: Optional[Callable[[str], Optional[str]]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Limiter-throttled GET with one extra limiter-respecting retry on 429.

    Returns ``(body, error)``. The first attempt is captured locally so a
    recovered 429 is not left in the pipeline sink. A second 429, or any
    non-429 miss, is recorded as before. Internal ``get_text`` retries are
    skipped (``retries=1``) so the in-lane retry is the one that re-acquires
    the bucket. ``validate`` is passed through to
    :func:`reddit_keyless_get_text`; a rejected body is a non-429 miss.
    """
    # retries=1 on purpose: letting request() sleep out a 42-60s
    # x-ratelimit-reset inside a lane worker starves the whole batch (the
    # 2026-08-31 smoke lost 14 feeds to future timeouts with retries=2 versus
    # 6 with 1). A keyless 429 fails fast, the lane retries once after a short
    # jittered pause through the bucket, and the memo keeps the other streams
    # from re-requesting the same URL.
    kwargs: Dict[str, Any] = {
        "timeout": timeout,
        "retries": 1,
        "accept": accept,
        "headers": headers,
        "validate": validate,
    }
    with capture_failures() as first:
        text = reddit_keyless_get_text(url, **kwargs)
    if text is not None:
        return text, None
    if _failures_are_429(first):
        _sleep_reddit_429_retry()
        with tee_failures() as second:
            text = reddit_keyless_get_text(url, **kwargs)
        if text is not None:
            return text, None
        err = second[-1] if second else (first[-1] if first else None)
        return None, str(err) if err is not None else "no response"
    for err in first:
        _record_failure(err)
    err = first[-1] if first else None
    return None, str(err) if err is not None else "no response"


# --- Logged-in Reddit session transport ----------------------------------
#
# The opt-in session (lib/reddit_session.py) is kept apart from the keyless
# family: its own limiter (so a keyless stampede never spends the account's
# pacing, and the reverse), and its own memo, so a page fetched with the
# cookie is never served to a logged-out caller and a logged-out page never
# to the session. Failures stay out of the stream's failure sink: one Reddit
# layer failing is a detail note, not the source's outcome.
REDDIT_SESSION_RATE = 1.0
REDDIT_SESSION_BURST = 2
REDDIT_SESSION_LIMITER = RateLimiter(REDDIT_SESSION_RATE, burst=REDDIT_SESSION_BURST)
REDDIT_SESSION_MEMO_MAX = 128
_REDDIT_SESSION_MEMO: "OrderedDict[str, tuple[int, str]]" = OrderedDict()
_REDDIT_SESSION_MEMO_LOCK = threading.Lock()


def reset_reddit_session_memo() -> None:
    with _REDDIT_SESSION_MEMO_LOCK:
        _REDDIT_SESSION_MEMO.clear()


def reddit_only_redirect(url: str) -> bool:
    """Redirect guard for session requests: https on reddit.com or a subdomain."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "reddit.com" or host.endswith(".reddit.com"))


def reddit_session_get(
    url: str,
    cookie_header: str,
    *,
    accept: str = "text/html",
    timeout: float = 20,
    deadline_monotonic: Optional[float] = None,
    limiter: Optional[RateLimiter] = None,
) -> tuple[Optional[int], Optional[str], bool]:
    """GET ``url`` with the session cookie: ``(status, body, from_memo)``.

    A 200 is memoized per URL in the session namespace; an error status
    comes back with its body (a 403 page says whether Reddit logged the
    session out) and is not memoized. ``status`` is None when no answer
    arrived. Never raises, and never puts the cookie in a log line or an
    error string: ``request`` logs only the URL.
    """
    if not reddit_only_redirect(url):
        return None, None, False
    with _REDDIT_SESSION_MEMO_LOCK:
        cached = _REDDIT_SESSION_MEMO.get(url)
        if cached is not None:
            _REDDIT_SESSION_MEMO.move_to_end(url)
            return cached[0], cached[1], True
    (limiter or REDDIT_SESSION_LIMITER).acquire()
    headers = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": cookie_header,
    }
    bounds: Dict[str, Any] = {"redirect_guard": reddit_only_redirect}
    if deadline_monotonic is not None:
        bounds["deadline_monotonic"] = deadline_monotonic
    with capture_failures():
        try:
            body = request(
                "GET", url, headers=headers, timeout=timeout, retries=1,
                max_429_retries=0, raw=True, **bounds,
            )
        except HTTPError as error:
            return error.status_code, error.body, False
        except Exception:
            return None, None, False
    text = body if isinstance(body, str) else ""
    with _REDDIT_SESSION_MEMO_LOCK:
        _REDDIT_SESSION_MEMO[url] = (200, text)
        _REDDIT_SESSION_MEMO.move_to_end(url)
        while len(_REDDIT_SESSION_MEMO) > REDDIT_SESSION_MEMO_MAX:
            _REDDIT_SESSION_MEMO.popitem(last=False)
    return 200, text, False


def scrapecreators_headers(token: str) -> Dict[str, str]:
    """Build ScrapeCreators request headers (x-api-key + JSON content type)."""
    return {
        "x-api-key": token,
        "Content-Type": "application/json",
    }


def get_reddit_json(path: str, timeout: int = DEFAULT_TIMEOUT, retries: int = MAX_RETRIES) -> Dict[str, Any]:
    """Fetch Reddit thread JSON.

    Args:
        path: Reddit path (e.g., /r/subreddit/comments/id/title)
        timeout: HTTP timeout per attempt in seconds
        retries: Number of retries on failure

    Returns:
        Parsed JSON response
    """
    # Ensure path starts with /
    if not path.startswith('/'):
        path = '/' + path

    # Remove trailing slash and add .json
    path = path.rstrip('/')
    if not path.endswith('.json'):
        path = path + '.json'

    url = f"https://www.reddit.com{path}?raw_json=1"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    return get(url, headers=headers, timeout=timeout, retries=retries)
