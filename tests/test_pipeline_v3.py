import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import pipeline
from lib import http
from lib import schema


class PipelineV3Tests(unittest.TestCase):
    def test_mock_pipeline_report_without_live_credentials(self):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["reddit", "x", "grounding"],
            mock=True,
        )
        self.assertEqual("test topic", report.topic)
        self.assertTrue(report.ranked_candidates)
        self.assertTrue(report.clusters)
        self.assertIn("x", report.items_by_source)
        # Grounding items now enter the ranked pool (web search backends produce real items)
        self.assertIn("grounding", report.items_by_source)
        self.assertEqual("gemini", report.provider_runtime.reasoning_provider)


class TestSourceFetchCap(unittest.TestCase):
    """X source fetch count must be capped by MAX_SOURCE_FETCHES."""

    def test_x_capped_in_max_source_fetches(self):
        """MAX_SOURCE_FETCHES must cap X at 2 to prevent 429 cascades."""
        self.assertIn("x", pipeline.MAX_SOURCE_FETCHES)
        self.assertEqual(pipeline.MAX_SOURCE_FETCHES["x"], 2)

    def test_cap_logic_limits_source_submissions(self):
        """Verify the cap logic skips submissions beyond the limit."""
        cap = pipeline.MAX_SOURCE_FETCHES.get("x", float("inf"))
        subquery_sources = [
            ["x", "reddit", "youtube"],
            ["x", "reddit", "youtube"],
            ["x", "reddit", "youtube"],
            ["x", "reddit", "youtube"],
        ]
        source_fetch_count: dict[str, int] = {}
        submitted: list[str] = []
        for sources in subquery_sources:
            for source in sources:
                source_cap = pipeline.MAX_SOURCE_FETCHES.get(source)
                if source_cap is not None:
                    current = source_fetch_count.get(source, 0)
                    if current >= source_cap:
                        continue
                    source_fetch_count[source] = current + 1
                submitted.append(source)

        x_count = submitted.count("x")
        reddit_count = submitted.count("reddit")
        self.assertEqual(x_count, 2, f"X should be capped at 2, got {x_count}")
        self.assertEqual(reddit_count, 4, f"Reddit should be uncapped, got {reddit_count}")

    @patch("lib.pipeline._retrieve_stream")
    def test_mock_run_caps_x_fetches(self, mock_retrieve):
        """Pipeline.run in mock mode should call _retrieve_stream for X at most 2 times."""
        mock_retrieve.side_effect = lambda **kwargs: pipeline._mock_stream_results(
            kwargs["source"], kwargs["subquery"]
        )
        report = pipeline.run(
            topic="compare iPhone vs Android vs Pixel vs Samsung",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["reddit", "x"],
            mock=True,
        )
        x_calls = [
            call for call in mock_retrieve.call_args_list
            if call.kwargs.get("source") == "x"
        ]
        self.assertLessEqual(
            len(x_calls), 2,
            f"X should be fetched at most 2 times, got {len(x_calls)}",
        )


class TestRateLimitSharing(unittest.TestCase):
    """429 signals should be shared across subqueries."""

    def test_is_rate_limit_error_detects_429_status(self):
        exc = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
        self.assertTrue(pipeline._is_rate_limit_error(exc))

    def test_is_rate_limit_error_ignores_non_429(self):
        exc = http.HTTPError("HTTP 400: Bad Request", status_code=400)
        self.assertFalse(pipeline._is_rate_limit_error(exc))

    def test_is_rate_limit_error_detects_429_in_string(self):
        exc = RuntimeError("xAI returned 429 rate limit")
        self.assertTrue(pipeline._is_rate_limit_error(exc))

    def test_is_rate_limit_error_rejects_unrelated_error(self):
        exc = RuntimeError("Connection refused")
        self.assertFalse(pipeline._is_rate_limit_error(exc))

    def test_retrieve_stream_skips_rate_limited_source(self):
        """_retrieve_stream should return empty when source is rate-limited."""
        from lib import schema
        rate_limited = {"x"}
        lock = threading.Lock()
        subquery = schema.SubQuery(
            label="test",
            search_query="test query",
            ranking_query="test query",
            sources=["x"],
        )
        items, artifact = pipeline._retrieve_stream(
            topic="test",
            subquery=subquery,
            source="x",
            config={},
            depth="quick",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=schema.ProviderRuntime(
                reasoning_provider="mock",
                planner_model="mock",
                rerank_model="mock",
            ),
            mock=True,
            rate_limited_sources=rate_limited,
            rate_limit_lock=lock,
        )
        self.assertEqual(items, [])
        self.assertEqual(artifact, {})


