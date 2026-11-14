"""When a run starts the patent lane, on which company, and what it records.

Every test drives the command line's own main runner. ``patents.fetch_patent_data``
is a stand-in that records its arguments, so nothing here opens a socket.
"""

from __future__ import annotations

import copy
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

import last30days as cli
from lib import hosted, patents, pipeline, resolve, schema

from tests.test_company_resolve import FakeWikidata, _subsidiary_label_requests

COMPANY = {
    "company": "MainBrand Inc.",
    "company_site": "https://mainbrand.example/",
    "company_names": ["MainBrand Labs", "MainBrand Licensing LLC"],
}
ARTIFACT = {
    "company": "MainBrand Inc.",
    "company_via_person": False,
    "window": {"from": "2026-06-01", "to": "2026-06-30"},
    "groups": [
        {"name": "MainBrand Inc.", "kind": "confirmed", "applications": 4, "grants": 2},
        {"name": "MainBrand Licensing LLC", "kind": "name-matched", "applications": 1, "grants": 0},
    ],
    "other_names": [],
    "counts": {
        "applications": 4, "grants": 2, "name_matched_applications": 1,
        "name_matched_grants": 0, "first_publications": 2,
        "name_matched_first_publications": 1, "continuing": 2,
        "previously_published_grants": 2, "designs": 0, "other_kinds": 0, "floor": False,
    },
    "entries": [],
    "index": [],
    "partial": None,
    "status": "digest",
}
PLAN = {
    "intent": "opinion", "freshness_mode": "balanced_recent", "cluster_mode": "debate",
    "subqueries": [{
        "label": "primary", "search_query": "test topic", "ranking_query": "test topic",
        "sources": ["hackernews"], "weight": 1.0,
    }],
}


class FakeLane:
    """Stands in for ``patents.fetch_patent_data``."""

    def __init__(self, outcome=None, wait_for: threading.Event | None = None):
        self.outcome = {"artifact": ARTIFACT} if outcome is None else outcome
        self.wait_for = wait_for
        self.calls: list[dict] = []

    def __call__(self, company, date_from, date_to, config=None, *, budget_seconds=90.0):
        self.calls.append({
            "company": company, "date_from": date_from, "date_to": date_to,
            "budget_seconds": budget_seconds,
        })
        if self.wait_for is not None:
            self.wait_for.wait(10)
        return self.outcome


def _stub_report(**kwargs) -> schema.Report:
    topic = kwargs["topic"]
    return schema.Report(
        topic=topic, range_from="2026-06-01", range_to="2026-06-30",
        generated_at="2026-06-30T12:00:00Z",
        provider_runtime=schema.ProviderRuntime("local", "none", "none"),
        query_plan=schema.QueryPlan("general", "recent", "none", topic, [], {}),
        clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
    )


def _real_window_report(real_run):
    """The pipeline's own run, offline: its report carries the window the
    pipeline itself derives from the lookback and the as-of date."""

    def research(**kwargs):
        with mock.patch.object(pipeline, "_retrieve_stream", return_value=([], {})):
            return real_run(**{
                **kwargs, "mock": True, "web_backend": "none", "external_plan": PLAN,
                "config": dict(kwargs["config"]),
            })

    return research


