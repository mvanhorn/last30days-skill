from __future__ import annotations

import threading
import time
import urllib.error
from dataclasses import replace
from unittest import mock
from uuid import uuid4

import pytest

from lib import dates, health, http, pipeline, schema


@pytest.fixture
def x_run(monkeypatch):
    topic = f"Widget compiler {uuid4().hex}"
    today = dates.get_date_range(30)[1]
    post = {
        "id": "1", "url": "https://x.com/widget/status/1",
        "author_handle": "widget", "text": f"{topic} ships incremental builds",
        "date": today, "engagement": {"likes": 100}, "relevance": 1.0,
    }
    plan = schema.QueryPlan(
        raw_topic=topic, intent="opinion", freshness_mode="balanced_recent",
        cluster_mode="debate", source_weights={"x": 1.0},
        subqueries=[schema.SubQuery(
            label="primary", search_query=topic, ranking_query=topic, sources=["x"],
        )],
    )
    runtime = schema.ProviderRuntime(
        reasoning_provider="native", planner_model="", rerank_model="",
        x_search_backend="xapi",
    )
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a: (runtime, None))
    monkeypatch.setattr(pipeline, "available_sources", lambda *a, **k: ["x"])
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xapi"])
    monkeypatch.setattr(pipeline.planner, "plan_query", lambda **k: plan)
    monkeypatch.setattr(pipeline.entity_extract, "extract_entities", lambda *a, **k: {
        "x_handles": ["widget"], "x_hashtags": [], "reddit_subreddits": [],
    })
    return topic, post, plan, runtime


