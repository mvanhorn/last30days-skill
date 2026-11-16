"""Public jobs/careers retrieval for Hiring Signals.

Tiered strategy (each tier degrades gracefully; the artifact records which
tier produced results so synthesis knows the confidence level):

- Tier 1 - direct ATS API. The company's own board is authoritative and
  structured. Discovery is careers-page-first: fetch the careers page and read
  the provider + exact slug straight off the embed/link, then call that API.
  We only emit ATS results when a call actually returns a non-empty board, so a
  bad slug guess never produces fabricated coverage. Slug-probing is a fallback,
  never the entry point.
  A careers page on a hiring platform (Oracle Recruiting Cloud, Jibe, Phenom,
  TalentBrew, Eightfold; ``lib.job_boards.detect``) is read through that
  platform's public listing endpoint. A reader that fails falls to the
  roster tier, then the web tier, and the artifact says so.
- Tier 1b - roster. A job sitemap the careers site lists in its robots.txt
  (``lib.job_boards.sitemap``): titles only, no posting dates.
- Tier 2 - careers page found, no supported ATS API. Parse schema.org
  ``JobPosting`` JSON-LD (emitted by most careers pages for Google Jobs SEO,
  regardless of ATS).
- Tier 3 - generic web search. Last resort, noisy, clearly low-confidence.
"""

from __future__ import annotations

import importlib
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

from . import dates, grounding, health, http, jobs_digest, jobs_story, log
from .job_boards import detect as board_detect
from .job_boards import robots as board_robots


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

# Company-run job boards. Most large companies run their careers site on one
# of a few hiring platforms, and the careers page names it: such a board is
# found by detection (``lib.job_boards.detect``) and read by that platform's
# reader, with no entry here. This table is only for the few companies whose
# board cannot be found from their official site: a job site of their own
# with no platform behind it (Apple, Amazon, Google), or a careers page that
# answers automated requests with a block while its hosted board does not
# (Intel, Salesforce). Each is read only when the run knows the company's
# official site, and the site's host is what picks the board, so a reader is
# never reached by guessing a domain or matching a name.
COMPANY_PROVIDER_APPLE = "apple"
COMPANY_PROVIDER_AMAZON = "amazon"
COMPANY_PROVIDER_EIGHTFOLD = "eightfold"
COMPANY_PROVIDER_GOOGLE = "google"
COMPANY_PROVIDER_IBM = "ibm"
# Platforms found by detection, read by ``lib.job_boards``.
PLATFORM_PROVIDER_ORACLE = board_detect.PROVIDER_ORACLE
PLATFORM_PROVIDER_JIBE = board_detect.PROVIDER_JIBE
PLATFORM_PROVIDER_PHENOM = board_detect.PROVIDER_PHENOM
PLATFORM_PROVIDER_TALENTBREW = board_detect.PROVIDER_TALENTBREW
PLATFORM_PROVIDER_KULA = board_detect.PROVIDER_KULA
SITEMAP_PROVIDER = "sitemap"
_GOOGLE_BOARD = {"name": "Google", "provider": COMPANY_PROVIDER_GOOGLE, "board": "www.google.com"}
# Official-site host -> the board that site's company runs. ``name`` is how
# the footer names the company when its board cannot be read. A ``workday``
# entry's ``board`` is the board token ``detect_ats`` would build.
COMPANY_BOARDS: dict[str, dict[str, str]] = {
    "apple.com": {"name": "Apple", "provider": COMPANY_PROVIDER_APPLE, "board": "jobs.apple.com"},
    "amazon.com": {"name": "Amazon", "provider": COMPANY_PROVIDER_AMAZON, "board": "www.amazon.jobs"},
    "google.com": _GOOGLE_BOARD,
    "about.google": _GOOGLE_BOARD,
    "abc.xyz": _GOOGLE_BOARD,
    # intel.com and its jobs host answer every request with a 403.
    "intel.com": {"name": "Intel", "provider": ATS_PROVIDER_WORKDAY, "board": "intel.wd1/External"},
    # careers.ibm.com answers an empty 202 to pages and sitemaps alike; the
    # search page reads IBM's own search API.
    "ibm.com": {"name": "IBM", "provider": COMPANY_PROVIDER_IBM, "board": "careers.ibm.com"},
    # Salesforce's job search loads its roles in the browser from Workday.
    "salesforce.com": {
        "name": "Salesforce", "provider": ATS_PROVIDER_WORKDAY,
        "board": "salesforce.wd12/External_Career_Site",
    },
}
# Official-site host -> the careers page to read first, for a company whose
# careers site is on a domain of its own that its official site does not link
# in a way the lane can follow. The page is then read like any careers page.
CAREERS_PAGES: dict[str, str] = {
    "disney.com": "https://join.disneycareers.com/global/en",
    "thewaltdisneycompany.com": "https://join.disneycareers.com/global/en",
    # join.nothing.tech is a static copy of this Kula board with no role
    # data, and careers.nothing.tech embeds a Greenhouse board that is gone.
    "nothing.tech": "https://careers.kula.ai/nothing",
}
# Official-site hosts whose job site is not read: its site prohibits
# automated collection, or it answers every automated request (pages,
# robots.txt and sitemap alike) with a block page. No request is sent to them.
UNREADABLE_JOB_SITES: dict[str, dict[str, str]] = {
    "meta.com": {"name": "Meta", "reason": "its site prohibits automated collection"},
    "metacareers.com": {"name": "Meta", "reason": "its site prohibits automated collection"},
    "tesla.com": {"name": "Tesla", "reason": "it answers every automated request with a block page"},
}
# Share of the lane's remaining time a company board's listing leaves for the
# role text of the roles the digest selects, so a long list never costs the
# digest its text.
DETAIL_SHARE = 1 / 3
# Story search: before a board's newest-first read, the board is searched for
# the run's story terms. A board with a keyword search is asked once per term,
# up to this many calls per company (every site of a Workday tenant counts as
# one company), fewer in a comparison. Eightfold answers 429 after two or
# three calls, so it is asked once.
STORY_CALLS_PER_COMPANY = 4
EIGHTFOLD_STORY_CALLS = 1
STORY_TIMEOUT_SECONDS = 10
# Story calls share this part of the time left when they start, so the
# newest-first read always keeps the rest.
STORY_SHARE = 1 / 3
# A company board is read newest first, a bounded number of pages at once,
# to its end or its ceiling. At about half a second a page, sixteen at once
# read Apple's 6,500 rows (20 a page) in about 20 s, inside the listing's
# share of the budget; Amazon (100 a page) and Oracle (200) take less.
COMPANY_BOARD_ROW_CEILING = 7000
COMPANY_BOARD_MAX_WORKERS = 16
AMAZON_PAGE_SIZE = 100
# Amazon's search reports at most this many hits and pages no further.
AMAZON_HITS_CAP = 10_000
APPLE_PAGE_SIZE = 20
EIGHTFOLD_PAGE_SIZE = 10
# The digest picks 40 roles; ten times that is enough to choose from.
EIGHTFOLD_ROW_CEILING = 400
# Apple's details call usually answers in 2 to 7 seconds, but about one call
# in four stalls; a second attempt after a short limit nearly always answers.
APPLE_DETAIL_TIMEOUT_SECONDS = 6
# Apple's retail store team. Its roles are counted on the board and never
# chosen for the digest: the newest pages are almost all retail.
APPLE_RETAIL_TEAM = "APPST"

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
    # Kula is read by its platform reader, under the slug the link names.
    (PLATFORM_PROVIDER_KULA, board_detect.KULA_BOARD_LINK),
]

# The hosts a board URL may be on: only these are read as a board's address.
_BOARD_DOMAINS = (
    "greenhouse.io", "ashbyhq.com", "lever.co", "workable.com",
    "smartrecruiters.com", "myworkdayjobs.com",
)
SUPPORTED_SYSTEMS = "Greenhouse, Ashby, Lever, Workable, SmartRecruiters and Workday"
# Hiring platforms read off a careers page on the company's own site.
PLATFORM_SYSTEMS = "Oracle Recruiting Cloud, Jibe, Phenom, TalentBrew, Eightfold and Kula"

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
        # Careers page URL -> the address that answered it, after redirects.
        self.landed: dict[str, str] = {}
        # Hosts that gave no answer at all, so no later step asks them again.
        self.unreachable: set[str] = set()

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


class StorySearch:
    """The story terms one company's boards are searched for, and the calls
    and time that search may still use.

    ``calls`` is how many server-side searches are left for the company. The
    deadline is fixed by the first story call and shared by every call after
    it: ``STORY_SHARE`` of the lane's time left at that moment.
    """

    def __init__(self, terms: list[str], *, calls: int) -> None:
        self.terms = list(terms)
        self.calls = calls
        self.deadline: float | None = None


_STORY: ContextVar[StorySearch | None] = ContextVar("last30days_jobs_story", default=None)


def _story_terms() -> list[str]:
    story = _STORY.get()
    return story.terms if story is not None else []


