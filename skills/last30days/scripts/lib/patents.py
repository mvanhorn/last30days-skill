"""A company's US patent filings in a date window, from USPTO Patent Public Search.

Keyless, stdlib only. An artifact lane: it returns no ranked items, only what
a patent digest is rendered from. The engine does not judge which filing is
interesting; it finds the filings that are the company's, sets aside the ones
that publish nothing new, and reads the text of a few of the rest.

One call makes one anonymous session, two list searches over the window
(published applications by applicant name, grants by assignee name), one list
search over the twelve months before the window, and up to ``DETAIL_SLOTS``
document reads, all inside one wall-clock budget. The baseline search gets at
most a third of it, so the reads are left time.

- Whose filings: every name a row was filed under is classified against the
  company (``lib.company_names``). Rows are grouped by that name; a group is
  confirmed (the company's own name, or one known to belong to it) or
  name-matched (the company's name plus more words, such as a licensing
  subsidiary). Names that are neither are only counted.
- What is new: a first US publication is an A1 application, or a B1 grant
  (one with no earlier publication), that does not continue an older
  application. Continuations, grants of applications already published,
  designs and other kinds are counted, never shown.
- What is counted: ``counts`` holds the company's own filings, those in
  confirmed groups: ``applications``, ``grants``, ``first_publications``,
  ``continuing``, ``previously_published_grants``, ``designs`` and
  ``other_kinds``. Filings in name-matched groups may be a stranger's, so
  they are counted apart and only as ``name_matched_applications``,
  ``name_matched_grants`` and ``name_matched_first_publications``. Entries
  and the index still hold name-matched first publications, each labelled
  by its ``group_kind``.
- Which get text: half the slots go to filings in the CPC subclasses the
  company filed in least over the year before, half to the newest.

USPTO has said the site will require sign-in. A refused anonymous session is
its own outcome, told by status and by the missing token, never by words in
the body: the working response already carries the notice.

``fetch_patent_data`` never raises. A run calls it through ``start`` and
``collect``: the fetch runs on a daemon thread beside the run's other sources,
so it adds no time to a run that outlasts it.
"""

from __future__ import annotations

import html
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from . import env, http, log, schema
from .company_names import CONFIRMED, NAME_MATCHED, classify_applicant, name_key

SOURCE = "patents"

BASE_URL = "https://ppubs.uspto.gov"
PDF_URL = "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/"
# A browser-style agent, sent with the Referer and Origin the site's own web app uses.
USER_AGENT = "Mozilla/5.0 (research-cli)"

APPLICATIONS = "US-PGPUB"
GRANTS = "USPAT"
# database -> (search field, the row field holding the filed-under names)
_NAME_FIELDS = {APPLICATIONS: ("aanm", "applicantName"), GRANTS: ("asnm", "assigneeName")}

# Everything one run spends on the lane, measured from ``start``. The lane
# runs beside a pipeline that takes minutes, so this rarely lengthens a run.
LANE_BUDGET_SECONDS = 90.0
# The most of the lane's budget the year-back baseline search may take.
BASELINE_SHARE = 1 / 3
COLLECT_GRACE_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 8.0
REQUEST_ATTEMPTS = 2
# lib.http gives up on the 429 that brings its count to this number, so one
# wait named by the host is honoured and the second 429 is the answer.
MAX_429_ATTEMPTS = 2

PAGE_SIZE = 500
MAX_PAGES = 2
MAX_EXTRA_NAMES = 12
MAX_GROUPS = 8
MAX_OTHER_NAMES = 5
DETAIL_SLOTS = 12
MAX_REFILL_FETCHES = 6
MAX_INDEX = 30
MIN_BASELINE_ROWS = 20
# An application filed more than 13 months after the earliest application it
# is related to continues it. A provisional is at most 12 months older.
CONTINUING_AFTER_DAYS = 396
ABSTRACT_CHARS = 400
FIRST_CLAIM_CHARS = 700
MAX_CPC_CODES = 5

