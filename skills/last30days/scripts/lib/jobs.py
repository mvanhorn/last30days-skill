"""Public jobs/careers retrieval for Hiring Signals.

Tiered strategy (each tier degrades gracefully; the artifact records which
tier produced results so synthesis knows the confidence level):

- Tier 1 - direct ATS API. The company's own board is authoritative and
  structured. Discovery is careers-page-first: fetch the careers page and read
  the provider + exact slug straight off the embed/link, then call that API.
  We only emit ATS results when a call actually returns a non-empty board, so a
  bad slug guess never produces fabricated coverage. Slug-probing is a fallback,
  never the entry point.
- Tier 2 - careers page found, no supported ATS API. Parse schema.org
  ``JobPosting`` JSON-LD (emitted by most careers pages for Google Jobs SEO,
  regardless of ATS).
- Tier 3 - generic web search. Last resort, noisy, clearly low-confidence.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from . import dates, grounding, http, jobs_digest


ATS_PROVIDER_GREENHOUSE = "greenhouse"
ATS_PROVIDER_ASHBY = "ashby"
ATS_PROVIDER_LEVER = "lever"
ATS_PROVIDER_WORKABLE = "workable"
ATS_PROVIDER_SMARTRECRUITERS = "smartrecruiters"
ATS_PROVIDER_WORKDAY = "workday"

# Workday's public listing endpoint serves 20 rows per call and stops at 2,000
# rows however large the board is.
WORKDAY_PAGE_SIZE = 20
WORKDAY_ROW_CEILING = 2000
# One page takes about a second, so a full 2,000-row board is about 100 s read
# one page at a time. Eight at once reads it in roughly 15 s.
WORKDAY_MAX_WORKERS = 8
# Wall time allowed for one board: the listing plus the detail calls. A
# comparison run gives each entity an equal share of it. Descriptions and true
# posted dates cost one call per role, so they are fetched only for the roles
# the digest selected.
WORKDAY_BUDGET_SECONDS = 45.0

# SmartRecruiters serves at most 100 rows per call. Its board is read to the
# same row ceiling and under the same time budget as a Workday board.
SMARTRECRUITERS_PAGE_SIZE = 100
SMARTRECRUITERS_ROW_CEILING = 2000

# Detection patterns: map an ATS embed/link found on a careers page to
# (provider, slug). The published slug is authoritative - this is why discovery
# is careers-page-first rather than blind probing.
_ATS_LINK_PATTERNS: list[tuple[str, str]] = [
    (ATS_PROVIDER_ASHBY, r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_GREENHOUSE, r"boards(?:-api)?\.greenhouse\.io/(?:v1/boards/|embed/job_board\?for=)?([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_GREENHOUSE, r"job-boards\.greenhouse\.io/([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_GREENHOUSE, r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_LEVER, r"(?:jobs\.lever\.co|api\.lever\.co/v0/postings)/([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_WORKABLE, r"apply\.workable\.com/(?:api/v[0-9]+/accounts/)?([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_WORKABLE, r"([A-Za-z0-9_-]+)\.workable\.com"),
    (ATS_PROVIDER_SMARTRECRUITERS, r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_.-]+)"),
    (ATS_PROVIDER_SMARTRECRUITERS, r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_.-]+)"),
    # Workday needs three parts to rebuild its endpoints: tenant, host number
    # and site. The site may follow a locale segment such as /en-US/.
    (
        ATS_PROVIDER_WORKDAY,
        r"([A-Za-z0-9_-]+)\.(wd[0-9]+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)",
    ),
]

# Tokens that show up in ATS URLs but are never real board slugs.
_SLUG_STOPWORDS = {"embed", "job_board", "v1", "v0", "api", "posting-api", "boards", "jobs", "job-boards", "www", "wday"}


def search_jobs(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    depth: str = "default",
    web_backend: str = "auto",
    explicit: bool = False,
    site: str = "",
    entity_count: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch public job postings for a company via the tiered strategy.

    ``site`` is the company's official site when it is known; its careers page
    is the only one tried, and a domain is guessed from the company's name
    only when no site is known. ``entity_count`` is the number of
    entities a comparison run is fetching at once.
    """
    company = company.strip()
    if not company:
        return [], {}

    attempted: list[str] = []

    # --- Discovery: fetch the careers page and read the ATS off it (Tier 1). ---
    careers_html, careers_url = _resolve_careers_page(
        company, date_range, config, backend=web_backend, site=site,
    )
    provider, slug = (None, None)
    found_via = "slug-guess"
    if careers_html:
        provider, slug = detect_ats(careers_html)
        if provider:
            attempted.append(f"careers:{provider}:{slug}")
            found_via = _found_via(careers_url or "", company, site)

    # Fallback discovery: cheap deterministic slug probe (only after careers-page
    # discovery fails - never the entry point).
    if not provider:
        provider, slug, probe_attempts = _probe_ats(company)
        attempted.extend(probe_attempts)

    # --- Tier 1: call the resolved ATS API. Trust it only if it has jobs. ---
    if provider and slug:
        board: dict[str, Any] = {}
        deadline = time.monotonic() + WORKDAY_BUDGET_SECONDS / max(1, entity_count)
        try:
            if provider == ATS_PROVIDER_WORKDAY:
                items, board = search_workday_board(slug, deadline=deadline)
            elif provider == ATS_PROVIDER_SMARTRECRUITERS:
                items, board = read_smartrecruiters_board(slug, deadline=deadline)
            elif provider == ATS_PROVIDER_WORKABLE:
                items, board = read_workable_board(slug)
            else:
                items = _fetch_ats(provider, slug)
        except http.HTTPError:
            items = []
        if items:
            artifact = _artifact("jobs", company, attempted, items, explicit,
                                 tier="ats", provider=provider, slug=slug)
            artifact.update(board)
            # A board belongs to the company when its known official site
            # links to it or the board names the company. A guessed slug or a
            # guessed domain can be another company's, so such a board still
            # counts roles but gives no digest unless it names the company.
            stated = next(
                (str(item["company_name"]) for item in items if item.get("company_name")), "",
            )
            owned = found_via == "site" or _same_company(stated, company)
            artifact.update({
                "found_via": found_via,
                "board_company": stated,
                "owned": owned,
            })
            if owned:
                selected = jobs_digest.select_roles(items)
                late = failed = 0
                if provider == ATS_PROVIDER_WORKDAY:
                    late, failed = fetch_workday_details(
                        [role["item"] for role in selected], deadline=deadline,
                    )
                artifact["digest"] = jobs_digest.build_digest(items, selected)
                if late or failed:
                    reasons = []
                    if late:
                        reasons.append(f"time budget spent before {late}")
                    if failed:
                        reasons.append(f"request failed or came back empty for {failed}")
                    artifact["digest"]["note"] = (
                        f"role text fetched for {len(selected) - late - failed} of "
                        f"{len(selected)} selected roles ({'; '.join(reasons)})"
                    )
            # Counted last: Workday's true posted dates arrive with the detail
            # calls above.
            artifact.update(_board_counts(items, board, date_range))
            return items, artifact

    # --- Tier 2: parse JSON-LD JobPosting off the careers page. ---
    if careers_html:
        attempted.append("careers:jsonld")
        jsonld_items = extract_jsonld_jobs(careers_html, careers_url or "")
        if jsonld_items:
            return jsonld_items, _artifact("jobs", company, attempted, jsonld_items,
                                           explicit, tier="careers-jsonld")

    # --- Tier 3: generic web search (noisy, low-confidence). ---
    fallback_items, artifact = search_jobs_web(
        company, date_range, config, backend=web_backend,
    )
    artifact = dict(artifact or {})
    artifact.setdefault("attempted", attempted)
    artifact.update({
        "label": artifact.get("label", "jobs"),
        "company": company,
        "tier": "web",
        "explicit": explicit,
        "resultCount": len(fallback_items),
    })
    return fallback_items, artifact


