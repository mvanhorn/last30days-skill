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
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from . import http

KEYLESS_BACKEND = "keyless"

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# Floor-tier relevance: below the paid backends' 0.8 so fusion prefers paid/native
# results when both are present.
_KEYLESS_RELEVANCE = 0.6

_TAG_RE = re.compile(r"<[^>]+>")
# Strip <style>/<script> blocks *including their contents* before dropping tags,
# so inline CSS/JS text (e.g. Startpage's emotion styles) never leaks into a
# title or snippet.
_STYLE_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_RESULT_A_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)

_STARTPAGE_HTML_URL = "https://www.startpage.com/sp/search"
# Startpage marks each organic hit with an <a class="result-title result-link …"
# href="<target>">…<h2 …>title</h2></a>, and the description in a following
# <p class="…description…">. Class names carry hashed emotion suffixes, so match
# on the stable "result-title" / "description" substrings.
_SP_RESULT_RE = re.compile(
    r'<a\b[^>]*class="[^"]*result-title[^"]*"[^>]*href="(?P<href>https?://[^"]+)"[^>]*>(?P<inner>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SP_H2_RE = re.compile(r"<h2\b[^>]*>(?P<title>.*?)</h2>", re.IGNORECASE | re.DOTALL)
_SP_DESC_RE = re.compile(
    r'<p\b[^>]*class="[^"]*description[^"]*"[^>]*>(?P<snippet>.*?)</p>',
    re.IGNORECASE | re.DOTALL,
)


_BRAVE_HTML_URL = "https://search.brave.com/search"
# Each organic Brave hit is a <div class="snippet …" data-type="web"> block whose
# first anchor is the result link, with the title in a "search-snippet-title"
# div and the age ("2 weeks ago -") in the first t-secondary span of its body.
# The inline comment cards under a Reddit hit repeat the thread link; only the
# block's first anchor is read.
_BRAVE_BLOCK_RE = re.compile(
    r'<div\b[^>]*class="snippet\b[^"]*"[^>]*data-type="web"[^>]*>', re.IGNORECASE,
)
_BRAVE_LINK_RE = re.compile(r'<a\b[^>]*href="(?P<href>https?://[^"]+)"', re.IGNORECASE)
_BRAVE_TITLE_RE = re.compile(
    r'<div\b[^>]*class="[^"]*search-snippet-title[^"]*"[^>]*>(?P<title>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_BRAVE_AGE_RE = re.compile(
    r'<span class="t-secondary">\s*(?P<age>[^<]*?)\s*-?\s*</span>', re.IGNORECASE,
)
_BRAVE_SNIPPET_RE = re.compile(
    r'<div\b[^>]*class="(?:content|inline-qa-question)\b[^"]*"[^>]*>(?P<snippet>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
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


def _strip_html(fragment: str) -> str:
    without_blocks = _STYLE_SCRIPT_RE.sub("", fragment or "")
    return html.unescape(_TAG_RE.sub("", without_blocks)).strip()


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
) -> tuple[list[dict], dict]:
    """Run keyless web search; returns (items, artifact). Never raises.

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
    for rung, search in ladder:
        if not RUNG_HEALTH.usable(rung):
            tried[rung] = SKIPPED
            continue
        # A DuckDuckGo anomaly block (202 challenge page on a datacenter IP)
        # falls through to Startpage, which still serves organic results there.
        items, state = search(query, count, freshness)
        tried[rung] = state
        RUNG_HEALTH.record(rung, state)
        used = rung
        if items:
            break
    if not items:
        searxng_url = (config.get("LAST30DAYS_SEARXNG_URL") or "").strip()
        if searxng_url:
            items = _search_searxng(query, count, searxng_url)
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
    if not items:
        artifact["reason"] = "keyless-search-unavailable"
    return items, artifact


def _fetch_page(rung: str, url: str) -> tuple[Optional[str], str]:
    """One rung's results page: ``(text, "")`` or ``(None, failure state)``.

    The transport swallows the status; the failures it records are read back
    here and still forwarded to the caller's failure sink.
    """
    with http.tee_failures() as failures:
        text = http.get_text(url, accept="text/html", retries=2)
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


def _brave_rung(query: str, count: int, freshness: Optional[str] = None) -> tuple[list[dict], str]:
    """Rung 0 (Reddit queries): Brave Search's HTML results page, with its state."""
    text, failed = _fetch_page("brave", _brave_url(query, freshness))
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


def parse_brave(text: str, count: int, now: Optional[datetime] = None) -> list[dict]:
    """Organic results from a Brave Search HTML page, each with its date when shown."""
    now = now or datetime.now(timezone.utc)
    starts = [m.start() for m in _BRAVE_BLOCK_RE.finditer(text or "")]
    items: list[dict] = []
    seen: set[str] = set()
    for idx, start in enumerate(starts):
        if len(items) >= count:
            break
        block = text[start:starts[idx + 1] if idx + 1 < len(starts) else len(text)]
        link = _BRAVE_LINK_RE.search(block)
        if not link:
            continue
        target = html.unescape(link.group("href"))
        if target in seen:
            continue
        title_match = _BRAVE_TITLE_RE.search(block)
        title = _strip_html(title_match.group("title")) if title_match else ""
        if not title:
            continue
        snippet_match = _BRAVE_SNIPPET_RE.search(block)
        snippet = ""
        if snippet_match:
            snippet = " ".join(_strip_html(
                _BRAVE_AGE_RE.sub("", snippet_match.group("snippet"), count=1)
            ).split())
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


def _ddg_rung(query: str, count: int, freshness: Optional[str] = None) -> tuple[list[dict], str]:
    text, failed = _fetch_page("ddg", _ddg_url(query, freshness))
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
    matches = list(_RESULT_A_RE.finditer(text))
    for idx, match in enumerate(matches):
        if len(items) >= count:
            break
        target = _unwrap_ddg_redirect(match.group("href"))
        if not target.startswith("http"):
            continue
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        window = text[match.end():next_start]
        snippet_match = _SNIPPET_RE.search(window)
        snippet = _strip_html(snippet_match.group("snippet")) if snippet_match else ""
        title = _strip_html(match.group("title"))
        items.append(_to_item(len(items), title, target, snippet))
    return items


def _search_startpage(query: str, count: int, freshness: Optional[str] = None) -> list[dict]:
    return _startpage_rung(query, count, freshness)[0]


def _startpage_rung(
    query: str, count: int, freshness: Optional[str] = None,
) -> tuple[list[dict], str]:
    """Keyless rung 2: Startpage's HTML results page. Unlike DuckDuckGo's HTML
    endpoint (which anomaly-blocks datacenter IPs with a 202 challenge page),
    Startpage returns organic results to a plain browser-UA GET, making it the
    working floor on hosts DuckDuckGo refuses. Never raises."""
    text, failed = _fetch_page("startpage", _startpage_url(query, freshness))
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
    result_matches = list(_SP_RESULT_RE.finditer(text))
    for idx, match in enumerate(result_matches):
        if len(items) >= count:
            break
        target = html.unescape(match.group("href"))
        if not target.startswith("http"):
            continue
        h2 = _SP_H2_RE.search(match.group("inner"))
        title = _strip_html(h2.group("title") if h2 else match.group("inner"))
        if not title:
            continue
        next_start = result_matches[idx + 1].start() if idx + 1 < len(result_matches) else len(text)
        desc = _SP_DESC_RE.search(text, match.end(), next_start)
        snippet = _strip_html(desc.group("snippet")) if desc else ""
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


def _search_searxng(
    query: str, count: int, instance_url: str, time_range: str | None = None,
) -> list[dict]:
    base = instance_url.rstrip("/")
    params = {'q': query, 'format': 'json'}
    if time_range:
        params['time_range'] = time_range
    url = f"{base}/search?{urlencode(params)}"
    try:
        data = http.get(url, headers={"Accept": "application/json"}, timeout=15, retries=2)
    except http.HTTPError:
        return []
    if not isinstance(data, dict):
        return []
    items: list[dict] = []
    for i, r in enumerate(data.get("results", [])):
        if len(items) >= count:
            break
        if not isinstance(r, dict):
            continue
        target = r.get("url", "")
        if not target.startswith("http"):
            continue
        items.append(_to_item(i, r.get("title", ""), target, r.get("content", "")))
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