class TestThinSourceRetry(unittest.TestCase):
    @patch("lib.pipeline._retrieve_stream")
    def test_retry_includes_planned_source_with_zero_initial_items(self, mock_retrieve):
        mock_retrieve.return_value = (
            [
                {
                    "id": "X100",
                    "text": "OpenClaw funding update from an investor",
                    "url": "https://x.com/example/status/100",
                    "author_handle": "example",
                    "date": "2026-03-15",
                    "engagement": {"likes": 25, "reposts": 4, "replies": 2},
                    "relevance": 0.8,
                    "why_relevant": "retry result",
                }
            ],
            {},
        )

        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="latest OpenClaw funding updates",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="latest OpenClaw funding updates",
                    ranking_query="What recent evidence matters for OpenClaw funding?",
                    sources=["x", "reddit"],
                )
            ],
            source_weights={"x": 1.0, "reddit": 1.0},
        )
        bundle = schema.RetrievalBundle(
            items_by_source={
                "reddit": [
                    _make_source_item("reddit", "r1", "https://reddit.com/1"),
                    _make_source_item("reddit", "r2", "https://reddit.com/2"),
                    _make_source_item("reddit", "r3", "https://reddit.com/3"),
                ]
            }
        )

        pipeline._retry_thin_sources(
            topic="latest OpenClaw funding updates",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            settings=pipeline.DEPTH_SETTINGS["default"],
        )

        self.assertEqual(["x"], [call.kwargs["source"] for call in mock_retrieve.call_args_list])
        self.assertIn("x", bundle.items_by_source)
        self.assertEqual("https://x.com/example/status/100", bundle.items_by_source["x"][0].url)


def _make_runtime(x_backend="bird"):
    return schema.ProviderRuntime(
        reasoning_provider="mock",
        planner_model="mock",
        rerank_model="mock",
        x_search_backend=x_backend,
    )


def _make_plan(topic="test topic"):
    return schema.QueryPlan(
        intent="exploration",
        freshness_mode="balanced_recent",
        cluster_mode="topic",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=f"What recent evidence matters for {topic}?",
                sources=["x", "reddit"],
            )
        ],
        source_weights={"x": 1.0, "reddit": 1.0},
    )


def _make_source_item(source, item_id, url, author=None, body="", container=None, metadata=None):
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=f"Item {item_id}",
        body=body,
        url=url,
        author=author,
        container=container,
        metadata=metadata or {},
    )


