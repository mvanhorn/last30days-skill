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
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from .. import http, jobs
from . import detect
from .detect import PROVIDER_TALENTBREW

PAGE_SIZE = 100
_TOTAL_RE = re.compile(r'data-total-results\s*=\s*["\']?(\d+)', re.I)
_TOKEN_RE = re.compile(r"<(/?)(li|a|span)\b([^<>]*)>", re.I)
_TOKEN_NO_SPAN_RE = re.compile(r"<(/?)(li|a)\b([^<>]*)>", re.I)


def _extract_entries(html: str) -> tuple[list[dict[str, str]], int | None]:
    """Single-pass linear tokenizer for TalentBrew results HTML, preserving nested <li>."""
    bounded = html[:detect.MAX_HTML_CHARS]
    total_match = _TOTAL_RE.search(bounded)
    total = int(total_match.group(1)) if total_match else None
    if "/job/" not in bounded:
        return [], total
    token_re = _TOKEN_RE if "</span" in bounded.lower() else _TOKEN_NO_SPAN_RE
    entries: list[dict[str, str]] = []
    li_depth = 0
    current: dict[str, Any] | None = None
    link_depth = 0
    link_start = 0
    span_kind = ""
    span_depth = 0
    span_start = 0
    for match in token_re.finditer(bounded):
        closing = bool(match.group(1))
        tag = match.group(2).lower()
        if tag == "li":
            if not closing:
                if li_depth == 0:
                    current = {"href": "", "title": "", "location": "", "date": ""}
                li_depth += 1
            elif li_depth > 0:
                li_depth -= 1
                if li_depth == 0 and current is not None:
                    if current["href"]:
                        entries.append(current)
                    current = None
                    link_depth = 0
                    span_kind = ""
                    span_depth = 0
            continue
        if current is None:
            continue
        if tag == "a":
            if not closing:
                if link_depth > 0:
                    link_depth += 1
                elif not current["href"]:
                    raw_attrs = match.group(3)
                    if "/job/" in raw_attrs:
                        href = detect.parse_html_attrs(raw_attrs).get("href", "").strip()
                        if (
                            href.startswith("/job/")
                            and not href.startswith("//")
                            and "\\" not in href
                            and not any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in href)
                        ):
                            current["href"] = href
                            link_depth = 1
                            link_start = match.end()
            elif link_depth > 0:
                link_depth -= 1
                if link_depth == 0 and not current["title"]:
                    current["title"] = jobs._clean_html(bounded[link_start:match.start()])
        elif tag == "span":
            if not closing:
                if span_depth > 0:
                    span_depth += 1
                else:
                    raw_attrs = match.group(3)
                    raw_lower = raw_attrs.lower()
                    if (
                        (not current["location"] and "location" in raw_lower)
                        or (not current["date"] and "date" in raw_lower)
                    ):
                        classes = detect.parse_html_attrs(raw_attrs).get("class", "").lower()
                        if "location" in classes and not current["location"]:
                            span_kind = "location"
                            span_depth = 1
                            span_start = match.end()
                        elif "date" in classes and not current["date"]:
                            span_kind = "date"
                            span_depth = 1
                            span_start = match.end()
            elif span_depth > 0:
                span_depth -= 1
                if span_depth == 0 and span_kind:
                    current[span_kind] = jobs._clean_html(bounded[span_start:match.start()])
                    span_kind = ""
    return entries, total


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
    raw_entries, total = _extract_entries(html)
    board = str(entry["board"]).strip().rstrip(".").lower()
    base = f"https://{board}/"
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_entries:
        url = urljoin(base, row["href"])
        parsed = urlparse(url)
        if (
            parsed.scheme.lower() != "https"
            or detect.page_host(url) != board
            or parsed.port is not None
        ):
            continue
        title = row["title"]
        if not title or url in seen:
            continue
        seen.add(url)
        location = row["location"]
        posted = _us_date(row["date"]) if row["date"] else None
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
    return items, total if total is not None else len(items)


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The board in the search's own order, with the total it reports. The
    order is not shown to be by date, so the read is not newest first."""

    def page(offset: int) -> Callable[[float], Any]:
        url = listing_url(entry, offset // PAGE_SIZE + 1)

        def fetch(http_deadline: float) -> Any:
            if not detect.is_safe_public_url(url):
                raise http.HTTPError("Refused non-public URL")
            return http.get(
                url, headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                timeout=jobs.API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
                public_only=True, redirect_guard=detect.same_site_redirect_guard(url),
            )

        return fetch

    return jobs._read_newest_first(
        page, lambda payload: parse_listing(payload, entry), page_size=PAGE_SIZE,
        row_ceiling=jobs.COMPANY_BOARD_ROW_CEILING, window_start=date_range[0],
        deadline=deadline, newest_first=False,
    )


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill the full text of the given roles from the JobPosting on their pages."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        url = str(item.get("url") or "")
        if not detect.is_safe_public_url(url):
            return lambda _http_deadline: None
        return lambda http_deadline: jobs.role_page_text(
            url, timeout=jobs.API_TIMEOUT_SECONDS, deadline_monotonic=http_deadline,
            redirect_guard=detect.same_site_redirect_guard(url),
        )

    return jobs._fill_role_text(items, call, jobs.jsonld_role_text, deadline=deadline)
