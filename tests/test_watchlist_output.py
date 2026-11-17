import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest

import last30days as cli
import store
import watchlist
from lib import hosted, schema


@pytest.fixture
def watch_store(tmp_path):
    with store.scoped_db(tmp_path / "watchlist.db"):
        yield


def _report(topic, *urls):
    return schema.Report(
        topic=topic,
        range_from="2026-09-01",
        range_to="2026-11-30",
        generated_at="2026-11-30T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("local", "local", "local"),
        query_plan=schema.QueryPlan("overview", "recent", "themes", topic, [], {}),
        clusters=[],
        ranked_candidates=[],
        items_by_source={
            "hackernews": [
                schema.SourceItem(url, "hackernews", f"{topic} finding", topic, url)
                for url in urls
            ],
        },
        errors_by_source={},
    )


def _comparison():
    first = _report("Alpha", "https://example.test/alpha", "https://example.test/shared")
    second = _report("Beta", "https://example.test/beta", "https://example.test/shared")
    return cli.emit_comparison_output(
        [("Alpha", first), ("Beta", second)], "json", json_profile="raw",
    )


def test_watchlist_persists_both_entities_under_the_original_topic(watch_store, monkeypatch):
    topic = store.add_topic("Weekly comparison", search_queries=["Alpha vs Beta"])
    output = _comparison()
    monkeypatch.setattr(
        watchlist.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=output, stderr=""),
    )

    result = watchlist._run_topic(topic)

    assert result["status"] == "completed"
    assert result["new"] == 3
    assert result["updated"] == 0
    findings = store.get_new_findings(topic["id"])
    assert {row["source_url"]: row["content"] for row in findings} == {
        "https://example.test/alpha": "Alpha",
        "https://example.test/beta": "Beta",
        "https://example.test/shared": "Alpha",
    }
    assert [row["name"] for row in store.list_topics()] == ["Weekly comparison"]
    assert len(store.get_latest_completed_runs(topic["id"])) == 1


@pytest.mark.parametrize(
    "invalid_output",
    [
        "non-object", "missing reports", "bad peer", "malformed finding",
        "invalid Unicode title", "invalid Unicode report topic", "invalid Unicode entity name",
    ],
)
def test_invalid_child_output_fails_one_run_and_lets_next_topic_complete(
    watch_store, monkeypatch, capsys, invalid_output,
):
    bad = store.add_topic("First")
    good = store.add_topic("Second")
    if invalid_output in ("bad peer", "invalid Unicode entity name"):
        comparison = json.loads(_comparison())
        peer = comparison["reports"][1]
        if invalid_output == "bad peer":
            del peer["report"]["topic"]
        else:
            peer["entity"] = "\ud800"
        invalid = json.dumps(comparison)
    elif invalid_output == "missing reports":
        invalid = '{"comparison": true}'
    elif invalid_output in ("malformed finding", "invalid Unicode title", "invalid Unicode report topic"):
        report = schema.to_dict(_report(bad["name"], "https://example.test/bad"))
        item = report["items_by_source"]["hackernews"][0]
        if invalid_output == "malformed finding":
            item["title"] = {"text": "not a title"}
        elif invalid_output == "invalid Unicode title":
            item["title"] = "\ud800"
        else:
            report["topic"] = "\ud800"
        invalid = json.dumps(report)
    else:
        invalid = "[]"
    valid = cli.emit_output(
        _report(good["name"], "https://example.test/café"),
        "json",
        json_profile="raw",
    )
    outputs = iter([invalid, valid])
    monkeypatch.setattr(
        watchlist.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout=next(outputs), stderr=""),
    )

    watchlist.cmd_run_all(None)

    results = json.loads(capsys.readouterr().out)["results"]
    assert [(item["topic"], item["status"]) for item in results] == [
        ("First", "failed"), ("Second", "completed"),
    ]
    assert "Invalid research output" in results[0]["error"]
    assert store.list_topics()[0]["last_status"] == "failed"
    assert store.get_new_findings(bad["id"]) == []
    assert [row["source_url"] for row in store.get_new_findings(good["id"])] == [
        "https://example.test/café",
    ]


def test_watchlist_reports_unsupported_hosted_backend_before_submission(watch_store, monkeypatch):
    topic = store.add_topic("Example research")
    submitted = []
    monkeypatch.setenv("LAST30DAYS_API_KEY", "DUMMY_TEST_KEY")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://example.test")

    def submit(query, depth, register="default"):
        submitted.append(query)
        return {"needs_clarification": True, "question": "Unexpected submission"}

    def run_engine(argv, *, env, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        with monkeypatch.context() as child:
            child.setattr(sys, "argv", argv[1:])
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main()
        return subprocess.CompletedProcess(argv, code, stdout=stdout.getvalue(), stderr=stderr.getvalue())

    monkeypatch.setattr(hosted, "submit", submit)
    monkeypatch.setattr(watchlist.subprocess, "run", run_engine)

    result = watchlist._run_topic(topic)

    assert submitted == []
    assert result["status"] == "failed"
    assert "Watchlist requires structured local reports" in result["error"]
    assert "LAST30DAYS_API_BASE" in result["error"]
    assert store.list_topics()[0]["last_status"] == "failed"
    assert store.get_daily_unknown_cost_runs() == 0
