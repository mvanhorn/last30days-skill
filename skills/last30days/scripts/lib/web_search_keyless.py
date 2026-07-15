"""Keyless web search (floor tier for engine-side general web).

Returns ranked web results for a query with no API key. This is strictly the
FLOOR of the search-source ladder:

    host-native search  >  paid engine backend  >  keyless engine search

It must never run on a host that has native search (the model does it better
there) or preempt a configured paid backend. The pipeline/grounding layer owns
that gating; this module just performs the search when asked.

Two vendor-neutral rungs, both stdlib-only via :mod:`http`:
  1. DuckDuckGo HTML endpoint (no key, no instance to maintain).
  2. A configurable SearXNG instance returning JSON (``LAST30DAYS_SEARXNG_URL``),
     tried when the primary yields nothing.

Never raises. Returns results in the same dict shape as the paid backends in
:mod:`grounding` so they flow through normalize/score/dedupe unchanged. On total
failure returns ``([], artifact)`` with a degraded reason in the artifact, so the
source-health layer can report it.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlencode, urlparse

from . import http

KEYLESS_BACKEND = "keyless"

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# Floor-tier relevance: below the paid backends' 0.8 so fusion prefers paid/native
# results when both are present.
_KEYLESS_RELEVANCE = 0.6

_TAG_RE = re.compile(r"<[^>]+>")
_RESULT_A_RE = re.compile(
    r'class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _domain(url: str) -> str:
    # Normalize identically to grounding._domain (strip + lowercase) so keyless
    # and paid results dedupe/group consistently by source_domain.
    try:
        return urlparse(url).netloc.strip().lower()
    except (ValueError, AttributeError):
        return ""


def _strip_html(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment or "")).strip()


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


def keyless_search(
    query: str,
    date_range: tuple[str, str],
    config: dict,
    count: int = 5,
) -> tuple[list[dict], dict]:
    """Run keyless web search; returns (items, artifact). Never raises."""
    items = _search_ddg(query, count)
    used = "ddg"
    if not items:
        searxng_url = (config.get("LAST30DAYS_SEARXNG_URL") or "").strip()
        if searxng_url:
            items = _search_searxng(query, count, searxng_url)
            used = "searxng"
    artifact = {
        "label": "keyless",
        "webSearchQueries": [query],
        "resultCount": len(items),
        "keyless_backend": used,
    }
    if not items:
        artifact["reason"] = "keyless-search-unavailable"
    return items, artifact


def _search_ddg(query: str, count: int) -> list[dict]:
    url = f"{_DDG_HTML_URL}?{urlencode({'q': query})}"
    text = http.get_text(url, accept="text/html", retries=2)
    if not text:
        return []
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


def _search_searxng(query: str, count: int, instance_url: str) -> list[dict]:
    base = instance_url.rstrip("/")
    url = f"{base}/search?{urlencode({'q': query, 'format': 'json'})}"
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