_FIRST = "first_publications"
_NAME_MATCHED_FIRST = "name_matched_first_publications"
_CONTINUING = "continuing"
_PUBLISHED_BEFORE = "previously_published_grants"
_DESIGNS = "designs"
_OTHER_KINDS = "other_kinds"

_CONTINUES_A_PARENT = re.compile(
    r"\b(?:continuation(?:-in-part)?|division(?:al)?)\b", re.IGNORECASE,
)
_CLAIM_ONE = re.compile(r"<b>\s*1\s*</b>")
_CLAIM_TWO = re.compile(r"<b>\s*2\s*</b>")


class _SignInRequired(Exception):
    """USPTO refused the anonymous session, or a later call made with it."""


class _OutOfTime(Exception):
    """The lane's budget ran out before a request could be sent."""


# lib.http raises this when a request's deadline passes; every document read
# carries the lane's own deadline. Bound here so a test transport standing in
# for ``http`` does not have to provide it.
_LANE_DEADLINE_PASSED = http.DeadlineExceeded


def _log(message: str) -> None:
    log.source_log("Patents", message, tty_only=False)


class _Budget:
    """The wall-clock allowance for everything one call does."""

    def __init__(self, seconds: float, *, within: _Budget | None = None) -> None:
        self.seconds = max(0.0, float(seconds))
        self.deadline = time.monotonic() + self.seconds
        if within is not None:
            self.deadline = min(self.deadline, within.deadline)

    def share(self, fraction: float) -> _Budget:
        """A budget of ``fraction`` of this one's seconds, from now, ending no
        later than this one."""
        return _Budget(self.seconds * fraction, within=self)

    def bounds(self) -> dict[str, Any]:
        """The limits of the next request: its own timeout clipped to what is
        left, and the call's deadline, which also ends any retry wait."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise _OutOfTime("Request deadline exceeded: the patent lane's time budget is spent")
        return {
            "timeout": min(REQUEST_TIMEOUT_SECONDS, remaining),
            "deadline_monotonic": self.deadline,
            "retries": REQUEST_ATTEMPTS,
            "max_429_retries": MAX_429_ATTEMPTS,
        }


def fetch_patent_data(
    company: dict,
    date_from: str,
    date_to: str,
    config: dict | None = None,
    *,
    budget_seconds: float = LANE_BUDGET_SECONDS,
) -> dict:
    """Return the company's filings published between two YYYY-MM-DD dates.

    One of: ``{"artifact": {...}}`` when a filing sits in a confirmed or
    name-matched group; ``{"state": "none", "other_names": [...]}`` when the
    searches answered and none does; ``{"state": "signin", "detail": str}``
    when USPTO refuses an anonymous session; ``{"state": "error", "detail":
    str}`` for any other failure before the lists were read. ``config`` is the
    lane calling convention; nothing here is configurable.
    """
    try:
        return _fetch(company, date_from, date_to, _Budget(budget_seconds))
    except _SignInRequired as exc:
        _log(str(exc))
        return {"state": "signin", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the lane must not fail a run
        known = isinstance(exc, (http.HTTPError, _OutOfTime))
        detail = str(exc) if known else f"{type(exc).__name__}: {exc}"
        _log(f"no patent data: {detail}")
        return {"state": "error", "detail": detail}


def is_excluded(config: dict[str, Any] | None) -> bool:
    """True when ``EXCLUDE_SOURCES`` names this source, or the caller's
    environment asks for a run with no patent digest."""
    return SOURCE in env.exclude_sources(config or {}) or env.patent_digest_disabled()


def start(
    company: dict | None, date_from: str, date_to: str, config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Begin fetching the company's filings beside the run; ``collect`` takes
    the handle. None, and no request, with no company name or with the source
    excluded.

    The thread is a daemon: a fetch that outlives its budget is left behind
    and cannot hold the process open."""
    if not (company or {}).get("company") or is_excluded(config):
        return None
    budget = LANE_BUDGET_SECONDS
    handle: dict[str, Any] = {
        "company": str(company["company"]),
        # A little past the fetch's own budget, so a fetch that ran out of time
        # reports that itself rather than racing the collector.
        "deadline": time.monotonic() + budget + COLLECT_GRACE_SECONDS,
        "outcome": None,
    }

    def work() -> None:
        handle["outcome"] = fetch_patent_data(
            dict(company), date_from, date_to, config, budget_seconds=budget,
        )

    handle["thread"] = threading.Thread(target=work, name="patents-lane", daemon=True)
    handle["thread"].start()
    return handle


