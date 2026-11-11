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
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextvars import ContextVar, copy_context
from html import unescape
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from . import dates, grounding, health, http, jobs_digest, log


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
# Wall time allowed for the whole lane on one topic: finding the careers page,
# reading the board off it, the listing pages and the detail calls. A
# comparison run gives each entity an equal share of it. When it is spent the
# lane returns what it has read. Descriptions and true posted dates cost one
# call per Workday role, so they are fetched only for the roles the digest
# selected.
JOBS_BUDGET_SECONDS = 45.0
# The same allowance under its first name, for a board read on its own.
WORKDAY_BUDGET_SECONDS = JOBS_BUDGET_SECONDS
# Longest one request may take, so that one host that never answers cannot
# spend the budget: a careers page on the company's known site, a page on a
# domain only guessed from its name, and one call to a board's API.
PAGE_TIMEOUT_SECONDS = 8
GUESS_TIMEOUT_SECONDS = 4
API_TIMEOUT_SECONDS = 10

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

# The hosts a board URL may be on: only these are read as a board's address.
_BOARD_DOMAINS = (
    "greenhouse.io", "ashbyhq.com", "lever.co", "workable.com",
    "smartrecruiters.com", "myworkdayjobs.com",
)
SUPPORTED_SYSTEMS = "Greenhouse, Ashby, Lever, Workable, SmartRecruiters and Workday"

# Tokens that show up in ATS URLs but are never real board slugs.
_SLUG_STOPWORDS = {"embed", "job_board", "v1", "v0", "api", "posting-api", "boards", "jobs", "job-boards", "www", "wday"}


