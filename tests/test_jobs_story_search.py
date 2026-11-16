"""Story search: each readable board is searched for the run's story terms.

Boards with a keyword search are asked once per term before their
newest-first read; boards read whole are matched where they are. Every
matched row carries the term it matched, and a role found both ways is one
row.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import health, http, jobs, jobs_digest, render

WINDOW = ("2026-10-15", "2026-11-14")
FIXTURES = Path(__file__).parent / "fixtures"
AMAZON = json.loads((FIXTURES / "jobs_amazon_search.json").read_text(encoding="utf-8"))
PCSX = json.loads((FIXTURES / "jobs_eightfold_pcsx.json").read_text(encoding="utf-8"))


def _missing(url, *_args, **_kwargs):
    raise http.HTTPError("HTTP 404: Not Found", status_code=404)


# --- Workday: the site's own search, through ``searchText`` ---

WORKDAY_SITE = "https://www.acme.com"
WORKDAY_PAGE = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers">Jobs</a>'


def _workday_row(index, title=None):
    return {
        "title": title or f"Role {index}",
        "externalPath": f"/job/Remote/Role-{index}_JR{index}",
        "locationsText": "Remote",
        "postedOn": "Posted Today",
        "bulletFields": [f"JR{index}"],
    }


class WorkdayBoard:
    """A 40-role Workday board whose own search matches titles."""

    def __init__(self):
        self.rows = [_workday_row(index) for index in range(40)]
        # One match inside the rows the newest-first read reaches, one past it.
        self.rows[3] = _workday_row(3, "Humanoid Controls Engineer")
        self.rows[35] = _workday_row(35, "Humanoid Hand Designer")
        self.bodies = []
        self.lock = threading.Lock()

    def post(self, url, json_data, headers=None, **_kwargs):
        with self.lock:
            self.bodies.append(dict(json_data))
        term = json_data["searchText"].lower()
        rows = [row for row in self.rows if term in row["title"].lower()] if term else self.rows
        offset = json_data["offset"]
        return {"total": len(rows) if offset == 0 else 0,
                "jobPostings": rows[offset:offset + json_data["limit"]]}

    def get(self, url, headers=None, **_kwargs):
        return {"jobPostingInfo": {"jobDescription": f"<p>Own {url}.</p>", "startDate": "2026-11-03"}}

    def search(self, terms, *, ceiling=20):
        pools = []
        real_select = jobs_digest.select_roles

        def select(items, **kwargs):
            pools.append(list(items))
            return real_select(items, **kwargs)

        with patch.object(jobs, "WORKDAY_ROW_CEILING", ceiling), \
                patch.object(jobs.http, "get_text",
                             side_effect=lambda url, **_k: None if url.endswith("/robots.txt") else WORKDAY_PAGE), \
                patch.object(jobs.http, "post", side_effect=self.post), \
                patch.object(jobs.http, "get", side_effect=self.get), \
                patch.object(jobs.jobs_digest, "select_roles", side_effect=select):
            items, artifact = jobs.search_jobs(
                "Acme", WINDOW, {"_jobs_story_terms": terms}, web_backend="none", site=WORKDAY_SITE,
            )
        return items, artifact, pools


def test_workday_story_term_is_sent_as_search_text_before_the_listing_and_matches_are_tagged():
    board = WorkdayBoard()
    items, artifact, pools = board.search(["humanoid"])

    assert board.bodies[0]["searchText"] == "humanoid"
    assert [body["searchText"] for body in board.bodies[1:]] == [""] * (len(board.bodies) - 1)
    tagged = {item["title"]: item.get("story_term") for item in items if item.get("story_term")}
    assert tagged == {"Humanoid Controls Engineer": "humanoid"}
    # The match the newest-first read did not reach is offered to the digest,
    # tagged, and is not one of the board's rows.
    pool = {item["title"]: item.get("story_term") for item in pools[0]}
    assert pool["Humanoid Hand Designer"] == "humanoid"
    assert "Humanoid Hand Designer" not in {item["title"] for item in items}
    assert len(items) == 20 and artifact["open_roles"] == 20
    assert artifact["story_search"] == {
        "ran": True, "terms": ["humanoid"], "matches": 2, "story_partial": False,
    }


def test_a_story_match_past_the_read_takes_a_digest_slot_with_its_fetched_text():
    board = WorkdayBoard()
    board.rows = [_workday_row(index) for index in range(100)]
    board.rows[90] = _workday_row(90, "Humanoid Hand Designer")
    _items, artifact, _pools = board.search(["humanoid"], ceiling=60)

    roles = artifact["digest"]["roles"]
    assert len(roles) == jobs_digest.DIGEST_MAX_ROLES
    first = roles[0]
    assert (first["title"], first["story_term"]) == ("Humanoid Hand Designer", "humanoid")
    assert first["text"] == "Own https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/AcmeCareers/job/Remote/Role-90_JR90."


# --- Amazon: ``base_query`` on search.json ---

HUMANOID_JOB = {
    "id_icims": "3100001",
    "title": "Applied Scientist, Humanoid Robotics",
    "job_path": "/en/jobs/3100001/applied-scientist-humanoid-robotics",
    "posted_date": "November 12, 2026",
    "description": "Teach humanoid robots to move packages.",
    "normalized_location": "Seattle, Washington, USA",
}
# The search also answers a role whose text never names the term.
LOOSE_JOB = {
    "id_icims": "3100002",
    "title": "Area Manager",
    "job_path": "/en/jobs/3100002/area-manager",
    "posted_date": "November 12, 2026",
    "description": "Lead a shift at a fulfillment center.",
    "normalized_location": "Austin, Texas, USA",
}


def _amazon_pages():
    pages = copy.deepcopy(AMAZON["pages"])
    # The matched role is also on the board's newest page.
    pages[0]["jobs"][1] = dict(HUMANOID_JOB)
    return pages


def _amazon_get(pages, story_pages, asked, *, before=None):
    def get(url, params=None, deadline_monotonic=None, **_kwargs):
        params = dict(params or {})
        asked.append(params)
        if not url.startswith("https://www.amazon.jobs/"):
            raise http.HTTPError("HTTP 404: Not Found", status_code=404)
        if before is not None:
            before(params, deadline_monotonic)
        if params.get("base_query"):
            answer = story_pages[params["base_query"]]
            if isinstance(answer, Exception):
                raise answer
            return copy.deepcopy(answer)
        index = int(params.get("offset") or 0) // jobs.AMAZON_PAGE_SIZE
        if index < len(pages):
            return copy.deepcopy(pages[index])
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    return get


def _amazon_search(terms, story_pages, *, before=None, budget=None):
    asked = []
    get = _amazon_get(_amazon_pages(), story_pages, asked, before=before)
    with patch.object(jobs, "AMAZON_PAGE_SIZE", 3), \
            patch.object(jobs.http, "get", side_effect=get), \
            patch.object(jobs.http, "get_text", return_value=None), \
            patch.object(jobs.http, "post", side_effect=_missing), \
            http.capture_failures() as failures:
        items, artifact = jobs.search_jobs(
            "Amazon", WINDOW, {"_jobs_story_terms": terms}, web_backend="none",
            site="https://www.amazon.com/", budget=budget,
        )
    return items, artifact, asked, failures


def _hits(*rows):
    return {"error": None, "hits": len(rows), "jobs": [dict(row) for row in rows]}


def test_amazon_story_term_is_sent_as_base_query_and_its_match_is_one_tagged_row():
    items, artifact, asked, _failures = _amazon_search(
        ["humanoid"], {"humanoid": _hits(HUMANOID_JOB, LOOSE_JOB)},
    )

    assert asked[0]["base_query"] == "humanoid"
    assert all("base_query" not in params for params in asked[1:])
    matched = [item for item in items if item["id"] == "AZ3100001"]
    assert len(matched) == 1 and matched[0]["story_term"] == "humanoid"
    # A row whose text does not name the term is not a match.
    assert not any(item["id"] == "AZ3100002" for item in items)
    assert len(items) == 9 and artifact["resultCount"] == 9
    assert artifact["open_roles"] == 10000
    assert artifact["story_search"]["matches"] == 1


def test_story_calls_that_reach_the_story_deadline_skip_the_rest_and_the_read_keeps_its_share():
    now = [0.0]
    budget = jobs.LaneBudget(45, clock=lambda: now[0])
    left_at_listing = []

    def before(params, deadline_monotonic):
        if params.get("base_query"):
            # Each story call takes all the time it is allowed.
            now[0] += round(deadline_monotonic - time.monotonic())
        elif not left_at_listing:
            left_at_listing.append(budget.remaining())

    terms = ["humanoid", "drone", "satellite", "pharmacy"]
    items, artifact, asked, _failures = _amazon_search(
        terms, {term: _hits() for term in terms}, before=before, budget=budget,
    )

    assert [params["base_query"] for params in asked if "base_query" in params] == ["humanoid", "drone"]
    assert artifact["story_search"]["story_partial"] is True
    assert artifact["story_search"]["terms"] == ["humanoid", "drone"]
    # One third of 45 s went to story calls; the newest-first read starts
    # with the other two thirds.
    assert left_at_listing[0] >= 30 - 0.5
    assert len(items) == 9


def test_a_429_on_the_second_story_call_stops_story_search_and_the_board_is_still_read():
    items, artifact, asked, failures = _amazon_search(
        ["humanoid", "drone", "satellite"],
        {
            "humanoid": _hits(HUMANOID_JOB),
            "drone": http.HTTPError("HTTP 429: Too Many Requests", status_code=429),
            "satellite": _hits(),
        },
    )

    assert [params["base_query"] for params in asked if "base_query" in params] == ["humanoid", "drone"]
    assert artifact["story_search"] == {
        "ran": True, "terms": ["humanoid"], "matches": 1, "story_partial": True,
    }
    assert len(items) == 9 and artifact["tier"] == "ats"
    assert not any(failure.status_code == 429 for failure in failures)



@pytest.mark.parametrize("failure", [
    http.HTTPError("HTTP 500: Internal Server Error", status_code=500),
    ValueError("not JSON"),
], ids=["server-error", "bad-shape"])
def test_a_failed_story_call_skips_its_term_marks_the_search_partial_and_the_next_term_runs(failure):
    items, artifact, asked, _failures = _amazon_search(
        ["drone", "humanoid"], {"drone": failure, "humanoid": _hits(HUMANOID_JOB)},
    )

    assert [params["base_query"] for params in asked if "base_query" in params] == ["drone", "humanoid"]
    assert artifact["story_search"] == {
        "ran": True, "terms": ["humanoid"], "matches": 1, "story_partial": True,
    }
    assert len(items) == 9


def test_a_story_call_that_times_out_stops_story_search():
    timeout = http.HTTPError("timed out", outcome_state=health.TIMEOUT)
    _items, artifact, asked, _failures = _amazon_search(
        ["drone", "humanoid"], {"drone": timeout, "humanoid": _hits(HUMANOID_JOB)},
    )

    assert [params["base_query"] for params in asked if "base_query" in params] == ["drone"]
    assert artifact["story_search"] == {"ran": False, "terms": [], "matches": 0, "story_partial": True}


def test_a_board_whose_every_story_call_failed_says_not_run_not_no_matches():
    unavailable = http.HTTPError("HTTP 503: Service Unavailable", status_code=503)
    items, artifact, _asked, _failures = _amazon_search(
        ["drone", "satellite"], {"drone": unavailable, "satellite": unavailable},
    )

    assert artifact["story_search"] == {"ran": False, "terms": [], "matches": 0, "story_partial": True}
    assert len(items) == 9
    assert render._story_search_status(artifact["story_search"]) == "; story search: not run (partial)"

# --- Eightfold: one story call, however many terms ---

_MICROSOFT_LANDING = (
    '<html><body><div id="careers-eightFoldCareerUrl" hidden>'
    "https://apply.careers.microsoft.com/careers</div>"
    "<p>Join our team. Open positions.</p></body></html>"
)
_MICROSOFT_PAGE = (
    '<html><head><link href="https://static.vscdn.net/fonts/css/eightfold-font-base.css">'
    '<script>window._EF_GROUP_ID = "microsoft.com";</script></head><body></body></html>'
)


def test_eightfold_is_asked_one_story_call_for_three_terms():
    listed = []

    def get(url, params=None, **_kwargs):
        if "/api/pcsx/search" in url:
            listed.append(dict(params or {}))
            answer = copy.deepcopy(PCSX["page"])
            if int(params["start"]) > 0:
                answer["data"]["positions"] = []
            return answer
        if "/api/pcsx/position_details" in url:
            return copy.deepcopy(PCSX["detail"])
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    pages = {
        "https://careers.microsoft.com/": _MICROSOFT_LANDING,
        "https://apply.careers.microsoft.com/careers": _MICROSOFT_PAGE,
    }
    with patch.object(jobs.http, "get", side_effect=get), \
            patch.object(jobs.http, "get_text", side_effect=lambda url, **_k: pages.get(url)), \
            patch.object(jobs.http, "post", side_effect=_missing):
        _items, artifact = jobs.search_jobs(
            "Microsoft", WINDOW, {"_jobs_story_terms": ["copilot", "quantum", "datacenter"]},
            web_backend="none", site="https://www.microsoft.com/",
        )

    story_calls = [params for params in listed if params.get("query")]
    assert [params["query"] for params in story_calls] == ["copilot"]
    assert listed[0]["query"] == "copilot"
    assert artifact["story_search"]["terms"] == ["copilot"]
    assert artifact["story_search"]["story_partial"] is False


# --- Greenhouse: a board read whole is matched where it is ---


def _greenhouse_job(ident, title, content):
    return {
        "id": ident, "title": title, "content": content,
        "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{ident}",
        "first_published": "2026-11-10T00:00:00Z",
        "departments": [{"name": "Engineering"}], "offices": [{"name": "Remote"}],
    }


def test_greenhouse_rows_are_matched_locally_on_whole_words_in_title_and_description():
    board = {"jobs": [
        _greenhouse_job(1, "AI Platform Engineer", "<p>Ship models.</p>"),
        _greenhouse_job(2, "Maintenance Technician", "<p>Keep the plant running.</p>"),
        _greenhouse_job(3, "Mechanical Engineer", "<p>Design arms for our robotics line.</p>"),
        _greenhouse_job(4, "Recruiter", "<p>Hire people.</p>"),
    ]}
    asked = []

    def get(url, **kwargs):
        asked.append(url)
        return copy.deepcopy(board)

    with patch.object(jobs.http, "get_text",
                      return_value='<a href="https://boards.greenhouse.io/acme">Jobs</a>'), \
            patch.object(jobs.http, "get", side_effect=get):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {"_jobs_story_terms": ["AI", "robotics"]},
            web_backend="none", site="https://www.acme.com/",
        )

    tags = {item["title"]: item.get("story_term") for item in items}
    assert tags == {
        "AI Platform Engineer": "AI",
        "Maintenance Technician": None,
        "Mechanical Engineer": "robotics",
        "Recruiter": None,
    }
    # The board is read once; no search call is added for a board read whole.
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/acme/jobs"]
    assert artifact["story_search"] == {
        "ran": True, "terms": ["AI", "robotics"], "matches": 2, "story_partial": False,
    }
    assert artifact["open_roles"] == 4


# --- A board with no story search says so ---


def test_a_board_this_lane_cannot_search_records_that_story_search_did_not_run(monkeypatch):
    def reader(entry, *, deadline, **_kwargs):
        rows = [jobs._ats_item(
            provider="stub", slug="jobs.acmecorp.com", ident=f"R{n}", title=f"Robotics Engineer {n}",
            url=f"https://jobs.acmecorp.com/roles/{n}", description="Build robots.",
            date="2026-11-10", department=f"Team {n}", location="Austin, TX", whole_posting=True,
        ) for n in range(3)]
        return rows, {"board_total": 3}

    monkeypatch.setitem(
        jobs.COMPANY_BOARDS, "acmecorp.com",
        {"name": "Acme", "provider": "stub", "board": "jobs.acmecorp.com"},
    )
    monkeypatch.setitem(jobs._COMPANY_READERS, "stub", "_stub_reader")
    monkeypatch.setattr(jobs, "_stub_reader", reader, raising=False)
    with patch.object(jobs.http, "get", side_effect=_missing), \
            patch.object(jobs.http, "get_text", return_value=None), \
            patch.object(jobs.http, "post", side_effect=_missing):
        items, artifact = jobs.search_jobs(
            "Acme", WINDOW, {"_jobs_story_terms": ["robotics"]},
            web_backend="none", site="https://www.acmecorp.com/",
        )

    assert artifact["story_search"] == {
        "ran": False, "terms": [], "matches": 0, "story_partial": False,
    }
    assert not any(item.get("story_term") for item in items)