def collect(handle: dict[str, Any] | None, report: schema.Report) -> None:
    """Record what the fetch begun by ``start`` found on the report.

    Waits only for what is left of the lane's budget. The ``patents`` artifact
    on success; a degraded source outcome on a failed fetch; nothing for a
    company with no filings, for USPTO requiring sign-in (nothing a user can
    fix), or for a fetch still running when the budget ends."""
    if handle is None:
        return
    handle["thread"].join(max(0.0, handle["deadline"] - time.monotonic()))
    outcome = handle["outcome"]
    company = handle["company"]
    if outcome is None:
        _log(f"{company}: still searching after {LANE_BUDGET_SECONDS:g}s; patents skipped")
        return
    if "artifact" in outcome:
        report.artifacts[SOURCE] = outcome["artifact"]
        _log(_summary(outcome["artifact"]))
        return
    state = outcome.get("state")
    if state == "error":
        detail = str(outcome.get("detail") or "patent search failed")
        report.source_status[SOURCE] = schema.SourceOutcome(
            source=SOURCE,
            state=http.classify_failure(message=detail),
            detail=detail,
            fix_hint="doctor",
        )
    elif state == "signin":
        _log(f"{company}: USPTO Patent Public Search now requires sign-in; patents skipped")
    elif state == "none":
        others = [str(name) for name in outcome.get("other_names") or []][:3]
        seen = f"; other names seen: {', '.join(others)}" if others else ""
        _log(f"{company}: no filings under the company's name in the window{seen}")


def _summary(artifact: dict[str, Any]) -> str:
    counts = artifact.get("counts") or {}
    applications = counts.get("applications", 0) + counts.get("name_matched_applications", 0)
    grants = counts.get("grants", 0) + counts.get("name_matched_grants", 0)
    groups = artifact.get("groups") or []
    names = ", ".join(f"{group['name']} ({group['kind']})" for group in groups[:3])
    more = f" and {len(groups) - 3} more" if len(groups) > 3 else ""
    return (
        f"{artifact.get('company')}: {applications} applications, {grants} grants, "
        f"{counts.get(_FIRST, 0) + counts.get(_NAME_MATCHED_FIRST, 0)} first publications; "
        f"filed under {names}{more}"
    )


