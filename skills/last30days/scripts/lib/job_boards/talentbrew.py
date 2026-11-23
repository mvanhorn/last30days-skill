"""TalentBrew (Radancy): the job search's own results call.

The search page asks ``/search-jobs/results`` with ``X-Requested-With:
XMLHttpRequest`` for each page of results; the answer is JSON whose
``results`` value is the HTML list it shows, with the total in
``data-total-results``. Each result is a link to the role's page with its
title, location and posted date in classed spans. A role's page carries a
schema.org ``JobPosting`` with the full text.
"""

from __future__ import annotations

import re
import time
from html import unescape
from typing import Any, Callable
from urllib.parse import urljoin

from .. import http, jobs
from .detect import PROVIDER_TALENTBREW

PAGE_SIZE = 100
_TOTAL = re.compile(r'data-total-results="(\d+)"')
_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.I | re.S)
_LINK = re.compile(r'<a\b[^>]*href="(/job/[^"]+)"[^>]*>(.*?)</a>', re.I | re.S)
_SPAN = re.compile(r'<span\b[^>]*class="([^"]*)"[^>]*>(.*?)</span>', re.I | re.S)


def listing_url(entry: dict[str, Any], page: int = 1) -> str:
    return (
        f"https://{entry['board']}/search-jobs/results?ActiveFacetID=0"
        f"&CurrentPage={page}&RecordsPerPage={PAGE_SIZE}&SortCriteria=1&SortDirection=1"
        "&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters"
    )


def _us_date(text: str) -> str | None:
    try:
        return time.strftime("%Y-%m-%d", time.strptime(text.strip(), "%m/%d/%Y"))
    except ValueError:
        return None


def parse_listing(payload: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    html = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(html, str):
        raise jobs.BoardShapeError("no results HTML")
    total = _TOTAL.search(html)
    base = f"https://{entry['board']}/"
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _ITEM.findall(html):
        link = _LINK.search(block)
        if not link:
            continue
        url = urljoin(base, unescape(link.group(1)))
        title = jobs._clean_html(link.group(2))
        if not title or url in seen:
            continue
        seen.add(url)
        location = date = ""
        for classes, text in _SPAN.findall(block):
            value = jobs._clean_html(text)
            if "location" in classes and not location:
                location = value
            elif "date" in classes and not date:
                date = value
        posted = _us_date(date) if date else None
        items.append(jobs._ats_item(
            provider=PROVIDER_TALENTBREW,
            slug=entry["board"],
            ident=url.rstrip("/").rsplit("/", 1)[-1],
            title=title,
            url=url,
            description="",
            date=posted,
            posted_date_true=bool(posted),
            department="",
            location=location,
            company_name=str(entry.get("name") or ""),
            whole_posting=True,
        ))
    return items, int(total.group(1)) if total else len(items)


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The board in the search's own order, with the total it reports. The
    order is not shown to be by date, so the read is not newest first."""

    def page(offset: int) -> Callable[[float], Any]:
        url = listing_url(entry, offset // PAGE_SIZE + 1)
        return lambda http_deadline: http.get(
            url, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
            timeout=jobs.API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
            public_only=True,
        )

    return jobs._read_newest_first(
        page, lambda payload: parse_listing(payload, entry), page_size=PAGE_SIZE,
        row_ceiling=jobs.COMPANY_BOARD_ROW_CEILING, window_start=date_range[0],
        deadline=deadline, newest_first=False,
    )


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill the full text of the given roles from the JobPosting on their pages."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        return lambda http_deadline: jobs.role_page_text(
            str(item["url"]), timeout=jobs.API_TIMEOUT_SECONDS, deadline_monotonic=http_deadline,
        )

    return jobs._fill_role_text(items, call, jobs.jsonld_role_text, deadline=deadline)
