"""Google: the server-rendered results page of Google's careers site.

``/about/careers/applications/jobs/results/?sort_by=date&page=N`` embeds its
twenty roles as the ``ds:1`` ``AF_initDataCallback`` data: each role's id,
title, company, locations and text, and the board's total. The order is by
the day a role was last changed, and no field is shown to be the day it was
first posted, so no row claims a posted date and the board prints no count
of roles new in the window.

Google's robots.txt disallows this path. The owner decided the lane reads it
anyway (see SECURITY.md); ``LAST30DAYS_JOBS_RESPECT_ROBOTS=1`` restores the
skip. The read is kept small and slow: ``PAGE_LIMIT`` pages, two at a time.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .. import http, jobs

PROVIDER_GOOGLE = "google"
PAGE_SIZE = 20
# Twenty-five pages of twenty: 500 of the newest-changed roles, enough for the
# digest's forty, in about five seconds.
PAGE_LIMIT = 25
WORKERS = 2
RESULTS_URL = "https://www.google.com/about/careers/applications/jobs/results/"
_DATA = re.compile(r"AF_initDataCallback\(\{key:\s*'ds:1'.*?data:", re.S)


def listing_url(page: int = 1) -> str:
    return f"{RESULTS_URL}?sort_by=date&page={page}"


def page_data(html: str) -> Any:
    """The ``ds:1`` data of one results page, or None."""
    match = _DATA.search(html or "")
    if not match:
        return None
    try:
        found, _end = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
    except ValueError:
        return None
    return found


def _text(field: Any) -> str:
    """A ``[null, "<html>"]`` text field's HTML, or ''."""
    if isinstance(field, list) and len(field) > 1 and isinstance(field[1], str):
        return field[1]
    return ""


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def parse_listing(payload: Any) -> tuple[list[dict[str, Any]], int]:
    data = page_data(payload) if isinstance(payload, str) else None
    rows = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else None
    if rows is None:
        raise jobs.BoardShapeError("no ds:1 job list")
    total = data[2] if len(data) > 2 and isinstance(data[2], int) else 0
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 11 or not row[0] or not row[1]:
            continue
        ident, title = str(row[0]), str(row[1]).strip()
        places = [
            str(place[0]).strip() for place in row[9] or []
            if isinstance(place, list) and place and place[0]
        ] if isinstance(row[9], list) else []
        html = "<br/>".join(part for part in (
            _text(row[10]),
            "<h3>Responsibilities</h3>" + _text(row[3]) if _text(row[3]) else "",
            _text(row[4]),
        ) if part)
        item = jobs._ats_item(
            provider=PROVIDER_GOOGLE,
            slug="www.google.com",
            ident=ident,
            title=title,
            url=f"{RESULTS_URL}{ident}-{_slug(title)}",
            description=jobs._clean_html(html),
            description_blocks=jobs._block_text(html),
            date=None,
            department="",
            location=places[0] if places else "",
            company_name=str(row[7] or "Google").strip(),
            whole_posting=True,
        )
        item["locations"] = places
        items.append(item)
    return items, total


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The newest-changed roles, ``PAGE_LIMIT`` pages at most, with the total."""

    def page(offset: int) -> Callable[[float], Any]:
        url = listing_url(offset // PAGE_SIZE + 1)
        return lambda http_deadline: _page_text(url, http_deadline)

    items, info = jobs._read_newest_first(
        page, parse_listing, page_size=PAGE_SIZE, row_ceiling=PAGE_SIZE * PAGE_LIMIT,
        window_start=None, deadline=deadline, workers=WORKERS,
    )
    # Ordered by the day a role last changed: the window is never counted.
    info["board_window_covered"] = False
    return items, info


def _page_text(url: str, http_deadline: float) -> str:
    """One results page; a page that does not answer is a failed call."""
    text = http.get_text(
        url, accept="text/html", timeout=jobs.API_TIMEOUT_SECONDS, retries=1,
        deadline_monotonic=http_deadline,
    )
    if text is None:
        raise http.HTTPError(f"Google results page did not answer: {url}")
    return text
