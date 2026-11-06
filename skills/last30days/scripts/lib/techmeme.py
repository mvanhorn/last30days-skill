"""Techmeme tech-news source for last30days.

Fetches Techmeme's live archive search page (``/search/d3results.jsp``, back
to ~2005, no auth) directly with the stdlib -- no third-party binary -- and
parses it with a port of the reviewed techmeme-pp-cli parser (anchor + iinf
date-block pairing). Each record is ``{num, source, headline, link, date}``,
where ``date`` is ISO ``YYYY-MM-DD`` or ``""`` when the markup was
unparseable.

Activation: always available (keyless, no install).

Dates: the adapter windows records to the research range
(``from_date <= date <= to_date``) so archive hits from years past never
masquerade as current news. Records with no usable date are kept but flow
downstream with no date, so ``normalize._normalize_techmeme`` assigns
``date_confidence: low``. Headlines are never stamped with today's date.
Publication-name header rows (very short ``headline`` values) are dropped;
ranking is topic relevance plus rank decay.
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode, urlsplit

from . import http, log
from .relevance import token_overlap_relevance


SEARCH_URL = "https://www.techmeme.com/search/d3results.jsp"

DEPTH_CONFIG = {
    "quick": 8,
    "default": 16,
    "deep": 30,
}

# A real story headline is a sentence; bare publication-name rows ("TechCrunch",
# "New York Times") are section headers in the feed, not stories. Require at
# least this many words to keep a record.
MIN_HEADLINE_WORDS = 4

SEARCH_TIMEOUT = 30

# Ported from techmeme-pp-cli internal/cli/search.go (pinned fork commit).
_ANCHOR_RE = re.compile(r'<A\s+[^>]*?HREF="([^"]+)"[^>]*>([^<]+)</A>', re.IGNORECASE)
_IINF_DATE_RE = re.compile(
    r'<div class="iinf">\s*<span class="idate">([^<]*)</span>', re.IGNORECASE | re.DOTALL
)
_PREVNEXT_RE = re.compile(r'<div class="prevnext">', re.IGNORECASE)
_PERMALINK_RE = re.compile(r"techmeme\.com/\d{6}/p\d+", re.IGNORECASE)
_CANVAS_RE = re.compile(r'class="resultscanvas"', re.IGNORECASE)
_SPONSORS_RE = re.compile(r'class="sponsorscanvas"', re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_DATE_LAYOUTS = ("%B %d, %Y, %I:%M %p", "%b %d, %Y, %I:%M %p", "%B %d, %Y", "%b %d, %Y")

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _log(msg: str) -> None:
    log.source_log("Techmeme", msg, tty_only=False)


def _build_search_url(topic: str) -> str:
    # Techmeme's search has no result-limit parameter, so the depth cap is
    # applied client-side after windowing.
    return f"{SEARCH_URL}?{urlencode({'q': topic, 'wm': 'false'})}"


def _clean_title(text: str) -> str:
    text = _TAG_RE.sub("", text).replace("&nbsp;", " ")
    return _html.unescape(text).strip()


def _parse_date(text: str) -> str:
    text = " ".join(text.split())
    for layout in _DATE_LAYOUTS:
        try:
            return datetime.strptime(text, layout).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _domain(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return url
    return host[4:] if host.startswith("www.") else host


def parse_search_page(page: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Parse a Techmeme search results page into records.

    Returns ``(records, markup_shift)``. ``markup_shift`` is True when story
    anchors and date blocks did not pair up (Techmeme markup may have changed).
    """
    m = _CANVAS_RE.search(page)
    if m:
        page = page[m.end():]
    m = _SPONSORS_RE.search(page)
    if m:
        page = page[: m.start()]
    m = _PREVNEXT_RE.search(page)
    if m:
        page = page[: m.start()]

    anchors: List[Tuple[int, str, str]] = []
    for am in _ANCHOR_RE.finditer(page):
        href = am.group(1)
        title = _clean_title(am.group(2))
        if "/r2/" in href or _PERMALINK_RE.search(href):
            continue
        if "techmeme.com/" in href and "context" in title:
            continue
        anchors.append((am.start(), _html.unescape(href), title))

    dates = list(_IINF_DATE_RE.finditer(page))
    if not dates:
        return [], bool(anchors)

    records: List[Dict[str, Any]] = []
    ai = 0
    for dm in dates:
        story = None
        while ai < len(anchors) and anchors[ai][0] < dm.start():
            story = anchors[ai]
            ai += 1
        if story is None:
            continue
        _, href, title = story
        records.append(
            {
                "num": len(records) + 1,
                "source": _domain(href),
                "headline": title,
                "link": href,
                "date": _parse_date(dm.group(1)),
            }
        )
    return records, len(records) < len(dates)