def _story_search_for(config: dict[str, Any], entity_count: int = 1) -> StorySearch:
    """The run's story terms (``_jobs_story_terms``) and its call cap,
    scaled down by the number of entities a comparison reads."""
    terms = [str(term) for term in config.get("_jobs_story_terms") or [] if str(term).strip()]
    entities = max(1, entity_count, int(config.get("_comparison_entity_count") or 1))
    return StorySearch(terms, calls=max(1, STORY_CALLS_PER_COMPANY // entities))


# Boards whose own search is asked for each story term, by provider: given
# the board (a Workday token, else the board's entry), the term and the
# call's hard deadline, each returns the rows found. Named like the readers.
_STORY_SEARCHERS: dict[str, str] = {
    ATS_PROVIDER_WORKDAY: "search_workday_story",
    COMPANY_PROVIDER_AMAZON: "search_amazon_story",
    COMPANY_PROVIDER_APPLE: "search_apple_story",
    COMPANY_PROVIDER_EIGHTFOLD: "search_eightfold_story",
    PLATFORM_PROVIDER_JIBE: "jibe:search_story",
}
# Searches whose rows carry the role's full text: a row is kept only when
# that text names the term. Rows from the others carry little or no text,
# so their board's match is taken as it is.
_STORY_TEXT_CHECKED = frozenset({COMPANY_PROVIDER_AMAZON, PLATFORM_PROVIDER_JIBE})
# Boards read whole: their rows are matched against the terms where they are.
_STORY_LOCAL = frozenset({
    ATS_PROVIDER_GREENHOUSE, ATS_PROVIDER_ASHBY, ATS_PROVIDER_LEVER,
    PLATFORM_PROVIDER_KULA, SITEMAP_PROVIDER,
})


def _story_text(item: dict[str, Any]) -> str:
    return f"{item.get('title') or ''}\n{item.get('description_blocks') or item.get('description') or ''}"


def _story_key(item: dict[str, Any]) -> str:
    """What makes a story row and a board row one role: Workday's job path
    (the same on every site of a tenant), Apple's role page (one row per
    location shares it), else the row's ID."""
    if item.get("provider") == ATS_PROVIDER_WORKDAY:
        return _workday_job_key(item)
    if item.get("provider") == COMPANY_PROVIDER_APPLE:
        return str(item.get("url") or "")
    return str(item.get("id") or item.get("url") or "")


def _story_record(*, ran: bool, terms: list[str], partial: bool = False) -> dict[str, Any]:
    """What a board's story search did: whether it ran, the terms it
    searched, and ``story_partial`` when a failed call, a 429, a timeout or
    the story deadline left a term unsearched. ``matches`` is
    filled in once the rows are tagged."""
    return {"ran": ran, "terms": list(terms), "matches": 0, "story_partial": partial}


def _search_story(provider: str, targets: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Ask a board's own search for each story term, before its newest-first read.

    ``targets`` are what the provider's searcher is given: each site of a
    Workday tenant, or the board's entry. Calls go one at a time, sites in
    order and each site's terms in order, until the company's calls are used
    (one on Eightfold) or the story deadline passes. A 429 or a timeout ends
    the story search for the board. Every row found carries ``story_term``;
    a role found by two calls is kept once. Returns ``(rows, record)``, the
    record None when the run has no story terms.
    """
    story = _STORY.get()
    if story is None or not story.terms:
        return [], None
    name = _STORY_SEARCHERS.get(provider)
    if name is None:
        return [], _story_record(ran=False, terms=[])
    clock = _clock()
    if story.deadline is None:
        budget = _BUDGET.get()
        left = budget.remaining() if budget is not None else JOBS_BUDGET_SECONDS
        story.deadline = clock() + left * STORY_SHARE
    search = _named(name)
    allowed = min(story.calls, EIGHTFOLD_STORY_CALLS) if provider == COMPANY_PROVIDER_EIGHTFOLD else story.calls
    pairs = [(target, term) for target in targets for term in story.terms][:max(0, allowed)]
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    searched: list[str] = []
    partial = False
    for target, term in pairs:
        left = story.deadline - clock()
        if left <= 0:
            partial = True
            break
        story.calls -= 1
        try:
            # A failed story call is said by ``story_partial``, not counted
            # among the run's failures: the board's own read still follows.
            with http.capture_failures():
                found = search(
                    target, term,
                    deadline_monotonic=time.monotonic() + min(left, STORY_TIMEOUT_SECONDS),
                )
        except http.HTTPError as exc:
            partial = True
            if exc.status_code == 429 or exc.outcome_state == health.TIMEOUT:
                break
            continue
        except ValueError:  # BoardShapeError, bad JSON
            partial = True
            continue
        if term not in searched:
            searched.append(term)
        for row in found:
            if provider in _STORY_TEXT_CHECKED and not jobs_story.matches(term, _story_text(row)):
                continue
            key = _story_key(row)
            if key and key not in seen:
                seen.add(key)
                # The order the board's search returned it, which the
                # digest keeps among one term's roles.
                rows.append({**row, "story_term": term, "story_rank": len(rows)})
    if partial:
        log.source_log(
            "Jobs", f"story search on the {provider} board stopped early "
            f"({len(searched)} of {len(story.terms)} terms searched); its newest-first read follows",
            tty_only=False,
        )
    # A board whose every story call failed was never searched: "not run", not "no matches".
    return rows, _story_record(ran=bool(searched), terms=searched, partial=partial)


def _tag_story_matches(items: list[dict[str, Any]], terms: list[str]) -> None:
    """Mark each row of a board read whole with the first story term its
    title or description names."""
    for item in items:
        text = _story_text(item)
        term = next((term for term in terms if jobs_story.matches(term, text)), None)
        if term is not None:
            item["story_term"] = term


def _merge_story_rows(
    items: list[dict[str, Any]], found: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tag the board rows a story search also found, and return the story
    rows the board's read did not reach. Those extras are offered to the
    digest and never added to the board's rows or counts."""
    by_key = {_story_key(item): item for item in items}
    extras: list[dict[str, Any]] = []
    for row in found:
        board_row = by_key.get(_story_key(row))
        if board_row is None:
            extras.append(row)
        elif not board_row.get("story_term"):
            board_row["story_term"] = row["story_term"]
            board_row["story_rank"] = row["story_rank"]
    return extras


def _local_story_record(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match the story terms against the rows of a board read whole."""
    terms = _story_terms()
    if not terms:
        return None
    _tag_story_matches(items, terms)
    record = _story_record(ran=True, terms=terms)
    record["matches"] = _story_matches(items)
    return record


def _story_matches(items: list[dict[str, Any]]) -> int:
    """Story-matched roles the digest may show."""
    return sum(1 for item in items if item.get("story_term") and not item.get("digest_excluded"))


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
    story_token = _STORY.set(_story_search_for(config, entity_count))
    try:
        items, artifact = _search_jobs(
            company, date_range, config,
            web_backend=web_backend, explicit=explicit, site=site, budget=budget,
        )
    finally:
        _STORY.reset(story_token)
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
    if (
        artifact.get("tier") == "web" and not items and not quiet
        and not artifact.get("job_site_unreadable")
    ):
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
        f"{SUPPORTED_SYSTEMS}, and {PLATFORM_SYSTEMS} on a careers page",
        tty_only=False,
    )


def _registry_entry(site: str) -> tuple[str, dict[str, str]] | None:
    """The registry entry for a known official site: ``("board", entry)`` for a
    company board the lane reads, ``("unreadable", entry)`` for a job site it
    does not, or None when the site is in neither."""
    host = _host(site) if site else ""
    if not host:
        return None
    for kind, table in (("board", COMPANY_BOARDS), ("unreadable", UNREADABLE_JOB_SITES)):
        for key, entry in table.items():
            if _same_site(host, key):
                return kind, {**entry, "site": key}
    return None


def _job_site_unreadable(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    entry: dict[str, str],
    reason: str,
    *,
    attempted: list[str],
    web_backend: str,
    explicit: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The lane's answer when the company's job site is known and not read.

    Says so once, then runs the web tier as any run without a board does. No
    careers page is fetched and no hosted-system slug is guessed.
    """
    name = entry.get("name") or company
    log.source_log(
        "Jobs", f"{jobs_digest.unreadable_job_site_line(name)} ({entry.get('site', '')}: {reason})",
        tty_only=False,
    )
    items, artifact = _web_tier(
        company, date_range, config, attempted=attempted, web_backend=web_backend,
        explicit=explicit,
    )
    artifact["job_site_unreadable"] = name
    artifact["job_site_unreadable_reason"] = reason
    return items, artifact


def _web_tier(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    attempted: list[str],
    web_backend: str,
    explicit: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Tier 3: a generic web search for postings (noisy, low-confidence)."""
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

    # --- A company whose board cannot be found from its site, or is not read. ---
    registered = _registry_entry(site)
    if registered is not None:
        kind, entry = registered
        if kind == "unreadable":
            return _job_site_unreadable(
                company, date_range, config, entry, entry["reason"],
                attempted=attempted, web_backend=web_backend, explicit=explicit,
            )
        attempted.append(f"company:{entry['provider']}:{entry['board']}")
        hosted = entry["provider"] in _HOSTED_PROVIDERS
        found, reason = _read_found_board(
            company, entry["provider"], entry["board"], date_range,
            attempted=attempted, explicit=explicit, budget=budget,
            found_via="site", company_board=None if hosted else entry,
        )
        if found is not None:
            return found
        if reason == "unanswered":
            # A board that did not answer is not one that cannot be read:
            # the budget names it, and a retry may read it.
            return _web_tier(
                company, date_range, config, attempted=attempted,
                web_backend=web_backend, explicit=explicit,
            )
        return _fall_through(
            company, date_range, config, entry, reason,
            hosts=_sitemap_hosts([entry["board"]] if not hosted else [], site),
            attempted=attempted, web_backend=web_backend, explicit=explicit, budget=budget,
        )

    # --- Discovery: fetch the careers page and read the board off it (Tier 1). ---
    careers_html, careers_url, page_via = _find_careers_page(
        company, date_range, config, backend=web_backend, site=site,
    )
    provider, slug = (None, None)
    found_via = "slug-guess"
    probed: list[dict[str, Any]] | None = None
    # What the careers page calls its organization, when that is a longer or
    # shorter form of the company's name ("Anduril Industries" for "Anduril").
    # A page a web search named is anyone's page and is not read for this.
    stated_names = [
        name for name in _stated_names(careers_html or "")
        if _leads_with(name, company) and not _same_company(name, company)
    ] if page_via != "search" else []
    platform: tuple[str, dict[str, Any]] | None = None
    if careers_html:
        provider, slug = detect_ats(careers_html)
        if provider == PLATFORM_PROVIDER_KULA:
            # A Kula board is under the slug the company chose, on Kula's own
            # host, so a link from any careers page names it, as a hosted
            # system's does; its platform reader reads it, and whose board it
            # is follows from how the page was found or the name it states.
            platform = (provider, board_detect.kula_entry(slug or ""))
            provider = slug = None
        elif provider:
            attempted.append(f"careers:{provider}:{slug}")
            found_via = page_via
        elif page_via == "site":
            # A hiring platform under the company's own host, named by the
            # page: read by the platform's reader. Only on a site known to be
            # the company's: a domain guessed from the name can be another
            # business's, and a platform board states no company of its own
            # (its reader names it after the company asked for), so it would
            # pass as the company's. A page a web search named is not read
            # for one either.
            platform = board_detect.detect_platform(careers_html, careers_url or "")

    if platform is not None:
        platform_provider, entry = platform
        entry = {"name": company, **entry}
        attempted.append(f"careers:{platform_provider}:{entry['board']}")
        found, reason = _read_found_board(
            company, platform_provider, entry["board"], date_range,
            attempted=attempted, explicit=explicit, budget=budget,
            found_via=page_via, company_board=entry,
        )
        if found is not None:
            return found
        if reason == "unanswered":
            return _web_tier(
                company, date_range, config, attempted=attempted,
                web_backend=web_backend, explicit=explicit,
            )
        return _fall_through(
            company, date_range, config, {"name": company, "site": entry["board"]},
            reason, hosts=_sitemap_hosts([entry["board"], _host(careers_url or "")], site),
            attempted=attempted, web_backend=web_backend, explicit=explicit, budget=budget,
            platform=platform_provider,
        )

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
            named_boards=_boards_named(careers_html or ""),
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

    # --- The roster tier: the job sitemap of the company's own site. ---
    if site and _host(site):
        roster = _sitemap_tier(
            company, date_range, _sitemap_hosts([_host(careers_url or "")], site),
            attempted=attempted, explicit=explicit, budget=budget,
        )
        if roster is not None:
            return roster

    # --- Tier 3: generic web search (noisy, low-confidence). ---
    return _web_tier(
        company, date_range, config, attempted=attempted, web_backend=web_backend,
        explicit=explicit,
    )


# Providers ``_read_board`` reads by slug or token rather than through a
# company reader.
_HOSTED_PROVIDERS = frozenset({
    ATS_PROVIDER_GREENHOUSE, ATS_PROVIDER_ASHBY, ATS_PROVIDER_LEVER,
    ATS_PROVIDER_WORKABLE, ATS_PROVIDER_SMARTRECRUITERS, ATS_PROVIDER_WORKDAY,
})
ROBOTS_REASON = "its robots.txt disallows the job listing"


def _read_found_board(
    company: str,
    provider: str,
    board: str,
    date_range: tuple[str, str],
    *,
    attempted: list[str],
    explicit: bool,
    budget: LaneBudget,
    found_via: str,
    company_board: dict[str, Any] | None,
) -> tuple[tuple[list[dict[str, Any]], dict[str, Any]] | None, str]:
    """Read a board the registry or detection named; ``(found, reason)``.

    ``reason`` says why nothing was read: ``unanswered`` for a board that did
    not answer (a retry may read it), or the words the footer gives.
    """
    entry = company_board or {"board": board}
    listing = _listing_url(provider, entry)
    if listing and board_robots.disallowed(
        listing, deadline_monotonic=time.monotonic() + max(0.0, budget.remaining()),
    ):
        attempted.append(f"robots-disallowed:{provider}:{board}")
        log.source_log(
            "Jobs", f"{company}: the {provider} board {board} is not read: {ROBOTS_REASON} "
            f"({board_robots.RESPECT_ROBOTS_VAR} is set)", tty_only=False,
        )
        return None, ROBOTS_REASON
    found = _read_board(
        company, provider, board, date_range,
        attempted=attempted, explicit=explicit, budget=budget,
        found_via=found_via, company_board=company_board,
    )
    if found is not None:
        return found, ""
    if f"the {provider} board {board} did not answer" in budget.unanswered:
        return None, "unanswered"
    return None, f"its {provider} board answered with an error or with no roles the lane could read"


def _fall_through(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    entry: dict[str, str],
    reason: str,
    *,
    hosts: list[str],
    attempted: list[str],
    web_backend: str,
    explicit: bool,
    budget: LaneBudget,
    platform: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """A named board that could not be read: the site's job sitemap, then the
    web tier, and the run says which happened. Never a silent zero."""
    name = entry.get("name") or company
    roster = _sitemap_tier(
        company, date_range, hosts, attempted=attempted, explicit=explicit, budget=budget,
        fallback=f"{entry.get('provider') or platform or 'its'} board not read: {reason}",
    )
    if roster is not None:
        return roster
    return _job_site_unreadable(
        company, date_range, config, {**entry, "name": name}, reason,
        attempted=attempted, web_backend=web_backend, explicit=explicit,
    )


def _sitemap_hosts(first: list[str], site: str) -> list[str]:
    """Hosts asked for a job sitemap: the board's and careers page's own,
    then the official site's jobs and careers hosts, then the site itself."""
    hosts = [host for host in first if host]
    base = _host(site).removeprefix("www.") if site else ""
    if base:
        hosts += [f"jobs.{base}", f"careers.{base}", _host(site)]
    return list(dict.fromkeys(host.lower() for host in hosts))


def _sitemap_tier(
    company: str,
    date_range: tuple[str, str],
    hosts: list[str],
    *,
    attempted: list[str],
    explicit: bool,
    budget: LaneBudget,
    fallback: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """The roster tier (``lib.job_boards.sitemap``): roles listed in a job
    sitemap of the company's own site, as a board read titles only. None when
    no host has a job sitemap."""
    hosts = [host for host in hosts if host not in budget.unreachable]
    if not hosts or _out_of_time("the job sitemap"):
        return None
    sitemap = importlib.import_module(f"{__package__}.job_boards.sitemap")
    deadline = budget.deadline - budget.remaining() * DETAIL_SHARE
    attempted.append("sitemap:" + ",".join(hosts[:4]))
    def read() -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            return sitemap.read_roster(hosts, company=company, deadline=deadline)
        except (UnicodeDecodeError, ValueError):
            # A sitemap that is not text, or not XML a pattern can read.
            return [], {}

    items, info = _within_budget(read, "the job sitemap", ([], {}))
    total = int(info.get("board_total") or 0)
    if not total:
        return None
    host = str(info.get("board_sitemap_host") or "")
    artifact = _artifact("jobs", company, attempted, items, explicit,
                         tier="ats", provider=SITEMAP_PROVIDER, slug=host)
    artifact.update(info)
    artifact.update({
        "found_via": "site", "board_company": company, "owned": True,
        "titles_only": True,
        "open_roles": total, "open_roles_floor": bool(info.get("board_total_floor")),
        "new_in_window": None,
    })
    if fallback:
        artifact["board_fallback"] = fallback
    notes = []
    if fallback:
        notes.append(fallback)
    story_record = _local_story_record(items)
    if story_record is not None:
        artifact["story_search"] = story_record
    if items:
        eligible = [item for item in items if not item.get("digest_excluded")]
        selected = jobs_digest.select_roles(eligible, open_roles=total)
        artifact["digest"] = jobs_digest.build_digest(eligible, selected)
        artifact["digest"]["titles_only"] = True
        notes.append(
            f"titles only: the roles come from the job sitemap of {host}, "
            "whose role pages could not be read, so each role is its title and link"
            + (f" ({len(items):,} of {total:,} listed roles have a title)"
               if len(items) < total else "")
        )
        artifact["digest"]["note"] = "; ".join(notes)
    else:
        notes.append(
            f"{total:,} roles are listed in the job sitemap of {host}, "
            "but neither their addresses nor their pages give a title"
        )
    artifact["coverage_note"] = "; ".join(notes)
    log.source_log("Jobs", f"{company}: {artifact['coverage_note']}", tty_only=False)
    return items, artifact


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
    company_board: dict[str, str] | None = None,
    named_boards: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
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

    ``company_board`` is the registry entry of a company-run board, which is
    read by its own reader. Its listing stops early enough to leave
    ``DETAIL_SHARE`` of the time for the role text of the selected roles.

    A Workday board that is the company's is read with every other career
    site of its tenant: those its host's robots.txt lists and those of
    ``named_boards`` (the boards the careers page names) on the same tenant.
    """
    if full:
        budget.limit_discovery(None)
    board: dict[str, Any] = dict(probed_board or {})
    deadline = budget.deadline if full else min(budget.deadline, budget.step_deadline)
    list_deadline = deadline
    if company_board is not None and provider in _DETAIL_FETCHERS:
        list_deadline = deadline - budget.remaining() * DETAIL_SHARE
    # Passed only when set, so the plain call stays what it always was.
    part: dict[str, Any] = {} if full else {"first_page_only": True}
    tenant_sites: list[str] = []
    # Rows the board's own search found for the run's story terms, and what
    # that search did; asked before the newest-first read, on a board read
    # in full.
    story_rows: list[dict[str, Any]] = []
    story_record: dict[str, Any] | None = None
    try:
        if probed is not None:
            # The probe already read this board; it is not read twice.
            items = probed
        elif _out_of_time(f"reading the {provider} board {slug}"):
            items = []
        elif company_board is not None:
            if full:
                story_rows, story_record = _search_story(provider, [company_board])
                if story_record is not None and provider in _DETAIL_FETCHERS:
                    list_deadline = deadline - budget.remaining() * DETAIL_SHARE
            items, board = _company_reader(provider)(
                company_board, deadline=list_deadline, date_range=date_range,
            )
        elif provider == ATS_PROVIDER_WORKDAY:
            if full:
                # Asked before the listing pages: a tenant answers a burst of
                # them with 429 to every request for a while after.
                tenant_sites = workday_tenant_sites(
                    slug, named_boards,
                    deadline_monotonic=time.monotonic() + max(0.0, budget.remaining()),
                )
                # Every site of the tenant is one company's, under one cap.
                story_rows, story_record = _search_story(provider, [slug, *tenant_sites])
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
    except ValueError as exc:  # BoardShapeError, UnicodeDecodeError, bad JSON
        # A company reader's answer in a shape or encoding it cannot read:
        # the board was not read, and the run falls through, not over.
        if company_board is None:
            raise
        log.source_log(
            "Jobs", f"{company}: the {provider} board {slug} could not be read "
            f"({type(exc).__name__})", tty_only=False,
        )
        items = []
    if not items:
        if company_board is not None and board.get("board_unanswered"):
            budget.unanswered.append(f"the {provider} board {slug} did not answer")
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
    if owned and tenant_sites:
        # The tenant's other career sites are the company's too. They leave
        # the role text of the selected roles its share of the time.
        items, board = read_workday_tenant_sites(
            slug, items, board, tenant_sites,
            deadline=deadline - budget.remaining() * DETAIL_SHARE,
        )
    artifact = _artifact("jobs", company, attempted, items, explicit,
                         tier="ats", provider=provider, slug=slug)
    artifact.update(board)
    artifact.update({
        "found_via": found_via,
        "board_company": stated,
        "owned": owned,
    })
    extras: list[dict[str, Any]] = []
    if owned:
        if provider in _STORY_LOCAL:
            story_record = _local_story_record(items)
        elif story_record is not None:
            extras = _merge_story_rows(items, story_rows)
            story_record["matches"] = _story_matches([*items, *extras])
        elif _story_terms():
            # A board this lane has no story search for, or read in part.
            story_record = _story_record(ran=False, terms=[])
        if story_record is not None:
            artifact["story_search"] = story_record
        # Roles a board marks as kept out of the digest (Apple's retail store
        # roles) are counted on the board but never chosen.
        eligible = [item for item in items if not item.get("digest_excluded")]
        # Story matches the newest-first read did not reach may be chosen,
        # and are never counted as the board's roles.
        selected = jobs_digest.select_roles(
            eligible + [item for item in extras if not item.get("digest_excluded")],
            open_roles=_board_counts(items, board, date_range)["open_roles"],
        )
        late = failed = 0
        fetch_details = _detail_fetcher(provider)
        if fetch_details is not None:
            late, failed = fetch_details(
                [role["item"] for role in selected], deadline=deadline,
            )
        artifact["digest"] = jobs_digest.build_digest(eligible, selected)
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
    story_token = _STORY.set(_story_search_for(config))
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
        _STORY.reset(story_token)
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
    listed = int(board.get("board_total") or 0)
    if not (board.get("board_newest_first") or board.get("board_reports_total")):
        listed = min(listed, int(board.get("board_row_ceiling") or WORKDAY_ROW_CEILING))
    rows = int(board.get("board_rows_read") or len(items))
    read = f"{rows:,} rows read" + (
        f" of the {listed:,} the board lists" if listed > rows else ""
    )
    if board.get("board_newest_first") or board.get("board_reports_total"):
        cause = (
            "the newest roles are read first" if board.get("board_newest_first")
            else "the board is read in its own order, which is not by date"
        )
        if budget.spent():
            cause += f" and the lane's {budget.seconds:g}s time budget ran out"
        elif board.get("board_row_ceiling"):
            cause += f", up to {int(board['board_row_ceiling']):,} rows"
    elif provider == ATS_PROVIDER_WORKABLE:
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
    """Find and fetch the company's careers page HTML: ``(html, url)`` or
    ``(None, None)``. See ``_find_careers_page``."""
    html, url, _via = _find_careers_page(company, date_range, config, backend=backend, site=site)
    return html, url


def _find_careers_page(
    company: str,
    date_range: tuple[str, str],
    config: dict[str, Any],
    *,
    backend: str = "auto",
    site: str = "",
) -> tuple[str | None, str | None, str]:
    """Find and fetch the company's careers page HTML.

    Tries conventional URLs on the company's known site, or on guessed
    domains when no site is known (free, deterministic). On a known site it
    also tries the site's ``careers.`` and ``jobs.`` hosts, which is where a
    large company's careers site often answers when its main site blocks
    automated requests, and a careers page the registry names for it
    (``CAREERS_PAGES``). A page that names a board ends the search; otherwise
    the first careers page found is kept. Falls back to a single web search
    for the careers page when a backend is configured.

    Returns ``(html, url, via)``: ``url`` is the address that answered (after
    redirects), so a platform under the company's own host is called there,
    and ``via`` is how the page was reached (``site``, ``guess`` or
    ``search``, as ``_found_via`` says). Never raises.
    """
    # Only a host guessed from the company's name may turn out not to exist.
    guessed = not (site and _host(site))
    kept: tuple[str, str, str] | None = None
    # Hosts that gave no answer at all: their other page is not asked.
    dead: set[str] = set()
    budget = _BUDGET.get()
    for phase in _careers_candidates(company, site):
        # The site answered on its other host form: ask that one from now on.
        moved: dict[str, str] = {}
        for url, on_site, named in phase:
            host = _host(url)
            if host in moved:
                url = url.replace(f"//{host}", f"//{moved[host]}", 1)
                host = moved[host]
            if host in dead:
                continue
            if _out_of_time(f"the careers page {url}"):
                return kept or (None, None, "search")
            page, no_answer, used = _careers_page_at(
                url, guessed=guessed or not on_site, company=company, site=site, named=named,
            )
            if _host(used) != host:
                moved[host] = _host(used)
            if no_answer:
                dead.add(host)
                if budget is not None:
                    # Both host forms were asked for a known site.
                    budget.unreachable.update({host, f"www.{host.removeprefix('www.')}",
                                               host.removeprefix("www.")})
            if page is None:
                continue
            if _names_board(page[0], page[1]):
                return page
            if kept is None:
                kept = page
            # The site's first careers page ends this group of pages; a
            # later group is tried only while no page has named a board.
            break
    if kept is not None:
        return kept

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
                return html, careers_url, _found_via(careers_url, company, site)

    return None, None, "search"


def _careers_candidates(company: str, site: str) -> list[list[tuple[str, bool, bool]]]:
    """Careers page URLs to try, in groups, each with whether it is on the
    host the run knows or guessed, and whether the registry names it as the
    known site's careers page.

    The groups: a careers page the registry names (``CAREERS_PAGES``); the
    ``/careers`` and ``/jobs`` pages of the known site, or of each guessed
    domain; then, for a known site only, its ``careers.`` and ``jobs.`` hosts,
    which may not exist.
    """
    groups: list[list[tuple[str, bool, bool]]] = []
    known = bool(site and _host(site))
    if known:
        hinted = next(
            (page for key, page in CAREERS_PAGES.items() if _same_site(_host(site), key)), "",
        )
        if hinted:
            groups.append([(hinted, False, True)])
    for host in _site_hosts(company, site):
        registered = _registry_entry(host)
        if registered is not None and registered[0] == "unreadable":
            # A job site the lane does not read gets no request, on any path.
            continue
        groups.append([(f"https://{host}{path}", True, False) for path in ("/careers", "/jobs")])
    if known:
        base = _host(site).removeprefix("www.")
        groups.extend([(f"https://{label}.{base}/", False, False)] for label in ("careers", "jobs"))
    return groups


def _careers_page_at(
    url: str, *, guessed: bool, company: str, site: str, named: bool = False,
) -> tuple[tuple[str, str, str] | None, bool, str]:
    """The careers page at ``url`` and the page naming its board, if any,
    whether the host gave no answer at all, and the URL that was asked (the
    site's other host form when that is the one that answered).

    The page is ``(html, landed, via)``. A guessed URL (a domain guessed from
    the name, a ``careers.``/``jobs.`` host of the known site, a registry
    careers page) may not exist, so its failure is recorded nowhere. A page
    that redirected off the company's site to a host that is not a jobs or
    careers host (``microsoft.com/careers`` to a search engine) is not its
    careers page. A page the registry names for the known site (``named``)
    is the site's own, wherever it is hosted.
    """
    budget = _BUDGET.get()
    if guessed:
        html, no_answer = _careers_text(url, guessed=True)
    else:
        html, no_answer, url = _known_site_text(url)
    if not html:
        return None, no_answer, url
    landed = (budget.landed.get(url) if budget is not None else None) or url
    if not (_same_site(_host(landed), _host(url)) or _jobs_host(_host(landed)) or named):
        return None, False, url
    if not (_looks_like_careers_html(html) or _names_board(html, landed)):
        return None, False, url
    page, page_url = _board_page(html, landed, guessed=guessed)
    return (page, page_url, "site" if named else _found_via(url, company, site)), False, url


def _names_board(html: str, url: str) -> bool:
    """Whether a page names a board the lane reads: a hosted system's link or
    a hiring platform's markup."""
    return bool(detect_ats(html)[0] or board_detect.detect_platform(html, url))


def _jobs_host(host: str) -> bool:
    """A host whose name says it serves jobs (``jobs.``, ``careers.``,
    ``apply.careers.``, ``explore.jobs.``)."""
    return bool(re.search(r"(?:^|\.)(?:jobs|careers?)\.", host or ""))


def _known_site_text(url: str) -> tuple[str | None, bool, str]:
    """A page on the company's known site, retried once on the site's other
    host form (``www.`` or bare) when the connection was dropped. Returns the
    page text, whether the host gave no answer (counting both forms dropping
    the connection), and the URL that was read."""
    answers: dict[str, tuple[str | None, bool]] = {}

    def fetch(page: str) -> str | None:
        with http.tee_failures() as failures:
            text, gone = _careers_text(page, guessed=False)
        answers[page] = (text, gone or http.connection_dropped(failures))
        return text

    html, used = http.text_on_either_host_form(fetch, url)
    if html is None:
        # A form that answered, even with a 403 or 404, is where the site's
        # other page is asked.
        used = next((page for page, (_text, gone) in answers.items() if not gone), used)
    # Only when every form tried gave no answer is the other page not asked.
    return html, all(gone for _text, gone in answers.values()), used


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
    landed: list[str] = []
    budget = _BUDGET.get()
    with http.expected_misses(403, 404):
        if guessed:
            # ``capture_failures`` keeps a guessed host's failures out of the
            # run's record, whatever they are.
            with http.capture_failures() as failures:
                html = http.get_text(
                    url, accept="text/html", retries=1, retry_dns=False, landed=landed,
                    **_bounds(GUESS_TIMEOUT_SECONDS),
                )
            if any(failure.outcome_state == health.TIMEOUT for failure in failures):
                _note_slow(f"{host} did not answer in {GUESS_TIMEOUT_SECONDS}s")
            if landed and budget is not None:
                budget.landed[url] = landed[-1]
            return html, any(failure.status_code is None for failure in failures)
        with http.tee_failures() as failures:
            html = http.get_text(
                url, accept="text/html", retries=1, landed=landed, **_bounds(PAGE_TIMEOUT_SECONDS),
            )
    if landed and budget is not None:
        budget.landed[url] = landed[-1]
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
    page it links to.

    A large company's careers page is often a landing page whose job search
    lives one link away, on a host of its own (``jobs.nvidia.com``), and only
    that page names the hiring system. Up to ``_LINKED_PAGE_LIMIT`` such links
    are followed: within the careers page's own site, or to a jobs host that
    carries the site's name (``explore.jobs.netflix.net`` from
    ``jobs.netflix.com``), so a board found this way is named by the page
    the company's site led to. An Eightfold board the page points at by name
    (``careers-eightFoldCareerUrl``) is followed first.

    Returns the page and the address that answered with it.
    """
    if _names_board(html, url):
        return html, url
    budget = _BUDGET.get()
    pointed = board_detect.eightfold_career_url(html)
    links = _jobs_links(html, url)
    if pointed and (_same_site(_host(pointed), _host(url)) or _named_jobs_host(_host(pointed), url)):
        links = [pointed, *(link for link in links if link != pointed)]
    for link in links[:_LINKED_PAGE_LIMIT]:
        if _out_of_time(f"the jobs page {link}"):
            break
        linked, _no_answer = _careers_text(link, guessed=guessed)
        landed = (budget.landed.get(link) if budget is not None else None) or link
        if linked and _names_board(linked, landed):
            return linked, landed
    return html, url


def _named_jobs_host(host: str, page_url: str) -> bool:
    """A jobs host on another domain that carries the page's site name:
    ``explore.jobs.netflix.net`` for a page on ``jobs.netflix.com``."""
    page_host = _host(page_url).removeprefix("www.")
    parts = page_host.split(".")
    name = parts[-2] if len(parts) >= 2 else ""
    return bool(name) and len(name) >= 3 and _jobs_host(host) and name in host.split(".")


def _jobs_links(html: str, page_url: str) -> list[str]:
    """Links on a careers page to a jobs page, best first: a jobs or careers
    host of its own site, then a path of its own host that reads like a job
    search, then a jobs host on another domain that carries the site's name
    (``_named_jobs_host``). A link to the page itself or to another page of
    the same kind (a translation of it, "life at") is left out."""
    page = urlparse(page_url)
    ranked: list[tuple[int, str]] = []
    for href in re.findall(r"""href\s*=\s*["']([^"'#\s]+)""", html, flags=re.I):
        link = urljoin(page_url, unescape(href))
        parsed = urlparse(link)
        host = parsed.netloc.lower()
        if parsed.scheme not in ("http", "https"):
            continue
        if not _same_site(host, page.netloc):
            if not _named_jobs_host(host, page_url) or _FILE_LINK.search(parsed.path):
                continue
            rank = 2
        elif _FILE_LINK.search(parsed.path):
            continue
        elif host != page.netloc.lower() and _jobs_host(host):
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
    otherwise it is None. A board read newest first (``board_newest_first``)
    reports its own total instead, see below.
    """
    from_date, to_date = date_range
    if board.get("board_newest_first") or board.get("board_reports_total"):
        # A board that reports its own total, read newest first or in its own
        # order up to a ceiling: the open-role count is the total it reports,
        # and the roles new in the window are counted only when the read
        # covered the window (went past its oldest day, or read the board).
        covered = bool(board.get("board_window_covered"))
        return {
            "open_roles": max(int(board.get("board_total") or 0), len(items)),
            "open_roles_floor": bool(board.get("board_total_floor")),
            "new_in_window": (
                sum(
                    1 for item in items
                    if item.get("posted_date_true") and item.get("date")
                    and from_date <= str(item["date"]) <= to_date
                )
                if covered else None
            ),
        }
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


def _workday_body(offset: int, search_text: str = "") -> dict[str, Any]:
    return {
        "appliedFacets": {},
        "limit": WORKDAY_PAGE_SIZE,
        "offset": offset,
        "searchText": search_text,
    }


def search_workday_story(
    token: str, term: str, *, deadline_monotonic: float,
) -> list[dict[str, Any]]:
    """The roles a Workday site's own search finds for a story term."""
    url = workday_list_url(token)
    if not url or board_robots.disallowed(url, deadline_monotonic=deadline_monotonic):
        return []
    payload = http.post(
        url, json_data=_workday_body(0, term), headers={"Accept": "application/json"},
        timeout=STORY_TIMEOUT_SECONDS, retries=1, deadline_monotonic=deadline_monotonic,
    )
    return parse_workday_response(payload, token) if isinstance(payload, dict) else []


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
            json_data=_workday_body(offset),
            headers={"Accept": "application/json"},
            timeout=API_TIMEOUT_SECONDS,
            retries=2,
            deadline_monotonic=http_deadline,
        )

    # The total is reported on the first page only, so read that one first.
    first = _workday_run([page(0)], deadline, clock)[0]
    if not isinstance(first, dict) or not isinstance(first.get("jobPostings"), list):
        # Not a listing: a site name the tenant does not have answers an
        # error record, which is not paged through.
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


# Paths a Workday host's robots.txt lists that are not career sites.
_WORKDAY_NON_SITES = {"refreshfacet", "wday"}


def workday_tenant_sites(
    token: str,
    named: set[tuple[str, str]] | frozenset[tuple[str, str]] = frozenset(),
    *,
    deadline_monotonic: float | None = None,
) -> list[str]:
    """The other career sites of a Workday board's tenant, as board tokens.

    A tenant can run several sites (Nike: ``nke``, ``nke2``, ``nke4``). They
    are the first path segment of every ``Allow``, ``Disallow`` and
    ``Sitemap`` line of the tenant host's robots.txt, and the Workday boards
    a careers page names (``named``, as ``_boards_named`` gives them) on the
    same tenant. A site on another tenant is another company's and is left
    out. Candidates are not checked here: a name that is not a site answers
    its listing call with something other than a listing.
    """
    parts = _workday_parts(token)
    if not parts:
        return []
    tenant_host, site = token.split("/", 1)
    tenant = tenant_host.split(".", 1)[0].lower()
    found: dict[str, str] = {site.lower(): token}
    for provider, slug in sorted(named):
        if provider == ATS_PROVIDER_WORKDAY and slug.split(".", 1)[0] == tenant:
            # ``_boards_named`` lower-cases its slugs; Workday site names are
            # not case sensitive.
            found.setdefault(slug.split("/", 1)[1], slug)
    rules = board_robots.rules_for(
        urlparse(parts[0]).hostname or "", deadline_monotonic=deadline_monotonic,
    )
    for path in [*rules.paths, *(urlparse(url).path for url in rules.sitemaps)]:
        first = path.strip("/").split("/", 1)[0]
        if re.fullmatch(r"[A-Za-z0-9_-]+", first) and first.lower() not in _WORKDAY_NON_SITES:
            found.setdefault(first.lower(), f"{tenant_host}/{first}")
    found.pop(site.lower())
    return list(found.values())


def _workday_job_key(item: dict[str, Any]) -> str:
    """A Workday role's ``externalPath``, which is the same on every site of
    the tenant that lists the role."""
    token = str(item.get("board_token") or "")
    url = str(item.get("url") or "")
    parts = _workday_parts(token)
    prefix = f"{parts[0]}/{token.split('/', 1)[1]}" if parts else ""
    return (url[len(prefix):] if prefix and url.startswith(prefix) else url).lower()


def read_workday_tenant_sites(
    token: str,
    items: list[dict[str, Any]],
    info: dict[str, Any],
    others: list[str],
    *,
    deadline: float,
    clock: Callable[[], float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Add the tenant's other career sites to a Workday board already read.

    ``items`` and ``info`` are what ``search_workday_board`` gave for
    ``token``; ``others`` are the sites ``workday_tenant_sites`` found. Each
    is read the same way, up to its own row ceiling, until
    ``COMPANY_BOARD_ROW_CEILING`` rows are held across sites. A role listed
    on two sites counts once. When a second site gives rows, ``board_sites``
    holds each site's role count, and the board's total, cap and cut-short
    flags cover every site read.
    """
    clock = clock or _clock()
    merged = list(items)
    seen = {_workday_job_key(item) for item in merged}
    sites = [{"site": token.split("/", 1)[1], "roles": len(items)}]
    totals = [min(int(info.get("board_total") or 0), WORKDAY_ROW_CEILING)]
    capped, cut_short = bool(info.get("board_capped")), bool(info.get("board_cut_short"))
    for other in others:
        if len(merged) >= COMPANY_BOARD_ROW_CEILING:
            capped = True
            break
        listing = workday_list_url(other)
        if board_robots.disallowed(
            listing, deadline_monotonic=time.monotonic() + max(0.0, deadline - clock()),
        ):
            continue
        rows, read = search_workday_board(other, deadline=deadline, clock=clock)
        for row in rows:
            key = _workday_job_key(row)
            if key not in seen:
                seen.add(key)
                merged.append(row)
        if not rows:
            continue
        sites.append({"site": other.split("/", 1)[1], "roles": len(rows)})
        totals.append(min(int(read.get("board_total") or 0), WORKDAY_ROW_CEILING))
        capped = capped or bool(read.get("board_capped"))
        cut_short = cut_short or bool(read.get("board_cut_short"))
    if len(sites) < 2:
        return items, info
    if len(merged) > COMPANY_BOARD_ROW_CEILING:
        capped = True
    log.source_log(
        "Jobs",
        "Workday tenant sites read: "
        + ", ".join(f"{site['site']} ({site['roles']:,})" for site in sites)
        + f"; {min(len(merged), COMPANY_BOARD_ROW_CEILING):,} distinct roles",
        tty_only=False,
    )
    return merged[:COMPANY_BOARD_ROW_CEILING], {
        **info,
        "board_total": min(sum(totals), COMPANY_BOARD_ROW_CEILING),
        "board_capped": capped,
        "board_cut_short": cut_short,
        "board_row_ceiling": COMPANY_BOARD_ROW_CEILING,
        "board_sites": sites,
    }


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


def role_page_text(
    url: str, *, timeout: float, deadline_monotonic: float, landed: list | None = None,
    redirect_guard: Callable[[str], bool] | None = None,
) -> str | None:
    """A role or sitemap page as text, or None when it is not text the lane
    can decode (a gzip sitemap, a page in another encoding): one such page
    costs its own role, not the whole read."""
    try:
        return http.get_text(
            url, accept="text/html,application/xml,*/*", timeout=timeout, retries=1,
            deadline_monotonic=deadline_monotonic, landed=landed,
            **({"redirect_guard": redirect_guard} if redirect_guard is not None else {}),
        )
    except UnicodeDecodeError:
        return None


def jsonld_role_text(html: Any) -> tuple[str, str] | None:
    """The text of the first ``JobPosting`` a role's page carries, with its
    breaks kept and as one line, or None when the page has none."""
    if not isinstance(html, str):
        return None
    for block in _JSONLD_RE.findall(html):
        for obj in _walk_jsonld(block):
            if isinstance(obj, dict) and _is_job_posting(obj):
                text = str(obj.get("description") or "")
                if text.strip():
                    text = unescape(text)
                    return _block_text(text), _clean_html(text)
    return None


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
        COMPANY_PROVIDER_APPLE: "AP",
        COMPANY_PROVIDER_AMAZON: "AZ",
        COMPANY_PROVIDER_EIGHTFOLD: "EF",
        COMPANY_PROVIDER_GOOGLE: "GG",
        COMPANY_PROVIDER_IBM: "IB",
        PLATFORM_PROVIDER_ORACLE: "OR",
        PLATFORM_PROVIDER_JIBE: "JB",
        PLATFORM_PROVIDER_PHENOM: "PH",
        PLATFORM_PROVIDER_TALENTBREW: "TB",
        PLATFORM_PROVIDER_KULA: "KL",
        SITEMAP_PROVIDER: "SM",
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
    if any(token in lowered for token in (
        "ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "smartrecruiters.com",
        "myworkdayjobs.com", "oraclecloud.com/hcmui", "/hcmui/candidateexperience",
        "phenompeople", "tbcdn.talentbrew.com", "jibecdn.com", "eightfold",
    )):
        return True
    return bool(re.search(r"\b(open roles|open positions|join (our|the) team|current openings|jobposting)\b", lowered))


class BoardShapeError(ValueError):
    """A company board answered, but not in the shape its reader expects."""


def _read_newest_first(
    page: Callable[[int], Callable[[float], Any]],
    parse: Callable[[Any], tuple[list[dict[str, Any]], int]],
    *,
    page_size: int,
    row_ceiling: int,
    window_start: str | None,
    deadline: float,
    workers: int = COMPANY_BOARD_MAX_WORKERS,
    newest_first: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a board's pages, newest first, ``workers`` at a time.

    ``page(offset)`` makes one listing call and ``parse(payload)`` returns its
    rows and the total the board reports, raising ``BoardShapeError`` for an
    answer it cannot read. The first page gives the total; every page after
    it up to ``row_ceiling`` is then asked, a bounded number at once, so a
    board of a few thousand rows is read whole inside the budget. Reading
    stops at the board's end, at the ceiling, at the deadline, or at a page
    that fails or comes back empty: no page after a failed one is asked, and
    the rows read before it are kept in board order.

    A first page that fails or cannot be read, or that is empty while the
    board reports roles, returns no rows: the board cannot be read.

    ``newest_first`` off is for a board that cannot be sorted by date: its
    rows are the whole board or a part of it, never the newest ones.
    """
    clock = _clock()
    info: dict[str, Any] = {
        "board_total": 0, "board_capped": False, "board_cut_short": False,
        "board_newest_first": newest_first, "board_window_covered": False,
        "board_row_ceiling": row_ceiling, "board_reports_total": True,
    }
    failures: list[http.HTTPError] = []

    def first_page(http_deadline: float) -> Any:
        try:
            return page(0)(http_deadline)
        except http.HTTPError as exc:
            failures.append(exc)
            raise

    first = _workday_run([first_page], deadline, clock)[0]
    if first is None or first is _OUT_OF_TIME:
        # No answer, an overload or a rate limit is not the board refusing
        # to be read: the run says it did not answer, and a retry may.
        status = failures[0].status_code if failures else None
        info["board_unanswered"] = first is _OUT_OF_TIME or status is None or status == 429 or status >= 500
        return [], info
    try:
        items, total = parse(first)
    except BoardShapeError:
        return [], info
    info["board_total"] = max(total, len(items))
    if not items:
        return [], info
    limit = min(info["board_total"], row_ceiling)
    calls = [page(offset) for offset in range(page_size, limit, page_size)]
    rest = _paged_run(calls, parse, deadline, clock, workers=workers)
    pages = [list(items), *rest]
    for rows in rest:
        items.extend(rows)
    # Every page up to the ceiling answered: a board that lost a role or two
    # while it was read is still read whole.
    every_page = len(rest) == len(calls)
    # Newest first, a row older than the window means the window was read
    # whole; in any other order only the whole board covers it.
    # Only rows reached through full pages count: a page that served fewer
    # rows than asked for leaves a gap the offsets skip, and a role posted in
    # the window may sit in it.
    older = False
    if newest_first and window_start is not None:
        for rows in pages:
            if any(str(row.get("date") or "") < window_start for row in rows if row.get("date")):
                older = True
                break
            if len(rows) < page_size:
                break
    # A board that serves fewer rows a page than asked for (a server-side cap)
    # answers every page and still leaves rows unread: whole only when the
    # rows read come to the total, give or take the drift of a live board.
    drift = max(2, info["board_total"] // 100)
    whole = (
        every_page and info["board_total"] <= row_ceiling
        and len(items) >= info["board_total"] - drift
    )
    info["board_window_covered"] = older or whole or len(items) >= info["board_total"]
    info["board_cut_short"] = not whole and len(items) < info["board_total"]
    info["board_capped"] = info["board_total"] > row_ceiling
    return items[:row_ceiling], info


_STOPPED = object()


def _paged_run(
    calls: list[Callable[[float], Any]],
    parse: Callable[[Any], tuple[list[dict[str, Any]], int]],
    deadline: float,
    clock: Callable[[], float],
    *,
    workers: int,
) -> list[list[dict[str, Any]]]:
    """Run listing calls at most ``workers`` at once and return the rows of
    each page in order, up to the first page that failed, could not be read,
    came back empty or would start after the deadline. Once one has, no later
    call that has not started yet is made; an earlier one still is, so the
    pages before the failed one are all read."""
    lock = threading.Lock()
    first_bad = [len(calls)]

    def mark(index: int) -> None:
        with lock:
            first_bad[0] = min(first_bad[0], index)

    def run(index: int, call: Callable[[float], Any]) -> Any:
        if index > first_bad[0]:
            return _STOPPED
        remaining = deadline - clock()
        if remaining <= 0:
            mark(index)
            return _OUT_OF_TIME
        try:
            payload = call(time.monotonic() + min(remaining, _request_seconds(API_TIMEOUT_SECONDS, 2)))
        except http.HTTPError:
            mark(index)
            return None
        try:
            rows = parse(payload)[0] if payload is not None else []
        except BoardShapeError:
            rows = []
        if not rows:
            mark(index)
        return rows

    if not calls:
        return []
    pages: list[list[dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(calls)))) as executor:
        futures = [
            http.submit_with_context(executor, run, index, call)
            for index, call in enumerate(calls)
        ]
        for future in futures:
            rows = future.result()
            if not isinstance(rows, list) or not rows:
                break
            pages.append(rows)
    return pages


# Amazon: one search endpoint, 100 roles per call with their full text. Errors
# arrive as HTTP 200 with an ``error`` key.


def parse_amazon_response(payload: Any) -> tuple[list[dict[str, Any]], int]:
    """Rows and reported hits from one ``amazon.jobs`` search page."""
    if not isinstance(payload, dict):
        raise BoardShapeError("not a JSON object")
    if payload.get("error"):
        raise BoardShapeError(str(payload["error"])[:120])
    jobs_list = payload.get("jobs")
    if not isinstance(jobs_list, list):
        raise BoardShapeError("no jobs list")
    items: list[dict[str, Any]] = []
    for job in jobs_list:
        if not isinstance(job, dict) or not job.get("title") or not job.get("job_path"):
            continue
        text = "<br/>".join(
            str(job.get(key) or "")
            for key in ("description", "basic_qualifications", "preferred_qualifications")
            if job.get(key)
        )
        posted = _amazon_date(job.get("posted_date"))
        items.append(_ats_item(
            provider=COMPANY_PROVIDER_AMAZON,
            slug="www.amazon.jobs",
            ident=str(job.get("id_icims") or job.get("id") or ""),
            title=str(job["title"]).strip(),
            url=f"https://www.amazon.jobs{job['job_path']}",
            description=_clean_html(text),
            description_blocks=_block_text(text),
            date=posted,
            posted_date_true=bool(posted),
            department=str(job.get("business_category") or "").strip(),
            location=str(job.get("normalized_location") or job.get("location") or "").strip(),
            company_name=str(job.get("company_name") or "").strip(),
            whole_posting=True,
        ))
    return items, _reported_total(payload, "hits")


def _amazon_date(value: Any) -> str | None:
    """``November 14, 2026`` as ``2026-11-14``."""
    try:
        return time.strftime("%Y-%m-%d", time.strptime(str(value or "").strip(), "%B %d, %Y"))
    except ValueError:
        return None


def _amazon_url(entry: dict[str, Any]) -> str:
    return f"https://{entry.get('board') or 'www.amazon.jobs'}/en/search.json"


def _amazon_params(offset: int, base_query: str = "") -> dict[str, str]:
    params = {"sort": "recent", "result_limit": str(AMAZON_PAGE_SIZE), "offset": str(offset)}
    if base_query:
        params["base_query"] = base_query
    return params


def search_amazon_story(
    entry: dict[str, Any], term: str, *, deadline_monotonic: float,
) -> list[dict[str, Any]]:
    """The roles Amazon's search finds for a story term (``base_query``)."""
    payload = http.get(
        _amazon_url(entry), params=_amazon_params(0, term),
        timeout=STORY_TIMEOUT_SECONDS, retries=1, deadline_monotonic=deadline_monotonic,
    )
    return parse_amazon_response(payload)[0]


def read_amazon_board(
    entry: dict[str, str], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Amazon's roles, newest first, with the board's reported total."""
    url = _amazon_url(entry)

    def page(offset: int) -> Callable[[float], Any]:
        params = _amazon_params(offset)
        return lambda http_deadline: http.get(
            url, params=params, timeout=API_TIMEOUT_SECONDS, retries=2,
            deadline_monotonic=http_deadline,
        )

    items, info = _read_newest_first(
        page, parse_amazon_response, page_size=AMAZON_PAGE_SIZE,
        row_ceiling=COMPANY_BOARD_ROW_CEILING, window_start=date_range[0], deadline=deadline,
    )
    # The search stops counting at 10,000, so that total is a floor.
    info["board_total_floor"] = info["board_total"] >= AMAZON_HITS_CAP
    return items, info


# Apple: a JSON search API, 20 rows per call with a short summary, one row per
# role and location, and a JSON details call per role with the full text.

_APPLE_SEARCH_URL = "https://jobs.apple.com/api/v1/search"
_APPLE_DETAILS_URL = "https://jobs.apple.com/api/v1/jobDetails/{position}?locale=en-us"


def _apple_search_body(page: int, query: str = "") -> dict[str, Any]:
    # Without the ``format`` key the API answers 200 with no results.
    return {
        "query": query,
        "filters": {"range": {"standardWeeklyHours": {"start": None, "end": None}}},
        "page": page,
        "locale": "en-us",
        "sort": "newest",
        "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
    }


def parse_apple_response(payload: Any) -> tuple[list[dict[str, Any]], int]:
    """Rows and the reported total from one Apple search page.

    A pipeline role (a standing requisition, flagged as managed or typed
    ``PIPE``) carries the day of the request as its posting date, so its date
    is not a true posted date. Managed pipeline roles and retail store roles
    (by team code or team name) are marked ``digest_excluded``.
    """
    res = payload.get("res") if isinstance(payload, dict) else None
    results = res.get("searchResults") if isinstance(res, dict) else None
    if not isinstance(results, list) or "totalRecords" not in res:
        raise BoardShapeError("no searchResults or totalRecords")
    items: list[dict[str, Any]] = []
    for job in results:
        if not isinstance(job, dict) or not job.get("postingTitle") or not job.get("positionId"):
            continue
        team = job.get("team") if isinstance(job.get("team"), dict) else {}
        locations = [loc for loc in job.get("locations") or [] if isinstance(loc, dict)]
        slug = str(job.get("transformedPostingTitle") or "role").strip()
        # A standing requisition: flagged as managed, or typed ``PIPE``.
        pipeline = bool(job.get("managedPipelineRole")) or job.get("type") == "PIPE"
        summary = str(job.get("jobSummary") or "").strip()
        item = _ats_item(
            provider=COMPANY_PROVIDER_APPLE,
            slug="jobs.apple.com",
            ident=str(job.get("id") or job["positionId"]),
            title=str(job["postingTitle"]).strip(),
            url=f"https://jobs.apple.com/en-us/details/{job['positionId']}/{slug}",
            description=summary,
            description_blocks=summary,
            date=_date_part(job.get("postDateInGMT")),
            posted_date_true=not pipeline and bool(_date_part(job.get("postDateInGMT"))),
            department=str(team.get("teamName") or "").strip(),
            location=str(locations[0].get("name") or "").strip() if locations else "",
            company_name="Apple",
            whole_posting=True,
        )
        retail = (
            team.get("teamCode") == APPLE_RETAIL_TEAM
            or str(team.get("teamName") or "").strip().lower() == "apple retail"
        )
        item["digest_excluded"] = bool(job.get("managedPipelineRole")) or retail
        item["detail_url"] = _APPLE_DETAILS_URL.format(position=job["positionId"])
        items.append(item)
    return items, _reported_total(res, "totalRecords")


def read_apple_board(
    entry: dict[str, str], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apple's roles, newest first, with the board's reported total."""

    def page(offset: int) -> Callable[[float], Any]:
        body = _apple_search_body(offset // APPLE_PAGE_SIZE + 1)
        return lambda http_deadline: http.post(
            _APPLE_SEARCH_URL, body, headers={"Accept": "application/json"},
            timeout=API_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
        )

    items, info = _read_newest_first(
        page, parse_apple_response, page_size=APPLE_PAGE_SIZE,
        row_ceiling=COMPANY_BOARD_ROW_CEILING, window_start=date_range[0], deadline=deadline,
    )
    # The board's total counts rows, so the rows read are counted the same way.
    info["board_rows_read"] = len(items)
    return _one_row_per_role(items), info


def search_apple_story(
    entry: dict[str, Any], term: str, *, deadline_monotonic: float,
) -> list[dict[str, Any]]:
    """The roles Apple's search finds for a story term, one row per role."""
    payload = http.post(
        _APPLE_SEARCH_URL, _apple_search_body(1, term), headers={"Accept": "application/json"},
        timeout=STORY_TIMEOUT_SECONDS, retries=1, deadline_monotonic=deadline_monotonic,
    )
    return _one_row_per_role(parse_apple_response(payload)[0])


def _one_row_per_role(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apple lists a role once per location; keep one row with every location."""
    roles: dict[str, dict[str, Any]] = {}
    for item in items:
        kept = roles.setdefault(str(item["url"]), item)
        if kept is not item:
            kept["offices"].extend(
                office for office in item["offices"] if office not in kept["offices"]
            )
    for role in roles.values():
        # The digest names a role's places from ``locations`` when a row has it.
        role["locations"] = list(role["offices"])
    return list(roles.values())


def parse_apple_detail(payload: Any) -> dict[str, str] | None:
    """The text fields of one Apple details answer, or None when it holds none."""
    role = payload.get("res") if isinstance(payload, dict) else None
    if not isinstance(role, dict):
        return None
    found = {
        key: str(role.get(key) or "").strip()
        for key in ("description", "responsibilities", "minimumQualifications", "preferredQualifications")
    }
    return found if any(found.values()) else None


def fetch_apple_details(
    items: list[dict[str, Any]], *, deadline: float,
) -> tuple[int, int]:
    """Fill the full text of the given Apple roles from their details calls.

    Returns the counts left unfetched: those the deadline stopped before their
    call started, and those whose call failed or held no role text. A role
    left unfetched keeps its search summary.
    """
    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        return lambda http_deadline: http.get(
            str(item["detail_url"]), headers={"Accept": "application/json"},
            timeout=APPLE_DETAIL_TIMEOUT_SECONDS, retries=2, deadline_monotonic=http_deadline,
        )

    def text_of(payload: Any) -> tuple[str, str] | None:
        detail = parse_apple_detail(payload)
        if detail is None:
            return None
        blocks = [detail["description"]]
        for label, key in (
            ("Responsibilities", "responsibilities"),
            ("Minimum Qualifications", "minimumQualifications"),
            ("Preferred Qualifications", "preferredQualifications"),
        ):
            if detail[key]:
                blocks.append(f"{label}\n{detail[key]}")
        text = "\n".join(block for block in blocks if block)
        return text, " ".join(text.split())

    return _fill_role_text(items, call, text_of, deadline=deadline)


def _fill_role_text(
    items: list[dict[str, Any]],
    call: Callable[[dict[str, Any]], Callable[[float], Any]],
    text_of: Callable[[Any], tuple[str, str] | None],
    *,
    deadline: float,
) -> tuple[int, int]:
    """Make one detail call per role and store the text ``text_of`` reads.

    ``text_of(payload)`` returns the role's text with its breaks kept and as
    one line, or None when the answer holds none. Returns the counts left
    unfetched (late, failed).
    """
    late = failed = 0
    for item, payload in zip(items, _workday_run([call(item) for item in items], deadline, _clock())):
        if payload is _OUT_OF_TIME:
            late += 1
            continue
        text = text_of(payload)
        if text is None:
            failed += 1
            continue
        item["description_blocks"], item["description"] = text
    return late, failed


# Eightfold: hosted boards with two API generations, and a tenant has one of
# them (``api`` in the registry entry). Both list 10 roles per call with no
# text, and give a role's text through one more call. Neither date a row
# carries is shown to be the day the role was first posted, so no row claims
# one and the board prints no count of roles new in the window.

def parse_eightfold_response(
    payload: Any, entry: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Rows and the reported total from one Eightfold listing page."""
    pcsx = entry.get("api") == "pcsx"
    body = payload.get("data") if pcsx and isinstance(payload, dict) else payload
    positions = body.get("positions") if isinstance(body, dict) else None
    if not isinstance(positions, list):
        raise BoardShapeError("no positions list")
    board, domain = entry["board"], entry["domain"]
    items: list[dict[str, Any]] = []
    for job in positions:
        if not isinstance(job, dict) or not job.get("id") or not job.get("name"):
            continue
        ident = str(job["id"])
        if pcsx:
            url = f"https://{board}{job.get('positionUrl') or f'/careers/job/{ident}'}"
            detail = f"https://{board}/api/pcsx/position_details?position_id={ident}&domain={domain}&hl=en"
        else:
            url = str(job.get("canonicalPositionUrl") or f"https://{board}/careers/job/{ident}")
            detail = f"https://{board}/api/apply/v2/jobs/{ident}?domain={domain}"
        locations = [str(loc).strip() for loc in job.get("locations") or [] if loc]
        item = _ats_item(
            provider=COMPANY_PROVIDER_EIGHTFOLD,
            slug=board,
            ident=ident,
            title=str(job["name"]).strip(),
            url=url,
            description="",
            date=None,
            department=str(job.get("department") or "").strip(),
            location=locations[0] if locations else str(job.get("location") or "").strip(),
            company_name=entry.get("name", ""),
            whole_posting=True,
        )
        item["detail_url"] = detail
        items.append(item)
    return items, _reported_total(body, "count")


def read_eightfold_board(
    entry: dict[str, str], *, deadline: float, date_range: tuple[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """An Eightfold board's newest roles, up to the row ceiling, with its total.

    A tenant serves one of two API generations. A detected board does not say
    which, so ``pcsx`` is asked first and ``v2`` when ``pcsx`` gives no rows;
    an entry that names its ``api`` is asked on that one alone. A 403 on every
    generation asked reads as no rows: the board cannot be read.
    """
    if entry.get("api"):
        return _read_eightfold_generation(entry, deadline=deadline)
    items, info = _read_eightfold_generation({**entry, "api": "pcsx"}, deadline=deadline)
    if items or info.get("board_unanswered"):
        return items, info
    # The robots check before the read covered the first generation's
    # listing; the second has its own path, checked before it is asked.
    if board_robots.disallowed(
        _eightfold_listing_url(entry["board"], "v2"),
        deadline_monotonic=time.monotonic() + max(0.0, deadline - _clock()()),
    ):
        return items, info
    return _read_eightfold_generation({**entry, "api": "v2"}, deadline=deadline)


def _eightfold_listing_url(board: str, api: str) -> str:
    """The listing call of an Eightfold API generation."""
    return f"https://{board}/api/pcsx/search" if api == "pcsx" else f"https://{board}/api/apply/v2/jobs"


def _read_eightfold_generation(
    entry: dict[str, str], *, deadline: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = _eightfold_listing_url(entry["board"], str(entry.get("api") or ""))

    def page(offset: int) -> Callable[[float], Any]:
        params = _eightfold_params(entry, offset)
        return lambda http_deadline: http.get(
            url, params=params, timeout=API_TIMEOUT_SECONDS, retries=2,
            deadline_monotonic=http_deadline,
        )

    items, info = _read_newest_first(
        page, lambda payload: parse_eightfold_response(payload, entry),
        page_size=EIGHTFOLD_PAGE_SIZE, row_ceiling=EIGHTFOLD_ROW_CEILING,
        window_start=None, deadline=deadline,
    )
    # No row carries a true posted date, so the window is never counted.
    info["board_window_covered"] = False
    return items, info


def _eightfold_params(entry: dict[str, Any], offset: int, query: str = "") -> dict[str, str]:
    domain = entry["domain"]
    if entry.get("api") == "pcsx":
        params = {"domain": domain, "query": query, "location": "", "sort_by": "timestamp"}
    else:
        params = {"domain": domain, "num": str(EIGHTFOLD_PAGE_SIZE), "sort_by": "timestamp"}
        if query:
            params["query"] = query
    return {**params, "start": str(offset)}


def search_eightfold_story(
    entry: dict[str, Any], term: str, *, deadline_monotonic: float,
) -> list[dict[str, Any]]:
    """The roles an Eightfold board's search finds for a story term, on the
    API generation the entry names. An entry that names none is asked on
    ``pcsx``, then on ``v2`` when ``pcsx`` refuses (403 or 404), as
    ``read_eightfold_board`` does; that pair counts as the board's one call."""
    def ask(api: str) -> list[dict[str, Any]]:
        generation = {**entry, "api": api}
        payload = http.get(
            _eightfold_listing_url(entry["board"], api),
            params=_eightfold_params(generation, 0, term),
            timeout=STORY_TIMEOUT_SECONDS, retries=1, deadline_monotonic=deadline_monotonic,
        )
        return parse_eightfold_response(payload, generation)[0]

    if entry.get("api"):
        return ask(str(entry["api"]))
    try:
        return ask("pcsx")
    except http.HTTPError as exc:
        if exc.status_code not in (403, 404):
            raise
    if board_robots.disallowed(
        _eightfold_listing_url(entry["board"], "v2"), deadline_monotonic=deadline_monotonic,
    ):
        return []
    return ask("v2")


def fetch_eightfold_details(
    items: list[dict[str, Any]], *, deadline: float,
) -> tuple[int, int]:
    """Fill the text of the given Eightfold roles, one call each.

    Returns the counts left unfetched (late, failed), like the other fetchers.
    """
    def call(item: dict[str, Any]) -> Callable[[float], Any]:
        return lambda http_deadline: http.get(
            str(item["detail_url"]), timeout=API_TIMEOUT_SECONDS, retries=1,
            deadline_monotonic=http_deadline,
        )

    def text_of(payload: Any) -> tuple[str, str] | None:
        body = payload.get("data", payload) if isinstance(payload, dict) else None
        html = ""
        if isinstance(body, dict):
            html = str(body.get("jobDescription") or body.get("job_description") or "")
        return (_block_text(html), _clean_html(html)) if html.strip() else None

    return _fill_role_text(items, call, text_of, deadline=deadline)


# Readers of company-run and platform boards, by provider: given the board's
# entry (from the registry or from detection) and the listing's deadline,
# each returns ``(items, board_info)`` like the hosted readers. Detail
# fetchers fill role text for the roles the digest selected and return how
# many were left unfetched (late, failed). Both are named, and looked up
# when called, so a test may stand in for one: a bare name is a function of
# this module, ``module:function`` one of ``lib.job_boards``.
_COMPANY_READERS: dict[str, str] = {
    COMPANY_PROVIDER_AMAZON: "read_amazon_board",
    COMPANY_PROVIDER_APPLE: "read_apple_board",
    COMPANY_PROVIDER_EIGHTFOLD: "read_eightfold_board",
    COMPANY_PROVIDER_GOOGLE: "google:read_board",
    COMPANY_PROVIDER_IBM: "ibm:read_board",
    PLATFORM_PROVIDER_ORACLE: "oracle:read_board",
    PLATFORM_PROVIDER_JIBE: "jibe:read_board",
    PLATFORM_PROVIDER_PHENOM: "phenom:read_board",
    PLATFORM_PROVIDER_TALENTBREW: "talentbrew:read_board",
    PLATFORM_PROVIDER_KULA: "kula:read_board",
}
_DETAIL_FETCHERS: dict[str, str] = {
    ATS_PROVIDER_WORKDAY: "fetch_workday_details",
    COMPANY_PROVIDER_APPLE: "fetch_apple_details",
    COMPANY_PROVIDER_EIGHTFOLD: "fetch_eightfold_details",
    PLATFORM_PROVIDER_ORACLE: "oracle:fetch_details",
    PLATFORM_PROVIDER_PHENOM: "phenom:fetch_details",
    PLATFORM_PROVIDER_TALENTBREW: "talentbrew:fetch_details",
    PLATFORM_PROVIDER_KULA: "kula:fetch_details",
}


def _named(name: str) -> Callable[..., Any]:
    if ":" not in name:
        return globals()[name]
    module, function = name.split(":", 1)
    return getattr(importlib.import_module(f"{__package__}.job_boards.{module}"), function)


def _company_reader(provider: str) -> Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]:
    return _named(_COMPANY_READERS[provider])


def _detail_fetcher(provider: str) -> Callable[..., tuple[int, int]] | None:
    name = _DETAIL_FETCHERS.get(provider)
    return _named(name) if name else None


def _listing_url(provider: str, entry: dict[str, Any]) -> str:
    """The first listing call a board's reader makes, for the robots.txt check."""
    board = str(entry.get("board") or "")
    if provider == COMPANY_PROVIDER_APPLE:
        return _APPLE_SEARCH_URL
    if provider == COMPANY_PROVIDER_AMAZON:
        return f"https://{board or 'www.amazon.jobs'}/en/search.json"
    if provider == COMPANY_PROVIDER_EIGHTFOLD:
        # An entry that names its generation is asked on that one alone.
        return _eightfold_listing_url(board, str(entry.get("api") or "pcsx"))
    if provider == ATS_PROVIDER_WORKDAY:
        return workday_list_url(board)
    if provider in _COMPANY_READERS and ":" in _COMPANY_READERS[provider]:
        module = importlib.import_module(
            f"{__package__}.job_boards.{_COMPANY_READERS[provider].split(':', 1)[0]}"
        )
        if provider == PLATFORM_PROVIDER_PHENOM:
            return module.widgets_url(entry)
        if provider == COMPANY_PROVIDER_GOOGLE:
            return module.listing_url(1)
        if provider == COMPANY_PROVIDER_IBM:
            return module.listing_url()
        return module.listing_url(entry)
    return ""
