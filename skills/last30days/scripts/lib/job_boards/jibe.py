"""Jibe (an iCIMS front end): the careers site's own ``/api/jobs`` search.

One call returns up to 100 roles, newest first, each with its full text and
the day it was posted, and the board's total.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

from .. import http, jobs
from . import detect
from .detect import PROVIDER_JIBE

PAGE_SIZE = 100


def listing_url(entry: dict[str, Any], page: int = 1, keywords: str = "") -> str:
    """One listing call; ``keywords`` makes it a search of the board."""
    return (
        f"https://{entry['board']}/api/jobs?page={page}&limit={PAGE_SIZE}"
        "&sortBy=posted_date&descending=true&internal=false"
        + (f"&keywords={quote(keywords)}" if keywords else "")
    )


def parse_listing(payload: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise jobs.BoardShapeError("no jobs list")
    board = entry["board"]
    raw_jobs_path = str(entry.get("jobs_path") or "/jobs").strip()
    if (
        not raw_jobs_path.startswith("/")
        or raw_jobs_path.startswith("//")
        or "\\" in raw_jobs_path
        or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw_jobs_path)
    ):
        raw_jobs_path = "/jobs"
    jobs_path = raw_jobs_path.rstrip("/")
    items: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") if isinstance(row, dict) else None
        if not isinstance(data, dict) or not data.get("title"):
            continue
        ident = str(data.get("req_id") or data.get("slug") or "").strip()
        if not ident:
            continue
        html = "<br/>".join(
            str(data.get(key) or "")
            for key in ("description", "responsibilities", "qualifications") if data.get(key)
        )
        posted = jobs._date_part(data.get("posted_date"))
        categories = [
            str(category.get("name") or "").strip()
            for category in data.get("categories") or [] if isinstance(category, dict)
        ]
        role_slug = quote(str(data.get("slug") or ident).strip(), safe="")
        items.append(jobs._ats_item(
            provider=PROVIDER_JIBE,
            slug=board,
            ident=ident,
            title=str(data["title"]).strip(),
            url=f"https://{board}{jobs_path}/{role_slug}",
            description=jobs._clean_html(html),
            description_blocks=jobs._block_text(html),
            date=posted,
            posted_date_true=bool(posted),
            department=str(data.get("department") or "").strip() or next(
                (category for category in categories if category), "",
            ),
            location=str(data.get("full_location") or data.get("location_name") or "").strip(),
            company_name=str(data.get("hiring_organization") or "").strip(),
            whole_posting=True,
        ))
    return items, jobs._reported_total(payload, "totalCount")


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The board's roles, newest first, with the total it reports."""

    def page(offset: int) -> Callable[[float], Any]:
        url = listing_url(entry, offset // PAGE_SIZE + 1)

        def fetch(http_deadline: float) -> Any:
            if not detect.is_safe_public_url(url):
                raise http.HTTPError("Refused non-public URL")
            return http.get(
                url, headers={"Accept": "application/json"},
                timeout=jobs.API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
                public_only=True, redirect_guard=detect.same_site_redirect_guard(url),
            )

        return fetch

    return jobs._read_newest_first(
        page, lambda payload: parse_listing(payload, entry), page_size=PAGE_SIZE,
        row_ceiling=jobs.COMPANY_BOARD_ROW_CEILING, window_start=date_range[0],
        deadline=deadline,
    )


def search_story(
    entry: dict[str, Any], term: str, *, deadline_monotonic: float,
) -> list[dict[str, Any]]:
    """The board's roles the site's own search finds for a story term."""
    url = listing_url(entry, 1, term)
    if not detect.is_safe_public_url(url):
        raise http.HTTPError("Refused non-public URL")
    payload = http.get(
        url, headers={"Accept": "application/json"},
        timeout=jobs.STORY_TIMEOUT_SECONDS, retries=1, deadline_monotonic=deadline_monotonic,
        public_only=True, redirect_guard=detect.same_site_redirect_guard(url),
    )
    return parse_listing(payload, entry)[0]