# --------------------------------------------------------------------------- #
# Careers-page discovery
# --------------------------------------------------------------------------- #

def _resolve_careers_page(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    backend: str = "auto",
    site: str = "",
) -> tuple[str | None, str | None]:
    """Find and fetch the company's careers page HTML.

    Tries conventional URLs on the company's known site, or on guessed
    domains when no site is known (free, deterministic); falls back to a single web search for the
    careers page when a backend is configured. Returns (html, url) or
    (None, None). Never raises.
    """
    # Only a host guessed from the company's name may turn out not to exist.
    guessed = not (site and _host(site))
    for host in _site_hosts(company, site):
        for path in ("/careers", "/jobs"):
            url = f"https://{host}{path}"
            html, no_host = _careers_text(url, guessed=guessed)
            if no_host:
                # Nothing answers at this host, so its other page is not tried.
                break
            if html and _looks_like_careers_html(html):
                return _board_page(html, url, guessed=guessed)

    if backend != "none":
        careers_url = _search_for_careers_url(company, date_range, config, backend=backend)
        if careers_url:
            html = http.get_text(careers_url, accept="text/html", retries=1)
            if html:
                return html, careers_url

    return None, None


def _careers_text(url: str, *, guessed: bool) -> tuple[str | None, bool]:
    """Fetch a careers page on the company's site or on a guessed domain.

    Returns the page text, and whether no host answered. A 403 or 404 is a
    page that is not there, never a run failure. On a ``guessed`` domain a
    name that does not resolve or a refused connection means the company has
    no such site: no board was found, so that is not recorded either. On the
    known official site the same failure is an outage and is recorded like a
    timeout or a server error.
    """
    with http.expected_misses(403, 404):
        if guessed:
            with http.expected_no_host() as no_host:
                html = http.get_text(url, accept="text/html", retries=1)
            return html, bool(no_host)
        with http.tee_failures() as failures:
            html = http.get_text(url, accept="text/html", retries=1)
    return html, any(failure.no_host for failure in failures)


# How many pages linked from a careers page are read when looking for the board.
_LINKED_PAGE_LIMIT = 2
_JOBS_LINK_WORDS = re.compile(r"job|position|opening|vacanc|search", re.I)


def _board_page(html: str, url: str, *, guessed: bool = False) -> tuple[str, str]:
    """The page that names the company's board: the careers page, or a jobs
    page of the same site that it links to.

    A large company's careers page is often a landing page whose job search
    lives one link away, on a host of its own (``jobs.nvidia.com``), and only
    that page names the hiring system. Up to ``_LINKED_PAGE_LIMIT`` such links
    are followed, and only within the careers page's own site, so a board found
    this way is named by the same site that named the careers page.
    """
    if detect_ats(html)[0]:
        return html, url
    for link in _jobs_links(html, url)[:_LINKED_PAGE_LIMIT]:
        linked, _no_host = _careers_text(link, guessed=guessed)
        if linked and detect_ats(linked)[0]:
            return linked, link
    return html, url