@pytest.mark.parametrize("expired", [True, False])
def test_enrichment_lifecycle_stops_late_supplemental_requests(monkeypatch, x_run, expired):
    topic, post, _, _ = x_run
    release = threading.Event()
    finished = threading.Event()
    stream_finished = threading.Event()
    calls = []
    if not expired:
        release.set()

    def topic_search(*args, **kwargs):
        calls.append("topic")
        assert release.wait(5)
        return {"items": [post, {
            **post, "id": "2", "url": "https://x.com/widget/status/2",
            "text": f"{topic} community discusses authentication errors and setup tutorials for new users",
        }]}

    def from_search(*args, **kwargs):
        calls.append("FROM")
        return []

    def about_search(*args, **kwargs):
        calls.append("ABOUT")
        return []

    original_run = pipeline.run
    original_stream = pipeline._retrieve_stream

    def tracked_stream(**kwargs):
        try:
            return original_stream(**kwargs)
        finally:
            stream_finished.set()

    def tracked_run(**kwargs):
        try:
            return original_run(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(pipeline, "run", tracked_run)
    monkeypatch.setattr(pipeline, "_retrieve_stream", tracked_stream)
    monkeypatch.setattr(pipeline.x_api, "search_x", topic_search)
    monkeypatch.setattr(pipeline.x_api, "search_handles", from_search)
    monkeypatch.setattr(pipeline.x_api, "search_mentions", about_search)
    start = time.monotonic()
    try:
        result = pipeline.enrich_nominations(
            [pipeline.Nomination(name=topic, seed_score=1)],
            config={"X_BEARER_TOKEN": "dummy", "LAST30DAYS_X_BACKEND": "xapi"},
            depth="default", requested_sources=["x"], budget_seconds=1 if expired else 5,
            max_workers=1,
        )
        elapsed = time.monotonic() - start
    finally:
        release.set()
    assert finished.wait(5), "the actual abandoned pipeline worker must finish"
    assert stream_finished.wait(5), "the in-flight stream must finish before checking for late requests"
    if expired:
        assert elapsed < 2
        assert result[0].error == "enrichment budget exhausted"
        assert calls == ["topic"], f"new adapter requests after return: {calls}"
    else:
        assert result[0].report is not None and result[0].error is None
        assert "FROM" in calls and "ABOUT" in calls


@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_x_failover_stops_after_inflight_backend(monkeypatch, x_run, stop):
    topic, _, plan, runtime = x_run
    cancel = threading.Event()
    config = {"_enrich_cancel": cancel, "_research_deadline": time.monotonic() + 10}
    calls = []

    def first(*args, **kwargs):
        calls.append("xapi")
        if stop == "cancel":
            cancel.set()
        else:
            config["_research_deadline"] = time.monotonic() - 1
        return {"items": []}

    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xapi", "xquik"])
    monkeypatch.setattr(pipeline.x_api, "search_x", first)
    monkeypatch.setattr(pipeline.xquik, "search_xquik", lambda *a, **k: calls.append("xquik") or {"items": []})
    try:
        pipeline._retrieve_stream_impl(
            topic=topic, subquery=plan.subqueries[0], source="x", config=config,
            depth="quick", date_range=dates.get_date_range(30), runtime=runtime, mock=False,
        )
    except pipeline.SourceRunError:
        pass
    assert calls == ["xapi"]


@pytest.mark.parametrize("backend", ["xapi", "bird", "xquik", "grok"])
def test_supplemental_stops_between_lanes(monkeypatch, x_run, backend):
    topic, _, plan, runtime = x_run
    runtime = replace(runtime, x_search_backend=backend)
    cancel = threading.Event()
    calls = []
    adapter = {"xapi": pipeline.x_api, "bird": pipeline.bird_x,
               "xquik": pipeline.xquik, "grok": pipeline.grok_x}[backend]

    def from_search(*args, **kwargs):
        calls.append("FROM")
        cancel.set()
        return ([], False) if backend == "grok" else []

    def about_search(*args, **kwargs):
        calls.append("ABOUT")
        return ([], False) if backend == "grok" else []

    monkeypatch.setattr(adapter, "search_handles", from_search)
    monkeypatch.setattr(adapter, "search_mentions", about_search)
    monkeypatch.setattr(pipeline.grok_x, "search_name", lambda *a, **k: calls.append("NAME") or ([], False))
    pipeline._run_supplemental_searches(
        topic=topic, bundle=schema.RetrievalBundle(), plan=plan,
        config={"_enrich_cancel": cancel, "X_BEARER_TOKEN": "dummy", "XQUIK_API_KEY": "dummy"},
        depth="default", date_range=dates.get_date_range(30), runtime=runtime, mock=False,
        rate_limited_sources=set(), rate_limit_lock=threading.Lock(), x_handle="widget",
        x_related=["related"],
    )
    assert calls == ["FROM"]


def test_enrichment_skips_queued_nomination_after_budget(monkeypatch, x_run):
    topic, post, _, _ = x_run
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def search(*args, **kwargs):
        calls.append(args[1])
        try:
            assert release.wait(5)
            return {"items": [post]}
        finally:
            finished.set()

    monkeypatch.setattr(pipeline.x_api, "search_x", search)
    try:
        result = pipeline.enrich_nominations(
            [pipeline.Nomination(name=name, seed_score=1) for name in (topic, "queued widget")],
            config={"X_BEARER_TOKEN": "dummy"}, requested_sources=["x"],
            max_workers=1, budget_seconds=1,
        )
    finally:
        release.set()
    assert finished.wait(5)
    for thread in threading.enumerate():
        if thread.name.startswith("discover-enrich-"):
            thread.join(timeout=5)
            assert not thread.is_alive()
    assert calls == [topic]
    assert all(entry.report is None and entry.error for entry in result)


@pytest.mark.parametrize("phase", ["stream", "discovery", "thin"])
def test_fanout_timeout_stops_late_x_failover(monkeypatch, x_run, phase):
    topic, _, plan, runtime = x_run
    release = threading.Event()
    finished = threading.Event()
    calls = []
    original_stream = pipeline._retrieve_stream
    original_discovery = pipeline._fetch_discovery_source

    def stream(*args, **kwargs):
        try:
            return original_stream(*args, **kwargs)
        finally:
            finished.set()

    def discovery(*args, **kwargs):
        try:
            return original_discovery(*args, **kwargs)
        finally:
            finished.set()

    def search(*args, **kwargs):
        calls.append("xapi")
        assert release.wait(5)
        return {"items": []}

    monkeypatch.setattr(pipeline, "_retrieve_stream", stream)
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", discovery)
    monkeypatch.setattr(pipeline.x_api, "search_x", search)
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xapi", "xquik"])
    monkeypatch.setattr(pipeline.xquik, "search_xquik", lambda *a, **k: calls.append("xquik") or {"items": []})
    for constant in ("STREAM_FUTURE_TIMEOUT_SECONDS", "DISCOVERY_FUTURE_TIMEOUT_SECONDS", "THIN_RETRY_FUTURE_TIMEOUT_SECONDS"):
        monkeypatch.setattr(pipeline, constant, 0.05)
    started = time.monotonic()
    try:
        if phase == "stream":
            result = pipeline.run(topic=topic, config={"X_BEARER_TOKEN": "dummy"}, depth="quick", requested_sources=["x"])
        elif phase == "discovery":
            result = pipeline.nominate_candidates(
                mock.Mock(domain=topic, sources=["x"]), from_date=dates.get_date_range(30)[0],
                to_date=dates.get_date_range(30)[1], depth="quick", mock=False, config={}, lookback_days=30,
            )
        else:
            result = schema.RetrievalBundle()
            pipeline._retry_thin_sources(
                topic=topic, bundle=result, plan=plan, config={}, depth="default",
                date_range=dates.get_date_range(30), runtime=runtime, mock=False,
                rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
                settings={"per_stream_limit": 6},
            )
        assert time.monotonic() - started < 1
        assert result.source_status["x"].state == health.TIMEOUT
    finally:
        release.set()
    assert finished.wait(5)
    assert calls == ["xapi"]


def test_provider_scoring_does_not_start_after_cancelled_rerank(monkeypatch, x_run):
    topic, post, _, _ = x_run
    cancel = threading.Event()
    calls = []
    monkeypatch.setattr(pipeline.x_api, "search_x", lambda *a, **k: {"items": [post]})
    original = pipeline.rerank.rerank_candidates

    def rerank(**kwargs):
        calls.append("rerank")
        result = original(**kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(pipeline.rerank, "rerank_candidates", rerank)
    monkeypatch.setattr(pipeline.rerank, "score_fun", lambda **k: calls.append("score"))
    with pytest.raises(TimeoutError):
        pipeline.run(topic=topic, config={"_enrich_cancel": cancel}, depth="quick", requested_sources=["x"])
    assert "rerank" in calls and "score" not in calls


def test_planner_does_not_start_after_cancelled_source_resolution(monkeypatch, x_run):
    topic, _, _, _ = x_run
    cancel = threading.Event()
    calls = []

    def available(*args, **kwargs):
        cancel.set()
        return ["x"]

    original = pipeline.planner.plan_query

    def plan(**kwargs):
        calls.append("planner")
        return original(**kwargs)

    monkeypatch.setattr(pipeline, "available_sources", available)
    monkeypatch.setattr(pipeline.planner, "plan_query", plan)
    with pytest.raises(TimeoutError):
        pipeline.run(topic=topic, config={"_enrich_cancel": cancel}, depth="quick", requested_sources=["x"])
    assert calls == []


@pytest.mark.parametrize("adapter", ["grok", "bird", "xquik", "xapi"])
def test_adapter_cancellation_stops_its_next_request(monkeypatch, adapter):
    cancel = threading.Event()
    calls = []

    def request(*args, **kwargs):
        calls.append(adapter)
        cancel.set()
        if adapter == "grok":
            return {"error": "temporary transport failure"}
        if adapter == "bird":
            from lib.subproc import SubprocResult
            return SubprocResult(0, "<html>interstitial</html>", ""), None
        if adapter == "xapi":
            return {
                "data": [{"id": "1", "text": "Widget release", "created_at": dates.get_date_range(30)[1]}],
                "meta": {"next_token": "next-page"},
            }
        return {"tweets": []}

    if adapter == "grok":
        monkeypatch.setattr(pipeline.grok_x, "_invoke", request)
        result = pipeline.grok_x.search_x("widget compiler", *dates.get_date_range(30), cancel=cancel)
    elif adapter == "bird":
        monkeypatch.setattr(pipeline.bird_x, "_invoke_bird_subprocess", request)
        result = pipeline.bird_x.search_x("widget compiler", *dates.get_date_range(30), cancel=cancel)
    elif adapter == "xquik":
        monkeypatch.setattr(pipeline.xquik.http, "get", request)
        topic = "best widget compiler tools for incremental development"
        assert len(pipeline.xquik.expand_xquik_queries(topic, "default")) > 1
        result = pipeline.xquik.search_xquik(topic, *dates.get_date_range(30), token="dummy", cancel=cancel)
    else:
        monkeypatch.setattr(pipeline.x_api.http, "get", request)
        result = pipeline.x_api.search_x("dummy", "widget compiler", *dates.get_date_range(30), cancel=cancel)
        assert result["items"], "keep the completed page as partial evidence"
    assert calls == [adapter]
    assert result.get("error") or result.get("warning")


def test_http_cancellation_stops_retry_without_backoff(monkeypatch):
    cancel = threading.Event()
    calls = []

    def request(*args, **kwargs):
        calls.append("request")
        cancel.set()
        raise urllib.error.HTTPError("https://example.invalid", 503, "temporarily unavailable", {}, None)

    monkeypatch.setattr(http.urllib.request, "urlopen", request)
    started = time.monotonic()
    with pytest.raises(http.DeadlineExceeded):
        http.get("https://example.invalid", retries=3, cancel=cancel)
    assert time.monotonic() - started < 0.5
    assert calls == ["request"]


def test_http_cancellation_bounds_inflight_body_wait(monkeypatch):
    cancel = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []
    response = mock.MagicMock()
    response.__enter__.return_value = response
    response.status = 200

    def read():
        calls.append("body")
        cancel.set()
        try:
            assert release.wait(5)
            return b'{"ok": true}'
        finally:
            finished.set()

    response.read.side_effect = read
    monkeypatch.setattr(http.urllib.request, "urlopen", lambda *a, **k: response)
    started = time.monotonic()
    try:
        with pytest.raises(http.DeadlineExceeded):
            http.get("https://example.invalid", retries=3, cancel=cancel)
        assert time.monotonic() - started < 0.5
        assert not finished.is_set(), "the caller must stop waiting while the body read remains blocked"
    finally:
        release.set()
    assert finished.wait(5)
    assert calls == ["body"]


@pytest.mark.parametrize("outcome", ["cancelled", "partial", "empty"])
def test_xquik_final_query_preserves_stop_status_and_items(monkeypatch, outcome):
    cancel = threading.Event()
    tweet = {"id": "123", "text": "Widget release", "author": {"username": "widget"}}

    def request(*args, **kwargs):
        if outcome != "empty":
            cancel.set()
        if outcome == "cancelled":
            raise http.DeadlineExceeded()
        return {"tweets": [tweet] if outcome == "partial" else []}

    monkeypatch.setattr(pipeline.xquik.http, "get", request)
    result = pipeline.xquik.search_xquik("Widget", *dates.get_date_range(30), depth="quick", token="dummy", cancel=cancel)
    if outcome == "empty":
        assert result == {"items": []}
    else:
        assert "timed out" in result.get("error", "")
        assert len(result["items"]) == (1 if outcome == "partial" else 0)


def test_sole_xquik_backend_reports_cancelled_final_request(monkeypatch, x_run):
    topic, _, plan, runtime = x_run
    cancel = threading.Event()

    def request(*args, **kwargs):
        cancel.set()
        raise http.DeadlineExceeded()

    monkeypatch.setattr(pipeline.xquik.http, "get", request)
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["xquik"])
    with pytest.raises(pipeline.SourceRunError) as caught:
        pipeline._retrieve_stream_impl(
            topic=topic, subquery=plan.subqueries[0], source="x",
            config={"XQUIK_API_KEY": "dummy", "_enrich_cancel": cancel},
            depth="quick", date_range=dates.get_date_range(30),
            runtime=replace(runtime, x_search_backend="xquik"), mock=False,
        )
    assert caught.value.outcome_state == health.TIMEOUT


def test_grok_retains_items_and_reports_cancelled_fanout(monkeypatch):
    cancel = threading.Event()

    def invoke(*args, **kwargs):
        cancel.set()
        return {"text": "id: 2087568620465607078\nhandle: widget\ncreated_at: Wed, 12 Aug 2026 15:55:18 GMT\nlikes: 10\ntext: Widget compiler release\n"}

    monkeypatch.setattr(pipeline.grok_x, "_invoke", invoke)
    result = pipeline.grok_x.search_x("Widget compiler", "2026-07-14", "2026-08-13", cancel=cancel)
    assert result["items"], "the completed query remains useful partial evidence"
    assert "timed out" in result.get("error", "")


@pytest.mark.parametrize("cancelled", [True, False])
def test_grok_final_empty_query_reports_cancellation(monkeypatch, cancelled):
    cancel = threading.Event()

    def invoke(*args, **kwargs):
        if cancelled:
            cancel.set()
        return {"text": "No matching posts found."}

    monkeypatch.setattr(pipeline.grok_x, "_invoke", invoke)
    result = pipeline.grok_x.search_x("Widget", "2026-07-14", "2026-08-13", depth="quick", cancel=cancel)
    if cancelled:
        assert "timed out" in result.get("error", "")
    else:
        assert result == {"items": []}


def test_grok_partial_cancellation_keeps_revoked_session_reason(monkeypatch):
    cancel = threading.Event()
    calls = []

    def invoke(*args, **kwargs):
        calls.append("invoke")
        if len(calls) == 1:
            return {"text": "id: 2087568620465607078\nhandle: widget\ncreated_at: Wed, 12 Aug 2026 15:55:18 GMT\nlikes: 10\ntext: Widget compiler release\n"}
        cancel.set()
        return {"error": "Grok session expired", "auth_revoked": True}

    monkeypatch.setattr(pipeline.grok_x, "_invoke", invoke)
    items, error = pipeline._fetch_x_backend(
        "grok", "Widget compiler", "2026-07-14", "2026-08-13", "default",
        {"_enrich_cancel": cancel},
    )
    assert len(items) == 1
    assert "session expired" in error
    assert "timed out" not in error


@pytest.mark.parametrize("partial", [True, False])
def test_grok_short_remaining_budget_reports_timeout(monkeypatch, partial):
    now = [100.0]
    deadline = 120.0 if partial else 105.0
    calls = []

    def invoke(*args, **kwargs):
        calls.append("invoke")
        now[0] = 116.0
        return {"text": "id: 2087568620465607078\nhandle: widget\ncreated_at: Wed, 12 Aug 2026 15:55:18 GMT\nlikes: 10\ntext: Widget compiler release\n"}

    monkeypatch.setattr(pipeline.grok_x.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(pipeline.grok_x, "_invoke", invoke)
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda c: ["grok"])
    kwargs = dict(
        topic="Widget", subquery=schema.SubQuery(label="primary", search_query="Widget", ranking_query="Widget", sources=["x"]),
        source="x", config={"_research_deadline": deadline}, depth="default",
        date_range=("2026-07-14", "2026-08-13"), mock=False,
        runtime=schema.ProviderRuntime(reasoning_provider="native", planner_model="", rerank_model="", x_search_backend="grok"),
    )
    if partial:
        items, artifact = pipeline._retrieve_stream(**kwargs)
        assert len(items) == 1
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert calls == ["invoke"]
    else:
        with pytest.raises(pipeline.SourceRunError) as caught:
            pipeline._retrieve_stream(**kwargs)
        assert caught.value.outcome_state == health.TIMEOUT
        assert calls == []
    assert now[0] < deadline


@pytest.mark.parametrize("failure", ["exception", "outcome"])
def test_rate_limit_stops_queued_streams_before_fanout_finishes(monkeypatch, x_run, failure):
    topic, _, plan, _ = x_run
    plan.subqueries = [replace(plan.subqueries[0], label=f"query-{index}") for index in range(6)]
    barrier = threading.Barrier(4)
    calls = []
    lock = threading.Lock()

    def request(*args, **kwargs):
        with lock:
            index = len(calls)
            calls.append(index)
        if index < 4:
            barrier.wait(2)
        if index == 0:
            if failure == "exception":
                raise http.HTTPError("Rate limit exceeded", status_code=429)
            return {"items": [], "error": "xapi: rate limited (429)"}
        threading.Event().wait(0.2)
        return {"items": []}

    monkeypatch.setattr(pipeline.x_api, "search_x", request)
    report = pipeline.run(
        topic=topic, config={"X_BEARER_TOKEN": "dummy", "_max_source_fetches": 6},
        depth="quick", requested_sources=["x"], internal_subrun=True,
    )
    assert calls == [0, 1, 2, 3], "queued X streams must see the completed worker's rate-limit signal"
    assert report.source_status["x"].state == health.RATE_LIMITED
