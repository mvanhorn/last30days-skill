"""Exercise strict child results through the watchlist and its database."""

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

import last30days as cli
import store
import watchlist
from lib import library_index, schema


def _install_child(tmp_path, monkeypatch, results):
    (tmp_path / "last30days.py").write_text(
        f"import sys\nstdout, stderr, exit_code = {results!r}[sys.argv[-1]]\n"
        "sys.stdout.write(stdout)\nsys.stderr.write(stderr)\nraise SystemExit(exit_code)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(watchlist, "SCRIPT_DIR", tmp_path)


@pytest.fixture
def watchlist_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    topic = store.add_topic("Example")
    item = schema.SourceItem(
        item_id="r1", source="reddit", title="A useful finding",
        body="Evidence from Reddit", url="https://www.reddit.com/r/example/comments/r1/",
    )
    report = schema.Report(
        topic=topic["name"],
        range_from="2026-04-01",
        range_to="2026-07-01",
        generated_at="2026-07-01T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("local", "local", "local"),
        query_plan=schema.QueryPlan(
            "general", "recent", "story", topic["name"], [], {},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"reddit": [item]},
        errors_by_source={"x": "rate-limited"},
        source_status={
            "reddit": schema.SourceOutcome("reddit", "ok", items_returned=1),
            "x": schema.SourceOutcome("x", "rate-limited"),
        },
    )
    args = cli.build_parser().parse_args([
        "--emit=json", "--json-profile=raw", "--save-dir", "", topic["name"],
    ])
    assert cli._render_save_and_print(args, report, None, None, {"LAST30DAYS_STRICT_EXIT": "1"}) == 3
    output = capsys.readouterr()
    return topic, item, output.out, output.err


@pytest.mark.parametrize(
    ("case", "exit_code", "want_status"),
    [
        ("normal", 0, "completed"),
        ("degraded", 3, "degraded"),
        ("clarification", 3, "failed"),
        ("unrelated-code-three", 3, "failed"),
        ("invalid-report", 3, "failed"),
        ("hard-failure", 1, "failed"),
    ],
)
def test_watchlist_persists_only_completed_reports(
    tmp_path, monkeypatch, watchlist_report, case, exit_code, want_status,
):
    topic, item, raw_report, receipt = watchlist_report
    notice = Mock()
    monkeypatch.setattr(watchlist, "_send_generic_webhook", notice)
    store.set_setting("delivery_channel", "https://webhook.example/notify")

    stdout = {
        "clarification": "",
        "invalid-report": "{}",
    }.get(case, raw_report)
    stderr = (
        receipt if case in {"degraded", "invalid-report", "hard-failure"}
        else "Choose what to research\n" if exit_code else ""
    )
    _install_child(tmp_path, monkeypatch, {topic["name"]: (stdout, stderr, exit_code)})
    result = watchlist._run_topic(topic)

    assert result["status"] == want_status
    assert store.list_topics()[0]["last_status"] == want_status
    findings = store.get_new_findings(topic["id"])
    if want_status == "failed":
        assert findings == []
        notice.assert_not_called()
        return

    assert result["new"] == 1
    assert [finding["source_url"] for finding in findings] == [item.url]
    matches = library_index.search(
        "Evidence from Reddit",
        db_path=tmp_path / "library.db",
        store_db_path=tmp_path / "research.db",
    )
    assert [(match.topic, match.url) for match in matches] == [(topic["name"], item.url)]
    notice.assert_called_once()
    assert ("degraded coverage" in notice.call_args.args[1]) == (want_status == "degraded")


def test_late_delivery_error_does_not_reclassify_report(tmp_path, monkeypatch, watchlist_report):
    topic, item, raw_report, receipt = watchlist_report
    _install_child(tmp_path, monkeypatch, {topic["name"]: (raw_report, receipt, 3)})
    notice = Mock(side_effect=ValueError("test delivery error"))
    monkeypatch.setattr(watchlist, "_deliver_findings", notice)

    with pytest.raises(ValueError, match="test delivery error"):
        watchlist._run_topic(topic)

    assert store.list_topics()[0]["last_status"] == "degraded"
    assert [finding["source_url"] for finding in store.get_new_findings(topic["id"])] == [item.url]


@pytest.mark.parametrize("use_fallback", [False, True], ids=["sqlite-default", "sqlite-fallback"])
def test_watchlist_alerts_each_topic_once_for_a_shared_url(
    tmp_path, monkeypatch, capsys, watchlist_report, use_fallback,
):
    if use_fallback:
        monkeypatch.setattr(store, "_SUPPORTS_RETURNING", False)
    first, item, raw_report, _ = watchlist_report
    second = store.add_topic("Later topic")
    second_report = json.loads(raw_report)
    second_report["topic"] = second["name"]
    second_report["query_plan"]["raw_topic"] = second["name"]
    _install_child(tmp_path, monkeypatch, {
        first["name"]: (raw_report, "", 0),
        second["name"]: (json.dumps(second_report), "", 0),
    })
    store.set_setting("delivery_channel", "https://webhook.example/notify")
    notice = Mock()
    monkeypatch.setattr(watchlist, "_send_generic_webhook", notice)

    watchlist.cmd_run_all(None)
    results = json.loads(capsys.readouterr().out)["results"]
    names = [first["name"], second["name"]]
    assert [(r["topic"], r["new"], r["updated"]) for r in results] == [
        (name, 1, 0) for name in names
    ]
    assert notice.call_count == 2
    for name, alert in zip(names, notice.call_args_list, strict=True):
        assert f"update: {name}" in alert.args[1]

    watchlist.cmd_run_all(None)
    results = json.loads(capsys.readouterr().out)["results"]
    assert [(r["topic"], r["new"], r["updated"]) for r in results] == [
        (name, 0, 1) for name in names
    ]
    assert notice.call_count == 2
    assert store.get_stats()["total_findings"] == 1
    for topic in (first, second):
        runs = store.get_latest_completed_runs(topic["id"])
        assert [(run["findings_new"], run["findings_updated"]) for run in runs] == [(0, 1), (1, 0)]
        assert [finding["source_url"] for finding in store.get_new_findings(topic["id"])] == [item.url]


def test_degraded_comparison_survives_invalid_webhook_and_batch_continues(
    tmp_path, monkeypatch, capsys, watchlist_report,
):
    topic, item, raw_report, _ = watchlist_report
    later = store.add_topic("Later topic")
    report = schema.report_from_dict(json.loads(raw_report))
    peer = replace(item, item_id="r2", url="https://www.reddit.com/r/example/comments/r2/")
    clean_report = replace(
        report, topic=later["name"], items_by_source={"reddit": [peer]},
        errors_by_source={}, source_status={},
    )
    args = cli.build_parser().parse_args([
        "--emit=json", "--json-profile=raw", "--save-dir", "", topic["name"],
    ])
    assert cli._render_save_and_print(
        args, clean_report, [(report.topic, report), (clean_report.topic, clean_report)],
        None, {"LAST30DAYS_STRICT_EXIT": "1"},
    ) == 3
    comparison = capsys.readouterr()
    _install_child(tmp_path, monkeypatch, {
        topic["name"]: (comparison.out, comparison.err, 3),
        later["name"]: (cli.emit_output(clean_report, "json", json_profile="raw"), "", 0),
    })
    invalid_channel = "https://[::1/unusable"
    store.set_setting("delivery_channel", invalid_channel)
    post = Mock()
    monkeypatch.setattr(watchlist.http, "post", post)

    watchlist.cmd_run_all(None)

    output = capsys.readouterr()
    results = json.loads(output.out)["results"]
    assert [(r["topic"], r["status"], r["new"], r["updated"]) for r in results] == [
        (topic["name"], "degraded", 2, 0),
        (later["name"], "completed", 1, 0),
    ]
    assert [(row["name"], row["last_status"]) for row in store.list_topics()] == [
        (topic["name"], "degraded"), (later["name"], "completed"),
    ]
    assert {row["source_url"] for row in store.get_new_findings(topic["id"])} == {item.url, peer.url}
    assert [row["source_url"] for row in store.get_new_findings(later["id"])] == [peer.url]
    assert output.err.splitlines() == ["Delivery failed: ValueError"] * 2
    assert invalid_channel not in output.out
    post.assert_not_called()