def _fetch(company: dict, date_from: str, date_to: str, budget: _Budget) -> dict:
    day_from, day_to = date.fromisoformat(date_from), date.fromisoformat(date_to)
    phrases = _phrases(company)
    if not phrases:
        raise ValueError("no company name to search patents for")
    classify = _classifier(company)

    session = _open_session(budget)
    rows: dict[str, list[dict]] = {}
    floor = False
    for database in (APPLICATIONS, GRANTS):
        query = _query(phrases, database, day_from, day_to)
        rows[database], cut = _search(session, query, database, budget, pages=MAX_PAGES)
        floor = floor or cut

    groups, other_names = _group(rows, classify)
    if not groups:
        return {"state": "none", "other_names": other_names}

    # The company's own counts are of confirmed groups. Of a name-matched
    # group, which may be a stranger's, only the first publications are
    # counted, under their own key.
    counts: Counter[str] = Counter()
    candidates = []
    for group in groups:
        confirmed = group["kind"] == CONFIRMED
        for database, row in group["rows"]:
            bucket = _bucket(database, row)
            if confirmed:
                counts[bucket] += 1
            elif bucket == _FIRST:
                counts[_NAME_MATCHED_FIRST] += 1
            if bucket == _FIRST:
                candidates.append(_candidate(database, row, group))

    frequencies = None
    if candidates:
        frequencies = _baseline(session, phrases, day_from, classify, budget.share(BASELINE_SHARE))
    ordered = _order(candidates, frequencies)
    entries, demoted, rest = _read_details(session, ordered, budget)
    for candidate in demoted:
        if candidate["group_kind"] == CONFIRMED:
            counts[_FIRST] -= 1
            counts[_CONTINUING if candidate["kind"] == "application" else _PUBLISHED_BEFORE] += 1
        else:
            counts[_NAME_MATCHED_FIRST] -= 1
    missing = min(DETAIL_SLOTS - len(entries), len(rest))
    index = [_index_item(candidate) for candidate in rest[:MAX_INDEX]]

    def total(kind: str, database: str) -> int:
        return sum(
            1 for group in groups if group["kind"] == kind
            for row_database, _row in group["rows"] if row_database == database
        )

    return {"artifact": {
        "company": str(company["company"]),
        "company_via_person": bool(company.get("company_via_person")),
        "window": {"from": date_from, "to": date_to},
        "groups": [
            {
                "name": group["name"],
                "kind": group["kind"],
                "applications": sum(1 for database, _row in group["rows"] if database == APPLICATIONS),
                "grants": sum(1 for database, _row in group["rows"] if database == GRANTS),
            }
            for group in groups
        ],
        "other_names": other_names,
        "counts": {
            "applications": total(CONFIRMED, APPLICATIONS),
            "grants": total(CONFIRMED, GRANTS),
            "name_matched_applications": total(NAME_MATCHED, APPLICATIONS),
            "name_matched_grants": total(NAME_MATCHED, GRANTS),
            _FIRST: counts[_FIRST],
            _NAME_MATCHED_FIRST: counts[_NAME_MATCHED_FIRST],
            _CONTINUING: counts[_CONTINUING],
            _PUBLISHED_BEFORE: counts[_PUBLISHED_BEFORE],
            _DESIGNS: counts[_DESIGNS],
            _OTHER_KINDS: counts[_OTHER_KINDS],
            "floor": floor,
        },
        "entries": entries,
        "index": index,
        "partial": {"missing_text": missing} if missing else None,
        "status": "digest" if entries or index else "counts-only",
    }}


# --- requests ---------------------------------------------------------------


def _headers(token: str | None) -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/pubwebapp/",
        "Origin": BASE_URL,
        "X-Access-Token": token or "null",
    }


def _send(call: Callable[..., Any], *args: Any, budget: _Budget, **kwargs: Any) -> Any:
    """Make one request inside the budget. A 401 or 403 is the sign-in outcome."""
    try:
        return call(*args, **kwargs, **budget.bounds())
    except http.HTTPError as exc:
        if exc.status_code in (401, 403):
            raise _SignInRequired(
                f"USPTO Patent Public Search refused an anonymous session (HTTP {exc.status_code})"
            ) from exc
        raise


def _open_session(budget: _Budget) -> dict[str, Any]:
    """Open an anonymous session: the token every later call sends and the
    case id every search names."""
    body, headers = _send(
        http.post,
        f"{BASE_URL}/api/users/me/session",
        json_data=-1,
        headers=_headers(None),
        return_headers=True,
        budget=budget,
    )
    if not isinstance(body, dict):
        raise TypeError("schema: the USPTO session response is not a JSON object")
    token = (headers or {}).get("x-access-token")
    case = body.get("userCase")
    case_id = case.get("caseId") if isinstance(case, dict) else None
    if not token or case_id is None:
        raise _SignInRequired("USPTO Patent Public Search gave no anonymous session")
    return {"token": str(token), "case_id": case_id}


