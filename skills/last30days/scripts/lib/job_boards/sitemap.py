"""The roster tier: a careers site's job sitemap, when its pages are walled.

Some careers sites answer every page with a challenge or a block but publish
a job sitemap for crawlers, listed in their robots.txt (SAP: ``jobs.sap.com``
-> ``sitemap.xml`` index -> ``/en/jobs/sitemap.xml``, one URL per role). The
tier reads that list: one role per job URL, its title from the URL's slug.
When the URLs carry no title (``/jobs/R-674391``), the first role pages are
asked for theirs (``og:title``, a ``JobPosting`` or ``<title>``); a site that
walls those too gives a count of roles and no titles.

A sitemap's ``lastmod`` is when the entry last changed, not when the role was
posted, so no row claims a posted date and the board prints no count of roles
new in the window. No row has text: the digest built from these rows is
titles only, and says so.
"""

from __future__ import annotations

import re
import socket  # noqa: F401  kept: tests patch name resolution through this module
import time
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

from .. import http, jobs, jobs_digest
from . import detect, robots

PROVIDER_SITEMAP = "sitemap"
# Sitemap files asked for in all, across indexes, for one site.
SITEMAP_FILE_LIMIT = 6
# Role pages asked for their title when the URLs carry none.
TITLE_PAGE_LIMIT = jobs_digest.DIGEST_MAX_ROLES
# Role pages asked first; the rest only when one of these gave a title.
TITLE_FIRST_WAVE = 8
TIMEOUT_SECONDS = 8
MAX_SITEMAP_BYTES = 10 * 1024 * 1024
MAX_SITEMAP_CHARS = 10 * 1024 * 1024
MAX_PAGE_BYTES = detect.MAX_HTML_BYTES
MAX_PAGE_CHARS = detect.MAX_HTML_CHARS
_XML_UNSAFE_DECL = re.compile(r"<!(?:DOCTYPE|ENTITY)\b", re.I)
# A role's own page: a jobs segment followed by the role's id or slug.
_JOB_PATH = re.compile(
    r"/(?:jobs?|job-detail|positions?|vacanc(?:y|ies)|openings?|requisitions?)/([^?#]+?)/?$", re.I,
)
_JOBS_WORD = re.compile(r"job|career|position|vacanc|opening", re.I)
_LOCALE = re.compile(r"^/[a-z]{2}(?:[-_][A-Za-z]{2})?/")
_ID_PART = re.compile(r"^(?:[A-Za-z]{0,4}[-_]?\d[\w-]*|\d+)$")


# A page title a challenge, block or error page gives in place of the role's.
_BLOCK_TITLE = re.compile(
    r"just a moment|attention required|access denied|forbidden|captcha|are you (?:a )?(?:human|robot)"
    r"|security check|verify(?:ing)? you are|request (?:rejected|blocked)|unusual traffic"
    r"|page not found|service unavailable|^not found$|^error\b|^(?:40[134]|50[0-9])\b|^sign in|^log ?in$",
    re.I,
)


def _public_url(url: str, host: str) -> bool:
    """Whether ``url`` is a standard-port http(s) address on the site of ``host``
    whose host is a public domain name, not an address on a private, loopback,
    multicast, NAT64 or reserved network.

    A sitemap is remote content: a role URL it lists is fetched only when it
    stays on the company's own site, so a sitemap cannot point the lane at a
    service on the user's network.
    """
    return detect.public_site_url(url, host, resolves=_resolves_public)


# The bounded-TTL resolver, kept under this module's name so the check above
# looks it up here at call time.
_resolves_public = detect.resolves_public


def _fetch(url: str, deadline: float) -> str | None:
    remaining = deadline - jobs._clock()()
    if remaining <= 0:
        return None
    with http.capture_failures():
        return _on_site(url, lambda landed: jobs.role_page_text(
            url, timeout=TIMEOUT_SECONDS,
            deadline_monotonic=time.monotonic() + min(remaining, TIMEOUT_SECONDS), landed=landed,
            redirect_guard=_guard(url), max_bytes=MAX_SITEMAP_BYTES,
        ))


def _guard(url: str) -> Callable[[str], bool]:
    """The redirect check for a fetch of ``url``: it may be redirected only to
    an address on the same site that is not on a private network."""
    custom_resolver = None if _resolves_public is detect.resolves_public else _resolves_public
    return detect.same_site_redirect_guard(url, resolves=custom_resolver)


def _on_site(url: str, fetch: Callable[[list], str | None]) -> str | None:
    """``fetch``'s text, or None when the request was redirected off the
    site ``url`` is on, downgraded from https to http, or sent to a private
    address: such a page never enters the report."""
    host = detect.page_host(url)
    if not host or not detect.is_safe_public_url(url):
        return None
    require_https = urlparse(url).scheme.lower() == "https"
    landed: list = []
    text = fetch(landed)
    if text is not None and landed:
        final = str(landed[-1])
        if not detect.is_safe_public_url(final):
            return None
        saw_https = require_https or any(
            detect.is_safe_public_url(str(hop)) and urlparse(str(hop)).scheme.lower() == "https"
            for hop in landed[:-1]
        )
        if (saw_https and urlparse(final).scheme.lower() != "https") or not _public_url(final, host):
            return None
    return text