def _jobs_links(html: str, page_url: str) -> list[str]:
    """Links on a careers page to a jobs page of the same site, best first:
    a jobs or careers host of its own, then a path that reads like a job
    search. A link to the page itself or to another page of the same kind
    (a translation of it, "life at") is left out."""
    page = urlparse(page_url)
    ranked: list[tuple[int, str]] = []
    for href in re.findall(r"""href\s*=\s*["']([^"'#\s]+)""", html, flags=re.I):
        link = urljoin(page_url, unescape(href))
        parsed = urlparse(link)
        host = parsed.netloc.lower()
        if parsed.scheme not in ("http", "https") or not _same_site(host, page.netloc):
            continue
        if host != page.netloc.lower() and re.match(r"(jobs|careers?)\.", host):
            rank = 0
        elif host == page.netloc.lower() and _JOBS_LINK_WORDS.search(parsed.path):
            rank = 1
        else:
            continue
        if all(link != seen for _rank, seen in ranked):
            ranked.append((rank, link))
    return [link for _rank, link in sorted(ranked, key=lambda entry: entry[0])]


def _same_site(host: str, site_host: str) -> bool:
    """Whether ``host`` is the site's own host or a subdomain of it."""
    base = site_host.lower().removeprefix("www.")
    host = host.lower().removeprefix("www.")
    return bool(base) and (host == base or host.endswith("." + base))


def _search_for_careers_url(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    backend: str = "auto",
) -> str | None:
    """Use the configured web backend to locate the careers page URL."""
    try:
        raw_items, _ = grounding.web_search(
            f"{company} careers jobs", date_range, config, backend=backend,
        )
    except Exception:
        return None
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url:
            continue
        lowered = url.lower()
        if any(token in lowered for token in (
            "career", "/jobs", "ashbyhq", "greenhouse", "lever.co", "workable", "smartrecruiters",
            "myworkdayjobs",
        )):
            return url
    return None


def _host(url: str) -> str:
    try:
        return urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except ValueError:
        return ""


def _site_hosts(company: str, site: str = "") -> list[str]:
    """Hosts whose careers page is tried: the known official site alone, or,
    when no site is known, domains guessed from the company's name."""
    if site and _host(site):
        return [_host(site)]
    slug = _company_slug(company)
    return [f"{slug}.com", f"{slug}.ai", f"{slug}.io"] if slug else []


def _found_via(careers_url: str, company: str, site: str = "") -> str:
    """How the careers page naming the board was reached.

    ``site`` is the known official site, the only one that makes a board the
    company's own; a page on a subdomain of it (``jobs.nvidia.com`` for
    ``www.nvidia.com``) is the same site. ``guess`` is a domain guessed from
    the company's name, which another business can own, and ``search`` is a
    web search result.
    """
    host = _host(careers_url)
    if site and host and _same_site(host, _host(site)):
        return "site"
    guessed = any(_same_site(host, guess) for guess in _site_hosts(company))
    return "guess" if guessed else "search"


def _same_company(stated: str, company: str) -> bool:
    """Whether a board's stated company name is the company being researched."""
    return bool(set(_candidate_slugs(stated)) & set(_candidate_slugs(company)))


def _board_counts(
    items: list[dict[str, Any]], board: dict[str, Any], date_range: tuple[str, str],
) -> dict[str, Any]:
    """What the board itself reports: open roles, and roles new in the window.

    ``open_roles_floor`` marks a count the board stopped short of (its row
    ceiling, or a listing cut short). ``new_in_window`` is a number only when
    the whole board was read and every role carries a true first-posted date;
    otherwise it is None.
    """
    from_date, to_date = date_range
    floor = bool(board.get("board_capped") or board.get("board_cut_short"))
    dated = not floor and all(
        item.get("posted_date_true") and item.get("date") for item in items
    )
    return {
        "open_roles": len(items),
        "open_roles_floor": floor,
        "new_in_window": (
            sum(1 for item in items if from_date <= str(item["date"]) <= to_date)
            if dated else None
        ),
    }


def detect_ats(html: str) -> tuple[str | None, str | None]:
    """Read the ATS provider + slug off a careers page's embed/links."""
    if not html:
        return None, None
    for provider, pattern in _ATS_LINK_PATTERNS:
        for match in re.finditer(pattern, html):
            slug = match.group(match.lastindex or 1).strip().strip("/.")
            if not slug or slug.lower() in _SLUG_STOPWORDS:
                continue
            if provider == ATS_PROVIDER_WORKDAY:
                # One token carries all three parts: "tenant.wdN/site".
                slug = f"{match.group(1)}.{match.group(2)}/{slug}"
            return provider, slug
    return None, None


def _probe_ats(company: str) -> tuple[str | None, str | None, list[str]]:
    """Fallback: probe candidate slugs against ATS APIs (deterministic).

    Only reached when careers-page discovery fails. Returns the first provider
    whose API returns a non-empty board.
    """
    attempts: list[str] = []
    # Workable/SmartRecruiters are omitted here: their slugs rarely match the
    # company name, so blind probing is unreliable - they're reached only via
    # careers-page discovery, where the real slug is published.
    for slug in _candidate_slugs(company):
        for provider in (ATS_PROVIDER_GREENHOUSE, ATS_PROVIDER_ASHBY, ATS_PROVIDER_LEVER):
            attempts.append(f"probe:{provider}:{slug}")
            try:
                with http.expected_misses(400, 401, 403, 404):
                    items = _fetch_ats(provider, slug)
            except http.HTTPError as exc:
                if exc.status_code in {400, 401, 403, 404}:
                    continue
                raise
            if items:
                return provider, slug, attempts
    return None, None, attempts


