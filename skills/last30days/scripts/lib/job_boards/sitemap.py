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

import functools
import ipaddress
import re
import socket
import time
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

from .. import http, jobs, jobs_digest
from . import robots

PROVIDER_SITEMAP = "sitemap"
# Sitemap files asked for in all, across indexes, for one site.
SITEMAP_FILE_LIMIT = 6
# Role pages asked for their title when the URLs carry none.
TITLE_PAGE_LIMIT = jobs_digest.DIGEST_MAX_ROLES
# Role pages asked first; the rest only when one of these gave a title.
TITLE_FIRST_WAVE = 8
TIMEOUT_SECONDS = 8
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.S | re.I)
_URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S | re.I)
_LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>", re.S | re.I)
# A role's own page: a jobs segment followed by the role's id or slug.
_JOB_PATH = re.compile(
    r"/(?:jobs?|job-detail|positions?|vacanc(?:y|ies)|openings?|requisitions?)/([^?#]+?)/?$", re.I,
)
_JOBS_WORD = re.compile(r"job|career|position|vacanc|opening", re.I)
_OG_TITLE = re.compile(r'<meta[^>]+property=["\']?og:title["\']?[^>]+content=["\']([^"\']+)', re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_JSONLD_TITLE = re.compile(r'"@type"\s*:\s*"JobPosting".*?"title"\s*:\s*"([^"]+)"', re.S)
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
    """Whether ``url`` is an http(s) address on the site of ``host`` whose host
    is a name, not an address on a private, loopback or reserved network.

    A sitemap is remote content: a role URL it lists is fetched only when it
    stays on the company's own site, so a sitemap cannot point the lane at a
    service on the user's network.
    """
    parsed = urlparse(url)
    name = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not name or name == "localhost" or name.endswith(".localhost"):
        return False
    if not jobs._same_site(name, host):
        return False
    try:
        return ipaddress.ip_address(name).is_global
    except ValueError:
        return _resolves_public(name)


@functools.lru_cache(maxsize=256)
def _resolves_public(name: str) -> bool:
    """Whether every address ``name`` resolves to is a public one. A name on
    the company's site that points at a private network (an internal
    subdomain) is not fetched; a name that does not resolve is not either."""
    try:
        answers = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return False
    addresses = set()
    for answer in answers:
        try:
            addresses.add(ipaddress.ip_address(answer[4][0].split("%", 1)[0]))
        except ValueError:
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def _fetch(url: str, deadline: float) -> str | None:
    remaining = deadline - jobs._clock()()
    if remaining <= 0:
        return None
    with http.capture_failures():
        return _on_site(url, lambda landed: jobs.role_page_text(
            url, timeout=TIMEOUT_SECONDS,
            deadline_monotonic=time.monotonic() + min(remaining, TIMEOUT_SECONDS), landed=landed,
            redirect_guard=_guard(url),
        ))


def _guard(url: str) -> Callable[[str], bool]:
    """The redirect check for a fetch of ``url``: it may be redirected only to
    an address on the same site that is not on a private network."""
    host = jobs._host(url)
    return lambda target: _public_url(target, host)


def _on_site(url: str, fetch: Callable[[list], str | None]) -> str | None:
    """``fetch``'s text, or None when the request was redirected off the
    site ``url`` is on or to a private address: such a page never enters the
    report."""
    landed: list = []
    text = fetch(landed)
    host = jobs._host(url)
    if text is not None and landed and not _public_url(str(landed[-1]), host):
        return None
    return text


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
    for host in hosts:
        if jobs._clock()() >= deadline:
            break
        rules = robots.rules_for(
            host, deadline_monotonic=time.monotonic() + max(0.0, deadline - jobs._clock()()),
        )
        jobs_host = bool(re.match(r"(?:jobs|careers?)\.", host)) or _JOBS_WORD.search(host.split(".")[0] or "")
        queue = [
            url for url in rules.sitemaps
            # robots.txt is remote content too: its sitemaps pass the same
            # check as the role URLs they list.
            if _public_url(url, host) and (jobs_host or _JOBS_WORD.search(url))
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
                missed = missed or bool(jobs_host) or bool(_JOBS_WORD.search(urlparse(url).path))
                continue
            if "<sitemapindex" in text[:2000]:
                children = [unescape(loc) for loc in _LOC.findall(text)]
                # A jobs sitemap first, then English ones, then the rest.
                children.sort(key=lambda child: (
                    not _JOBS_WORD.search(urlparse(child).path), "/en" not in child,
                ))
                # One file per path across languages: /fr/jobs/sitemap.xml
                # lists the roles /en/jobs/sitemap.xml does.
                kept: dict[str, str] = {}
                for child in children:
                    if _public_url(child, host):
                        kept.setdefault(_LOCALE.sub("/", urlparse(child).path, count=1), child)
                queue = list(kept.values()) + queue
                continue
            for block in _URL_BLOCK.findall(text):
                loc = _LOC.search(block)
                if not loc:
                    continue
                page = unescape(loc.group(1)).strip()
                key = _role_key(page)
                if _public_url(page, host) and _JOB_PATH.search(urlparse(page).path) and key not in found:
                    lastmod = _LASTMOD.search(block)
                    found[key] = (page, lastmod.group(1).strip() if lastmod else "")
        if found:
            unread = bool(queue) and (
                bool(jobs_host) or any(_JOBS_WORD.search(urlparse(url).path) for url in queue)
            )
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


def title_from_page(html: str, site_name: str = "") -> str:
    """A role page's own title: its JobPosting, ``og:title`` or ``<title>``,
    with a trailing site name (`` - Walmart Careers``) cut off."""
    for pattern in (_JSONLD_TITLE, _OG_TITLE, _TITLE):
        match = pattern.search(html or "")
        if match:
            title = " ".join(unescape(match.group(1)).split())
            parts = re.split(r"\s+[-|–]\s+", title)
            if len(parts) > 1 and re.search(r"career|job|" + re.escape(site_name or "~"), parts[-1], re.I):
                title = " - ".join(parts[:-1])
            if title and not _BLOCK_TITLE.search(title):
                return title
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
            redirect_guard=_guard(url),
        ))

    # The first wave tells whether the role pages can be read at all: a site
    # that walls them answers every one of those with a block, and the rest
    # are not asked.
    wave = TITLE_FIRST_WAVE
    with http.capture_failures():
        pages = jobs._workday_run([call(url) for url in urls[:wave]], deadline, jobs._clock())
        if any(isinstance(page, str) and title_from_page(page, company) for page in pages):
            pages += jobs._workday_run([call(url) for url in urls[wave:]], deadline, jobs._clock())
    return [
        (url, title_from_page(page, company))
        for url, page in zip(urls, pages)
        if isinstance(page, str) and title_from_page(page, company)
    ]