class LaneBudget:
    """The wall-clock allowance for everything the lane does for one topic.

    One budget covers every company tried for the topic and every step:
    careers pages, board detection, listing pages and detail calls. Each step
    asks how long it may still take, and a step that finds no time left says
    so with ``stop``. ``slow`` names what did not answer in its own time, so a
    lane that ends with nothing can say where the time went.
    """

    def __init__(
        self,
        seconds: float = JOBS_BUDGET_SECONDS,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.clock = clock or time.monotonic
        self.seconds = float(seconds)
        self.started = self.clock()
        self.deadline = self.started + self.seconds
        # Finding a board may be given less than the whole budget, so that a
        # later company still has time; reading a found board has all of it.
        self.step_deadline = self.deadline
        self.slow: list[str] = []
        self.stopped_at = ""
        # Board APIs that reached their own request limit while the lane
        # still had time: that call's failure, not the lane's.
        self.unanswered: list[str] = []
        # Set once the lane reached its answer for the topic, so a second
        # pass of the same run does not ask the same hosts again.
        self.settled = False

    def remaining(self) -> float:
        return max(0.0, min(self.deadline, self.step_deadline) - self.clock())

    def spent(self) -> bool:
        return self.clock() >= self.deadline

    def limit_discovery(self, seconds: float | None) -> None:
        self.step_deadline = (
            self.deadline if seconds is None
            else min(self.deadline, self.clock() + max(0.0, seconds))
        )

    def note_slow(self, what: str) -> None:
        if what and what not in self.slow:
            self.slow.append(what)

    def stop(self, step: str) -> None:
        """Record the first step that found no time left."""
        if not self.stopped_at:
            self.stopped_at = step

    def reason(self) -> str:
        """Where the time went, for a lane that ran out of it."""
        text = (
            f"jobs lane ran out of time ({self.seconds:g}s budget)" if self.spent()
            else f"jobs search used up its share of the lane's {self.seconds:g}s"
        )
        if self.stopped_at:
            text += f" before {self.stopped_at}"
        if self.slow:
            text += "; slow: " + ", ".join(self.slow[:4])
            if len(self.slow) > 4:
                text += f" and {len(self.slow) - 4} more"
        return text


_BUDGET: ContextVar[LaneBudget | None] = ContextVar("last30days_jobs_budget", default=None)


def _clock() -> Callable[[], float]:
    budget = _BUDGET.get()
    return budget.clock if budget is not None else time.monotonic


def _out_of_time(step: str) -> bool:
    """Whether the lane's budget leaves no time for ``step``; records it if so."""
    budget = _BUDGET.get()
    if budget is None or budget.remaining() > 0:
        return False
    budget.stop(step)
    return True


def _request_seconds(timeout: float, attempts: int = 1) -> float:
    """The longest one call may take in all: each attempt's timeout and the
    pause ``lib.http`` takes between attempts."""
    return timeout * attempts + http.RETRY_DELAY * sum(range(1, attempts))


def _bounds(timeout: float, attempts: int = 1) -> dict[str, Any]:
    """The per-attempt timeout and the call's hard deadline.

    ``timeout`` only reaches each socket operation, so a host that stalls in
    DNS or sends a byte now and then could hold a request far longer. The
    hard deadline is the earlier of the call's own limit and what is left of
    the lane's budget, and ``lib.http`` stops waiting there whatever the
    socket is doing. Without a budget the call's own limit is the deadline.
    """
    seconds = _request_seconds(timeout, attempts)
    budget = _BUDGET.get()
    if budget is not None:
        seconds = min(seconds, budget.remaining())
    return {"timeout": timeout, "deadline_monotonic": time.monotonic() + seconds}


def _note_slow(what: str) -> None:
    budget = _BUDGET.get()
    if budget is not None:
        budget.note_slow(what)


def _within_budget(call: Callable[[], Any], step: str, default: Any) -> Any:
    """Run a call that takes no deadline of its own, waiting only as long as
    the budget allows. ``default`` is returned when the time runs out."""
    budget = _BUDGET.get()
    if budget is None:
        return call()
    if _out_of_time(step):
        return default
    future: Future = Future()
    context = copy_context()

    def worker() -> None:
        try:
            future.set_result(context.run(call))
        except BaseException as exc:  # handed to the waiting caller
            future.set_exception(exc)

    threading.Thread(target=worker, daemon=True).start()
    try:
        return future.result(timeout=budget.remaining())
    except FutureTimeout:
        budget.note_slow(f"{step} did not finish")
        budget.stop(f"the end of {step}")
        return default


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
    budget: LaneBudget | None = None,
    discovery_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch public job postings for a company via the tiered strategy.

    ``site`` is the company's official site when it is known; its careers page
    is the only one tried, and a domain is guessed from the company's name
    only when no site is known. ``entity_count`` is the number of
    entities a comparison run is fetching at once.

    ``budget`` is the lane's wall-clock allowance; a caller that tries more
    than one company for a topic passes the same one each time, and may cap
    the time spent finding this company's board with ``discovery_seconds``.
    Without one the call gets ``JOBS_BUDGET_SECONDS``, shared between the
    entities of a comparison. When the budget is spent the call returns what
    it has: the rows read so far, or nothing with the reason under
    ``_source_outcome``.
    """
    company = company.strip()
    if not company:
        return [], {}
    shared = budget is not None
    if budget is None:
        budget = LaneBudget(JOBS_BUDGET_SECONDS / max(1, entity_count))
    budget.limit_discovery(discovery_seconds)
    token = _BUDGET.set(budget)
    try:
        items, artifact = _search_jobs(
            company, date_range, config,
            web_backend=web_backend, explicit=explicit, site=site, budget=budget,
        )
    finally:
        _BUDGET.reset(token)
        budget.limit_discovery(None)
    # A caller that tries several companies on one budget says once, for all
    # of them, that none has a board (``log_no_board``).
    return items, _with_budget_outcome(items, artifact, budget, company, quiet=shared)


def _with_budget_outcome(
    items: list[dict[str, Any]],
    artifact: dict[str, Any],
    budget: LaneBudget,
    company: str,
    *,
    quiet: bool = False,
) -> dict[str, Any]:
    """Say so when the budget, not the board, ended the lane.

    With nothing read the reason is the lane's outcome; with rows it rides
    along as a note. A lane that looked everywhere in time and found no
    supported board says only that, and reports no failure.
    """
    if artifact.get("tier") == "ats":
        return artifact
    if budget.stopped_at or budget.spent():
        reason = f"{company}: {budget.reason()}"
        log.source_log("Jobs", reason, tty_only=False)
        artifact = dict(artifact)
        if items:
            artifact["_source_outcome_detail"] = reason
        else:
            artifact["_source_outcome"] = {
                "state": health.TIMEOUT, "detail": reason, "attempted": True,
            }
            # Only the lane's own budget running out means a second pass
            # would run out again. A share of it running out, for one of
            # several companies, leaves the rest of the budget to a retry.
            if budget.spent():
                artifact["_source_out_of_time"] = True
        return artifact
    if budget.unanswered and not items:
        # Time was left, but a board's API never answered: the lane cannot
        # say there is no board. Named, and open to a retry.
        reason = f"{company}: {'; '.join(budget.unanswered[:4])}"
        log.source_log("Jobs", reason, tty_only=False)
        return {
            **artifact,
            "_source_outcome": {"state": health.TIMEOUT, "detail": reason, "attempted": True},
        }
    if artifact.get("tier") == "web" and not items and not quiet:
        log_no_board([company])
    return artifact


def log_no_board(companies: list[str]) -> None:
    """The lane's one line for a topic none of whose companies has a board
    it can read. Not a failure: the run goes on without jobs."""
    first, *rest = companies
    also = f" (also tried: {', '.join(rest)})" if rest else ""
    log.source_log(
        "Jobs",
        f"no supported job board found for {first}{also}; the lane reads "
        f"{SUPPORTED_SYSTEMS}",
        tty_only=False,
    )


def _search_jobs(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    web_backend: str,
    explicit: bool,
    site: str,
    budget: LaneBudget,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    attempted: list[str] = []

    # --- Discovery: fetch the careers page and read the ATS off it (Tier 1). ---
    careers_html, careers_url = _resolve_careers_page(
        company, date_range, config, backend=web_backend, site=site,
    )
    provider, slug = (None, None)
    found_via = "slug-guess"
    probed: list[dict[str, Any]] | None = None
    # What the careers page calls its organization, when that is a longer or
    # shorter form of the company's name ("Anduril Industries" for "Anduril").
    # A page a web search named is anyone's page and is not read for this.
    page_via = _found_via(careers_url or "", company, site)
    stated_names = [
        name for name in _stated_names(careers_html or "")
        if _leads_with(name, company) and not _same_company(name, company)
    ] if page_via != "search" else []
    if careers_html:
        provider, slug = detect_ats(careers_html)
        if provider:
            attempted.append(f"careers:{provider}:{slug}")
            found_via = _found_via(careers_url or "", company, site)

    # Fallback discovery: cheap deterministic slug probe (only after careers-page
    # discovery fails - never the entry point).
    if not provider:
        if stated_names:
            provider, slug, probe_attempts, probed = _probe_boards(company, also=stated_names)
        else:
            provider, slug, probe_attempts, probed = _probe_boards(company)
        attempted.extend(probe_attempts)
        if provider and page_via == "site" and slug not in _candidate_slugs(company):
            # The slug is the confirmed official site's own name for the
            # company. A domain guessed from the company's name can be
            # another business's, so the name it states may find a board but
            # never makes it the company's: that board stays a slug guess.
            found_via = "site-name"

    # --- Tier 1: call the resolved ATS API. Trust it only if it has jobs. ---
    # Finding the board may have had a share of the budget; reading a found
    # board has all that is left. The share comes back when the board turns
    # out to hold nothing, so the fallbacks below cannot spend the time of
    # the companies still to be tried.
    share = budget.step_deadline
    if provider and slug:
        found = _read_board(
            company, provider, slug, date_range,
            attempted=attempted, explicit=explicit, budget=budget,
            found_via=found_via, probed=probed, stated_names=stated_names,
        )
        if found is not None:
            return found

    budget.step_deadline = share

    # --- Tier 2: parse JSON-LD JobPosting off the careers page. ---
    if careers_html:
        attempted.append("careers:jsonld")
        jsonld_items = extract_jsonld_jobs(careers_html, careers_url or "")
        if jsonld_items:
            return jsonld_items, _artifact("jobs", company, attempted, jsonld_items,
                                           explicit, tier="careers-jsonld")

    # --- Tier 3: generic web search (noisy, low-confidence). ---
    fallback_items, artifact = _within_budget(
        lambda: search_jobs_web(company, date_range, config, backend=web_backend),
        "the web search for jobs pages",
        ([], {}),
    ) if web_backend != "none" else ([], {})
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


def _read_board(
    company: str,
    provider: str,
    slug: str,
    date_range: tuple[str, str],
    *,
    attempted: list[str],
    explicit: bool,
    budget: LaneBudget,
    found_via: str,
    probed: list[dict[str, Any]] | None = None,
    stated_names: list[str] | None = None,
    owner: Callable[[str], tuple[str, str]] | None = None,
    full: bool = True,
    probed_board: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """Read a found board and build its artifact, or None when it holds nothing.

    A board belongs to the company when its known official site links to it
    or the board names the company. A guessed slug or a guessed domain can be
    another company's, so such a board still counts roles but gives no digest
    unless it names the company. A board under a slug taken from the name the
    company's confirmed official site states (``site-name``) is the company's
    when the board states that same name.

    ``owner`` decides instead for a board the host passed: given the name the
    board states, it returns the company whose board it is (or '') and how
    that is known. With ``full`` off (a host board not yet tied to the
    company) the read stays inside the discovery share and a paged board
    gives its first page only.
    """
    if full:
        budget.limit_discovery(None)
    board: dict[str, Any] = dict(probed_board or {})
    deadline = budget.deadline if full else min(budget.deadline, budget.step_deadline)
    # Passed only when set, so the plain call stays what it always was.
    part: dict[str, Any] = {} if full else {"first_page_only": True}
    try:
        if probed is not None:
            # The probe already read this board; it is not read twice.
            items = probed
        elif _out_of_time(f"reading the {provider} board {slug}"):
            items = []
        elif provider == ATS_PROVIDER_WORKDAY:
            items, board = search_workday_board(slug, deadline=deadline, **part)
        elif provider == ATS_PROVIDER_SMARTRECRUITERS:
            items, board = read_smartrecruiters_board(slug, deadline=deadline, **part)
        elif provider == ATS_PROVIDER_WORKABLE:
            items, board = read_workable_board(slug)
        else:
            items = _fetch_ats(provider, slug)
    except http.HTTPError as exc:
        if exc.outcome_state == health.TIMEOUT:
            _note_slow(f"the {provider} board {slug} did not answer")
            # Its own request limit or the lane's: either way the board
            # was found and not read, which is said by name.
            budget.unanswered.append(f"the {provider} board {slug} did not answer")
        items = []
    if not items:
        return None
    stated = next(
        (str(item["company_name"]) for item in items if item.get("company_name")), "",
    )
    if owner is not None:
        owned_by, found_via = owner(stated)
        owned = bool(owned_by)
        company = owned_by or company
    else:
        owned = (
            found_via == "site"
            or _same_company(stated, company)
            or (
                found_via == "site-name"
                and any(_same_company(stated, name) for name in stated_names or [])
            )
        )
    artifact = _artifact("jobs", company, attempted, items, explicit,
                         tier="ats", provider=provider, slug=slug)
    artifact.update(board)
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
        notes = []
        coverage = _coverage_note(items, board, provider, budget)
        if coverage:
            notes.append(f"{coverage}, so the roles below are chosen from the rows read")
        if late or failed:
            reasons = []
            if late:
                reasons.append(f"time budget spent before {late}")
            if failed:
                reasons.append(f"request failed or came back empty for {failed}")
            notes.append(
                f"role text fetched for {len(selected) - late - failed} of "
                f"{len(selected)} selected roles ({'; '.join(reasons)})"
            )
        if notes:
            artifact["digest"]["note"] = "; ".join(notes)
    # Counted last: Workday's true posted dates arrive with the detail
    # calls above.
    artifact.update(_board_counts(items, board, date_range))
    coverage = _coverage_note(items, board, provider, budget)
    if coverage:
        artifact["coverage_note"] = coverage
        log.source_log(
            "Jobs", f"{company}: {provider} board {slug}: {coverage}", tty_only=False,
        )
    return items, artifact


def parse_board_url(url: str) -> tuple[str | None, str | None]:
    """The provider and slug of a job-board URL, or ``(None, None)``.

    Only a URL whose host is one of the supported systems' own is read, so a
    system's name in the path or query of another site is not a board.
    """
    text = (url or "").strip()
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except ValueError:
        return None, None
    host = parsed.netloc.lower().removeprefix("www.")
    if parsed.scheme not in ("http", "https") or not any(
        _same_site(host, domain) for domain in _BOARD_DOMAINS
    ):
        return None, None
    return detect_ats(host + parsed.path + (f"?{parsed.query}" if parsed.query else ""))


def search_board_url(
    url: str,
    companies: list[str],
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    site: str = "",
    sites: dict[str, str] | None = None,
    explicit: bool = False,
    budget: LaneBudget | None = None,
    discovery_seconds: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read the board at a URL the host found by web search (``--jobs-board``).

    ``companies`` are the names the board may belong to: the topic or the
    company it resolved to, then the companies the host named. The board is
    one of theirs when the name it states is that company's (or leads with
    it as whole words: "Anduril Industries" for "Anduril"), when it states no
    name and its slug is the company's name, or when the official ``site``
    links to it (``site`` is the first company's; ``sites`` gives the
    confirmed site of any other by name). Any other board gives its counts
    and no digest, with a line on stderr saying why. A URL that is not on a
    supported system, or a board that cannot be read, gives ``([], {})`` and
    one line.

    Whose board it is is settled before the board is read in full. Until
    then the call stays inside ``discovery_seconds``, its share of the
    budget, and a paged board (Workday, SmartRecruiters) that is not the
    company's gives its first page only, so a large board passed by mistake
    leaves the lane time to find the right one.
    """
    companies = [name.strip() for name in companies if name and name.strip()]
    provider, slug = parse_board_url(url)
    if not provider or not slug:
        log.source_log(
            "Jobs",
            f"--jobs-board ignored: {_host(url) or url.strip()[:80] or 'the value'} is not a "
            f"job board the lane reads ({SUPPORTED_SYSTEMS})",
            tty_only=False,
        )
        return [], {}
    if not companies:
        return [], {}
    budget = budget or LaneBudget()
    budget.limit_discovery(discovery_seconds)
    token = _BUDGET.set(budget)
    known_sites = {companies[0]: site, **(sites or {})}

    def owner(stated: str) -> tuple[str, str]:
        # A board that states no name is known by its slug (Workday: its tenant).
        own = stated or slug.split(".", 1)[0]
        for name in companies:
            if _same_company(own, name) or (stated and _leads_with(stated, name)):
                return name, "host"
        checked_site = False
        for name in companies:
            if not known_sites.get(name):
                continue
            checked_site = True
            if _site_links_board(name, known_sites[name], provider, slug, date_range, config):
                return name, "site"
        named = f"names {stated!r}" if stated else "states no company name"
        checked = (
            "and the official site does not link it" if checked_site
            else "and there is no official site to check it against"
        )
        log.source_log(
            "Jobs",
            f"--jobs-board: the {provider} board {slug} {named}, not "
            f"{' or '.join(companies)}, {checked}; its roles are counted, with no digest",
            tty_only=False,
        )
        return "", "host"

    try:
        # A board that is not there is the host's wrong URL, not an outage.
        with http.expected_misses(400, 401, 403, 404):
            # Whose board it is can be known before the board is read: most
            # systems state no name, Greenhouse states it in the board's own
            # record, and SmartRecruiters on every row of its first page.
            settled: tuple[str, str] | None = None
            first: list[dict[str, Any]] | None = None
            _info: dict[str, Any] = {}
            if provider == ATS_PROVIDER_GREENHOUSE:
                stated = _greenhouse_board_name(slug)
                # With no record to go by, the rows say whose it is.
                settled = owner(stated) if stated else None
            elif provider != ATS_PROVIDER_SMARTRECRUITERS:
                settled = owner("")
            else:
                first, _info = read_smartrecruiters_board(
                    slug, deadline=min(budget.deadline, budget.step_deadline),
                    first_page_only=True,
                )
                settled = owner(next(
                    (str(item["company_name"]) for item in first if item.get("company_name")), "",
                )) if first else ("", "host")
            # A board that is not the company's is not read past the page
            # that showed whose it is.
            unowned_page = first if settled is not None and not settled[0] else None
            found = _read_board(
                companies[0], provider, slug, date_range,
                attempted=[f"host:{provider}:{slug}"], explicit=explicit, budget=budget,
                found_via="host",
                owner=(lambda _stated: settled) if settled is not None else owner,
                # Only a board already known to be the company's leaves the
                # share and is read with all the time the lane has left.
                full=bool(settled and settled[0]),
                probed=unowned_page, probed_board=_info if unowned_page else None,
            )
    except http.HTTPError:
        found = None
    finally:
        _BUDGET.reset(token)
        budget.limit_discovery(None)
    if found is None:
        log.source_log(
            "Jobs",
            f"--jobs-board: the {provider} board {slug} could not be read or lists no "
            "roles; looking for the company's board without it",
            tty_only=False,
        )
        return [], {}
    return found


def _greenhouse_board_name(slug: str) -> str:
    """The company name a Greenhouse board's own record states, or '' when
    the record could not be read. One small request, where the board's job
    list can be megabytes."""
    try:
        data = http.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}",
            retries=1, **_bounds(API_TIMEOUT_SECONDS),
        )
    except http.HTTPError:
        return ""
    return " ".join(str(data.get("name") or "").split()) if isinstance(data, dict) else ""


