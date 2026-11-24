"""Which hiring platform a careers page runs on, read off the page itself.

A large company's careers site is usually one of a few hosted platforms under
the company's own host name. The page names its platform in its markup: the
API host and site number of Oracle Recruiting Cloud, Phenom's ``phApp`` page
object, TalentBrew's asset host, Jibe's asset host, Eightfold's group id, a
link to a Kula board (``careers.kula.ai/<slug>``). The
detector reads that and returns the board to read, so no company is added by
hand. Nothing here makes a request.

Each board is returned as ``(provider, entry)``, where ``entry`` holds what
the platform's reader needs (``board`` is always the host to call) and has
the shape of a registry entry in ``lib.jobs``.
"""

from __future__ import annotations

from collections import OrderedDict
import functools
import ipaddress
import re
import socket
import threading
import time
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

from .. import public_host

PROVIDER_ORACLE = "oracle"
PROVIDER_JIBE = "jibe"
PROVIDER_PHENOM = "phenom"
PROVIDER_TALENTBREW = "talentbrew"
PROVIDER_EIGHTFOLD = "eightfold"
PROVIDER_KULA = "kula"
KULA_HOST = "careers.kula.ai"

MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_HTML_CHARS = 5 * 1024 * 1024
DNS_CACHE_TTL_SECONDS = 5.0
DNS_CACHE_MAXSIZE = 256

# The order platforms are tried in: a page names one platform's own API
# before another's asset (a Phenom page can link a Workday apply URL, which
# ``lib.jobs`` reads on its own first).
PLATFORMS = (
    PROVIDER_ORACLE, PROVIDER_PHENOM, PROVIDER_EIGHTFOLD, PROVIDER_JIBE, PROVIDER_TALENTBREW,
    PROVIDER_KULA,
)