def _run(
    monkeypatch, tmp_path, *, topic="MainBrand", args=(), config=None, companies=None,
    lane=None, competitors_list=None, real_window=False, wikidata=None,
):
    """Run the command line. ``wikidata`` swaps the company stand-in for the
    real resolver answering from that recorded Wikidata."""
    lane = lane or FakeLane()
    reports: dict[str, schema.Report] = {}
    build = _real_window_report(pipeline.run) if real_window else _stub_report

    def research(**kwargs):
        reports[kwargs["topic"]] = build(**kwargs)
        return reports[kwargs["topic"]]

    companies = {"MainBrand": COMPANY} if companies is None else companies
    if wikidata is None:
        company_resolver = mock.Mock(
            side_effect=lambda name, **_kwargs: copy.deepcopy(companies.get(name, {}))
        )
    else:
        company_resolver = mock.Mock(wraps=cli.company_resolve.resolve_company)
        monkeypatch.setattr(cli.company_resolve.http, "get", wikidata)
    monkeypatch.setattr(patents, "fetch_patent_data", lane)
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {} if config is None else config)
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(cli.env, "CONFIG_FILE", tmp_path / "config" / ".env")
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_a, **_k: {"available_sources": ["reddit"]})
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(resolve, "_has_backend", lambda _config: False)
    monkeypatch.setattr(resolve, "auto_resolve", lambda _topic, _config: {})
    monkeypatch.setattr(
        resolve, "official_site_socials", lambda _site: {"x_handle": "", "github_user": ""},
    )
    monkeypatch.setattr(cli.company_resolve, "resolve_company", company_resolver)
    monkeypatch.setattr(cli.yahoo_finance, "_is_available", lambda: False)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", "")
    monkeypatch.setenv("LAST30DAYS_STORE", "")
    argv = ["last30days.py", topic]
    if competitors_list:
        argv.extend(["--competitors-list", competitors_list])
    argv.extend(["--emit=json", "--json-profile=raw", "--save-dir=", *args])
    monkeypatch.setattr(sys, "argv", argv)

    assert cli.main() == 0
    return SimpleNamespace(
        lane=lane, reports=reports, report=reports.get(topic), company_resolver=company_resolver,
    )


# --- which runs, and on which company ---------------------------------------


def test_resolved_company_run_starts_the_lane_with_that_company_and_attaches_the_artifact(
    monkeypatch, tmp_path,
):
    observed = _run(monkeypatch, tmp_path)

    assert [call["company"] for call in observed.lane.calls] == [COMPANY]
    assert observed.lane.calls[0]["company"]["company_names"] == COMPANY["company_names"]
    assert observed.report.artifacts["patents"] == ARTIFACT
    assert "patents" not in observed.report.source_status


def test_resolved_company_run_with_a_host_ticker_attaches_the_artifact_too(monkeypatch, tmp_path):
    observed = _run(monkeypatch, tmp_path, args=["--ticker", "MAIN:MainBrand"])

    assert [call["company"]["company"] for call in observed.lane.calls] == ["MainBrand Inc."]
    assert observed.report.artifacts["patents"] == ARTIFACT
    assert observed.report.artifacts["resolved"]["ticker_source"] == "host"


@pytest.mark.parametrize(
    ("kwargs", "env_vars"),
    [
        ({"competitors_list": "PeerOne,PeerTwo"}, {}),
        ({"args": ["--mock"]}, {}),
        ({"args": ["--hiring-signals"]}, {}),
        ({"args": ["--quick"]}, {}),
        ({"config": {"EXCLUDE_SOURCES": "patents"}}, {}),
        ({}, {"LAST30DAYS_NO_PATENT_DIGEST": "1"}),
    ],
    ids=["comparison", "mock", "hiring-signals", "quick", "excluded", "no-patent-digest-env"],
)
def test_runs_that_never_ask_for_patents(monkeypatch, tmp_path, kwargs, env_vars):
    for name, value in env_vars.items():
        monkeypatch.setenv(name, value)
    companies = {name: {"company": f"{name} Inc."} for name in ("MainBrand", "PeerOne", "PeerTwo")}

    observed = _run(monkeypatch, tmp_path, companies=companies, **kwargs)

    assert observed.lane.calls == []
    assert observed.reports
    for report in observed.reports.values():
        assert "patents" not in report.artifacts
        assert "patents" not in report.source_status


@pytest.mark.parametrize(
    ("kwargs", "env_vars"),
    [
        ({"competitors_list": "Stripe,IBM"}, {}),
        ({"args": ["--hiring-signals"]}, {}),
        ({"args": ["--quick"]}, {}),
        ({}, {"LAST30DAYS_NO_PATENT_DIGEST": "1"}),
    ],
    ids=["comparison", "hiring-signals", "quick", "no-patent-digest-env"],
)
def test_runs_that_never_ask_for_patents_never_read_the_company_names(
    monkeypatch, tmp_path, kwargs, env_vars,
):
    for name, value in env_vars.items():
        monkeypatch.setenv(name, value)
    wikidata = FakeWikidata()

    observed = _run(monkeypatch, tmp_path, topic="Alphabet", wikidata=wikidata, **kwargs)

    # Alphabet, Stripe and IBM each list subsidiaries; none is asked for.
    assert wikidata.calls
    assert _subsidiary_label_requests(wikidata) == []
    asked = observed.company_resolver.call_args_list
    assert asked
    assert [call.kwargs.get("names", False) for call in asked] == [False] * len(asked)
    for report in observed.reports.values():
        assert report.artifacts["resolved"]["company"]
        assert "company_names" not in report.artifacts["resolved"]
    assert observed.lane.calls == []


