"""IBM: the search API behind ``www.ibm.com/careers/search``.

IBM's careers site (``careers.ibm.com``) answers automated requests with an
empty 202, sitemaps included. Its search page loads the roles in the browser
from ``www-api.ibm.com/search/api/v2``, which answers up to 100 a call with
each role's title, team, location and the opening of its text, and the
board's total. No field is a posting date, so no row claims one.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import http, jobs

PROVIDER_IBM = "ibm"
PAGE_SIZE = 100
SEARCH_URL = "https://www-api.ibm.com/search/api/v2"


def listing_url(entry: dict[str, Any] | None = None) -> str:
    return SEARCH_URL


def search_body(offset: int) -> dict[str, Any]:
    return {
        "appId": "careers",
        "scopes": ["careers2"],
        "query": {"bool": {"must": []}},
        "size": PAGE_SIZE,
        "from": offset,
        "sort": [{"_score": "desc"}],
        "lang": "zz",
        "localeSelector": {},
        "sm": {"query": "", "lang": "zz"},
        "_source": [
            "_id", "title", "url", "description", "language",
            "field_keyword_08", "field_keyword_17", "field_keyword_18", "field_keyword_19",
        ],
    }


def parse_listing(payload: Any) -> tuple[list[dict[str, Any]], int]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    rows = hits.get("hits") if isinstance(hits, dict) else None
    if not isinstance(rows, list):
        raise jobs.BoardShapeError("no hits")
    total = hits.get("total")
    total = int(total.get("value") or 0) if isinstance(total, dict) else jobs._reported_total(hits, "total")
    items: list[dict[str, Any]] = []
    for row in rows:
        source = row.get("_source") if isinstance(row, dict) else None
        if not isinstance(source, dict) or not source.get("title") or not source.get("url"):
            continue
        url = str(source["url"]).strip()
        text = str(source.get("description") or "").strip()
        items.append(jobs._ats_item(
            provider=PROVIDER_IBM,
            slug="careers.ibm.com",
            ident=url.rsplit("=", 1)[-1],
            title=str(source["title"]).strip(),
            url=url,
            description=jobs._clean_html(text),
            description_blocks=jobs._block_text(text),
            date=None,
            department=str(source.get("field_keyword_08") or "").strip(),
            location=str(source.get("field_keyword_19") or "").strip(),
            company_name="IBM",
            whole_posting=True,
        ))
    return items, total


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The whole board up to the row ceiling, in the search's own order."""

    def page(offset: int) -> Callable[[float], Any]:
        body = search_body(offset)
        return lambda http_deadline: http.post(
            SEARCH_URL, body,
            headers={"Accept": "application/json"},
            timeout=jobs.API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
        )

    items, info = jobs._read_newest_first(
        page, parse_listing, page_size=PAGE_SIZE, row_ceiling=jobs.COMPANY_BOARD_ROW_CEILING,
        window_start=None, deadline=deadline, newest_first=False,
    )
    info["board_window_covered"] = False
    return items, info