_ORACLE_HOST = r"[a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com"
_ORACLE_HOST_RE = re.compile(rf"^{_ORACLE_HOST}$", re.I)
_SECOND_LEVEL_PUBLIC_LABELS = frozenset({
    "ac", "co", "com", "edu", "gob", "gov", "mil", "ne", "net", "or", "org",
})
_MULTI_TENANT_SUFFIXES = frozenset({
    "appspot.com", "azurewebsites.net", "blogspot.com", "cloudfront.net",
    "firebaseapp.com", "fly.dev", "github.io", "gitlab.io", "herokuapp.com",
    "myshopify.com", "netlify.app", "onrender.com", "pages.dev",
    "s3.amazonaws.com", "squarespace.com", "vercel.app", "web.app",
    "wixsite.com", "wordpress.com", "workers.dev",
})
_RESERVED_HOST_SUFFIXES = frozenset({"localhost", "local", "internal", "localdomain", "arpa"})
_VALID_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)
_VALID_TLD_RE = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}")
_UNSAFE_URL_CHAR_RE = re.compile(r"[\s\x00-\x1f\x7f\\]")
_HTML_ATTR_RE = re.compile(
    r'([A-Za-z_:][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'=<>`]+)))?'
)
_START_TAG_RE = re.compile(r"<[a-zA-Z][a-zA-Z0-9:-]*(?:\s[^<>]*)?>")
_SCRIPT_OPEN_RE = re.compile(r"<script\b([^<>]*)>", re.I)
_SCRIPT_CLOSE_RE = re.compile(r"</script(?:\s[^<>]*)?>", re.I)
_META_TAG_RE = re.compile(r"<meta\b([^<>]*)>", re.I)
_LINK_TAG_RE = re.compile(r"<link\b([^<>]*)>", re.I)
_TITLE_OPEN_RE = re.compile(r"<title\b[^<>]*>", re.I)
_TITLE_CLOSE_RE = re.compile(r"</title(?:\s[^<>]*)?>", re.I)
_ORACLE_SITE_NUMBER_VALUE_RE = re.compile(r"^CX_\d+$")
_ORACLE_SITE_LINK = re.compile(
    rf"https?://({_ORACLE_HOST})(?::443)?/hcmUI/CandidateExperience/[A-Za-z-]+/sites/(CX_\d+)", re.I,
)
_ORACLE_SITE_NUMBER = re.compile(r"siteNumber=(CX_\d+)")
_PHENOM_BASE = re.compile(r'"baseUrl"\s*:\s*"(https://[^"/]+/([a-z]{2,8})/([a-z]{2})/)"')
_EIGHTFOLD_GROUP = re.compile(r'_EF_GROUP_ID\s*=\s*"([A-Za-z0-9.-]+)"')
# A Kula board: the company's slug on Kula's own careers host. Kula's assets
# are on other hosts (``careers-cdn.kula.ai``), so a slug is never ``_next``.
KULA_BOARD_LINK = r"careers\.kula\.ai/([A-Za-z0-9][A-Za-z0-9_-]*)"
_HTTPS_PREFIX_RE = re.compile(r"\s*(https://[^<\s]+)", re.I)


def parse_html_attrs(raw: str) -> dict[str, str]:
    """Parse attributes from a single matched HTML tag header in linear time."""
    attrs: dict[str, str] = {}
    for match in _HTML_ATTR_RE.finditer(raw):
        name = match.group(1).lower()
        val = match.group(2) if match.group(2) is not None else (
            match.group(3) if match.group(3) is not None else (match.group(4) or "")
        )
        if name not in attrs:
            attrs[name] = unescape(val)
    return attrs


def extract_script_bodies(html: str) -> list[str]:
    """Extract ``<script>...</script>`` bodies in strictly linear time without
    using offsets from a lowercased copy (which shift on chars like U+0130)."""
    if not html:
        return []
    bounded = html[:MAX_HTML_CHARS]
    if not _SCRIPT_CLOSE_RE.search(bounded):
        return []
    bodies: list[str] = []
    cursor = 0
    for match in _SCRIPT_OPEN_RE.finditer(bounded):
        if match.start() < cursor:
            continue
        close = _SCRIPT_CLOSE_RE.search(bounded, match.end())
        if close is None:
            break
        bodies.append(bounded[match.end():close.start()])
        cursor = close.end()
    return bodies


def extract_jsonld_blocks(html: str) -> list[str]:
    """Extract ``<script type="application/ld+json">`` blocks in strictly linear time."""
    if not html:
        return []
    bounded = html[:MAX_HTML_CHARS]
    if "ld+json" not in bounded.lower() or not _SCRIPT_CLOSE_RE.search(bounded):
        return []
    blocks: list[str] = []
    cursor = 0
    for match in _SCRIPT_OPEN_RE.finditer(bounded):
        if match.start() < cursor:
            continue
        attrs_raw = match.group(1)
        if "ld+json" not in attrs_raw.lower():
            continue
        script_type = parse_html_attrs(attrs_raw).get("type", "").split(";", 1)[0].strip().lower()
        if script_type != "application/ld+json":
            continue
        close = _SCRIPT_CLOSE_RE.search(bounded, match.end())
        if close is None:
            break
        blocks.append(bounded[match.end():close.start()])
        cursor = close.end()
    return blocks


def extract_meta_contents(html: str, attr_name: str, attr_value: str) -> list[str]:
    """Extract ``content`` values from matching ``<meta>`` tags in strictly linear time."""
    if not html:
        return []
    bounded = html[:MAX_HTML_CHARS]
    want_name = attr_name.lower()
    want_value = attr_value.lower()
    lowered = bounded.lower()
    if "<meta" not in lowered or want_value not in lowered:
        return []
    found: list[str] = []
    for match in _META_TAG_RE.finditer(bounded):
        attrs_raw = match.group(1)
        if want_value not in attrs_raw.lower():
            continue
        attrs = parse_html_attrs(attrs_raw)
        if attrs.get(want_name, "").strip().lower() == want_value:
            content = attrs.get("content", "").strip()
            if content:
                found.append(content)
    return found


def extract_title_texts(html: str) -> list[str]:
    """Extract ``<title>...</title>`` text contents in strictly linear time."""
    if not html:
        return []
    bounded = html[:MAX_HTML_CHARS]
    if not _TITLE_CLOSE_RE.search(bounded):
        return []
    titles: list[str] = []
    cursor = 0
    for match in _TITLE_OPEN_RE.finditer(bounded):
        if match.start() < cursor:
            continue
        close = _TITLE_CLOSE_RE.search(bounded, match.end())
        if close is None:
            break
        raw = unescape(bounded[match.end():close.start()]).strip()
        if raw:
            titles.append(raw)
        cursor = close.end()
    return titles


def page_host(url: str) -> str:
    """The host of a page URL, lowercased and without a port or trailing dot."""
    raw = (url or "").strip()
    if not raw or "\\" in raw or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return ""
    try:
        parsed = urlparse(raw)
        _ = parsed.port
    except ValueError:
        return ""
    if parsed.username is not None or parsed.password is not None:
        return ""
    raw_host = (parsed.hostname or "").lower()
    if not raw_host or "\\" in raw_host or ".." in raw_host or raw_host.startswith("."):
        return ""
    return raw_host.rstrip(".")


@functools.lru_cache(maxsize=1024)
def _is_safe_public_host(raw_host: str) -> bool:
    if not raw_host or ".." in raw_host or raw_host.startswith("."):
        return False
    host = raw_host.rstrip(".")
    if not host or len(host) > 253 or not _VALID_HOST_RE.fullmatch(host):
        return False
    if public_host._parse_ip_literal(host) is not None:
        return False
    labels = host.split(".")
    if labels[-1] in _RESERVED_HOST_SUFFIXES or not _VALID_TLD_RE.fullmatch(labels[-1]):
        return False
    return True


def is_safe_public_url(url: str) -> bool:
    """Zero-DNS check: http(s) URL with standard port, no userinfo/backslash,
    not an IP literal or reserved/internal domain."""
    if not isinstance(url, str) or not url or _UNSAFE_URL_CHAR_RE.search(url):
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        return False
    if not parsed.netloc.isascii() or "%" in parsed.netloc:
        return False
    return _is_safe_public_host((parsed.hostname or "").lower())


@functools.lru_cache(maxsize=1024)
def registrable_domain(host: str) -> str:
    """Return the registrable site domain (eTLD+1), or '' for IP literals,
    malformed hosts, single-label hosts, or bare public suffixes."""
    raw = (host or "").strip().lower()
    if not raw or ".." in raw or raw.startswith("."):
        return ""
    h = raw.rstrip(".")
    if not h or len(h) > 253 or not _VALID_HOST_RE.fullmatch(h):
        return ""
    if public_host._parse_ip_literal(h) is not None:
        return ""
    parts = h.removeprefix("www.").split(".")
    if len(parts) < 2 or parts[-1] in _RESERVED_HOST_SUFFIXES or not _VALID_TLD_RE.fullmatch(parts[-1]):
        return ""
    if len(parts) >= 3 and ".".join(parts[-3:]) in _MULTI_TENANT_SUFFIXES:
        return ".".join(parts[-4:]) if len(parts) >= 4 else ""
    tail2 = ".".join(parts[-2:])
    if tail2 in _MULTI_TENANT_SUFFIXES or (
        len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL_PUBLIC_LABELS
    ):
        return ".".join(parts[-3:]) if len(parts) >= 3 else ""
    return tail2


def registrable_label(host: str) -> str:
    """Return the leftmost label of ``registrable_domain(host)``, or ''."""
    reg = registrable_domain(host)
    return reg.split(".", 1)[0] if reg else ""


def same_host_or_subdomain(host: str, site_host: str) -> bool:
    """Whether ``host`` is ``site_host`` (ignoring ``www.``) or a strict
    subdomain of it, rejecting IP literals and public-suffix-only bases."""
    if not registrable_domain(host) or not registrable_domain(site_host):
        return False
    base = site_host.strip().rstrip(".").lower().removeprefix("www.")
    cand = host.strip().rstrip(".").lower().removeprefix("www.")
    return bool(base) and (cand == base or cand.endswith("." + base))


def _resolve_all_public(name: str) -> bool:
    try:
        answers = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return False
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for answer in answers:
        try:
            addresses.add(ipaddress.ip_address(answer[4][0].split("%", 1)[0]))
        except ValueError:
            return False
    return bool(addresses) and all(public_host._is_public_ip(address) for address in addresses)


class _BoundedPublicResolver:
    """Thread-safe bounded-TTL DNS resolver verifying every address is public."""

    def __init__(
        self,
        *,
        maxsize: int = DNS_CACHE_MAXSIZE,
        ttl_seconds: float = DNS_CACHE_TTL_SECONDS,
    ) -> None:
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[bool, float]] = OrderedDict()

    def cache_clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __call__(self, name: str) -> bool:
        raw_host = (name or "").strip().lower()
        if not raw_host or ".." in raw_host or raw_host.startswith("."):
            return False
        bare = raw_host.rstrip(".")
        if not bare or bare == "localhost" or bare.endswith(".localhost"):
            return False
        literal = public_host._parse_ip_literal(bare)
        if literal is not None:
            return public_host._is_public_ip(literal)
        dns_host = raw_host
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(dns_host)
            if cached is not None:
                verdict, expires_at = cached
                if now < expires_at:
                    self._cache.move_to_end(dns_host)
                    return verdict
                del self._cache[dns_host]
        verdict = _resolve_all_public(dns_host)
        expires_at = time.monotonic() + self.ttl_seconds
        with self._lock:
            self._cache[dns_host] = (verdict, expires_at)
            self._cache.move_to_end(dns_host)
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)
        return verdict


resolves_public = _BoundedPublicResolver()


def public_site_url(
    url: str,
    site_host: str,
    *,
    resolves: Callable[[str], bool] | None = None,
    require_https: bool = False,
) -> bool:
    """Whether ``url`` is a safe standard-port http(s) URL on the site of
    ``site_host`` whose host resolves exclusively to public IPs."""
    if not is_safe_public_url(url):
        return False
    parsed = urlparse(url)
    if require_https and parsed.scheme.lower() != "https":
        return False
    target_host = page_host(url)
    if not target_host or not same_host_or_subdomain(target_host, site_host):
        return False
    dns_host = (parsed.hostname or "").lower()
    return (resolves or resolves_public)(dns_host or target_host)


def public_redirect_guard(url: str) -> Callable[[str], bool]:
    """Stateless redirect guard allowing any safe public URL while forbidding
    ``https -> http`` downgrades from an ``https://`` origin."""
    if not is_safe_public_url(url):
        return lambda _target: False
    if urlparse(url).scheme.lower() != "https":
        return is_safe_public_url
    return lambda target: is_safe_public_url(target) and urlparse(target).scheme.lower() == "https"


def same_site_redirect_guard(
    url: str,
    *,
    resolves: Callable[[str], bool] | None = None,
) -> Callable[[str], bool]:
    """Stateless redirect guard allowing only safe standard-port http(s)
    same-site URLs, forbidding ``https -> http`` downgrades from an
    ``https://`` origin.

    When ``resolves`` is omitted, no live DNS lookup runs inside the guard so
    transient DNS errors remain retryable; callers pair this guard with
    ``http.*(..., public_only=True)`` which resolves and pins public IPs at
    connect time.
    """
    if not is_safe_public_url(url):
        return lambda _target: False
    origin_host = page_host(url)
    if not origin_host:
        return lambda _target: False
    require_https = urlparse(url).scheme.lower() == "https"
    resolver = resolves if resolves is not None else (lambda _host: True)
    return lambda target: public_site_url(
        target, origin_host, resolves=resolver, require_https=require_https,
    )


def detect_platform(html: str, page_url: str) -> tuple[str, dict[str, Any]] | None:
    """The platform board a careers page names, or None.

    ``page_url`` is the address that answered with ``html`` (after
    redirects): a platform served under the company's own host is called on
    that host.
    """
    if not html:
        return None
    bounded = html[:MAX_HTML_CHARS]
    host = page_host(page_url)
    for provider in PLATFORMS:
        entry = _DETECTORS[provider](bounded, host)
        if entry:
            return provider, {"provider": provider, **entry}
    return None


def eightfold_career_url(html: str) -> str:
    """The Eightfold board a careers landing page points at (Microsoft's
    ``careers-eightFoldCareerUrl`` element), or ''."""
    bounded = (html or "")[:MAX_HTML_CHARS]
    if "eightfoldcareerurl" not in bounded.lower():
        return ""
    for match in _START_TAG_RE.finditer(bounded):
        if "eightfoldcareerurl" not in match.group(0).lower():
            continue
        url_match = _HTTPS_PREFIX_RE.match(bounded, match.end())
        if url_match:
            return unescape(url_match.group(1))
    return ""


def _oracle(html: str, host: str) -> dict[str, Any] | None:
    lowered = html.lower()
    if "data-apibaseurl" in lowered and "data-sitenumber" in lowered:
        for match in _START_TAG_RE.finditer(html):
            tag_text = match.group(0)
            tag_lower = tag_text.lower()
            if "data-apibaseurl" not in tag_lower or "data-sitenumber" not in tag_lower:
                continue
            attrs = parse_html_attrs(tag_text[1:-1])
            raw_api_url = attrs.get("data-apibaseurl", "").strip()
            site_number = attrs.get("data-sitenumber", "").strip()
            if not raw_api_url or not _ORACLE_SITE_NUMBER_VALUE_RE.fullmatch(site_number):
                continue
            api_host = page_host(raw_api_url)
            if (
                api_host
                and is_safe_public_url(raw_api_url)
                and (_ORACLE_HOST_RE.fullmatch(api_host) or (host and _same_site(api_host, host)))
            ):
                return {"board": api_host, "site_number": site_number}
    if "oraclecloud" in lowered:
        match = _ORACLE_SITE_LINK.search(html)
        if match:
            return {"board": match.group(1).lower(), "site_number": match.group(2)}
    if "sitenumber=" not in lowered:
        return None
    number = _ORACLE_SITE_NUMBER.search(html)
    if not number:
        return None
    if "oraclecloud" in lowered:
        api = re.search(rf"https?://({_ORACLE_HOST})", html, re.I)
        if api:
            return {"board": api.group(1).lower(), "site_number": number.group(1)}
    if (
        host
        and is_safe_public_url(f"https://{host}/")
        and "/hcmUI/CandidateExperience" in html
        and "/hcmRestApi/" in html
    ):
        # Served under the company's own host (Dell): the API answers there.
        return {"board": host, "site_number": number.group(1)}
    return None


def _phenom(html: str, host: str) -> dict[str, Any] | None:
    if not host or "phApp" not in html or "phenom" not in html.lower():
        return None
    for match in _PHENOM_BASE.finditer(html):
        base, country, lang = match.groups()
        if not is_safe_public_url(base):
            continue
        parsed = urlparse(base)
        if parsed.scheme.lower() != "https" or parsed.port not in (None, 443):
            continue
        board = page_host(base)
        if board and _same_site(board, host):
            return {
                "board": board,
                "country": country,
                "lang": lang,
                "base_url": f"https://{board}/{country}/{lang}/",
            }
    return None


def _eightfold(html: str, host: str) -> dict[str, Any] | None:
    lowered = html.lower()
    if "eightfold" not in lowered or not host or not is_safe_public_url(f"https://{host}/"):
        return None
    group = _EIGHTFOLD_GROUP.search(html)
    if not group:
        return None
    # The API generation is found by the reader: ``pcsx`` first, then ``v2``.
    return {"board": host, "domain": group.group(1).lower()}


def _canonical_href(html: str) -> str:
    bounded = html[:MAX_HTML_CHARS]
    if "canonical" not in bounded.lower():
        return ""
    for match in _LINK_TAG_RE.finditer(bounded):
        attrs_raw = match.group(1)
        if "canonical" not in attrs_raw.lower():
            continue
        attrs = parse_html_attrs(attrs_raw)
        if "canonical" in attrs.get("rel", "").lower().split() and attrs.get("href"):
            return attrs["href"].strip()
    return ""


def _jibe(html: str, host: str) -> dict[str, Any] | None:
    if not host or not is_safe_public_url(f"https://{host}/") or "jibecdn.com" not in html:
        return None
    # A role's page is under the site's jobs path (``/careers-home/jobs/95802``).
    prefix = ""
    canonical = _canonical_href(html)
    if canonical:
        try:
            path = urlparse(canonical).path
        except ValueError:
            path = ""
        if "/jobs" in path:
            candidate = path.split("/jobs", 1)[0].rstrip("/")
            if (
                candidate
                and candidate.startswith("/")
                and not candidate.startswith("//")
                and "\\" not in candidate
                and not any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in candidate)
            ):
                prefix = candidate
    return {"board": host, "jobs_path": f"{prefix}/jobs"}


def _talentbrew(html: str, host: str) -> dict[str, Any] | None:
    if not host or not is_safe_public_url(f"https://{host}/") or "tbcdn.talentbrew.com" not in html:
        return None
    if "/search-jobs" not in html:
        # A TalentBrew asset on a page that is not the job search itself.
        return None
    return {"board": host}


def kula_entry(slug: str) -> dict[str, Any]:
    """The board entry of the Kula board under ``slug``."""
    return {"provider": PROVIDER_KULA, "board": KULA_HOST, "slug": slug}


def _kula(html: str, host: str) -> dict[str, Any] | None:
    match = re.search(KULA_BOARD_LINK, html)
    if not match:
        return None
    return {"board": KULA_HOST, "slug": match.group(1)}


def _same_site(host: str, other: str) -> bool:
    """Whether two hosts belong to the same registrable site (``careers.adobe.com``
    and ``www.adobe.com``, without equating distinct tenants under ``*.co.uk`` or
    multi-tenant suffixes like ``github.io``)."""
    r1 = registrable_domain(host)
    return bool(r1) and r1 == registrable_domain(other)


_DETECTORS = {
    PROVIDER_ORACLE: _oracle,
    PROVIDER_PHENOM: _phenom,
    PROVIDER_EIGHTFOLD: _eightfold,
    PROVIDER_JIBE: _jibe,
    PROVIDER_TALENTBREW: _talentbrew,
    PROVIDER_KULA: _kula,
}