def _site_links_board(
    company: str,
    site: str,
    provider: str,
    slug: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
) -> bool:
    """Whether the official site's careers page, or the jobs page it links
    to, names this board."""
    html, _url = _resolve_careers_page(company, date_range, config, backend="none", site=site)
    return (provider, slug.lower()) in _boards_named(html or "")


def _boards_named(html: str) -> set[tuple[str, str]]:
    """Every (provider, lower-cased slug) a page names."""
    named: set[tuple[str, str]] = set()
    for provider, pattern in _ATS_LINK_PATTERNS:
        for match in re.finditer(pattern, html):
            slug = match.group(match.lastindex or 1).strip().strip("/.")
            if not slug or slug.lower() in _SLUG_STOPWORDS:
                continue
            if provider == ATS_PROVIDER_WORKDAY:
                slug = f"{match.group(1)}.{match.group(2)}/{slug}"
            named.add((provider, slug.lower()))
    return named


_ORGANIZATION_TYPES = {"organization", "corporation", "localbusiness"}
_SITE_NAME_RE = re.compile(
    r"""<meta[^>]+property=["']og:site_name["'][^>]*content=["']([^"']{1,80})["']""", re.I,
)
_STATED_NAME_LIMIT = 3


def _stated_names(html: str) -> list[str]:
    """What a page calls the organization behind it: the names of the
    Organization in its structured data, then its ``og:site_name``."""
    names: list[str] = []

    def add(value: Any) -> None:
        for name in value if isinstance(value, list) else [value]:
            text = " ".join(unescape(name).split()) if isinstance(name, str) else ""
            if text and len(text) <= 80 and text not in names:
                names.append(text)

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, list):
            for child in node:
                walk(child, depth + 1)
        elif isinstance(node, dict):
            kinds = node.get("@type")
            kinds = kinds if isinstance(kinds, list) else [kinds]
            if any(isinstance(kind, str) and kind.lower() in _ORGANIZATION_TYPES for kind in kinds):
                for key in ("name", "legalName", "alternateName"):
                    add(node.get(key))
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child, depth + 1)

    for block in _JSONLD_RE.findall(html or ""):
        try:
            walk(json.loads(block.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    add(_SITE_NAME_RE.findall(html or ""))
    return names[:_STATED_NAME_LIMIT]


# --------------------------------------------------------------------------- #
# Careers-page discovery
# --------------------------------------------------------------------------- #

def _coverage_note(
    items: list[dict[str, Any]], board: dict[str, Any], provider: str, budget: LaneBudget,
) -> str:
    """What stopped a board's listing short of the rows it reports, or ''."""
    if not board.get("board_cut_short"):
        return ""
    listed = min(int(board.get("board_total") or 0), WORKDAY_ROW_CEILING)
    read = f"{len(items):,} rows read" + (
        f" of the {listed:,} the board lists" if listed > len(items) else ""
    )
    if provider == ATS_PROVIDER_WORKABLE:
        cause = "only the board's first page is read"
    elif board.get("board_first_page_only"):
        cause = "only the first page of a board not tied to the company is read"
    elif budget.spent():
        cause = f"the lane's {budget.seconds:g}s time budget ran out"
    else:
        cause = "a listing page failed or the board gave no total"
    return f"board read in part: {read} ({cause})"


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
            if _out_of_time(f"the careers page {url}"):
                return None, None
            html, no_answer = _careers_text(url, guessed=guessed)
            if no_answer:
                # Nothing answers at this host, so its other page is not tried.
                break
            if html and _looks_like_careers_html(html):
                return _board_page(html, url, guessed=guessed)

    if backend != "none":
        careers_url = _within_budget(
            lambda: _search_for_careers_url(company, date_range, config, backend=backend),
            "the web search for the careers page",
            None,
        )
        if careers_url and not _out_of_time(f"the careers page {careers_url}"):
            # A page a web search named, on anyone's host: like a guessed
            # domain, its failure to answer is not an outage of the lane.
            html, _no_answer = _careers_text(careers_url, guessed=True)
            if html:
                return html, careers_url

    return None, None


def _careers_text(url: str, *, guessed: bool) -> tuple[str | None, bool]:
    """Fetch a careers page on the company's site or on a guessed domain.

    Returns the page text, and whether the host gave no answer at all: its
    name did not resolve, it refused the connection, or it did not answer in
    time. A 403 or 404 is a page that is not there, never a run failure. On a
    ``guessed`` domain no answer means the company has no such site: no board
    was found, so nothing is recorded as a failure, and a host that only
    timed out is named in the budget's ``slow`` list. On the known official
    site the same failure is an outage and is recorded.

    One attempt, no longer than ``GUESS_TIMEOUT_SECONDS`` on a guessed domain
    or ``PAGE_TIMEOUT_SECONDS`` on the official site, and never past the
    lane's budget.
    """
    host = _host(url)
    try:
        return _careers_fetch(url, host, guessed=guessed)
    except UnicodeDecodeError:
        # Not a text page (a file, or text in another encoding): no page.
        return None, False


def _careers_fetch(url: str, host: str, *, guessed: bool) -> tuple[str | None, bool]:
    with http.expected_misses(403, 404):
        if guessed:
            # ``capture_failures`` keeps a guessed host's failures out of the
            # run's record, whatever they are.
            with http.capture_failures() as failures:
                html = http.get_text(
                    url, accept="text/html", retries=1, retry_dns=False,
                    **_bounds(GUESS_TIMEOUT_SECONDS),
                )
            if any(failure.outcome_state == health.TIMEOUT for failure in failures):
                _note_slow(f"{host} did not answer in {GUESS_TIMEOUT_SECONDS}s")
            return html, any(failure.status_code is None for failure in failures)
        with http.tee_failures() as failures:
            html = http.get_text(
                url, accept="text/html", retries=1, **_bounds(PAGE_TIMEOUT_SECONDS),
            )
    timed_out = any(failure.outcome_state == health.TIMEOUT for failure in failures)
    if timed_out:
        _note_slow(f"{host} did not answer in {PAGE_TIMEOUT_SECONDS}s")
    return html, timed_out or any(failure.no_host for failure in failures)


# How many pages linked from a careers page are read when looking for the board.
_LINKED_PAGE_LIMIT = 2
_JOBS_LINK_WORDS = re.compile(r"job|position|opening|vacanc|search|board|role", re.I)
# A link to a file is not a jobs page (a PDF under /jobs/ is a legal notice).
_FILE_LINK = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|png|jpe?g|gif|svg|webp|mp4|ics|xml|json|css|js)$", re.I)


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
        if _out_of_time(f"the jobs page {link}"):
            break
        linked, _no_answer = _careers_text(link, guessed=guessed)
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
        if _FILE_LINK.search(parsed.path):
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
        if not url or _is_search_redirect(url):
            # An ad or a search engine's own redirect is no one's careers page.
            continue
        lowered = url.lower()
        if any(token in lowered for token in (
            "career", "/jobs", "ashbyhq", "greenhouse", "lever.co", "workable", "smartrecruiters",
            "myworkdayjobs",
        )):
            return url
    return None


_SEARCH_ENGINE_HOSTS = ("duckduckgo.com", "bing.com", "google.com", "yahoo.com", "brave.com")
_REDIRECT_PATH = re.compile(r"^/(?:y\.js|l/?|url|aclk|ck/a|r|rd)(?:$|[/?])", re.I)


def _is_search_redirect(url: str) -> bool:
    """Whether a URL is a search engine's ad or click-through link
    (``duckduckgo.com/y.js?ad_domain=...``, ``duckduckgo.com/l/?uddg=...``,
    ``google.com/url?q=...``, ``bing.com/aclk``) rather than a page."""
    try:
        parsed = urlparse(url if "://" in url else f"https:{url}" if url.startswith("//") else f"https://{url}")
    except ValueError:
        return True
    host = parsed.netloc.lower()
    if not any(_same_site(host, engine) for engine in _SEARCH_ENGINE_HOSTS):
        return False
    query = parsed.query.lower()
    return bool(
        _REDIRECT_PATH.match(parsed.path or "/")
        or "ad_domain=" in query or "uddg=" in query
    )


# One posting on a supported board: the URL shapes each provider gives a role.
_POSTING_URL_PATTERNS: tuple[str, ...] = (
    r"^(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([A-Za-z0-9_.-]+)/jobs/\d+",
    r"^jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)/[0-9a-f-]{20,}",
    r"^jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)/[0-9a-f-]{20,}",
    r"^apply\.workable\.com/([A-Za-z0-9_.-]+)/j/[A-Za-z0-9]+",
    r"^jobs\.smartrecruiters\.com/([A-Za-z0-9_.-]+)/\d+",
    r"^([A-Za-z0-9_-]+)\.wd[0-9]+\.myworkdayjobs\.com/(?:[^?#]*/)?job/[^?#]+",
)


def _posting_board(url: str) -> str:
    """The board slug when ``url`` is one job posting on a supported board,
    else ''. A board's front page, a careers landing page, a news story or a
    search engine's link is not a posting."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https"):
        return ""
    target = parsed.netloc.lower().removeprefix("www.") + parsed.path
    for pattern in _POSTING_URL_PATTERNS:
        match = re.match(pattern, target, flags=re.I)
        if match:
            return match.group(1)
    return ""


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


def _names_overlap(stated: str, company: str) -> bool:
    """Whether a board's stated name could be a longer or shorter form of the
    company's ("Meta Platforms" for "Meta"). Looser than ``_same_company``:
    it only tells a namesake's board from one that may be the company's."""
    one, other = _company_slug(stated), _company_slug(company)
    if min(len(one), len(other)) < 3:
        return one == other
    return one in other or other in one


def _name_words(name: str) -> list[str]:
    text = re.sub(r"['\u2019.]", "", name.lower())
    text = re.sub(r"\b(inc|llc|ltd|corp|corporation|company|co)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).split()


def _leads_with(stated: str, company: str) -> bool:
    """Whether one name is the other with whole words added at its end
    ("Anduril Industries" for "Anduril"; never "Metabase" for "Meta").

    Looser than ``_same_company``, so it never makes a board found by a
    guessed slug the company's: it is used where something besides the slug
    points at the board (the host passed its URL, or the company's careers
    page states the longer name)."""
    one, other = _name_words(stated), _name_words(company)
    short, long = sorted((one, other), key=len)
    return bool(short) and long[:len(short)] == short


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
    provider, slug, attempts, _items = _probe_boards(company)
    return provider, slug, attempts


# Slugs tried beyond the company's own: those of the names its careers page states.
_STATED_SLUG_LIMIT = 4


def _probe_boards(
    company: str,
    *,
    also: list[str] | None = None,
) -> tuple[str | None, str | None, list[str], list[dict[str, Any]] | None]:
    """``_probe_ats`` plus the rows of the board it found, so the caller does
    not read that board a second time.

    A board found by a guessed slug that states another company's name is
    someone else's board under the same slug, and is passed over. A probe the
    lane's budget cut off ends the probing; any other failure of a board's API
    is an outage and is raised, naming the provider and slug.

    ``also`` are other names for the company (what its careers page calls
    its organization); their slugs are tried after the company's own.
    """
    attempts: list[str] = []
    slugs = _candidate_slugs(company)
    more = [
        slug for name in also or [] for slug in _candidate_slugs(name) if slug not in slugs
    ]
    slugs += list(dict.fromkeys(more))[:_STATED_SLUG_LIMIT]
    # Workable/SmartRecruiters are omitted here: their slugs rarely match the
    # company name, so blind probing is unreliable - they're reached only via
    # careers-page discovery, where the real slug is published.
    for slug in slugs:
        for provider in (ATS_PROVIDER_GREENHOUSE, ATS_PROVIDER_ASHBY, ATS_PROVIDER_LEVER):
            if _out_of_time(f"the {provider} probe for the slug {slug}"):
                return None, None, attempts, None
            attempts.append(f"probe:{provider}:{slug}")
            try:
                with http.expected_misses(400, 401, 403, 404):
                    items = _fetch_ats(provider, slug)
            except http.DeadlineExceeded:
                # The request's hard deadline is the earlier of its own
                # limit and the time the lane has left (``_bounds``). Only
                # the second ends the probing; a provider that used up its
                # own limit is that probe's failure, and the next is asked.
                budget = _BUDGET.get()
                if budget is not None:
                    budget.note_slow(f"the {provider} API had not answered for the slug {slug}")
                    if budget.remaining() <= 0:
                        budget.stop(f"the {provider} probe for the slug {slug} answered")
                        return None, None, attempts, None
                    budget.unanswered.append(
                        f"the {provider} API did not answer for the slug {slug}"
                    )
                attempts.append(f"no-answer:{provider}:{slug}")
                continue
            except http.HTTPError as exc:
                if exc.status_code in {400, 401, 403, 404}:
                    continue
                failure = http.HTTPError(
                    f"{provider} board API failed for the guessed slug {slug!r}: {exc}",
                    status_code=exc.status_code,
                    body=exc.body,
                    outcome_state=exc.outcome_state,
                )
                failure.no_host = exc.no_host
                raise failure from exc
            if not items:
                continue
            stated = next(
                (str(item["company_name"]) for item in items if item.get("company_name")), "",
            )
            if stated and not _names_overlap(stated, company):
                attempts.append(f"not-the-company:{provider}:{slug}")
                continue
            return provider, slug, attempts, items
    return None, None, attempts, None


# --------------------------------------------------------------------------- #
# Tier 1: ATS API fetchers + parsers
# --------------------------------------------------------------------------- #

def _fetch_ats(provider: str, slug: str, *, retries: int = 2) -> list[dict[str, Any]]:
    fetchers: dict[str, Callable[..., list[dict[str, Any]]]] = {
        ATS_PROVIDER_GREENHOUSE: search_greenhouse_board,
        ATS_PROVIDER_ASHBY: search_ashby_board,
        ATS_PROVIDER_LEVER: search_lever_board,
        ATS_PROVIDER_WORKABLE: search_workable_board,
        ATS_PROVIDER_SMARTRECRUITERS: search_smartrecruiters_board,
    }
    fetcher = fetchers.get(provider)
    if not fetcher:
        return []
    # Only the three providers a slug is probed on take an attempt count.
    if retries != 2 and provider in (
        ATS_PROVIDER_GREENHOUSE, ATS_PROVIDER_ASHBY, ATS_PROVIDER_LEVER,
    ):
        return fetcher(slug, retries=retries)
    return fetcher(slug)


def search_greenhouse_board(board_token: str, *, retries: int = 2) -> list[dict[str, Any]]:
    """Return jobs from Greenhouse's public Job Board API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    data = http.get(
        url, params={"content": "true"}, retries=retries,
        **_bounds(API_TIMEOUT_SECONDS, retries),
    )
    jobs = data.get("jobs") if isinstance(data, dict) else []
    if not isinstance(jobs, list):
        return []
    return [_greenhouse_job_to_item(job, board_token) for job in jobs if isinstance(job, dict)]


