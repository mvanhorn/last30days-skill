"""Phenom: the careers site's ``/widgets`` search, as its own page calls it.

The search page embeds ``phApp.ddo`` with ten roles; the same ``refineSearch``
query sent to ``/widgets`` answers up to 500 a call, with the board's total
in ``totalHits``. The search cannot be sorted by date, so the board is read
whole (to the row ceiling) rather than newest first. A role's page embeds its
full text under ``jobDetail``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import quote, urlparse

from .. import http, jobs
from . import detect
from .detect import PROVIDER_PHENOM

PAGE_SIZE = 500
_DDO = re.compile(r"phApp\.ddo\s*=\s*")


def widgets_url(entry: dict[str, Any]) -> str:
    return f"https://{entry['board']}/widgets"


def search_body(entry: dict[str, Any], offset: int) -> dict[str, Any]:
    country = entry.get("country") or "global"
    return {
        "lang": f"{entry.get('lang') or 'en'}_{country}",
        "deviceType": "desktop",
        "country": country,
        "pageName": "search-results",
        "ddoKey": "refineSearch",
        "sortBy": "",
        "subsearch": "",
        "from": offset,
        "jobs": True,
        "counts": False,
        "all_fields": [],
        "size": PAGE_SIZE,
        "clearAll": False,
        "jdsource": "facets",
        "isSliderEnable": False,
        "pageId": "page20",
        "siteType": "external",
        "keywords": "",
        "global": True,
        "selected_fields": {},
        "locationData": {},
    }


def _safe_base_url(entry: dict[str, Any]) -> str:
    board = str(entry["board"]).strip().rstrip(".").lower()
    raw = str(entry.get("base_url") or "").strip()
    if raw and detect.is_safe_public_url(raw):
        parsed = urlparse(raw)
        if (
            parsed.scheme.lower() == "https"
            and parsed.port in (None, 443)
            and detect.page_host(raw) == board
            and not parsed.query
            and not parsed.fragment
        ):
            path = parsed.path or "/"
            if not path.startswith("/"):
                path = "/" + path
            if not path.endswith("/"):
                path += "/"
            return f"https://{board}{path}"
    return f"https://{board}/"


def parse_listing(payload: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    search = payload.get("refineSearch") if isinstance(payload, dict) else None
    data = search.get("data") if isinstance(search, dict) else None
    rows = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise jobs.BoardShapeError("no refineSearch jobs")
    base = _safe_base_url(entry)
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("title") or not row.get("jobId"):
            continue
        ident = str(row["jobId"]).strip()
        if not ident:
            continue
        posted = jobs._date_part(row.get("postedDate"))
        places = [str(place).strip() for place in row.get("multi_location") or [] if place]
        teaser = str(row.get("descriptionTeaser") or "").strip()
        item = jobs._ats_item(
            provider=PROVIDER_PHENOM,
            slug=entry["board"],
            ident=ident,
            title=str(row["title"]).strip(),
            url=f"{base}job/{quote(ident, safe='')}",
            description=teaser,
            description_blocks=teaser,
            date=posted,
            posted_date_true=bool(posted),
            department=str(row.get("category") or "").strip(),
            location=str(row.get("location") or row.get("cityStateCountry") or "").strip(),
            company_name=str(row.get("companyName") or entry.get("name") or "").strip(),
            whole_posting=True,
        )
        if places:
            item["locations"] = places
        items.append(item)
    return items, jobs._reported_total(search, "totalHits")


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The whole board up to the row ceiling, with the total it reports."""

    def page(offset: int) -> Callable[[float], Any]:
        body = search_body(entry, offset)
        url = widgets_url(entry)

        def fetch(http_deadline: float) -> Any:
            if not detect.is_safe_public_url(url):
                raise http.HTTPError("Refused non-public URL")
            return http.post(
                url, body, headers={"Accept": "application/json"},
                timeout=jobs.API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
                public_only=True, redirect_guard=detect.same_site_redirect_guard(url),
            )

        return fetch

    return jobs._read_newest_first(
        page, lambda payload: parse_listing(payload, entry), page_size=PAGE_SIZE,
        row_ceiling=jobs.COMPANY_BOARD_ROW_CEILING, window_start=date_range[0],
        deadline=deadline, newest_first=False,
    )


def page_object(html: str) -> dict[str, Any] | None:
    """The ``phApp.ddo`` object a Phenom page embeds, or None."""
    bounded = (html or "")[:detect.MAX_HTML_CHARS]
    match = _DDO.search(bounded)
    if not match:
        return None
    start = bounded.find("{", match.end())
    if start < 0:
        return None
    try:
        found, _end = json.JSONDecoder().raw_decode(bounded, start)
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill the full text of the given roles from their own pages."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        url = str(item.get("url") or "")
        if not detect.is_safe_public_url(url):
            return lambda _http_deadline: None
        return lambda http_deadline: jobs.role_page_text(
            url, timeout=jobs.API_TIMEOUT_SECONDS, deadline_monotonic=http_deadline,
            redirect_guard=detect.same_site_redirect_guard(url),
        )

    def text_of(payload: Any) -> tuple[str, str] | None:
        found = page_object(payload) if isinstance(payload, str) else None
        detail = (found or {}).get("jobDetail")
        role = ((detail or {}).get("data") or {}).get("job") if isinstance(detail, dict) else None
        html = str((role or {}).get("description") or "") if isinstance(role, dict) else ""
        return (jobs._block_text(html), jobs._clean_html(html)) if html.strip() else None

    return jobs._fill_role_text(items, call, text_of, deadline=deadline)
