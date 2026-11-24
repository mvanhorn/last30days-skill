"""Regression tests for PR 23: pipeline concurrency, deadlines, containment & mock isolation.

Covers HIGH-013, HIGH-015, HIGH-016, HIGH-017, HIGH-018, HIGH-020, HIGH-021, HIGH-024.
"""

from __future__ import annotations

import concurrent.futures
import concurrent.futures.thread
import contextvars
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

from lib import dates, fetch_memo, health, http, jobs, pipeline, schema, x_api, x_envelope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_x_envelope(tmp_path: Path, topic: str, rows: list[dict]) -> x_envelope.Envelope:
    from_date, to_date = dates.get_date_range(30)
    payload = {
        "schema": x_envelope.SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "window": {"from": from_date, "to": to_date},
        "provider": "x-connector",
        "status": "ok",
        "calls": [{"lane": "topic", "handles": [], "posts": rows}],
    }
    path = tmp_path / "x_posts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return x_envelope.read(str(path), (from_date, to_date), topic)


def _x_row(index: int, handle: str, text: str, *, likes: int = 20) -> dict:
    _, to_date = dates.get_date_range(30)
    day = (date.fromisoformat(to_date) - timedelta(days=1 + (index % 15))).isoformat()
    when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(
        hours=8 + (index % 12), minutes=(index * 13) % 60
    )
    ms = int(when.timestamp() * 1000)
    snowflake = str(((ms - x_api._SNOWFLAKE_EPOCH_MS) << 22) | (((index + 1) * 104729) & ((1 << 22) - 1)))
    return {
        "id": snowflake,
        "author_handle": handle,
        "created_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text": text,
        "likes": likes,
        "reposts": 2,
        "replies": 1,
        "quotes": 0,
    }


def _stub_report(topic: str) -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-06-01",
        range_to="2026-07-01",
        generated_at="2026-07-01T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="deterministic",
            rerank_model="local-score",
        ),
        query_plan=schema.QueryPlan(
            intent="factual",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic=topic,
            subqueries=[],
            source_weights={},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={},
        errors_by_source={},
    )


# ---------------------------------------------------------------------------
# HIGH-013: Deterministic single-subquery Phase 1 submission for --x-posts
# ---------------------------------------------------------------------------


def test_high013_x_envelope_submits_only_first_x_subquery_deterministically(tmp_path):
    topic = "ai agents"
    envelope = _make_x_envelope(
        tmp_path,
        topic,
        [
            _x_row(0, "alice", "ai agents runtime overview and benchmarks", likes=80),
            _x_row(1, "bob", "ai agents tooling feedback from production", likes=45),
        ],
    )
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": topic,
                "ranking_query": f"What are people saying about {topic}?",
                "sources": ["x"],
                "weight": 1.0,
            },
            {
                "label": "narrow-angle",
                "search_query": "quantum toaster recipes",
                "ranking_query": "quantum toaster recipes",
                "sources": ["x"],
                "weight": 0.4,
            },
        ],
        "source_weights": {"x": 1.0},
    }
    phase1_labels: list[str] = []
    original = pipeline._retrieve_stream_impl

    def spy(*args, **kwargs):
        if kwargs.get("source") == "x":
            phase1_labels.append(kwargs["subquery"].label)
        return original(*args, **kwargs)

    with mock.patch("lib.env.x_backend_chain", return_value=[]), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline._retrieve_stream_impl", side_effect=spy):
        report = pipeline.run(
            topic=topic,
            config={"_max_source_fetches": 5},
            depth="default",
            requested_sources=["x"],
            mock=False,
            external_plan=plan,
            x_posts=envelope,
            web_backend="none",
            save_dir="",
        )

    assert phase1_labels == ["primary"]
    assert len(report.items_by_source["x"]) == 2
    assert report.source_status["x"].state == health.OK


def test_high013_x_envelope_preserves_combined_stream_limit(tmp_path):
    topic = "ai agents"
    onsets = ("br", "k", "pl", "st", "gr", "v", "dr", "sh")
    vowels = ("a", "e", "i", "o", "u", "ai", "oo")
    codas = ("n", "x", "lt", "mp", "rd", "sk", "th", "z")

    def word(n: int) -> str:
        return onsets[n % 8] + vowels[(n // 8) % 7] + codas[(n // 56) % 8]

    rows = [
        _x_row(i, f"user{i}", f"ai agents {word(i * 3)} {word(i * 3 + 1)} {word(i * 3 + 2)}", likes=100 - i)
        for i in range(40)
    ]
    envelope = _make_x_envelope(tmp_path, topic, rows)
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {"label": "primary", "search_query": topic, "ranking_query": topic, "sources": ["x"]},
            {"label": "secondary", "search_query": "agents", "ranking_query": "agents", "sources": ["x"]},
        ],
        "source_weights": {"x": 1.0},
    }
    with mock.patch("lib.env.x_backend_chain", return_value=[]), \
         mock.patch("lib.pipeline._retry_thin_sources"):
        report = pipeline.run(
            topic=topic,
            config={"_max_source_fetches": 3},
            depth="default",
            requested_sources=["x"],
            mock=False,
            external_plan=plan,
            x_posts=envelope,
            web_backend="none",
            save_dir="",
        )

    # per_stream_limit (12) * _source_fetch_cap("x", {_max_source_fetches: 3}) (3) = 36
    assert len(report.items_by_source["x"]) == 36


