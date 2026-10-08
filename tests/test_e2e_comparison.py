"""Offline regression checks for the opt-in manual engine comparison."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import pipeline, schema


def build_report(topic, source_counts, *, errors=None):
    items_by_source = {
        source: [schema.SourceItem(
            item_id=f"{topic}-{source}-{index}", source=source, title=topic,
            body="fixture evidence", url=f"https://example.invalid/{source}/{index}",
        ) for index in range(count)]
        for source, count in source_counts.items()
    }
    candidates = [schema.Candidate(
        candidate_id=item.item_id, item_id=item.item_id, source=source,
        title=item.title, url=item.url, snippet=item.body,
        subquery_labels=[topic], native_ranks={source: index + 1},
        local_relevance=0.8, freshness=1, engagement=1, source_quality=0.8,
        rrf_score=0.1,
    ) for source, items in items_by_source.items() for index, item in enumerate(items)]
    return schema.Report(
        topic=topic, range_from="2026-09-06", range_to="2026-10-06",
        generated_at="2026-10-06T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("local", "fixture", "fixture"),
        query_plan=schema.QueryPlan(
            intent="concept", freshness_mode="balanced_recent", cluster_mode="topic",
            raw_topic=topic,
            subqueries=[schema.SubQuery(topic, topic, topic, list(source_counts))],
            source_weights={source: 1.0 for source in source_counts},
        ),
        clusters=[schema.Cluster(
            cluster_id=topic, title=topic,
            candidate_ids=[candidate.candidate_id for candidate in candidates],
            representative_ids=[candidates[0].candidate_id] if candidates else [],
            sources=list(source_counts), score=0.8,
        )],
        ranked_candidates=candidates, items_by_source=items_by_source,
        errors_by_source=errors or {},
        source_status={source: schema.SourceOutcome(
            source=source, state="ok" if items else "no-results", items_returned=len(items),
        ) for source, items in items_by_source.items()},
    )


def load_comparison_module():
    path = Path(__file__).with_name("e2e_comparison.py")
    spec = importlib.util.spec_from_file_location("e2e_comparison_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_current_engine_uses_canonical_path():
    module = load_comparison_module()
    assert Path(module.V3_SCRIPT) == (
        Path(__file__).resolve().parents[1]
        / "skills" / "last30days" / "scripts" / "last30days.py"
    )
    assert Path(module.V3_SCRIPT).is_file()


@pytest.mark.parametrize("source_error", [False, True])
def test_comparison_output_aggregates_nested_reports_and_errors(tmp_path, capsys, source_error):
    module = load_comparison_module()
    reports = [
        ("alpha", build_report("alpha", {"reddit": 2, "web": 1})),
        ("beta", build_report("beta", {"reddit": 1, "x": 0},
                              errors={"x": "fixture timeout"} if source_error else None)),
    ]
    if source_error:
        reports[1][1].source_status["x"] = schema.SourceOutcome(source="x", state="timeout")
    payload = cli.emit_comparison_output(reports, "json", json_profile="raw")
    result = subprocess.CompletedProcess([], 0, payload, "")
    reference = tmp_path / "reference.py"
    reference.touch()
    with mock.patch.object(module.subprocess, "run", return_value=result), mock.patch.object(
        module, "QUERIES", [("alpha vs beta", "comparison")]
    ), mock.patch.object(sys, "argv", ["e2e_comparison", "--v2-script", str(reference)]):
        metrics = module.run_query("fixture.py", "alpha vs beta")
        assert metrics["sources"] == 3
        assert metrics["total_items"] == 4
        assert metrics["candidates"] == 4
        assert metrics["clusters"] == 2
        assert metrics["intent"] == "comparison"
        assert metrics["subqueries"] == 2
        assert metrics["errors"] == (["beta: x"] if source_error else [])
        assert module.main() == int(source_error)
    output = capsys.readouterr().out
    assert "| Total items retrieved | 4 | 4 | +0 |" in output
    assert "| Total sources with items | 3 | 3 | +0 |" in output
    assert f"Outcome: {'partial' if source_error else 'passed'}" in output
    if source_error:
        assert "beta: x" in output


@pytest.mark.parametrize("comparison", [False, True])
@pytest.mark.parametrize("state", [
    "partial", "rate-limited", "auth-failed", "payment-required",
    "unreachable", "timeout", "schema-drift", "error",
])
def test_source_status_failures_are_partial_without_legacy_errors(tmp_path, capsys, comparison, state):
    module = load_comparison_module()
    report = build_report("alpha", {"reddit": 1 if state in {"partial", "auth-failed"} else 0, "web": 1})
    bundle = schema.RetrievalBundle()
    for source, items in report.items_by_source.items():
        bundle.add_items("alpha", source, items)
    bundle.record_failure("reddit", state, "fixture retrieval failure")
    report.source_status = pipeline._finalize_source_status(bundle.source_status, report.items_by_source)
    report.warnings = pipeline._warnings(
        report.items_by_source, report.ranked_candidates, {}, {"reddit": "fixture retrieval failure"},
    )
    assert report.errors_by_source == {}
    assert report.source_status["reddit"].state == state
    payload = (
        cli.emit_comparison_output([("alpha", report)], "json", json_profile="raw")
        if comparison else json.dumps(schema.to_dict(report))
    )
    result = subprocess.CompletedProcess([], 0, payload, "")
    reference = tmp_path / "reference.py"
    reference.touch()
    with mock.patch.object(module.subprocess, "run", return_value=result), mock.patch.object(
        module, "QUERIES", [("fixture topic", "concept")]
    ), mock.patch.object(sys, "argv", ["e2e_comparison", "--v2-script", str(reference)]):
        metrics = module.run_query("fixture.py", "fixture topic")
        assert metrics["errors"] == (["alpha: reddit"] if comparison else ["reddit"])
        assert module.main() == 1
    output = capsys.readouterr().out
    assert "| Source errors | 1 | 1 | +0 |" in output
    assert "Outcome: partial" in output


@pytest.mark.parametrize("state", ["ok", "no-results", "skipped-unconfigured"])
def test_benign_source_status_and_warnings_remain_successful(tmp_path, capsys, state):
    module = load_comparison_module()
    report = build_report("alpha", {"reddit": 1 if state == "ok" else 0, "web": 1})
    report.source_status["reddit"] = schema.SourceOutcome(
        source="reddit", state=state, items_returned=len(report.items_by_source["reddit"]),
        attempted=state != "skipped-unconfigured", detail="Informational coverage note",
        lane_failure_state="timeout" if state == "ok" else None,
    )
    report.warnings = ["Evidence is thin for this topic.", "Informational coverage note"]
    result = subprocess.CompletedProcess([], 0, json.dumps(schema.to_dict(report)), "")
    reference = tmp_path / "reference.py"
    reference.touch()
    with mock.patch.object(module.subprocess, "run", return_value=result), mock.patch.object(
        module, "QUERIES", [("fixture topic", "concept")]
    ), mock.patch.object(sys, "argv", ["e2e_comparison", "--v2-script", str(reference)]):
        assert module.main() == 0
    output = capsys.readouterr().out
    assert "| Source errors | 0 | 0 | +0 |" in output
    assert "Outcome: passed" in output


def test_source_error_is_counted_once_when_both_channels_report_it():
    module = load_comparison_module()
    report = build_report("alpha", {"reddit": 0, "web": 1}, errors={"reddit": "fixture timeout"})
    report.source_status["reddit"] = schema.SourceOutcome(source="reddit", state="timeout")
    result = subprocess.CompletedProcess([], 0, json.dumps(schema.to_dict(report)), "")
    with mock.patch.object(module.subprocess, "run", return_value=result):
        assert module.run_query("fixture.py", "fixture topic")["errors"] == ["reddit"]


def test_legacy_v2_success_retains_source_and_item_counts():
    module = load_comparison_module()
    payload = {"mode": "legacy", "reddit": [{"id": "one"}, {"id": "two"}], "web": [{"id": "three"}]}
    result = subprocess.CompletedProcess([], 0, json.dumps(payload), "")
    with mock.patch.object(module.subprocess, "run", return_value=result):
        metrics = module.run_query("fixture.py", "fixture topic")
    assert metrics["sources"] == 2
    assert metrics["total_items"] == 3
    assert metrics["candidates"] == 3
    assert metrics["intent"] == "legacy"
    assert metrics["errors"] == []


@pytest.mark.parametrize("comparison", [False, True])
def test_process_exits_nonzero_for_partial_source_status(tmp_path, comparison):
    report = build_report("alpha", {"reddit": 1})
    bundle = schema.RetrievalBundle()
    bundle.add_items("alpha", "reddit", report.items_by_source["reddit"])
    bundle.record_failure("reddit", "rate-limited", "fixture retrieval failure")
    report.source_status = pipeline._finalize_source_status(bundle.source_status, report.items_by_source)
    payload = (
        cli.emit_comparison_output([("alpha", report)], "json", json_profile="raw")
        if comparison else json.dumps(schema.to_dict(report))
    )
    runner = tmp_path / "comparison_runner.py"
    tool = Path(__file__).with_name("e2e_comparison.py").resolve()
    reference = tmp_path / "reference.py"
    reference.touch()
    runner.write_text(
        "import runpy, subprocess\n"
        "from unittest.mock import patch\n"
        f"result = subprocess.CompletedProcess([], 0, {payload!r}, '')\n"
        "with patch('subprocess.run', return_value=result):\n"
        f"    runpy.run_path({str(tool)!r}, run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, str(runner), "--v2-script", str(reference)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1, result.stderr
    assert "Outcome: partial" in result.stdout
    expected_source = "alpha: reddit" if comparison else "reddit"
    assert expected_source in result.stdout


@pytest.mark.parametrize("source_errors,expected_exit,outcome", [
    ({}, 0, "passed"), ({"reddit": "rate-limited"}, 1, "partial"),
])
def test_reports_source_outcomes(tmp_path, capsys, source_errors, expected_exit, outcome):
    module = load_comparison_module()
    engine = tmp_path / "fixture_engine.py"
    payload = {
        "query_plan": {"intent": "concept", "subqueries": [{}]},
        "items_by_source": {"reddit": [{"id": "fixture"}]},
        "ranked_candidates": [{"id": "fixture"}],
        "errors_by_source": source_errors,
    }
    engine.write_text("print(" + repr(json.dumps(payload)) + ")\n")
    with mock.patch.object(module, "V3_SCRIPT", str(engine)), mock.patch.object(
        module, "QUERIES", [("fixture topic", "concept")]
    ), mock.patch.object(sys, "argv", ["e2e_comparison", "--v2-script", str(engine)]):
        assert module.main() == expected_exit
    output = capsys.readouterr().out
    assert "| Total items retrieved | 1 | 1 | +0 |" in output
    assert "| Command errors | 0 | 0 | +0 |" in output
    assert f"| Source errors | {len(source_errors)} | {len(source_errors)} | +0 |" in output
    assert f"Outcome: {outcome}" in output


@pytest.mark.parametrize("failed_engines,outcome", [(2, "failed"), (1, "partial")])
def test_command_failures_are_visible_and_fail(tmp_path, capsys, failed_engines, outcome):
    module = load_comparison_module()
    reference = tmp_path / "reference.py"
    reference.touch()
    failure = subprocess.CompletedProcess([], 3, "", "engine-failure-sentinel")
    success = subprocess.CompletedProcess([], 0, '{"reddit":[{"id":"fixture"}]}', "")
    outcomes = [failure, failure if failed_engines == 2 else success]
    with mock.patch.object(module.subprocess, "run", side_effect=outcomes) as run, mock.patch.object(
        module, "QUERIES", [("fixture topic", "concept")]
    ), mock.patch.object(sys, "argv", ["e2e_comparison", "--v2-script", str(reference)]):
        assert module.main() == 1
    assert run.call_count == 2
    output = capsys.readouterr().out
    other_errors = failed_engines - 1
    assert f"| Command errors | 1 | {other_errors} | {1 - other_errors:+d} |" in output
    assert "engine-failure-sentinel" in output
    assert f"Outcome: {outcome}" in output


@pytest.mark.parametrize("result,expected_error", [
    (subprocess.CompletedProcess([], 4, "", ""), "engine exited with status 4"),
    (subprocess.CompletedProcess([], 0, "not-json", ""), "Expecting value"),
    (subprocess.CompletedProcess([], 0, '{"comparison":true,"reports":[]}', ""), "comparison output contains no reports"),
    (subprocess.TimeoutExpired("fixture", 7), "timeout"),
])
def test_invalid_output_and_timeout_are_command_errors(result, expected_error):
    module = load_comparison_module()
    behavior = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
    with mock.patch.object(module.subprocess, "run", **behavior) as run:
        report = module.run_query("fixture.py", "fixture topic", timeout=7)
    assert expected_error in report["error"]
    assert report["sources"] == 0
    assert report["candidates"] == 0
    assert run.call_args.kwargs["timeout"] == 7


def test_process_exits_nonzero_when_engines_fail(tmp_path):
    runner = tmp_path / "comparison_runner.py"
    tool = Path(__file__).with_name("e2e_comparison.py").resolve()
    reference = tmp_path / "reference.py"
    reference.touch()
    runner.write_text(
        "import runpy, subprocess\n"
        "from unittest.mock import patch\n"
        "failure = subprocess.CompletedProcess([], 3, '', 'fixture-failure')\n"
        "with patch('subprocess.run', return_value=failure):\n"
        f"    runpy.run_path({str(tool)!r}, run_name='__main__')\n"
    )
    result = subprocess.run(
        [sys.executable, str(runner), "--v2-script", str(reference)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1, result.stderr
    assert "Outcome: failed" in result.stdout