def search_ashby_board(slug: str, *, retries: int = 2) -> list[dict[str, Any]]:
    """Return jobs from Ashby's public posting API (needs a browser UA)."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    data = http.get(
        url,
        params={"includeCompensation": "true"},
        headers={"User-Agent": http.BROWSER_USER_AGENT},
        retries=retries,
        **_bounds(API_TIMEOUT_SECONDS, retries),
    )
    return parse_ashby_response(data, slug)


def search_lever_board(slug: str, *, retries: int = 2) -> list[dict[str, Any]]:
    """Return jobs from Lever's public postings API (returns a JSON list)."""
    url = f"https://api.lever.co/v0/postings/{slug}"
    data = http.get(
        url, params={"mode": "json"}, retries=retries, **_bounds(API_TIMEOUT_SECONDS, retries),
    )
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
    data = http.post(url, json_data={}, retries=2, **_bounds(API_TIMEOUT_SECONDS, 2))
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
    clock: Callable[[], float] | None = None,
    first_page_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a SmartRecruiters board's rows and what was read of the board.

    The board is read a page at a time up to the row ceiling. The second value
    has the same keys as ``search_workday_board``'s: ``board_capped`` when the
    board reports more rows than the ceiling, ``board_cut_short`` when the time
    budget ran out or a page failed. A failed first page returns no rows.
    ``first_page_only`` reads that page alone, for a board not yet known to
    be the company's.
    """
    info: dict[str, Any] = {"board_total": 0, "board_capped": False, "board_cut_short": False}
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    clock = clock or _clock()
    deadline = _workday_deadline(deadline, budget_seconds, clock)

    def page(offset: int) -> Callable[[float], Any]:
        params = {"limit": str(SMARTRECRUITERS_PAGE_SIZE)}
        if offset:
            params["offset"] = str(offset)
        return lambda http_deadline: http.get(
            url, params=params, timeout=API_TIMEOUT_SECONDS, retries=2,
            deadline_monotonic=http_deadline,
        )

    first = _workday_run([page(0)], deadline, clock)[0]
    if not isinstance(first, dict):
        return [], info
    items = parse_smartrecruiters_response(first, slug)
    total = max(_reported_total(first, "totalFound"), len(items))
    info["board_total"] = total
    info["board_capped"] = total > SMARTRECRUITERS_ROW_CEILING
    if first_page_only:
        # With no reported total, a full page may not be the whole board.
        info.update(
            board_cut_short=total > len(items) or (
                "totalFound" not in first and len(items) >= SMARTRECRUITERS_PAGE_SIZE
            ),
            board_first_page_only=True,
        )
        return items, info
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
            # Each call makes up to two attempts; one page or detail that
            # never finishes is cut off at its own limit, not the board's.
            return call(time.monotonic() + min(remaining, _request_seconds(API_TIMEOUT_SECONDS, 2)))
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
    clock: Callable[[], float] | None = None,
    first_page_only: bool = False,
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
    clock = clock or _clock()
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
            timeout=API_TIMEOUT_SECONDS,
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
    if first_page_only:
        # For a board not yet known to be the company's.
        items = parse_workday_response(first, token)
        info.update(board_cut_short=total > len(items), board_first_page_only=True)
        return items, info
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
    clock: Callable[[], float] | None = None,
) -> tuple[int, int]:
    """Fill ``description`` and the true posted date on the given Workday items.

    One call per item, so pass only the few roles that need it. An item whose
    call fails, or would start after the deadline, is left as it was. Returns
    two counts of items left unfetched: those the deadline stopped before
    their call started, and those whose call failed or returned no posting.
    """
    clock = clock or _clock()
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
            timeout=API_TIMEOUT_SECONDS,
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
    """Fallback to the configured web search, for job postings only.

    A result is kept when its URL is one posting on a supported board
    (Greenhouse, Ashby, Lever, Workable, SmartRecruiters, Workday) and that
    board's slug is a form of the company's name. Everything else a search
    for "careers jobs hiring" returns is left out: ads, a search engine's
    redirect links, encyclopedia and news pages, job aggregators and the
    company's own careers landing page are not roles.
    """
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
        board = _posting_board(url)
        if not board or not _names_overlap(board, company):
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
            "why_relevant": "Job posting on a supported board, found by web search",
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

