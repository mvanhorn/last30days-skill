"""Regression tests for PR 28: pipeline weighting/X-floor/SSH-ytdlp, fetch_memo lifecycle, schema & dates.

Covers HIGH-014, HIGH-019, HIGH-022, HIGH-023, HIGH-031, HIGH-033, HIGH-034, HIGH-035.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import threading
from unittest import mock

import pytest

from lib import (
    dates,
    fanout,
    fetch_memo,
    grounding,
    health,
    pipeline,
    schema,
    youtube_yt,
)


def _make_runtime() -> schema.ProviderRuntime:
    return schema.ProviderRuntime(
        reasoning_provider="local",
        planner_model="local",
        rerank_model="local",
    )


def _make_plan(
    sources: list[str],
    *,
    num_subqueries: int = 1,
    normalized: bool = True,
) -> schema.QueryPlan:
    sq_weight = (1.0 / num_subqueries) if normalized else 1.0
    subqueries = [
        schema.SubQuery(
            label=f"sq{i + 1}",
            search_query=f"topic angle {i + 1}",
            ranking_query=f"topic angle {i + 1}",
            sources=list(sources),
            weight=sq_weight,
        )
        for i in range(num_subqueries)
    ]
    src_weight = (1.0 / len(sources)) if (normalized and sources) else 1.0
    return schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="story",
        raw_topic="Acme Corp",
        subqueries=subqueries,
        source_weights={source: src_weight for source in sources},
    )


def _make_report(
    *,
    subqueries: list[schema.SubQuery],
    source_weights: dict[str, float],
    ranked_candidates: list[schema.Candidate],
    clusters: list[schema.Cluster],
    items_by_source: dict[str, list[schema.SourceItem]],
    artifacts: dict | None = None,
    source_status: dict[str, schema.SourceOutcome] | None = None,
) -> schema.Report:
    return schema.Report(
        topic="Acme rollout",
        range_from="2026-03-01",
        range_to="2026-03-31",
        generated_at="2026-03-31T12:00:00Z",
        provider_runtime=_make_runtime(),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic="Acme rollout",
            subqueries=subqueries,
            source_weights=source_weights,
        ),
        clusters=clusters,
        ranked_candidates=ranked_candidates,
        items_by_source=items_by_source,
        errors_by_source={},
        source_status=source_status or {},
        artifacts=artifacts or {},
    )


# ---------------------------------------------------------------------------
# HIGH-014: fetch_memo lifecycle resets & bounded LRU eviction
# ---------------------------------------------------------------------------


class TestHigh014FetchMemoLifecycleAndEviction:
    def test_pipeline_run_isolates_fetch_memo_standalone_and_shares_internal_subrun(self):
        # Populate the ambient default store before any run starts.
        fetch_memo.fetch_once(("x", "stale-key"), lambda: {"gen": "ambient"}, timeout=1)
        assert ("x", "stale-key") in fetch_memo._CACHE

        plan = {
            "intent": "general",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "test topic",
                    "ranking_query": "test topic",
                    "sources": ["reddit"],
                }
            ],
            "source_weights": {"reddit": 1.0},
        }
        seen_standalone: list[dict] = []

        def _retrieve_standalone(**_kwargs):
            seen_standalone.append(
                fetch_memo.fetch_once(("x", "stale-key"), lambda: {"gen": "fresh"}, timeout=1)
            )
            return [], {}

        # Standalone run enters an isolated run_scope: it fetches fresh instead
        # of seeing the ambient entry AND does not wipe the ambient store.
        with mock.patch.object(pipeline, "_retrieve_stream", side_effect=_retrieve_standalone):
            pipeline.run(
                topic="test topic",
                config={},
                depth="quick",
                requested_sources=["reddit"],
                mock=True,
                external_plan=plan,
                web_backend="none",
                internal_subrun=False,
            )
        assert seen_standalone == [{"gen": "fresh"}]
        assert fetch_memo._CACHE[("x", "stale-key")] == {"gen": "ambient"}

        # Internal subrun shares the caller's active run_scope.
        seen_subrun: list[dict] = []

        def _retrieve_subrun(**_kwargs):
            seen_subrun.append(
                fetch_memo.fetch_once(
                    ("x", "shared-in-run"), lambda: {"gen": "should-not-run"}, timeout=1
                )
            )
            return [], {}

        with fetch_memo.run_scope():
            fetch_memo.fetch_once(("x", "shared-in-run"), lambda: {"gen": "shared"}, timeout=1)
            with mock.patch.object(pipeline, "_retrieve_stream", side_effect=_retrieve_subrun):
                pipeline.run(
                    topic="test topic",
                    config={},
                    depth="quick",
                    requested_sources=["reddit"],
                    mock=True,
                    external_plan=plan,
                    web_backend="none",
                    internal_subrun=True,
                )
        assert seen_subrun == [{"gen": "shared"}]

    def test_concurrent_overlapping_pipeline_runs_isolate_fetch_memo_and_sequential_start_fresh(self):
        plan = {
            "intent": "general",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": "sq1",
                    "search_query": "topic",
                    "ranking_query": "topic",
                    "sources": ["reddit"],
                }
            ],
            "source_weights": {"reddit": 1.0},
        }
        both_started = threading.Barrier(2, timeout=5)
        b_finished = threading.Event()
        fetch_counts = {"A": 0, "B": 0, "C": 0}
        lock = threading.Lock()
        observed: dict[str, list[dict]] = {"A": [], "B": [], "C": []}

        def _retrieve(**kwargs):
            run_id = kwargs["topic"]

            def _fetch():
                with lock:
                    fetch_counts[run_id] += 1
                    return {"run": run_id, "n": fetch_counts[run_id]}

            first = fetch_memo.fetch_once(("shared", "key"), _fetch, timeout=5)
            observed[run_id].append(first)
            if run_id in ("A", "B"):
                both_started.wait()
                if run_id == "A":
                    # Wait until concurrent run B has populated its own scope and exited.
                    assert b_finished.wait(timeout=5)
                # Second lookup inside the same run must still hit this run's own entry
                # (neither cleared nor overwritten by the overlapping run).
                second = fetch_memo.fetch_once(("shared", "key"), _fetch, timeout=5)
                observed[run_id].append(second)
            return [], {}

        def _invoke(run_id: str) -> None:
            pipeline.run(
                topic=run_id,
                config={},
                depth="quick",
                requested_sources=["reddit"],
                mock=True,
                external_plan=plan,
                web_backend="none",
                internal_subrun=False,
            )
            if run_id == "B":
                b_finished.set()

        with mock.patch.object(pipeline, "_retrieve_stream", side_effect=_retrieve):
            with ThreadPoolExecutor(max_workers=2) as pool:
                fa = pool.submit(_invoke, "A")
                fb = pool.submit(_invoke, "B")
                fa.result(timeout=10)
                fb.result(timeout=10)
            # Sequential run C starts fresh without seeing A or B's entries.
            _invoke("C")

        assert fetch_counts == {"A": 1, "B": 1, "C": 1}
        assert observed["A"] == [{"run": "A", "n": 1}, {"run": "A", "n": 1}]
        assert observed["B"] == [{"run": "B", "n": 1}, {"run": "B", "n": 1}]
        assert observed["C"] == [{"run": "C", "n": 1}]

    def test_competitor_fanout_scopes_fetch_memo_for_parallel_subruns_without_wiping_ambient(self):
        stale_calls = 0

        def _stale():
            nonlocal stale_calls
            stale_calls += 1
            return {"gen": "stale"}

        fetch_memo.fetch_once(("digg", "shared"), _stale, timeout=1)
        assert stale_calls == 1

        fresh_calls = 0
        barrier = threading.Barrier(2, timeout=5)
        dummy_report = _make_report(
            subqueries=[
                schema.SubQuery(
                    label="p", search_query="q", ranking_query="q", sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0},
            ranked_candidates=[],
            clusters=[],
            items_by_source={},
        )

        def _fresh():
            nonlocal fresh_calls
            fresh_calls += 1
            return {"gen": "fresh"}

        def _subrun(_entity=None):
            barrier.wait()
            val = fetch_memo.fetch_once(("digg", "shared"), _fresh, timeout=5)
            assert val == {"gen": "fresh"}
            return dummy_report

        results = fanout.run_competitor_fanout(
            main_topic="Alpha",
            main_runner=_subrun,
            competitors=["Beta"],
            competitor_runner=_subrun,
        )
        assert len(results) == 2
        # Parallel subruns ignored stale ambient entry and coalesced onto one fresh fetch.
        assert fresh_calls == 1
        # Ambient store outside fanout was never wiped.
        assert fetch_memo._CACHE[("digg", "shared")] == {"gen": "stale"}

    def test_enrich_nominations_scopes_fetch_memo_across_subruns(self):
        fetch_memo.fetch_once(("polymarket", "stale"), lambda: {"old": 1}, timeout=1)
        assert ("polymarket", "stale") in fetch_memo._CACHE

        fresh_calls = 0
        seen: list[dict] = []

        def _fresh():
            nonlocal fresh_calls
            fresh_calls += 1
            return {"fresh": fresh_calls}

        def _fake_run(**_kwargs):
            seen.append(fetch_memo.fetch_once(("polymarket", "stale"), _fresh, timeout=5))
            return _make_report(
                subqueries=[
                    schema.SubQuery(
                        label="p", search_query="q", ranking_query="q", sources=["reddit"],
                    )
                ],
                source_weights={"reddit": 1.0},
                ranked_candidates=[],
                clusters=[],
                items_by_source={},
            )

        nominations = [
            pipeline.Nomination(name="Nom 1", seed_score=50.0, items=[], summary="s1"),
            pipeline.Nomination(name="Nom 2", seed_score=40.0, items=[], summary="s2"),
        ]
        with mock.patch.object(pipeline, "run", side_effect=_fake_run):
            enriched = pipeline.enrich_nominations(
                nominations, config={}, mock=True, max_workers=1,
            )
        assert len(enriched) == 2
        # Both nominations shared one fresh fetch in the batch scope (ignoring ambient stale).
        assert fresh_calls == 1
        assert seen == [{"fresh": 1}, {"fresh": 1}]
        assert fetch_memo._CACHE[("polymarket", "stale")] == {"old": 1}

    def test_discovery_sweep_isolates_fetch_memo(self):
        fetch_memo.fetch_once(("x", "old-sweep"), lambda: {"old": True}, timeout=1)
        assert ("x", "old-sweep") in fetch_memo._CACHE

        seen: list[dict] = []

        def _fake_fetch(*_args, **_kwargs):
            seen.append(
                fetch_memo.fetch_once(("x", "old-sweep"), lambda: {"old": False}, timeout=1)
            )
            return [], None

        with mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=_fake_fetch):
            pipeline.run_discover_nominate(
                domain="ai",
                config={},
                requested_sources=["reddit"],
                mock=True,
            )
        assert seen == [{"old": False}]
        assert fetch_memo._CACHE[("x", "old-sweep")] == {"old": True}

    def test_lru_eviction_bounds_cache_and_keeps_recently_used_entries(self, monkeypatch):
        monkeypatch.setattr(fetch_memo, "MAX_ENTRIES", 3)
        calls: dict[str, int] = {}

        def _fetch(name: str):
            def _inner():
                calls[name] = calls.get(name, 0) + 1
                return {"k": name, "n": calls[name]}
            return _inner

        fetch_memo.fetch_once("k1", _fetch("k1"), timeout=1)
        fetch_memo.fetch_once("k2", _fetch("k2"), timeout=1)
        fetch_memo.fetch_once("k3", _fetch("k3"), timeout=1)
        # Touch k1 so k2 becomes the least-recently-used entry.
        assert fetch_memo.fetch_once("k1", _fetch("k1"), timeout=1) == {"k": "k1", "n": 1}
        fetch_memo.fetch_once("k4", _fetch("k4"), timeout=1)

        assert len(fetch_memo._CACHE) == 3
        assert "k2" not in fetch_memo._CACHE
        assert list(fetch_memo._CACHE.keys()) == ["k3", "k1", "k4"]
        # Evicted k2 refetches; retained k1 serves from cache.
        assert fetch_memo.fetch_once("k1", _fetch("k1"), timeout=1) == {"k": "k1", "n": 1}
        assert fetch_memo.fetch_once("k2", _fetch("k2"), timeout=1) == {"k": "k2", "n": 2}
        assert len(fetch_memo._CACHE) == 3

    def test_reset_and_eviction_preserve_inflight_singleflight_safety(self, monkeypatch):
        monkeypatch.setattr(fetch_memo, "MAX_ENTRIES", 2)
        owner_entered = threading.Event()
        release_owner = threading.Event()

        def _slow_owner():
            owner_entered.set()
            release_owner.wait(timeout=5)
            return {"version": "old-inflight"}

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_owner = pool.submit(fetch_memo.fetch_once, "shared", _slow_owner, timeout=5)
            assert owner_entered.wait(timeout=5)
            waiter_waiting = threading.Event()
            with fetch_memo._LOCK:
                live_slot = fetch_memo._INFLIGHT["shared"]
            real_wait = live_slot.event.wait

            def _observed_wait(timeout=None):
                waiter_waiting.set()
                return real_wait(timeout)

            live_slot.event.wait = _observed_wait
            f_waiter = pool.submit(
                fetch_memo.fetch_once, "shared", lambda: {"version": "unused"}, timeout=5,
            )
            assert waiter_waiting.wait(timeout=5)

            fetch_memo.reset()
            # Fill cache past MAX_ENTRIES after reset while old owner is still running.
            fetch_memo.fetch_once("a", lambda: 1, timeout=1)
            fetch_memo.fetch_once("b", lambda: 2, timeout=1)
            fetch_memo.fetch_once("c", lambda: 3, timeout=1)
            assert len(fetch_memo._CACHE) == 2

            release_owner.set()
            assert f_owner.result(timeout=5) == {"version": "old-inflight"}
            assert f_waiter.result(timeout=5) == {"version": "old-inflight"}

        # Stale owner must not have polluted the post-reset cache.
        assert "shared" not in fetch_memo._CACHE
        assert fetch_memo.fetch_once("shared", lambda: {"version": "fresh"}, timeout=1) == {
            "version": "fresh",
        }


# ---------------------------------------------------------------------------
# HIGH-019: Deferred X relevance floor consistency across streams & merged view
# ---------------------------------------------------------------------------


class TestHigh019DeferredXRelevanceFloorConsistency:
    def _run_x_floor_pipeline(
        self,
        stream_by_label: dict[str, list[dict]],
        *,
        topic: str = "Acme launch @acmedev",
        x_handle: str = "acmedev",
    ) -> tuple[schema.Report, dict[tuple[str, str], list[schema.SourceItem]]]:
        plan = {
            "intent": "general",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": label,
                    "search_query": f"acme {label}",
                    "ranking_query": f"acme {label} benchmark release",
                    "sources": ["x"],
                }
                for label in stream_by_label
            ],
            "source_weights": {"x": 1.0},
        }

        def _fake_retrieve(**kwargs):
            label = kwargs["subquery"].label
            return list(stream_by_label.get(label, [])), {}

        with mock.patch.object(pipeline, "_retrieve_stream", side_effect=_fake_retrieve), \
             mock.patch.object(pipeline, "_retry_thin_sources"), \
             mock.patch.object(pipeline, "_run_supplemental_searches"), \
             mock.patch.object(pipeline, "weighted_rrf", wraps=pipeline.weighted_rrf) as fuse:
            report = pipeline.run(
                topic=topic,
                config={},
                depth="default",
                requested_sources=["x"],
                mock=True,
                x_handle=x_handle,
                external_plan=plan,
                web_backend="none",
                as_of_date="2026-03-31",
            )
        return report, fuse.call_args.args[0]

    def test_weak_subquery_does_not_leak_dropped_posts_into_fusion(self):
        # sq1 has only an off-topic zero-engagement post that fails the X floor.
        # sq2 has an on-topic post by the resolved subject handle that passes.
        weak_post = {
            "id": "X-weak",
            "text": "random unrelated sandwich lunch photo",
            "author_handle": "bystander",
            "url": "https://x.com/bystander/status/1001",
            "date": "2026-03-30",
            "engagement": {"likes": 0, "reposts": 0, "replies": 0},
        }
        strong_post = {
            "id": "X-strong",
            "text": "Acme benchmark release shipped today with 2x speedup",
            "author_handle": "acmedev",
            "url": "https://x.com/acmedev/status/2002",
            "date": "2026-03-30",
            "engagement": {"likes": 250, "reposts": 40, "replies": 15},
        }

        report, fused_streams = self._run_x_floor_pipeline(
            {"sq1": [weak_post], "sq2": [strong_post]},
        )

        assert fused_streams.get(("sq1", "x")) == []
        assert [i.url for i in fused_streams.get(("sq2", "x"), [])] == [
            "https://x.com/acmedev/status/2002"
        ]
        report_urls = {item.url for item in report.items_by_source.get("x", [])}
        candidate_urls = {cand.url for cand in report.ranked_candidates}
        assert report_urls == {"https://x.com/acmedev/status/2002"}
        assert candidate_urls == report_urls
        assert report.source_status["x"].items_returned == 1

    def test_all_fail_rescue_stays_consistent_across_both_views(self):
        # Both posts fail the deferred X relevance/engagement floor (zero
        # engagement + low overlap with the ranking query), triggering the
        # all-fail rescue consistently across merged and per-query views.
        weak_1 = {
            "id": "X-w1",
            "text": "acme coffee mug sitting quietly beside breakfast sandwich photo",
            "author_handle": "user1",
            "url": "https://x.com/user1/status/3001",
            "date": "2026-03-30",
            "engagement": {"likes": 0, "reposts": 0, "replies": 0},
        }
        weak_2 = {
            "id": "X-w2",
            "text": "acme umbrella weather forecast drizzle walking downtown afternoon",
            "author_handle": "user2",
            "url": "https://x.com/user2/status/3002",
            "date": "2026-03-30",
            "engagement": {"likes": 0, "reposts": 0, "replies": 0},
        }

        report, fused_streams = self._run_x_floor_pipeline({"sq1": [weak_1], "sq2": [weak_2]})

        # Confirm the posts genuinely failed the floor (rescued, not passed).
        from lib import signals
        assert signals.prune_low_relevance(
            report.items_by_source["x"],
            first_party_handles={"acmedev"},
            passing_only=True,
        ) == []

        fused_urls = {
            item.url for stream in fused_streams.values() for item in stream
        }
        report_urls = {item.url for item in report.items_by_source.get("x", [])}
        candidate_urls = {cand.url for cand in report.ranked_candidates}
        expected = {
            "https://x.com/user1/status/3001",
            "https://x.com/user2/status/3002",
        }
        assert fused_urls == expected
        assert report_urls == expected
        assert candidate_urls == expected
        assert report.source_status["x"].items_returned == 2

    def test_duplicate_empty_url_item_id_does_not_collide_during_stream_sync(self):
        # Two distinct posts with empty URL and identical item_id across streams:
        # sq1's fails the floor, sq2's passes (authored by resolved handle).
        weak_no_url = {
            "id": "dup-id",
            "text": "unrelated noise text",
            "author_handle": "stranger",
            "url": "",
            "date": "2026-03-30",
            "engagement": {"likes": 0},
        }
        strong_no_url = {
            "id": "dup-id",
            "text": "Acme release announcement benchmark notes",
            "author_handle": "acmedev",
            "url": "",
            "date": "2026-03-30",
            "engagement": {"likes": 120},
        }

        report, fused_streams = self._run_x_floor_pipeline(
            {"sq1": [weak_no_url], "sq2": [strong_no_url]},
        )

        assert fused_streams.get(("sq1", "x")) == []
        assert len(fused_streams.get(("sq2", "x"), [])) == 1
        x_items = report.items_by_source.get("x", [])
        assert len(x_items) == 1
        assert x_items[0].author == "acmedev"
        assert len(report.ranked_candidates) == 1
        assert report.ranked_candidates[0].subquery_labels == ["sq2"]
        assert report.source_status["x"].items_returned == 1

    def test_duplicate_url_failing_in_one_subquery_does_not_survive_in_that_stream(self):
        # Same URL retrieved in sq1 (off-topic text/low relevance) and sq2 (authored by
        # resolved subject handle so it passes). Only sq2's surviving copy enters fusion.
        shared_url = "https://x.com/shared/status/4004"
        failing_copy = {
            "id": "X-4004",
            "text": "completely unrelated lunch photo",
            "author_handle": "bystander",
            "url": shared_url,
            "date": "2026-03-30",
            "engagement": {"likes": 0},
        }
        passing_copy = {
            "id": "X-4004",
            "text": "Acme benchmark release shipped today",
            "author_handle": "acmedev",
            "url": shared_url,
            "date": "2026-03-30",
            "engagement": {"likes": 90},
        }

        report, fused_streams = self._run_x_floor_pipeline(
            {"sq1": [failing_copy], "sq2": [passing_copy]},
        )

        assert fused_streams.get(("sq1", "x")) == []
        assert len(fused_streams.get(("sq2", "x"), [])) == 1
        assert len(report.items_by_source.get("x", [])) == 1
        assert report.source_status["x"].items_returned == 1
        assert report.ranked_candidates[0].subquery_labels == ["sq2"]


# ---------------------------------------------------------------------------
# HIGH-022: Post-plan source & subquery weight normalization
# ---------------------------------------------------------------------------


class TestHigh022PostPlanWeightNormalization:
    TEN_SOURCES = [
        "reddit", "x", "youtube", "hackernews", "polymarket",
        "github", "grounding", "tiktok", "instagram", "bluesky",
    ]

    def test_ensure_devto_in_plan_uses_mean_weight(self):
        plan = _make_plan(self.TEN_SOURCES)
        pipeline._ensure_devto_in_plan(plan, [*self.TEN_SOURCES, "devto"])

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        assert plan.source_weights["devto"] == pytest.approx(1.0 / 11)
        assert plan.source_weights["reddit"] == pytest.approx(1.0 / 11)

    def test_ensure_unplanned_lanes_in_host_plan_uses_mean_weight(self):
        plan = _make_plan(self.TEN_SOURCES)
        pipeline._ensure_unplanned_lanes_in_host_plan(
            plan,
            [*self.TEN_SOURCES, "digg", "arxiv"],
            installed=True,
        )

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        for source in (*self.TEN_SOURCES, "digg", "arxiv"):
            assert plan.source_weights[source] == pytest.approx(1.0 / 12)

    def test_ensure_company_jobs_in_host_plan_uses_mean_weight(self):
        plan = _make_plan(self.TEN_SOURCES)
        pipeline._ensure_company_jobs_in_host_plan(
            plan, [*self.TEN_SOURCES, "jobs"], "official site acme.com",
        )

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        assert plan.source_weights["jobs"] == pytest.approx(1.0 / 11)
        assert plan.source_weights["reddit"] == pytest.approx(1.0 / 11)

    def test_ensure_jobs_in_plan_uses_mean_weight_and_renormalizes(self):
        plan = _make_plan(self.TEN_SOURCES)
        pipeline._ensure_jobs_in_plan(
            plan, [*self.TEN_SOURCES, "jobs"], explicit=True, topic="Acme Corp",
        )

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        assert plan.source_weights["jobs"] == pytest.approx(1.0 / 11)
        assert plan.source_weights["reddit"] == pytest.approx(1.0 / 11)

    def test_ensure_perplexity_in_plan_normalizes_both_subquery_and_source_weights(self):
        plan = _make_plan(self.TEN_SOURCES, num_subqueries=3)
        pipeline._ensure_perplexity_in_plan(
            plan, "Acme Corp", [*self.TEN_SOURCES, "perplexity"], force=True,
        )

        assert sum(sq.weight for sq in plan.subqueries) == pytest.approx(1.0)
        assert len(plan.subqueries) == 4
        for sq in plan.subqueries:
            assert sq.weight == pytest.approx(0.25)

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        assert plan.source_weights["perplexity"] == pytest.approx(1.0 / 11)
        assert plan.source_weights["reddit"] == pytest.approx(1.0 / 11)

    def test_sequential_injections_do_not_exponentially_halve_primary_sources(self):
        plan = _make_plan(self.TEN_SOURCES, num_subqueries=3)
        available = [*self.TEN_SOURCES, "digg", "jobs", "devto", "perplexity"]

        pipeline._ensure_unplanned_lanes_in_host_plan(plan, available, installed=True)
        pipeline._ensure_company_jobs_in_host_plan(plan, available, "ticker ACME")
        pipeline._ensure_devto_in_plan(plan, available)
        pipeline._ensure_perplexity_in_plan(plan, "Acme Corp", available, force=True)

        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        for source in available:
            assert plan.source_weights[source] == pytest.approx(1.0 / len(available))
        assert all(sq.weight == pytest.approx(0.25) for sq in plan.subqueries)

    def test_empty_and_zero_weight_maps_fall_back_safely_to_one(self):
        assert pipeline._mean_source_weight({}) == 1.0
        assert pipeline._mean_source_weight({"a": 0.0, "b": 0.0}) == 1.0

        plan = _make_plan(["perplexity"], num_subqueries=1)
        plan.source_weights = {}
        pipeline._ensure_perplexity_in_plan(plan, "Acme", ["perplexity"], force=True)
        assert len(plan.subqueries) == 1
        assert plan.subqueries[0].weight == pytest.approx(1.0)
        assert plan.source_weights["perplexity"] == pytest.approx(1.0)

    def test_sanitize_plan_and_backfill_use_planned_mean_and_preserve_explicit_weights(self):
        from lib import planner

        raw = {
            "intent": "general",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "source_weights": {source: 0.1 for source in self.TEN_SOURCES},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "Acme Corp",
                    "ranking_query": "What is new with Acme Corp?",
                    "sources": list(self.TEN_SOURCES),
                    "weight": 1.0,
                }
            ],
        }
        available = [*self.TEN_SOURCES, "digg"]
        plan = planner._sanitize_plan(
            raw, "Acme Corp", available, None, "default", honor_plan_sources=True,
        )
        pipeline._ensure_unplanned_lanes_in_host_plan(plan, available, installed=True)

        assert "digg" in plan.subqueries[0].sources
        assert sum(plan.source_weights.values()) == pytest.approx(1.0)
        assert plan.source_weights["digg"] == pytest.approx(1.0 / 11)
        assert plan.source_weights["reddit"] == pytest.approx(1.0 / 11)

        # Explicit unequal weights are preserved while omitted eligible source gets their mean.
        weighted_raw = {
            **raw,
            "source_weights": {"reddit": 0.6, "x": 0.4},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "Acme Corp",
                    "ranking_query": "What is new with Acme Corp?",
                    "sources": ["reddit", "x"],
                    "weight": 1.0,
                }
            ],
        }
        weighted_plan = planner._sanitize_plan(
            weighted_raw, "Acme Corp", ["reddit", "x", "digg"], None, "default",
            honor_plan_sources=True,
        )
        # mean(0.6, 0.4) = 0.5 -> normalized [0.6/1.5, 0.4/1.5, 0.5/1.5]
        assert weighted_plan.source_weights["reddit"] == pytest.approx(0.6 / 1.5)
        assert weighted_plan.source_weights["x"] == pytest.approx(0.4 / 1.5)
        assert weighted_plan.source_weights["digg"] == pytest.approx(0.5 / 1.5)

        # Explicit weight for a backfilled source in raw plan is respected.
        explicit_raw = {
            **weighted_raw,
            "source_weights": {"reddit": 0.6, "x": 0.2, "digg": 0.2},
        }
        explicit_plan = planner._sanitize_plan(
            explicit_raw, "Acme Corp", ["reddit", "x", "digg"], None, "default",
            honor_plan_sources=True,
        )
        assert explicit_plan.source_weights["reddit"] == pytest.approx(0.6)
        assert explicit_plan.source_weights["digg"] == pytest.approx(0.2)

    def test_sanitize_plan_preserves_raw_unnormalized_source_boosts_and_demotions(self):
        from lib import planner

        # Raw boost map (reddit=3.0, x unweighted): x defaults to 1.0 so reddit keeps 3:1 boost.
        boosted_raw = {
            "intent": "general",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "story",
            "source_weights": {"reddit": 3.0},
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "Acme Corp",
                    "ranking_query": "What is new with Acme Corp?",
                    "sources": ["reddit", "x"],
                    "weight": 1.0,
                }
            ],
        }
        boosted_plan = planner._sanitize_plan(
            boosted_raw, "Acme Corp", ["reddit", "x"], None, "default",
            honor_plan_sources=True,
        )
        assert boosted_plan.source_weights["reddit"] == pytest.approx(0.75)
        assert boosted_plan.source_weights["x"] == pytest.approx(0.25)

        # Raw demotion map (reddit=0.5, x unweighted): x defaults to 1.0 so reddit stays 1:2.
        demoted_raw = {**boosted_raw, "source_weights": {"reddit": 0.5}}
        demoted_plan = planner._sanitize_plan(
            demoted_raw, "Acme Corp", ["reddit", "x"], None, "default",
            honor_plan_sources=True,
        )
        assert demoted_plan.source_weights["reddit"] == pytest.approx(1.0 / 3)
        assert demoted_plan.source_weights["x"] == pytest.approx(2.0 / 3)

        # Multi-source raw boost map (reddit=3.0, youtube=2.0, x unweighted -> x=1.0).
        multi_raw = {**boosted_raw, "source_weights": {"reddit": 3.0, "youtube": 2.0}}
        multi_plan = planner._sanitize_plan(
            multi_raw, "Acme Corp", ["reddit", "youtube", "x"], None, "default",
            honor_plan_sources=True,
        )
        assert multi_plan.source_weights["reddit"] == pytest.approx(3.0 / 6.0)
        assert multi_plan.source_weights["youtube"] == pytest.approx(2.0 / 6.0)
        assert multi_plan.source_weights["x"] == pytest.approx(1.0 / 6.0)

    def test_sanitize_plan_ignores_filtered_out_and_overflow_weights_for_default(self):
        from lib import planner, signals

        base_raw = {
            "intent": "product",
            "freshness_mode": "last30days",
            "cluster_mode": "story",
            "subqueries": [
                {
                    "label": "primary",
                    "search_query": "Acme Corp",
                    "ranking_query": "Acme Corp reviews",
                    "sources": ["reddit", "x"],
                }
            ],
            "source_weights": {"reddit": 0.6, "x": 0.4},
        }
        baseline = planner._sanitize_plan(
            base_raw, "Acme Corp", ["reddit", "x", "digg"], None, "default",
            honor_plan_sources=True,
        )
        # Filtered weights sum to 1.0 (0.6 + 0.4), so unweighted digg gets 0.5 -> 0.5 / 1.5 = 1/3.
        assert baseline.source_weights["reddit"] == pytest.approx(0.6 / 1.5)
        assert baseline.source_weights["x"] == pytest.approx(0.4 / 1.5)
        assert baseline.source_weights["digg"] == pytest.approx(0.5 / 1.5)

        # Adding a weight for an unavailable source must not skew the default weight.
        with_unavailable = {
            **base_raw,
            "source_weights": {"reddit": 0.6, "x": 0.4, "youtube": 0.2},
        }
        plan_unavailable = planner._sanitize_plan(
            with_unavailable, "Acme Corp", ["reddit", "x", "digg"], None, "default",
            honor_plan_sources=True,
        )
        assert plan_unavailable.source_weights == pytest.approx(baseline.source_weights)

        # Adding a weight for a source excluded via requested_sources must not skew it either.
        plan_excluded = planner._sanitize_plan(
            with_unavailable,
            "Acme Corp",
            ["reddit", "x", "digg", "youtube"],
            ["reddit", "x", "digg"],
            "default",
            honor_plan_sources=True,
        )
        assert plan_excluded.source_weights == pytest.approx(baseline.source_weights)

        # Huge / non-finite / bool weights on excluded or eligible sources must not crash planning.
        with_huge_excluded = {
            **base_raw,
            "source_weights": {"reddit": 0.6, "x": 0.4, "youtube": 10**400},
        }
        plan_huge_excluded = planner._sanitize_plan(
            with_huge_excluded, "Acme Corp", ["reddit", "x", "digg"], None, "default",
            honor_plan_sources=True,
        )
        assert plan_huge_excluded.source_weights == pytest.approx(baseline.source_weights)

        for bad_weight in (10**400, -10**400, float("inf"), float("nan"), True, "not-a-number"):
            with_bad_eligible = {
                **base_raw,
                "source_weights": {"reddit": 0.6, "x": 0.4, "digg": bad_weight},
            }
            plan_bad_eligible = planner._sanitize_plan(
                with_bad_eligible, "Acme Corp", ["reddit", "x", "digg"], None, "default",
                honor_plan_sources=True,
            )
            assert plan_bad_eligible.source_weights == pytest.approx(baseline.source_weights)

        # Normalized plan where one planned source is disabled/unavailable
        # (reddit=0.5, x=0.3, youtube=0.2 -> raw sum 1.0, filtered sum 0.8):
        # unweighted digg must receive the filtered mean 0.4 (0.4 / 1.2 = 1/3),
        # not 1.0.
        normalized_with_disabled = {
            **base_raw,
            "source_weights": {"reddit": 0.5, "x": 0.3, "youtube": 0.2},
        }
        plan_disabled_unavailable = planner._sanitize_plan(
            normalized_with_disabled,
            "Acme Corp",
            ["reddit", "x", "digg"],
            None,
            "default",
            honor_plan_sources=True,
        )
        assert plan_disabled_unavailable.source_weights["reddit"] == pytest.approx(0.5 / 1.2)
        assert plan_disabled_unavailable.source_weights["x"] == pytest.approx(0.3 / 1.2)
        assert plan_disabled_unavailable.source_weights["digg"] == pytest.approx(0.4 / 1.2)

        plan_disabled_excluded = planner._sanitize_plan(
            normalized_with_disabled,
            "Acme Corp",
            ["reddit", "x", "digg", "youtube"],
            ["reddit", "x", "digg"],
            "default",
            honor_plan_sources=True,
        )
        assert plan_disabled_excluded.source_weights == pytest.approx(
            plan_disabled_unavailable.source_weights
        )

        # Unrecognized source names / typos (e.g. "youtub": 0.5 alongside "reddit": 0.5)
        # must NOT be counted as normalized planned sources; X keeps default 1.0
        # so Reddit keeps its 1:2 demotion.
        typo_raw = {
            **base_raw,
            "source_weights": {"reddit": 0.5, "youtub": 0.5},
        }
        plan_with_typo = planner._sanitize_plan(
            typo_raw, "Acme Corp", ["reddit", "x"], None, "default",
            honor_plan_sources=True,
        )
        assert plan_with_typo.source_weights["reddit"] == pytest.approx(1.0 / 3)
        assert plan_with_typo.source_weights["x"] == pytest.approx(2.0 / 3)
        assert "youtub" not in plan_with_typo.source_weights
        assert pipeline.KNOWN_SEARCH_SOURCES <= planner.KNOWN_SOURCES
        assert set(signals._ENGAGEMENT_LOG_REFERENCE) <= planner.KNOWN_SOURCES
        assert signals._NO_ENGAGEMENT_SCORE_SOURCES <= planner.KNOWN_SOURCES


# ---------------------------------------------------------------------------
# HIGH-023: YouTube SSH host routing without local yt-dlp binary
# ---------------------------------------------------------------------------


class TestHigh023YoutubeSshHostAvailabilityAndStream:
    def test_available_sources_includes_youtube_with_ssh_host_in_env_without_local_ytdlp(
        self, monkeypatch,
    ):
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: None)
        monkeypatch.setenv("LAST30DAYS_YOUTUBE_SSH_HOST", "macmini")

        assert "youtube" in pipeline.available_sources({})

    def test_available_sources_includes_youtube_with_ssh_host_in_config_without_env(
        self, monkeypatch,
    ):
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: None)
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)

        without_ssh = pipeline.available_sources({})
        assert "youtube" not in without_ssh
        assert "tiktok" not in without_ssh
        assert "instagram" not in without_ssh

        with_ssh = pipeline.available_sources({"LAST30DAYS_YOUTUBE_SSH_HOST": "macmini"})
        assert "youtube" in with_ssh
        assert "tiktok" in with_ssh
        assert "instagram" in with_ssh
        # Config-scoped check must not pollute process-wide os.environ.
        assert "LAST30DAYS_YOUTUBE_SSH_HOST" not in os.environ

    def test_retrieve_stream_uses_ssh_ytdlp_without_local_binary_from_config(
        self, monkeypatch,
    ):
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: None)
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)

        observed: dict[str, object] = {}

        def _fake_search_and_transcribe(*_args, **_kwargs):
            observed["ssh_host"] = youtube_yt._ytdlp_ssh_host()
            observed["is_installed"] = youtube_yt.is_ytdlp_installed()
            return {
                "items": [
                    {
                        "video_id": f"v{i}",
                        "title": f"Acme video {i}",
                        "url": f"https://www.youtube.com/watch?v=v{i}",
                        "date": "2026-03-20",
                        "transcript_snippet": "substantive transcript",
                    }
                    for i in range(4)
                ]
            }

        with mock.patch.object(
            youtube_yt, "search_and_transcribe", side_effect=_fake_search_and_transcribe,
        ) as ytdlp_search, mock.patch.object(
            youtube_yt, "search_youtube_sc",
        ) as sc_search, mock.patch.object(
            youtube_yt, "enrich_with_comments", side_effect=lambda items, **_k: items,
        ):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Acme",
                subquery=schema.SubQuery(
                    label="primary",
                    search_query="Acme",
                    ranking_query="Acme",
                    sources=["youtube"],
                ),
                source="youtube",
                config={
                    "LAST30DAYS_YOUTUBE_SSH_HOST": "macmini",
                    "SCRAPECREATORS_API_KEY": "dummy-sc-key",
                },
                depth="default",
                date_range=("2026-03-01", "2026-03-31"),
                runtime=_make_runtime(),
                mock=False,
            )

        assert ytdlp_search.call_count == 1
        sc_search.assert_not_called()
        assert observed == {"ssh_host": "macmini", "is_installed": True}
        assert len(items) == 4
        assert "_source_outcome" not in artifact
        assert "LAST30DAYS_YOUTUBE_SSH_HOST" not in os.environ

    @pytest.mark.parametrize(
        ("source", "urls"),
        [
            (
                "tiktok",
                [
                    "https://www.tiktok.com/@acmedev/video/7621000000000000001",
                    "https://www.tiktok.com/@acmedev/video/7621000000000000002",
                ],
            ),
            (
                "instagram",
                [
                    "https://www.instagram.com/reel/DWabcd12341/",
                    "https://www.instagram.com/reel/DWabcd12342/",
                ],
            ),
        ],
    )
    def test_shortform_streams_and_multibatch_workers_inherit_config_ssh_host_scope(
        self, monkeypatch, source, urls,
    ):
        import json
        from lib import shortform_oss, subproc

        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: None)
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)

        config = {
            "LAST30DAYS_YOUTUBE_SSH_HOST": "macmini",
            "EXCLUDE_SOURCES": "tiktok_comments,instagram_comments",
        }
        assert source in pipeline.available_sources(config)

        seen_cmds: list[list[str]] = []
        seen_enrich_worker_hosts: list[str | None] = []
        lock = threading.Lock()

        def _fake_run(cmd, *, input_text="", **_kwargs):
            with lock:
                seen_cmds.append(list(cmd))
            if "--flat-playlist" in " ".join(cmd):
                return subproc.SubprocResult(returncode=0, stdout="", stderr="")
            url = input_text.strip()
            vid = url.rstrip("/").split("/")[-1]
            with lock:
                seen_enrich_worker_hosts.append(youtube_yt._ytdlp_ssh_host())
            payload = json.dumps(
                {
                    "id": vid,
                    "uploader": "acmedev",
                    "timestamp": 1774000000,  # 2026-03-20 UTC
                    "view_count": 500,
                    "like_count": 40,
                    "comment_count": 5,
                    "description": "Acme launch benchmark review",
                    "webpage_url": url,
                }
            )
            return subproc.SubprocResult(returncode=0, stdout=payload + "\n", stderr="")

        with mock.patch.object(
            shortform_oss, "discover_urls", return_value=(urls, "keyless"),
        ), mock.patch.object(
            shortform_oss, "_outside_window", return_value=False,
        ), mock.patch.object(
            shortform_oss.subproc, "run_with_timeout", side_effect=_fake_run,
        ), mock.patch.object(
            pipeline.tiktok, "enrich_with_comments", side_effect=lambda items, **_k: items,
        ), mock.patch.object(
            pipeline.instagram, "enrich_with_comments", side_effect=lambda items, **_k: items,
        ):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Acme launch",
                subquery=schema.SubQuery(
                    label="primary",
                    search_query="Acme launch",
                    ranking_query="Acme launch",
                    sources=[source],
                ),
                source=source,
                config=config,
                depth="default",
                date_range=("2026-03-01", "2026-03-31"),
                runtime=_make_runtime(),
                mock=False,
            )

        assert "_source_outcome" not in artifact
        assert len(items) == 2
        assert len(seen_cmds) >= 2
        assert all(cmd[0] == "ssh" and "macmini" in cmd for cmd in seen_cmds)
        assert seen_enrich_worker_hosts == ["macmini", "macmini"]
        assert "LAST30DAYS_YOUTUBE_SSH_HOST" not in os.environ

    def test_invalid_ssh_host_does_not_enable_ytdlp(self, monkeypatch, capsys):
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: None)
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)

        bad_config = {"LAST30DAYS_YOUTUBE_SSH_HOST": "-oProxyCommand=evil"}
        assert "youtube" not in pipeline.available_sources(bad_config)
        avail_err = capsys.readouterr().err
        assert avail_err.count("does not look like a plain hostname/alias") == 1

        with mock.patch.object(youtube_yt, "search_and_transcribe") as ytdlp_search:
            items, _artifact = pipeline._retrieve_stream_impl(
                topic="Acme",
                subquery=schema.SubQuery(
                    label="primary",
                    search_query="Acme",
                    ranking_query="Acme",
                    sources=["youtube"],
                ),
                source="youtube",
                config=bad_config,
                depth="default",
                date_range=("2026-03-01", "2026-03-31"),
                runtime=_make_runtime(),
                mock=False,
            )
        stream_err = capsys.readouterr().err
        assert stream_err.count("does not look like a plain hostname/alias") == 1
        ytdlp_search.assert_not_called()
        assert items == []

    def test_youtube_search_cache_isolates_different_ssh_hosts_and_inflight_slots(
        self, monkeypatch,
    ):
        import json
        from lib import subproc

        youtube_yt.reset_search_cache()
        monkeypatch.setattr(youtube_yt.shutil, "which", lambda _cmd: "/usr/bin/yt-dlp")
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)

        calls: list[str | None] = []
        both_inflight = threading.Barrier(2, timeout=5)
        lock = threading.Lock()

        def _fake_run(cmd, **_kwargs):
            host = youtube_yt._ytdlp_ssh_host()
            with lock:
                calls.append(host)
            if host in ("host-a", "host-b"):
                both_inflight.wait()
            label = host or "local"
            payload = json.dumps(
                {
                    "id": f"vid_{label}",
                    "title": f"Acme from {label}",
                    "upload_date": "20260320",
                    "view_count": 100,
                    "like_count": 10,
                    "comment_count": 1,
                }
            )
            return subproc.SubprocResult(returncode=0, stdout=payload + "\n", stderr="")

        def _run_search(host: str | None):
            with youtube_yt.ssh_host_scope(host):
                return youtube_yt.search_youtube(
                    "Acme", "2026-03-01", "2026-03-31", depth="quick",
                )

        try:
            with mock.patch.object(youtube_yt.subproc, "run_with_timeout", side_effect=_fake_run):
                # Concurrent in-flight calls on host-a vs host-b must not coalesce onto each other.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    fa = pool.submit(_run_search, "host-a")
                    fb = pool.submit(_run_search, "host-b")
                    res_a = fa.result(timeout=5)
                    res_b = fb.result(timeout=5)
                # Local call must also miss both SSH cache entries.
                res_local = _run_search(None)
                # Repeating host-a hits host-a's cached result without another subprocess call.
                res_a_cached = _run_search("host-a")

            assert [i["video_id"] for i in res_a["items"]] == ["vid_host-a"]
            assert [i["video_id"] for i in res_b["items"]] == ["vid_host-b"]
            assert [i["video_id"] for i in res_local["items"]] == ["vid_local"]
            assert [i["video_id"] for i in res_a_cached["items"]] == ["vid_host-a"]
            assert sorted(c or "local" for c in calls) == ["host-a", "host-b", "local"]
        finally:
            youtube_yt.reset_search_cache()


# ---------------------------------------------------------------------------
# HIGH-031: RetrievalBundle.record_detail substring preservation & specificity
# ---------------------------------------------------------------------------


class TestHigh031RecordDetailPreservationAndSpecificity:
    def test_shorter_substring_detail_does_not_clobber_existing_detail(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("reddit")
        full = "3 sub-requests rate-limited (HTTP 429); 1 sub-request blocked (HTTP 403)"
        shorter = "1 sub-request blocked (HTTP 403)"

        bundle.record_detail("reddit", full, state=schema.RATE_LIMITED)
        bundle.record_detail("reddit", shorter, state=schema.RATE_LIMITED)

        assert bundle.detail_by_source["reddit"] == full
        assert bundle.source_status["reddit"].detail == full

    def test_longer_superset_detail_replaces_shorter_without_duplication(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("reddit")
        shorter = "1 sub-request blocked (HTTP 403)"
        full = "3 sub-requests rate-limited (HTTP 429); 1 sub-request blocked (HTTP 403)"

        bundle.record_detail("reddit", shorter, state=schema.RATE_LIMITED)
        bundle.record_detail("reddit", full, state=schema.RATE_LIMITED)

        assert bundle.detail_by_source["reddit"] == full
        assert bundle.source_status["reddit"].detail == full

    def test_distinct_failure_counts_with_shared_suffix_are_both_preserved(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("reddit")
        twelve = "12 sub-requests rate-limited (HTTP 429)"
        two = "2 sub-requests rate-limited (HTTP 429)"

        bundle.record_detail("reddit", twelve, state=schema.RATE_LIMITED)
        bundle.record_detail("reddit", two, state=schema.RATE_LIMITED)
        assert bundle.detail_by_source["reddit"] == f"{twelve}; {two}"
        assert bundle.source_status["reddit"].detail == f"{twelve}; {two}"

        # Reverse order preserves both clauses too.
        bundle_rev = schema.RetrievalBundle()
        bundle_rev.mark_attempted("reddit")
        bundle_rev.record_detail("reddit", two, state=schema.RATE_LIMITED)
        bundle_rev.record_detail("reddit", twelve, state=schema.RATE_LIMITED)
        assert bundle_rev.detail_by_source["reddit"] == f"{two}; {twelve}"

        # Overlapping multi-clause details deduplicate exact clauses without losing distinct ones.
        bundle_overlap = schema.RetrievalBundle()
        bundle_overlap.mark_attempted("reddit")
        bundle_overlap.record_detail("reddit", "clause A; clause B")
        bundle_overlap.record_detail("reddit", "clause B; clause C")
        assert bundle_overlap.detail_by_source["reddit"] == "clause A; clause B; clause C"

    def test_distinct_detail_appends_and_exact_repeat_does_not_duplicate(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("reddit")
        bundle.record_detail("reddit", "first note")
        bundle.record_detail("reddit", "second note")
        bundle.record_detail("reddit", "first note")
        bundle.record_detail("reddit", "second note")

        assert bundle.detail_by_source["reddit"] == "first note; second note"
        assert bundle.source_status["reddit"].detail == "first note; second note"

    def test_lane_failure_state_respects_failure_specificity(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("x")
        bundle.record_detail("x", "auth expired", state=schema.AUTH_FAILED)
        bundle.record_detail("x", "generic error", state=health.ERROR)
        bundle.record_detail("x", "rate limited", state=schema.RATE_LIMITED)

        assert bundle.lane_state_by_source["x"] == schema.AUTH_FAILED
        assert bundle.source_status["x"].lane_failure_state == schema.AUTH_FAILED

        bundle_upgrade = schema.RetrievalBundle()
        bundle_upgrade.mark_attempted("x")
        bundle_upgrade.record_detail("x", "generic error", state=health.ERROR)
        bundle_upgrade.record_detail("x", "payment required", state=schema.PAYMENT_REQUIRED)
        assert bundle_upgrade.lane_state_by_source["x"] == schema.PAYMENT_REQUIRED


# ---------------------------------------------------------------------------
# HIGH-033: schema.without_sources invariants, artifacts, and candidate re-homing
# ---------------------------------------------------------------------------


class TestHigh033WithoutSourcesInvariants:
    def test_rebuilds_frozen_subqueries_drops_empty_and_roundtrips(self):
        sq_corpus_only = schema.SubQuery(
            label="local",
            search_query="internal docs",
            ranking_query="internal docs",
            sources=["corpus"],
            weight=0.4,
        )
        sq_mixed = schema.SubQuery(
            label="mixed",
            search_query="acme rollout",
            ranking_query="acme rollout",
            sources=["corpus", "reddit"],
            weight=0.6,
        )
        report = _make_report(
            subqueries=[sq_corpus_only, sq_mixed],
            source_weights={"corpus": 0.5, "reddit": 0.5},
            ranked_candidates=[],
            clusters=[],
            items_by_source={},
        )

        clean = schema.without_sources(report, {"corpus"})

        # Original report subqueries remain untouched.
        assert report.query_plan.subqueries[0].sources == ["corpus"]
        assert report.query_plan.subqueries[1].sources == ["corpus", "reddit"]

        # Clean report drops the empty subquery and rebuilds the mixed one cleanly.
        assert len(clean.query_plan.subqueries) == 1
        assert clean.query_plan.subqueries[0].label == "mixed"
        assert clean.query_plan.subqueries[0].sources == ["reddit"]
        assert clean.query_plan.subqueries[0] is not sq_mixed

        # Must survive serialization + deserialization without raising ValueError.
        restored = schema.report_from_dict(schema.to_dict(clean))
        assert [sq.sources for sq in restored.query_plan.subqueries] == [["reddit"]]

    def test_artifact_scrubbing_uses_token_boundaries_not_raw_substrings(self):
        report = _make_report(
            subqueries=[
                schema.SubQuery(
                    label="p", search_query="q", ranking_query="q", sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0},
            ranked_candidates=[],
            clusters=[],
            items_by_source={},
            artifacts={
                "drill_context": {"topic": "keep me"},
                "corpus_in_export": False,
                "extra_notes": "keep me too",
                "patents": {"count": 2},
                "x": {"raw": 1},
                "x_provenance": {"lane": "connector"},
                "x_partial_coverage": ["note"],
                "extra_x": {"drop": True},
                "prefix_x_suffix": {"drop": True},
                "x-cache": {"drop": True},
                "meta_ads_page": {"name": "Acme"},
                "meta_ads_tally": {"total": 5},
            },
        )

        clean_x = schema.without_sources(report, {"x"})
        assert "drill_context" in clean_x.artifacts
        assert "corpus_in_export" in clean_x.artifacts
        assert "extra_notes" in clean_x.artifacts
        assert "patents" in clean_x.artifacts
        assert "meta_ads_page" in clean_x.artifacts
        for removed in ("x", "x_provenance", "x_partial_coverage", "extra_x", "prefix_x_suffix", "x-cache"):
            assert removed not in clean_x.artifacts

        clean_meta = schema.without_sources(report, {"meta_ads"})
        assert "meta_ads_page" not in clean_meta.artifacts
        assert "meta_ads_tally" not in clean_meta.artifacts
        assert "drill_context" in clean_meta.artifacts

    def test_rehomes_multi_source_candidate_and_rebuilds_cluster_title(self):
        private_item = schema.SourceItem(
            item_id="C-secret",
            source="corpus",
            title="PRIVATE-CORPUS-TITLE",
            body="PRIVATE-CORPUS-BODY",
            url="corpus://notes/secret.md",
            snippet="PRIVATE-CORPUS-SNIPPET",
            published_at="2026-03-20",
        )
        public_item = schema.SourceItem(
            item_id="R-public",
            source="reddit",
            title="Public Reddit rollout thread",
            body="Public Reddit body text",
            url="https://www.reddit.com/r/devops/comments/abc123/rollout/",
            snippet="Public Reddit rollout snippet",
            published_at="2026-03-21",
        )
        fused = schema.Candidate(
            candidate_id="cand-fused",
            item_id="C-secret",
            source="corpus",
            title="PRIVATE-CORPUS-TITLE",
            url="corpus://notes/secret.md",
            snippet="PRIVATE-CORPUS-SNIPPET",
            subquery_labels=["primary"],
            native_ranks={"primary:corpus": 1, "primary:reddit": 2},
            local_relevance=0.9,
            freshness=90,
            engagement=50,
            source_quality=1.0,
            rrf_score=0.05,
            sources=["corpus", "reddit"],
            source_items=[private_item, public_item],
            final_score=88.0,
            explanation="Matched PRIVATE-CORPUS-TITLE note",
            metadata={
                "provenance": [
                    {"source": "corpus", "subquery_label": "primary", "native_rank": 1, "item_id": "C-secret"},
                    {"source": "reddit", "subquery_label": "primary", "native_rank": 2, "item_id": "R-public"},
                ],
            },
        )
        cluster = schema.Cluster(
            cluster_id="cluster-1",
            title="PRIVATE-CORPUS-TITLE",
            candidate_ids=["cand-fused"],
            representative_ids=["cand-fused"],
            sources=["corpus", "reddit"],
            score=88.0,
        )
        report = _make_report(
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="acme", ranking_query="acme",
                    sources=["corpus", "reddit"],
                )
            ],
            source_weights={"corpus": 0.5, "reddit": 0.5},
            ranked_candidates=[fused],
            clusters=[cluster],
            items_by_source={"corpus": [private_item], "reddit": [public_item]},
        )

        clean = schema.without_sources(report, {"corpus"})
        assert len(clean.ranked_candidates) == 1
        rehomed = clean.ranked_candidates[0]
        assert rehomed.source == "reddit"
        assert rehomed.item_id == "R-public"
        assert rehomed.title == "Public Reddit rollout thread"
        assert rehomed.url == public_item.url
        assert rehomed.snippet == "Public Reddit rollout snippet"
        assert rehomed.sources == ["reddit"]
        assert rehomed.explanation is None
        assert list(rehomed.native_ranks.keys()) == ["primary:reddit"]
        assert [p["source"] for p in rehomed.metadata["provenance"]] == ["reddit"]
        assert clean.clusters[0].title == "Public Reddit rollout thread"
        assert clean.clusters[0].sources == ["reddit"]

        serialized = str(schema.to_dict(clean))
        assert "PRIVATE-CORPUS" not in serialized
        assert "corpus://" not in serialized
        restored = schema.report_from_dict(schema.to_dict(clean))
        assert restored.ranked_candidates[0].source == "reddit"

    def test_drops_orphan_candidate_when_all_source_items_are_excluded(self):
        private_only_item = schema.SourceItem(
            item_id="C1",
            source="corpus",
            title="Private note",
            body="secret",
            url="corpus://note.md",
            snippet="secret",
        )
        # Candidate's top-level source says reddit, but every backing source_item is corpus.
        orphan = schema.Candidate(
            candidate_id="cand-orphan",
            item_id="C1",
            source="reddit",
            title="Orphan",
            url="corpus://note.md",
            snippet="secret",
            subquery_labels=["primary"],
            native_ranks={"primary:corpus": 1},
            local_relevance=0.8,
            freshness=80,
            engagement=10,
            source_quality=1.0,
            rrf_score=0.02,
            sources=["corpus"],
            source_items=[private_only_item],
            final_score=75.0,
        )
        public_item = schema.SourceItem(
            item_id="R2",
            source="reddit",
            title="Public survivor",
            body="public",
            url="https://www.reddit.com/r/test/comments/r2/p/",
            snippet="public snippet",
        )
        survivor = schema.Candidate(
            candidate_id="cand-survivor",
            item_id="R2",
            source="reddit",
            title="Public survivor",
            url=public_item.url,
            snippet="public snippet",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.8,
            freshness=80,
            engagement=10,
            source_quality=1.0,
            rrf_score=0.02,
            sources=["reddit"],
            source_items=[public_item],
            final_score=70.0,
        )
        report = _make_report(
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="acme", ranking_query="acme",
                    sources=["reddit", "corpus"],
                )
            ],
            source_weights={"reddit": 0.5, "corpus": 0.5},
            ranked_candidates=[orphan, survivor],
            clusters=[
                schema.Cluster(
                    cluster_id="c-only-orphan",
                    title="Orphan",
                    candidate_ids=["cand-orphan"],
                    representative_ids=["cand-orphan"],
                    sources=["corpus"],
                    score=75.0,
                ),
                schema.Cluster(
                    cluster_id="c-mixed",
                    title="Orphan",
                    candidate_ids=["cand-orphan", "cand-survivor"],
                    representative_ids=["cand-orphan"],
                    sources=["corpus", "reddit"],
                    score=75.0,
                ),
            ],
            items_by_source={"corpus": [private_only_item], "reddit": [public_item]},
        )

        clean = schema.without_sources(report, {"corpus"})
        assert [c.candidate_id for c in clean.ranked_candidates] == ["cand-survivor"]
        assert [cl.cluster_id for cl in clean.clusters] == ["c-mixed"]
        assert clean.clusters[0].candidate_ids == ["cand-survivor"]
        assert clean.clusters[0].representative_ids == ["cand-survivor"]
        assert clean.clusters[0].title == "Public survivor"
        restored = schema.report_from_dict(schema.to_dict(clean))
        assert len(restored.clusters) == 1

    def test_strips_leaked_excluded_snippet_on_surviving_candidate(self):
        public_item = schema.SourceItem(
            item_id="R1",
            source="reddit",
            title="Public thread",
            body="public body",
            url="https://www.reddit.com/r/test/comments/r1/x/",
            snippet="Public thread snippet",
        )
        private_item = schema.SourceItem(
            item_id="C1",
            source="corpus",
            title="Private note",
            body="private body",
            url="corpus://private.md",
            snippet="LONGER PRIVATE CORPUS SNIPPET THAT WON DURING FUSION",
        )
        candidate = schema.Candidate(
            candidate_id="cand-1",
            item_id="R1",
            source="reddit",
            title="Public thread",
            url=public_item.url,
            snippet=private_item.snippet,
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1, "primary:corpus": 2},
            local_relevance=0.9,
            freshness=90,
            engagement=10,
            source_quality=1.0,
            rrf_score=0.03,
            sources=["reddit", "corpus"],
            source_items=[public_item, private_item],
        )
        report = _make_report(
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="acme", ranking_query="acme",
                    sources=["reddit", "corpus"],
                )
            ],
            source_weights={"reddit": 0.5, "corpus": 0.5},
            ranked_candidates=[candidate],
            clusters=[],
            items_by_source={"reddit": [public_item], "corpus": [private_item]},
        )

        clean = schema.without_sources(report, {"corpus"})
        assert clean.ranked_candidates[0].snippet == "Public thread snippet"


# ---------------------------------------------------------------------------
# HIGH-034 & HIGH-035: dates.parse_date & dates.timestamp_to_date
# ---------------------------------------------------------------------------


class TestHigh034And035DatesParsing:
    @pytest.mark.parametrize(
        "bad_value",
        [
            "1e20",
            "-1e20",
            "inf",
            "-inf",
            "nan",
            "99999999999999999999",
            True,
            False,
            float("inf"),
            float("-inf"),
            float("nan"),
            1e20,
            -1e20,
            10**400,
            -(10**400),
            "9999-12-31 23:59:59-01:00",
            "9999-12-31T23:59:59-01:00",
            "0001-01-01 00:00:00+01:00",
            "0001-01-01T00:00:00+01:00",
            None,
            "",
            "   ",
        ],
    )
    def test_parse_date_returns_none_on_overflow_nonfinite_and_bool(self, bad_value):
        assert dates.parse_date(bad_value) is None

    def test_reader_end_date_and_is_today_survive_astimezone_overflow(self):
        from datetime import date, timedelta

        boundary_now = datetime(9999, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-2)))
        assert dates.reader_end_date("2026-04-01", now=boundary_now) == date(2026, 4, 1)
        assert dates.is_today("2026-04-01", now=boundary_now) is False
        assert dates.is_today("9999-12-31", now=boundary_now) is True

    @pytest.mark.parametrize(
        "bad_ts",
        [
            None,
            True,
            False,
            float("inf"),
            float("-inf"),
            float("nan"),
            1e20,
            -1e20,
            10**25,
            10**400,
            -(10**400),
            "bad",
            "1768435200",
        ],
    )
    def test_timestamp_to_date_returns_none_on_overflow_nonfinite_and_bool(self, bad_ts):
        assert dates.timestamp_to_date(bad_ts) is None

    def test_windows_oserror_in_fromtimestamp_returns_none(self, monkeypatch):
        class _RaisingDateTime(datetime):
            @classmethod
            def fromtimestamp(cls, *_args, **_kwargs):
                raise OSError("Windows errno 22")

        monkeypatch.setattr(dates, "datetime", _RaisingDateTime)
        assert dates.parse_date("1768435200") is None
        assert dates.timestamp_to_date(1768435200) is None

    def test_compact_yyyymmdd_bare_year_and_zero_not_misparsed_as_1970(self):
        compact = dates.parse_date("20260401")
        assert compact == datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)

        assert dates.parse_date("2026") is None
        assert dates.parse_date("0") is None
        assert dates.parse_date("-1") is None
        assert dates.parse_date("12345") is None
        assert dates.parse_date("20261345") is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-04-01", datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)),
            ("20260401", datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)),
            ("2026-04-01T12:34:56", datetime(2026, 4, 1, 12, 34, 56, tzinfo=timezone.utc)),
            (
                "2026-04-01T12:34:56.123456",
                datetime(2026, 4, 1, 12, 34, 56, 123456, tzinfo=timezone.utc),
            ),
            (
                "2026-04-01T12:34:56.123456Z",
                datetime(2026, 4, 1, 12, 34, 56, 123456, tzinfo=timezone.utc),
            ),
            (
                "2026-04-01 12:34:56",
                datetime(2026, 4, 1, 12, 34, 56, tzinfo=timezone.utc),
            ),
            (
                "2026-04-01 12:34:56.500000+02:00",
                datetime(2026, 4, 1, 10, 34, 56, 500000, tzinfo=timezone.utc),
            ),
            (
                "2026-04-01T12:34:56+0000",
                datetime(2026, 4, 1, 12, 34, 56, tzinfo=timezone.utc),
            ),
            (
                "  2026-04-01T12:34:56Z  ",
                datetime(2026, 4, 1, 12, 34, 56, tzinfo=timezone.utc),
            ),
        ],
    )
    def test_parse_date_iso_8601_variants_normalize_to_utc(self, raw, expected):
        parsed = dates.parse_date(raw)
        assert parsed == expected
        assert parsed.tzinfo == timezone.utc

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1768435200", datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)),
            ("1710460800.5", datetime(2024, 3, 15, 0, 0, 0, 500000, tzinfo=timezone.utc)),
            ("946684800", datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)),
            (1768435200, datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)),
        ],
    )
    def test_parse_date_preserves_valid_unix_epoch_seconds(self, raw, expected):
        parsed = dates.parse_date(raw)
        assert parsed == expected
        assert parsed.tzinfo == timezone.utc

    def test_grounding_normalize_date_does_not_drop_compact_yyyymmdd_or_bare_year(self):
        assert grounding._normalize_date("20260401") == "2026-04-01"
        assert not grounding._known_date_out_of_range(
            grounding._normalize_date("20260401"), ("2026-03-15", "2026-04-15"),
        )
        assert grounding._normalize_date("2026") is None
        assert not grounding._known_date_out_of_range(
            grounding._normalize_date("2026"), ("2026-03-15", "2026-04-15"),
        )
