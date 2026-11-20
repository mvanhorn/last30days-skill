"""Find and verify a person's LinkedIn profile for a person run.

Order of trust:

1. A ``--linkedin`` URL the user supplied is an anchor as given. Its profile
   is read for facts; if its current employer differs from ``--employer``
   the mismatch is recorded, since people change jobs.
2. With a Deepline key and an Exa key, an Exa people search on the name and
   employer (never any part of an email) proposes ``linkedin.com/in/`` URLs.
   Only results whose text names the employer are read, so a namesake costs
   nothing.
3. A Deepline name lookup, by name plus employer, or by the full email when
   there is no employer.

A looked-up URL becomes an anchor only when the profile read shows a current
or recent position at the employer (by name or company website), or at the
work email's domain, or when the lookup found it from the email itself. A
rejected candidate leaves nothing behind. Without a Deepline key the
ScrapeCreators profile reader can read a supplied URL when LinkedIn is opted
in or the URL was passed; it never looks a URL up and never reports a role.

Every outcome lands on the ``PersonContext``: ``linkedin_status``,
``match_basis`` in plain words, ``linkedin_url``, ``anchors`` and ``profile``.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Iterable
from urllib.parse import urlsplit

from . import company_names, deepline, grounding, linkedin, log
from . import person
from .person import PersonContext, normalize_linkedin_url

# Exa people results read for candidates.
EXA_RESULT_COUNT = 5
# The free pre-search gets at most this long, and runs only when enough of the
# shared deadline is left for the paid profile read after it.
EXA_SECONDS = 15.0
# History entries, newest first, that count as a recent position.
RECENT_POSITIONS = 3
# A past position counts only when it ended within this many years.
RECENT_POSITION_YEARS = 2
# Current-role employers added as anchors from an anchored profile.
MAX_EMPLOYER_ANCHORS = 3

SUPPLIED_BASIS = "LinkedIn URL supplied by user"
SELF_REPORTED_BASIS = "profile employer is self-reported"
EMAIL_LOOKUP_BASIS = "LinkedIn profile found by email lookup"


def _log(msg: str) -> None:
    log.source_log("Person", msg, tty_only=False)


def linkedin_opted_in(config: dict, requested_sources: Iterable[str] | None = None) -> bool:
    """True when the user opted into LinkedIn (INCLUDE_SOURCES or --search)."""
    include = {
        token.strip()
        for token in str(config.get("INCLUDE_SOURCES") or "").lower().split(",")
        if token.strip()
    }
    return "linkedin" in include or "linkedin" in set(requested_sources or ())


def scrapecreators_allowed(
    ctx: PersonContext, config: dict, requested_sources: Iterable[str] | None = None,
) -> bool:
    """The ScrapeCreators fallback: no Deepline key, a ScrapeCreators key, and
    LinkedIn opted in or a URL supplied (which is consent to read that one)."""
    if deepline.is_available(config) or not str(config.get("SCRAPECREATORS_API_KEY") or "").strip():
        return False
    return bool(ctx.linkedin_url) or linkedin_opted_in(config, requested_sources)


def anchor_supplied_url(ctx: PersonContext) -> None:
    """Anchor a user-supplied URL. No network; safe to call before any lookup."""
    if ctx.linkedin_url:
        ctx.add_anchor(ctx.linkedin_url)
        if SUPPLIED_BASIS not in ctx.match_basis:
            ctx.match_basis.append(SUPPLIED_BASIS)
        ctx.identity_weak = False


# -- matching ------------------------------------------------------------------


def _host(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        host = urlsplit(text if "://" in text else f"https://{text}").hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _names_match(company: str, employer: str) -> bool:
    """Equal name keys, or one key a whole-word run inside the other."""
    a, b = company_names.name_key(company), company_names.name_key(employer)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return bool(re.search(rf"(?<!\w){re.escape(short)}(?!\w)", long))


def _role_match(role: dict, ctx: PersonContext) -> str:
    """What about this role matches the person's anchors, or ""."""
    company = str(role.get("company") or "")
    if ctx.employer_name and _names_match(company, ctx.employer_name):
        return ctx.employer_name
    website = _host(str(role.get("company_website") or ""))
    for domain in (ctx.employer_domain, ctx.email_domain):
        if person.same_or_subdomain(website, domain):
            return domain
    return ""