# --------------------------------------------------------------------------- #
# Tier 1: ATS API fetchers + parsers
# --------------------------------------------------------------------------- #

def _fetch_ats(provider: str, slug: str) -> list[dict[str, Any]]:
    fetchers: dict[str, Callable[[str], list[dict[str, Any]]]] = {
        ATS_PROVIDER_GREENHOUSE: search_greenhouse_board,
        ATS_PROVIDER_ASHBY: search_ashby_board,
        ATS_PROVIDER_LEVER: search_lever_board,
        ATS_PROVIDER_WORKABLE: search_workable_board,
        ATS_PROVIDER_SMARTRECRUITERS: search_smartrecruiters_board,
    }
    fetcher = fetchers.get(provider)
    return fetcher(slug) if fetcher else []


def search_greenhouse_board(board_token: str) -> list[dict[str, Any]]:
    """Return jobs from Greenhouse's public Job Board API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    data = http.get(url, params={"content": "true"}, timeout=15, retries=2)
    jobs = data.get("jobs") if isinstance(data, dict) else []
    if not isinstance(jobs, list):
        return []
    return [_greenhouse_job_to_item(job, board_token) for job in jobs if isinstance(job, dict)]


def search_ashby_board(slug: str) -> list[dict[str, Any]]:
    """Return jobs from Ashby's public posting API (needs a browser UA)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    data = http.get(
        url,
        params={"includeCompensation": "true"},
        headers={"User-Agent": http.BROWSER_USER_AGENT},
        timeout=15,
        retries=2,
    )
    return parse_ashby_response(data, slug)


def search_lever_board(slug: str) -> list[dict[str, Any]]:
    """Return jobs from Lever's public postings API (returns a JSON list)."""
    url = f"https://api.lever.co/v0/postings/{slug}"
    data = http.get(url, params={"mode": "json"}, timeout=15, retries=2)
    return parse_lever_response(data, slug)


def search_workable_board(slug: str) -> list[dict[str, Any]]:
    """Return jobs from Workable's public widget API."""
    return read_workable_board(slug)[0]


