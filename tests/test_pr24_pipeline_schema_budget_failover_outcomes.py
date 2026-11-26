"""Regression tests for PR 24: pipeline & schema budget, failover, and outcome integrity.

Covers:
- HIGH-025: finalization_deadline enforcement & live elapsed across Digg/Reddit/dev.to/Amazon
- HIGH-026: X failover preserves x_receipts, runs x_judge retry/pruning, ranks chain_errors by specificity
- HIGH-027: _attempt_x_backend classifies Bird errors with bird_x and catches non-HTTP exceptions
- HIGH-028: RetrievalBundle.record_failure specificity preservation, Phase 2 any_revoked short-circuit,
            and xapi _guard_lane non-failure partial-coverage contract
- HIGH-029: Reddit ScrapeCreators backfill TimeoutError classification, _enrich_cancel wakeup,
            and inner exception preservation
- HIGH-030: Proportional Reddit lane_deadline scaling prevents premature paid SC spend on <= 65s budgets
- HIGH-032: RetrievalBundle.add_items completion-order independence for promoted lane_state vs hard failures
"""

from __future__ import annotations

import threading
import time
from unittest import mock

import pytest

from lib import (
    amazon,
    bird_x,
    devto,
    digg,
    health,
    http,
    pipeline,
    reddit,
    reddit_keyless,
    schema,
    subproc,
    x_api,
    youtube_yt,
)


def _source_item(
    source: str,
    item_id: str,
    url: str,
    *,
    title: str = "Title",
    body: str = "Body",
    author: str = "alice",
    engagement: dict | None = None,
    metadata: dict | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=body,
        url=url,
        author=author,
        engagement=engagement or {},
        metadata=metadata or {},
    )


def _x_post(post_id: str, text: str, author: str = "alice") -> dict:
    return {
        "id": post_id,
        "text": text,
        "url": f"https://x.com/{author}/status/{post_id}",
        "author_handle": author,
        "date": "2026-11-05",
        "engagement": {"likes": 42, "reposts": 5, "replies": 3},
        "relevance": 0.9,
    }


def _reddit_post(post_id: str, title: str = "Elon Musk discussion") -> dict:
    return {
        "id": post_id,
        "title": title,
        "url": f"https://www.reddit.com/r/elonmusk/comments/{post_id}/slug/",
        "subreddit": "elonmusk",
        "date": "2026-11-05",
        "engagement": {"score": 50, "num_comments": 12},
        "relevance": 0.9,
        "metadata": {"post_id": post_id},
    }


def _x_plan(topic: str = "AI safety") -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="story",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=topic,
                sources=["x"],
            )
        ],
        source_weights={"x": 1.0},
    )


def _runtime(x_backend: str | None = None) -> schema.ProviderRuntime:
    return schema.ProviderRuntime(
        reasoning_provider="local",
        planner_model="",
        rerank_model="",
        x_search_backend=x_backend,
    )


# ---------------------------------------------------------------------------
# HIGH-025: Post-ranking enrichments respect finalization_deadline & live elapsed
# ---------------------------------------------------------------------------


