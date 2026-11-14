"""Company-run job boards: the registry, the readers and the honest failure.

A large company often runs its own job site, so no link on its careers page
names a hosted board. The lane reads such a board only when the run knows the
company's official site and that site is in the registry, and says plainly
when a known job site cannot be read.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import http, jobs, render

from tests.test_render_patents import _report

WINDOW = ("2026-10-15", "2026-11-14")
SITE = "https://www.acmecorp.com/"


def _role(index, *, date="2026-11-10", posted=True, **extra):
    item = jobs._ats_item(
        provider="stub", slug="jobs.acmecorp.com", ident=f"R{index}",
        title=f"Engineer {index}, Team {index}", url=f"https://jobs.acmecorp.com/roles/{index}",
        description=f"Build system {index} for team {index}.", date=date,
        department=f"Team {index}", location="Austin, TX", posted_date_true=posted,
        whole_posting=True,
    )
    item.update(extra)
    return item


@pytest.fixture
def stub(monkeypatch):
    """A company board registered for acmecorp.com, read by a stub reader."""
    calls = {"reader": [], "details": []}
    state = {"rows": [_role(n) for n in range(6)], "info": {"board_total": 6}}

    def reader(entry, *, deadline, **_kwargs):
        calls["reader"].append({"entry": entry, "deadline": deadline})
        return list(state["rows"]), dict(state["info"])

    def details(items, *, deadline):
        calls["details"].append({"ids": [item["id"] for item in items], "deadline": deadline})
        for item in items:
            item["description_blocks"] = f"Full text of {item['title']}."
        return 0, 0

    monkeypatch.setitem(
        jobs.COMPANY_BOARDS, "acmecorp.com",
        {"name": "Acme", "provider": "stub", "board": "jobs.acmecorp.com"},
    )
    monkeypatch.setitem(jobs._COMPANY_READERS, "stub", "_stub_reader")
    monkeypatch.setitem(jobs._DETAIL_FETCHERS, "stub", "_stub_details")
    monkeypatch.setattr(jobs, "_stub_reader", reader, raising=False)
    monkeypatch.setattr(jobs, "_stub_details", details, raising=False)
    return calls, state


class _NoRequests:
    """Records every request the lane sends and answers it with a 404."""

    def __init__(self):
        self.urls = []

    def __call__(self, url, *_args, **_kwargs):
        self.urls.append(url)
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    def text(self, url, *_args, **_kwargs):
        # ``get_text`` answers a failure with None rather than raising.
        self.urls.append(url)
        return None


def _search(company="Acme", site=SITE, **kwargs):
    requests = _NoRequests()
    with patch.object(jobs.http, "get_text", side_effect=requests.text), \
            patch.object(jobs.http, "get", side_effect=requests), \
            patch.object(jobs.http, "post", side_effect=requests), \
            http.capture_failures() as failures:
        kwargs.setdefault("web_backend", "none")
        items, artifact = jobs.search_jobs(company, WINDOW, {}, site=site, **kwargs)
    return items, artifact, requests.urls, failures


# --- the registry is consulted only with a known official site ---


def test_a_run_with_no_known_site_never_reaches_a_registered_reader(stub):
    calls, _state = stub
    _items, artifact, urls, _failures = _search(site="")
    assert calls["reader"] == []
    assert artifact["tier"] == "web"
    # Today's discovery ran: guessed domains, then hosted-system slugs.
    assert any("acme.com/careers" in url for url in urls)


def test_a_known_site_outside_the_registry_follows_todays_discovery(stub):
    calls, _state = stub
    _items, artifact, urls, _failures = _search(company="Initech", site="https://www.initech.com/")
    assert calls["reader"] == []
    assert urls[0] == "https://www.initech.com/careers"
    assert "job_site_unreadable" not in artifact


def test_a_registered_reader_s_rows_become_an_ats_artifact_with_a_digest(stub):
    calls, _state = stub
    items, artifact, urls, _failures = _search()
    assert len(items) == 6
    assert artifact["tier"] == "ats"
    assert artifact["provider"] == "stub"
    assert artifact["owned"] and artifact["found_via"] == "site"
    assert artifact["digest"]["roles"]
    assert calls["reader"][0]["entry"]["board"] == "jobs.acmecorp.com"
    # Nothing else was asked: no careers page, no slug probe.
    assert urls == []


def test_detail_text_is_fetched_for_the_selected_roles_only(stub):
    calls, state = stub
    state["rows"] = [_role(n) for n in range(60)]
    state["info"] = {"board_total": 60}
    _items, artifact, _urls, _failures = _search()
    [fetched] = calls["details"]
    selected = {role["url"] for role in artifact["digest"]["roles"]}
    assert {f"https://jobs.acmecorp.com/roles/{ident[3:]}" for ident in fetched["ids"]} == selected
    assert len(fetched["ids"]) < 60


def test_a_list_read_leaves_time_for_the_role_text(stub):
    calls, _state = stub
    started = time.monotonic()
    _search()
    [read] = calls["reader"]
    [fetched] = calls["details"]
    budget = jobs.JOBS_BUDGET_SECONDS
    # The listing must end with about a third of the budget still left for the text.
    assert read["deadline"] - started <= budget * (1 - jobs.DETAIL_SHARE) + 1
    assert fetched["deadline"] - started >= budget - 1


def test_a_comparison_gives_the_reader_its_entity_s_share(stub):
    calls, _state = stub
    started = time.monotonic()
    _search(entity_count=3)
    [read] = calls["reader"]
    assert read["deadline"] - started <= jobs.JOBS_BUDGET_SECONDS / 3 + 1


def test_roles_marked_excluded_are_counted_but_never_selected(stub):
    _calls, state = stub
    state["rows"] = [_role(n, digest_excluded=n < 4) for n in range(8)]
    state["info"] = {"board_total": 8}
    _items, artifact, _urls, _failures = _search()
    titles = {role["title"] for role in artifact["digest"]["roles"]}
    assert titles and not any(f"Engineer {n}," in title for n in range(4) for title in titles)
    assert artifact["open_roles"] == 8


# --- counts from a board read newest first ---


def test_a_newest_first_read_that_left_the_window_counts_new_roles_exactly(stub):
    _calls, state = stub
    state["rows"] = [_role(n, date="2026-11-01") for n in range(5)] + [
        _role(n, date="2026-09-01") for n in range(5, 15)
    ]
    state["info"] = {
        "board_total": 6459, "board_newest_first": True, "board_window_covered": True,
        "board_cut_short": True,
    }
    _items, artifact, _urls, _failures = _search()
    assert artifact["open_roles"] == 6459
    assert artifact["open_roles_floor"] is False
    assert artifact["new_in_window"] == 5


def test_a_newest_first_read_cut_short_inside_the_window_prints_no_new_count(stub):
    _calls, state = stub
    state["rows"] = [_role(n, date="2026-11-12") for n in range(400)]
    state["info"] = {
        "board_total": 6459, "board_newest_first": True, "board_window_covered": False,
        "board_cut_short": True,
    }
    _items, artifact, _urls, _failures = _search()
    assert artifact["open_roles"] == 6459
    assert artifact["new_in_window"] is None
    line = render._jobs_board_footer_line(_report(jobs=artifact))
    assert line == "💼 Jobs: 6,459 open roles"


def test_a_capped_board_total_prints_as_a_floor(stub):
    _calls, state = stub
    state["info"] = {
        "board_total": 10000, "board_total_floor": True, "board_newest_first": True,
        "board_window_covered": False, "board_cut_short": True,
    }
    _items, artifact, _urls, _failures = _search()
    assert render._jobs_board_footer_line(_report(jobs=artifact)) == "💼 Jobs: 10,000+ open roles"


# --- a job site that cannot be read ---


def test_a_reader_that_fails_gives_the_cant_be_read_outcome_and_no_slug_probe(stub, capsys):
    _calls, state = stub
    state["rows"] = []
    items, artifact, urls, _failures = _search()
    assert items == []
    assert artifact["job_site_unreadable"] == "Acme"
    assert urls == []
    err = capsys.readouterr().err
    assert "[Jobs] Acme's job site can't be read automatically" in err
    assert "no supported job board found" not in err


@pytest.mark.parametrize(
    ("company", "site", "name"),
    [
        ("Tesla", "https://www.tesla.com/", "Tesla"),
        ("Google", "https://www.google.com/", "Google"),
        ("Meta Platforms", "https://about.meta.com/", "Meta"),
    ],
)
def test_a_job_site_declared_unreadable_sends_no_request(company, site, name, capsys):
    items, artifact, urls, failures = _search(company=company, site=site)
    assert items == [] and urls == [] and failures == []
    assert artifact["tier"] == "web"
    assert artifact["job_site_unreadable"] == name
    err = capsys.readouterr().err
    assert f"{name}'s job site can't be read automatically" in err
    assert "no supported job board found" not in err


def test_the_footer_says_a_known_job_site_cannot_be_read():
    report = _report()
    report.artifacts["jobs_site_unreadable"] = "Tesla"
    rendered = render.render_compact(report)
    assert "💼 Jobs: Tesla's job site can't be read automatically" in rendered


def test_the_footer_line_is_absent_when_the_site_is_not_in_the_registry():
    _items, artifact, _urls, _failures = _search(company="Initech", site="https://www.initech.com/")
    assert "job_site_unreadable" not in artifact
    assert "can't be read automatically" not in render.render_compact(_report())


def test_a_hiring_signals_run_on_an_unreadable_site_still_returns_the_web_tier():
    found = [{"id": "W1", "title": "Robotics Engineer", "url": "https://boards.greenhouse.io/tesla/jobs/1"}]
    with patch.object(jobs, "search_jobs_web", return_value=(found, {"tier": "web"})):
        items, artifact, urls, _failures = _search(
            company="Tesla", site="https://www.tesla.com/", web_backend="auto", explicit=True,
        )
    assert items == found
    assert artifact["job_site_unreadable"] == "Tesla"
    assert urls == []


def test_a_board_url_on_a_company_job_site_is_still_ignored_without_a_request(capsys):
    assert jobs.parse_board_url("https://jobs.apple.com/en-us/search") == (None, None)
    requests = _NoRequests()
    with patch.object(jobs.http, "get_text", side_effect=requests.text), \
            patch.object(jobs.http, "get", side_effect=requests), \
            patch.object(jobs.http, "post", side_effect=requests):
        items, artifact = jobs.search_board_url(
            "https://jobs.apple.com/en-us/search", ["Apple"], WINDOW, {},
        )
    assert (items, artifact) == ([], {})
    assert requests.urls == []
    err = capsys.readouterr().err
    assert err.count("--jobs-board ignored: jobs.apple.com") == 1


# --- Amazon -------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
AMAZON = json.loads((FIXTURES / "jobs_amazon_search.json").read_text(encoding="utf-8"))


def _amazon(pages, *, fail_at=None):
    asked = []

    def get(url, params=None, **_kwargs):
        offset = int((params or {}).get("offset") or 0)
        asked.append((url, offset))
        index = offset // jobs.AMAZON_PAGE_SIZE
        if index == fail_at:
            raise http.HTTPError("HTTP 503: Service Unavailable", status_code=503)
        if url.startswith("https://www.amazon.jobs/") and index < len(pages):
            return copy.deepcopy(pages[index])
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    return asked, get


def _amazon_search(pages, **kwargs):
    asked, get = _amazon(pages, **kwargs)
    texts = _NoRequests()
    with patch.object(jobs.http, "get", side_effect=get), \
            patch.object(jobs.http, "get_text", side_effect=texts.text), \
            patch.object(jobs.http, "post", side_effect=texts):
        items, artifact = jobs.search_jobs(
            "Amazon", WINDOW, {}, web_backend="none", site="https://www.amazon.com/",
        )
    return items, artifact, asked, texts.urls


def test_amazon_pages_parse_to_rows_with_their_full_text():
    items = [item for page in AMAZON["pages"] for item in jobs.parse_amazon_response(page)[0]]
    assert len(items) == 9
    first = items[0]
    assert first["title"] == "Sr. Electrical Engineer, Prime Air Perception"
    assert first["url"] == "https://www.amazon.jobs/en/jobs/10605151/sr-electrical-engineer-prime-air-perception"
    assert first["department"] == "retail"
    assert first["location"] == "Seattle, Washington, USA"
    assert (first["date"], first["posted_date_true"]) == ("2026-11-14", True)
    assert first["provider"] == "amazon" and first["id"] == "AZ10605151"
    assert "Bachelor's degree" in first["description_blocks"]
    assert "\n" in first["description_blocks"]


def test_amazon_reads_past_a_mixed_page_and_stops_after_one_with_no_new_role():
    items, artifact, asked, other = _amazon_search(AMAZON["pages"])
    assert len(items) == 9
    assert {0, 100, 200} <= {offset for _url, offset in asked}
    assert artifact["tier"] == "ats" and artifact["provider"] == "amazon"
    # Three new on the first page and two on the mixed second page.
    assert artifact["new_in_window"] == 5
    assert artifact["open_roles"] == 10000 and artifact["open_roles_floor"] is True
    assert other == []


def test_amazon_s_capped_total_prints_as_a_floor_and_the_run_prints_a_digest():
    _items, artifact, _asked, _other = _amazon_search(AMAZON["pages"])
    rendered = render.render_compact(_report(jobs=artifact))
    assert "\n## Jobs Digest\n" in rendered
    assert "💼 Jobs: 10,000+ open roles │ 5 new in the L30" in rendered
    assert "Prime Air Perception" in rendered


def test_amazon_error_answered_as_http_200_is_the_cant_be_read_outcome():
    error = {"error": "Result limit cannot be greater than 100", "hits": 0, "jobs": None}
    items, artifact, asked, other = _amazon_search([error])
    assert items == []
    assert artifact["job_site_unreadable"] == "Amazon"
    assert len(asked) == 1
    # No careers page and no hosted-system slug was tried.
    assert other == []
    assert all(url.startswith("https://www.amazon.jobs/") for url, _offset in asked)


def test_amazon_page_that_fails_midway_keeps_the_rows_read():
    items, artifact, _asked, _other = _amazon_search(AMAZON["pages"], fail_at=1)
    assert len(items) == 3
    assert artifact["board_cut_short"] is True
    assert artifact["new_in_window"] is None
    assert artifact["open_roles"] == 10000


# --- Apple --------------------------------------------------------------------

APPLE = json.loads((FIXTURES / "jobs_apple_search.json").read_text(encoding="utf-8"))
APPLE_DETAIL = json.loads((FIXTURES / "jobs_apple_detail.json").read_text(encoding="utf-8"))


def _apple_search(pages, *, detail=lambda url: copy.deepcopy(APPLE_DETAIL), window=("2026-10-15", "2026-11-14")):
    posts, details = [], []

    def post(url, body, **_kwargs):
        posts.append(body["page"])
        assert url == "https://jobs.apple.com/api/v1/search" and "format" in body
        answer = pages.get(body["page"])
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise http.HTTPError("HTTP 404: Not Found", status_code=404)
        return copy.deepcopy(answer)

    others = _NoRequests()

    def get(url, *args, **kwargs):
        if not url.startswith("https://jobs.apple.com/api/v1/jobDetails/"):
            return others(url, *args, **kwargs)
        details.append(url)
        answer = detail(url)
        if answer is None:
            raise http.HTTPError("HTTP 404: Not Found", status_code=404)
        return answer

    with patch.object(jobs.http, "post", side_effect=post), \
            patch.object(jobs.http, "get_text", side_effect=others.text), \
            patch.object(jobs.http, "get", side_effect=get):
        items, artifact = jobs.search_jobs(
            "Apple", window, {}, web_backend="none", site="https://www.apple.com/",
        )
    return items, artifact, posts, details, others.urls


def test_an_apple_page_parses_to_twenty_rows():
    items, total = jobs.parse_apple_response(APPLE["corporate_page"])
    assert total == 6459 and len(items) == 20
    first = items[0]
    assert first["title"].startswith("Senior Legal Counsel")
    assert first["title"].endswith("Home and Wearables Product Counsel")
    assert first["department"] == "Corporate Functions"
    assert first["location"] == "Cupertino"
    assert (first["date"], first["posted_date_true"]) == ("2026-11-14", True)
    assert first["url"] == (
        "https://jobs.apple.com/en-us/details/200689996/senior-legal-counsel-home-and-wearables-product-counsel"
    )
    assert first["detail_url"] == "https://jobs.apple.com/api/v1/jobDetails/200689996?locale=en-us"
    assert not first["digest_excluded"]


def test_an_apple_role_listed_once_per_location_is_one_row_with_every_location():
    rows = APPLE["corporate_page"]["res"]["searchResults"]
    repeated = next(row["positionId"] for row in rows if [r["positionId"] for r in rows].count(row["positionId"]) > 1)
    places = {loc["name"] for row in rows if row["positionId"] == repeated for loc in row["locations"]}
    items, _artifact, _posts, _details, _others = _apple_search({1: APPLE["corporate_page"]})
    merged = [item for item in items if f"/details/{repeated}/" in item["url"]]
    assert len(merged) == 1 and set(merged[0]["offices"]) == places
    assert len(items) == len({row["positionId"] for row in rows})


def test_apple_retail_pipeline_roles_are_counted_but_never_chosen():
    items, total = jobs.parse_apple_response(APPLE["retail_page"])
    assert len(items) == 20 and total == 6459
    assert all(item["digest_excluded"] for item in items)
    # A standing pipeline role carries the request's day, which is not when it was posted.
    assert not any(item["posted_date_true"] for item in items)
    _items, artifact, _posts, details, _others = _apple_search({1: APPLE["retail_page"]})
    assert artifact["open_roles"] == 6459
    assert artifact["digest"]["roles"] == [] and details == []


def test_apple_retail_by_team_name_is_excluded_and_a_pipe_row_has_no_true_date():
    page = copy.deepcopy(APPLE["corporate_page"])
    rows = page["res"]["searchResults"]
    rows[0]["team"] = {"teamName": "Apple Retail", "teamCode": "RTLMY"}
    rows[1].update({"type": "PIPE", "managedPipelineRole": False})
    items, _total = jobs.parse_apple_response(page)
    assert items[0]["digest_excluded"] is True
    assert items[1]["posted_date_true"] is False and items[1]["digest_excluded"] is False


def test_apple_digest_holds_only_corporate_roles_with_their_full_text():
    pages = {1: APPLE["retail_page"], 2: APPLE["corporate_page"], 3: APPLE["mixed_page"]}

    def detail(url):
        # Each role gets a sentence of its own, so it survives boilerplate removal.
        position = url.split("/jobDetails/", 1)[1].split("?", 1)[0]
        answer = copy.deepcopy(APPLE_DETAIL)
        answer["res"]["description"] = f"Own the work of position {position}. " + answer["res"]["description"]
        return answer

    items, artifact, posts, details, others = _apple_search(pages, detail=detail)
    assert {1, 2, 3} <= set(posts)
    assert len(items) == 50
    # The board's total counts rows, so its note counts the 60 rows read, not the 50 roles.
    assert artifact["coverage_note"].startswith("board read in part: 60 rows read of the 6,459")
    roles = artifact["digest"]["roles"]
    by_url = {item["url"]: item for item in items}
    assert roles and not any(by_url[role["url"]]["digest_excluded"] for role in roles)
    assert sorted(details) == sorted(by_url[role["url"]]["detail_url"] for role in roles)
    assert all("Own the work of position" in role["text"] for role in roles)
    assert "role text fetched" not in (artifact["digest"].get("note") or "")
    assert artifact["open_roles"] == 6459 and artifact["new_in_window"] is None
    rendered = render.render_compact(_report(jobs=artifact))
    assert "\n## Jobs Digest\n" in rendered
    assert "💼 Jobs: 6,459 open roles" in rendered
    assert others == []


def test_the_apple_detail_parser_keeps_description_and_qualifications_apart():
    detail = jobs.parse_apple_detail(APPLE_DETAIL)
    assert detail["description"].startswith("You will be responsible for advising")
    assert detail["responsibilities"].startswith("Deep understanding of general privacy laws")
    assert detail["minimumQualifications"].startswith("Knowledge of comprehensive privacy laws")
    assert detail["preferredQualifications"].startswith("Experience providing strategic")
    assert jobs.parse_apple_detail({"res": {"description": ""}}) is None
    assert jobs.parse_apple_detail("<html>no data</html>") is None


def test_apple_answering_no_rows_against_a_total_is_the_cant_be_read_outcome():
    empty = {"res": {"searchResults": [], "totalRecords": 6459}}
    items, artifact, posts, _details, others = _apple_search({1: empty})
    assert items == [] and posts == [1]
    assert artifact["job_site_unreadable"] == "Apple"
    assert others == []


def test_an_apple_detail_that_fails_keeps_its_summary_and_is_counted():
    first_failed = []

    def detail(url):
        if not first_failed:
            first_failed.append(url)
            return None
        return copy.deepcopy(APPLE_DETAIL)

    _items, artifact, _posts, _details, _others = _apple_search(
        {1: APPLE["corporate_page"]}, detail=detail,
    )
    selected = len(artifact["digest"]["roles"])
    assert f"role text fetched for {selected - 1} of {selected} selected roles" in artifact["digest"]["note"]


def test_a_slow_apple_page_ends_that_call_and_keeps_the_rows_read():
    timeout = http.HTTPError("timed out", outcome_state=http.health.TIMEOUT)
    items, artifact, _posts, _details, _others = _apple_search(
        {1: APPLE["corporate_page"], 2: timeout},
    )
    # The page's 20 rows are 15 roles.
    assert len(items) == 15
    assert artifact["board_cut_short"] is True
    assert artifact["open_roles"] == 6459


# --- Eightfold (Microsoft, Qualcomm, Netflix) ----------------------------------

PCSX = json.loads((FIXTURES / "jobs_eightfold_pcsx.json").read_text(encoding="utf-8"))
V2 = json.loads((FIXTURES / "jobs_eightfold_v2.json").read_text(encoding="utf-8"))
MICROSOFT = jobs.COMPANY_BOARDS["microsoft.com"]
NETFLIX = jobs.COMPANY_BOARDS["netflix.com"]


def _eightfold_page(fixture, start, *, pcsx, total=None):
    """The fixture's listing page with ids moved by ``start`` so every page differs."""
    answer = copy.deepcopy(fixture["page"])
    body = answer["data"] if pcsx else answer
    for position in body["positions"]:
        position["id"] = int(position["id"]) + start
        if pcsx:
            position["positionUrl"] = f"/careers/job/{position['id']}"
        else:
            position["canonicalPositionUrl"] = f"https://explore.jobs.netflix.net/careers/job/{position['id']}"
    if total is not None:
        body["count"] = total
    if start >= body["count"]:
        body["positions"] = []
    return answer