def test_high013_x_envelope_honors_zero_max_source_fetches(tmp_path):
    topic = "ai agents"
    envelope = _make_x_envelope(tmp_path, topic, [_x_row(0, "alice", "ai agents launch")])
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {"label": "primary", "search_query": topic, "ranking_query": topic, "sources": ["x"]},
        ],
        "source_weights": {"x": 1.0},
    }
    calls: list[str] = []
    with mock.patch("lib.env.x_backend_chain", return_value=[]), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline._retrieve_stream", side_effect=lambda **kw: (calls.append(kw["source"]), ([], {}))[1]):
        pipeline.run(
            topic=topic,
            config={"_max_source_fetches": 0},
            depth="quick",
            requested_sources=["x"],
            mock=False,
            external_plan=plan,
            x_posts=envelope,
            web_backend="none",
            save_dir="",
        )
    assert calls == []


# ---------------------------------------------------------------------------
# HIGH-015: Rescue done futures & dequeued enrichment reports at deadlines
# ---------------------------------------------------------------------------


def test_high015_wait_for_research_rescues_done_futures_past_deadline():
    f_done = concurrent.futures.Future()
    f_done.set_result("completed-at-boundary")
    f_pending = concurrent.futures.Future()
    f_cancelled = concurrent.futures.Future()
    f_cancelled.cancel()

    config = {"_research_deadline": time.monotonic() - 1.0}
    done, pending = pipeline._wait_for_research([f_done, f_pending, f_cancelled], config)

    assert done == {f_done}
    assert pending == {f_pending, f_cancelled}


def test_high015_completed_streams_rescues_done_future_for_ordinary_batch():
    f_done = concurrent.futures.Future()
    f_done.set_result(([{"id": "1"}], {}))
    f_hung = concurrent.futures.Future()

    subquery = schema.SubQuery(label="primary", search_query="q", ranking_query="q", sources=["reddit", "x"])
    futures = {f_done: (subquery, "reddit"), f_hung: (subquery, "x")}
    wait_config = {"_research_deadline": time.monotonic() - 1.0}
    bundle = schema.RetrievalBundle()

    yielded = list(pipeline._completed_streams([(futures, wait_config, 180.0, False)], bundle))

    assert len(yielded) == 1
    future, _sq, source, budget_expired = yielded[0]
    assert future is f_done
    assert source == "reddit"
    assert budget_expired is True
    assert "reddit" not in bundle.errors_by_source
    assert bundle.source_status["x"].state == health.TIMEOUT


def test_high015_nominate_candidates_rescues_future_done_at_deadline(monkeypatch):
    today = dates.get_date_range(5)[0]
    raw = {
        "id": "hn1",
        "title": "Boundary discovery story",
        "url": "https://example.com/boundary",
        "hn_url": "https://news.ycombinator.com/item?id=99",
        "author": "alice",
        "date": today,
        "engagement": {"points": 150, "comments": 40},
        "relevance": 0.9,
    }
    monkeypatch.setattr(pipeline, "_fetch_discovery_source", lambda *a, **k: ([raw], None))
    # Force deadline already expired so _wait_for_research exits immediately,
    # while the future finishes before the not_done sweep.
    real_wait = pipeline._wait_for_research

    def boundary_wait(futures, config):
        for future in futures:
            future.result(timeout=2)
        expired_cfg = {**config, "_research_deadline": time.monotonic() - 1.0}
        return real_wait(futures, expired_cfg)

    monkeypatch.setattr(pipeline, "_wait_for_research", boundary_wait)
    plan = mock.Mock(sources=["hackernews"], domain="boundary")
    bundle = pipeline.nominate_candidates(
        plan,
        from_date=dates.get_date_range(30)[0],
        to_date=dates.get_date_range(30)[1],
        depth="quick",
        mock=False,
        config={},
        lookback_days=30,
    )
    assert len(bundle.items_by_source.get("hackernews", [])) == 1
    assert bundle.source_status["hackernews"].state == health.OK


def test_high015_enrich_nominations_preserves_dequeued_and_ready_reports_at_deadline(monkeypatch):
    n1 = pipeline.Nomination(name="Topic One", seed_score=90.0)
    n2 = pipeline.Nomination(name="Topic Two", seed_score=80.0)
    r1 = _stub_report("Topic One")
    r2 = _stub_report("Topic Two")
    both_enqueued = threading.Event()
    calls = {"n": 0}
    lock = threading.Lock()
    real_monotonic = time.monotonic

    def fake_run(*, topic, **_kwargs):
        with lock:
            calls["n"] += 1
            if calls["n"] == 2:
                both_enqueued.set()
        return r1 if topic == "Topic One" else r2

    # Advance synthetic clock past deadline right as the first blocking get() returns.
    now = [100.0]
    first_get = {"done": False}
    real_get = queue.Queue.get

    def get_then_expire(self, block=True, timeout=None):
        if block and not first_get["done"]:
            first_get["done"] = True
            assert both_enqueued.wait(timeout=5)
            # Ensure both workers have put into results_queue before returning.
            deadline_wait = real_monotonic() + 2.0
            while self.qsize() < 2 and real_monotonic() < deadline_wait:
                time.sleep(0.01)
            item = real_get(self, block=block, timeout=timeout)
            now[0] = 200.0  # Cross the deadline immediately after dequeuing item 1
            return item
        return real_get(self, block=block, timeout=timeout)

    monkeypatch.setattr(pipeline, "run", fake_run)
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(queue.Queue, "get", get_then_expire)

    results = pipeline.enrich_nominations([n1, n2], config={}, budget_seconds=1.0, max_workers=2)
    by_name = {entry.nomination.name: entry for entry in results}
    assert by_name["Topic One"].report is r1
    assert by_name["Topic One"].error is None
    assert by_name["Topic Two"].report is r2
    assert by_name["Topic Two"].error is None


