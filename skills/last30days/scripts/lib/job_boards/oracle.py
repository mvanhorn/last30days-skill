"""Oracle Recruiting Cloud: the candidate-experience REST API.

One listing call returns up to 200 requisitions, newest first, with the
board's total; one more call per role gives its full text. A board lives on
the tenant's ``*.fa.*.oraclecloud.com`` host or under the company's own host
(Dell), and is named by its site number (``CX_1001``). ``PostedDate`` is the
day the requisition was posted to that site.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import quote

from .. import http, jobs
from . import detect
from .detect import PROVIDER_ORACLE

PAGE_SIZE = 200
_LIST_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
_DETAIL_PATH = "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"


def listing_url(entry: dict[str, Any], offset: int = 0) -> str:
    site = quote(str(entry["site_number"]), safe="")
    finder = (
        f"findReqs;siteNumber={site},limit={PAGE_SIZE},"
        f"offset={offset},sortBy=POSTING_DATES_DESC"
    )
    return (
        f"https://{entry['board']}{_LIST_PATH}?onlyData=true"
        f"&expand=requisitionList.secondaryLocations&finder={finder}"
    )


def detail_url(entry: dict[str, Any], ident: str) -> str:
    encoded_ident = quote(str(ident), safe="")
    site = quote(str(entry["site_number"]), safe="")
    return (
        f"https://{entry['board']}{_DETAIL_PATH}?expand=all&onlyData=true"
        f'&finder=ById;Id="{encoded_ident}",siteNumber={site}'
    )


def parse_listing(payload: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Rows and the reported total from one listing page."""
    found = payload.get("items") if isinstance(payload, dict) else None
    search = found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None
    rows = search.get("requisitionList") if search else None
    if not isinstance(rows, list):
        raise jobs.BoardShapeError("no requisitionList")
    board, site = entry["board"], entry["site_number"]
    encoded_site = quote(str(site), safe="")
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("Id") or not row.get("Title"):
            continue
        ident = str(row["Id"])
        posted = jobs._date_part(row.get("PostedDate"))
        places = [str(row.get("PrimaryLocation") or "").strip()]
        for extra in row.get("secondaryLocations") or []:
            if isinstance(extra, dict) and extra.get("Name"):
                places.append(str(extra["Name"]).strip())
        places = [place for place in dict.fromkeys(places) if place]
        summary = str(row.get("ShortDescriptionStr") or "").strip()
        item = jobs._ats_item(
            provider=PROVIDER_ORACLE,
            slug=board,
            ident=ident,
            title=str(row["Title"]).strip(),
            url=f"https://{board}/hcmUI/CandidateExperience/en/sites/{encoded_site}/job/{quote(ident, safe='')}",
            description=summary,
            description_blocks=summary,
            date=posted,
            posted_date_true=bool(posted),
            department=str(row.get("JobFamily") or row.get("JobFunction") or "").strip(),
            location=places[0] if places else "",
            company_name=str(entry.get("name") or ""),
            whole_posting=True,
        )
        item["locations"] = places
        item["detail_url"] = detail_url(entry, ident)
        items.append(item)
    return items, jobs._reported_total(search, "TotalJobsCount")


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The board's roles, newest first, with the total it reports."""

    def page(offset: int) -> Callable[[float], Any]:
        url = listing_url(entry, offset)

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


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill the full text of the given roles, one call each."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        url = str(item.get("detail_url") or "")
        if not detect.is_safe_public_url(url):
            return lambda _http_deadline: None
        return lambda http_deadline: http.get(
            url, headers={"Accept": "application/json"},
            timeout=jobs.API_TIMEOUT_SECONDS, retries=1, deadline_monotonic=http_deadline,
            public_only=True, redirect_guard=detect.same_site_redirect_guard(url),
        )

    def text_of(payload: Any) -> tuple[str, str] | None:
        found = payload.get("items") if isinstance(payload, dict) else None
        role = found[0] if isinstance(found, list) and found and isinstance(found[0], dict) else None
        if role is None:
            return None
        parts = [
            str(role.get(key) or "")
            for key in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                "ExternalQualificationsStr",
            )
            if role.get(key)
        ]
        html = "<br/>".join(parts)
        return (jobs._block_text(html), jobs._clean_html(html)) if html.strip() else None

    return jobs._fill_role_text(items, call, text_of, deadline=deadline)