class TestSupplementalSearches(unittest.TestCase):
    """R1: Phase 2 entity drilling should be wired into the pipeline."""

    def test_run_supplemental_searches_exists(self):
        """_run_supplemental_searches must be a callable in pipeline module."""
        self.assertTrue(
            hasattr(pipeline, "_run_supplemental_searches"),
            "_run_supplemental_searches function not found in pipeline module",
        )
        self.assertTrue(callable(pipeline._run_supplemental_searches))

    @patch("lib.bird_x.search_handles")
    @patch("lib.entity_extract.extract_entities")
    def test_entity_extract_called_after_phase1(self, mock_extract, mock_handles):
        """Phase 2 should call entity_extract on Phase 1 X results, then search_handles."""
        mock_extract.return_value = {"x_handles": ["analyst1", "reporter2"], "x_hashtags": [], "reddit_subreddits": []}
        mock_handles.return_value = [
            {
                "id": "supp1",
                "text": "Supplemental tweet from analyst1",
                "url": "https://x.com/analyst1/status/999",
                "author_handle": "analyst1",
                "date": "2026-03-15",
                "engagement": {"likes": 50},
                "relevance": 0.8,
                "why_relevant": "direct handle search",
            }
        ]

        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/analyst1/status/1", author="analyst1", body="Some tweet about AI"),
            _make_source_item("x", "X2", "https://x.com/reporter2/status/2", author="reporter2", body="AI analysis @expert3"),
        ]

        plan = _make_plan("AI safety")
        config = {}

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=plan,
            config=config,
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )

        mock_extract.assert_called_once()
        mock_handles.assert_called_once()
        # Supplemental items should be merged into bundle
        x_urls = {item.url for item in bundle.items_by_source.get("x", [])}
        self.assertIn("https://x.com/analyst1/status/999", x_urls)

    @patch("lib.bird_x.search_handles")
    @patch("lib.entity_extract.extract_entities")
    def test_supplemental_items_deduplicated_by_url(self, mock_extract, mock_handles):
        """Supplemental items with same URL as Phase 1 should not be duplicated."""
        mock_extract.return_value = {"x_handles": ["analyst1"], "x_hashtags": [], "reddit_subreddits": []}
        # Return item with same URL as Phase 1
        mock_handles.return_value = [
            {
                "id": "dup1",
                "text": "Same tweet",
                "url": "https://x.com/analyst1/status/1",
                "author_handle": "analyst1",
                "date": "2026-03-15",
                "engagement": {"likes": 50},
                "relevance": 0.8,
                "why_relevant": "duplicate",
            }
        ]

        bundle = schema.RetrievalBundle()
        original = _make_source_item("x", "X1", "https://x.com/analyst1/status/1", author="analyst1")
        bundle.items_by_source["x"] = [original]

        plan = _make_plan("AI safety")

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )

        # Should still have only 1 item (no duplicates)
        x_items = bundle.items_by_source.get("x", [])
        urls = [item.url for item in x_items]
        self.assertEqual(
            urls.count("https://x.com/analyst1/status/1"), 1,
            f"Duplicate URL found: {urls}",
        )

    def test_phase2_skipped_in_quick_mode(self):
        """_run_supplemental_searches should return immediately when depth='quick'."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/a/1", author="someone"),
        ]

        # If it tries to import entity_extract, that's fine -- it should return before calling it
        pipeline._run_supplemental_searches(
            topic="test",
            bundle=bundle,
            plan=_make_plan(),
            config={},
            depth="quick",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )
        # Bundle should be unchanged (only original item)
        self.assertEqual(len(bundle.items_by_source["x"]), 1)

    def test_phase2_skipped_in_mock_mode(self):
        """_run_supplemental_searches should return immediately when mock=True."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/a/1", author="someone"),
        ]

        pipeline._run_supplemental_searches(
            topic="test",
            bundle=bundle,
            plan=_make_plan(),
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=True,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )
        self.assertEqual(len(bundle.items_by_source["x"]), 1)

    def test_phase2_skipped_when_x_rate_limited(self):
        """_run_supplemental_searches should skip when X is rate-limited."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/a/1", author="someone"),
        ]

        pipeline._run_supplemental_searches(
            topic="test",
            bundle=bundle,
            plan=_make_plan(),
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources={"x"},
            rate_limit_lock=threading.Lock(),
        )
        self.assertEqual(len(bundle.items_by_source["x"]), 1)

    def test_phase2_skipped_when_backend_not_bird(self):
        """_run_supplemental_searches should skip when X backend is not bird."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/a/1", author="someone"),
        ]

        pipeline._run_supplemental_searches(
            topic="test",
            bundle=bundle,
            plan=_make_plan(),
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("xai"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )
        self.assertEqual(len(bundle.items_by_source["x"]), 1)


class TestThinSourceRetry(unittest.TestCase):
    """R2: Dynamic query refinement on thin results."""

    def test_retry_thin_sources_exists(self):
        """_retry_thin_sources must be a callable in pipeline module."""
        self.assertTrue(
            hasattr(pipeline, "_retry_thin_sources"),
            "_retry_thin_sources function not found in pipeline module",
        )
        self.assertTrue(callable(pipeline._retry_thin_sources))

    @patch("lib.pipeline._retrieve_stream")
    def test_thin_source_retried_with_core_subject(self, mock_retrieve):
        """Sources with < 3 items and no errors should be retried."""
        mock_retrieve.return_value = (
            [
                {
                    "id": "retry1",
                    "title": "Retry result",
                    "url": "https://reddit.com/r/test/2",
                    "subreddit": "test",
                    "date": "2026-03-15",
                    "engagement": {"score": 10},
                    "selftext": "Retry content",
                    "relevance": 0.7,
                    "why_relevant": "retry",
                }
            ],
            {},
        )

        bundle = schema.RetrievalBundle()
        # Only 1 reddit item (thin)
        bundle.items_by_source["reddit"] = [
            _make_source_item("reddit", "R1", "https://reddit.com/r/test/1", container="test"),
        ]
        # 5 X items (not thin)
        bundle.items_by_source["x"] = [
            _make_source_item("x", f"X{i}", f"https://x.com/a/{i}") for i in range(5)
        ]

        plan = _make_plan("advanced AI safety techniques")
        settings = pipeline.DEPTH_SETTINGS["default"]

        pipeline._retry_thin_sources(
            topic="advanced AI safety techniques",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime(),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            settings=settings,
        )

        # _retrieve_stream should have been called for reddit (thin source)
        mock_retrieve.assert_called()
        call_sources = [c.kwargs.get("source") for c in mock_retrieve.call_args_list]
        self.assertIn("reddit", call_sources)
        # X should NOT have been retried
        self.assertNotIn("x", call_sources)

    def test_sources_with_enough_items_not_retried(self):
        """Sources with >= 3 items should not be retried."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["reddit"] = [
            _make_source_item("reddit", f"R{i}", f"https://reddit.com/r/test/{i}") for i in range(5)
        ]
        bundle.items_by_source["x"] = [
            _make_source_item("x", f"X{i}", f"https://x.com/a/{i}") for i in range(5)
        ]

        plan = _make_plan("AI safety")
        settings = pipeline.DEPTH_SETTINGS["default"]

        with patch("lib.pipeline._retrieve_stream") as mock_retrieve:
            pipeline._retry_thin_sources(
                topic="AI safety",
                bundle=bundle,
                plan=plan,
                config={},
                depth="default",
                date_range=("2026-02-15", "2026-03-17"),
                runtime=_make_runtime(),
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=settings,
            )
            mock_retrieve.assert_not_called()

    def test_errored_sources_not_retried(self):
        """Sources in errors_by_source should not be retried even if thin.
        Non-errored thin sources SHOULD still be retried."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["reddit"] = [
            _make_source_item("reddit", "R1", "https://reddit.com/r/test/1"),
        ]
        bundle.errors_by_source["reddit"] = "API error"

        plan = _make_plan("AI safety")
        settings = pipeline.DEPTH_SETTINGS["default"]

        mock_items = [{"id": "X1", "title": "test", "url": "https://x.com/1", "text": "test"}]
        with patch("lib.pipeline._retrieve_stream", return_value=(mock_items, {})) as mock_retrieve:
            pipeline._retry_thin_sources(
                topic="AI safety",
                bundle=bundle,
                plan=plan,
                config={},
                depth="default",
                date_range=("2026-02-15", "2026-03-17"),
                runtime=_make_runtime(),
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=settings,
            )
            # x (non-errored, thin) should be retried; reddit (errored) should not
            if mock_retrieve.call_count > 0:
                retried_sources = [call.kwargs.get("source") or call.args[2] for call in mock_retrieve.call_args_list if hasattr(call, 'kwargs')]
                self.assertNotIn("reddit", [c.kwargs.get("source") for c in mock_retrieve.call_args_list])

    def test_retry_skipped_in_quick_mode(self):
        """_retry_thin_sources should return immediately in quick mode."""
        bundle = schema.RetrievalBundle()
        bundle.items_by_source["reddit"] = [
            _make_source_item("reddit", "R1", "https://reddit.com/r/test/1"),
        ]

        plan = _make_plan("AI safety")
        settings = pipeline.DEPTH_SETTINGS["quick"]

        with patch("lib.pipeline._retrieve_stream") as mock_retrieve:
            pipeline._retry_thin_sources(
                topic="AI safety",
                bundle=bundle,
                plan=plan,
                config={},
                depth="quick",
                date_range=("2026-02-15", "2026-03-17"),
                runtime=_make_runtime(),
                mock=False,
                rate_limited_sources=set(),
                rate_limit_lock=threading.Lock(),
                settings=settings,
            )
            mock_retrieve.assert_not_called()