def test_a_company_run_reads_the_company_names_for_the_patent_lane(monkeypatch, tmp_path):
    wikidata = FakeWikidata()

    observed = _run(monkeypatch, tmp_path, topic="Alphabet", wikidata=wikidata)

    observed.company_resolver.assert_called_once_with("Alphabet", names=True)
    assert len(_subsidiary_label_requests(wikidata)) == 1
    (call,) = observed.lane.calls
    assert {"Google", "Waymo"} <= set(call["company"]["company_names"])


def test_a_company_run_with_a_host_ticker_reads_the_company_names_too(monkeypatch, tmp_path):
    observed = _run(monkeypatch, tmp_path, args=["--ticker", "MAIN:MainBrand"])

    observed.company_resolver.assert_called_once_with("MainBrand", listing=False, names=True)


def test_hosted_run_never_asks_for_patents(monkeypatch):
    lane = FakeLane()
    monkeypatch.setattr(patents, "fetch_patent_data", lane)
    monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
    monkeypatch.setattr(hosted, "run_hosted", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        cli.pipeline, "run", mock.Mock(side_effect=AssertionError("local pipeline must not run")),
    )
    monkeypatch.setattr(sys, "argv", ["last30days.py", "MainBrand"])

    assert cli.main() == 0
    assert lane.calls == []


def test_topic_with_no_company_makes_no_call(monkeypatch, tmp_path):
    observed = _run(
        monkeypatch, tmp_path, topic="Kanye West", companies={},
        # Neither the ticker's company nor the official site is the patent company.
        args=["--ticker", "MAIN:MainBrand", "--official-site", "mainbrand.example"],
    )

    assert observed.lane.calls == []
    assert "patents" not in observed.report.artifacts


def test_host_company_is_used_when_the_resolver_named_none(monkeypatch, tmp_path):
    observed = _run(
        monkeypatch, tmp_path, topic="Palmer Luckey", companies={},
        args=["--company", "Anduril:anduril.com,Oculus"],
    )

    assert [call["company"] for call in observed.lane.calls] == [{"company": "Anduril"}]
    assert observed.report.artifacts["patents"] == ARTIFACT


def test_resolved_company_wins_over_a_host_company(monkeypatch, tmp_path):
    observed = _run(monkeypatch, tmp_path, args=["--company", "Anduril"])

    assert [call["company"] for call in observed.lane.calls] == [COMPANY]


def test_person_topic_passes_the_company_they_lead(monkeypatch, tmp_path):
    led = {**COMPANY, "company_via_person": True}
    observed = _run(monkeypatch, tmp_path, topic="Jensen Huang", companies={"Jensen Huang": led})

    assert [call["company"] for call in observed.lane.calls] == [led]
    assert observed.lane.calls[0]["company"]["company_via_person"] is True


# --- the window --------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], None),
        (["--as-of", "2026-03-15", "--days", "14"], ("2026-03-01", "2026-03-15")),
    ],
    ids=["default", "past-as-of"],
)
def test_lane_window_is_the_reports_own_range(monkeypatch, tmp_path, args, expected):
    observed = _run(monkeypatch, tmp_path, args=args, real_window=True)

    (call,) = observed.lane.calls
    report = observed.report
    assert (call["date_from"], call["date_to"]) == (report.range_from, report.range_to)
    if expected:
        assert (call["date_from"], call["date_to"]) == expected


# --- what a run records ------------------------------------------------------


def test_lane_gets_the_budget_it_is_waited_for(monkeypatch, tmp_path):
    observed = _run(monkeypatch, tmp_path)

    assert observed.lane.calls[0]["budget_seconds"] == patents.LANE_BUDGET_SECONDS == 90.0