def _ended_recently(role: dict, today: date | None = None) -> bool:
    """True when the role is ongoing or ended within RECENT_POSITION_YEARS. A
    role whose end cannot be dated does not count: an old job at the same
    employer must not confirm a namesake."""
    end = str(role.get("end_date") or "").strip().lower()
    if end in ("present", "current", "now"):
        return True
    years = [int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", end)]
    if not years:
        return False
    today = today or date.today()
    return max(years) >= today.year - RECENT_POSITION_YEARS


def _verify(profile: dict, ctx: PersonContext) -> str:
    """A plain-words basis when a current or recent position matches, else ""."""
    for role in profile.get("current_roles") or []:
        matched = _role_match(role, ctx)
        if matched:
            return f"profile current employer matches {matched}"
    for role in (profile.get("history") or [])[:RECENT_POSITIONS]:
        if not _ended_recently(role):
            continue
        matched = _role_match(role, ctx)
        if matched:
            return f"profile recent position matches {matched}"
    return ""


# -- context writes ------------------------------------------------------------


def _store_profile(ctx: PersonContext, profile: dict, source: str) -> None:
    ctx.profile = {
        "source": source,
        "headline": profile.get("headline") or "",
        "location": profile.get("location") or "",
        "current_roles": list(profile.get("current_roles") or []),
        "history": list(profile.get("history") or []),
        "websites": list(profile.get("websites") or []),
    }
    if profile.get("posts"):
        ctx.profile["posts"] = list(profile["posts"])


def _anchor_profile(ctx: PersonContext, url: str, profile: dict | None, source: str) -> None:
    ctx.linkedin_url = url
    ctx.add_anchor(url)
    ctx.identity_weak = False
    if profile is None:
        return
    _store_profile(ctx, profile, source)
    employers = [
        str(role.get("company") or "").strip()
        for role in profile.get("current_roles") or []
        if str(role.get("company") or "").strip()
    ]
    for company in employers[:MAX_EMPLOYER_ANCHORS]:
        ctx.add_anchor(company)
    if ctx.employer_name and employers and not any(
        _names_match(company, ctx.employer_name) for company in employers
    ):
        ctx.linkedin_mismatch = (
            f"LinkedIn lists current employer {', '.join(employers[:MAX_EMPLOYER_ANCHORS])}, "
            f"not {ctx.employer_name}"
        )


def _canonical(url: str | None) -> str:
    try:
        return normalize_linkedin_url(url or "")
    except ValueError:
        return ""


# -- lanes -----------------------------------------------------------------


def _read_supplied(ctx, config, client, deadline, requested_sources) -> None:
    if client is not None:
        result = client.get_profile(ctx.linkedin_url, deadline_monotonic=deadline)
        if result["status"] != deepline.OK:
            ctx.set_linkedin_status(person.LINKEDIN_FAILED)
            return
        _anchor_profile(ctx, ctx.linkedin_url, result["data"], "deepline")
        ctx.set_linkedin_status(person.LINKEDIN_CHECKED)
        return
    if scrapecreators_allowed(ctx, config, requested_sources):
        result = linkedin.read_profile(
            ctx.linkedin_url, str(config["SCRAPECREATORS_API_KEY"]).strip(),
            deadline_monotonic=deadline,
        )
        if result["status"] != "ok":
            ctx.set_linkedin_status(person.LINKEDIN_FAILED)
            return
        _store_profile(ctx, result["data"], "scrapecreators")
        ctx.set_linkedin_status(person.LINKEDIN_CHECKED)
        return
    ctx.set_linkedin_status(person.LINKEDIN_NO_KEY)


def _exa_candidates(ctx: PersonContext, config: dict, deadline: float | None = None) -> list[str]:
    """LinkedIn profile URLs from an Exa people search whose result text names
    the employer. The query is the name and employer only."""
    key = grounding.exa_api_key(config)
    if not key or not ctx.employer_name:
        return []
    exa_deadline = time.monotonic() + EXA_SECONDS
    if deadline is not None:
        if deadline - time.monotonic() < EXA_SECONDS + deepline.MIN_ATTEMPT_SECONDS:
            return []
        exa_deadline = min(exa_deadline, deadline)
    try:
        results, _artifact = grounding.exa_people_search(
            f"{ctx.full_name} {ctx.employer_name}", key, count=EXA_RESULT_COUNT,
            deadline_monotonic=exa_deadline, retries=1,
        )
    except Exception as exc:  # noqa: BLE001 - a free pre-search failing just skips it
        _log(f"Exa people search failed: {type(exc).__name__}")
        return []
    employer_key = company_names.name_key(ctx.employer_name)
    urls: list[str] = []
    for result in results:
        url = _canonical(str(result.get("url") or ""))
        if not url or url in urls:
            continue
        text = f"{result.get('title') or ''} {result.get('snippet') or ''}"
        key_text = company_names.name_key(text)
        named = bool(employer_key) and bool(
            re.search(rf"(?<!\w){re.escape(employer_key)}(?!\w)", key_text)
        )
        if named or (ctx.employer_domain and ctx.employer_domain in text.lower()):
            urls.append(url)
    return urls


def _lookup(ctx: PersonContext, client, deadline) -> tuple[str, bool, bool]:
    """(candidate URL or "", found by email, lookup failed)."""
    if ctx.employer_name or ctx.employer_domain:
        result = client.find_profiles(
            deadline_monotonic=deadline, full_name=ctx.full_name,
            company_name=ctx.employer_name or None, company_domain=ctx.employer_domain or None,
        )
        by_email = False
    elif ctx.email:
        result = client.find_profiles(
            deadline_monotonic=deadline, full_name=ctx.full_name, email=ctx.email,
        )
        by_email = True
    else:
        return "", False, False
    if result["status"] != deepline.OK:
        return "", by_email, True
    return _canonical((result["data"] or {}).get("linkedin_url")), by_email, False


def _look_up(ctx, config, client, deadline) -> None:
    rejected = failed = False
    read: dict[str, dict | None] = {}

    def read_profile(url: str) -> dict | None:
        nonlocal failed
        if url not in read:
            result = client.get_profile(url, deadline_monotonic=deadline)
            read[url] = result["data"] if result["status"] == deepline.OK else None
            failed = failed or read[url] is None
        return read[url]

    for url in _exa_candidates(ctx, config, deadline)[:1]:
        profile = read_profile(url)
        if profile is None:
            continue
        basis = _verify(profile, ctx)
        if basis:
            _anchor_profile(ctx, url, profile, "deepline")
            ctx.match_basis += [basis, SELF_REPORTED_BASIS]
            ctx.set_linkedin_status(person.LINKEDIN_CHECKED)
            return
        rejected = True

    url, by_email, lookup_failed = _lookup(ctx, client, deadline)
    failed = failed or lookup_failed
    if url:
        profile = read_profile(url)
        basis = _verify(profile, ctx) if profile is not None else ""
        if by_email:
            _anchor_profile(ctx, url, profile, "deepline")
            ctx.match_basis.append(EMAIL_LOOKUP_BASIS)
            if basis:
                ctx.match_basis += [basis, SELF_REPORTED_BASIS]
            ctx.set_linkedin_status(person.LINKEDIN_CHECKED if profile is not None else person.LINKEDIN_FAILED)
            return
        if basis:
            _anchor_profile(ctx, url, profile, "deepline")
            ctx.match_basis += [basis, SELF_REPORTED_BASIS]
            ctx.set_linkedin_status(person.LINKEDIN_CHECKED)
            return
        rejected = rejected or profile is not None

    if rejected:
        ctx.set_linkedin_status(person.LINKEDIN_REJECTED)
    elif failed:
        ctx.set_linkedin_status(person.LINKEDIN_FAILED)
    else:
        ctx.set_linkedin_status(person.LINKEDIN_NOT_FOUND)


def resolve_linkedin(
    ctx: PersonContext,
    config: dict,
    *,
    deadline_monotonic: float | None,
    run_id: str,
    requested_sources: Iterable[str] | None = None,
) -> PersonContext:
    """Resolve, read, and verify the person's LinkedIn profile into ``ctx``.

    Makes at most the Deepline client's per-run paid calls (one lookup, two
    profile reads). Never raises for a provider failure: it becomes the
    "failed" status.
    """
    anchor_supplied_url(ctx)
    client = deepline.from_config(config, run_id)
    try:
        if ctx.linkedin_url:
            _read_supplied(ctx, config, client, deadline_monotonic, requested_sources)
        elif client is None:
            ctx.set_linkedin_status(person.LINKEDIN_NO_KEY)
        else:
            _look_up(ctx, config, client, deadline_monotonic)
    except Exception as exc:  # noqa: BLE001 - resolution never fails the run
        _log(f"LinkedIn resolution failed: {type(exc).__name__}")
        ctx.set_linkedin_status(person.LINKEDIN_FAILED)
    _log(f"LinkedIn: {ctx.linkedin_status}")
    return ctx
