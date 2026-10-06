"""Offline regression checks for the opt-in manual engine comparison."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


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