def _eightfold_search(company, site, fixture, *, pcsx, total=None, status=None):
    listed, details = [], []

    def get(url, params=None, **_kwargs):
        if "/api/pcsx/search" in url or url.endswith("/api/apply/v2/jobs"):
            listed.append((url, dict(params or {})))
            if status is not None:
                raise http.HTTPError(f"HTTP {status}", status_code=status)
            return _eightfold_page(fixture, int(params["start"]), pcsx=pcsx, total=total)
        if "/api/pcsx/position_details" in url or "/api/apply/v2/jobs/" in url:
            details.append(url)
            # Each role gets a sentence of its own, so it survives boilerplate removal.
            answer = copy.deepcopy(fixture["detail"])
            body = answer["data"] if pcsx else answer
            key = "jobDescription" if pcsx else "job_description"
            body[key] = f"<p>Own the work behind {url.split('jobs/')[-1]}.</p>" + body[key]
            return answer
        raise http.HTTPError("HTTP 404: Not Found", status_code=404)

    texts = _NoRequests()
    with patch.object(jobs.http, "get", side_effect=get), \
            patch.object(jobs.http, "get_text", side_effect=texts.text), \
            patch.object(jobs.http, "post", side_effect=texts):
        items, artifact = jobs.search_jobs(company, WINDOW, {}, web_backend="none", site=site)
    return items, artifact, listed, details, texts.urls