# ---------------------------------------------------------------------------
# HIGH-016: Incremental collection, non-starving async 5xx retries & 429s
# ---------------------------------------------------------------------------


def test_high016_transient_5xx_retry_succeeds_while_sibling_hangs(monkeypatch):
    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 1.2)
    monkeypatch.setattr(pipeline, "_TRANSIENT_RETRY_DELAY_SECONDS", 0.05)

    release = threading.Event()
    attempts: dict[str, int] = {"x": 0, "reddit": 0}
    retry_finished_at: list[float] = []
    started = time.monotonic()
    today = dates.get_date_range(5)[0]

    def fake_retrieve(**kwargs):
        source = kwargs["source"]
        attempts[source] = attempts.get(source, 0) + 1
        if source == "reddit":
            release.wait(timeout=5.0)
            return [], {}
        if attempts["x"] == 1:
            raise http.HTTPError("HTTP 503: Service Unavailable", status_code=503)
        retry_finished_at.append(time.monotonic() - started)
        return (
            [
                {
                    "id": "X1",
                    "text": "Async retry probe succeeded on X",
                    "url": "https://x.com/alice/status/1",
                    "author_handle": "alice",
                    "date": today,
                    "engagement": {"likes": 50},
                    "relevance": 0.9,
                }
            ],
            {},
        )

    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())
    try:
        report = pipeline.run(
            topic="async retry probe",
            config={},
            depth="quick",
            requested_sources=["reddit", "x"],
            mock=True,
            web_backend="none",
        )
    finally:
        release.set()

    assert attempts["x"] == 2
    assert retry_finished_at and retry_finished_at[0] < 0.8, (
        f"retry should finish while sibling hangs, not after timeout ({retry_finished_at})"
    )
    assert len(report.items_by_source.get("x", [])) == 1
    assert report.source_status["x"].state == health.OK
    assert report.source_status["reddit"].state == health.TIMEOUT


def test_high016_parallel_5xx_retries_run_concurrently_and_only_once(monkeypatch):
    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(pipeline, "_TRANSIENT_RETRY_DELAY_SECONDS", 0.25)

    attempts: dict[str, int] = {"reddit": 0, "hackernews": 0}
    retry_times: dict[str, float] = {}
    lock = threading.Lock()
    today = dates.get_date_range(5)[0]

    def fake_retrieve(**kwargs):
        source = kwargs["source"]
        with lock:
            attempts[source] += 1
            count = attempts[source]
        if count == 1:
            raise http.HTTPError("HTTP 503: Service Unavailable", status_code=503)
        with lock:
            retry_times[source] = time.monotonic()
        if source == "hackernews":
            raise http.HTTPError("HTTP 502: Bad Gateway", status_code=502)
        return (
            [
                {
                    "id": "R1",
                    "title": "Parallel retry probe discussion",
                    "url": "https://reddit.com/r/example/comments/1",
                    "subreddit": "example",
                    "date": today,
                    "engagement": {"score": 40, "num_comments": 10},
                    "selftext": "Parallel retry probe",
                    "relevance": 0.9,
                }
            ],
            {},
        )

    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())
    started = time.monotonic()
    report = pipeline.run(
        topic="parallel retry probe",
        config={},
        depth="quick",
        requested_sources=["reddit", "hackernews"],
        mock=True,
        web_backend="none",
    )
    elapsed = time.monotonic() - started

    assert attempts == {"reddit": 2, "hackernews": 2}
    assert abs(retry_times["reddit"] - retry_times["hackernews"]) < 0.2
    assert elapsed < 0.9, f"retries should not serialize backoff ({elapsed:.2f}s)"
    assert report.source_status["reddit"].state == health.OK
    assert report.source_status["hackernews"].state == health.ERROR
    assert "retried once, still failed" in report.errors_by_source["hackernews"]


