"""The person resolve step in ``main``: LinkedIn, bio page, and handles.

A person run resolves who the person is before the X-handle gate, because a
handle found on their profile or bio page changes which lanes run. These
tests drive ``main`` with the LinkedIn lookup faked at ``lib.http.request``
and the bio lane faked at its entry point, and read what reaches
``pipeline.run``.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import last30days as cli
from lib import deepline, employer_site, http

NAME = "Dana Okafor"
EMPLOYER = "Northwind Freight:northwind.example"
PROFILE_URL = "https://www.linkedin.com/in/dana-okafor"
DEEPLINE = "https://code.deepline.com/api/v2/integrations"
DIAG = {
    "available_sources": ["reddit", "x", "hackernews", "github"],
    "providers": {"google": False, "openai": False, "xai": False},
    "x_backend": None,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": None,
    "permission_preflight": {},
}
PLAN_WITH_X = (
    '{"intent":"general","freshness_mode":"balanced_recent","cluster_mode":"none",'
    '"subqueries":[{"label":"primary","search_query":"Dana Okafor","ranking_query":'
    '"What has Dana Okafor done lately?","sources":["reddit","x","hackernews"],"weight":1.0}]}'
)
HOSTED = {"CLAUDECODE": "1"}
NO_BIO = {"status": "not found", "url": "", "title": "", "bio": "", "links": [], "domain": "northwind.example"}


def _profile(websites: list[str], company: str = "Northwind Freight") -> dict:
    return {"status": "completed", "result": {"data": {"element": {
        "firstName": "Dana", "lastName": "Okafor",
        "headline": f"VP Operations at {company}",
        "linkedinUrl": PROFILE_URL,
        "currentPosition": [{"position": "VP Operations", "companyName": company,
                             "startDate": {"text": "Mar 2024"}}],
        "experience": [],
        "websites": websites,
    }}}}


class _RetrievalReached(Exception):
    pass


def _run_main(monkeypatch, argv, *, websites=(), bio=None, config=None, host_env=None, auto=None):
    for name in ("LAST30DAYS_API_KEY", "LAST30DAYS_API_BASE", "LAST30DAYS_MEMORY_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    for name, value in (host_env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])

    requests: list[str] = []

    def fake_request(method, url, headers=None, json_data=None, **kwargs):
        requests.append(url)
        if url == f"{DEEPLINE}/harvestapi_get_profile/execute":
            return _profile(list(websites))
        if url == f"{DEEPLINE}/limadata_find_person_profiles/execute":
            return {"status": "completed", "result": {"data": {"linkedin_url": PROFILE_URL}}}
        raise AssertionError(f"unexpected request to {url}")

    bio_lane = bio if callable(bio) else mock.Mock(return_value=dict(bio or NO_BIO))
    run_mock = mock.Mock(side_effect=_RetrievalReached)
    err = io.StringIO()
    cfg = {"OPENROUTER_API_KEY": "dummy-openrouter-key-000", "DEEPLINE_API_KEY": "dl-test-key"}
    cfg.update(config or {})
    with mock.patch.object(cli.env, "get_config", return_value=cfg), \
         mock.patch.object(cli.pipeline, "diagnose", mock.Mock(return_value=dict(DIAG))), \
         mock.patch.object(cli.pipeline, "run", run_mock), \
         mock.patch.object(http, "request", fake_request), \
         mock.patch.object(employer_site, "find_bio", bio_lane), \
         mock.patch("lib.resolve.auto_resolve", mock.Mock(return_value=dict(auto or {}))), \
         mock.patch("lib.resolve.official_site_socials", mock.Mock(return_value={"x_handle": "", "github_user": ""})), \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()), \
         redirect_stdout(io.StringIO()), redirect_stderr(err):
        try:
            rc = cli.main()
        except _RetrievalReached:
            rc = None
        except SystemExit as exc:
            rc = exc.code
    return rc, err.getvalue(), run_mock, requests


def _person(run_mock):
    return run_mock.call_args.kwargs["config"]["_person"]


def test_profile_x_link_feeds_the_x_lane_as_a_derived_handle(monkeypatch):
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--linkedin", PROFILE_URL, "--plan", PLAN_WITH_X],
        websites=["https://x.com/danaokafor", "https://github.com/dokafor"], host_env=HOSTED,
    )
    assert rc is None, err
    assert run_mock.call_args.kwargs["x_handle"] == "danaokafor"
    assert run_mock.call_args.kwargs["github_user"] == "dokafor"
    assert _person(run_mock).derived_handles == {"x": "danaokafor", "github": "dokafor"}


def test_host_x_handle_beats_the_bio_page(monkeypatch):
    bio = dict(NO_BIO, status="found", links=["https://x.com/dana_bio"])
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--x-handle", "dana_host", "--plan", PLAN_WITH_X],
        bio=bio, host_env=HOSTED,
    )
    assert rc is None, err
    assert run_mock.call_args.kwargs["x_handle"] == "dana_host"
    assert "x" not in _person(run_mock).derived_handles


def test_bio_page_links_win_over_profile_websites(monkeypatch):
    bio = dict(NO_BIO, status="found", links=["https://x.com/dana_bio"])
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--linkedin", PROFILE_URL, "--plan", PLAN_WITH_X],
        websites=["https://x.com/dana_profile"], bio=bio, host_env=HOSTED,
    )
    assert rc is None, err
    assert run_mock.call_args.kwargs["x_handle"] == "dana_bio"


def test_company_x_account_is_not_a_derived_handle(monkeypatch):
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--linkedin", PROFILE_URL, "--plan", PLAN_WITH_X],
        websites=["https://x.com/NorthwindFreight", "https://www.northwind.example/", "https://danaokafor.example"],
        host_env=HOSTED,
    )
    assert rc is None, err
    assert run_mock.call_args.kwargs["x_handle"] is None
    assert _person(run_mock).derived_handles == {"site": "https://danaokafor.example"}


def test_person_run_with_no_handles_passes_the_x_handle_gate(monkeypatch):
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--plan", PLAN_WITH_X], host_env=HOSTED,
    )
    assert rc is None, err
    assert "X handle decision missing" not in err
    assert "No X handle:" in err
    assert run_mock.call_args.kwargs["x_handle"] is None


def test_person_run_ignores_handles_auto_resolve_picked_by_mention_count(monkeypatch):
    rc, err, run_mock, _ = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--auto-resolve"],
        auto={"x_handle": "dana_namesake", "github_user": "dana-namesake"}, host_env=HOSTED,
    )
    assert rc is None, err
    assert run_mock.call_args.kwargs["x_handle"] is None
    assert run_mock.call_args.kwargs["github_user"] is None


def test_resolve_deadline_lets_the_run_proceed_and_records_a_timeout(monkeypatch):
    monkeypatch.setattr(cli, "PERSON_RESOLVE_SECONDS", 0.5)
    # The paid client will not start a call with under its usual floor left.
    monkeypatch.setattr(deepline, "MIN_ATTEMPT_SECONDS", 0.0)
    release = threading.Event()

    def slow_bio(*args, **kwargs):
        release.wait(5)
        return dict(NO_BIO)

    started = time.monotonic()
    try:
        rc, err, run_mock, _ = _run_main(
            monkeypatch, [NAME, "--employer", EMPLOYER, "--linkedin", PROFILE_URL, "--plan", PLAN_WITH_X],
            websites=["https://x.com/danaokafor"], bio=slow_bio, host_env=HOSTED,
        )
    finally:
        release.set()
    assert time.monotonic() - started < 4
    assert rc is None, err
    person = _person(run_mock)
    assert person.timed_out is True
    assert person.linkedin_status == "checked"
    assert run_mock.call_args.kwargs["x_handle"] == "danaokafor"


def test_end_to_end_person_run_reaches_the_pipeline_resolved(monkeypatch):
    bio = dict(NO_BIO, status="found", url="https://northwind.example/team/dana-okafor",
               links=["https://github.com/dokafor"])
    rc, err, run_mock, requests = _run_main(
        monkeypatch, [NAME, "--employer", EMPLOYER, "--email", "dana.okafor@northwind.example",
                      "--plan", PLAN_WITH_X],
        websites=["https://x.com/danaokafor"], bio=bio, host_env=HOSTED,
    )
    assert rc is None, err
    assert requests == [
        f"{DEEPLINE}/limadata_find_person_profiles/execute",
        f"{DEEPLINE}/harvestapi_get_profile/execute",
    ]
    person = _person(run_mock)
    assert person.linkedin_url == PROFILE_URL
    assert person.linkedin_status == "checked"
    for anchor in ("Northwind Freight", "northwind.example", PROFILE_URL):
        assert anchor in person.anchors
    assert person.bio["url"] == "https://northwind.example/team/dana-okafor"
    assert person.derived_handles == {"x": "danaokafor", "github": "dokafor"}
    assert person.timed_out is False
    assert run_mock.call_args.kwargs["x_handle"] == "danaokafor"
    assert run_mock.call_args.kwargs["github_user"] == "dokafor"
    assert "dana.okafor@northwind.example" not in err


def test_run_without_person_flags_has_no_resolve_step(monkeypatch):
    rc, err, run_mock, requests = _run_main(
        monkeypatch, [NAME, "--x-handle", "dana_host", "--plan", PLAN_WITH_X], host_env=HOSTED,
    )
    assert rc is None, err
    assert requests == []
    assert "_person" not in run_mock.call_args.kwargs["config"]
