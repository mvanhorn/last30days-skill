"""Public-host checks for fetching addresses taken from remote content.

``lib/http.py`` follows any redirect and resolves any name. A lane that
fetches an address it read from a page, a sitemap or a guess about a
company's site uses these checks so that remote content cannot point it at a
service on the user's own network: the address must be http(s), on the site
the lane meant to read, and every address its name resolves to must be a
public one. ``redirect_guard`` gives the same check to ``http.get_text`` so a
redirect is refused before it is followed.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse


def _host_of(url: str) -> str:
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    return (parsed.hostname or "").rstrip(".").lower()


def _parse_ip_literal(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    bare = name.split("%", 1)[0]
    try:
        return ipaddress.ip_address(bare)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(bare)
    except OSError:
        return None
    return ipaddress.IPv4Address(packed)


def _same_site(host: str, site_host: str) -> bool:
    base = site_host.rstrip(".").lower().removeprefix("www.")
    cand = host.rstrip(".").lower().removeprefix("www.")
    if not base or not cand:
        return False
    if _parse_ip_literal(base) is not None or _parse_ip_literal(cand) is not None:
        return cand == base
    return cand == base or cand.endswith("." + base)


_NAT64_WELL_KNOWN = ipaddress.IPv6Network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.IPv6Network("64:ff9b:1::/48")


def _is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        not address.is_global
        or address.is_multicast
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        for embedded in (
            address.ipv4_mapped,
            address.sixtofour,
            address.teredo[1] if address.teredo else None,
        ):
            if embedded is not None and not _is_public_ip(embedded):
                return False
        if address in _NAT64_WELL_KNOWN or address in _NAT64_LOCAL:
            embedded = ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
            if not _is_public_ip(embedded):
                return False
    return True


def is_public_address_syntax(url: str) -> bool:
    """Zero-DNS check: ``url`` is http(s) with no userinfo, not localhost, and
    not a non-public IP literal."""
    if not isinstance(url, str) or not url or url != url.strip():
        return False
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    name = (parsed.hostname or "").rstrip(".").lower()
    if not name or name == "localhost" or name.endswith(".localhost"):
        return False
    ip = _parse_ip_literal(name)
    if ip is not None:
        return _is_public_ip(ip)
    return True


def public_any_url(url: str, *, resolves: Callable[[str], bool] | None = None) -> bool:
    """Whether ``url`` is an http(s) address with no userinfo whose host is a
    public IP or resolves exclusively to public IPs (any site)."""
    if not is_public_address_syntax(url):
        return False
    name = (urlparse(url).hostname or "").rstrip(".").lower()
    ip = _parse_ip_literal(name)
    if ip is not None:
        return _is_public_ip(ip)
    return (resolves or resolves_public)(name)


def public_url(url: str, host: str, *, resolves: Callable[[str], bool] | None = None) -> bool:
    """Whether ``url`` is an http(s) address on the site of ``host`` whose host
    is a name, not an address on a private, loopback or reserved network.

    ``resolves`` decides whether a name resolves only to public addresses;
    it defaults to ``resolves_public``.
    """
    if not is_public_address_syntax(url):
        return False
    name = (urlparse(url).hostname or "").rstrip(".").lower()
    if not _same_site(name, host):
        return False
    ip = _parse_ip_literal(name)
    if ip is not None:
        return _is_public_ip(ip)
    return (resolves or resolves_public)(name)


@functools.lru_cache(maxsize=256)
def resolves_public(name: str) -> bool:
    """Whether every address ``name`` resolves to is a public one. A name that
    points at a private network (an internal subdomain) is not fetched; a
    name that does not resolve is not either."""
    try:
        answers = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    addresses = set()
    for answer in answers:
        try:
            addresses.add(ipaddress.ip_address(answer[4][0].split("%", 1)[0]))
        except ValueError:
            return False
    return bool(addresses) and all(_is_public_ip(address) for address in addresses)


def redirect_guard(url: str, *, resolves: Callable[[str], bool] | None = None) -> Callable[[str], bool]:
    """The redirect check for a fetch of ``url``: it may be redirected only to
    an address on the same site that is not on a private network."""
    host = _host_of(url)
    return lambda target: public_url(target, host, resolves=resolves)