def test_high016_rate_limit_signals_incrementally_before_slow_sibling_finishes(monkeypatch):
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {"label": "sq1", "search_query": "topic", "ranking_query": "topic", "sources": ["x", "reddit"]},
            {"label": "sq2", "search_query": "topic angle", "ranking_query": "topic", "sources": ["x"]},
        ],
        "source_weights": {"x": 1.0, "reddit": 1.0},
    }
    x_429_signalled = threading.Event()
    sq2_observed_before_reddit = threading.Event()
    reddit_finished = threading.Event()
    sq2_saw_rate_limit: list[bool] = []

    def fake_retrieve(**kwargs):
        source = kwargs["source"]
        label = kwargs["subquery"].label
        rate_limited = kwargs["rate_limited_sources"]
        lock = kwargs["rate_limit_lock"]
        if source == "reddit":
            # Hold reddit open until sq2 observes the incremental 429 signal.
            x_429_signalled.wait(timeout=2.0)
            sq2_observed_before_reddit.wait(timeout=1.5)
            reddit_finished.set()
            return [], {}
        if label == "sq1":
            x_429_signalled.set()
            raise http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
        # sq2 runs after sq1 frees a worker slot (max_workers=2); main thread
        # must record 429 while reddit is still running.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with lock:
                if "x" in rate_limited:
                    sq2_saw_rate_limit.append(not reddit_finished.is_set())
                    sq2_observed_before_reddit.set()
                    return [], {}
            time.sleep(0.01)
        sq2_saw_rate_limit.append(False)
        sq2_observed_before_reddit.set()
        return [], {}

    monkeypatch.setattr(pipeline, "_inner_max_workers", lambda *a, **k: 2)
    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())
    report = pipeline.run(
        topic="rate limit probe",
        config={"_max_source_fetches": 3},
        depth="default",
        requested_sources=["x", "reddit"],
        mock=True,
        external_plan=plan,
        web_backend="none",
    )

    assert sq2_saw_rate_limit == [True]
    assert report.source_status["x"].state == health.RATE_LIMITED


def test_high016_two_source_subrun_uses_full_max_workers_concurrently(monkeypatch):
    """A 2-source comparison sub-run (max_workers=2) must run both primary searches
    concurrently without reserving an idle retry slot."""
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "two source comparison",
                "ranking_query": "two source comparison",
                "sources": ["reddit", "x"],
            },
        ],
        "source_weights": {"reddit": 1.0, "x": 1.0},
    }
    both_started = threading.Barrier(2, timeout=2.0)
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_retrieve(**kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            both_started.wait()
            return [], {}
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())
    report = pipeline.run(
        topic="two source comparison",
        config={},
        depth="quick",
        requested_sources=["reddit", "x"],
        mock=True,
        external_plan=plan,
        web_backend="none",
        internal_subrun=True,
    )
    assert report is not None
    assert state["peak"] == 2


def test_high016_ready_retry_preempts_queued_primary_under_strict_max_workers_cap(monkeypatch):
    """Backoff consumes zero worker slots, active work never exceeds max_workers,
    and once ready a retry runs ahead of queued primary searches."""
    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(pipeline, "_TRANSIENT_RETRY_DELAY_SECONDS", 0.05)
    monkeypatch.setattr(pipeline, "_inner_max_workers", lambda *a, **k: 2)

    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "priority retry probe",
                "ranking_query": "priority retry probe",
                "sources": ["x", "reddit", "hackernews", "bluesky"],
            },
        ],
        "source_weights": {"x": 1.0, "reddit": 1.0, "hackernews": 1.0, "bluesky": 1.0},
    }
    hn_entered = threading.Event()
    release_reddit = threading.Event()
    release_hn = threading.Event()
    order: list[str] = []
    attempts: dict[str, int] = {"x": 0}
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}
    today = dates.get_date_range(5)[0]

    def fake_retrieve(**kwargs):
        source = kwargs["source"]
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            if source == "x":
                with lock:
                    attempts["x"] += 1
                    attempt = attempts["x"]
                if attempt == 1:
                    raise http.HTTPError("HTTP 503: Service Unavailable", status_code=503)
                with lock:
                    order.append("x-retry")
                release_hn.set()
                return (
                    [
                        {
                            "id": "X-RETRY",
                            "text": "Priority retry probe recovered ahead of queued primary",
                            "url": "https://x.com/alice/status/99",
                            "author_handle": "alice",
                            "date": today,
                            "engagement": {"likes": 42},
                            "relevance": 0.9,
                        }
                    ],
                    {},
                )
            if source == "reddit":
                # Wait until hackernews took x's freed slot (proving backoff holds 0 slots)
                # and until x's retry_at (0.05s) has elapsed, then free this worker.
                assert hn_entered.wait(timeout=2.0)
                time.sleep(0.1)
                release_reddit.set()
                return [], {}
            if source == "hackernews":
                hn_entered.set()
                release_hn.wait(timeout=2.0)
                return [], {}
            with lock:
                order.append(source)
            return [], {}
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())
    try:
        report = pipeline.run(
            topic="priority retry probe",
            config={},
            depth="default",
            requested_sources=["x", "reddit", "hackernews", "bluesky"],
            mock=True,
            external_plan=plan,
            web_backend="none",
            internal_subrun=True,
        )
    finally:
        release_hn.set()

    assert attempts["x"] == 2
    assert state["peak"] == 2
    assert order == ["x-retry", "bluesky"], (
        f"ready retry must run ahead of queued primary search: {order}"
    )
    assert len(report.items_by_source.get("x", [])) == 1
    assert report.source_status["x"].state == health.OK