def test_a_pcsx_page_parses_to_rows_with_a_public_url():
    items, total = jobs.parse_eightfold_response(PCSX["page"], MICROSOFT)
    assert total == 2392 and len(items) == 10
    first = items[0]
    assert first["title"] == "Technical Program Manager II - FD&E"
    assert first["department"] == "Technical Program Management"
    assert first["location"] == "United States, Washington, Redmond"
    assert first["url"] == "https://apply.careers.microsoft.com/careers/job/1970393557041639"
    assert first["id"] == "EF1970393557041639" and first["provider"] == "eightfold"
    assert first["detail_url"].startswith(
        "https://apply.careers.microsoft.com/api/pcsx/position_details?position_id=1970393557041639&domain=microsoft.com"
    )


def test_a_v2_page_parses_the_same_fields_from_its_own_names():
    items, total = jobs.parse_eightfold_response(V2["page"], NETFLIX)
    assert total == 418 and len(items) == 10
    first = items[0]
    assert first["title"] == "AI Engineer 6 - AI Foundation & Tooling, Ads Platform"
    assert first["department"] == "Data & Insights"
    assert first["location"] == "Los Gatos,California,United States of America"
    assert first["url"] == "https://explore.jobs.netflix.net/careers/job/790298014263"
    assert first["detail_url"] == "https://explore.jobs.netflix.net/api/apply/v2/jobs/790298014263?domain=netflix.com"


