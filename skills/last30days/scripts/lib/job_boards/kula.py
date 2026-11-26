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
from urllib.parse import quote, urljoin, urlparse

from .. import http, jobs
from . import detect
from .detect import KULA_HOST, PROVIDER_KULA

_PUSH_MARKER = "self.__next_f.push("
_JOBS_KEY = '"jobs":'
_JSON_ARRAY_ELEM_STARTS = frozenset('0123456789-"[{tfn')
_MAX_DECODE_FAILURES = 16
_ANCHOR_TAG_RE = re.compile(r"<a\b([^<>]*)>", re.I)


def listing_url(entry: dict[str, Any]) -> str:
    slug = quote(str(entry["slug"]).strip("/"), safe="")
    return f"https://{KULA_HOST}/{slug}"


def _extract_script_bodies(html: str) -> list[str]:
    return detect.extract_script_bodies(html)


def _extract_push_chunks(text: str) -> list[str]:
    """Iteratively extract ``self.__next_f.push([...])`` string payloads in linear time."""
    parts: list[str] = []
    decoder = json.JSONDecoder()
    pos = 0
    limit = len(text)
    failures = 0
    while pos < limit:
        idx = text.find(_PUSH_MARKER, pos)
        if idx < 0:
            break
        start = idx + len(_PUSH_MARKER)
        while start < limit and text[start].isspace():
            start += 1
        if start >= limit or text[start] != "[":
            pos = start
            continue
        peek = start + 1
        while peek < limit and text[peek].isspace():
            peek += 1
        if peek >= limit or (text[peek] != "]" and text[peek] not in _JSON_ARRAY_ELEM_STARTS):
            pos = peek
            continue
        try:
            data, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError as exc:
            failures += 1
            if "Unterminated" in exc.msg or exc.pos >= limit - 1 or failures >= _MAX_DECODE_FAILURES:
                break
            pos = max(start + 1, exc.pos)
            continue
        except ValueError:
            failures += 1
            if failures >= _MAX_DECODE_FAILURES:
                break
            pos = start + 1
            continue
        except RecursionError:
            break
        if isinstance(data, list) and len(data) > 1 and isinstance(data[1], str):
            parts.append(data[1])
        pos = end
    return parts


def _payload(html: str) -> str:
    """The page's RSC payload: the text of its push chunks, in order."""
    bounded = html[:detect.MAX_HTML_CHARS]
    if _PUSH_MARKER not in bounded:
        return ""
    scripts = _extract_script_bodies(bounded)
    if scripts:
        parts: list[str] = []
        for script in scripts:
            if _PUSH_MARKER in script:
                parts.extend(_extract_push_chunks(script))
        return "".join(parts)
    return "".join(_extract_push_chunks(bounded))


def _find_nested_role_list(obj: Any) -> list[dict[str, Any]] | None:
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            candidate = node.get("jobs")
            if isinstance(candidate, list) and all(isinstance(row, dict) for row in candidate):
                return candidate
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))
    return None


def _role_list(payload: str) -> list[dict[str, Any]] | None:
    decoder = json.JSONDecoder()
    pos = 0
    limit = len(payload)
    failures = 0
    while pos < limit:
        idx = payload.find(_JOBS_KEY, pos)
        if idx < 0:
            break
        start = idx + len(_JOBS_KEY)
        while start < limit and payload[start].isspace():
            start += 1
        if start >= limit or payload[start] != "[":
            pos = start
            continue
        peek = start + 1
        while peek < limit and payload[peek].isspace():
            peek += 1
        if peek >= limit or (payload[peek] != "]" and payload[peek] not in _JSON_ARRAY_ELEM_STARTS):
            pos = peek
            continue
        try:
            rows, end = decoder.raw_decode(payload, start)
        except json.JSONDecodeError as exc:
            failures += 1
            if "Unterminated" in exc.msg or exc.pos >= limit - 1 or failures >= _MAX_DECODE_FAILURES:
                break
            pos = max(start + 1, exc.pos)
            continue
        except ValueError:
            failures += 1
            if failures >= _MAX_DECODE_FAILURES:
                break
            pos = start + 1
            continue
        except RecursionError:
            break
        if isinstance(rows, list):
            if all(isinstance(row, dict) for row in rows):
                return rows
            nested = _find_nested_role_list(rows)
            if nested is not None:
                return nested
        pos = end
    return None


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _role_paths(html: str, slug: str) -> dict[str, str]:
    prefix = f"/{slug}/"
    if not slug or prefix not in html:
        return {}
    path_re = re.compile(rf"^/{re.escape(slug)}/(\d+)-[^\s\"'<>]*$")
    paths: dict[str, str] = {}
    for match in _ANCHOR_TAG_RE.finditer(html):
        raw_attrs = match.group(1)
        if prefix not in raw_attrs:
            continue
        href = detect.parse_html_attrs(raw_attrs).get("href", "").strip()
        if not href or "\\" in href or href.startswith("//"):
            continue
        matched = path_re.match(href)
        if matched:
            paths[matched.group(1)] = href
    return paths