def test_high016_multiple_delayed_retries_run_in_parallel_and_cancel_cleanly_on_shutdown():
    """Slow first retry must not block a second ready retry when another worker
    is free, and shutdown(wait=False, cancel_futures=True) must cancel pending
    delayed retries immediately."""
    both_retries = threading.Barrier(2, timeout=2.0)
    with pipeline._abandoning_executor(max_workers=2) as pool:
        ready_at = time.monotonic() + 0.05
        f1 = pool.submit_delayed(ready_at, lambda: (both_retries.wait(), "r1")[1])
        f2 = pool.submit_delayed(ready_at, lambda: (both_retries.wait(), "r2")[1])
        assert f1.result(timeout=2.0) == "r1"
        assert f2.result(timeout=2.0) == "r2"

    ran_late: list[int] = []
    t0 = time.monotonic()
    with pipeline._abandoning_executor(max_workers=2) as pool:
        f_far = pool.submit_delayed(time.monotonic() + 30.0, ran_late.append, 1)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"shutdown blocked on pending delayed retry ({elapsed:.2f}s)"
    assert f_far.cancelled()
    assert ran_late == []


# ---------------------------------------------------------------------------
# HIGH-017: Single-fetch caps & thread-safe _jobs_lane / LaneBudget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("source", ["perplexity", "jobs", "trustpilot", "amazon", "meta_ads"])
def test_high017_single_entity_sources_stay_capped_under_override(source):
    assert pipeline._source_fetch_cap(source, {}) == 1
    assert pipeline._source_fetch_cap(source, {"_max_source_fetches": 5}) == 1
    assert pipeline._source_fetch_cap(source, {"_max_source_fetches": 0}) == 0
    assert pipeline._source_fetch_cap(source, {"_max_source_fetches": -3}) == 0
    assert pipeline._source_fetch_cap("x", {"_max_source_fetches": -2}) == 0


def test_high017_jobs_lane_budget_init_is_thread_safe():
    config = {"_jobs_lane": {}}
    barrier = threading.Barrier(12, timeout=5)
    budgets: list[jobs.LaneBudget] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        b = pipeline._jobs_lane_budget(config)
        with lock:
            budgets.append(b)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert len(budgets) == 12
    assert len({id(b) for b in budgets}) == 1
    assert config["_jobs_lane"] == {"budget": budgets[0]}


def test_high017_lane_budget_concurrent_mutations_are_synchronized():
    budget = jobs.LaneBudget(60.0)
    barrier = threading.Barrier(8, timeout=5)
    errors: list[BaseException] = []

    def worker(idx: int):
        try:
            barrier.wait()
            for j in range(50):
                budget.note_slow(f"slow-{idx}-{j}")
                budget.note_unanswered(f"unanswered-{idx}-{j}")
                budget.record_landed(f"https://example.com/{idx}/{j}", f"https://dest.example/{idx}/{j}")
                assert budget.get_landed(f"https://example.com/{idx}/{j}") == f"https://dest.example/{idx}/{j}"
                budget.mark_unreachable({f"host-{idx}-{j}.example"})
                assert budget.is_unreachable(f"host-{idx}-{j}.example")
                budget.limit_discovery(30.0)
                budget.remaining()
                budget.spent()
                budget.stop(f"step-{idx}")
                budget.settled = True
                budget.reason()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive()

    assert errors == []
    assert len(budget.slow) == 400
    assert len(budget.unanswered_snapshot()) == 400
    assert len(budget.landed) == 400
    assert len(budget.unreachable) == 400
    assert budget.stopped_at.startswith("step-")
    assert budget.settled is True


# ---------------------------------------------------------------------------
# HIGH-018: Daemon-thread _abandoning_executor & cancellable fetch_memo
# ---------------------------------------------------------------------------


def test_high018_abandoning_executor_uses_daemon_threads_cancels_queued_and_preserves_context():
    release = threading.Event()
    started = threading.Event()
    worker_info: dict[str, object] = {}
    queued_ran: list[int] = []

    ctx_var: contextvars.ContextVar[str] = contextvars.ContextVar("pr23_ctx", default="unset")
    ctx_seen: list[str] = []

    with pipeline._abandoning_executor(max_workers=2) as pool:
        token = ctx_var.set("carried-value")
        try:
            f_ctx = http.submit_with_context(pool, ctx_var.get)
        finally:
            ctx_var.reset(token)
        ctx_seen.append(f_ctx.result(timeout=5))

    assert ctx_seen == ["carried-value"]

    def hung():
        t = threading.current_thread()
        worker_info["daemon"] = t.daemon
        worker_info["in_cf_registry"] = t in concurrent.futures.thread._threads_queues
        started.set()
        release.wait(timeout=5.0)
        return "done"

    try:
        with pipeline._abandoning_executor(max_workers=1) as pool:
            f_hung = pool.submit(hung)
            assert started.wait(timeout=5.0)
            queued = [pool.submit(queued_ran.append, i) for i in range(3)]
        assert all(f.cancelled() for f in queued)
        assert not f_hung.done()
    finally:
        release.set()

    assert worker_info["daemon"] is True
    assert worker_info["in_cf_registry"] is False
    assert queued_ran == []


def test_high018_abandoned_straggler_does_not_block_interpreter_exit():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts")
    code = (
        "import threading, time\n"
        "from lib import pipeline\n"
        "started = threading.Event()\n"
        "def hang():\n"
        "    started.set()\n"
        "    time.sleep(30)\n"
        "with pipeline._abandoning_executor(max_workers=1) as pool:\n"
        "    pool.submit(hang)\n"
        "    assert started.wait(timeout=5)\n"
    )
    env = {**os.environ, "PYTHONPATH": scripts_dir}
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=5.0,
    )
    elapsed = time.monotonic() - t0
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 2.5, f"interpreter blocked on abandoned straggler ({elapsed:.2f}s)"


