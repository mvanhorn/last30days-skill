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
    assert [offset for _url, offset in asked][:3] == [0, 100, 200]
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
