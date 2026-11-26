"""Keyless web search (floor tier for engine-side general web).

Returns ranked web results for a query with no API key. This is strictly the
FLOOR of the search-source ladder:

    host-native search  >  paid engine backend  >  keyless engine search

It must never run on a host that has native search (the model does it better
there) or preempt a configured paid backend. The pipeline/grounding layer owns
that gating; this module just performs the search when asked.

Vendor-neutral rungs, all stdlib-only via :mod:`http`:
  0. Brave Search HTML results, tried first for ``site:reddit.com`` queries
     only (the Reddit web floor): it serves dated Reddit thread links to a
     plain GET where DuckDuckGo throttles.
  1. DuckDuckGo HTML endpoint (no key, no instance to maintain).
  2. Startpage HTML results, tried when DuckDuckGo yields nothing — notably
     when DuckDuckGo anomaly-blocks a datacenter IP with a 202 challenge page.
  3. A configurable SearXNG instance returning JSON (``LAST30DAYS_SEARXNG_URL``),
     tried when the HTML rungs yield nothing.

Each HTML rung keeps a health state for the run (:class:`RungHealth`). A 403
marks a rung ``blocked`` and a 429 that survived its one retry (or a known
challenge page) marks it ``throttled``; either way the rung is skipped for the
rest of the run and the next rung answers. One failed page of any other kind
(``error``) or a page with no results (``empty``) skips nothing.
:func:`seed_rung_health` lets a caller start a run with rungs it already
knows are down.

Never raises. Returns results in the same dict shape as the paid backends in
:mod:`grounding` so they flow through normalize/score/dedupe unchanged. On total
failure returns ``([], artifact)`` with a degraded reason in the artifact, so the
source-health layer can report it.
"""

from __future__ import annotations

import html
import ipaddress
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunsplit

from . import health, http, log, public_host

KEYLESS_BACKEND = "keyless"

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# Floor-tier relevance: below the paid backends' 0.8 so fusion prefers paid/native
# results when both are present.
_KEYLESS_RELEVANCE = 0.6

_MAX_SERP_BYTES = 2 * 1024 * 1024
_MAX_SEARXNG_BYTES = 2 * 1024 * 1024
# With a result filter, how many rows are looked at before giving up.
_MAX_SEARXNG_SCANNED = 100

_BLOCKED_SEARXNG_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})
_AWS_IPV6_IMDS = ipaddress.IPv6Address("fd00:ec2::254")
_ALIBABA_IMDS = ipaddress.IPv4Address("100.100.100.200")