def test_lane_still_running_after_its_budget_is_dropped_without_a_trace(monkeypatch, tmp_path):
    release = threading.Event()
    monkeypatch.setattr(patents, "LANE_BUDGET_SECONDS", 0.05)
    started = time.monotonic()
    try:
        observed = _run(monkeypatch, tmp_path, lane=FakeLane(wait_for=release))
        elapsed = time.monotonic() - started
    finally:
        release.set()

    assert len(observed.lane.calls) == 1
    assert elapsed < 5
    assert "patents" not in observed.report.artifacts
    assert "patents" not in observed.report.source_status


def test_lane_thread_cannot_keep_the_process_alive(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(patents, "fetch_patent_data", FakeLane(wait_for=release))
    try:
        handle = patents.start(COMPANY, "2026-06-01", "2026-06-30", {})
        assert handle["thread"].daemon is True
    finally:
        release.set()


def test_success_logs_one_summary_line(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path)

    lines = [line for line in capsys.readouterr().err.splitlines() if "[Patents]" in line]
    assert len(lines) == 1
    assert "5 applications" in lines[0] and "2 grants" in lines[0]
    assert "3 first publications" in lines[0]
    assert "MainBrand Inc." in lines[0] and "MainBrand Licensing LLC" in lines[0]


def test_failed_lane_records_a_degraded_outcome(monkeypatch, tmp_path):
    detail = "HTTP 500: Internal Server Error"
    observed = _run(monkeypatch, tmp_path, lane=FakeLane({"state": "error", "detail": detail}))

    outcome = observed.report.source_status["patents"]
    assert (outcome.source, outcome.detail, outcome.fix_hint) == ("patents", detail, "doctor")
    assert outcome.state == cli.http.classify_failure(message=detail)
    assert "patents" not in observed.report.artifacts


def test_sign_in_wall_records_nothing_and_says_so_once(monkeypatch, tmp_path, capsys):
    observed = _run(
        monkeypatch, tmp_path,
        lane=FakeLane({"state": "signin", "detail": "USPTO Patent Public Search gave no anonymous session"}),
    )

    assert "patents" not in observed.report.source_status
    assert "patents" not in observed.report.artifacts
    lines = [line for line in capsys.readouterr().err.splitlines() if "[Patents]" in line]
    assert len(lines) == 1
    assert "sign-in" in lines[0] and "skipped" in lines[0]


def test_no_filings_records_nothing_and_names_up_to_three_other_names(monkeypatch, tmp_path, capsys):
    others = ["Mainbrand Foods", "Main Brand Co", "MainBrandt GmbH", "Fourth Name"]
    observed = _run(
        monkeypatch, tmp_path, lane=FakeLane({"state": "none", "other_names": others}),
    )

    assert "patents" not in observed.report.source_status
    assert "patents" not in observed.report.artifacts
    lines = [line for line in capsys.readouterr().err.splitlines() if "[Patents]" in line]
    assert len(lines) == 1
    assert all(name in lines[0] for name in others[:3])
    assert "Fourth Name" not in lines[0]


# --- the company lookup ------------------------------------------------------


def test_company_lookup_is_skipped_only_with_every_company_lane_excluded(monkeypatch, tmp_path):
    every = _run(
        monkeypatch, tmp_path, config={"EXCLUDE_SOURCES": "jobs, yahoo_finance, patents"},
    )
    assert every.company_resolver.call_count == 0
    assert every.lane.calls == []
    assert "company" not in every.report.artifacts["resolved"]

    two = _run(monkeypatch, tmp_path, config={"EXCLUDE_SOURCES": "jobs, yahoo_finance"})
    two.company_resolver.assert_called_once_with("MainBrand", names=True)
    assert [call["company"] for call in two.lane.calls] == [COMPANY]
    assert two.report.artifacts["patents"] == ARTIFACT


def test_company_lookup_is_skipped_when_the_remaining_lane_cannot_run(monkeypatch, tmp_path):
    # Patents is the only lane left, and this run never starts it.
    monkeypatch.setenv("LAST30DAYS_NO_PATENT_DIGEST", "1")
    observed = _run(monkeypatch, tmp_path, config={"EXCLUDE_SOURCES": "jobs, yahoo_finance"})

    assert observed.company_resolver.call_count == 0
    assert observed.lane.calls == []