class TestHigh025FinalizationDeadline:
    def test_expired_finalization_deadline_skips_digg_reddit_devto_amazon(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        calls: list[str] = []
        monkeypatch.setattr(
            pipeline.digg, "enrich_source_items",
            lambda *a, **k: calls.append("digg") or a[0],
        )
        monkeypatch.setattr(
            pipeline.devto, "enrich_source_items",
            lambda *a, **k: calls.append("devto") or a[0],
        )
        monkeypatch.setattr(
            pipeline.amazon, "enrich_source_items",
            lambda *a, **k: calls.append("amazon") or a[0],
        )

        items_by_source = {
            "digg": [
                _source_item(
                    "digg", "d1", "https://di.gg/ai/d1",
                    engagement={"postCount": 5}, metadata={"clusterUrlId": "d1"},
                )
            ],
            "reddit": [
                _source_item(
                    "reddit", "r1", "https://www.reddit.com/r/test/comments/r1/x/",
                    engagement={"score": 20, "num_comments": 5},
                )
            ],
            "devto": [
                _source_item(
                    "devto", "101", "https://dev.to/alice/post-101",
                    engagement={"reactions": 10, "comments": 4},
                )
            ],
            "amazon": [
                _source_item(
                    "amazon", "B0001", "https://www.amazon.com/dp/B0001",
                    metadata={"asin": "B0001"},
                )
            ],
        }

        # Standalone CLI pattern: _finalization_deadline set in past, NO _research_deadline.
        # Use mock.patch.object for reddit_keyless.enrich_source_items so teardown
        # nests cleanly inside conftest's autouse _no_reddit_kept_thread_comments patch.
        with mock.patch.object(
            pipeline.reddit_keyless, "enrich_source_items",
            side_effect=lambda *a, **k: calls.append("reddit") or a[0],
        ):
            pipeline._finalize_items_by_source(
                items_by_source,
                topic="test topic",
                config={"_finalization_deadline": 999.0},
                depth="default",
                mock=False,
            )
        assert calls == []

        # Positive control: before finalization_deadline, all four enrichments run.
        seen_deadlines: dict[str, float | None] = {}
        monkeypatch.setattr(
            pipeline.digg, "enrich_source_items",
            lambda items, **kw: seen_deadlines.setdefault("digg", kw.get("deadline")) or items,
        )
        monkeypatch.setattr(
            pipeline.devto, "enrich_source_items",
            lambda items, **kw: seen_deadlines.setdefault("devto", kw.get("deadline")) or items,
        )
        monkeypatch.setattr(
            pipeline.amazon, "enrich_source_items",
            lambda items, **kw: seen_deadlines.setdefault("amazon", kw.get("deadline")) or items,
        )

        with mock.patch.object(
            pipeline.reddit_keyless, "enrich_source_items",
            side_effect=lambda items, **kw: seen_deadlines.setdefault("reddit", kw.get("deadline")) or items,
        ):
            pipeline._finalize_items_by_source(
                items_by_source,
                topic="test topic",
                config={"_finalization_deadline": 1050.0, "_research_deadline": 1070.0},
                depth="default",
                mock=False,
            )
        # _finalization_deadline (1050.0) is tighter than _research_deadline - 10s (1060.0).
        assert seen_deadlines == {
            "digg": 1050.0,
            "reddit": 1050.0,
            "devto": 1050.0,
            "amazon": 1050.0,
        }

    def test_reddit_receives_clamped_finalization_deadline_not_raw_research_deadline(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
        captured: list[float | None] = []
        with mock.patch.object(
            pipeline.reddit_keyless, "enrich_source_items",
            side_effect=lambda items, **kw: captured.append(kw.get("deadline")) or items,
        ):
            pipeline._finalize_items_by_source(
                {"reddit": [_source_item("reddit", "r1", "https://www.reddit.com/r/t/comments/r1/x/")]},
                topic="topic",
                config={"_research_deadline": 1080.0},
                depth="default",
                mock=False,
            )
        # _finalization_deadline reserves DISCOVERY_FINALIZATION_RESERVE_SECONDS (10s).
        assert captured == [1080.0 - pipeline.DISCOVERY_FINALIZATION_RESERVE_SECONDS]

    def test_amazon_receives_live_elapsed_after_earlier_sources_finalize(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        def slow_devto(items, **kwargs):
            now[0] += 45.0
            return items

        amazon_kwargs: dict = {}

        def capture_amazon(items, **kwargs):
            amazon_kwargs.update(kwargs)
            return items

        monkeypatch.setattr(pipeline.devto, "enrich_source_items", slow_devto)
        monkeypatch.setattr(pipeline.amazon, "enrich_source_items", capture_amazon)

        pipeline._finalize_items_by_source(
            {
                "devto": [
                    _source_item(
                        "devto", "101", "https://dev.to/a/101",
                        engagement={"comments": 2},
                    )
                ],
                "amazon": [
                    _source_item(
                        "amazon", "B0001", "https://www.amazon.com/dp/B0001",
                        metadata={"asin": "B0001"},
                    )
                ],
            },
            topic="widget",
            config={},
            depth="default",
            mock=False,
            elapsed=100.0,
        )
        # Entry elapsed was 100s; devto took 45s -> Amazon must see ~145s, not stale 100s.
        assert amazon_kwargs["elapsed"] == pytest.approx(145.0)
        assert amazon_kwargs["deadline"] == pytest.approx(1180.0)

    def test_amazon_remaining_lane_budget_and_enrich_respect_deadline(self, monkeypatch):
        monkeypatch.setattr(amazon.time, "monotonic", lambda: 1000.0)
        # Small elapsed (5s) but tight deadline (20s left < MIN_USEFUL_REVIEW_BUDGET 45s) -> skip (0).
        assert amazon._remaining_lane_budget(5.0, deadline=1020.0) == 0
        # Deadline 52s left (between 45s and 60s) -> clamps budget to 52s.
        assert amazon._remaining_lane_budget(5.0, deadline=1052.0) == 52
        # enrich_source_items must skip firing fetcher when deadline is below floor.
        fired: list[str] = []
        item = _source_item(
            "amazon", "B0001", "https://www.amazon.com/dp/B0001",
            metadata={"asin": "B0001", "name": "Widget", "num_ratings": 100},
        )
        amazon.enrich_source_items(
            [item], depth="default", elapsed=5.0, deadline=1020.0,
            fetcher=lambda url: fired.append(url) or {"records": []},
        )
        assert fired == []

    def test_digg_enrich_source_items_stops_at_deadline_and_threads_bounds(self, monkeypatch):
        now = [1000.0]
        seen_calls: list[tuple[float, float | None, threading.Event | None]] = []

        def fake_run(cmd, *, timeout, env=None, deadline_monotonic=None, cancel=None):
            seen_calls.append((timeout, deadline_monotonic, cancel))
            now[0] += 10.0  # exhaust deadline after first cluster
            return subproc.SubprocResult(
                returncode=0,
                stdout=(
                    '{"results": [{"body": "Great point", "xUrl": "https://x.com/u/status/1",'
                    ' "author": {"username": "u"}}]}'
                ),
                stderr="",
            )

        monkeypatch.setattr(digg.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/digg-pp-cli")
        monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)

        cancel = threading.Event()
        items = [
            _source_item(
                "digg", "ID1", "https://di.gg/ai/ID1",
                engagement={"postCount": 5}, metadata={"clusterUrlId": "ID1"},
            ),
            _source_item(
                "digg", "ID2", "https://di.gg/ai/ID2",
                engagement={"postCount": 5}, metadata={"clusterUrlId": "ID2"},
            ),
        ]
        digg.enrich_source_items(items, top_k=3, deadline=1006.0, cancel=cancel)
        # Only first cluster fetched (timeout clamped to 6.0s); second stopped after deadline passed.
        assert seen_calls == [(6.0, 1006.0, cancel)]
        assert len(items[0].metadata.get("posts", [])) == 1
        assert not items[1].metadata.get("posts")

    def test_devto_enrich_source_items_respects_deadline(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(devto.time, "monotonic", lambda: now[0])

        called: list[tuple[str, float | None]] = []

        def fake_fetch(article_id: str, deadline: float | None = None):
            called.append((article_id, deadline))
            return [{"author": "a", "text": "Nice writeup", "replies": 2}]

        monkeypatch.setattr(devto, "_fetch_comments", fake_fetch)

        # Already expired -> skips without invoking _fetch_comments.
        expired_item = _source_item(
            "devto", "10", "https://dev.to/a/10", engagement={"comments": 3},
        )
        devto.enrich_source_items([expired_item], deadline=999.0)
        assert called == []
        assert not expired_item.metadata.get("top_comments")

        # Active deadline -> forwards deadline to _fetch_comments.
        active_item = _source_item(
            "devto", "11", "https://dev.to/a/11", engagement={"comments": 3},
        )
        devto.enrich_source_items([active_item], deadline=1008.0)
        assert called == [("11", 1008.0)]
        assert active_item.metadata.get("top_comments")

    def test_deep_research_finalization_deadline_preserved(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
        deadline = pipeline._finalization_deadline(
            {"_deep_research": True, "_finalization_deadline": 2500.0},
            run_started=1000.0,
        )
        assert deadline == 2500.0


# ---------------------------------------------------------------------------
# HIGH-026: X failover preserves x_receipts, runs x_judge, ranks chain_errors
# ---------------------------------------------------------------------------


class TestHigh026XFailoverControlFlow:
    def test_fallback_preserves_x_receipts_and_runs_x_judge_pruning_and_retry(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok", "xapi"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        xapi_calls: list[str] = []

        def fake_attempt(backend, q, from_date, to_date, depth, config, warnings=None, deadline=None):
            if backend == "grok":
                return [], "connection reset", health.UNREACHABLE
            xapi_calls.append(q)
            if warnings is not None:
                warnings.append("X: xapi window truncated to 7 days")
            if len(xapi_calls) == 1:
                # Initial fallback returns mostly off-topic flood + 1 on-topic post.
                return [
                    _x_post("1", "Completely unrelated celebrity gossip about movies"),
                    _x_post("2", "Another unrelated sports score from last night"),
                    _x_post("3", "Weather forecast for tomorrow morning"),
                    _x_post("4", "SQLite write-ahead logging benchmark results"),
                ], "", None
            # Retry query improves on-topic ratio.
            return [
                _x_post("10", "SQLite write-ahead logging lock contention fix"),
                _x_post("11", "SQLite WAL checkpoint tuning guide"),
                _x_post("12", "Random unrelated cat video"),
            ], "", None

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={"X_BEARER_TOKEN": "dummy"},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        # Fallback warning receipt must survive failover!
        assert artifact.get("x_receipts") == ["X: xapi window truncated to 7 days"]
        # Failover detail metadata must also survive!
        assert "X served via xapi after grok: connection reset" in artifact["_source_outcome_detail"]
        # x_judge must have retried against fallback backend AND pruned off-topic items!
        assert len(xapi_calls) == 2
        texts = [it["text"] for it in items]
        assert all("SQLite" in text for text in texts)
        assert not any("cat video" in text or "celebrity" in text for text in texts)
        assert any("pruned" in w for w in artifact.get("_warnings", []))

    def test_failover_selects_most_specific_prior_failure_from_chain_errors(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok", "bird", "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_attempt(backend, q, from_date, to_date, depth, config, warnings=None, deadline=None):
            if backend == "grok":
                return [], "grok session expired", health.AUTH_FAILED
            if backend == "bird":
                return [], "timed out after 30s", health.TIMEOUT
            return [_x_post("100", "SQLite WAL tuning tips")], "", None

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite WAL",
            subquery=schema.SubQuery(
                label="primary", search_query="SQLite WAL", ranking_query="SQLite WAL", sources=["x"],
            ),
            source="x",
            config={"XAI_API_KEY": "dummy"},
            depth="quick",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite WAL",
        )

        assert len(items) == 1
        # Earlier grok AUTH_FAILED must NOT be masked by later bird TIMEOUT.
        assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED
        assert "grok: grok session expired" in artifact["_source_outcome"]["detail"]
        assert "re-login needed for primary backend" in artifact["_source_outcome"]["detail"]

    def test_all_backends_failed_selects_most_specific_error_in_chain(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok", "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_attempt(backend, *_a, **_k):
            if backend == "grok":
                return [], "grok session expired", health.AUTH_FAILED
            return [], "unexpected failure", health.ERROR

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        with pytest.raises(pipeline.SourceRunError) as exc_info:
            pipeline._retrieve_stream_impl(
                topic="SQLite",
                subquery=schema.SubQuery(
                    label="primary", search_query="SQLite", ranking_query="SQLite", sources=["x"],
                ),
                source="x",
                config={},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime("grok"),
                mock=False,
                raw_topic="SQLite",
            )

        assert exc_info.value.outcome_state == health.AUTH_FAILED
        assert "grok: grok session expired" in str(exc_info.value)

    def test_mixed_result_items_plus_err_still_preserves_x_receipts_and_prunes(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_attempt(backend, q, from_date, to_date, depth, config, warnings=None, deadline=None):
            if warnings is not None:
                warnings.append("X: grok partial window")
            return [
                _x_post("1", "SQLite write-ahead logging details"),
                _x_post("2", "Unrelated baking recipe for sourdough"),
            ], "grok session expired", health.AUTH_FAILED

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)
        monkeypatch.setattr(pipeline.x_judge, "should_retry_x_search", lambda *a, **k: False)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        assert artifact.get("x_receipts") == ["X: grok partial window"]
        assert artifact["_source_outcome"]["state"] == health.AUTH_FAILED
        assert [it["id"] for it in items] == ["1"]


# ---------------------------------------------------------------------------
# HIGH-027: _attempt_x_backend Bird classification + non-HTTP exception failover
# ---------------------------------------------------------------------------


class TestHigh027AttemptXBackend:
    @pytest.mark.parametrize(
        "message,expected_state",
        [
            ("cookie expired", health.AUTH_FAILED),
            ("expired cookie on profile", health.AUTH_FAILED),
            ("login required to continue", health.AUTH_FAILED),
            ("Could not authenticate you", health.AUTH_FAILED),
            ("session rejected by X", health.AUTH_FAILED),
            ("HTML interstitial returned", health.SCHEMA_DRIFT),
        ],
    )
    def test_bird_error_uses_bird_x_classify_run_failure(self, monkeypatch, message, expected_state):
        monkeypatch.setattr(pipeline, "_fetch_x_backend", lambda *a, **k: ([], message))
        items, detail, state = pipeline._attempt_x_backend(
            "bird", "topic", "2026-10-10", "2026-11-10", "default", {},
        )
        assert items == []
        assert detail == message
        assert state == expected_state

    def test_bird_cookie_expired_failover_emits_auth_failed_and_relogin(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["bird", "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_fetch(backend, *_a, **_k):
            if backend == "bird":
                return [], "cookie expired"
            return [_x_post("1", "SQLite WAL benchmark")], ""

        monkeypatch.setattr(pipeline, "_fetch_x_backend", fake_fetch)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite",
            subquery=schema.SubQuery(
                label="primary", search_query="SQLite", ranking_query="SQLite", sources=["x"],
            ),
            source="x",
            config={},
            depth="quick",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("bird"),
            mock=False,
            raw_topic="SQLite",
        )
        assert len(items) == 1
        assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED
        assert "re-login needed for primary backend" in artifact["_source_outcome"]["detail"]

    @pytest.mark.parametrize(
        "exc,expected_state",
        [
            (OSError("network is unreachable"), health.UNREACHABLE),
            (TimeoutError("socket stalled"), health.TIMEOUT),
            (KeyError("data"), health.ERROR),
            (ValueError("bad payload"), health.ERROR),
            (RuntimeError("grok session expired"), health.AUTH_FAILED),
        ],
    )
    def test_non_http_exceptions_return_failure_tuple_and_fail_over(self, monkeypatch, exc, expected_state):
        monkeypatch.setattr(pipeline, "_fetch_x_backend", mock.Mock(side_effect=exc))
        items, detail, state = pipeline._attempt_x_backend(
            "grok", "topic", "2026-10-10", "2026-11-10", "default", {},
        )
        assert items == []
        assert detail.startswith(f"{type(exc).__name__}: ")
        assert state == expected_state

    def test_non_http_exception_fails_over_to_backup_backend_in_chain(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok", "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_fetch(backend, *_a, **_k):
            if backend == "grok":
                raise RuntimeError("unexpected subprocess crash")
            return [_x_post("77", "SQLite WAL improvements")], ""

        monkeypatch.setattr(pipeline, "_fetch_x_backend", fake_fetch)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite",
            subquery=schema.SubQuery(
                label="primary", search_query="SQLite", ranking_query="SQLite", sources=["x"],
            ),
            source="x",
            config={},
            depth="quick",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite",
        )
        assert [it["id"] for it in items] == ["77"]
        assert "RuntimeError: unexpected subprocess crash" in artifact["_source_outcome_detail"]

    @pytest.mark.parametrize("exc_cls", [KeyboardInterrupt, SystemExit])
    def test_base_exceptions_are_not_swallowed(self, monkeypatch, exc_cls):
        monkeypatch.setattr(pipeline, "_fetch_x_backend", mock.Mock(side_effect=exc_cls()))
        with pytest.raises(exc_cls):
            pipeline._attempt_x_backend("grok", "topic", "2026-10-10", "2026-11-10", "default", {})


# ---------------------------------------------------------------------------
# HIGH-028: record_failure specificity, Phase 2 any_revoked, xapi _guard_lane
# ---------------------------------------------------------------------------


class TestHigh028FailureSpecificityAndPhase2Guards:
    @pytest.mark.parametrize("with_items", [False, True])
    def test_record_failure_never_clobbers_auth_failed_with_less_specific_failure(self, with_items):
        bundle = schema.RetrievalBundle()
        if with_items:
            bundle.add_items("primary", "x", [_source_item("x", "X1", "https://x.com/a/status/1")])
        bundle.record_failure("x", schema.AUTH_FAILED, "grok session revoked")
        bundle.record_failure("x", health.TIMEOUT, "ABOUT-lane timed out")
        bundle.record_failure("x", health.UNREACHABLE, "connection reset")
        bundle.record_failure("x", health.ERROR, "generic error")

        assert bundle.source_status["x"].state == schema.AUTH_FAILED
        assert bundle.source_status["x"].detail == "grok session revoked"
        assert bundle.errors_by_source["x"] == "grok session revoked"

    def test_record_failure_upgrades_to_more_specific_failure_and_keeps_errors_aligned(self):
        bundle = schema.RetrievalBundle()
        bundle.add_items("primary", "x", [_source_item("x", "X1", "https://x.com/a/status/1")])
        bundle.record_failure("x", health.TIMEOUT, "lane timed out")
        assert bundle.source_status["x"].state == schema.PARTIAL
        assert bundle.errors_by_source["x"] == "lane timed out"

        bundle.record_failure("x", schema.AUTH_FAILED, "grok session revoked")
        assert bundle.source_status["x"].state == schema.AUTH_FAILED
        assert bundle.source_status["x"].detail == "grok session revoked"
        assert bundle.errors_by_source["x"] == "grok session revoked"

    def test_record_failure_preserves_payment_required_over_generic_error(self):
        bundle = schema.RetrievalBundle()
        bundle.record_failure("x", health.PAYMENT_REQUIRED, "402 credits exhausted")
        bundle.record_failure("x", health.ERROR, "later generic error")

        assert bundle.source_status["x"].state == health.PAYMENT_REQUIRED
        assert bundle.source_status["x"].detail == "402 credits exhausted"
        assert bundle.errors_by_source["x"] == "402 credits exhausted"

    def test_phase2_skips_remaining_lanes_after_auth_revocation(self, monkeypatch):
        bundle = schema.RetrievalBundle()
        bundle.add_items(
            "primary",
            "x",
            [
                _source_item("x", "X1", "https://x.com/extracted1/status/1", author="extracted1", body="AI safety note 1"),
                _source_item("x", "X2", "https://x.com/extracted1/status/2", author="extracted1", body="AI safety note 2"),
            ],
        )
        calls: list[str] = []

        def fake_handles(handles, *args, **kwargs):
            calls.append(f"from:{','.join(handles)}")
            # First call (explicit handle) reports auth revocation.
            return [], True

        def fake_mentions(*args, **kwargs):
            calls.append("about")
            return [], False

        def fake_name(*args, **kwargs):
            calls.append("name")
            return [], False

        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok"])
        monkeypatch.setattr(
            pipeline.entity_extract, "extract_entities",
            lambda *_a, **_k: {"x_handles": ["extracted1"], "x_hashtags": [], "reddit_subreddits": []},
        )
        monkeypatch.setattr(pipeline.grok_x, "search_handles", fake_handles)
        monkeypatch.setattr(pipeline.grok_x, "search_mentions", fake_mentions)
        monkeypatch.setattr(pipeline.grok_x, "search_name", fake_name)

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=_x_plan("AI safety"),
            config={},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            x_handle="primaryuser",
            x_related=["peeruser"],
        )

        # Only the first explicit FROM-lane call ran; extracted FROM, ABOUT, NAME, and related all skipped!
        assert calls == ["from:primaryuser"]
        assert bundle.source_status["x"].state == schema.AUTH_FAILED
        assert "FROM-lane (explicit)" in bundle.source_status["x"].detail
        assert bundle.errors_by_source["x"] == bundle.source_status["x"].detail

    def test_xapi_guard_lane_deadline_records_partial_coverage_not_failure(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        bundle = schema.RetrievalBundle()
        bundle.add_items(
            "primary", "x",
            [_source_item("x", "X1", "https://x.com/steipete/status/1", author="steipete", body="AI safety")],
        )

        handle_calls = 0

        def fake_search_handles(*args, **kwargs):
            nonlocal handle_calls
            handle_calls += 1
            # Advance clock past supplemental_deadline before ABOUT lane runs.
            now[0] += 200.0
            return []

        mention_mock = mock.Mock(return_value=[])
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["xapi"])
        monkeypatch.setattr(
            pipeline.entity_extract, "extract_entities",
            lambda *_a, **_k: {"x_handles": [], "x_hashtags": [], "reddit_subreddits": []},
        )
        monkeypatch.setattr(pipeline.x_api, "search_handles", fake_search_handles)
        monkeypatch.setattr(pipeline.x_api, "search_mentions", mention_mock)

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=_x_plan("AI safety"),
            config={"X_BEARER_TOKEN": "dummy", "_research_deadline": 1050.0},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("xapi"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            x_handle="steipete",
        )

        assert handle_calls == 1
        mention_mock.assert_not_called()
        assert bundle.artifacts.get("x_partial_coverage") == [
            f"X handle lanes: {x_api.DEADLINE_DETAIL}"
        ]
        # Must NOT call record_failure -> source stays OK and errors_by_source stays empty.
        assert bundle.source_status["x"].state == health.OK
        assert bundle.errors_by_source == {}

    def test_non_xapi_guard_lane_deadline_still_records_failure(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        bundle = schema.RetrievalBundle()
        bundle.add_items(
            "primary", "x",
            [_source_item("x", "X1", "https://x.com/steipete/status/1", author="steipete", body="AI safety")],
        )

        def expire_during_from(*args, **kwargs):
            now[0] += 200.0
            return []

        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["bird"])
        monkeypatch.setattr(
            pipeline.entity_extract, "extract_entities",
            lambda *_a, **_k: {"x_handles": [], "x_hashtags": [], "reddit_subreddits": []},
        )
        monkeypatch.setattr(pipeline.bird_x, "search_handles", expire_during_from)
        monkeypatch.setattr(pipeline.bird_x, "search_mentions", mock.Mock(return_value=[]))

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=_x_plan("AI safety"),
            config={"_research_deadline": 1050.0},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            x_handle="steipete",
        )

        assert bundle.source_status["x"].state == schema.PARTIAL
        assert "timed out" in bundle.source_status["x"].detail.lower()


# ---------------------------------------------------------------------------
# HIGH-029: Reddit ScrapeCreators backfill timeout classification & _enrich_cancel
# ---------------------------------------------------------------------------


class TestHigh029RedditBackfillTimeoutAndCancel:
    def test_backfill_timeout_classified_as_timeout_not_error(self, monkeypatch):
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_MIN_SECONDS", 10.0)
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_RETURN_MARGIN_SECONDS", 5.0)
        monkeypatch.setattr(pipeline, "REDDIT_LANE_RETURN_MARGIN_SECONDS", 0.0)
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_RESERVE_SECONDS", 0.0)

        # No free items + insufficient budget left for SC backfill -> TimeoutError -> health.TIMEOUT.
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Elon Musk",
                subquery=schema.SubQuery(
                    label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
                ),
                source="reddit",
                config={
                    "SCRAPECREATORS_API_KEY": "dummy",
                    "_research_deadline": time.monotonic() + 8.0,  # 8 - 5 = 3s < 10s min
                },
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        assert items == []
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert "timed out" in artifact["_source_outcome"]["detail"].lower()

    def test_backfill_custom_timeout_error_without_keyword_still_classified_timeout(self):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]), \
             mock.patch.object(
                 pipeline, "_run_reddit_backfill",
                 side_effect=TimeoutError("budget gone without keyword"),
             ):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Elon Musk",
                subquery=schema.SubQuery(
                    label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
                ),
                source="reddit",
                config={"SCRAPECREATORS_API_KEY": "dummy"},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        assert items == []
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT

    def test_backfill_wakes_promptly_on_enrich_cancel(self):
        cancel = threading.Event()
        release = threading.Event()

        def slow_call():
            release.wait(10.0)
            return {"posts": []}

        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with pytest.raises(TimeoutError, match="timed out"):
                pipeline._run_reddit_backfill(
                    {
                        "_research_deadline": time.monotonic() + 120.0,
                        "_enrich_cancel": cancel,
                    },
                    slow_call,
                )
        finally:
            timer.cancel()
            release.set()

        elapsed = time.monotonic() - started
        assert elapsed < 2.0, f"expected cancel wakeup within ~0.25s, took {elapsed:.2f}s"

    def test_backfill_preserves_inner_timeout_error_verbatim(self):
        def inner_timeout():
            raise TimeoutError("upstream socket read stalled after 5s")

        with pytest.raises(TimeoutError, match="upstream socket read stalled after 5s"):
            pipeline._run_reddit_backfill(
                {"_research_deadline": time.monotonic() + 120.0},
                inner_timeout,
            )

    def test_backfill_timeout_with_free_items_sets_detail_state_to_timeout(self):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[_reddit_post("a")]), \
             mock.patch.object(
                 pipeline, "_run_reddit_backfill",
                 side_effect=TimeoutError("no room left"),
             ):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Elon Musk",
                subquery=schema.SubQuery(
                    label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
                ),
                source="reddit",
                config={"SCRAPECREATORS_API_KEY": "dummy"},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        assert len(items) == 1
        assert artifact["_source_outcome_detail_state"] == health.TIMEOUT


# ---------------------------------------------------------------------------
# HIGH-030: Proportional Reddit lane_deadline scaling on tight budgets
# ---------------------------------------------------------------------------


class TestHigh030ProportionalRedditLaneDeadline:
    def test_tight_60s_budget_with_sc_key_gives_free_lane_real_share_and_avoids_sc_spend(self, monkeypatch):
        now = 1000.0
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now)
        monkeypatch.setattr(reddit_keyless.time, "monotonic", lambda: now)

        seen_deadline: list[float | None] = []

        def fake_public(*_args, **_kwargs):
            dl = reddit_keyless._lane_deadline.get()
            seen_deadline.append(dl)
            assert not reddit_keyless._out_of_time(dl)
            return [_reddit_post(c) for c in "abcde"]

        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=fake_public), \
             mock.patch("lib.reddit.search_and_enrich") as sc_mock:
            items, _artifact = pipeline._retrieve_stream_impl(
                topic="Elon Musk",
                subquery=schema.SubQuery(
                    label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
                ),
                source="reddit",
                config={
                    "SCRAPECREATORS_API_KEY": "dummy",
                    "_research_deadline": now + 60.0,
                },
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        # margin = min(20, 60*0.15=9) = 9; reserve = min(45, 60*0.35=21) = 21 -> free lane gets 30s!
        assert seen_deadline == [pytest.approx(now + 30.0)]
        assert len(items) == 5
        sc_mock.assert_not_called()

    def test_tight_15s_budget_without_sc_key_runs_free_lane_instead_of_immediate_timeout(self, monkeypatch):
        now = 1000.0
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now)
        monkeypatch.setattr(reddit_keyless.time, "monotonic", lambda: now)

        seen_deadline: list[float | None] = []

        def fake_public(*_args, **_kwargs):
            dl = reddit_keyless._lane_deadline.get()
            seen_deadline.append(dl)
            assert not reddit_keyless._out_of_time(dl)
            return [_reddit_post("a")]

        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=fake_public):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="Elon Musk",
                subquery=schema.SubQuery(
                    label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
                ),
                source="reddit",
                config={"_research_deadline": now + 15.0},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        # margin = min(20, 15*0.15=2.25) = 2.25 -> free lane gets 12.75s!
        assert seen_deadline == [pytest.approx(now + 12.75)]
        assert len(items) == 1
        assert "_source_outcome" not in artifact