def _local_tag(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _parse_sitemap_xml(text: str) -> tuple[str | None, list[tuple[str, str]]]:
    """Parse a sitemap index or urlset with ElementTree after rejecting DTD/ENTITY.

    Returns ``("sitemapindex", children)``, ``("urlset", entries)``, or
    ``(None, [])`` when the document is rejected, malformed, or not a sitemap.
    """
    if not text or len(text) > MAX_SITEMAP_CHARS or _XML_UNSAFE_DECL.search(text):
        return None, []
    cleaned = text.lstrip("\ufeff \t\r\n")
    if not cleaned:
        return None, []
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return None, []
    kind = _local_tag(root.tag)
    if kind == "sitemapindex":
        children: list[tuple[str, str]] = []
        for elem in root:
            if _local_tag(elem.tag) != "sitemap":
                continue
            for sub in elem:
                if _local_tag(sub.tag) == "loc":
                    loc = (sub.text or "").strip()
                    if loc:
                        children.append((loc, ""))
                    break
        return "sitemapindex", children
    if kind == "urlset":
        entries: list[tuple[str, str]] = []
        for elem in root:
            if _local_tag(elem.tag) != "url":
                continue
            loc = lastmod = ""
            for sub in elem:
                sub_tag = _local_tag(sub.tag)
                if sub_tag == "loc" and not loc:
                    loc = (sub.text or "").strip()
                elif sub_tag == "lastmod" and not lastmod:
                    lastmod = (sub.text or "").strip()
            if loc:
                entries.append((loc, lastmod))
        return "urlset", entries
    return None, []


def _is_jobs_sitemap_url(url: str, jobs_host: bool) -> bool:
    return bool(jobs_host) or bool(_JOBS_WORD.search(url))


def job_urls(hosts: list[str], *, deadline: float) -> tuple[str, list[tuple[str, str]], bool]:
    """The host whose sitemaps list job URLs, those URLs with their
    ``lastmod`` ('' when none), from the first host in ``hosts`` that has any,
    and whether some of its sitemap files went unread (the file limit, or a
    file that did not answer), so the count is a floor.

    A role listed under several URLs (one sitemap per language, the same
    role id in each) counts once.

    A host's robots.txt names its sitemaps. On a jobs or careers host every
    sitemap is read; on another host (the company's main site) only those
    whose address names jobs or careers, so a marketing sitemap is not.
    """
    for raw_host in hosts:
        if jobs._clock()() >= deadline:
            break
        host = (raw_host or "").strip().rstrip(".").lower()
        robots_url = f"https://{host}/robots.txt"
        if not host or not detect.is_safe_public_url(robots_url) or detect.page_host(robots_url) != host:
            continue
        rules = robots.rules_for(
            host, deadline_monotonic=time.monotonic() + max(0.0, deadline - jobs._clock()()),
        )
        jobs_host = bool(re.match(r"(?:jobs|careers?)\.", host)) or bool(_JOBS_WORD.search(host.split(".")[0] or ""))
        queue = [
            url for url in rules.sitemaps
            # robots.txt is remote content too: its sitemaps pass the same
            # check as the role URLs they list.
            if _public_url(url, host) and _is_jobs_sitemap_url(url, jobs_host)
        ]
        found: dict[str, tuple[str, str]] = {}
        asked = 0
        missed = False
        while queue and asked < SITEMAP_FILE_LIMIT:
            url = queue.pop(0)
            asked += 1
            text = _fetch(url, deadline)
            if not text:
                # On a jobs host any sitemap file may hold roles
                # (``sitemap-2.xml``); elsewhere only one that names jobs.
                missed = missed or _is_jobs_sitemap_url(url, jobs_host)
                continue
            xml_kind, entries = _parse_sitemap_xml(text)
            if not xml_kind:
                missed = missed or _is_jobs_sitemap_url(url, jobs_host)
                continue
            if xml_kind == "sitemapindex":
                # Screen malformed URLs before urlparse() and sort jobs sitemaps first.
                children = sorted(
                    (loc for loc, _lastmod in entries if detect.is_safe_public_url(loc)),
                    key=lambda child: (
                        not _JOBS_WORD.search(urlparse(child).path), "/en" not in child,
                    ),
                )
                # One file per path across languages: /fr/jobs/sitemap.xml
                # lists the roles /en/jobs/sitemap.xml does.
                kept: dict[str, str] = {}
                for child in children:
                    if _public_url(child, host):
                        kept.setdefault(_LOCALE.sub("/", urlparse(child).path, count=1), child)
                queue = list(kept.values()) + queue
                continue
            for page, lastmod in entries:
                if not _public_url(page, host):
                    continue
                key = _role_key(page)
                if _JOB_PATH.search(urlparse(page).path) and key not in found:
                    found[key] = (page, lastmod)
        if found:
            unread = bool(queue) and any(_is_jobs_sitemap_url(url, jobs_host) for url in queue)
            return host, list(found.values()), missed or unread
    return "", [], False


def _role_key(url: str) -> str:
    """What makes two job URLs the same role: the role's id (the longest run
    of five or more digits in the path), or the URL when it carries none."""
    ids = re.findall(r"\d{5,}", urlparse(url).path)
    return max(ids, key=len) if ids else url


def title_from_url(url: str) -> str:
    """The title a role's URL carries in its slug, or ''."""
    match = _JOB_PATH.search(urlparse(url).path)
    if not match:
        return ""
    for part in reversed(match.group(1).split("/")):
        part = re.sub(r"^XMLNAME-", "", part)
        part = re.sub(r"_[A-Z]*\d[\w-]*$", "", part)
        words = [word for word in re.split(r"[-_+]+", part) if word]
        if _ID_PART.match(part) or len(words) < 2 or not any(re.search("[A-Za-z]", w) for w in words):
            continue
        return " ".join(word if word.isupper() else word.capitalize() for word in words)
    return ""


def _clean_page_title(raw: str, site_name: str, *, unescape_entities: bool = False) -> str:
    text = unescape(raw) if unescape_entities else raw
    title = " ".join(text.split())
    parts = re.split(r"\s+[-|–]\s+", title)
    if len(parts) > 1 and re.search(r"career|job|" + re.escape(site_name or "~"), parts[-1], re.I):
        title = " - ".join(parts[:-1])
    if title and not _BLOCK_TITLE.search(title):
        return title
    return ""


def title_from_page(html: str, site_name: str = "") -> str:
    """A role page's own title: its JobPosting, ``og:title`` or ``<title>``,
    with a trailing site name (`` - Walmart Careers``) cut off."""
    if not html:
        return ""
    bounded = html[:MAX_PAGE_CHARS]
    for block in detect.extract_jsonld_blocks(bounded):
        try:
            nodes = jobs._walk_jsonld(block)
        except RecursionError:
            continue
        for obj in nodes:
            if isinstance(obj, dict) and jobs._is_job_posting(obj):
                raw = obj.get("title") or obj.get("name")
                if isinstance(raw, str) and raw.strip():
                    cleaned = _clean_page_title(raw, site_name, unescape_entities=True)
                    if cleaned:
                        return cleaned
    for candidate in (
        *detect.extract_meta_contents(bounded, "property", "og:title"),
        *detect.extract_title_texts(bounded),
    ):
        cleaned = _clean_page_title(candidate, site_name)
        if cleaned:
            return cleaned
    return ""


def read_roster(
    hosts: list[str],
    *,
    company: str,
    deadline: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows from the first of ``hosts`` with a job sitemap, and what was read.

    Rows with no title (a site that walls its role pages too) are not
    returned; ``board_total`` still counts every job URL, and
    ``board_titles_missing`` says the titles could not be read.
    """
    host, urls, partial = job_urls(hosts, deadline=deadline)
    info: dict[str, Any] = {
        "board_total": len(urls), "board_sitemap_host": host, "board_total_floor": partial,
    }
    if not urls:
        return [], info
    titled = [(url, title_from_url(url)) for url, _lastmod in urls]
    if sum(1 for _url, title in titled if title) < len(titled) / 2:
        titled = _titles_from_pages([url for url, _lastmod in urls[:TITLE_PAGE_LIMIT]], company, deadline)
        info["board_titles_from_pages"] = True
        if not titled:
            info["board_titles_missing"] = True
            return [], info
    items = []
    for url, title in titled:
        if not title:
            continue
        item = jobs._ats_item(
            provider=PROVIDER_SITEMAP,
            slug=host,
            ident=url.rstrip("/").rsplit("/", 1)[-1][:60],
            title=title,
            url=url,
            description="",
            date=None,
            department="",
            location="",
            company_name=company,
            whole_posting=True,
        )
        items.append(item)
    info["board_rows_read"] = len(items)
    info["board_cut_short"] = len(items) < len(urls)
    return items, info


def _titles_from_pages(urls: list[str], company: str, deadline: float) -> list[tuple[str, str]]:
    def call(url: str) -> Callable[[float], Any]:
        return lambda http_deadline: _on_site(url, lambda landed: jobs.role_page_text(
            url, timeout=TIMEOUT_SECONDS, deadline_monotonic=http_deadline, landed=landed,
            redirect_guard=_guard(url), max_bytes=MAX_PAGE_BYTES,
        ))

    # The first wave tells whether the role pages can be read at all: a site
    # that walls them answers every one of those with a block, and the rest
    # are not asked.
    wave = TITLE_FIRST_WAVE
    titled: list[tuple[str, str]] = []
    with http.capture_failures():
        first_pages = jobs._workday_run([call(url) for url in urls[:wave]], deadline, jobs._clock())
        for url, page in zip(urls[:wave], first_pages):
            if isinstance(page, str):
                title = title_from_page(page, company)
                if title:
                    titled.append((url, title))
        if titled and len(urls) > wave:
            rest_pages = jobs._workday_run([call(url) for url in urls[wave:]], deadline, jobs._clock())
            for url, page in zip(urls[wave:], rest_pages):
                if isinstance(page, str):
                    title = title_from_page(page, company)
                    if title:
                        titled.append((url, title))
    return titled