def test_high018_fetch_memo_waiter_unblocks_promptly_on_cancel():
    owner_started = threading.Event()
    release = threading.Event()
    cancel = threading.Event()

    def slow_owner():
        owner_started.set()
        release.wait(timeout=10.0)
        return {"items": [{"id": "1"}]}

    owner = threading.Thread(
        target=fetch_memo.fetch_once,
        args=(("pr23", "cancel-test"), slow_owner),
        kwargs={"timeout": 10.0},
        daemon=True,
    )
    owner.start()
    assert owner_started.wait(timeout=5.0)
    try:
        threading.Timer(0.05, cancel.set).start()
        t0 = time.monotonic()
        with pytest.raises(fetch_memo.MemoWaitTimeout) as caught:
            fetch_memo.fetch_once(
                ("pr23", "cancel-test"),
                slow_owner,
                timeout=10.0,
                cancel=cancel,
            )
        elapsed = time.monotonic() - t0
        assert caught.value.outcome_state == health.TIMEOUT
        assert elapsed < 0.8, f"cancelled waiter did not unblock promptly ({elapsed:.2f}s)"

        # Already-cancelled caller must not invoke a fresh fetch either.
        ran: list[int] = []
        with pytest.raises(fetch_memo.MemoWaitTimeout):
            fetch_memo.fetch_once(
                ("pr23", "fresh-key-cancelled"),
                lambda: (ran.append(1), {"items": []})[1],
                timeout=5.0,
                cancel=cancel,
            )
        assert ran == []
    finally:
        release.set()
        owner.join(timeout=5.0)


# ---------------------------------------------------------------------------
# HIGH-020: Phase 1 per-source normalization/post-processing containment
# ---------------------------------------------------------------------------


def test_high020_malformed_source_normalization_does_not_crash_healthy_sources(monkeypatch):
    today = dates.get_date_range(5)[0]
    healthy_reddit = [
        {
            "id": "R1",
            "title": "Containment test healthy discussion",
            "url": "https://reddit.com/r/example/comments/containment-1",
            "subreddit": "example",
            "date": today,
            "engagement": {"score": 90, "num_comments": 25},
            "selftext": "Containment test healthy discussion",
            "relevance": 0.9,
        }
    ]

    def fake_retrieve(**kwargs):
        if kwargs["source"] == "reddit":
            return healthy_reddit, {}
        return [{"id": "bad"}], {}

    orig_norm = pipeline._normalize_score_dedupe

    def exploding_norm(source, *args, **kwargs):
        if source == "hackernews":
            raise TypeError("unexpected schema drift in hackernews payload")
        return orig_norm(source, *args, **kwargs)

    monkeypatch.setattr(pipeline, "_retrieve_stream", fake_retrieve)
    monkeypatch.setattr(pipeline, "_normalize_score_dedupe", exploding_norm)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", mock.Mock())

    report = pipeline.run(
        topic="containment test",
        config={},
        depth="quick",
        requested_sources=["reddit", "hackernews"],
        mock=True,
        web_backend="none",
    )

    assert len(report.items_by_source.get("reddit", [])) == 1
    assert report.source_status["reddit"].state == health.OK
    assert report.source_status["hackernews"].state == health.SCHEMA_DRIFT
    assert "unexpected schema drift" in report.errors_by_source["hackernews"]


# ---------------------------------------------------------------------------
# HIGH-021: GitHub project/person pre-loop mock, plan, first-party & no-fallback
# ---------------------------------------------------------------------------


def test_high021_mock_true_never_calls_live_github_project_or_person():
    plan = {
        "intent": "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "topic",
        "subqueries": [
            {"label": "primary", "search_query": "octocat", "ranking_query": "octocat", "sources": ["github"]},
        ],
        "source_weights": {"github": 1.0},
    }
    with mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person, \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_project:
        r_person = pipeline.run(
            topic="octocat",
            config={},
            depth="quick",
            requested_sources=["github"],
            mock=True,
            web_backend="none",
            external_plan=plan,
            github_user="octocat",
        )
        r_project = pipeline.run(
            topic="octocat/hello-world",
            config={},
            depth="quick",
            requested_sources=["github"],
            mock=True,
            web_backend="none",
            external_plan=plan,
            github_repos=["octocat/hello-world"],
        )
    mock_person.assert_not_called()
    mock_project.assert_not_called()
    assert r_person is not None
    assert r_project is not None


def test_high021_external_plan_omitting_github_skips_project_and_person_preloop():
    plan_no_github = {
        "intent": "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "topic",
        "subqueries": [
            {"label": "primary", "search_query": "octocat", "ranking_query": "octocat", "sources": ["hackernews"]},
        ],
        "source_weights": {"hackernews": 1.0},
    }
    with mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person, \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_project:
        pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            requested_sources=["hackernews", "github"],
            mock=False,
            web_backend="none",
            external_plan=plan_no_github,
            github_user="octocat",
        )
        pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            requested_sources=["hackernews", "github"],
            mock=False,
            web_backend="none",
            external_plan=plan_no_github,
            github_repos=["octocat/hello-world"],
        )
    mock_person.assert_not_called()
    mock_project.assert_not_called()