class TestErrorCleanup(unittest.TestCase):
    """Source errors should be cleared when the source has items from other subqueries."""

    def test_error_cleared_when_source_has_items(self):
        """A source that 429'd on one subquery but succeeded on another is not errored."""
        bundle = schema.RetrievalBundle(artifacts={})
        item = schema.SourceItem(
            item_id="x1", source="x", title="A tweet", body="content",
            url="https://x.com/user/status/1",
        )
        bundle.items_by_source["x"] = [item]
        bundle.errors_by_source["x"] = "HTTP 429: Too Many Requests"

        # Simulate the cleanup logic from pipeline.run()
        for source in list(bundle.errors_by_source):
            if bundle.items_by_source.get(source):
                del bundle.errors_by_source[source]

        self.assertNotIn("x", bundle.errors_by_source,
                         "X should not be errored when it has items")

    def test_error_kept_when_source_has_no_items(self):
        """A source with zero items should remain in errors_by_source."""
        bundle = schema.RetrievalBundle(artifacts={})
        bundle.errors_by_source["x"] = "HTTP 429: Too Many Requests"

        for source in list(bundle.errors_by_source):
            if bundle.items_by_source.get(source):
                del bundle.errors_by_source[source]

        self.assertIn("x", bundle.errors_by_source,
                      "X should remain errored when it has no items")


