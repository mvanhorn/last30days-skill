"""Offline tests for the scheduled Reddit canary (scripts/reddit_canary.py)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import reddit_canary as canary
from lib import env, reddit_arctic

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reddit-canary.yml"
FIXTURES = ROOT / "fixtures"
NOW = datetime(2026, 11, 15, 13, 30, tzinfo=timezone.utc)

BRAVE_PAGE = (FIXTURES / "brave_reddit_serp.html").read_text(encoding="utf-8")
ARCTIC_OK = json.dumps({"data": [
    {"id": pid, "title": f"Known thread {pid}", "subreddit": "test", "score": 10,
     "num_comments": 2, "created_utc": 1740800000, "permalink": f"/r/test/comments/{pid}/x/"}
    for pid in canary.KNOWN_THREAD_IDS
]})
DDG_NO_RESULTS = (
    '<div class="no-results__container result__title"><span class="no-results">'
    '<div class="no-results__message"><h1>No more results found</h1></div></span></div>'
)


def _fetcher(pages: dict[str, tuple]):
    """A fetch stub: the first key that is a prefix of the URL answers."""
    calls: list[str] = []

    def fetch(url: str, accept: str = "text/html"):
        calls.append(url)
        for prefix, answer in pages.items():
            if url.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected fetch {url}")

    fetch.calls = calls
    return fetch


def _hydrate_ok(refs, **_kwargs):
    items = [{"engagement_source": "arctic", "top_comments": [{"id": "c1"}]} for _ in refs]
    return SimpleNamespace(items=items, arctic_state="ok", detail=f"web+arctic served {len(items)}")


def _run(pages, tmp_path, *, now=NOW, hydrate=_hydrate_ok, state_name="state.json"):
    return canary.run(
        fetch=_fetcher(pages), hydrate=hydrate, topics=("Claude Code",),
        state_file=tmp_path / state_name, now=now, sleep=lambda _s: None,
    )


BRAVE = "https://search.brave.com/"
DDG = "https://html.duckduckgo.com/"
STARTPAGE = "https://www.startpage.com/"
ARCTIC = reddit_arctic.API


def _states(report) -> dict[str, str]:
    return {check["name"]: check["state"] for check in report["checks"]}


# --- workflow static checks -------------------------------------------------


def test_workflow_never_runs_on_pull_requests_and_is_least_privilege():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request" not in triggers and "pull_request_target" not in triggers
    assert triggers["schedule"][0]["cron"]
    # Top level grants nothing; the one job reads repository contents only.
    assert workflow["permissions"] in ({}, "read-all")
    for job in workflow["jobs"].values():
        assert job.get("permissions", {}) in ({}, {"contents": "read"})
        assert job.get("timeout-minutes")


def test_workflow_pins_every_action_by_full_sha_and_runs_the_canary():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses
    for ref in uses:
        _, _, sha = ref.partition("@")
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), ref
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    runs = " ".join(step.get("run", "") for step in steps)
    assert "reddit_canary.py" in runs and "--state-file" in runs
    # The state survives a failed run so the two-day rule can see it.
    save = next(step for step in steps if step.get("uses", "").startswith("actions/cache/save@"))
    assert save["if"] == "always()"


def test_canary_is_excluded_from_install_time_scans():
    entries = (ROOT / "skills" / "last30days" / ".skillignore").read_text(encoding="utf-8").split()
    assert "scripts/reddit_canary.py" in entries


# --- classification ---------------------------------------------------------


@pytest.mark.parametrize("status,text,threads,parsed,expected", [
    (200, "<html>results</html>", 3, 3, canary.OK),
    (202, "", 0, 0, canary.CHALLENGED),
    (429, "", 0, 0, canary.CHALLENGED),
    (200, "<div>please solve this CAPTCHA</div>", 0, 0, canary.CHALLENGED),
    (200, '<div class="anomaly-modal">', 0, 0, canary.CHALLENGED),
    (200, "<html>new markup</html>", 0, 0, canary.PARSER_BROKE),
    (200, DDG_NO_RESULTS, 0, 0, canary.DOWN),
    (200, "<html>other sites</html>", 0, 4, canary.DOWN),
    (403, "Forbidden", 0, 0, canary.DOWN),
    (503, "", 0, 0, canary.DOWN),
    (None, "", 0, 0, canary.DOWN),
])
def test_classify_page(status, text, threads, parsed, expected):
    assert canary.classify_page("ddg", status, text, threads, parsed) == expected


def test_startpage_challenge_marker_is_reused_from_the_engine():
    page = '<script>var x = {"challenge":{"issuedAt":1}}</script>'
    assert canary.classify_page("startpage", 200, page, 0, 0) == canary.CHALLENGED


def test_healthy_run_passes_and_hydrates_discovered_threads(tmp_path):
    code, report = _run({
        BRAVE: (200, BRAVE_PAGE), DDG: (200, DDG_NO_RESULTS), STARTPAGE: (429, ""), ARCTIC: (200, ARCTIC_OK),
    }, tmp_path)
    states = _states(report)
    assert code == 0, report["failures"]
    assert states["brave: Claude Code"] == canary.OK
    assert states["ddg: Claude Code"] == canary.DOWN
    assert states["startpage: Claude Code"] == canary.CHALLENGED
    assert states["hydrate: Claude Code"] == canary.OK
    assert states["arctic: known ids"] == canary.OK
    assert report["no_rung_served"] is False
    assert "PASS" in canary.render(report)


def test_stubbed_200_with_zero_parseable_results_fails_as_parser_broke(tmp_path):
    code, report = _run({
        BRAVE: (200, "<html><body>a redesigned results page</body></html>"),
        DDG: (200, DDG_NO_RESULTS), STARTPAGE: (429, ""), ARCTIC: (200, ARCTIC_OK),
    }, tmp_path)
    assert code != 0
    assert _states(report)["brave: Claude Code"] == canary.PARSER_BROKE
    assert any("parser-broke" in reason for reason in report["failures"])


def test_stubbed_arctic_503_on_fixed_ids_fails_as_down(tmp_path):
    code, report = _run({
        BRAVE: (200, BRAVE_PAGE), DDG: (202, ""), STARTPAGE: (429, ""), ARCTIC: (503, ""),
    }, tmp_path)
    assert code != 0
    assert _states(report)["arctic: known ids"] == canary.DOWN
    assert any("Arctic hydration is down" in reason for reason in report["failures"])


def test_arctic_answer_without_known_threads_is_parser_broke(tmp_path):
    code, report = _run({
        BRAVE: (200, BRAVE_PAGE), DDG: (202, ""), STARTPAGE: (429, ""), ARCTIC: (200, '{"data": []}'),
    }, tmp_path)
    assert code != 0
    assert _states(report)["arctic: known ids"] == canary.PARSER_BROKE


def test_arctic_slow_down_is_challenged_not_down(tmp_path):
    code, report = _run({
        BRAVE: (200, BRAVE_PAGE), DDG: (202, ""), STARTPAGE: (429, ""), ARCTIC: (422, "slow down"),
    }, tmp_path)
    assert code == 0
    assert _states(report)["arctic: known ids"] == canary.CHALLENGED


def test_topic_hydration_down_fails(tmp_path):
    def hydrate_down(refs, **_kwargs):
        return SimpleNamespace(items=[], arctic_state="down", detail="web served 3; arctic down")

    code, report = _run({
        BRAVE: (200, BRAVE_PAGE), DDG: (202, ""), STARTPAGE: (429, ""), ARCTIC: (200, ARCTIC_OK),
    }, tmp_path, hydrate=hydrate_down)
    assert code != 0
    assert _states(report)["hydrate: Claude Code"] == canary.DOWN


def test_stubbed_202_is_challenged_and_exits_zero_on_day_one(tmp_path):
    pages = {BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK)}
    code, report = _run(pages, tmp_path)
    assert code == 0, report["failures"]
    assert {_states(report)[f"{rung}: Claude Code"] for rung in canary.RUNGS} == {canary.CHALLENGED}
    assert report["no_rung_served"] is True
    # No thread was discovered, so there is nothing to hydrate per topic; the
    # fixed-id check still covers the hydrator.
    assert "hydrate: Claude Code" not in _states(report)
    assert _states(report)["arctic: known ids"] == canary.OK

    # A same-day rerun does not count as a second day.
    code, report = _run(pages, tmp_path, now=NOW + timedelta(hours=3))
    assert code == 0
    assert report["no_serve_since"] == NOW.isoformat(timespec="seconds")

    # The next day's run, still challenged everywhere, fails.
    code, report = _run(pages, tmp_path, now=NOW + timedelta(days=1))
    assert code != 0
    assert any("two days running" in reason for reason in report["failures"])


def test_a_served_day_resets_the_challenged_streak(tmp_path):
    challenged = {BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK)}
    healthy = {**challenged, BRAVE: (200, BRAVE_PAGE)}
    assert _run(challenged, tmp_path)[0] == 0
    code, report = _run(healthy, tmp_path, now=NOW + timedelta(days=1))
    assert code == 0 and report["no_serve_since"] is None
    assert _run(challenged, tmp_path, now=NOW + timedelta(days=2))[0] == 0


def test_a_stale_previous_state_starts_a_new_streak(tmp_path):
    challenged = {BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK)}
    assert _run(challenged, tmp_path)[0] == 0
    code, _report = _run(challenged, tmp_path, now=NOW + timedelta(days=4))
    assert code == 0


def test_every_rung_down_fails_on_day_one(tmp_path):
    code, report = _run({
        BRAVE: (None, ""), DDG: (403, ""), STARTPAGE: (500, ""), ARCTIC: (200, ARCTIC_OK),
    }, tmp_path)
    assert code != 0
    assert "every discovery rung is down" in report["failures"]


def test_state_file_round_trips_and_unreadable_state_is_ignored(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{not json", encoding="utf-8")
    code, report = _run({
        BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK),
    }, tmp_path)
    assert code == 0
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["no_rung_served"] is True
    assert saved["checks"] == report["checks"]


def test_known_ids_are_asked_in_one_archive_request(tmp_path):
    fetch = _fetcher({BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK)})
    canary.run(fetch=fetch, hydrate=_hydrate_ok, topics=("Claude Code",), state_file=None,
               now=NOW, sleep=lambda _s: None)
    arctic_calls = [url for url in fetch.calls if url.startswith(ARCTIC)]
    assert arctic_calls == [f"{ARCTIC}?ids={','.join(canary.KNOWN_THREAD_IDS)}"]


def test_rung_queries_are_the_floor_query_month_filtered(tmp_path):
    fetch = _fetcher({BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK)})
    canary.run(fetch=fetch, hydrate=_hydrate_ok, topics=("Claude Code",), state_file=None,
               now=NOW, sleep=lambda _s: None)
    rung_calls = [url for url in fetch.calls if not url.startswith(ARCTIC)]
    assert len(rung_calls) == len(canary.RUNGS)
    assert all("site%3Areddit.com+Claude+Code" in url for url in rung_calls)
    assert "tf=pm" in rung_calls[0] and "df=m" in rung_calls[1] and "with_date=m" in rung_calls[2]


def test_clean_environment_drops_keys_session_and_config(monkeypatch):
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "dummy-sc-key")
    monkeypatch.setenv(env.REDDIT_SESSION_COOKIE_VAR, "dummy-cookie")
    monkeypatch.setenv("REDDIT_BROWSER_CONSENT", "true")
    for key in canary.CLEAN_ENV:
        monkeypatch.setenv(key, os.environ.get(key, "placeholder"))
    monkeypatch.setattr(env, "CONFIG_DIR", Path("/tmp/should-not-be-used"))
    monkeypatch.setattr(env, "CONFIG_FILE", Path("/tmp/should-not-be-used/.env"))
    canary.clean_environment()
    assert "SCRAPECREATORS_API_KEY" not in os.environ
    assert env.REDDIT_SESSION_COOKIE_VAR not in os.environ
    assert os.environ["REDDIT_BROWSER_CONSENT"] == "false"
    assert os.environ["FROM_BROWSER"] == "off"
    assert env.CONFIG_DIR is None and env.CONFIG_FILE is None
    allowed, _why = env.reddit_session_gate(env.get_config())
    assert allowed is False


def test_main_writes_report_and_step_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(canary, "clean_environment", lambda: None)
    monkeypatch.setattr(canary, "http_fetch", _fetcher({
        BRAVE: (202, ""), DDG: (202, ""), STARTPAGE: (202, ""), ARCTIC: (200, ARCTIC_OK),
    }))
    monkeypatch.setattr(canary, "TOPICS", ("Claude Code",))
    monkeypatch.setattr(canary, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(canary.time, "sleep", lambda _s: None)
    # run() binds its defaults at definition time; route main through stubs.
    real_run = canary.run
    monkeypatch.setattr(canary, "run", lambda **kw: real_run(
        fetch=canary.http_fetch, hydrate=_hydrate_ok, topics=canary.TOPICS,
        now=NOW, sleep=lambda _s: None, **kw,
    ))
    report_path = tmp_path / "out" / "report.json"
    code = canary.main(["--state-file", str(tmp_path / "state.json"), "--report", str(report_path)])
    assert code == 0
    assert json.loads(report_path.read_text())["checks"]
    assert "| arctic: known ids | ok |" in summary.read_text()
