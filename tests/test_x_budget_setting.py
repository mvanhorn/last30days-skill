"""LAST30DAYS_X_BUDGET_SECONDS: the X chain budget follows the resolved config.

A grok call on a subscription account measured 150-170s, longer than the fixed
90s chain deadline, so the lane exhausted its budget before the first call
returned and reported no X posts (issue #1626).
"""

import contextlib
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))

from lib import pipeline, schema  # noqa: E402


class XBudgetSettingTest(unittest.TestCase):
    def test_default_budget_keeps_the_chain_constant(self):
        self.assertEqual(
            pipeline._x_chain_budget_seconds({}), pipeline.X_CHAIN_DEADLINE_SECONDS,
        )

    def test_configured_budget_is_used(self):
        self.assertEqual(
            pipeline._x_chain_budget_seconds({"LAST30DAYS_X_BUDGET_SECONDS": "200"}), 200.0,
        )
        self.assertEqual(
            pipeline._x_chain_budget_seconds({"LAST30DAYS_X_BUDGET_SECONDS": 150}), 150.0,
        )

    def test_unusable_budgets_fall_back_to_the_default(self):
        for raw in (None, "", "  ", "not-a-number", 0, -5, "0"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    pipeline._x_chain_budget_seconds({"LAST30DAYS_X_BUDGET_SECONDS": raw}),
                    pipeline.X_CHAIN_DEADLINE_SECONDS,
                )

    def test_non_finite_budgets_fall_back_to_the_default(self):
        # A NaN would otherwise pass the non-positive check, propagate through
        # min(), and leave the chain deadline permanently unreached.
        for raw in ("nan", float("nan"), "inf", float("inf"), "-inf"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    pipeline._x_chain_budget_seconds({"LAST30DAYS_X_BUDGET_SECONDS": raw}),
                    pipeline.X_CHAIN_DEADLINE_SECONDS,
                )

    def test_only_x_plans_take_the_raised_stream_wait(self):
        plan = mock.Mock()
        plan.subqueries = [mock.Mock(sources=["reddit"])]
        self.assertFalse(pipeline._plan_includes_x(plan, {"reddit"}))
        plan.subqueries = [mock.Mock(sources=["reddit", "x"])]
        self.assertTrue(pipeline._plan_includes_x(plan, {"reddit", "x"}))
        self.assertFalse(pipeline._plan_includes_x(plan, {"reddit"}))

    def test_budget_above_the_per_call_ceiling_clamps(self):
        self.assertEqual(
            pipeline._x_chain_budget_seconds({"LAST30DAYS_X_BUDGET_SECONDS": "999"}),
            pipeline.X_CHAIN_DEADLINE_MAX_SECONDS,
        )

    def test_stream_wait_covers_the_configured_budget(self):
        self.assertEqual(
            pipeline._x_stream_wait_seconds({}), pipeline.STREAM_FUTURE_TIMEOUT_SECONDS,
        )
        self.assertEqual(
            pipeline._x_stream_wait_seconds({"LAST30DAYS_X_BUDGET_SECONDS": "200"}), 210.0,
        )

    def test_unset_budget_keeps_the_standard_stream_wait(self):
        # The raise is for an explicit setting only: a run with no budget keeps
        # the module's standard wait even when another run lowered the constant.
        with mock.patch.object(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 0.2):
            self.assertEqual(pipeline._x_stream_wait_seconds({}), 0.2)
            self.assertEqual(
                pipeline._x_stream_wait_seconds({"LAST30DAYS_X_BUDGET_SECONDS": "200"}),
                200.0 + pipeline.X_STREAM_WAIT_MARGIN_SECONDS,
            )

    def test_run_keeps_the_standard_wait_without_a_budget(self):
        """No explicit budget: a tight cutoff must not shorten the base wait."""
        seen = {}

        def fake_retrieve(**kwargs):
            if kwargs.get("source") == "x":
                seen["deadline"] = kwargs["config"]["_research_deadline"]
            return [], {}

        plan = schema.QueryPlan(
            raw_topic="sqlite",
            intent="technical",
            freshness_mode="balanced_recent",
            cluster_mode="coverage",
            source_weights={"x": 1.0},
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="sqlite",
                    ranking_query="sqlite", sources=["x"],
                )
            ],
        )
        real_completed_streams = pipeline._completed_streams

        def fake_batches(batches, bundle, retry_origins=None):
            seen["batch_deadline"] = batches[0][2]
            return real_completed_streams(batches, bundle, retry_origins=retry_origins)

        cutoff = pipeline.time.monotonic() + 120.0
        config = {"_finalization_deadline": cutoff}
        with mock.patch.object(pipeline, "_retrieve_stream", fake_retrieve), \
                mock.patch.object(pipeline, "_completed_streams", fake_batches):
            report = pipeline.run(
                topic="sqlite", config=config, depth="quick",
                requested_sources=["x"], mock=True, web_backend="none",
                external_plan=schema.to_dict(plan),
            )
        self.assertIsNotNone(report)
        self.assertIn("deadline", seen, "run() never submitted the X stream")
        self.assertGreater(
            seen["deadline"], cutoff + 1.0,
            "an unset budget shortened the standard 180s wait to the cutoff",
        )
        self.assertGreater(
            seen["batch_deadline"], cutoff + 1.0,
            "an unset budget shortened the collector deadline to the cutoff",
        )

    def test_run_caps_the_x_wait_at_the_finalization_cutoff(self):
        """run() must not hand X a wait that outlives the finalization window."""
        seen = {}

        def fake_retrieve(**kwargs):
            if kwargs.get("source") == "x":
                seen["deadline"] = kwargs["config"]["_research_deadline"]
            return [], {}

        plan = schema.QueryPlan(
            raw_topic="sqlite",
            intent="technical",
            freshness_mode="balanced_recent",
            cluster_mode="coverage",
            source_weights={"x": 1.0},
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="sqlite",
                    ranking_query="sqlite", sources=["x"],
                )
            ],
        )
        # 120s left before finalization: enough for the cut-off case to differ
        # from the old 180s floor, and far longer than this mocked run takes.
        real_completed_streams = pipeline._completed_streams

        def fake_batches(batches, bundle, retry_origins=None):
            seen["batch_deadline"] = batches[0][2]
            return real_completed_streams(batches, bundle, retry_origins=retry_origins)

        cutoff = pipeline.time.monotonic() + 120.0
        config = {
            "LAST30DAYS_X_BUDGET_SECONDS": "200",
            "_finalization_deadline": cutoff,
        }
        with mock.patch.object(pipeline, "_retrieve_stream", fake_retrieve), \
                mock.patch.object(pipeline, "_completed_streams", fake_batches):
            report = pipeline.run(
                topic="sqlite", config=config, depth="quick",
                requested_sources=["x"], mock=True, web_backend="none",
                external_plan=schema.to_dict(plan),
            )
        self.assertIsNotNone(report)
        self.assertIn("deadline", seen, "run() never submitted the X stream")
        self.assertLessEqual(
            seen["deadline"], cutoff + 0.001,
            "the X stream wait outlived the run's finalization cutoff",
        )
        self.assertGreater(seen["deadline"], cutoff - 120.0)
        self.assertLessEqual(
            seen["batch_deadline"], cutoff + 0.001,
            "run() handed the collector a deadline past the finalization cutoff",
        )
        self.assertGreaterEqual(
            seen["batch_deadline"], cutoff - 0.5,
            "run() collapsed the raised wait to zero instead of the cutoff",
        )

    def test_slow_submission_cannot_extend_the_collector_past_the_cutoff(self):
        """A late collector start still stops at the original absolute cutoff."""
        seen = {}
        release = threading.Event()
        real_abandoning_executor = pipeline._abandoning_executor

        class _SlowSubmit:
            """Delay each submission so the collector starts after fan-out began."""

            def __init__(self, inner, delay, recorder):
                self._inner = inner
                self._delay = delay
                self._recorder = recorder

            def submit(self, *args, **kwargs):
                self._recorder["submits"] = self._recorder.get("submits", 0) + 1
                if self._recorder["submits"] <= 1:
                    pipeline.time.sleep(self._delay)
                return self._inner.submit(*args, **kwargs)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        @contextlib.contextmanager
        def slow_abandoning_executor(max_workers):
            with real_abandoning_executor(max_workers=max_workers) as inner:
                yield _SlowSubmit(inner, 0.8, seen)

        def fake_retrieve(**kwargs):
            if kwargs.get("source") == "x":
                seen["x_started"] = pipeline.time.monotonic()
                release.wait(10.0)
            return [], {}

        def fake_finalization_deadline(_config, _run_started):
            # Deterministic cutoff relative to run()'s own clock: the fan-out
            # starts milliseconds after this call. Later finalization calls
            # must see the same cutoff.
            if "cutoff" not in seen:
                seen["cutoff"] = pipeline.time.monotonic() + 1.5
            return seen["cutoff"]

        plan = schema.QueryPlan(
            raw_topic="sqlite",
            intent="technical",
            freshness_mode="balanced_recent",
            cluster_mode="coverage",
            source_weights={"x": 1.0},
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="sqlite",
                    ranking_query="sqlite", sources=["x"],
                )
            ],
        )
        real_completed_streams = pipeline._completed_streams

        def fake_batches(batches, bundle, retry_origins=None):
            seen["collector_start"] = pipeline.time.monotonic()
            seen["batch_deadline"] = batches[0][2]
            seen["bundle"] = bundle
            return real_completed_streams(batches, bundle, retry_origins=retry_origins)

        started = pipeline.time.monotonic()
        try:
            with mock.patch.object(pipeline, "_retrieve_stream", fake_retrieve), \
                    mock.patch.object(pipeline, "_completed_streams", fake_batches), \
                    mock.patch.object(pipeline, "_finalization_deadline", fake_finalization_deadline), \
                    mock.patch.object(pipeline, "_abandoning_executor", slow_abandoning_executor):
                report = pipeline.run(
                    topic="sqlite", config={"LAST30DAYS_X_BUDGET_SECONDS": "200"},
                    depth="quick", requested_sources=["x"], mock=True,
                    web_backend="none", external_plan=schema.to_dict(plan),
                )
        finally:
            release.set()
        elapsed = pipeline.time.monotonic() - started
        self.assertIsNotNone(report)
        self.assertIn("x_started", seen, "run() never submitted the X stream")
        self.assertGreater(
            seen["submits"], 0, "the test never delayed a stream submission",
        )
        self.assertGreaterEqual(
            seen["collector_start"] - started, 0.7,
            "the collector started before the delayed submission finished",
        )
        self.assertLess(
            elapsed, seen["cutoff"] - started + 0.5,
            "run() waited past the absolute cutoff after a slow submission",
        )
        self.assertLessEqual(
            seen["batch_deadline"], seen["cutoff"] + 0.001,
            "the collector deadline passed the run's finalization cutoff",
        )
        self.assertGreaterEqual(
            seen["batch_deadline"], seen["cutoff"] - 0.5,
            "the collector deadline was not the finalization cutoff",
        )
        detail = seen["bundle"].errors_by_source.get("x", "")
        self.assertIn("timed out after", detail)
        reported = float(detail.split("timed out after ")[1].rstrip("s"))
        self.assertLess(
            reported, 1.3,
            "the timeout detail reported the pre-fan-out budget, not the cutoff wait",
        )
        self.assertGreaterEqual(
            reported, 0.4,
            "the timeout detail did not report the cutoff-limited wait",
        )

    def test_supplemental_lane_deadline_clamps_to_the_recorded_cutoff(self):
        """A supplemental lane budget may not outlive the recorded cutoff."""
        cutoff = pipeline.time.monotonic() + 5.0
        config = {
            "LAST30DAYS_X_BUDGET_SECONDS": "200",
            "_finalization_deadline": cutoff,
            "_research_deadline": cutoff + 600.0,
        }
        self.assertEqual(pipeline._x_lane_deadline(config, 150.0), cutoff)
        self.assertEqual(
            pipeline._x_lane_deadline(
                {"_research_deadline": cutoff}, pipeline.grok_x.LANE_BUDGET_SECONDS,
            ),
            cutoff,
        )
        # Without a recorded cutoff the lane keeps its own budget.
        lane = pipeline._x_lane_deadline({}, 150.0)
        self.assertGreater(lane, pipeline.time.monotonic() + 140.0)

    def test_supplemental_handle_lanes_receive_the_cutoff_deadline(self):
        """The grok handle lanes must run on the clamped budget, not now+150s."""
        captured = {}
        cutoff = pipeline.time.monotonic() + 5.0
        config = {
            "LAST30DAYS_X_BUDGET_SECONDS": "200",
            "_finalization_deadline": cutoff,
        }
        plan = schema.QueryPlan(
            raw_topic="Peter Steinberger",
            intent="person",
            freshness_mode="balanced_recent",
            cluster_mode="coverage",
            source_weights={"x": 1.0},
            subqueries=[
                schema.SubQuery(
                    label="primary", search_query="Peter Steinberger",
                    ranking_query="Peter Steinberger", sources=["x"],
                )
            ],
        )

        def fake_search(*_args, **kwargs):
            captured.setdefault("deadlines", []).append(kwargs.get("deadline"))
            return [], False

        with mock.patch.object(pipeline.env, "x_backend_chain", lambda _c: ["grok"]), \
                mock.patch.object(
                    pipeline.x_judge, "promotable_handles",
                    lambda *_a, **_k: (["steipete"], []),
                ), \
                mock.patch.object(pipeline.grok_x, "search_handles", fake_search), \
                mock.patch.object(pipeline.grok_x, "search_mentions", fake_search), \
                mock.patch.object(pipeline.grok_x, "search_name", fake_search):
            pipeline._run_supplemental_searches(
                topic="Peter Steinberger",
                bundle=schema.RetrievalBundle(),
                plan=plan,
                config=config,
                depth="default",
                date_range=("2026-10-27", "2026-11-26"),
                runtime=mock.Mock(x_search_backend=None),
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                x_handle="steipete",
            )
        self.assertTrue(captured.get("deadlines"), "no grok handle lane ran")
        for deadline in captured["deadlines"]:
            self.assertLessEqual(
                deadline, cutoff + 0.001,
                "a supplemental handle lane outlived the finalization cutoff",
            )
            self.assertGreaterEqual(
                deadline, cutoff - 0.5,
                "the handle lane did not receive the recorded cutoff",
            )


    def test_resolved_config_layers_the_setting(self):
        with mock.patch.dict("os.environ", {"LAST30DAYS_X_BUDGET_SECONDS": "200"}):
            config = pipeline.env.get_config()
        self.assertEqual(config.get("LAST30DAYS_X_BUDGET_SECONDS"), "200")

    def test_pinned_chain_deadline_uses_the_configured_budget(self):
        """The chain handed to the backend must run past one 150-170s grok call."""
        captured = {}

        def fake_attempt(
            backend, query, from_date, to_date, depth, config,
            warnings=None, deadline=None,
        ):
            captured["deadline"] = deadline
            return [], "boom", None

        plan = mock.Mock()
        plan.domain = "topic"
        with mock.patch.object(pipeline.env, "x_backend_chain", lambda c: ["grok"]), \
                mock.patch.object(pipeline, "_attempt_x_backend", fake_attempt), \
                mock.patch.object(pipeline.time, "monotonic", return_value=1000.0):
            pipeline._fetch_discovery_source(
                "x", plan, from_date="2026-08-01", to_date="2026-08-31",
                depth="quick", mock=False,
                config={"LAST30DAYS_X_BUDGET_SECONDS": "200"},
                keyword_gate=True,
            )
        self.assertAlmostEqual(captured["deadline"], 1200.0)


if __name__ == "__main__":
    unittest.main()