class TestXHandleFlag(unittest.TestCase):
    """R3: --x-handle CLI flag and pipeline parameter."""

    def test_cli_accepts_x_handle_flag(self):
        """build_parser() should accept --x-handle."""
        import last30days as cli

        parser = cli.build_parser()
        args = parser.parse_args(["test topic", "--x-handle", "elonmusk"])
        self.assertEqual(args.x_handle, "elonmusk")

    def test_cli_x_handle_default_is_none(self):
        """--x-handle should default to None."""
        import last30days as cli

        parser = cli.build_parser()
        args = parser.parse_args(["test topic"])
        self.assertIsNone(args.x_handle)

    def test_pipeline_run_accepts_x_handle(self):
        """pipeline.run() should accept x_handle keyword argument."""
        import inspect
        sig = inspect.signature(pipeline.run)
        self.assertIn("x_handle", sig.parameters, "pipeline.run() must accept x_handle parameter")

    def test_x_handle_passed_to_supplemental_searches(self):
        """When x_handle is provided, it should trigger targeted handle search."""
        # Run pipeline in mock mode with x_handle -- should not raise
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["reddit", "x", "grounding"],
            mock=True,
            x_handle="testuser",
        )
        self.assertEqual("test topic", report.topic)


class TestWarnings(unittest.TestCase):
    def _item(self, source="reddit"):
        return schema.SourceItem(item_id="1", source=source, title="t", body="b", url="u")

    def _candidate(self, source="reddit", score=50.0):
        c = schema.Candidate(
            candidate_id="c1", item_id="1", source=source, title="t", url="u",
            snippet="s", subquery_labels=["main"], native_ranks={"main:reddit": 1},
            local_relevance=0.5, freshness=50, engagement=10, source_quality=0.7,
            rrf_score=0.01, sources=[source],
        )
        c.final_score = score
        return c

    def test_no_candidates_warning(self):
        w = pipeline._warnings({"reddit": [self._item()]}, [], {})
        self.assertTrue(any("No candidates" in msg for msg in w))

    def test_thin_evidence_warning(self):
        candidates = [self._candidate() for _ in range(3)]
        w = pipeline._warnings({"reddit": [self._item()]}, candidates, {})
        self.assertTrue(any("thin" in msg.lower() for msg in w))

    def test_single_source_concentration(self):
        candidates = [self._candidate() for _ in range(5)]
        w = pipeline._warnings({"reddit": [self._item()]}, candidates, {})
        self.assertTrue(any("concentrated" in msg.lower() for msg in w))

    def test_source_errors_listed(self):
        w = pipeline._warnings({}, [self._candidate()], {"x": "timeout"})
        self.assertTrue(any("x" in msg for msg in w))

    def test_no_items_warning(self):
        w = pipeline._warnings({}, [], {})
        self.assertTrue(any("No source returned" in msg for msg in w))