# ---------------------------------------------------------------------------
# HIGH-032: Parallel completion-order independence in RetrievalBundle.add_items
# ---------------------------------------------------------------------------


class TestHigh032AddItemsOrderIndependence:
    @pytest.mark.parametrize("lane_state", [schema.AUTH_FAILED, schema.RATE_LIMITED])
    def test_empty_lane_state_stream_then_populated_sibling_matches_reverse_order(self, lane_state):
        item = _source_item("reddit", "R1", "https://www.reddit.com/r/t/comments/r1/x/")

        # Order A: empty stream with swallowed lane failure finishes FIRST, sibling with item finishes SECOND.
        bundle_a = schema.RetrievalBundle()
        bundle_a.mark_attempted("reddit")
        bundle_a.record_detail("reddit", "sub-request failure", state=lane_state)
        bundle_a.add_items("sqA", "reddit", [])
        # While empty, lane_state is promoted.
        assert bundle_a.source_status["reddit"].state == lane_state
        # Sibling delivers item -> must revert cleanly to OK, retaining detail & lane_failure_state!
        bundle_a.add_items("sqB", "reddit", [item])

        # Order B: sibling with item finishes FIRST, empty stream with swallowed lane failure finishes SECOND.
        bundle_b = schema.RetrievalBundle()
        bundle_b.mark_attempted("reddit")
        bundle_b.add_items("sqB", "reddit", [item])
        bundle_b.record_detail("reddit", "sub-request failure", state=lane_state)
        bundle_b.add_items("sqA", "reddit", [])

        for outcome in (bundle_a.source_status["reddit"], bundle_b.source_status["reddit"]):
            assert outcome.state == health.OK
            assert outcome.items_returned == 1
            assert outcome.detail == "sub-request failure"
            assert outcome.lane_failure_state == lane_state
            assert outcome.fix_hint is None

    def test_all_empty_streams_keep_promoted_lane_state(self):
        bundle = schema.RetrievalBundle()
        bundle.mark_attempted("polymarket")
        bundle.record_detail("polymarket", "5 sub-requests rate-limited", state=schema.RATE_LIMITED)
        bundle.add_items("sqA", "polymarket", [])
        bundle.add_items("sqB", "polymarket", [])

        outcome = bundle.source_status["polymarket"]
        assert outcome.state == schema.RATE_LIMITED
        assert outcome.items_returned == 0
        assert outcome.detail == "5 sub-requests rate-limited"
        assert outcome.lane_failure_state == schema.RATE_LIMITED

    def test_hard_record_failure_still_preserved_when_sibling_delivers_items(self):
        item = _source_item("x", "X1", "https://x.com/a/status/1")

        # Hard AUTH_FAILED stays AUTH_FAILED.
        auth_bundle = schema.RetrievalBundle()
        auth_bundle.record_failure("x", schema.AUTH_FAILED, "grok revoked")
        auth_bundle.add_items("sqB", "x", [item])
        assert auth_bundle.source_status["x"].state == schema.AUTH_FAILED
        assert auth_bundle.source_status["x"].detail == "grok revoked"
        assert auth_bundle.source_status["x"].fix_hint == "doctor"

        # Hard TIMEOUT becomes PARTIAL when items exist.
        timeout_bundle = schema.RetrievalBundle()
        timeout_bundle.record_failure("x", health.TIMEOUT, "timed out")
        timeout_bundle.add_items("sqB", "x", [item])
        assert timeout_bundle.source_status["x"].state == schema.PARTIAL
        assert timeout_bundle.source_status["x"].detail == "timed out"
        assert timeout_bundle.source_status["x"].fix_hint == "doctor"