def test_high021_quick_engine_plan_omitting_github_still_runs_pinned_github():
    quick_engine_plan = schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="topic",
        raw_topic="acme widget",
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query="acme widget",
                ranking_query="acme widget",
                sources=["reddit", "hackernews"],
                weight=1.0,
            ),
        ],
        source_weights={"reddit": 1.0, "hackernews": 1.0},
    )
    drill_plan = schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="topic",
        raw_topic="acme widget",
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query="acme widget",
                ranking_query="acme widget",
                sources=["reddit"],
                weight=1.0,
            ),
        ],
        source_weights={"reddit": 1.0},
        notes=["drill-mode"],
    )
    with mock.patch("lib.pipeline.planner.plan_query", return_value=quick_engine_plan), \
         mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_project, \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person:
        pipeline.run(
            topic="acme widget",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            mock=False,
            web_backend="none",
            github_repos=["acme/widget"],
        )
        pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            mock=False,
            web_backend="none",
            github_user="octocat",
        )
    mock_project.assert_called_once()
    mock_person.assert_called_once()

    # Drill-mode plan omitting github must still skip pinned lookups.
    with mock.patch("lib.pipeline.planner.plan_query", return_value=drill_plan), \
         mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_drill_project, \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_drill_person:
        pipeline.run(
            topic="acme widget",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            mock=False,
            web_backend="none",
            github_repos=["acme/widget"],
        )
        pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            mock=False,
            web_backend="none",
            github_user="octocat",
        )
    mock_drill_project.assert_not_called()
    mock_drill_person.assert_not_called()


@pytest.mark.parametrize("target", ["project", "person"])
def test_high021_missing_github_auth_records_skipped_unconfigured_without_fallback(target):
    plan = {
        "intent": "product" if target == "project" else "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "none",
        "subqueries": [
            {"label": "primary", "search_query": "acme widget", "ranking_query": "acme widget", "sources": ["github"]},
        ],
        "source_weights": {"github": 1.0},
    }
    extra = {"github_repos": ["acme/widget"]} if target == "project" else {"github_user": "octocat"}
    with mock.patch("lib.pipeline.github.resolve_token", return_value=None) as mock_resolve, \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_project, \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person, \
         mock.patch("lib.pipeline._retrieve_stream") as mock_retrieve:
        report = pipeline.run(
            topic="acme widget",
            config={},
            depth="default",
            requested_sources=["github"],
            mock=False,
            web_backend="none",
            external_plan=plan,
            **extra,
        )

    mock_resolve.assert_called_once_with(None)
    mock_project.assert_not_called()
    mock_person.assert_not_called()
    mock_retrieve.assert_not_called()
    outcome = report.source_status["github"]
    assert outcome.state == schema.SKIPPED_UNCONFIGURED
    assert outcome.attempted is False
    assert "no GitHub token or gh login available" in outcome.detail


@pytest.mark.parametrize("target", ["project", "person"])
def test_high021_pinned_github_resolves_token_once_on_authenticated_empty_result(target):
    plan = {
        "intent": "product" if target == "project" else "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "none",
        "subqueries": [
            {"label": "primary", "search_query": "acme widget", "ranking_query": "acme widget", "sources": ["github"]},
        ],
        "source_weights": {"github": 1.0},
    }
    extra = {"github_repos": ["acme/widget"]} if target == "project" else {"github_user": "octocat"}
    with mock.patch("lib.pipeline.github.resolve_token", return_value="ghp_from_cli") as mock_resolve, \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[]) as mock_project, \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person, \
         mock.patch("lib.pipeline._retrieve_stream") as mock_retrieve:
        report = pipeline.run(
            topic="acme widget",
            config={},
            depth="default",
            requested_sources=["github"],
            mock=False,
            web_backend="none",
            external_plan=plan,
            **extra,
        )

    mock_resolve.assert_called_once_with(None)
    if target == "project":
        mock_project.assert_called_once()
        assert mock_project.call_args.kwargs["token"] == "ghp_from_cli"
        mock_person.assert_not_called()
    else:
        mock_person.assert_called_once()
        assert mock_person.call_args.kwargs["token"] == "ghp_from_cli"
        mock_project.assert_not_called()
    mock_retrieve.assert_not_called()
    assert report.source_status["github"].state == schema.NO_RESULTS