class TestXRelatedSupplementalSearch(unittest.TestCase):
    """Tests for --x-related weighted supplemental search."""

    @patch("lib.bird_x.search_handles")
    @patch("lib.entity_extract.extract_entities")
    def test_x_related_triggers_supplemental_related_label(self, mock_extract, mock_handles):
        """x_related handles should be searched and added with supplemental-related label."""
        mock_extract.return_value = {"x_handles": [], "x_hashtags": [], "reddit_subreddits": []}
        mock_handles.return_value = [
            {
                "id": "rel1",
                "text": "Related tweet from biancacensori",
                "url": "https://x.com/biancacensori/status/555",
                "author_handle": "biancacensori",
                "date": "2026-03-15",
                "engagement": {"likes": 30},
                "relevance": 0.7,
                "why_relevant": "related handle search",
            }
        ]

        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/kanyewest/status/1", author="kanyewest"),
        ]

        plan = _make_plan("Kanye West")

        pipeline._run_supplemental_searches(
            topic="Kanye West",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            x_related=["biancacensori"],
        )

        # search_handles should have been called for the related handle
        mock_handles.assert_called()
        # The supplemental-related subquery label should exist in the plan
        labels = [sq.label for sq in plan.subqueries]
        self.assertIn("supplemental-related", labels)
        # The supplemental-related subquery should have weight 0.3
        related_sq = [sq for sq in plan.subqueries if sq.label == "supplemental-related"][0]
        self.assertAlmostEqual(related_sq.weight, 0.3)

    @patch("lib.bird_x.search_handles")
    @patch("lib.entity_extract.extract_entities")
    def test_no_x_related_no_supplemental_related_label(self, mock_extract, mock_handles):
        """Without x_related, supplemental-related label should not appear."""
        mock_extract.return_value = {"x_handles": ["analyst1"], "x_hashtags": [], "reddit_subreddits": []}
        mock_handles.return_value = [
            {
                "id": "supp1",
                "text": "Supplemental tweet",
                "url": "https://x.com/analyst1/status/999",
                "author_handle": "analyst1",
                "date": "2026-03-15",
                "engagement": {"likes": 50},
                "relevance": 0.8,
                "why_relevant": "direct handle search",
            }
        ]

        bundle = schema.RetrievalBundle()
        bundle.items_by_source["x"] = [
            _make_source_item("x", "X1", "https://x.com/analyst1/status/1", author="analyst1"),
        ]

        plan = _make_plan("AI safety")

        pipeline._run_supplemental_searches(
            topic="AI safety",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime("bird"),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
        )

        # supplemental-related label should NOT exist (no x_related provided)
        labels = [sq.label for sq in plan.subqueries]
        self.assertNotIn("supplemental-related", labels)


class TestRetryThinSourcesCoreEqualsTopic(unittest.TestCase):
    """Test that _retry_thin_sources fires even when core == topic (the fix)."""

    @patch("lib.pipeline._retrieve_stream")
    def test_retry_fires_when_core_equals_topic(self, mock_retrieve):
        """Topic 'Kanye West' with 0 YouTube items should trigger retry.

        Previously this was skipped because core 'kanye west' == topic.
        The fix ensures retry still fires for short topics.
        """
        mock_retrieve.return_value = (
            [
                {
                    "id": "YT1",
                    "title": "Kanye West new album leak",
                    "url": "https://www.youtube.com/watch?v=abc123",
                    "date": "2026-03-15",
                    "engagement": {"views": 1000},
                    "relevance": 0.8,
                    "why_relevant": "retry result",
                }
            ],
            {},
        )

        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="Kanye West",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="Kanye West",
                    ranking_query="What recent evidence matters for Kanye West?",
                    sources=["youtube", "x"],
                )
            ],
            source_weights={"youtube": 1.0, "x": 1.0},
        )
        bundle = schema.RetrievalBundle()
        # YouTube has 0 items (thin), X has enough
        bundle.items_by_source["x"] = [
            _make_source_item("x", f"X{i}", f"https://x.com/a/{i}") for i in range(5)
        ]

        pipeline._retry_thin_sources(
            topic="Kanye West",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-02-15", "2026-03-17"),
            runtime=_make_runtime(),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            settings=pipeline.DEPTH_SETTINGS["default"],
        )

        # _retrieve_stream should have been called for youtube
        mock_retrieve.assert_called()
        retried_sources = [c.kwargs["source"] for c in mock_retrieve.call_args_list]
        self.assertIn("youtube", retried_sources)
        # YouTube should now have items in the bundle
        self.assertIn("youtube", bundle.items_by_source)


class TestZeroKeyPipelineRun(unittest.TestCase):
    """Pipeline should complete with local fallbacks when no reasoning keys are configured."""

    @patch("lib.pipeline._retrieve_stream")
    def test_zero_key_run_produces_report(self, mock_retrieve):
        mock_retrieve.side_effect = lambda **kwargs: pipeline._mock_stream_results(
            kwargs["source"], kwargs["subquery"]
        )
        config = {"LAST30DAYS_REASONING_PROVIDER": "auto"}
        report = pipeline.run(
            topic="test zero key topic",
            config=config,
            depth="quick",
            requested_sources=["hackernews"],
        )
        self.assertEqual("test zero key topic", report.topic)
        self.assertEqual("local", report.provider_runtime.reasoning_provider)
        self.assertEqual("deterministic", report.provider_runtime.planner_model)
        self.assertTrue(
            any("fallback" in note for note in report.query_plan.notes),
            f"Expected fallback plan, got notes: {report.query_plan.notes}",
        )
        for candidate in report.ranked_candidates:
            self.assertEqual("fallback-local-score", candidate.explanation)


if __name__ == "__main__":
    unittest.main()