# ---------------------------------------------------------------------------
# Greptile P1 #1: Deadline-skipped enrichments record [] for fixture replay
# ---------------------------------------------------------------------------


class TestGreptileP1DeadlineSkippedFixtureReplay:
    @pytest.mark.parametrize("source", ["youtube", "digg", "reddit", "devto", "amazon"])
    def test_deadline_skipped_enrichment_records_empty_and_replays_cleanly(
        self, tmp_path, monkeypatch, source,
    ):
        fixture_dir = tmp_path / source
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        def _make_item() -> schema.SourceItem:
            if source == "digg":
                return _source_item(
                    "digg", "item-1", "https://di.gg/ai/item-1",
                    engagement={"postCount": 5}, metadata={"clusterUrlId": "item-1"},
                )
            if source == "reddit":
                return _source_item(
                    "reddit", "item-1", "https://www.reddit.com/r/test/comments/item1/x/",
                    engagement={"score": 20, "num_comments": 5}, metadata={"existing": "keep"},
                )
            if source == "devto":
                return _source_item(
                    "devto", "item-1", "https://dev.to/alice/post-1",
                    engagement={"reactions": 10, "comments": 4}, metadata={"existing": "keep"},
                )
            if source == "amazon":
                return _source_item(
                    "amazon", "item-1", "https://www.amazon.com/dp/B0001",
                    metadata={"asin": "B0001", "existing": "keep"},
                )
            return _source_item(
                "youtube", "item-1", "https://www.youtube.com/watch?v=item1",
                metadata={"existing": "keep"},
            )

        fail_live = lambda *_a, **_k: pytest.fail(f"live enrichment ran for {source}")

        item = _make_item()
        original_metadata = dict(item.metadata)

        # Record mode after finalization_deadline has expired: live call skipped,
        # but empty exchange [] must still be recorded for offline replay.
        with mock.patch.object(pipeline.youtube_yt, "backfill_transcripts", side_effect=fail_live), \
             mock.patch.object(pipeline.digg, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.reddit_keyless, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.devto, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.amazon, "enrich_source_items", side_effect=fail_live):
            with http.recording_requests(fixture_dir):
                recorded = pipeline._finalize_items_by_source(
                    {source: [item]},
                    topic="agents",
                    config={"_finalization_deadline": 999.0},
                    depth="quick",
                    mock=False,
                )

        assert recorded[source][0].metadata == original_metadata

        # Replay mode (even with a generous deadline) must match the recorded
        # empty exchange without raising Unrecorded CLI-backed source request
        # and leave item metadata untouched.
        replay_item = _make_item()
        with mock.patch.object(pipeline.youtube_yt, "backfill_transcripts", side_effect=fail_live), \
             mock.patch.object(pipeline.digg, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.reddit_keyless, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.devto, "enrich_source_items", side_effect=fail_live), \
             mock.patch.object(pipeline.amazon, "enrich_source_items", side_effect=fail_live):
            with http.replaying_requests(fixture_dir):
                replayed = pipeline._finalize_items_by_source(
                    {source: [replay_item]},
                    topic="agents",
                    config={"_finalization_deadline": 1500.0},
                    depth="quick",
                    mock=False,
                )

        assert len(replayed[source]) == 1
        assert replayed[source][0].metadata == original_metadata


