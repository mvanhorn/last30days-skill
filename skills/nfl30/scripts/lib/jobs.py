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
from html import unescape
from typing import Any, Callable
from urllib.parse import urlparse

from . import dates, grounding, http


ATS_PROVIDER_GREENHOUSE = "greenhouse"
ATS_PROVIDER_ASHBY = "ashby"
ATS_PROVIDER_LEVER = "lever"
ATS_PROVIDER_WORKABLE = "workable"
ATS_PROVIDER_SMARTRECRUITERS = "smartrecruiters"

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
]

# Tokens that show up in ATS URLs but are never real board slugs.
_SLUG_STOPWORDS = {"embed", "job_board", "v1", "v0", "api", "posting-api", "boards", "jobs", "job-boards", "www"}


def search_jobs(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    depth: str = "default",
    web_backend: str = "auto",
    explicit: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch public job postings for a company via the tiered strategy."""
    company = company.strip()
    if not company:
        return [], {}

    attempted: list[str] = []

    # --- Discovery: fetch the careers page and read the ATS off it (Tier 1). ---
    careers_html, careers_url = _resolve_careers_page(
        company, date_range, config, backend=web_backend,
    )
    provider, slug = (None, None)
    if careers_html:
        provider, slug = detect_ats(careers_html)
        if provider:
            attempted.append(f"careers:{provider}:{slug}")

    # Fallback discovery: cheap deterministic slug probe (only after careers-page
    # discovery fails - never the entry point).
    if not provider:
        provider, slug, probe_attempts = _probe_ats(company)
        attempted.extend(probe_attempts)

    # --- Tier 1: call the resolved ATS API. Trust it only if it has jobs. ---
    if provider and slug:
        try:
            items = _fetch_ats(provider, slug)
        except http.HTTPError:
            items = []
        if items:
            return items, _artifact("jobs", company, attempted, items, explicit,
                                    tier="ats", provider=provider, slug=slug)

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
) -> tuple[str | None, str | None]:
    """Find and fetch the company's careers page HTML.

    Tries conventional URLs on a guessed domain first (free, deterministic);
    falls back to a single web search for the careers page when a backend is
    configured. Returns (html, url) or (None, None). Never raises.
    """
    slug = _company_slug(company)
    candidates: list[str] = []
    if slug:
        for host in (f"{slug}.com", f"{slug}.ai", f"{slug}.io"):
            candidates.extend([f"https://{host}/careers", f"https://{host}/jobs"])

    for url in candidates:
        with http.expected_misses(403, 404):
            html = http.get_text(url, accept="text/html", retries=1)
        if html and _looks_like_careers_html(html):
            return html, url

    if backend != "none":
        careers_url = _search_for_careers_url(company, date_range, config, backend=backend)
        if careers_url:
            html = http.get_text(careers_url, accept="text/html", retries=1)
            if html:
                return html, careers_url

    return None, None


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
        )):
            return url
    return None


def detect_ats(html: str) -> tuple[str | None, str | None]:
    """Read the ATS provider + slug off a careers page's embed/links."""
    if not html:
        return None, None
    for provider, pattern in _ATS_LINK_PATTERNS:
        for match in re.finditer(pattern, html):
            slug = match.group(1).strip().strip("/.")
            if slug and slug.lower() not in _SLUG_STOPWORDS:
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
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    data = http.post(url, json_data={}, timeout=15, retries=2)
    return parse_workable_response(data, slug)


def search_smartrecruiters_board(slug: str) -> list[dict[str, Any]]:
    """Return jobs from SmartRecruiters' public postings API."""
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    data = http.get(url, params={"limit": "100"}, timeout=15, retries=2)
    return parse_smartrecruiters_response(data, slug)


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
        description = _clean_html(str(job.get("descriptionHtml") or job.get("descriptionPlain") or ""))
        items.append(_ats_item(
            provider=ATS_PROVIDER_ASHBY,
            slug=slug,
            ident=str(job.get("id") or job.get("jobId") or ""),
            title=str(job.get("title") or "").strip(),
            url=str(job.get("jobUrl") or job.get("applyUrl") or "").strip(),
            description=description,
            date=_date_part(job.get("publishedDate") or job.get("publishedAt")),
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
        description = _clean_html(str(job.get("descriptionPlain") or job.get("description") or ""))
        items.append(_ats_item(
            provider=ATS_PROVIDER_LEVER,
            slug=slug,
            ident=str(job.get("id") or ""),
            title=str(job.get("text") or "").strip(),
            url=str(job.get("hostedUrl") or job.get("applyUrl") or "").strip(),
            description=description,
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
            date=_date_part(job.get("published_on") or job.get("created_at")),
            department=str(job.get("department") or "").strip(),
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
) -> dict[str, Any]:
    prefix = {
        ATS_PROVIDER_ASHBY: "AB",
        ATS_PROVIDER_LEVER: "LV",
        ATS_PROVIDER_WORKABLE: "WK",
        ATS_PROVIDER_SMARTRECRUITERS: "SR",
    }.get(provider, "AT")
    return {
        "id": f"{prefix}{ident or slug}",
        "title": title,
        "url": url,
        "description": description,
        "date": date,
        "date_confidence": "high" if date else "low",
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
    return {
        "id": f"GH{job.get('id') or job.get('internal_job_id') or board_token}",
        "title": str(job.get("title") or "").strip(),
        "url": str(job.get("absolute_url") or "").strip(),
        "description": description,
        "date": _date_part(job.get("updated_at")),
        "date_confidence": "high" if job.get("updated_at") else "low",
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


def _looks_like_careers_html(html: str) -> bool:
    lowered = html.lower()
    if any(token in lowered for token in ("ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "smartrecruiters.com")):
        return True
    return bool(re.search(r"\b(open roles|open positions|join (our|the) team|current openings|jobposting)\b", lowered))


def _looks_like_jobs_page(title: str, url: str, snippet: str) -> bool:
    haystack = " ".join([title, url, snippet]).lower()
    return bool(re.search(r"\b(careers?|jobs?|job openings?|hiring|greenhouse|lever|ashby|workable)\b", haystack))
