"""Kula: a company's public careers page on ``careers.kula.ai/<slug>``.

The page is a Next.js page: its roles are in the React Server Components
payload it carries (``self.__next_f.push`` chunks), each with its title,
team, offices and the day it was launched, which is the day its posting
gives as posted. The page lists every open role at once. A role's own page
carries a ``JobPosting`` with the role's text. Kula's API needs a key and is
not used.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

from .. import http, jobs
from .detect import KULA_HOST, PROVIDER_KULA

_PUSH = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
_JOBS_KEY = re.compile(r'"jobs":\[')
_APP_NAME = re.compile(r'<meta[^>]+name="application-name"[^>]+content="([^"]+)"', re.I)


def listing_url(entry: dict[str, Any]) -> str:
    return f"https://{KULA_HOST}/{entry['slug']}"


def _payload(html: str) -> str:
    """The page's RSC payload: the text of its push chunks, in order."""
    parts: list[str] = []
    for chunk in _PUSH.findall(html):
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], str):
            parts.append(data[1])
    return "".join(parts)


def _role_list(payload: str) -> list[dict[str, Any]] | None:
    decoder = json.JSONDecoder()
    for match in _JOBS_KEY.finditer(payload):
        try:
            rows, _end = decoder.raw_decode(payload, match.end() - 1)
        except ValueError:
            continue
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return rows
    return None


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def parse_listing(html: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    rows = _role_list(_payload(html)) if isinstance(html, str) else None
    if rows is None:
        raise jobs.BoardShapeError("no jobs list in the page payload")
    slug = entry["slug"]
    # The address of each role's page, as the page links it.
    paths = {
        ident: path for path, ident in re.findall(rf'href="(/{re.escape(slug)}/(\d+)-[^"]*)"', html)
    }
    named = _APP_NAME.search(html)
    company = named.group(1).strip() if named else ""
    items: list[dict[str, Any]] = []
    for row in rows:
        title = " ".join(str(row.get("title") or "").split())
        if not title or row.get("listed") is False or row.get("on_career_page") is False:
            continue
        ident = str(row.get("id") or "").strip()
        if not ident:
            continue
        ats = row.get("ats_job") if isinstance(row.get("ats_job"), dict) else {}
        team = ats.get("ats_department") if isinstance(ats.get("ats_department"), dict) else {}
        places = [
            str(office.get("location") or office.get("name") or "").strip()
            for office in ats.get("offices") or [] if isinstance(office, dict)
        ]
        places = [place for place in places if place]
        posted = jobs._date_part(row.get("launch_at"))
        path = paths.get(ident) or f"/{slug}/{ident}-{_slugify(title)}"
        item = jobs._ats_item(
            provider=PROVIDER_KULA,
            slug=slug,
            ident=ident,
            title=title,
            url=f"https://{KULA_HOST}{path}",
            description="",
            date=posted,
            posted_date_true=bool(posted),
            department=" ".join(str(team.get("name") or "").split()),
            location=places[0] if places else "",
            company_name=company,
            whole_posting=True,
        )
        if len(places) > 1:
            item["locations"] = places
        items.append(item)
    return items, len(items)


def read_board(
    entry: dict[str, Any], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every role the board lists, from its one page.

    A page whose payload holds no role list raises ``BoardShapeError``, so
    the run says the board could not be read and goes on.
    """
    html = http.get_text(
        listing_url(entry), accept="text/html,*/*", timeout=jobs.API_TIMEOUT_SECONDS,
        retries=2, deadline_monotonic=deadline,
    )
    items, total = parse_listing(html or "", entry)
    return items, {"board_total": total}


def _posted_date(html: Any) -> str | None:
    if not isinstance(html, str):
        return None
    for block in jobs._JSONLD_RE.findall(html):
        for obj in jobs._walk_jsonld(block):
            if isinstance(obj, dict) and jobs._is_job_posting(obj):
                return jobs._date_part(obj.get("datePosted"))
    return None


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill each role's text from the JobPosting on its page, and its posted
    date when the listing gave none."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        def fetch(http_deadline: float) -> Any:
            html = jobs.role_page_text(
                str(item["url"]), timeout=jobs.API_TIMEOUT_SECONDS, deadline_monotonic=http_deadline,
            )
            posted = None if item.get("date") else _posted_date(html)
            if posted:
                item.update(date=posted, date_confidence="high", posted_date_true=True)
            return html
        return fetch

    return jobs._fill_role_text(items, call, jobs.jsonld_role_text, deadline=deadline)