# ---------------------------------------------------------------------------
# Greptile P1 #2: Cancelled / expired Reddit SC backfill stops paid HTTP spend
# ---------------------------------------------------------------------------


class TestGreptileP1CancelledBackfillStopsSpend:
    def test_enrich_cancel_during_subreddit_search_stops_global_and_comment_calls(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        cancel = threading.Event()
        worker_done = threading.Event()
        endpoints: list[str] = []

        def fake_http_get(url, **kwargs):
            endpoints.append(url)
            if "/subreddit/search" in url:
                # Cancel arrives while first paid subreddit call is in flight.
                cancel.set()
                return {
                    "posts": [{
                        "id": "p1",
                        "title": "SQLite WAL write-ahead logging",
                        "permalink": "/r/sqlite/comments/p1/wal/",
                        "subreddit": "sqlite",
                        "created_utc": 1793836800,
                        "ups": 40,
                        "num_comments": 10,
                    }]
                }
            raise AssertionError(f"paid ScrapeCreators call fired after cancel: {url}")

        monkeypatch.setattr(reddit.http, "get", fake_http_get)

        def call():
            try:
                return reddit.search_and_enrich_memo(
                    "SQLite WAL", "2026-10-10", "2026-11-10",
                    depth="default", token="dummy-sc-key", subreddits=["sqlite"],
                )
            finally:
                worker_done.set()

        with pytest.raises(TimeoutError, match="timed out"):
            pipeline._run_reddit_backfill(
                {
                    "_research_deadline": time.monotonic() + 120.0,
                    "_enrich_cancel": cancel,
                },
                call,
            )

        assert worker_done.wait(timeout=2.0), "worker thread should exit promptly after cancel"
        assert len(endpoints) == 1 and endpoints[0].endswith("/subreddit/search")
        assert not any(
            url.endswith("/reddit/search") or "/post/comments" in url for url in endpoints
        )
        # Paid request already started before cancel -> cached in _SC_MEMO so a
        # retry with a fresh deadline never buys the same search twice.
        assert len(reddit._SC_MEMO) == 1
        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="default", token="dummy-sc-key", subreddits=["sqlite"],
                deadline_monotonic=time.monotonic() + 120.0,
            )
        assert len(endpoints) == 1

    def test_wait_deadline_expiry_during_subreddit_search_stops_subsequent_paid_calls(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_MIN_SECONDS", 0.05)
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_RETURN_MARGIN_SECONDS", 0.0)

        import json as _json
        from lib import bounded_get

        worker_done = threading.Event()
        endpoints: list[str] = []

        def fake_bounded_get(req, *, timeout, deadline_monotonic, cancel=None, on_start=None, **_kwargs):
            url = req.full_url
            endpoints.append(url)
            if "/subreddit/search" in url:
                if on_start is not None:
                    on_start()
                # Sleep past wait_deadline (~0.15s) before returning.
                time.sleep(0.25)
                payload = _json.dumps({
                    "posts": [{
                        "id": "p2",
                        "title": "SQLite WAL benchmark",
                        "permalink": "/r/sqlite/comments/p2/wal/",
                        "subreddit": "sqlite",
                        "created_utc": 1793836800,
                        "ups": 50,
                        "num_comments": 12,
                    }]
                }).encode("utf-8")
                return 200, payload, None
            raise AssertionError(f"paid ScrapeCreators call fired past deadline: {url}")

        monkeypatch.setattr(bounded_get, "get", fake_bounded_get)
        monkeypatch.setattr(http.usage, "begin_http", lambda *_a, **_kw: None)
        monkeypatch.setattr(http.usage, "finish", lambda *_a, **_kw: None)

        def call():
            try:
                return reddit.search_and_enrich_memo(
                    "SQLite WAL", "2026-10-10", "2026-11-10",
                    depth="default", token="dummy-sc-key", subreddits=["sqlite"],
                )
            finally:
                worker_done.set()

        with pytest.raises(TimeoutError, match="timed out"):
            pipeline._run_reddit_backfill(
                {"_research_deadline": time.monotonic() + 0.15},
                call,
            )

        assert worker_done.wait(timeout=2.0), "worker thread should stop after deadline expiry"
        assert len(endpoints) == 1 and "/subreddit/search" in endpoints[0]
        assert not any(
            "/reddit/search?" in url or "/post/comments" in url for url in endpoints
        )
        # Paid request already dispatched via on_start before deadline -> cached in _SC_MEMO.
        assert len(reddit._SC_MEMO) == 1


# ---------------------------------------------------------------------------
# Greptile P1 #3: Timeout must not hide PAYMENT_REQUIRED / AUTH_FAILED in X chain
# ---------------------------------------------------------------------------


class TestGreptileP1XChainTimeoutSpecificity:
    @pytest.mark.parametrize(
        "primary_backend,primary_err,primary_state",
        [
            ("xapi", "payment required (X API credits exhausted)", health.PAYMENT_REQUIRED),
            ("grok", "grok session expired", health.AUTH_FAILED),
        ],
    )
    def test_specific_primary_failure_beats_later_chain_timeout(
        self, monkeypatch, primary_backend, primary_err, primary_state,
    ):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: [primary_backend, "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_attempt(backend, *_a, **_k):
            if backend == primary_backend:
                return [], primary_err, primary_state
            # Second backend exhausts the chain deadline.
            now[0] += 200.0
            return [], "request timed out", health.TIMEOUT

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        with pytest.raises(pipeline.SourceRunError) as exc_info:
            pipeline._retrieve_stream_impl(
                topic="SQLite",
                subquery=schema.SubQuery(
                    label="primary", search_query="SQLite", ranking_query="SQLite", sources=["x"],
                ),
                source="x",
                config={"_research_deadline": 1050.0},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(primary_backend),
                mock=False,
                raw_topic="SQLite",
            )

        assert exc_info.value.outcome_state == primary_state
        assert f"{primary_backend}: {primary_err}" in str(exc_info.value)

    def test_chain_timeout_beats_generic_primary_error_with_matching_message(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["xai", "xquik"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        def fake_attempt(backend, *_a, **_k):
            if backend == "xai":
                return [], "unexpected server glitch", health.ERROR
            # Second backend returns generic error after exhausting chain_deadline.
            now[0] += 200.0
            return [], "incomplete response", health.ERROR

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        with pytest.raises(pipeline.SourceRunError) as exc_info:
            pipeline._retrieve_stream_impl(
                topic="SQLite",
                subquery=schema.SubQuery(
                    label="primary", search_query="SQLite", ranking_query="SQLite", sources=["x"],
                ),
                source="x",
                config={"_research_deadline": 1050.0},
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime("xai"),
                mock=False,
                raw_topic="SQLite",
            )

        assert exc_info.value.outcome_state == health.TIMEOUT
        assert "timed out" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Greptile P2 #4: Skip x_judge retry when serving backend reported AUTH_FAILED
# ---------------------------------------------------------------------------


class TestGreptileP2SkipJudgeRetryOnRevokedBackend:
    def test_mixed_result_with_auth_failed_skips_judge_retry_but_still_prunes(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        attempt_calls: list[tuple[str, str]] = []

        def fake_attempt(backend, q, *_a, **_k):
            attempt_calls.append((backend, q))
            return [
                _x_post("1", "Unrelated movie review about action heroes"),
                _x_post("2", "Unrelated baseball game score from yesterday"),
                _x_post("3", "Unrelated weather alert for coastal cities"),
                _x_post("4", "SQLite write-ahead logging concurrency benchmark"),
            ], "grok session expired", health.AUTH_FAILED

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        # Revoked backend must NOT be called a second time by x_judge retry!
        assert len(attempt_calls) == 1
        # Off-topic pruning must still run on the returned items.
        assert [it["id"] for it in items] == ["4"]
        assert artifact["_source_outcome"]["state"] == health.AUTH_FAILED

    def test_healthy_fallback_after_primary_auth_failed_still_runs_judge_retry(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok", "xai"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        xai_calls: list[str] = []

        def fake_attempt(backend, q, *_a, **_k):
            if backend == "grok":
                return [], "grok session expired", health.AUTH_FAILED
            xai_calls.append(q)
            if len(xai_calls) == 1:
                return [
                    _x_post("1", "Unrelated movie review about action heroes"),
                    _x_post("2", "Unrelated baseball game score from yesterday"),
                    _x_post("3", "Unrelated weather alert for coastal cities"),
                    _x_post("4", "SQLite write-ahead logging concurrency benchmark"),
                ], "", None
            return [
                _x_post("10", "SQLite write-ahead logging checkpoint tuning"),
                _x_post("11", "SQLite write-ahead logging read scalability"),
            ], "", None

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={"XAI_API_KEY": "dummy"},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        # Healthy fallback xai must still run x_judge retry (initial + retry = 2 calls).
        assert len(xai_calls) == 2
        assert [it["id"] for it in items] == ["10", "11"]
        # Primary grok AUTH_FAILED outcome must still be preserved.
        assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED
        assert "re-login needed for primary backend" in artifact["_source_outcome"]["detail"]


# ---------------------------------------------------------------------------
# Greptile Round 2 P1 #1: Keep paid SC Reddit posts when comments / Phase 3 hit deadline
# ---------------------------------------------------------------------------


class TestGreptileRound2P1PreservePaidPostsOnCommentDeadline:
    def test_deadline_before_comments_keeps_paid_posts_and_surfaces_note(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        now = [1000.0]
        monkeypatch.setattr(reddit.time, "monotonic", lambda: now[0])
        monkeypatch.setattr(pipeline.time, "monotonic", lambda: now[0])

        paid_post = {
            "id": "p100",
            "title": "SQLite WAL write-ahead logging guide",
            "permalink": "/r/sqlite/comments/p100/wal_guide/",
            "subreddit": "sqlite",
            "created_utc": 1793836800,
            "ups": 80,
            "num_comments": 15,
        }

        def fake_search_reddit(*_args, **_kwargs):
            # Search finishes right at the deadline -> no room left for comments.
            now[0] = 1050.0
            return {
                "items": [{
                    "id": "R1",
                    "reddit_id": "p100",
                    "title": paid_post["title"],
                    "url": "https://www.reddit.com/r/sqlite/comments/p100/wal_guide/",
                    "subreddit": "sqlite",
                    "date": "2026-11-05",
                    "engagement": {"score": 80, "num_comments": 15},
                    "relevance": 0.9,
                }]
            }

        comment_calls: list[str] = []
        monkeypatch.setattr(reddit, "search_reddit", fake_search_reddit)
        monkeypatch.setattr(
            reddit, "fetch_post_comments",
            lambda url, *_a, **_k: comment_calls.append(url) or [],
        )

        # Direct search_and_enrich keeps the paid post and skips comment calls.
        res = reddit.search_and_enrich(
            "SQLite WAL", "2026-10-10", "2026-11-10",
            depth="quick", token="dummy-sc-key", deadline_monotonic=1050.0,
        )
        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "p100"
        assert res.get("comments_incomplete") is True
        assert "comments incomplete" in res.get("comments_note", "").lower()
        assert comment_calls == []

        # Pipeline backfill path surfaces comments_note in _source_outcome_detail.
        now[0] = 1000.0
        reddit.reset_scrapecreators_memo()
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
            items, artifact = pipeline._retrieve_stream_impl(
                topic="SQLite WAL",
                subquery=schema.SubQuery(
                    label="primary", search_query="SQLite WAL", ranking_query="SQLite WAL", sources=["reddit"],
                ),
                source="reddit",
                config={
                    "SCRAPECREATORS_API_KEY": "dummy-sc-key",
                    "_research_deadline": 1055.0,  # wait_deadline = 1050.0 -> hits deadline right after search
                },
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime(),
                mock=False,
            )

        assert len(items) == 1
        assert "_source_outcome" not in artifact
        assert "Reddit comments incomplete" in artifact.get("_source_outcome_detail", "")

    def test_deadline_mid_comments_keeps_completed_comments_and_stops_further_calls(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        release = threading.Event()
        fetched: list[str] = []

        def fake_fetch_comments(url, token, **kwargs):
            fetched.append(url)
            if "fast" in url:
                return [{"body": "Fast comment with useful detail on WAL", "ups": 25, "author": "alice"}]
            # Slow comment waits until cancelled/abandoned.
            release.wait(timeout=5.0)
            return [{"body": "Too late comment", "ups": 1, "author": "bob"}]

        posts = [
            {
                "id": "R1",
                "reddit_id": "fast",
                "title": "SQLite WAL fast thread",
                "url": "https://www.reddit.com/r/sqlite/comments/fast/wal/",
                "subreddit": "sqlite",
                "date": "2026-11-05",
                "engagement": {"score": 100, "num_comments": 20},
                "relevance": 0.9,
            },
            {
                "id": "R2",
                "reddit_id": "slow",
                "title": "SQLite WAL slow thread",
                "url": "https://www.reddit.com/r/sqlite/comments/slow/wal/",
                "subreddit": "sqlite",
                "date": "2026-11-05",
                "engagement": {"score": 90, "num_comments": 15},
                "relevance": 0.9,
            },
        ]

        monkeypatch.setattr(reddit, "search_reddit", lambda *_a, **_k: {"items": posts})
        monkeypatch.setattr(reddit, "fetch_post_comments", fake_fetch_comments)

        try:
            res = reddit.search_and_enrich(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 0.35,
            )
        finally:
            release.set()

        # Both paid posts survive, fast post keeps its completed comment, and flag is set.
        assert len(res["items"]) == 2
        assert res["items"][0].get("top_comments")
        assert res["items"][0]["top_comments"][0]["author"] == "alice"
        assert not res["items"][1].get("top_comments")
        assert res.get("comments_incomplete") is True

    def test_phase3_deadline_keeps_phase2_paid_posts(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(reddit.time, "monotonic", lambda: now[0])

        def fake_global(*_args, **_kwargs):
            # Phase 2 gathers a paid post and uses up the budget before Phase 3.
            now[0] = 1050.0
            return [{
                "id": "g1",
                "title": "SQLite WAL performance guide",
                "permalink": "/r/sqlite/comments/g1/wal/",
                "subreddit": "sqlite",
                "created_utc": 1793836800,
                "ups": 70,
                "num_comments": 14,
            }]

        sub_mock = mock.Mock(side_effect=AssertionError("Phase 3 must not fire past deadline"))
        monkeypatch.setattr(reddit, "_global_search", fake_global)
        monkeypatch.setattr(reddit, "_subreddit_search", sub_mock)

        res = reddit.search_reddit(
            "SQLite WAL", "2026-10-10", "2026-11-10",
            depth="quick", token="dummy-sc-key", deadline_monotonic=1050.0,
        )
        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "g1"
        assert res.get("incomplete") is True
        sub_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Greptile Round 2 P1 #2: Memo caches mid-spend timeouts, skips pre-spend ones
# ---------------------------------------------------------------------------


class TestGreptileRound2P1MemoNoDoubleSpend:
    def test_timeout_after_paid_request_caches_and_prevents_second_paid_call(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        http_calls: list[str] = []

        def fake_http_get(url, **kwargs):
            http_calls.append(url)
            raise http.DeadlineExceeded(dispatched=True)

        monkeypatch.setattr(reddit.http, "get", fake_http_get)

        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 10.0,
            )

        first_call_count = len(http_calls)
        assert first_call_count >= 1
        assert len(reddit._SC_MEMO) == 1

        # Second call with a fresh generous deadline replays cached TimeoutError with ZERO new HTTP calls!
        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 120.0,
            )
        assert len(http_calls) == first_call_count

    def test_pre_spend_timeout_does_not_cache_so_fresh_deadline_can_run(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        http_calls: list[str] = []

        def fake_http_get(url, **kwargs):
            http_calls.append(url)
            if url.endswith("/reddit/search"):
                return {
                    "posts": [{
                        "id": "fresh1",
                        "title": "SQLite WAL write-ahead logging",
                        "permalink": "/r/sqlite/comments/fresh1/wal/",
                        "subreddit": "sqlite",
                        "created_utc": 1793836800,
                        "ups": 60,
                        "num_comments": 5,
                    }]
                }
            return {"posts": [], "comments": []}

        monkeypatch.setattr(reddit.http, "get", fake_http_get)

        # Already-expired deadline stops BEFORE any paid HTTP request starts.
        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() - 1.0,
            )
        assert http_calls == []
        assert reddit._SC_MEMO == {}

        # A subsequent call with a fresh deadline succeeds cleanly!
        result = reddit.search_and_enrich_memo(
            "SQLite WAL", "2026-10-10", "2026-11-10",
            depth="quick", token="dummy-sc-key",
            deadline_monotonic=time.monotonic() + 60.0,
        )
        assert len(result["items"]) == 1
        assert result["items"][0]["reddit_id"] == "fresh1"
        assert len(http_calls) >= 1


# ---------------------------------------------------------------------------
# Greptile Round 2 P2 #3: x_judge retry preserves error/timeout details when adopted
# ---------------------------------------------------------------------------


class TestGreptileRound2P2JudgeRetryPreservesErrorDetail:
    def test_adopted_retry_preserves_timeout_error_detail_and_state(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["xquik"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        calls: list[str] = []

        def fake_attempt(backend, q, *_a, **_k):
            calls.append(q)
            if len(calls) == 1:
                return [
                    _x_post("1", "Unrelated movie trailer announcement"),
                    _x_post("2", "Unrelated soccer match highlights"),
                    _x_post("3", "Unrelated cooking recipe tips"),
                    _x_post("4", "SQLite write-ahead logging note"),
                ], "", None
            # Retry improves on-topic ratio BUT also reports a timeout (partial search).
            return [
                _x_post("10", "SQLite write-ahead logging concurrency benchmark"),
                _x_post("11", "SQLite write-ahead logging checkpoint latency"),
            ], "request timed out after 30s", health.TIMEOUT

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={"XQUIK_API_KEY": "dummy"},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("xquik"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        assert len(calls) == 2
        assert [it["id"] for it in items] == ["10", "11"]
        assert "X retry via xquik returned partial results (request timed out after 30s)" in artifact["_source_outcome_detail"]
        assert artifact["_source_outcome_detail_state"] == health.TIMEOUT

    def test_adopted_retry_auth_failed_preserves_outcome_and_existing_receipts(self, monkeypatch):
        monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _cfg: ["grok"])
        monkeypatch.setattr(pipeline.fetch_memo, "fetch_once", lambda _k, fn, **_kw: fn())

        calls: list[str] = []

        def fake_attempt(backend, q, from_date, to_date, depth, config, warnings=None, deadline=None):
            calls.append(q)
            if len(calls) == 1:
                if warnings is not None:
                    warnings.append("X: grok narrowed window")
                return [
                    _x_post("1", "Unrelated movie trailer announcement"),
                    _x_post("2", "Unrelated soccer match highlights"),
                    _x_post("3", "Unrelated cooking recipe tips"),
                    _x_post("4", "SQLite write-ahead logging note"),
                ], "", None
            return [
                _x_post("10", "SQLite write-ahead logging concurrency benchmark"),
                _x_post("11", "SQLite write-ahead logging checkpoint latency"),
            ], "grok session expired", health.AUTH_FAILED

        monkeypatch.setattr(pipeline, "_attempt_x_backend", fake_attempt)

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite write-ahead logging",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite write-ahead logging",
                ranking_query="SQLite write-ahead logging",
                sources=["x"],
            ),
            source="x",
            config={},
            depth="default",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("grok"),
            mock=False,
            raw_topic="SQLite write-ahead logging",
        )

        assert [it["id"] for it in items] == ["10", "11"]
        assert artifact["x_receipts"] == ["X: grok narrowed window"]
        assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED
        assert "re-login needed" in artifact["_source_outcome"]["detail"]


# ---------------------------------------------------------------------------
# Greptile Round 3 P1 #1: Preserve finished sibling search results on timeout
# ---------------------------------------------------------------------------


class TestGreptileRound3P1BatchPartialSurvival:
    def test_global_batch_preserves_finished_sibling_when_other_query_times_out(self, monkeypatch):
        release = threading.Event()
        call_index = [0]
        lock = threading.Lock()

        def fake_global(query, token, sort, timeframe, **_kwargs):
            with lock:
                idx = call_index[0]
                call_index[0] += 1
            if idx == 0:
                return [{
                    "id": "fast1",
                    "title": "SQLite WAL concurrency benchmarks",
                    "permalink": "/r/sqlite/comments/fast1/wal/",
                    "subreddit": "sqlite",
                    "created_utc": 1793836800,
                    "ups": 90,
                    "num_comments": 18,
                }]
            release.wait(timeout=5.0)
            return []

        comment_mock = mock.Mock(side_effect=AssertionError("Comments must not run after incomplete search"))
        sub_mock = mock.Mock(side_effect=AssertionError("Phase 3 must not run after incomplete Phase 2"))
        monkeypatch.setattr(
            reddit,
            "expand_reddit_queries",
            lambda *_a, **_kw: ["sqlite wal concurrency", "sqlite wal benchmark"],
        )
        monkeypatch.setattr(reddit, "_global_search", fake_global)
        monkeypatch.setattr(reddit, "_subreddit_search", sub_mock)
        monkeypatch.setattr(reddit, "fetch_post_comments", comment_mock)

        try:
            res = reddit.search_and_enrich(
                "SQLite WAL concurrency", "2026-10-10", "2026-11-10",
                depth="default", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 0.3,
            )
        finally:
            release.set()

        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "fast1"
        assert res.get("incomplete") is True
        assert res.get("incomplete_note")
        assert res.get("comments_incomplete") is True
        sub_mock.assert_not_called()
        comment_mock.assert_not_called()

    def test_preresolved_subreddit_batch_preserves_finished_sibling_and_skips_global(self, monkeypatch):
        release = threading.Event()

        def fake_sub(sub, query, token, sort, timeframe, **_kwargs):
            if sub == "sqlite":
                return [{
                    "id": "sub1",
                    "title": "SQLite WAL checkpoint tuning",
                    "permalink": "/r/sqlite/comments/sub1/wal/",
                    "subreddit": "sqlite",
                    "created_utc": 1793836800,
                    "ups": 75,
                    "num_comments": 12,
                }]
            release.wait(timeout=5.0)
            return []

        global_mock = mock.Mock(side_effect=AssertionError("Phase 2 must not run after incomplete Phase 1.5"))
        monkeypatch.setattr(reddit, "_subreddit_search", fake_sub)
        monkeypatch.setattr(reddit, "_global_search", global_mock)

        try:
            res = reddit.search_reddit(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                subreddits=["sqlite", "slowsub"],
                deadline_monotonic=time.monotonic() + 0.3,
            )
        finally:
            release.set()

        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "sub1"
        assert res.get("incomplete") is True
        global_mock.assert_not_called()

    def test_global_batch_raises_timeout_when_zero_posts_collected(self, monkeypatch):
        release = threading.Event()

        def fake_global(*_args, **_kwargs):
            release.wait(timeout=5.0)
            return []

        monkeypatch.setattr(reddit, "_global_search", fake_global)

        try:
            with pytest.raises(TimeoutError):
                reddit.search_reddit(
                    "SQLite WAL concurrency", "2026-10-10", "2026-11-10",
                    depth="default", token="dummy-sc-key",
                    deadline_monotonic=time.monotonic() + 0.25,
                )
        finally:
            release.set()


# ---------------------------------------------------------------------------
# Greptile Round 3 P2 #2: Dispatch-aware paid tracking in http.py + reddit.py
# ---------------------------------------------------------------------------


class TestGreptileRound3P2DispatchAwarePaidTracking:
    def test_presend_deadline_exceeded_does_not_cache_memo_and_allows_retry(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        fail_first = [True]
        calls: list[str] = []

        def fake_http_get(url, **kwargs):
            calls.append(url)
            if fail_first[0]:
                # Default dispatched=False signals expiry before any network send.
                raise http.DeadlineExceeded()
            if url.endswith("/reddit/search"):
                return {
                    "posts": [{
                        "id": "ok1",
                        "title": "SQLite WAL write-ahead logging",
                        "permalink": "/r/sqlite/comments/ok1/wal/",
                        "subreddit": "sqlite",
                        "created_utc": 1793836800,
                        "ups": 60,
                        "num_comments": 5,
                    }]
                }
            return {"posts": [], "comments": []}

        monkeypatch.setattr(reddit.http, "get", fake_http_get)

        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 10.0,
            )
        assert reddit._SC_MEMO == {}

        fail_first[0] = False
        res = reddit.search_and_enrich_memo(
            "SQLite WAL", "2026-10-10", "2026-11-10",
            depth="quick", token="dummy-sc-key",
            deadline_monotonic=time.monotonic() + 60.0,
        )
        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "ok1"

    def test_real_http_request_presend_expiry_leaves_memo_uncached(self, monkeypatch):
        import json as _json
        from lib import bounded_get

        reddit.reset_scrapecreators_memo()
        transport_calls: list[str] = []
        expire_in_http = [True]

        def fake_bounded_get(req, *, timeout, deadline_monotonic, cancel=None, on_start=None, **_kwargs):
            transport_calls.append(req.full_url)
            if on_start is not None:
                on_start()
            if "/reddit/search" in req.full_url:
                body = _json.dumps({
                    "posts": [{
                        "id": "real1",
                        "title": "SQLite WAL write-ahead logging",
                        "permalink": "/r/sqlite/comments/real1/wal/",
                        "subreddit": "sqlite",
                        "created_utc": 1793836800,
                        "ups": 80,
                        "num_comments": 7,
                    }]
                }).encode("utf-8")
                return 200, body, None
            return 200, b'{"posts": [], "comments": []}', None

        real_monotonic = time.monotonic

        def fake_http_monotonic():
            base = real_monotonic()
            return base + 100.0 if expire_in_http[0] else base

        monkeypatch.setattr(bounded_get, "get", fake_bounded_get)
        monkeypatch.setattr(http.time, "monotonic", fake_http_monotonic)
        monkeypatch.setattr(http.usage, "begin_http", lambda *_a, **_kw: None)
        monkeypatch.setattr(http.usage, "finish", lambda *_a, **_kw: None)

        # Deadline is still live in reddit._check_sc_spend, then expired by the time
        # http.request checks remaining budget before launching bounded_get.
        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=real_monotonic() + 10.0,
            )
        assert transport_calls == []
        assert reddit._SC_MEMO == {}

        # Fresh call succeeds once budget is live inside http.request.
        expire_in_http[0] = False
        res = reddit.search_and_enrich_memo(
            "SQLite WAL", "2026-10-10", "2026-11-10",
            depth="quick", token="dummy-sc-key",
            deadline_monotonic=real_monotonic() + 60.0,
        )
        assert len(res["items"]) == 1
        assert res["items"][0]["reddit_id"] == "real1"
        assert len(transport_calls) >= 1

    def test_real_http_request_midflight_timeout_marks_dispatched_and_caches_memo(self, monkeypatch):
        from lib import bounded_get

        reddit.reset_scrapecreators_memo()
        release = threading.Event()
        transport_calls: list[str] = []

        def blocking_bounded_get(req, *, timeout, deadline_monotonic, cancel=None, on_start=None, **_kwargs):
            transport_calls.append(req.full_url)
            if on_start is not None:
                on_start()
            release.wait(timeout=5.0)
            raise bounded_get.GetTimeout("in flight", started=True)

        monkeypatch.setattr(bounded_get, "get", blocking_bounded_get)
        monkeypatch.setattr(http.usage, "begin_http", lambda *_a, **_kw: None)
        monkeypatch.setattr(http.usage, "finish", lambda *_a, **_kw: None)

        try:
            with pytest.raises(TimeoutError):
                reddit.search_and_enrich_memo(
                    "SQLite WAL", "2026-10-10", "2026-11-10",
                    depth="quick", token="dummy-sc-key",
                    deadline_monotonic=time.monotonic() + 0.3,
                )
        finally:
            release.set()

        first_opens = len(transport_calls)
        assert first_opens >= 1
        assert len(reddit._SC_MEMO) == 1

        # Second call replays cached TimeoutError without dispatching any new request.
        with pytest.raises(TimeoutError):
            reddit.search_and_enrich_memo(
                "SQLite WAL", "2026-10-10", "2026-11-10",
                depth="quick", token="dummy-sc-key",
                deadline_monotonic=time.monotonic() + 60.0,
            )
        assert len(transport_calls) == first_opens

    def test_unowned_http_request_tracks_presend_vs_midflight_dispatch(self, monkeypatch):
        release = threading.Event()
        open_calls: list[str] = []

        def blocking_open(req, timeout, **kwargs):
            open_calls.append(req.full_url)
            release.wait(timeout=5.0)
            raise TimeoutError("socket timed out")

        monkeypatch.setattr(http, "_open_request", blocking_open)
        monkeypatch.setattr(http.usage, "begin_http", lambda *_a, **_kw: None)
        monkeypatch.setattr(http.usage, "finish", lambda *_a, **_kw: None)

        # Pre-send expiry: _open_request never runs and dispatched=False.
        dispatched: list[bool] = []
        with pytest.raises(http.DeadlineExceeded) as exc_pre:
            with http.observe_dispatch(lambda: dispatched.append(True)):
                http.get(
                    "https://api.scrapecreators.com/v1/reddit/search",
                    deadline_monotonic=time.monotonic() - 1.0,
                )
        assert exc_pre.value.dispatched is False
        assert dispatched == []
        assert open_calls == []

        # Mid-flight timeout: _open_request starts, observe_dispatch fires, and dispatched=True.
        try:
            with pytest.raises(http.DeadlineExceeded) as exc_mid:
                with http.observe_dispatch(lambda: dispatched.append(True)):
                    http.get(
                        "https://api.scrapecreators.com/v1/reddit/search",
                        deadline_monotonic=time.monotonic() + 0.15,
                    )
        finally:
            release.set()
        assert exc_mid.value.dispatched is True
        assert dispatched == [True]
        assert len(open_calls) == 1

    def test_owned_get_timeout_dispatched_matches_bounded_get_started_flag(self, monkeypatch):
        from lib import bounded_get

        monkeypatch.setattr(http.usage, "begin_http", lambda *_a, **_kw: None)
        monkeypatch.setattr(http.usage, "cancel", lambda *_a, **_kw: None)

        dispatched_events: list[bool] = []
        monkeypatch.setattr(
            bounded_get,
            "get",
            lambda *_a, **_kw: (_ for _ in ()).throw(bounded_get.GetTimeout("before launch", started=False)),
        )
        with pytest.raises(http.DeadlineExceeded) as exc_unstarted:
            with http.observe_dispatch(lambda: dispatched_events.append(True)):
                http.get(
                    "https://api.scrapecreators.com/v1/reddit/post/comments",
                    owned_get=True,
                    deadline_monotonic=time.monotonic() + 10.0,
                )
        assert exc_unstarted.value.dispatched is False
        assert dispatched_events == []

        monkeypatch.setattr(
            bounded_get,
            "get",
            lambda *_a, **_kw: (_ for _ in ()).throw(bounded_get.GetTimeout("in flight", started=True)),
        )
        with pytest.raises(http.DeadlineExceeded) as exc_started:
            with http.observe_dispatch(lambda: dispatched_events.append(True)):
                http.get(
                    "https://api.scrapecreators.com/v1/reddit/post/comments",
                    owned_get=True,
                    deadline_monotonic=time.monotonic() + 10.0,
                )
        assert exc_started.value.dispatched is True
        assert dispatched_events == [True]


# ---------------------------------------------------------------------------
# Greptile Round 3 P2 #3: Pinned SC-primary incomplete empty -> TIMEOUT
# ---------------------------------------------------------------------------


class TestGreptileRound3P2PinnedIncompleteEmptyTimeout:
    def test_pinned_scrapecreators_incomplete_empty_after_date_filter_raises_timeout(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        now = [1000.0]
        monkeypatch.setattr(reddit.time, "monotonic", lambda: now[0])

        # Phase 2 returns an out-of-window post (date filtered out in Phase 5),
        # and exhausts the budget before Phase 3 so search_reddit returns
        # {"items": [], "incomplete": True, "incomplete_note": ...}.
        def fake_global(*_args, **_kwargs):
            now[0] = 1050.0
            return [{
                "id": "old1",
                "title": "SQLite WAL write-ahead logging old post",
                "permalink": "/r/sqlite/comments/old1/wal/",
                "subreddit": "sqlite",
                "created_utc": 1700000000,  # 2023-11-14: outside 2026-10-10..2026-11-10
                "ups": 90,
                "num_comments": 10,
            }]

        monkeypatch.setattr(reddit, "_global_search", fake_global)
        monkeypatch.setattr(reddit, "_subreddit_search", mock.Mock(side_effect=AssertionError("Phase 3 must not run")))
        monkeypatch.setattr(pipeline.reddit_public, "search_reddit_public", lambda *_a, **_kw: [])

        config = {
            "SCRAPECREATORS_API_KEY": "dummy-sc-key",
            pipeline.env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
            "_research_deadline": 1050.0,
        }
        with pytest.raises(pipeline.SourceRunError) as exc_info:
            pipeline._retrieve_stream_impl(
                topic="SQLite WAL",
                subquery=schema.SubQuery(
                    label="primary",
                    search_query="SQLite WAL",
                    ranking_query="SQLite WAL",
                    sources=["reddit"],
                ),
                source="reddit",
                config=config,
                depth="quick",
                date_range=("2026-10-10", "2026-11-10"),
                runtime=_runtime("xquik"),
                mock=False,
                raw_topic="SQLite WAL",
            )
        assert exc_info.value.outcome_state == health.TIMEOUT
        assert "deadline" in str(exc_info.value).lower() or "incomplete" in str(exc_info.value).lower()

    def test_pinned_scrapecreators_incomplete_empty_with_public_fallback_surfaces_timeout(self, monkeypatch):
        reddit.reset_scrapecreators_memo()
        monkeypatch.setattr(
            reddit,
            "search_and_enrich_memo",
            lambda *_a, **_kw: {
                "items": [],
                "incomplete": True,
                "incomplete_note": "Reddit search incomplete (deadline reached)",
            },
        )
        public_item = {
            "id": "R1",
            "title": "SQLite WAL public fallback item",
            "url": "https://www.reddit.com/r/sqlite/comments/pub1/wal/",
            "subreddit": "sqlite",
            "date": "2026-11-01",
            "score": 45,
        }
        monkeypatch.setattr(
            pipeline.reddit_public,
            "search_reddit_public",
            lambda *_a, **_kw: [public_item],
        )

        items, artifact = pipeline._retrieve_stream_impl(
            topic="SQLite WAL",
            subquery=schema.SubQuery(
                label="primary",
                search_query="SQLite WAL",
                ranking_query="SQLite WAL",
                sources=["reddit"],
            ),
            source="reddit",
            config={
                "SCRAPECREATORS_API_KEY": "dummy-sc-key",
                pipeline.env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
            },
            depth="quick",
            date_range=("2026-10-10", "2026-11-10"),
            runtime=_runtime("xquik"),
            mock=False,
            raw_topic="SQLite WAL",
        )
        assert items == [public_item]
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert "deadline" in artifact["_source_outcome"]["detail"].lower()
