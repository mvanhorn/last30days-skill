"""Which hiring platform a careers page runs on, read off the page itself.

A large company's careers site is usually one of a few hosted platforms under
the company's own host name. The page names its platform in its markup: the
API host and site number of Oracle Recruiting Cloud, Phenom's ``phApp`` page
object, TalentBrew's asset host, Jibe's asset host, Eightfold's group id, a
link to a Kula board (``careers.kula.ai/<slug>``). The
detector reads that and returns the board to read, so no company is added by
hand. Nothing here makes a request.

Each board is returned as ``(provider, entry)``, where ``entry`` holds what
the platform's reader needs (``board`` is always the host to call) and has
the shape of a registry entry in ``lib.jobs``.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

PROVIDER_ORACLE = "oracle"
PROVIDER_JIBE = "jibe"
PROVIDER_PHENOM = "phenom"
PROVIDER_TALENTBREW = "talentbrew"
PROVIDER_EIGHTFOLD = "eightfold"
PROVIDER_KULA = "kula"
KULA_HOST = "careers.kula.ai"

# The order platforms are tried in: a page names one platform's own API
# before another's asset (a Phenom page can link a Workday apply URL, which
# ``lib.jobs`` reads on its own first).
PLATFORMS = (
    PROVIDER_ORACLE, PROVIDER_PHENOM, PROVIDER_EIGHTFOLD, PROVIDER_JIBE, PROVIDER_TALENTBREW,
    PROVIDER_KULA,
)

_ORACLE_HOST = r"[a-z0-9-]+\.fa(?:\.[a-z0-9-]+)?\.oraclecloud\.com"
_ORACLE_API_ATTRS = re.compile(
    r'data-apibaseurl="(https?://[^"]+)"[^>]*?data-sitenumber="(CX_\d+)"', re.I,
)
_ORACLE_SITE_LINK = re.compile(
    rf"https?://({_ORACLE_HOST})(?::443)?/hcmUI/CandidateExperience/[A-Za-z-]+/sites/(CX_\d+)", re.I,
)
_ORACLE_SITE_NUMBER = re.compile(r"siteNumber=(CX_\d+)")
_PHENOM_BASE = re.compile(r'"baseUrl"\s*:\s*"(https://[^"/]+/([a-z]{2,8})/([a-z]{2})/)"')
_EIGHTFOLD_GROUP = re.compile(r'_EF_GROUP_ID\s*=\s*"([A-Za-z0-9.-]+)"')
# A Kula board: the company's slug on Kula's own careers host. Kula's assets
# are on other hosts (``careers-cdn.kula.ai``), so a slug is never ``_next``.
KULA_BOARD_LINK = r"careers\.kula\.ai/([A-Za-z0-9][A-Za-z0-9_-]*)"
_EIGHTFOLD_CAREER_URL = re.compile(r"eightFoldCareerUrl[^>]*>\s*(https://[^<\s]+)", re.I)


def page_host(url: str) -> str:
    """The host of a page URL, lowercased and without a port."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def detect_platform(html: str, page_url: str) -> tuple[str, dict[str, Any]] | None:
    """The platform board a careers page names, or None.

    ``page_url`` is the address that answered with ``html`` (after
    redirects): a platform served under the company's own host is called on
    that host.
    """
    if not html:
        return None
    host = page_host(page_url)
    for provider in PLATFORMS:
        entry = _DETECTORS[provider](html, host)
        if entry:
            return provider, {"provider": provider, **entry}
    return None


def eightfold_career_url(html: str) -> str:
    """The Eightfold board a careers landing page points at (Microsoft's
    ``careers-eightFoldCareerUrl`` element), or ''."""
    match = _EIGHTFOLD_CAREER_URL.search(html or "")
    return unescape(match.group(1)) if match else ""


def _oracle(html: str, host: str) -> dict[str, Any] | None:
    match = _ORACLE_API_ATTRS.search(html)
    if match:
        api_host = page_host(unescape(match.group(1)))
        if api_host:
            return {"board": api_host, "site_number": match.group(2)}
    match = _ORACLE_SITE_LINK.search(html)
    if match:
        return {"board": match.group(1).lower(), "site_number": match.group(2)}
    number = _ORACLE_SITE_NUMBER.search(html)
    if not number:
        return None
    api = re.search(rf"https?://({_ORACLE_HOST})", html, re.I)
    if api:
        return {"board": api.group(1).lower(), "site_number": number.group(1)}
    if host and "/hcmUI/CandidateExperience" in html and "/hcmRestApi/" in html:
        # Served under the company's own host (Dell): the API answers there.
        return {"board": host, "site_number": number.group(1)}
    return None


def _phenom(html: str, host: str) -> dict[str, Any] | None:
    if "phApp" not in html:
        return None
    for match in _PHENOM_BASE.finditer(html):
        base, country, lang = match.groups()
        board = page_host(base)
        if board and ("phenom" in html.lower()) and (not host or _same_site(board, host)):
            return {"board": board, "country": country, "lang": lang, "base_url": base}
    return None


def _eightfold(html: str, host: str) -> dict[str, Any] | None:
    lowered = html.lower()
    if "eightfold" not in lowered or not host:
        return None
    group = _EIGHTFOLD_GROUP.search(html)
    if not group:
        return None
    # The API generation is found by the reader: ``pcsx`` first, then ``v2``.
    return {"board": host, "domain": group.group(1).lower()}


_CANONICAL = re.compile(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', re.I)


def _jibe(html: str, host: str) -> dict[str, Any] | None:
    if not host or "jibecdn.com" not in html:
        return None
    # A role's page is under the site's jobs path (``/careers-home/jobs/95802``).
    prefix = ""
    canonical = _CANONICAL.search(html)
    if canonical:
        path = urlparse(unescape(canonical.group(1))).path
        if "/jobs" in path:
            prefix = path.split("/jobs", 1)[0].rstrip("/")
    return {"board": host, "jobs_path": f"{prefix}/jobs"}


def _talentbrew(html: str, host: str) -> dict[str, Any] | None:
    if not host or "tbcdn.talentbrew.com" not in html:
        return None
    if "/search-jobs" not in html:
        # A TalentBrew asset on a page that is not the job search itself.
        return None
    return {"board": host}


def kula_entry(slug: str) -> dict[str, Any]:
    """The board entry of the Kula board under ``slug``."""
    return {"provider": PROVIDER_KULA, "board": KULA_HOST, "slug": slug}


def _kula(html: str, host: str) -> dict[str, Any] | None:
    match = re.search(KULA_BOARD_LINK, html)
    if not match:
        return None
    return {"board": KULA_HOST, "slug": match.group(1)}


def _same_site(host: str, other: str) -> bool:
    """Whether two hosts share their last two labels (``careers.adobe.com``
    and ``www.adobe.com``)."""
    def base(name: str) -> str:
        return ".".join(name.lower().removeprefix("www.").split(".")[-2:])
    return bool(host) and base(host) == base(other)


_DETECTORS = {
    PROVIDER_ORACLE: _oracle,
    PROVIDER_PHENOM: _phenom,
    PROVIDER_EIGHTFOLD: _eightfold,
    PROVIDER_JIBE: _jibe,
    PROVIDER_TALENTBREW: _talentbrew,
    PROVIDER_KULA: _kula,
}