def _phrases(company: Any) -> list[str]:
    """The names to search under: the company's, then those of its other
    names that the company's own name does not already match as a phrase."""
    if not isinstance(company, dict):
        return []
    own = name_key(company.get("company"))
    if not own:
        return []
    phrases = [own]
    for name in company.get("company_names") or ():
        key = name_key(name)
        if not key or key in phrases or key.startswith(f"{own} "):
            continue
        phrases.append(key)
        if len(phrases) > MAX_EXTRA_NAMES:
            break
    return phrases


def _query(phrases: list[str], database: str, day_from: date, day_to: date) -> str:
    names = " OR ".join(f'"{phrase}"' for phrase in phrases)
    return (
        f"({names}).{_NAME_FIELDS[database][0]}. "
        f'AND @pd>="{day_from:%Y%m%d}"<="{day_to:%Y%m%d}"'
    )


def _search(
    session: dict[str, Any], query: str, database: str, budget: _Budget, *, pages: int,
) -> tuple[list[dict], bool]:
    """Rows of a list search, newest first, and whether more exist than the
    pages read. Rows are documents and ``numFound`` counts them."""
    rows: list[dict] = []
    for page in range(pages):
        body = _send(
            http.post,
            f"{BASE_URL}/api/searches/searchWithBeFamily",
            json_data={
                "start": page * PAGE_SIZE,
                "pageCount": PAGE_SIZE,
                "sort": "date_publ desc",
                "docFamilyFiltering": "noFiltering",
                "searchType": 1,
                "familyIdEnglishOnly": True,
                "familyIdFirstPreferred": "US-PGPUB",
                "familyIdSecondPreferred": "USPAT",
                "familyIdThirdPreferred": "FPRS",
                "showDocPerFamilyPref": "showEnglish",
                "queryId": 0,
                "tagDocSearch": False,
                "query": {
                    "caseId": session["case_id"],
                    "hl_snippets": "2",
                    "op": "OR",
                    "q": query,
                    "queryName": query,
                    "highlights": "0",
                    "qt": "brs",
                    "spellCheck": False,
                    "viewName": "tile",
                    "plurals": True,
                    "britishEquivalents": True,
                    "databaseFilters": [{"databaseName": database, "countryCodes": []}],
                    "searchType": 1,
                    "ignorePersist": True,
                    "userEnteredQuery": query,
                },
            },
            headers=_headers(session["token"]),
            budget=budget,
        )
        found = body.get("patents") if isinstance(body, dict) else ""
        if not isinstance(found, list | None):
            raise TypeError("schema: the USPTO search response has no list of patents")
        rows.extend(row for row in found or () if isinstance(row, dict))
        total = body.get("numFound")
        if not isinstance(total, int) or total <= (page + 1) * PAGE_SIZE:
            return rows, False
    return rows, True


def _baseline(
    session: dict[str, Any],
    phrases: list[str],
    day_from: date,
    classify: Callable[[str], str | None],
    budget: _Budget,
) -> Counter[str] | None:
    """How many of the company's recent applications named each CPC subclass,
    or None when that cannot be said, including when the search outlasts
    ``budget``, the baseline's own share of the lane's. The sample is one page: the newest
    ``PAGE_SIZE`` applications of the twelve months before the window, which
    for a large filer covers only the latest months of that year. It says what
    the company has been filing lately, not what it filed all year."""
    try:
        start = day_from.replace(year=day_from.year - 1)
    except ValueError:  # 29 February
        start = day_from.replace(year=day_from.year - 1, day=28)
    query = _query(phrases, APPLICATIONS, start, day_from - timedelta(days=1))
    try:
        rows, _cut = _search(session, query, APPLICATIONS, budget, pages=1)
    except Exception as exc:  # noqa: BLE001 - the digest is built without it
        _log(f"no baseline of earlier filings, newest first instead: {exc}")
        return None
    field = _NAME_FIELDS[APPLICATIONS][1]
    own = [row for row in rows if _best_name(_names(row.get(field)), classify)]
    if len(own) < MIN_BASELINE_ROWS:
        return None
    return Counter(subclass for row in own for subclass in _subclasses(_cpc_codes(row)))