def test_no_eightfold_row_claims_a_true_posted_date():
    rows = (jobs.parse_eightfold_response(PCSX["page"], MICROSOFT)[0]
            + jobs.parse_eightfold_response(V2["page"], NETFLIX)[0])
    assert rows and not any(row["posted_date_true"] or row["date"] for row in rows)


def test_a_403_on_the_registered_generation_is_the_cant_be_read_outcome():
    items, artifact, listed, details, other = _eightfold_search(
        "Microsoft", "https://www.microsoft.com/", PCSX, pcsx=True, status=403,
    )
    assert items == [] and artifact["job_site_unreadable"] == "Microsoft"
    assert len(listed) == 1 and details == [] and other == []


def test_a_large_eightfold_board_is_read_to_its_ceiling_and_prints_the_reported_total():
    items, artifact, listed, details, other = _eightfold_search(
        "Microsoft", "https://www.microsoft.com/", PCSX, pcsx=True,
    )
    assert len(items) == jobs.EIGHTFOLD_ROW_CEILING == 400
    assert len(listed) == 40 and other == []
    assert all(params["domain"] == "microsoft.com" and params["sort_by"] == "timestamp"
               for _url, params in listed)
    assert artifact["open_roles"] == 2392 and artifact["new_in_window"] is None
    rendered = render.render_compact(_report(jobs=artifact))
    assert "💼 Jobs: 2,392 open roles" in rendered
    assert "new in the L30" not in rendered.split("💼 Jobs:", 1)[1].split("\n", 1)[0]


def test_eightfold_role_text_is_requested_only_for_digest_selected_roles():
    items, artifact, _listed, details, _other = _eightfold_search(
        "Microsoft", "https://www.microsoft.com/", PCSX, pcsx=True,
    )
    roles = artifact["digest"]["roles"]
    assert roles and len(details) == len(roles) < len(items)
    by_url = {item["url"]: item for item in items}
    assert {by_url[role["url"]]["detail_url"] for role in roles} == set(details)
    assert all("Own the work behind" in role["text"] for role in roles)


@pytest.mark.parametrize(
    "company, site, fixture, pcsx",
    [("Microsoft", "https://www.microsoft.com/", PCSX, True),
     ("Netflix", "https://www.netflix.com/", V2, False)],
)
def test_microsoft_and_netflix_runs_print_a_digest(company, site, fixture, pcsx):
    _items, artifact, _listed, _details, _other = _eightfold_search(company, site, fixture, pcsx=pcsx)
    rendered = render.render_compact(_report(jobs=artifact))
    assert "\n## Jobs Digest\n" in rendered
    assert "role text fetched" not in (artifact["digest"].get("note") or "")