# Single-character-class opening-tag and attribute patterns: [^<>]* never
# crosses a tag boundary, keeping all SERP scans linear even on malformed HTML.
_A_TAG_RE = re.compile(r"<a\b[^<>]*>", re.IGNORECASE)
_DIV_TAG_RE = re.compile(r"<div\b[^<>]*>", re.IGNORECASE)
_H2_TAG_RE = re.compile(r"<h2\b[^<>]*>", re.IGNORECASE)
_P_TAG_RE = re.compile(r"<p\b[^<>]*>", re.IGNORECASE)
_CLOSE_A_RE = re.compile(r"</a\s*>", re.IGNORECASE)
_CLOSE_DIV_RE = re.compile(r"</div\s*>", re.IGNORECASE)
_CLOSE_H2_RE = re.compile(r"</h2\s*>", re.IGNORECASE)
_CLOSE_P_RE = re.compile(r"</p\s*>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r'\bclass="(?P<cls>[^"]*)"', re.IGNORECASE)
_HREF_ATTR_RE = re.compile(r'\bhref="(?P<href>[^"]+)"', re.IGNORECASE)
_HTTP_HREF_ATTR_RE = re.compile(r'\bhref="(?P<href>https?://[^"]+)"', re.IGNORECASE)
_DATA_TYPE_WEB_RE = re.compile(r'\bdata-type="web"', re.IGNORECASE)
_BRAVE_SNIPPET_CLASS_RE = re.compile(r"^(?:content|inline-qa-question)\b", re.IGNORECASE)

_STARTPAGE_HTML_URL = "https://www.startpage.com/sp/search"
_BRAVE_HTML_URL = "https://search.brave.com/search"

# Bounded character class with no overlapping whitespace quantifiers:
# _brave_date strips surrounding whitespace and trailing dashes in Python.
_BRAVE_AGE_RE = re.compile(
    r'<span class="t-secondary">(?P<age>[^<]{1,120})</span>', re.IGNORECASE,
)
_RELATIVE_AGE_RE = re.compile(
    r"^(?P<n>\d+)\s+(?P<unit>minute|hour|day|week|month|year)s?\s+ago", re.IGNORECASE,
)
_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}
_ABSOLUTE_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y")

# Freshness filters per rung: Brave ``tf``, DuckDuckGo ``df``, Startpage ``with_date``.
_FRESHNESS = {
    "day": ("pd", "d", "d"),
    "week": ("pw", "w", "w"),
    "month": ("pm", "m", "m"),
    "year": ("py", "y", "y"),
}

# A DuckDuckGo anomaly page answers 202 with no results; it is a throttle.
_CHALLENGE_MARKERS = {
    "ddg": ("anomaly-modal",),
    # Startpage answers a refused client with a proof-of-work challenge page.
    "startpage": ('"challenge":{"issuedAt"',),
}

RUNGS = ("brave", "ddg", "startpage")
OK = "ok"
EMPTY = "empty"
ERROR = "error"
THROTTLED = "throttled"
BLOCKED = "blocked"
SKIPPED = "skipped"
# States that take a rung out of the run.
_DOWN = frozenset({THROTTLED, BLOCKED})


class RungHealth:
    """Per-run health of the HTML rungs, shared by every thread of a run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, str] = {}
        self._seeded: set[str] = set()

    def usable(self, rung: str) -> bool:
        with self._lock:
            return rung not in self._seeded and self._states.get(rung) not in _DOWN

    def record(self, rung: str, state: str) -> None:
        with self._lock:
            # A rung already down stays down for the run.
            if self._states.get(rung) not in _DOWN:
                self._states[rung] = state

    def seed(self, states: Mapping[str, str]) -> None:
        """Start the run with rungs already known down (``blocked``/``throttled``)."""
        with self._lock:
            for rung, state in (states or {}).items():
                if rung in RUNGS and state in _DOWN:
                    self._seeded.add(rung)
                    self._states[rung] = state

    def snapshot(self) -> dict[str, str]:
        """Each rung's state this run; a seeded rung that never ran reads ``skipped``."""
        with self._lock:
            out = dict(self._states)
            for rung in self._seeded:
                out[rung] = SKIPPED
            return out

    def reset(self) -> None:
        with self._lock:
            self._states.clear()
            self._seeded.clear()


RUNG_HEALTH = RungHealth()


def rung_health() -> dict[str, str]:
    """The HTML rungs' states this run (see :class:`RungHealth`)."""
    return RUNG_HEALTH.snapshot()


def seed_rung_health(states: Mapping[str, str]) -> None:
    """Skip the given down rungs for this run without asking them."""
    RUNG_HEALTH.seed(states)


def reset_rung_health() -> None:
    """Forget every rung's state (call between runs in one process)."""
    RUNG_HEALTH.reset()


def _domain(url: str) -> str:
    # Normalize identically to grounding._domain (strip + lowercase) so keyless
    # and paid results dedupe/group consistently by source_domain.
    try:
        return urlparse(url).netloc.strip().lower()
    except (ValueError, AttributeError):
        return ""


class _TextStripper(HTMLParser):
    """Single-pass O(N) HTML-to-text stripper that drops <style>/<script> contents."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depths = {"style": 0, "script": 0}
        self._skip_active = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._skip_depths:
            self._skip_depths[tag_lower] += 1
            self._skip_active += 1

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower in self._skip_depths and self._skip_depths[tag_lower] > 0:
            self._skip_depths[tag_lower] -= 1
            self._skip_active -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_active == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(fragment: str) -> str:
    if not fragment:
        return ""
    parser = _TextStripper()
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _is_blocked_searxng_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject link-local/cloud-metadata, unspecified, and multicast IP addresses.

    Loopback (127.0.0.0/8, ::1) and RFC1918 private LAN addresses are allowed
    so self-hosted local/Docker/homelab SearXNG instances continue to work.
    """
    if addr.is_link_local or addr.is_unspecified or addr.is_multicast:
        return True
    if addr == _AWS_IPV6_IMDS or addr == _ALIBABA_IMDS:
        return True
    if isinstance(addr, ipaddress.IPv6Address):
        for embedded in (
            addr.ipv4_mapped,
            addr.sixtofour,
            addr.teredo[1] if addr.teredo else None,
        ):
            if embedded is not None and _is_blocked_searxng_ip(embedded):
                return True
        if addr in public_host._NAT64_WELL_KNOWN or addr in public_host._NAT64_LOCAL:
            embedded = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
            if _is_blocked_searxng_ip(embedded):
                return True
    return False


def _is_allowed_searxng_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not _is_blocked_searxng_ip(addr)


def _normalize_searxng_host(host: str) -> str:
    """Normalize a SearXNG hostname via IDNA to canonical ASCII, or '' if invalid."""
    raw = (host or "").strip()
    if not raw or "%" in raw:
        return ""
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        return ""
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    if ":" in raw:
        return raw.lower().rstrip(".") if raw.isascii() else ""
    try:
        normalized = raw.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        return ""
    if not normalized or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in normalized):
        return ""
    return normalized


def _is_blocked_searxng_host(host: str) -> bool:
    normalized = _normalize_searxng_host(host)
    if not normalized:
        return True
    if (
        normalized in _BLOCKED_SEARXNG_HOSTS
        or normalized.endswith(".metadata.google.internal")
        or normalized.endswith(".metadata.goog")
    ):
        return True
    ip = public_host._parse_ip_literal(normalized)
    if ip is not None:
        return _is_blocked_searxng_ip(ip)
    return False


def _validate_searxng_url(instance_url: str) -> Optional[tuple[str, tuple[str, str, int]]]:
    """Validate LAST30DAYS_SEARXNG_URL and return ``(normalized_base, origin)`` or None."""
    if not isinstance(instance_url, str):
        return None
    text = instance_url.strip()
    if not text or "\\" in text or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if "%" in parts.netloc:
        return None
    if parts.query or parts.fragment:
        return None
    host = _normalize_searxng_host(parts.hostname or "")
    if not host or _is_blocked_searxng_host(host):
        return None
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    origin = (scheme, host, effective_port)
    base_path = parts.path.rstrip("/")
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = f"{netloc_host}:{port}" if port is not None else netloc_host
    normalized = urlunsplit((scheme, netloc, base_path, "", ""))
    return normalized, origin


def _rung_redirect_guard(url: str):
    """Allow redirects only over HTTPS to the same public search rung host."""
    expected_host = (urlsplit(url).hostname or "").strip().lower().rstrip(".")

    def _guard(target: str) -> bool:
        if not isinstance(target, str) or not target or target != target.strip():
            return False
        if "\\" in target or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
            return False
        if not public_host.is_public_address_syntax(target):
            return False
        try:
            parts = urlsplit(target)
            port = parts.port
        except ValueError:
            return False
        if parts.scheme.lower() != "https":
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if port not in (None, 443):
            return False
        host = (parts.hostname or "").strip().lower().rstrip(".")
        return bool(expected_host) and host == expected_host

    return _guard


def _searxng_redirect_guard(origin: tuple[str, str, int]):
    """Allow SearXNG redirects only within the exact configured origin."""
    def _guard(target: str) -> bool:
        if not isinstance(target, str) or not target or target != target.strip():
            return False
        if "\\" in target or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
            return False
        try:
            parts = urlsplit(target)
            port = parts.port
        except ValueError:
            return False
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            return False
        if parts.username is not None or parts.password is not None:
            return False
        if "%" in parts.netloc:
            return False
        host = _normalize_searxng_host(parts.hostname or "")
        if not host or _is_blocked_searxng_host(host):
            return False
        effective_port = port if port is not None else (443 if scheme == "https" else 80)
        return (scheme, host, effective_port) == origin

    return _guard


def _unwrap_ddg_redirect(href: str) -> str:
    """DuckDuckGo wraps result links as //duckduckgo.com/l/?uddg=<encoded>."""
    if "uddg=" not in href:
        return href if href.startswith("http") else f"https:{href}" if href.startswith("//") else href
    try:
        query = urlparse(href if href.startswith("http") else f"https:{href}").query
        target = parse_qs(query).get("uddg", [""])[0]
        return target or href
    except (ValueError, AttributeError):
        return href


def _is_reddit_query(query: str) -> bool:
    return "site:reddit.com" in (query or "").lower()


def keyless_search(
    query: str,
    date_range: tuple[str, str],
    config: dict,
    count: int = 5,
    *,
    freshness: Optional[str] = None,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
    accept_result: Optional[Callable[[dict], bool]] = None,
) -> tuple[list[dict], dict]:
    """Run keyless web search; returns (items, artifact). Never raises.

    ``deadline_monotonic`` bounds every page fetch, and no further rung starts
    once it passes or ``cancel`` is set.
    ``accept_result`` filters each rung before deciding whether it served.

    ``freshness`` (``day``/``week``/``month``/``year``) asks each HTML rung for
    results from that recent period only. The artifact's ``keyless_rungs``
    names the state each HTML rung reached for this query (``skipped`` for a
    rung down earlier in the run).
    """
    tried: dict[str, str] = {}
    items: list[dict] = []
    used = "ddg"
    ladder = (("brave", _brave_rung),) if _is_reddit_query(query) else ()
    ladder += (("ddg", _ddg_rung), ("startpage", _startpage_rung))
    bound: dict[str, Any] = {}
    if deadline_monotonic is not None:
        bound["deadline_monotonic"] = deadline_monotonic
    if cancel is not None:
        bound["cancel"] = cancel

    def stopped() -> bool:
        if cancel is not None and cancel.is_set():
            return True
        return deadline_monotonic is not None and time.monotonic() >= deadline_monotonic

    searxng_attempted = False
    searxng_state: Optional[str] = None
    with http.tee_failures() as run_failures:
        for rung, search in ladder:
            if stopped():
                tried[rung] = SKIPPED
                continue
            if not RUNG_HEALTH.usable(rung):
                tried[rung] = SKIPPED
                continue
            # A DuckDuckGo anomaly block (202 challenge page on a datacenter IP)
            # falls through to Startpage, which still serves organic results there.
            items, state = search(query, count, freshness, **bound)
            if accept_result is not None and items:
                items = [item for item in items if accept_result(item)]
                if not items and state == OK:
                    state = EMPTY
            tried[rung] = state
            RUNG_HEALTH.record(rung, state)
            used = rung
            if items:
                break
        if not items:
            searxng_url = (config.get("LAST30DAYS_SEARXNG_URL") or "").strip()
            if searxng_url and not stopped():
                searxng_attempted = True
                searxng_outcome: dict[str, str] = {}
                items = _search_searxng(
                    query, count, searxng_url, accept_result=accept_result,
                    outcome=searxng_outcome, **bound,
                )
                # Reported on its own key so that ``keyless_rungs`` and the shared rung
                # health stay the HTML rungs only.
                searxng_state = searxng_outcome.get("state")
                used = "searxng"
    # These backends never return a publish date, so normalize's grounding
    # require_date would drop every result. Keep undated links only when the
    # window reaches today; a historical window can't vouch for them.
    if date_range[1] >= datetime.now(timezone.utc).date().isoformat():
        for item in items:
            item["metadata"] = {"date_window_basis": "keyless_current"}
    artifact = {
        "label": "keyless",
        "webSearchQueries": [query],
        "resultCount": len(items),
        "keyless_backend": used,
        "keyless_rungs": tried,
    }
    if searxng_state:
        artifact["keyless_searxng"] = searxng_state
    if not items:
        budget_exhausted = stopped() or any(
            isinstance(failure, http.DeadlineExceeded) for failure in run_failures
        )
        if budget_exhausted:
            artifact["reason"] = "keyless-search-timeout"
            artifact["_source_outcome"] = {
                "state": health.TIMEOUT,
                "detail": "Keyless web search cancelled or timed out",
                "attempted": searxng_attempted or any(state != SKIPPED for state in tried.values()),
            }
        else:
            artifact["reason"] = "keyless-search-unavailable"
    return items, artifact


def _fetch_page(
    rung: str,
    url: str,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[Optional[str], str]:
    """One rung's results page: ``(text, "")`` or ``(None, failure state)``.

    The transport swallows the status; the failures it records are read back
    here and still forwarded to the caller's failure sink.
    """
    extra: dict[str, Any] = {}
    if cancel is not None:
        extra["cancel"] = cancel
    with http.tee_failures() as failures:
        try:
            text = http.get_text(
                url,
                accept="text/html",
                retries=2,
                deadline_monotonic=deadline_monotonic,
                public_only=True,
                redirect_guard=_rung_redirect_guard(url),
                max_bytes=_MAX_SERP_BYTES,
                decode_errors="replace",
                **extra,
            )
        except (UnicodeDecodeError, ValueError):
            return None, ERROR
    if is_challenge_page(rung, text or ""):
        return None, THROTTLED
    if text:
        return text, ""
    statuses = [f.status_code for f in failures if f.status_code]
    if 403 in statuses:
        return None, BLOCKED
    if 429 in statuses:
        return None, THROTTLED
    return None, ERROR if text is None else EMPTY


def _freshness_param(freshness: Optional[str], index: int) -> Optional[str]:
    values = _FRESHNESS.get((freshness or "").lower())
    return values[index] if values else None



def _brave_url(query: str, freshness: Optional[str] = None) -> str:
    params = {"q": query, "source": "web"}
    tf = _freshness_param(freshness, 0)
    if tf:
        params["tf"] = tf
    return f"{_BRAVE_HTML_URL}?{urlencode(params)}"


def _brave_rung(
    query: str, count: int, freshness: Optional[str] = None,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[list[dict], str]:
    """Rung 0 (Reddit queries): Brave Search's HTML results page, with its state."""
    text, failed = _fetch_page(
        "brave", _brave_url(query, freshness), deadline_monotonic, cancel=cancel,
    )
    if text is None:
        return [], failed
    items = parse_brave(text, count)
    return items, OK if items else EMPTY


def _brave_date(age: str, now: datetime) -> Optional[str]:
    """``"2 weeks ago"`` or ``"Nov 3, 2026"`` as YYYY-MM-DD, else None."""
    age = (age or "").strip().rstrip("-").strip()
    match = _RELATIVE_AGE_RE.match(age)
    if match:
        days = int(match.group("n")) * _UNIT_DAYS[match.group("unit").lower()]
        return (now - timedelta(days=days)).date().isoformat()
    for fmt in _ABSOLUTE_DATE_FORMATS:
        try:
            return datetime.strptime(age, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _tag_class(tag: str) -> str:
    match = _CLASS_ATTR_RE.search(tag)
    return match.group("cls") if match else ""


def _brave_block_starts(text: str) -> list[int]:
    starts: list[int] = []
    for match in _DIV_TAG_RE.finditer(text):
        tag = match.group(0)
        if not _DATA_TYPE_WEB_RE.search(tag):
            continue
        cls = _tag_class(tag)
        if cls and re.match(r"^snippet\b", cls, re.IGNORECASE):
            starts.append(match.start())
    return starts


def parse_brave(text: str, count: int, now: Optional[datetime] = None) -> list[dict]:
    """Organic results from a Brave Search HTML page, each with its date when shown."""
    now = now or datetime.now(timezone.utc)
    text = text or ""
    starts = _brave_block_starts(text)
    items: list[dict] = []
    seen: set[str] = set()
    for idx, start in enumerate(starts):
        if len(items) >= count:
            break
        block = text[start:starts[idx + 1] if idx + 1 < len(starts) else len(text)]
        target = ""
        for a_match in _A_TAG_RE.finditer(block):
            href_match = _HTTP_HREF_ATTR_RE.search(a_match.group(0))
            if href_match:
                target = html.unescape(href_match.group("href"))
                break
        if not target or target in seen:
            continue
        title = ""
        snippet = ""
        tried_title = False
        tried_snippet = False
        div_pos = 0
        while div_pos < len(block) and not (tried_title and tried_snippet):
            div_match = _DIV_TAG_RE.search(block, div_pos)
            if div_match is None:
                break
            div_pos = div_match.end()
            cls = _tag_class(div_match.group(0))
            if not cls:
                continue
            cls_lower = cls.lower()
            if not tried_title and "search-snippet-title" in cls_lower:
                tried_title = True
                close = _CLOSE_DIV_RE.search(block, div_match.end())
                if close is None:
                    break
                title = _strip_html(block[div_match.end():close.start()])
                div_pos = close.end()
            elif not tried_snippet and _BRAVE_SNIPPET_CLASS_RE.match(cls):
                tried_snippet = True
                close = _CLOSE_DIV_RE.search(block, div_match.end())
                if close is None:
                    break
                raw_snippet = block[div_match.end():close.start()]
                snippet = " ".join(_strip_html(
                    _BRAVE_AGE_RE.sub("", raw_snippet, count=1)
                ).split())
                div_pos = close.end()
        if not title:
            continue
        age = _BRAVE_AGE_RE.search(block)
        seen.add(target)
        item = _to_item(len(items), title, target, snippet)
        item["date"] = _brave_date(age.group("age"), now) if age else None
        items.append(item)
    return items


def _search_ddg(query: str, count: int, freshness: Optional[str] = None) -> list[dict]:
    return _ddg_rung(query, count, freshness)[0]


def _ddg_url(query: str, freshness: Optional[str] = None) -> str:
    params = {"q": query}
    df = _freshness_param(freshness, 1)
    if df:
        params["df"] = df
    return f"{_DDG_HTML_URL}?{urlencode(params)}"


def _ddg_rung(
    query: str, count: int, freshness: Optional[str] = None,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[list[dict], str]:
    text, failed = _fetch_page(
        "ddg", _ddg_url(query, freshness), deadline_monotonic, cancel=cancel,
    )
    if text is None:
        return [], failed
    items = parse_ddg(text, count)
    return items, OK if items else EMPTY


def parse_ddg(text: str, count: int) -> list[dict]:
    """Organic results from a DuckDuckGo HTML results page."""
    text = text or ""
    items: list[dict] = []
    # Associate each result's snippet by position, not by a parallel index:
    # some result anchors (video/news modules) have no snippet, so a global
    # zip would shift every later snippet onto the wrong result. Take the first
    # snippet that falls between this anchor and the next one.
    anchors: list[tuple[int, int, str, str]] = []
    pos = 0
    while pos < len(text):
        match = _A_TAG_RE.search(text, pos)
        if match is None:
            break
        pos = match.end()
        tag = match.group(0)
        if "result__a" not in tag.lower():
            continue
        cls = _tag_class(tag)
        if "result__a" not in cls.lower().split():
            continue
        href_match = _HREF_ATTR_RE.search(tag)
        if not href_match:
            continue
        close = _CLOSE_A_RE.search(text, match.end())
        if close is None:
            break
        anchors.append((match.start(), close.end(), href_match.group("href"), text[match.end():close.start()]))
        pos = close.end()
    for idx, (_start, end, raw_href, raw_title) in enumerate(anchors):
        if len(items) >= count:
            break
        target = _unwrap_ddg_redirect(raw_href)
        if not target.startswith("http"):
            continue
        next_start = anchors[idx + 1][0] if idx + 1 < len(anchors) else len(text)
        snippet = ""
        for s_match in _A_TAG_RE.finditer(text, end, next_start):
            s_tag = s_match.group(0)
            if "result__snippet" not in s_tag.lower():
                continue
            s_cls = _tag_class(s_tag)
            if "result__snippet" not in s_cls.lower().split():
                continue
            s_close = _CLOSE_A_RE.search(text, s_match.end(), next_start)
            if s_close is not None:
                snippet = _strip_html(text[s_match.end():s_close.start()])
            break
        title = _strip_html(raw_title)
        items.append(_to_item(len(items), title, target, snippet))
    return items


def _search_startpage(query: str, count: int, freshness: Optional[str] = None) -> list[dict]:
    return _startpage_rung(query, count, freshness)[0]


def _startpage_rung(
    query: str, count: int, freshness: Optional[str] = None,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> tuple[list[dict], str]:
    """Keyless rung 2: Startpage's HTML results page. Unlike DuckDuckGo's HTML
    endpoint (which anomaly-blocks datacenter IPs with a 202 challenge page),
    Startpage returns organic results to a plain browser-UA GET, making it the
    working floor on hosts DuckDuckGo refuses. Never raises."""
    text, failed = _fetch_page(
        "startpage", _startpage_url(query, freshness), deadline_monotonic, cancel=cancel,
    )
    if text is None:
        return [], failed
    items = parse_startpage(text, count)
    return items, OK if items else EMPTY


def _startpage_url(query: str, freshness: Optional[str] = None) -> str:
    params = {"query": query}
    with_date = _freshness_param(freshness, 2)
    if with_date:
        params["with_date"] = with_date
    return f"{_STARTPAGE_HTML_URL}?{urlencode(params)}"


def parse_startpage(text: str, count: int) -> list[dict]:
    """Organic results from a Startpage HTML results page."""
    text = text or ""
    items: list[dict] = []
    results: list[tuple[int, int, str, str]] = []
    pos = 0
    while pos < len(text):
        match = _A_TAG_RE.search(text, pos)
        if match is None:
            break
        pos = match.end()
        tag = match.group(0)
        if "result-title" not in tag.lower():
            continue
        cls = _tag_class(tag)
        if "result-title" not in cls.lower():
            continue
        href_match = _HTTP_HREF_ATTR_RE.search(tag)
        if not href_match:
            continue
        close = _CLOSE_A_RE.search(text, match.end())
        if close is None:
            break
        results.append((match.start(), close.end(), href_match.group("href"), text[match.end():close.start()]))
        pos = close.end()
    for idx, (_start, end, raw_href, inner) in enumerate(results):
        if len(items) >= count:
            break
        target = html.unescape(raw_href)
        if not target.startswith("http"):
            continue
        h2_open = _H2_TAG_RE.search(inner)
        if h2_open is not None:
            h2_close = _CLOSE_H2_RE.search(inner, h2_open.end())
            raw_title = inner[h2_open.end():h2_close.start()] if h2_close is not None else inner
        else:
            raw_title = inner
        title = _strip_html(raw_title)
        if not title:
            continue
        next_start = results[idx + 1][0] if idx + 1 < len(results) else len(text)
        snippet = ""
        for p_match in _P_TAG_RE.finditer(text, end, next_start):
            p_tag = p_match.group(0)
            if "description" not in p_tag.lower():
                continue
            p_cls = _tag_class(p_tag)
            if "description" not in p_cls.lower():
                continue
            p_close = _CLOSE_P_RE.search(text, p_match.end(), next_start)
            if p_close is not None:
                snippet = _strip_html(text[p_match.end():p_close.start()])
            break
        items.append(_to_item(len(items), title, target, snippet))
    return items


def rung_url(rung: str, query: str, freshness: Optional[str] = None) -> str:
    """The results-page address one HTML rung asks for ``query``."""
    return {"brave": _brave_url, "ddg": _ddg_url, "startpage": _startpage_url}[rung](query, freshness)


def parse_rung(rung: str, text: str, count: int) -> list[dict]:
    """One HTML rung's parser applied to a results page it served."""
    if rung == "brave":
        return parse_brave(text, count)
    return {"ddg": parse_ddg, "startpage": parse_startpage}[rung](text, count)


def is_challenge_page(rung: str, text: str) -> bool:
    """Whether ``text`` is a known challenge/anomaly page for ``rung``."""
    return bool(text) and any(marker in text for marker in _CHALLENGE_MARKERS.get(rung, ()))


def _searxng_failure_state(exc: http.HTTPError) -> str:
    """The rung state for a failed SearXNG request (a refusal, a throttle, a timeout or other).

    A refusal status wins. Any other error the transport normalized to a timeout counts as one,
    including the shared deadline. Only the shared deadline ends a run, which is decided elsewhere.
    """
    if exc.status_code == 403:
        return BLOCKED
    if exc.status_code == 429:
        return THROTTLED
    if exc.outcome_state == health.TIMEOUT:
        return health.TIMEOUT
    return ERROR


def _search_searxng(
    query: str, count: int, instance_url: str, time_range: str | None = None,
    deadline_monotonic: float | None = None,
    cancel: Optional[threading.Event] = None,
    accept_result: Optional[Callable[[dict], bool]] = None,
    outcome: Optional[dict[str, str]] = None,
) -> list[dict]:
    """SearXNG results, up to ``count`` that ``accept_result`` keeps.

    The filter runs before the count, over at most ``_MAX_SEARXNG_SCANNED`` rows.
    ``outcome``, when given, receives ``state``: ok, empty, blocked (403),
    throttled (429), timeout or error. The URL and response text stay out of it.
    """
    def record(state: str) -> None:
        if outcome is not None:
            outcome["state"] = state

    validated = _validate_searxng_url(instance_url)
    if validated is None:
        log.source_log(
            "Web",
            "Ignoring invalid or blocked LAST30DAYS_SEARXNG_URL",
            tty_only=False,
        )
        return []
    base, origin = validated
    params = {'q': query, 'format': 'json'}
    if time_range:
        params['time_range'] = time_range
    url = f"{base}/search?{urlencode(params)}"
    extra: dict[str, Any] = {}
    if cancel is not None:
        extra["cancel"] = cancel
    try:
        data = http.get(
            url,
            headers={"Accept": "application/json"},
            timeout=15,
            retries=2,
            deadline_monotonic=deadline_monotonic,
            redirect_guard=_searxng_redirect_guard(origin),
            ip_validator=_is_allowed_searxng_ip,
            max_bytes=_MAX_SEARXNG_BYTES,
            decode_errors="replace",
            **extra,
        )
    except http.HTTPError as exc:
        record(_searxng_failure_state(exc))
        return []
    except (UnicodeDecodeError, ValueError):
        record(ERROR)
        return []
    if not isinstance(data, dict):
        record(ERROR)
        return []
    raw_results = data.get("results")
    if not isinstance(raw_results, list):
        record(ERROR)
        return []
    if accept_result is not None:
        raw_results = raw_results[:_MAX_SEARXNG_SCANNED]
    items: list[dict] = []
    for i, r in enumerate(raw_results):
        if len(items) >= count:
            break
        if not isinstance(r, dict):
            continue
        target = r.get("url")
        if not isinstance(target, str) or not target.startswith(("http://", "https://")):
            continue
        title = r.get("title")
        content = r.get("content")
        item = _to_item(
            i,
            title if isinstance(title, str) else str(title or ""),
            target,
            content if isinstance(content, str) else str(content or ""),
        )
        if accept_result is not None and not accept_result(item):
            continue
        items.append(item)
    record(OK if items else EMPTY)
    return items


def _to_item(index: int, title: str, url: str, snippet: str) -> dict:
    return {
        "id": f"WK{index + 1}",
        "title": title,
        "url": url,
        "source_domain": _domain(url),
        "snippet": (snippet or "")[:500],
        "date": None,
        "relevance": _KEYLESS_RELEVANCE,
        "why_relevant": "Keyless web search",
    }