def _read_details(
    session: dict[str, Any], ordered: list[dict], budget: _Budget,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Read candidates in order until the slots are full.

    Returns the entries built, the candidates their own continuity data showed
    to continue an older application, and the candidates left with list data
    only: those whose read failed, then those never read. A read that fails
    or times out costs only its own document, and the reading goes on. The
    reading ends, keeping what was built, when the lane's budget is spent,
    when the site rate-limits past its one honoured wait, or when it asks for
    sign-in.
    """
    entries: list[dict] = []
    demoted: list[dict] = []
    failed: list[dict] = []
    position = 0
    while (
        position < len(ordered)
        and len(entries) < DETAIL_SLOTS
        and position < DETAIL_SLOTS + MAX_REFILL_FETCHES
    ):
        candidate = ordered[position]
        try:
            document = _send(
                http.get,
                f"{BASE_URL}/api/patents/highlight/{candidate['guid']}"
                f"?queryId=1&source={candidate['database']}&includeSections=true&uniqueId=",
                headers=_headers(session["token"]),
                budget=budget,
            )
            if not isinstance(document, dict):
                raise TypeError("schema: the USPTO document response is not a JSON object")
        except _SignInRequired:
            if not entries:
                raise
            _log(f"stopped reading documents after {len(entries)}: sign-in required")
            break
        except (_OutOfTime, _LANE_DEADLINE_PASSED) as exc:
            _log(f"stopped reading documents after {len(entries)}: {exc}")
            break
        except (http.HTTPError, TypeError) as exc:
            if isinstance(exc, http.HTTPError) and exc.status_code == 429:
                _log(f"stopped reading documents after {len(entries)}: {exc}")
                break
            _log(f"no text for {candidate['number']}, reading on: {exc}")
            failed.append(candidate)
            position += 1
            continue
        position += 1
        if _CONTINUES_A_PARENT.search(" ".join(_strings(document.get("continuityData")))):
            demoted.append(candidate)
        else:
            entries.append(_entry(candidate, document))
    return entries, demoted, failed + ordered[position:]


# --- whose filing -----------------------------------------------------------


def _classifier(company: Any) -> Callable[[str], str | None]:
    """A function from a filed-under name to CONFIRMED, NAME_MATCHED or None."""
    own = company.get("company") if isinstance(company, dict) else None
    known = list(company.get("company_names") or ()) if isinstance(company, dict) else []
    seen: dict[str, str | None] = {}

    def classify(name: str) -> str | None:
        if name not in seen:
            kind = classify_applicant(name, own, known)
            seen[name] = kind if kind in (CONFIRMED, NAME_MATCHED) else None
        return seen[name]

    return classify


def _best_name(names: list[str], classify: Callable[[str], str | None]) -> str | None:
    """The name that decides a row's group: a confirmed one before a
    name-matched one, the first listed of each."""
    for kind in (CONFIRMED, NAME_MATCHED):
        for name in names:
            if classify(name) == kind:
                return name
    return None


def _group(
    rows: dict[str, list[dict]], classify: Callable[[str], str | None],
) -> tuple[list[dict], list[dict]]:
    """Group rows by the name they were filed under.

    Returns the groups kept (every confirmed one, then name-matched ones
    while fewer than ``MAX_GROUPS`` are kept, largest first within each kind)
    and the largest names that are neither. Names with
    the same key are one name, shown as its most frequent spelling.
    """
    groups: dict[str, dict] = {}
    others: dict[str, Counter[str]] = {}
    for database, found in rows.items():
        field = _NAME_FIELDS[database][1]
        for row in found:
            names = _names(row.get(field))
            best = _best_name(names, classify)
            if best is not None:
                group = groups.setdefault(
                    name_key(best), {"kind": classify(best), "spellings": Counter(), "rows": []},
                )
                group["spellings"][best] += 1
                group["rows"].append((database, row))
                continue
            counted = set()
            for name in names:
                key = name_key(name)
                if key and key not in counted:
                    counted.add(key)
                    others.setdefault(key, Counter())[name] += 1
    ranked = sorted(
        groups.items(), key=lambda item: (item[1]["kind"] != CONFIRMED, -len(item[1]["rows"])),
    )
    # Every confirmed name is kept, however many there are: the company's
    # counts are of all of them. The cap only limits name-matched names.
    confirmed = sum(1 for _key, group in ranked if group["kind"] == CONFIRMED)
    kept = ranked[:max(MAX_GROUPS, confirmed)]
    largest = sorted(others.values(), key=lambda spellings: -sum(spellings.values()))
    return (
        [
            {**group, "key": key, "name": group["spellings"].most_common(1)[0][0]}
            for key, group in kept
        ],
        [
            {"name": spellings.most_common(1)[0][0], "count": sum(spellings.values())}
            for spellings in largest[:MAX_OTHER_NAMES]
        ],
    )


# --- what a row is ----------------------------------------------------------


def _bucket(database: str, row: dict) -> str:
    """Whether a row is a first US publication, or which count it belongs to."""
    kind_code = _kind_code(row)
    if database == APPLICATIONS:
        if kind_code != "A1":
            return _OTHER_KINDS
        return _CONTINUING if _continues_older_application(row) else _FIRST
    if kind_code == "B1":
        return _PUBLISHED_BEFORE if _continues_older_application(row) else _FIRST
    if kind_code == "B2":
        return _PUBLISHED_BEFORE
    return _DESIGNS if kind_code.startswith("S") else _OTHER_KINDS


def _continues_older_application(row: dict) -> bool:
    filed = _dates(row.get("applicationFilingDate"))
    related = _dates(row.get("relatedApplFilingDate"))
    if not filed or not related:
        return False
    return (filed[0] - min(related)).days > CONTINUING_AFTER_DAYS


def _candidate(database: str, row: dict, group: dict) -> dict:
    """A first publication, with everything the list says about it."""
    guid = str(row.get("guid") or "")
    parts = guid.split("-")
    number = str(row.get("publicationReferenceDocumentNumber") or (parts[1] if len(parts) > 1 else guid))
    published = _dates(row.get("datePublished"))
    filed = _dates(row.get("applicationFilingDate"))
    earlier = _dates(row.get("priorityClaimsDate")) + _dates(row.get("relatedApplFilingDate"))
    return {
        "guid": guid,
        "database": database,
        "group_key": group["key"],
        "number": number,
        "title": _text(row.get("inventionTitle")),
        "url": f"{PDF_URL}{number}",
        "kind": "application" if database == APPLICATIONS else "grant",
        "kind_code": _kind_code(row),
        "published": published[0].isoformat() if published else "",
        "filed": filed[0].isoformat() if filed else None,
        "earliest_priority": min(earlier).isoformat() if earlier else None,
        "cpc": _cpc_codes(row),
        "filed_as": group["name"],
        "group_kind": group["kind"],
    }


def _order(candidates: list[dict], frequencies: Counter[str] | None) -> list[dict]:
    """The order candidates are read in.

    Without a baseline, newest first. With one, the first half of the slots
    (rounded up) goes to the candidates whose rarest CPC subclass the company
    filed in least in its recent filings, a subclass absent from them first;
    everything else follows newest first. The newer candidate wins a tie.
    """
    newest = sorted(candidates, key=lambda candidate: candidate["published"], reverse=True)
    if frequencies is None:
        return newest

    def rarity(candidate: dict) -> float:
        subclasses = _subclasses(candidate["cpc"])
        return min((frequencies[subclass] for subclass in subclasses), default=float("inf"))

    rare = sorted(newest, key=rarity)[:(DETAIL_SLOTS + 1) // 2]
    chosen = {id(candidate) for candidate in rare}
    return rare + [candidate for candidate in newest if id(candidate) not in chosen]


def _entry(candidate: dict, document: dict) -> dict:
    codes = [str(code).split()[0] for code in _strings(document.get("cpcInventive")) if str(code).strip()]
    return {
        **{key: candidate[key] for key in (
            "guid", "number", "title", "url", "kind", "kind_code", "published", "filed",
            "earliest_priority",
        )},
        "cpc": codes[:MAX_CPC_CODES],
        "filed_as": candidate["filed_as"],
        "group_kind": candidate["group_kind"],
        "applicant_location": _location(candidate, document),
        "abstract": _text(document.get("abstractHtml"))[:ABSTRACT_CHARS].rstrip(),
        "first_claim": _first_claim(document.get("claimsHtml")),
    }


def _index_item(candidate: dict) -> dict:
    return {
        "title": candidate["title"],
        "url": candidate["url"],
        "kind": candidate["kind"],
        "published": candidate["published"],
        "cpc": "; ".join(candidate["cpc"][:MAX_CPC_CODES]),
        "filed_as": candidate["filed_as"],
        "group_kind": candidate["group_kind"],
    }


def _location(candidate: dict, document: dict) -> str:
    """City, state and country of the party the document was filed under:
    the applicant of an application, the assignee of a grant."""
    parties = ("applicant", "assignee") if candidate["kind"] == "application" else ("assignee", "applicant")
    for party in parties:
        names = _strings(document.get(f"{party}Name"))
        at = next(
            (n for n, name in enumerate(names) if name_key(name) == candidate["group_key"]), 0,
        )
        parts = []
        for field in ("City", "State", "Country"):
            values = _strings(document.get(f"{party}{field}"))
            value = values[at].strip() if at < len(values) else ""
            if value and value.upper() != "N/A":
                parts.append(value)
        if parts:
            return ", ".join(parts)
    return ""


def _first_claim(claims_html: Any) -> str:
    """Claim 1: from its number up to the number of claim 2."""
    claims = " ".join(_strings(claims_html))
    start = _CLAIM_ONE.search(claims)
    if not start:
        return ""
    end = _CLAIM_TWO.search(claims, start.end())
    claim = claims[start.start():end.start() if end else len(claims)]
    return _text(claim)[:FIRST_CLAIM_CHARS].rstrip()


# --- field shapes -----------------------------------------------------------


def _strings(value: Any) -> list[str]:
    """A field that may be null, one value or a list, as a list of strings."""
    if value is None:
        return []
    values = value if isinstance(value, list | tuple) else [value]
    return [str(item) for item in values if item is not None]


def _names(value: Any) -> list[str]:
    """The distinct names in a name field, HTML entities decoded."""
    names: list[str] = []
    for raw in _strings(value):
        name = " ".join(html.unescape(raw).split())
        if name and name not in names:
            names.append(name)
    return names


def _dates(value: Any) -> list[date]:
    dates = []
    for raw in _strings(value):
        try:
            dates.append(date.fromisoformat(raw[:10]))
        except ValueError:
            continue
    return dates


def _kind_code(row: dict) -> str:
    codes = _strings(row.get("kindCode"))
    return codes[0].strip().upper() if codes else ""


def _cpc_codes(row: dict) -> list[str]:
    return [code.strip() for code in str(row.get("cpcInventiveFlattened") or "").split(";") if code.strip()]


def _subclasses(codes: list[str]) -> set[str]:
    return {code[:4] for code in codes}


def _text(markup: Any) -> str:
    """Markup as one line of text."""
    text = re.sub(r"<br\s*/?>", " ", " ".join(_strings(markup)), flags=re.IGNORECASE)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text)).split())