def parse_listing(html: Any, entry: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(html, str):
        raise jobs.BoardShapeError("no jobs list in the page payload")
    bounded = html[:detect.MAX_HTML_CHARS]
    rows = _role_list(_payload(bounded))
    if rows is None:
        raise jobs.BoardShapeError("no jobs list in the page payload")
    slug = str(entry["slug"]).strip("/")
    paths = _role_paths(bounded, slug)
    app_names = detect.extract_meta_contents(bounded, "name", "application-name")
    company = app_names[0] if app_names else ""
    encoded_slug = quote(slug, safe="")
    base = f"https://{KULA_HOST}/"
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
        fallback_path = f"/{encoded_slug}/{quote(ident, safe='')}-{_slugify(title)}"
        candidate = urljoin(base, paths.get(ident) or fallback_path)
        parsed = urlparse(candidate)
        if (
            parsed.scheme.lower() != "https"
            or detect.page_host(candidate) != KULA_HOST
            or parsed.port is not None
        ):
            candidate = f"https://{KULA_HOST}{fallback_path}"
        item = jobs._ats_item(
            provider=PROVIDER_KULA,
            slug=slug,
            ident=ident,
            title=title,
            url=candidate,
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
    the run says the board could not be read and goes on; a failed request
    raises its ``HTTPError``, so a timeout is noted as a slow board.
    """
    url = listing_url(entry)
    if not detect.is_safe_public_url(url):
        raise http.HTTPError("Refused non-public URL")
    with http.tee_failures() as failures:
        html = http.get_text(
            url, accept="text/html,*/*", timeout=jobs.API_TIMEOUT_SECONDS,
            retries=2, deadline_monotonic=deadline, public_only=True,
            redirect_guard=detect.same_site_redirect_guard(url),
            max_bytes=detect.MAX_HTML_BYTES,
        )
    if html is None and failures:
        # A timeout or HTTP error is reported as one, not as a bad page.
        raise failures[-1]
    items, total = parse_listing(html or "", entry)
    return items, {"board_total": total}


def _posted_date(html: Any) -> str | None:
    if not isinstance(html, str):
        return None
    for block in jobs.extract_jsonld_blocks(html):
        for obj in jobs._walk_jsonld(block):
            if isinstance(obj, dict) and jobs._is_job_posting(obj):
                return jobs._date_part(obj.get("datePosted"))
    return None


def fetch_details(items: list[dict[str, Any]], *, deadline: float) -> tuple[int, int]:
    """Fill each role's text from the JobPosting on its page, and its posted
    date when the listing gave none."""

    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        url = str(item.get("url") or "")
        if not detect.is_safe_public_url(url) or detect.page_host(url) != KULA_HOST:
            return lambda _http_deadline: None

        def fetch(http_deadline: float) -> Any:
            html = jobs.role_page_text(
                url, timeout=jobs.API_TIMEOUT_SECONDS, deadline_monotonic=http_deadline,
                redirect_guard=detect.same_site_redirect_guard(url),
            )
            posted = None if item.get("date") else _posted_date(html)
            if posted:
                item.update(date=posted, date_confidence="high", posted_date_true=True)
            return html
        return fetch

    return jobs._fill_role_text(items, call, jobs.jsonld_role_text, deadline=deadline)
