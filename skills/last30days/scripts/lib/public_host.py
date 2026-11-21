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

from . import jobs


def public_url(url: str, host: str, *, resolves: Callable[[str], bool] | None = None) -> bool:
    """Whether ``url`` is an http(s) address on the site of ``host`` whose host
    is a name, not an address on a private, loopback or reserved network.

    ``resolves`` decides whether a name resolves only to public addresses;
    it defaults to ``resolves_public``.
    """
    parsed = urlparse(url)
    name = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not name or name == "localhost" or name.endswith(".localhost"):
        return False
    if not jobs._same_site(name, host):
        return False
    try:
        return ipaddress.ip_address(name).is_global
    except ValueError:
        return (resolves or resolves_public)(name)


@functools.lru_cache(maxsize=256)
def resolves_public(name: str) -> bool:
    """Whether every address ``name`` resolves to is a public one. A name that
    points at a private network (an internal subdomain) is not fetched; a
    name that does not resolve is not either."""
    try:
        answers = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    addresses = set()
    for answer in answers:
        try:
            addresses.add(ipaddress.ip_address(answer[4][0].split("%", 1)[0]))
        except ValueError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def redirect_guard(url: str, *, resolves: Callable[[str], bool] | None = None) -> Callable[[str], bool]:
    """The redirect check for a fetch of ``url``: it may be redirected only to
    an address on the same site that is not on a private network."""
    host = jobs._host(url)
    return lambda target: public_url(target, host, resolves=resolves)
