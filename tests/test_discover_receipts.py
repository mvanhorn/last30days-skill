import json
import time

import pytest

import last30days as cli
from lib import discovery_handoff, health, pipeline, render, schema


def _args(directory, *extra):
    return cli.build_parser().parse_args([
        "--discover", "AI agents", "--search", "x", "--discover-shallow",
        "--save-dir", str(directory), *extra,
    ])


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("discard", [False, True])
def test_sweep_warning_survives_saved_protocol_round_trip(tmp_path, monkeypatch, capsys, legacy, discard):
    warning = "X: xapi window truncated to 7 days"
    config = {
        "LAST30DAYS_X_BACKEND": "xapi", "X_BEARER_TOKEN": "dummy",
        "LAST30DAYS_STORE": str(tmp_path / "research.db"),
    }
    calls = []

    def search(_token, topic, from_date, to_date, **kwargs):
        calls.append(topic)
        result = {"items": [{
            "id": "1", "text": f"{topic}: Agent memory protocols improve local runtimes",
            "url": "https://x.com/example/status/1", "author_handle": "example",
            "date": to_date, "engagement": {"likes": 900, "reposts": 100},
            "relevance": 1.0,
        }]}
        if len(calls) == 1:
            result["warning"] = "window truncated to 7 days"
        return result

    monkeypatch.setattr(pipeline.x_api, "search_x", search)
    monkeypatch.setattr(pipeline.x_api, "search_handles", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.x_api, "search_mentions", lambda *a, **k: [])
    runtime = schema.ProviderRuntime(
        reasoning_provider="native", planner_model="", rerank_model="",
        x_search_backend="xapi",
    )
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a: (runtime, None))

    assert cli._run_discover_protocol_leg(_args(tmp_path, "--nominate-only"), config) == 0
    capsys.readouterr()
    bundle_path = tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert payload["nominations"]
    assert payload["warnings"] == [warning]
    if legacy:
        payload.pop("warnings")
        bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    expected = [] if legacy else [warning]
    bundle = discovery_handoff.read_nominations_bundle(save_dir=tmp_path)
    assert bundle.schema_version == "1.0"
    assert bundle.warnings == expected
    assert bundle.source_status["x"].state == "ok"

    judgments = tmp_path / "judgments.json"
    judgments.write_text(json.dumps({
        "bundle_id": bundle.bundle_id,
        "judgments": [{"id": row.nomination_id, "junk": discard, "worthiness": 90}
                      for row in bundle.nominations],
    }), encoding="utf-8")
    assert cli._run_discover_protocol_leg(_args(tmp_path, "--judgments", str(judgments)), config) == 0
    resumed = capsys.readouterr()
    if discard:
        assert "Nothing solid this window." in resumed.out
        assert resumed.out.count(warning) == len(expected)
        assert not (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).exists()
        return
    assert len(calls) > 1
    pending = discovery_handoff.read_pending_report(save_dir=tmp_path)
    assert pending.report["topics"]
    assert pending.report["warnings"].count(warning) == len(expected)
    assert pending.report["source_status"]["x"]["state"] == "ok"

    assert cli._run_discover_protocol_leg(_args(tmp_path, "--finalize"), config) == 0
    assert capsys.readouterr().out.count(warning) == len(expected)
    saved = list(tmp_path.glob("*.md"))
    assert saved
    assert saved[0].read_text(encoding="utf-8").count(warning) == len(expected)


@pytest.mark.parametrize("outcome", ["budget-stop", "complete", "first-failure"])
def test_empty_nomination_report_preserves_bird_coverage(monkeypatch, outcome):
    now = [1000.0]
    calls = []
    config = {
        "LAST30DAYS_X_BACKEND": "bird", "AUTH_TOKEN": "dummy", "CT0": "dummy",
        "_research_deadline": 1005.0,
    }

    def search(query, count, timeout, deadline=None, **kwargs):
        calls.append(query)
        if outcome != "complete":
            now[0] = deadline - 0.5
        if outcome == "first-failure":
            return {"items": [], "error": "Invalid JSON response (anti-bot interstitial)"}
        return {"items": []}

    monkeypatch.setattr(pipeline.bird_x, "_run_bird_search", search)
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    result = pipeline.run_discover_nominate(
        domain="Widget compiler development", config=config, depth="quick", requested_sources=["x"],
    )
    assert result.pool == []
    report = pipeline.nominate_nothing_solid_report(result)
    compact = render.render_discovery(report)
    receipts = [warning for warning in report.warnings if "search budget" in warning]
    if outcome == "budget-stop":
        assert len(calls) == 1
        assert report.source_status["x"].state == schema.NO_RESULTS
        assert receipts == [
            "X: bird Partial coverage: optional zero-result retries skipped because the search budget was exhausted."
        ]
        assert receipts[0] in compact
    elif outcome == "complete":
        assert len(calls) > 1
        assert report.source_status["x"].state == schema.NO_RESULTS
        assert receipts == []
    else:
        assert len(calls) == 1
        assert report.source_status["x"].state == health.SCHEMA_DRIFT
        assert "Invalid JSON response" in report.source_status["x"].detail
        assert "Some discovery sources degraded: x." in report.warnings
        assert receipts == []