def read_workable_board(slug: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the first page of a Workable board and what was read of it.

    Only that page is read. When the board reports more roles than the page
    holds, ``board_cut_short`` says so; the keys are ``search_workday_board``'s.
    """
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    data = http.post(url, json_data={}, timeout=15, retries=2)
    items = parse_workable_response(data, slug)
    total = max(_reported_total(data, "total"), len(items))
    return items, {
        "board_total": total, "board_capped": False, "board_cut_short": total > len(items),
    }


def search_smartrecruiters_board(slug: str) -> list[dict[str, Any]]:
    """Return jobs from SmartRecruiters' public postings API."""
    return read_smartrecruiters_board(slug)[0]


def _reported_total(payload: Any, key: str) -> int:
    try:
        return int(payload.get(key) or 0) if isinstance(payload, dict) else 0
    except (TypeError, ValueError):
        return 0


def read_smartrecruiters_board(
    slug: str,
    *,
    deadline: float | None = None,
    budget_seconds: float = WORKDAY_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a SmartRecruiters board's rows and what was read of the board.

    The board is read a page at a time up to the row ceiling. The second value
    has the same keys as ``search_workday_board``'s: ``board_capped`` when the
    board reports more rows than the ceiling, ``board_cut_short`` when the time
    budget ran out or a page failed. A failed first page returns no rows.
    """
    info: dict[str, Any] = {"board_total": 0, "board_capped": False, "board_cut_short": False}
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    deadline = _workday_deadline(deadline, budget_seconds, clock)

    def page(offset: int) -> Callable[[float], Any]:
        params = {"limit": str(SMARTRECRUITERS_PAGE_SIZE)}
        if offset:
            params["offset"] = str(offset)
        return lambda http_deadline: http.get(
            url, params=params, timeout=15, retries=2, deadline_monotonic=http_deadline,
        )

    first = _workday_run([page(0)], deadline, clock)[0]
    if not isinstance(first, dict):
        return [], info
    items = parse_smartrecruiters_response(first, slug)
    total = max(_reported_total(first, "totalFound"), len(items))
    info["board_total"] = total
    info["board_capped"] = total > SMARTRECRUITERS_ROW_CEILING
    rest = _workday_run(
        [
            page(offset)
            for offset in range(
                SMARTRECRUITERS_PAGE_SIZE,
                min(total, SMARTRECRUITERS_ROW_CEILING),
                SMARTRECRUITERS_PAGE_SIZE,
            )
        ],
        deadline,
        clock,
    )
    # A full page from a board that reports no total may not be the last one.
    info["board_cut_short"] = any(not isinstance(payload, dict) for payload in rest) or (
        "totalFound" not in first and len(items) >= SMARTRECRUITERS_PAGE_SIZE
    )
    for payload in rest:
        if isinstance(payload, dict):
            items.extend(parse_smartrecruiters_response(payload, slug))
    return items[:SMARTRECRUITERS_ROW_CEILING], info


# Workday: a paginated listing with no descriptions, plus one detail call per
# role. The board token is "tenant.wdN/site", as detect_ats builds it.

def _workday_parts(token: str) -> tuple[str, str] | None:
    """Split a board token into (host, API base URL), or None if malformed."""
    match = re.fullmatch(r"([A-Za-z0-9_-]+)\.(wd[0-9]+)/([A-Za-z0-9_-]+)", token or "")
    if not match:
        return None
    tenant, number, site = match.groups()
    host = f"https://{tenant}.{number}.myworkdayjobs.com"
    return host, f"{host}/wday/cxs/{tenant}/{site}"


def workday_list_url(token: str) -> str:
    parts = _workday_parts(token)
    return f"{parts[1]}/jobs" if parts else ""


def _workday_deadline(
    deadline: float | None, budget_seconds: float, clock: Callable[[], float],
) -> float:
    return clock() + budget_seconds if deadline is None else deadline


_OUT_OF_TIME = object()


def _workday_run(
    calls: list[Callable[[float], Any]],
    deadline: float,
    clock: Callable[[], float],
) -> list[Any]:
    """Run calls at most WORKDAY_MAX_WORKERS at once; results keep call order.

    A call that fails yields None; one that would start after the deadline
    yields ``_OUT_OF_TIME``. Each call gets the monotonic deadline to hand to
    ``lib.http``.
    """
    def run(call: Callable[[float], Any]) -> Any:
        remaining = deadline - clock()
        if remaining <= 0:
            return _OUT_OF_TIME
        try:
            return call(time.monotonic() + remaining)
        except http.HTTPError:
            return None

    if not calls:
        return []
    with ThreadPoolExecutor(max_workers=min(WORKDAY_MAX_WORKERS, len(calls))) as executor:
        futures = [http.submit_with_context(executor, run, call) for call in calls]
        return [future.result() for future in futures]


def search_workday_board(
    token: str,
    *,
    deadline: float | None = None,
    budget_seconds: float = WORKDAY_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a Workday board's listing rows and what was read of the board.

    The second value has ``board_total`` (the count the board reports, which
    Workday itself stops at the row ceiling), ``board_capped`` (the listing hit
    that ceiling, so the board may be larger) and ``board_cut_short`` (the time
    budget ran out or a page failed, so some reported rows were not read).
    A failed first page returns no rows rather than raising.
    """
    info: dict[str, Any] = {"board_total": 0, "board_capped": False, "board_cut_short": False}
    url = workday_list_url(token)
    if not url:
        return [], info
    deadline = _workday_deadline(deadline, budget_seconds, clock)

    def page(offset: int) -> Callable[[float], Any]:
        return lambda http_deadline: http.post(
            url,
            json_data={
                "appliedFacets": {},
                "limit": WORKDAY_PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            },
            headers={"Accept": "application/json"},
            timeout=15,
            retries=2,
            deadline_monotonic=http_deadline,
        )

    # The total is reported on the first page only, so read that one first.
    first = _workday_run([page(0)], deadline, clock)[0]
    if not isinstance(first, dict):
        return [], info
    try:
        total = int(first.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    info["board_total"] = total
    info["board_capped"] = total >= WORKDAY_ROW_CEILING
    wanted = min(total, WORKDAY_ROW_CEILING)
    rest = _workday_run(
        [page(offset) for offset in range(WORKDAY_PAGE_SIZE, wanted, WORKDAY_PAGE_SIZE)],
        deadline,
        clock,
    )
    info["board_cut_short"] = any(not isinstance(payload, dict) for payload in rest)

    items: list[dict[str, Any]] = []
    for payload in [first, *rest]:
        if isinstance(payload, dict):
            items.extend(parse_workday_response(payload, token))
    return items[:WORKDAY_ROW_CEILING], info


def parse_workday_response(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
    """Parse one Workday listing page. Rows have no description or posted date:
    ``postedOn`` is a relative label ("Posted 3 Days Ago"), so it is not read
    as a date."""
    postings = payload.get("jobPostings") if isinstance(payload, dict) else []
    parts = _workday_parts(token)
    if not isinstance(postings, list) or not parts:
        return []
    site_url = f"{parts[0]}/{token.split('/', 1)[1]}"
    items: list[dict[str, Any]] = []
    for job in postings:
        if not isinstance(job, dict):
            continue
        title = str(job.get("title") or "").strip()
        path = str(job.get("externalPath") or "").strip()
        if not title or not path.startswith("/"):
            continue
        bullets = job.get("bulletFields") if isinstance(job.get("bulletFields"), list) else []
        ident = str(bullets[0]).strip() if bullets else path.rsplit("/", 1)[-1]
        items.append(_ats_item(
            provider=ATS_PROVIDER_WORKDAY,
            slug=token,
            ident=ident,
            title=title,
            url=f"{site_url}{path}",
            description="",
            date=None,
            department="",
            location=str(job.get("locationsText") or "").strip(),
            # One row is one requisition with all its locations ("2 Locations").
            whole_posting=True,
        ))
    return items


def fetch_workday_details(
    items: list[dict[str, Any]],
    *,
    deadline: float | None = None,
    budget_seconds: float = WORKDAY_BUDGET_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, int]:
    """Fill ``description`` and the true posted date on the given Workday items.

    One call per item, so pass only the few roles that need it. An item whose
    call fails, or would start after the deadline, is left as it was. Returns
    two counts of items left unfetched: those the deadline stopped before
    their call started, and those whose call failed or returned no posting.
    """
    deadline = _workday_deadline(deadline, budget_seconds, clock)

    def detail(item: dict[str, Any]) -> Callable[[float], Any]:
        parts = _workday_parts(str(item.get("board_token") or ""))
        site_url = f"{parts[0]}/{item['board_token'].split('/', 1)[1]}" if parts else ""
        url = str(item.get("url") or "")
        if not parts or not url.startswith(f"{site_url}/"):
            return lambda http_deadline: None
        detail_url = f"{parts[1]}{url[len(site_url):]}"
        return lambda http_deadline: http.get(
            detail_url,
            headers={"Accept": "application/json"},
            timeout=15,
            retries=2,
            deadline_monotonic=http_deadline,
        )

    payloads = _workday_run([detail(item) for item in items], deadline, clock)
    late = failed = 0
    for item, payload in zip(items, payloads):
        posting = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
        if not isinstance(posting, dict):
            if payload is _OUT_OF_TIME:
                late += 1
            else:
                failed += 1
            continue
        item["description"] = _clean_html(str(posting.get("jobDescription") or ""))
        item["description_blocks"] = _block_text(str(posting.get("jobDescription") or ""))
        posted = _date_part(posting.get("startDate"))
        if posted:
            item["date"] = posted
            item["date_confidence"] = "high"
            item["posted_date_true"] = True
    return late, failed


def parse_greenhouse_response(payload: dict[str, Any], board_token: str = "") -> list[dict[str, Any]]:
    """Parse a Greenhouse jobs payload for tests and callers with cached data."""
    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    if not isinstance(jobs, list):
        return []
    return [_greenhouse_job_to_item(job, board_token) for job in jobs if isinstance(job, dict)]


def parse_ashby_response(payload: dict[str, Any], slug: str = "") -> list[dict[str, Any]]:
    jobs = payload.get("jobs") if isinstance(payload, dict) else []
    if not isinstance(jobs, list):
        return []
    items: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        department = str(job.get("departmentName") or job.get("teamName") or "").strip()
        location = str(job.get("locationName") or job.get("location") or "").strip()
        raw = str(job.get("descriptionHtml") or job.get("descriptionPlain") or "")
        published = _date_part(job.get("publishedDate") or job.get("publishedAt"))
        items.append(_ats_item(
            provider=ATS_PROVIDER_ASHBY,
            slug=slug,
            ident=str(job.get("id") or job.get("jobId") or ""),
            title=str(job.get("title") or "").strip(),
            url=str(job.get("jobUrl") or job.get("applyUrl") or "").strip(),
            description=_clean_html(raw),
            description_blocks=_block_text(raw),
            date=published,
            posted_date_true=bool(published),
            department=department,
            location=location,
        ))
    return items


def parse_lever_response(payload: Any, slug: str = "") -> list[dict[str, Any]]:
    postings = payload if isinstance(payload, list) else []
    items: list[dict[str, Any]] = []
    for job in postings:
        if not isinstance(job, dict):
            continue
        categories = job.get("categories") if isinstance(job.get("categories"), dict) else {}
        department = str(categories.get("department") or categories.get("team") or "").strip()
        location = str(categories.get("location") or "").strip()
        raw = str(job.get("descriptionPlain") or job.get("description") or "")
        items.append(_ats_item(
            provider=ATS_PROVIDER_LEVER,
            slug=slug,
            ident=str(job.get("id") or ""),
            title=str(job.get("text") or "").strip(),
            url=str(job.get("hostedUrl") or job.get("applyUrl") or "").strip(),
            description=_clean_html(raw),
            description_blocks=_block_text(raw),
            # When the posting was created, which can be well before it went
            # public, so it is not counted as a posting date.
            date=_epoch_ms_to_date(job.get("createdAt")),
            department=department,
            location=location,
        ))
    return items


def parse_workable_response(payload: dict[str, Any], slug: str = "") -> list[dict[str, Any]]:
    results = []
    if isinstance(payload, dict):
        results = payload.get("results") or payload.get("jobs") or []
    if not isinstance(results, list):
        return []
    items: list[dict[str, Any]] = []
    for job in results:
        if not isinstance(job, dict):
            continue
        loc = job.get("location") if isinstance(job.get("location"), dict) else {}
        location = _join_parts(str(loc.get("city") or ""), str(loc.get("country") or ""))
        # The v3 jobs endpoint gives `published` and a list of departments;
        # the older widget rows gave `published_on` and one department name.
        published = _date_part(job.get("published")) or _date_part(job.get("published_on"))
        raw_department = job.get("department")
        if isinstance(raw_department, (list, tuple)):
            department = _join_parts(*(str(name or "") for name in raw_department))
        else:
            department = str(raw_department or "").strip()
        shortcode = str(job.get("shortcode") or "").strip()
        url = str(job.get("url") or job.get("application_url") or "").strip()
        if not url and shortcode and slug:
            url = f"https://apply.workable.com/{slug}/j/{shortcode}/"
        items.append(_ats_item(
            provider=ATS_PROVIDER_WORKABLE,
            slug=slug,
            ident=shortcode or str(job.get("id") or ""),
            title=str(job.get("title") or "").strip(),
            url=url,
            description=_clean_html(str(job.get("description") or "")),
            description_blocks=_block_text(str(job.get("description") or "")),
            date=published or _date_part(job.get("created_at")),
            posted_date_true=bool(published),
            department=department,
            location=location,
        ))
    return items


def parse_smartrecruiters_response(payload: dict[str, Any], slug: str = "") -> list[dict[str, Any]]:
    content = payload.get("content") if isinstance(payload, dict) else []
    if not isinstance(content, list):
        return []
    items: list[dict[str, Any]] = []
    for job in content:
        if not isinstance(job, dict):
            continue
        department = ""
        if isinstance(job.get("department"), dict):
            department = str(job["department"].get("label") or "").strip()
        location = ""
        if isinstance(job.get("location"), dict):
            location = _join_parts(
                str(job["location"].get("city") or ""),
                str(job["location"].get("country") or ""),
            )
        ident = str(job.get("id") or job.get("uuid") or "").strip()
        url = ""
        if isinstance(job.get("ref"), str):
            url = job["ref"]
        if not url and slug and ident:
            url = f"https://jobs.smartrecruiters.com/{slug}/{ident}"
        items.append(_ats_item(
            provider=ATS_PROVIDER_SMARTRECRUITERS,
            slug=slug,
            ident=ident,
            title=str(job.get("name") or "").strip(),
            url=url,
            description="",
            date=_date_part(job.get("releasedDate") or job.get("createdOn")),
            posted_date_true=bool(_date_part(job.get("releasedDate"))),
            company_name=(
                str(job["company"].get("name") or "").strip()
                if isinstance(job.get("company"), dict) else ""
            ),
            department=department,
            location=location,
        ))
    return items


# --------------------------------------------------------------------------- #
# Tier 2: JSON-LD JobPosting crawler
# --------------------------------------------------------------------------- #

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_jsonld_jobs(html: str, base_url: str = "") -> list[dict[str, Any]]:
    """Extract schema.org JobPosting objects embedded as JSON-LD in a page."""
    if not html:
        return []
    domain = _domain(base_url)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in _JSONLD_RE.findall(html):
        for obj in _walk_jsonld(block):
            if not isinstance(obj, dict):
                continue
            if not _is_job_posting(obj):
                continue
            title = str(obj.get("title") or obj.get("name") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            posting_url = str(obj.get("url") or "").strip()
            items.append({
                "id": f"JL{len(items) + 1}",
                "title": title,
                "url": posting_url,
                "description": _clean_html(str(obj.get("description") or ""))[:2000],
                "date": _date_part(obj.get("datePosted")),
                "date_confidence": "high" if obj.get("datePosted") else "low",
                "provider": "careers-jsonld",
                "department": str(obj.get("occupationalCategory") or "").strip(),
                "location": _jsonld_location(obj),
                "source_url": base_url,
                "source_domain": domain,
                "relevance": 0.6,
                "why_relevant": "JobPosting structured data on careers page",
            })
    return items


def _walk_jsonld(block: str) -> list[Any]:
    try:
        data = json.loads(block.strip())
    except (json.JSONDecodeError, ValueError):
        return []
    out: list[Any] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            out.append(node)
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
    return out


def _is_job_posting(obj: dict[str, Any]) -> bool:
    type_field = obj.get("@type")
    if isinstance(type_field, str):
        return type_field.lower() == "jobposting"
    if isinstance(type_field, list):
        return any(isinstance(t, str) and t.lower() == "jobposting" for t in type_field)
    return False


def _jsonld_location(obj: dict[str, Any]) -> str:
    loc = obj.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        address = loc.get("address")
        if isinstance(address, dict):
            return _join_parts(
                str(address.get("addressLocality") or ""),
                str(address.get("addressRegion") or ""),
                str(address.get("addressCountry") or ""),
            )
    if obj.get("jobLocationType"):
        return str(obj.get("jobLocationType")).strip()
    return ""


# --------------------------------------------------------------------------- #
# Tier 3: generic web search fallback
# --------------------------------------------------------------------------- #

def search_jobs_web(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    backend: str = "auto",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fallback to configured web search for public careers/jobs pages."""
    if backend == "none":
        return [], {}
    query = f'{company} careers jobs hiring'
    raw_items, artifact = grounding.web_search(query, date_range, config, backend=backend)
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        snippet = str(raw.get("snippet") or "").strip()
        if not _looks_like_jobs_page(title, url, snippet):
            continue
        items.append({
            "id": raw.get("id") or f"JW{index + 1}",
            "title": title,
            "url": url,
            "description": snippet,
            "date": raw.get("date"),
            "date_confidence": raw.get("date_confidence") or "low",
            "provider": "web",
            "source_domain": raw.get("source_domain"),
            "relevance": raw.get("relevance", 0.45),
            "why_relevant": "Public careers/jobs web result",
        })
    artifact = dict(artifact or {})
    artifact.update({"label": "jobs-web", "company": company, "resultCount": len(items)})
    return items, artifact


# --------------------------------------------------------------------------- #
# Item builders + helpers
# --------------------------------------------------------------------------- #

def _ats_item(
    *,
    provider: str,
    slug: str,
    ident: str,
    title: str,
    url: str,
    description: str,
    date: str | None,
    department: str,
    location: str,
    description_blocks: str = "",
    posted_date_true: bool = False,
    company_name: str = "",
    whole_posting: bool = False,
) -> dict[str, Any]:
    """Build one board row.

    ``description_blocks`` is the description with its paragraph and list
    breaks kept, ``posted_date_true`` says ``date`` is the day the role was
    first posted, and ``company_name`` is the company the board says it
    belongs to, when the provider states one. ``whole_posting`` says the row
    is one posting with all its locations, so another row with the same title
    is a different posting.
    """
    prefix = {
        ATS_PROVIDER_ASHBY: "AB",
        ATS_PROVIDER_LEVER: "LV",
        ATS_PROVIDER_WORKABLE: "WK",
        ATS_PROVIDER_SMARTRECRUITERS: "SR",
        ATS_PROVIDER_WORKDAY: "WD",
    }.get(provider, "AT")
    return {
        "id": f"{prefix}{ident or slug}",
        "title": title,
        "url": url,
        "description": description,
        "description_blocks": description_blocks,
        "date": date,
        "date_confidence": "high" if date else "low",
        "posted_date_true": posted_date_true,
        "company_name": company_name,
        "whole_posting": whole_posting,
        "provider": provider,
        "board_token": slug,
        "department": department,
        "departments": [department] if department else [],
        "location": location,
        "offices": [location] if location else [],
        "relevance": 0.75,
        "why_relevant": f"Public {provider} job posting",
    }


def _greenhouse_job_to_item(job: dict[str, Any], board_token: str) -> dict[str, Any]:
    departments = [
        str(dept.get("name") or "").strip()
        for dept in (job.get("departments") or [])
        if isinstance(dept, dict) and str(dept.get("name") or "").strip()
    ]
    offices = [
        str(office.get("name") or office.get("location") or "").strip()
        for office in (job.get("offices") or [])
        if isinstance(office, dict) and str(office.get("name") or office.get("location") or "").strip()
    ]
    location = ""
    if isinstance(job.get("location"), dict):
        location = str(job["location"].get("name") or "").strip()
    description = _clean_html(str(job.get("content") or ""))
    # ``updated_at`` moves on every edit, so only ``first_published`` says when
    # a role was posted.
    published = _date_part(job.get("first_published"))
    return {
        "id": f"GH{job.get('id') or job.get('internal_job_id') or board_token}",
        "title": str(job.get("title") or "").strip(),
        "url": str(job.get("absolute_url") or "").strip(),
        "description": description,
        "description_blocks": _block_text(str(job.get("content") or "")),
        "date": published,
        "date_confidence": "high" if published else "low",
        "posted_date_true": bool(published),
        "company_name": str(job.get("company_name") or "").strip(),
        "provider": ATS_PROVIDER_GREENHOUSE,
        "board_token": board_token,
        "department": departments[0] if departments else "",
        "departments": departments,
        "location": location,
        "offices": offices,
        "relevance": 0.75,
        "why_relevant": "Public Greenhouse job posting",
    }


def _artifact(
    label: str,
    company: str,
    attempted: list[str],
    items: list[dict[str, Any]],
    explicit: bool,
    *,
    tier: str,
    provider: str = "",
    slug: str = "",
) -> dict[str, Any]:
    return {
        "label": label,
        "company": company,
        "attempted": attempted,
        "resultCount": len(items),
        "explicit": explicit,
        "tier": tier,
        "provider": provider,
        "board_token": slug,
    }


def _candidate_slugs(company: str) -> list[str]:
    base = _company_slug(company)
    if not base:
        return []
    candidates = [base]
    compact = re.sub(r"(inc|labs|ai|hq|app|tech)$", "", base)
    if compact and compact != base:
        candidates.append(compact)
    # hyphenated form (e.g. "listen labs" -> "listen-labs")
    hyphen = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")
    if hyphen and hyphen not in candidates:
        candidates.append(hyphen)
    deduped: list[str] = []
    for token in candidates:
        if token and token not in deduped:
            deduped.append(token)
    return deduped[:4]


def _join_parts(*parts: str) -> str:
    """Join non-empty, stripped location parts as 'City, Country'."""
    return ", ".join(part.strip() for part in parts if part and part.strip())


def _company_slug(company: str) -> str:
    text = company.lower()
    text = re.sub(r"\b(inc|inc\.|llc|ltd|corp|corporation|company|co\.)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text).strip()


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except ValueError:
        return ""


def _date_part(value: Any) -> str | None:
    text = str(value or "")
    if not text:
        return None
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else None


def _epoch_ms_to_date(value: Any) -> str | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return dates.timestamp_to_date(ms / 1000)


def _clean_html(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"</p\s*>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


_BLOCK_END = re.compile(r"<(?:br|/p|/li|/div|/h[1-6]|/tr|/ul|/ol)\b[^>]*>", re.I)


def _block_text(value: str) -> str:
    """Like ``_clean_html``, but each paragraph, heading and list item keeps its own line."""
    value = _BLOCK_END.sub("\n", unescape(value))
    value = re.sub(r"<[^>]+>", " ", value)
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _looks_like_careers_html(html: str) -> bool:
    lowered = html.lower()
    if any(token in lowered for token in ("ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "smartrecruiters.com", "myworkdayjobs.com")):
        return True
    return bool(re.search(r"\b(open roles|open positions|join (our|the) team|current openings|jobposting)\b", lowered))


def _looks_like_jobs_page(title: str, url: str, snippet: str) -> bool:
    haystack = " ".join([title, url, snippet]).lower()
    return bool(re.search(r"\b(careers?|jobs?|job openings?|hiring|greenhouse|lever|ashby|workable)\b", haystack))