def _record_iso_date(rec: Dict[str, Any]) -> str | None:
    """The record's ``date`` as a valid ISO YYYY-MM-DD string, else None.

    The parser emits ``""`` when Techmeme's date markup was unparseable.
    Anything that isn't a clean ISO date is
    treated as absent."""
    value = rec.get("date")
    if isinstance(value, str) and _ISO_DATE_RE.match(value.strip()):
        return value.strip()
    return None


def _fetch(url: str, timeout: int) -> Dict[str, Any]:
    """GET the search page and return ``{"results": [...records...]}``.
    Never raises."""
    try:
        page = http.request(
            "GET",
            url,
            headers={"Accept": "text/html", "User-Agent": http.BROWSER_USER_AGENT},
            timeout=timeout,
            retries=2,
            raw=True,
        )
    except http.HTTPError as exc:
        _log(f"request failed: {exc}")
        return {"results": [], "error": str(exc)}
    if not isinstance(page, str) or not page.strip():
        return {"results": []}
    records, markup_shift = parse_search_page(page)
    if markup_shift:
        _log(
            "search results region shape unrecognized (anchors and date blocks "
            "did not pair); Techmeme markup may have changed"
        )
    return {"results": records}


def search_techmeme(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search Techmeme's live archive.

    Windows records to ``from_date..to_date`` on each record's own ISO date
    (lexicographic compare is exact for ISO strings). Records with no usable
    date are kept -- their recency is resolved downstream as low confidence.
    There is deliberately no keep-all fallback when nothing is in-window:
    Techmeme's archive reaches back decades, so zero in-window records means
    zero results, not "serve stale news". Returns a dict with a ``results``
    list of raw records; on failure ``results`` is empty.
    """
    if not topic or not topic.strip():
        return {"results": []}
    limit = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    _log(f"search '{topic}' (cap={limit})")
    response = _fetch(_build_search_url(topic), timeout=SEARCH_TIMEOUT)
    records = response.get("results") or []
    if isinstance(records, list):
        # Hard date window: drop records whose date falls outside the research
        # range; keep undated records (unparseable markup).
        dated_in_window = []
        undated = []
        dropped = 0
        for rec in records:
            iso = _record_iso_date(rec)
            if iso is None:
                undated.append(rec)
            elif from_date <= iso <= to_date:
                dated_in_window.append(rec)
            else:
                dropped += 1
        if dropped:
            _log(f"dropped {dropped} records outside {from_date}..{to_date}")
        if records and not dated_in_window and not dropped:
            # Every record lacks a usable date: a Techmeme markup change
            # upstream. Windowing is inactive.
            _log(
                "no records carry usable dates; date windowing inactive "
                "(Techmeme date markup may have changed)"
            )
        # Techmeme returns all matches; apply the depth cap after windowing.
        # Dated in-window records take cap slots first so undated archive
        # hits can never evict confirmed-fresh stories; undated records fill
        # whatever slots remain.
        response["results"] = (dated_in_window + undated)[:limit]
    _log(f"found {len(response.get('results') or [])} records")
    return response


def _is_story_headline(headline: str, source: str) -> bool:
    """Reject bare publication-name header rows; keep sentence-shaped stories."""
    if not headline:
        return False
    if len(headline.split()) < MIN_HEADLINE_WORDS:
        return False
    # A row whose headline is just the publication name is a header.
    if source and headline.strip().lower() == source.strip().lower():
        return False
    return True


def parse_techmeme_response(
    response: Dict[str, Any],
    query: str = "",
) -> List[Dict[str, Any]]:
    """Parse a Techmeme search envelope into normalized item dicts.

    Drops publication-name header rows and records missing a link. Each item
    carries the record's own ISO date, or None when the record has no usable
    date (never today's date -- undated items get ``date_confidence: low``
    downstream). Computes a token-overlap relevance hint. Returns dicts ready
    for ``normalize._normalize_techmeme``.
    """
    raw = response.get("results") if isinstance(response, dict) else None
    if not isinstance(raw, list):
        return []

    items: List[Dict[str, Any]] = []
    for i, rec in enumerate(raw):
        if not isinstance(rec, dict):
            continue
        headline = " ".join(str(rec.get("headline") or "").split()).strip()
        source_name = str(rec.get("source") or "").strip()
        if not _is_story_headline(headline, source_name):
            continue
        link = str(rec.get("link") or "").strip()
        if not link:
            continue

        rank_decay = max(0.3, 1.0 - (i * 0.03))
        content_score = token_overlap_relevance(query, headline) if query else 0.5
        relevance = min(1.0, 0.55 * rank_decay + 0.45 * content_score)

        items.append(
            {
                "id": link,
                "title": headline,
                "url": link,
                "source_name": source_name,
                "date": _record_iso_date(rec),
                "engagement": {},
                "relevance": round(relevance, 2),
                "why_relevant": (
                    f"Techmeme headline ({source_name})" if source_name else "Techmeme headline"
                ),
            }
        )

    return items
