"""Timeout architecture: shared X-chain deadline and bounded stream waits."""

import queue
import threading
import time
from unittest import mock

import pytest

from lib import health, http, pipeline, schema, xai_x


def test_stream_timeouts_are_bounded():
    for name in (
        "STREAM_FUTURE_TIMEOUT_SECONDS",
        "DISCOVERY_FUTURE_TIMEOUT_SECONDS",
        "THIN_RETRY_FUTURE_TIMEOUT_SECONDS",
        "X_CHAIN_DEADLINE_SECONDS",
    ):
        value = getattr(pipeline, name)
        assert 0 < value <= 600, f"{name}={value} must be a finite wall-clock bound"


def test_fetch_x_backend_forwards_deadline(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        pipeline.grok_x, "search_x",
        lambda *a, **k: (seen.update(grok=k.get("deadline")), {"items": []})[1],
    )
    monkeypatch.setattr(
        pipeline.xai_x, "search_x",
        lambda *a, **k: (seen.update(xai=k.get("deadline_monotonic")), {"items": []})[1],
    )
    monkeypatch.setattr(pipeline.xai_x, "parse_x_response", lambda r: [])
    monkeypatch.setattr(
        pipeline.xquik, "search_xquik",
        lambda *a, **k: (seen.update(xquik=k.get("deadline")), {"items": []})[1],
    )
    monkeypatch.setattr(pipeline.xquik, "parse_xquik_response", lambda r: [])
    monkeypatch.setattr(
        pipeline.x_api, "search_x",
        lambda *a, **k: (seen.update(xapi=k.get("deadline")), {"items": []})[1],
    )
    monkeypatch.setattr(
        pipeline.bird_x, "search_x",
        lambda *a, **k: (seen.update(bird=k.get("deadline")), {"items": [], "error": ""})[1],
    )
    monkeypatch.setattr(pipeline.bird_x, "parse_bird_response", lambda *a, **k: [])
    monkeypatch.setattr(
        pipeline.xurl_x, "search_x",
        lambda *a, **k: (seen.update(xurl=k.get("deadline")), {"items": [], "error": ""})[1],
    )
    monkeypatch.setattr(pipeline.xurl_x, "parse_x_response", lambda *a, **k: [])
    deadline = time.monotonic() + 90
    cfg = {"XAI_API_KEY": "k", "X_BEARER_TOKEN": "b"}
    monkeypatch.setattr(pipeline.env, "get_xquik_token", lambda c: "q")
    pipeline._fetch_x_backend("grok", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["grok"] == deadline
    pipeline._fetch_x_backend("xai", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["xai"] == deadline
    pipeline._fetch_x_backend("xquik", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["xquik"] == deadline
    pipeline._fetch_x_backend("xapi", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["xapi"] == deadline
    pipeline._fetch_x_backend("bird", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["bird"] == deadline
    pipeline._fetch_x_backend("xurl", "q", "2026-08-01", "2026-08-31", "quick", cfg, deadline=deadline)
    assert seen["xurl"] == deadline


def test_x_chain_skips_past_deadline(monkeypatch):
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xapi", "xquik"])
    calls = []
    monkeypatch.setattr(
        pipeline, "_fetch_x_backend",
        lambda *a, **k: (calls.append(a[0]), ([], "boom"))[1],
    )
    # First monotonic() call sets the deadline, the second (chain check) is past it.
    monkeypatch.setattr(
        pipeline.time, "monotonic",
        mock.Mock(side_effect=[1000.0, 2000.0, 2000.0]),
    )
    plan = mock.Mock()
    plan.domain = "topic"
    items, err = pipeline._fetch_discovery_source(
        "x", plan, from_date="2026-08-01", to_date="2026-08-31",
        depth="quick", mock=False, config={}, keyword_gate=True,
    )
    assert items == []
    assert "budget exhausted" in (err or "")
    assert calls == [], "no backend may start past the shared deadline"


def test_enrich_cancel_skips_network(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    subquery = schema.SubQuery(
        label="primary", search_query="topic",
        ranking_query="topic", sources=["reddit"], weight=1.0,
    )
    items, artifact = pipeline._retrieve_stream_impl(
        topic="topic", subquery=subquery, source="reddit",
        config={"_enrich_cancel": cancel}, depth="quick",
        date_range=("2026-08-01", "2026-08-31"),
        runtime=mock.Mock(), mock=True,
    )
    assert items == [] and artifact == {}


def test_enrich_cancel_skips_real_transport(monkeypatch):
    """Same early exit with mock=False: no transport call may fire."""
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(
        pipeline.grounding, "web_search",
        mock.Mock(side_effect=AssertionError("transport must not fire")),
    )
    subquery = schema.SubQuery(
        label="primary", search_query="topic",
        ranking_query="topic", sources=["grounding"], weight=1.0,
    )
    items, artifact = pipeline._retrieve_stream_impl(
        topic="topic", subquery=subquery, source="grounding",
        config={"_enrich_cancel": cancel}, depth="quick",
        date_range=("2026-08-01", "2026-08-31"),
        runtime=mock.Mock(), mock=False,
    )
    assert items == [] and artifact == {}
    assert pipeline.grounding.web_search.call_count == 0


def test_discovery_timeout_records_timeout_partial(monkeypatch):
    monkeypatch.setattr(pipeline, "DISCOVERY_FUTURE_TIMEOUT_SECONDS", 0.05)
    def slow(*a, **k):
        time.sleep(0.4)
        return [], None
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", slow)
    plan = mock.Mock()
    plan.sources = ["reddit"]
    plan.domain = "topic"
    start = time.monotonic()
    bundle = pipeline.nominate_candidates(
        plan, from_date="2026-08-01", to_date="2026-08-31",
        depth="quick", mock=False, config={}, lookback_days=30,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"hung lane must not stall nominate (took {elapsed:.2f}s)"
    outcome = bundle.source_status["reddit"]
    assert outcome.state == health.TIMEOUT


def test_xai_single_attempt_with_deadline(monkeypatch):
    seen = {}
    def fake_post(url, payload, headers=None, **kwargs):
        seen.update(kwargs)
        return {"output": []}
    monkeypatch.setattr(pipeline.xai_x.http, "post", fake_post)
    deadline = time.monotonic() + 90
    xai_x.search_x("k", "m", "topic", "2026-08-01", "2026-08-31", deadline_monotonic=deadline)
    assert seen.get("retries") == 1
    assert seen.get("deadline_monotonic") == deadline


def test_bird_clamps_timeout_to_remaining_deadline(monkeypatch):
    from lib import bird_x
    seen = []
    monkeypatch.setattr(
        bird_x, "_run_bird_search",
        lambda q, count, timeout, deadline=None: (seen.append(timeout), {"items": [], "error": ""})[1],
    )
    monkeypatch.setattr(bird_x, "parse_bird_response", lambda *a, **k: [])
    bird_x.search_x("topic", "2026-08-01", "2026-08-31", depth="default",
                    deadline=time.monotonic() + 5)
    assert seen, "at least the first search must run"
    assert all(t <= 5 for t in seen), f"per-attempt timeouts must fit the budget: {seen}"


def test_bird_skips_subprocess_past_deadline(monkeypatch):
    from lib import bird_x
    calls = []
    monkeypatch.setattr(
        bird_x, "_run_bird_search",
        lambda *a, **k: (calls.append(a), {"items": [], "error": ""})[1],
    )
    result = bird_x.search_x("topic", "2026-08-01", "2026-08-31",
                             deadline=time.monotonic() - 1)
    assert calls == []
    assert "budget exhausted" in result.get("error", "")


def test_xurl_skips_subprocess_past_deadline(monkeypatch):
    from lib import xurl_x
    calls = []
    monkeypatch.setattr(
        xurl_x.subprocess, "run",
        lambda *a, **k: (calls.append(k.get("timeout")), mock.Mock(returncode=0, stdout="{}", stderr=""))[1],
    )
    result = xurl_x.search_x("topic", deadline=time.monotonic() - 1)
    assert calls == []
    assert "budget exhausted" in result.get("error", "")
    xurl_x.search_x("topic", deadline=time.monotonic() + 5)
    assert calls and calls[-1] <= 5


def test_grok_fanout_respects_cancel(monkeypatch):
    from lib import grok_x
    calls = []
    monkeypatch.setattr(grok_x, "binary_path", lambda: "/usr/bin/grok")
    def fake_run(cmd, **kwargs):
        import subprocess
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "no posts", "")
    monkeypatch.setattr(grok_x.subprocess, "run", fake_run)
    cancel = threading.Event()
    cancel.set()
    result = grok_x.search_x("topic", "2026-08-01", "2026-08-31", cancel=cancel)
    assert calls == [], "a cancelled fan-out must not start subprocesses"
    assert result["items"] == []


def test_discovery_xapi_warning_reaches_bundle_artifact(monkeypatch):
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xapi"])
    monkeypatch.setattr(
        pipeline.x_api, "search_x",
        lambda *a, **k: {"items": [{"id": "1"}], "warning": "window truncated to 7 days"},
    )
    plan = mock.Mock()
    plan.domain = "topic"
    warnings: list[str] = []
    items, err = pipeline._fetch_discovery_source(
        "x", plan, from_date="2026-08-01", to_date="2026-08-31",
        depth="quick", mock=False, config={}, keyword_gate=True,
        warnings=warnings,
    )
    assert items and err is None
    assert warnings == ["X: xapi window truncated to 7 days"]


def test_nominate_surfaces_x_receipts_as_artifact(monkeypatch):
    def fake_source(source, plan, **kwargs):
        warnings = kwargs.get("warnings")
        if warnings is not None:
            warnings.extend(["X: xapi window truncated to 7 days"])
        return [], None
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", fake_source)
    plan = mock.Mock()
    plan.sources = ["x"]
    plan.domain = "topic"
    bundle = pipeline.nominate_candidates(
        plan, from_date="2026-08-01", to_date="2026-08-31",
        depth="quick", mock=False, config={}, lookback_days=30,
    )
    assert bundle.artifacts.get("x_partial_coverage") == ["X: xapi window truncated to 7 days"]


def test_thin_retry_skipped_when_cancelled(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    calls = []
    monkeypatch.setattr(
        pipeline, "_retrieve_stream",
        lambda **k: (calls.append(k.get("source")), ([], {}))[1],
    )
    bundle = schema.RetrievalBundle()
    plan = mock.Mock()
    plan.subqueries = [schema.SubQuery(
        label="primary", search_query="Kanye West",
        ranking_query="Kanye West", sources=["reddit"], weight=1.0,
    )]
    plan.freshness_mode = "breaking"
    pipeline._retry_thin_sources(
        topic="Kanye West", bundle=bundle, plan=plan,
        config={"_enrich_cancel": cancel}, depth="default",
        date_range=("2026-08-01", "2026-08-31"), runtime=mock.Mock(),
        mock=False, rate_limited_sources=set(),
        rate_limit_lock=threading.Lock(),
        settings={"per_stream_limit": 6, "pool_limit": 15, "rerank_limit": 12},
    )
    assert calls == [], "no thin-retry fetch may start once cancelled"


def test_inline_retry_skipped_after_budget_expiry(monkeypatch):
    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 0.05)
    retrieve_calls: list[str] = []
    sleep_calls: list[float] = []
    gate = threading.Event()
    def fake_retrieve(**kwargs):
        retrieve_calls.append(kwargs.get("source"))
        if kwargs.get("source") == "reddit":
            gate.wait(0.6)
            return [], {}
        raise http.HTTPError("Service unavailable", status_code=503)
    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))
    report = pipeline.run(
        topic="timeout probe",
        config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
        depth="quick",
        requested_sources=["reddit", "x"],
        mock=True,
    )
    assert retrieve_calls.count("x") == 1, "a timed-out budget must not buy a retry"
    assert sleep_calls == []
    assert report is not None


def test_cancel_set_on_drain_failure(monkeypatch):
    seen: dict = {}
    def fake_run(**kwargs):
        seen["cancel"] = kwargs["config"].get("_enrich_cancel")
        return mock.Mock()
    monkeypatch.setattr(pipeline, "run", fake_run)
    orig_get = queue.Queue.get
    first = {"done": False}
    def flaky_get(self, *a, **k):
        if not first["done"]:
            first["done"] = True
            raise RuntimeError("drain boom")
        return orig_get(self, *a, **k)
    monkeypatch.setattr(queue.Queue, "get", flaky_get)
    nominations = [pipeline.Nomination(name="t", seed_score=1.0)]
    with pytest.raises(RuntimeError, match="drain boom"):
        pipeline.enrich_nominations(nominations, config={}, budget_seconds=5)
    assert seen["cancel"] is not None and seen["cancel"].is_set()


# Hung-lane regressions: the worker blocks far past the budget, so a caller
# that still joins it on executor exit fails the elapsed bound.
HANG_SECONDS = 5.0
RETURN_BOUND_SECONDS = 1.5


def test_nominate_does_not_join_hung_lane(monkeypatch):
    monkeypatch.setattr(pipeline, "DISCOVERY_FUTURE_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    def hung(*a, **k):
        release.wait(HANG_SECONDS)
        return [], None
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", hung)
    plan = mock.Mock()
    plan.sources = ["reddit"]
    plan.domain = "topic"
    start = time.monotonic()
    try:
        bundle = pipeline.nominate_candidates(
            plan, from_date="2026-08-01", to_date="2026-08-31",
            depth="quick", mock=False, config={}, lookback_days=30,
        )
        elapsed = time.monotonic() - start
    finally:
        release.set()
    assert elapsed < RETURN_BOUND_SECONDS, f"nominate joined the hung lane ({elapsed:.2f}s)"
    assert bundle.source_status["reddit"].state == health.TIMEOUT


def test_nominate_drops_receipts_from_timed_out_x(monkeypatch):
    monkeypatch.setattr(pipeline, "DISCOVERY_FUTURE_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    def x_straggler(source, plan, **kwargs):
        kwargs["warnings"].append("X: straggler receipt")
        release.wait(HANG_SECONDS)
        return [], None
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", x_straggler)
    plan = mock.Mock()
    plan.sources = ["x"]
    plan.domain = "topic"
    try:
        bundle = pipeline.nominate_candidates(
            plan, from_date="2026-08-01", to_date="2026-08-31",
            depth="quick", mock=False, config={}, lookback_days=30,
        )
    finally:
        release.set()
    assert bundle.source_status["x"].state == health.TIMEOUT
    assert "x_partial_coverage" not in bundle.artifacts


def test_run_does_not_join_hung_stream(monkeypatch):
    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 0.2)
    release = threading.Event()
    def fake_retrieve(**kwargs):
        if kwargs.get("source") == "reddit":
            release.wait(HANG_SECONDS)
        return [], {}
    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    start = time.monotonic()
    try:
        report = pipeline.run(
            topic="timeout probe",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["reddit", "x"],
            mock=True,
        )
        elapsed = time.monotonic() - start
    finally:
        release.set()
    assert elapsed < RETURN_BOUND_SECONDS + 1.0, f"run() joined the hung stream ({elapsed:.2f}s)"
    assert report.source_status["reddit"].state == health.TIMEOUT


def test_thin_retry_does_not_join_hung_retry(monkeypatch):
    monkeypatch.setattr(pipeline, "THIN_RETRY_FUTURE_TIMEOUT_SECONDS", 0.05)
    release = threading.Event()
    def hung(**kwargs):
        release.wait(HANG_SECONDS)
        return [], {}
    monkeypatch.setattr(pipeline, "_retrieve_stream", hung)
    bundle = schema.RetrievalBundle()
    plan = mock.Mock()
    plan.subqueries = [schema.SubQuery(
        label="primary", search_query="Kanye West",
        ranking_query="Kanye West", sources=["reddit"], weight=1.0,
    )]
    plan.freshness_mode = "breaking"
    start = time.monotonic()
    try:
        pipeline._retry_thin_sources(
            topic="Kanye West", bundle=bundle, plan=plan,
            config={}, depth="default",
            date_range=("2026-08-01", "2026-08-31"), runtime=mock.Mock(),
            mock=False, rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            settings={"per_stream_limit": 6, "pool_limit": 15, "rerank_limit": 12},
        )
        elapsed = time.monotonic() - start
    finally:
        release.set()
    assert elapsed < RETURN_BOUND_SECONDS, f"thin retry joined the hung lane ({elapsed:.2f}s)"
    assert bundle.source_status["reddit"].state == health.TIMEOUT


def _interstitial():
    from lib.subproc import SubprocResult
    return SubprocResult(returncode=0, stdout="<!DOCTYPE html><html>blocked</html>", stderr="")


def test_bird_decode_retry_skipped_when_delay_exceeds_budget(monkeypatch):
    from lib import bird_x
    timeouts, sleeps = [], []
    monkeypatch.setattr(
        bird_x, "_invoke_bird_subprocess",
        lambda q, c, timeout: (timeouts.append(timeout), (_interstitial(), None))[1],
    )
    monkeypatch.setattr(bird_x.time, "sleep", lambda s: sleeps.append(s))
    response = bird_x._run_bird_search(
        "q", count=10, timeout=60,
        deadline=time.monotonic() + bird_x.JSON_DECODE_RETRY_DELAY - 2,
    )
    assert timeouts == [60] and sleeps == [], "no retry may start without budget for it"
    assert "budget exhausted" in response["error"]
    assert bird_x.classify_run_failure(response["error"]) == health.SCHEMA_DRIFT


def test_bird_decode_retry_reclamps_timeout_to_remaining_budget(monkeypatch):
    from lib import bird_x
    timeouts = []
    monkeypatch.setattr(
        bird_x, "_invoke_bird_subprocess",
        lambda q, c, timeout: (timeouts.append(timeout), (_interstitial(), None))[1],
    )
    monkeypatch.setattr(bird_x.time, "sleep", lambda s: None)
    bird_x._run_bird_search("q", count=10, timeout=60, deadline=time.monotonic() + 30)
    assert len(timeouts) == 2
    assert timeouts[0] == 60 and timeouts[1] <= 30, f"retry must fit the budget: {timeouts}"


def test_bird_budget_stop_keeps_clean_empty_outcome(monkeypatch):
    from lib import bird_x
    deadline = time.monotonic() + 30
    calls = []
    def fake_run(query, count, timeout, deadline=None):
        calls.append(query)
        monkeypatch.setattr(bird_x.time, "monotonic", lambda: deadline + 1)
        return {"items": []}
    monkeypatch.setattr(bird_x, "_run_bird_search", fake_run)
    response = bird_x.search_x(
        "multi word agent topic", "2026-08-01", "2026-08-31", deadline=deadline,
    )
    assert len(calls) == 1, "zero-result retries must not start past the deadline"
    assert response == {"items": []}, f"clean no-results must not become an error: {response}"