def test_high021_person_and_project_preloop_pass_first_party_handles():
    today = dates.get_date_range(5)[0]
    # Unrelated title with zero lexical overlap against "What is @octocat doing on GitHub?",
    # alongside a strongly overlapping item so prune_low_relevance would drop it
    # unless first_party_handles exempts author="octocat".
    low_overlap_own = {
        "id": "GH-OWN",
        "title": "Fix SIMD register spilling in allocator",
        "snippet": "Rewrites loop invariant motion.",
        "url": "https://github.com/octocat/compiler/pull/42",
        "author": "octocat",
        "container": "octocat/compiler",
        "date": today,
        "engagement": {"comments": 5},
        "relevance": 0.1,
    }
    high_overlap_other = {
        "id": "GH-HIGH",
        "title": "octocat GitHub roadmap summary",
        "snippet": "What is octocat doing on GitHub",
        "url": "https://github.com/other/docs/issues/1",
        "author": "other",
        "container": "other/docs",
        "date": today,
        "engagement": {"comments": 50},
        "relevance": 0.95,
    }
    plan = {
        "intent": "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "topic",
        "subqueries": [
            {"label": "primary", "search_query": "octocat", "ranking_query": "octocat", "sources": ["github"]},
        ],
        "source_weights": {"github": 1.0},
    }
    seen_handles: list[set[str]] = []
    orig_norm = pipeline._normalize_score_dedupe

    def spy_norm(*args, **kwargs):
        seen_handles.append(set(kwargs.get("first_party_handles") or ()))
        return orig_norm(*args, **kwargs)

    with mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline.github.enrich_candidates_with_stars"), \
         mock.patch("lib.pipeline._normalize_score_dedupe", side_effect=spy_norm), \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[low_overlap_own, high_overlap_other]):
        person_report = pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            requested_sources=["github"],
            mock=False,
            web_backend="none",
            external_plan=plan,
            github_user="octocat",
        )

    assert seen_handles and "octocat" in seen_handles[0]
    assert "https://github.com/octocat/compiler/pull/42" in {
        i.url for i in person_report.items_by_source.get("github", [])
    }

    seen_handles.clear()
    with mock.patch("lib.pipeline._retrieve_stream", return_value=([], {})), \
         mock.patch("lib.pipeline._retry_thin_sources"), \
         mock.patch("lib.pipeline.github.enrich_candidates_with_stars"), \
         mock.patch("lib.pipeline._normalize_score_dedupe", side_effect=spy_norm), \
         mock.patch("lib.pipeline.github.search_github_project", return_value=[low_overlap_own, high_overlap_other]):
        project_report = pipeline.run(
            topic="octocat",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="quick",
            requested_sources=["github"],
            mock=False,
            web_backend="none",
            external_plan=plan,
            github_user="octocat",
            github_repos=["octocat/compiler"],
        )

    assert seen_handles and "octocat" in seen_handles[0]
    assert "https://github.com/octocat/compiler/pull/42" in {
        i.url for i in project_report.items_by_source.get("github", [])
    }


@pytest.mark.parametrize("mode", ["empty", "error"])
def test_high021_pinned_github_repos_suppresses_generic_fallback_on_empty_or_error(mode):
    plan = {
        "intent": "product",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "none",
        "subqueries": [
            {"label": "primary", "search_query": "acme widget", "ranking_query": "acme widget", "sources": ["github"]},
        ],
        "source_weights": {"github": 1.0},
    }
    project_mock = (
        mock.patch("lib.pipeline.github.search_github_project", return_value=[])
        if mode == "empty"
        else mock.patch("lib.pipeline.github.search_github_project", side_effect=RuntimeError("API down"))
    )
    with project_mock as mock_project, \
         mock.patch("lib.pipeline.github.search_github_person", return_value=[]) as mock_person, \
         mock.patch("lib.pipeline._retrieve_stream") as mock_retrieve:
        report = pipeline.run(
            topic="acme widget",
            config={"GITHUB_TOKEN": "dummy-token"},
            depth="default",
            requested_sources=["github"],
            mock=False,
            web_backend="none",
            external_plan=plan,
            github_repos=["acme/widget"],
            github_user="acme-dev",
        )

    mock_project.assert_called_once()
    mock_person.assert_not_called()
    mock_retrieve.assert_not_called()
    if mode == "empty":
        assert report.source_status["github"].state == schema.NO_RESULTS
        assert "Project mode found no activity for acme/widget" in report.source_status["github"].detail
    else:
        assert report.source_status["github"].state == health.ERROR
        assert report.errors_by_source["github"] == "Project-mode failed: API down"


# ---------------------------------------------------------------------------
# HIGH-024: Digg enrichment skipped under mock=True
# ---------------------------------------------------------------------------


def test_high024_mock_run_skips_digg_enrich_source_items(monkeypatch):
    mock_digg_item = schema.SourceItem(
        item_id="mock2def",
        source="digg",
        title="Second Digg cluster on ai agents",
        body="Another angle on ai agents.",
        url="https://di.gg/ai/mock2def-ai-agents",
        engagement={"postCount": 3, "uniqueAuthors": 2, "rank": 18, "rank_score": 33.0},
        metadata={"posts": []},
    )
    called: list[int] = []

    def fail_if_called(items, **kwargs):
        called.append(len(items))
        return items

    monkeypatch.setattr(pipeline.digg, "enrich_source_items", fail_if_called)

    # Mock finalize must not invoke digg.enrich_source_items or fixture_source_record.
    with mock.patch(
        "lib.http.fixture_source_record",
        side_effect=AssertionError("fixture_source_record must not fire for digg under mock=True"),
    ):
        finalized = pipeline._finalize_items_by_source(
            {"digg": [mock_digg_item]}, topic="ai agents", depth="quick", mock=True,
        )
    assert len(finalized["digg"]) == 1
    assert called == []

    # Full mock pipeline.run() with digg must also never touch digg.enrich_source_items.
    report = pipeline.run(
        topic="ai agents",
        config={},
        depth="quick",
        requested_sources=["digg"],
        mock=True,
        web_backend="none",
    )
    assert report is not None
    assert called == []

    # Positive control: mock=False still invokes digg.enrich_source_items.
    pipeline._finalize_items_by_source(
        {"digg": [mock_digg_item]}, topic="ai agents", depth="quick", mock=False,
    )
    assert called == [1]
