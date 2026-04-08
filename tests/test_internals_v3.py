"""Unit tests for untested internal functions across rerank, render, planner, and signals.

These pin the correct behavior of core building blocks that higher-level
tests exercise transitively but don't assert on directly. A regression in
any of these functions would silently degrade output quality.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import planner, rerank, render, signals, schema


def _item(source: str = "reddit", **kwargs) -> schema.SourceItem:
    defaults = dict(
        item_id="t1", source=source, title="Test Title", body="Test body",
        url="https://example.com", engagement={}, metadata={},
    )
    defaults.update(kwargs)
    return schema.SourceItem(**defaults)


def _candidate(source: str = "reddit", **kwargs) -> schema.Candidate:
    defaults = dict(
        candidate_id="c1", item_id="t1", source=source, title="Test",
        url="https://example.com", snippet="snippet", subquery_labels=["primary"],
        native_ranks={"primary": 1}, local_relevance=0.5, freshness=50,
        engagement=50, source_quality=0.7, rrf_score=0.01, sources=[source],
        source_items=[],
    )
    defaults.update(kwargs)
    return schema.Candidate(**defaults)


# ---------------------------------------------------------------------------
# rerank._fallback_tuple
# ---------------------------------------------------------------------------

class TestFallbackTuple(unittest.TestCase):

    def test_returns_score_and_explanation(self):
        c = _candidate(local_relevance=0.8, freshness=80, source_quality=0.7)
        score, explanation = rerank._fallback_tuple(c)
        self.assertIsInstance(score, float)
        self.assertEqual(explanation, "fallback-local-score")

    def test_score_clamped_to_0_100(self):
        c = _candidate(local_relevance=2.0, freshness=200, source_quality=2.0)
        score, _ = rerank._fallback_tuple(c)
        self.assertLessEqual(score, 100.0)
        self.assertGreaterEqual(score, 0.0)

    def test_higher_relevance_gives_higher_score(self):
        high = _candidate(local_relevance=0.9, freshness=50, source_quality=0.7)
        low = _candidate(local_relevance=0.1, freshness=50, source_quality=0.7)
        self.assertGreater(rerank._fallback_tuple(high)[0], rerank._fallback_tuple(low)[0])


# ---------------------------------------------------------------------------
# rerank._normalized_rrf
# ---------------------------------------------------------------------------

class TestNormalizedRrf(unittest.TestCase):

    def test_zero_input(self):
        self.assertAlmostEqual(rerank._normalized_rrf(0.0), 0.0)

    def test_positive_input(self):
        result = rerank._normalized_rrf(0.04)
        self.assertGreater(result, 0.0)
        self.assertLessEqual(result, 100.0)

    def test_clamped_at_100(self):
        result = rerank._normalized_rrf(1.0)
        self.assertLessEqual(result, 100.0)


# ---------------------------------------------------------------------------
# render._assess_data_freshness
# ---------------------------------------------------------------------------

class TestAssessDataFreshness(unittest.TestCase):

    def _report(self, items_by_source: dict) -> schema.Report:
        return schema.Report(
            topic="test", range_from="2026-02-15", range_to="2026-03-17",
            generated_at="2026-03-17T00:00:00Z",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="test", planner_model="test", rerank_model="test",
            ),
            query_plan=schema.QueryPlan(
                intent="comparison", freshness_mode="balanced_recent",
                cluster_mode="debate", raw_topic="test", subqueries=[],
                source_weights={},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source=items_by_source, errors_by_source={},
        )

    def test_no_items_returns_warning(self):
        report = self._report({})
        result = render._assess_data_freshness(report)
        self.assertIsNotNone(result)
        self.assertIn("Limited", result)

    def test_all_old_items_returns_warning(self):
        items = [_item(published_at="2026-01-01") for _ in range(10)]
        report = self._report({"reddit": items})
        result = render._assess_data_freshness(report)
        self.assertIsNotNone(result)

    def test_many_recent_items_returns_none(self):
        from datetime import date
        today = date.today().isoformat()
        items = [_item(published_at=today) for _ in range(10)]
        report = self._report({"reddit": items})
        result = render._assess_data_freshness(report)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# render._format_date
# ---------------------------------------------------------------------------

class TestFormatDate(unittest.TestCase):

    def test_high_confidence_clean(self):
        item = _item(published_at="2026-03-10", date_confidence="high")
        self.assertEqual(render._format_date(item), "2026-03-10")

    def test_low_confidence_tagged(self):
        item = _item(published_at="2026-03-10", date_confidence="low")
        self.assertIn("date:low", render._format_date(item))

    def test_none_item(self):
        self.assertIn("unknown", render._format_date(None).lower())


# ---------------------------------------------------------------------------
# render._format_actor
# ---------------------------------------------------------------------------

class TestFormatActor(unittest.TestCase):

    def test_reddit_subreddit(self):
        item = _item(source="reddit", container="python")
        self.assertEqual(render._format_actor(item), "r/python")

    def test_x_handle(self):
        item = _item(source="x", author="karpathy")
        self.assertEqual(render._format_actor(item), "@karpathy")

    def test_youtube_channel(self):
        item = _item(source="youtube", author="Fireship")
        self.assertEqual(render._format_actor(item), "Fireship")


# ---------------------------------------------------------------------------
# render._format_engagement
# ---------------------------------------------------------------------------

class TestFormatEngagement(unittest.TestCase):

    def test_reddit_format(self):
        item = _item(engagement={"score": 344, "num_comments": 119})
        result = render._format_engagement(item)
        self.assertIn("344", result)
        self.assertIn("pts", result)

    def test_empty_engagement(self):
        item = _item(engagement={})
        self.assertIsNone(render._format_engagement(item))


# ---------------------------------------------------------------------------
# render._format_corroboration
# ---------------------------------------------------------------------------

class TestFormatCorroboration(unittest.TestCase):

    def test_multi_source(self):
        c = _candidate(sources=["reddit", "x", "hackernews"])
        result = render._format_corroboration(c)
        self.assertIn("Also on", result)
        self.assertIn("X", result)

    def test_single_source_none(self):
        c = _candidate(sources=["reddit"])
        self.assertIsNone(render._format_corroboration(c))


# ---------------------------------------------------------------------------
# render._format_explanation
# ---------------------------------------------------------------------------

class TestFormatExplanation(unittest.TestCase):

    def test_hides_fallback_sentinel(self):
        c = _candidate(explanation="fallback-local-score")
        self.assertIsNone(render._format_explanation(c))

    def test_shows_real_explanation(self):
        c = _candidate(explanation="Directly compares frameworks")
        self.assertEqual(render._format_explanation(c), "Directly compares frameworks")


# ---------------------------------------------------------------------------
# render._fmt_pairs and _format_number
# ---------------------------------------------------------------------------

class TestFmtPairs(unittest.TestCase):

    def test_basic(self):
        self.assertEqual(render._fmt_pairs([(120, "pts"), (48, "cmt")]), "120pts, 48cmt")

    def test_skips_none_and_zero(self):
        self.assertEqual(render._fmt_pairs([(None, "pts"), (0, "cmt"), (5, "re")]), "5re")

    def test_large_numbers(self):
        self.assertIn("94,200", render._fmt_pairs([(94200, "views")]))


class TestFormatNumber(unittest.TestCase):

    def test_comma_thousands(self):
        self.assertEqual(render._format_number(94200), "94,200")

    def test_small_integer(self):
        self.assertEqual(render._format_number(42), "42")


# ---------------------------------------------------------------------------
# render._truncate
# ---------------------------------------------------------------------------

class TestTruncate(unittest.TestCase):

    def test_short_text(self):
        self.assertEqual(render._truncate("hello", 100), "hello")

    def test_long_text_has_ellipsis(self):
        result = render._truncate("a" * 200, 50)
        self.assertTrue(result.endswith("..."))
        self.assertEqual(len(result), 50)


# ---------------------------------------------------------------------------
# planner._normalize_subquery_weights
# ---------------------------------------------------------------------------

class TestNormalizeSubqueryWeights(unittest.TestCase):

    def test_sums_to_one(self):
        sqs = [
            schema.SubQuery(label="a", search_query="a", ranking_query="a?", sources=["r"], weight=3.0),
            schema.SubQuery(label="b", search_query="b", ranking_query="b?", sources=["r"], weight=1.0),
        ]
        normed = planner._normalize_subquery_weights(sqs)
        total = sum(sq.weight for sq in normed)
        self.assertAlmostEqual(total, 1.0)

    def test_preserves_ratio(self):
        sqs = [
            schema.SubQuery(label="a", search_query="a", ranking_query="a?", sources=["r"], weight=4.0),
            schema.SubQuery(label="b", search_query="b", ranking_query="b?", sources=["r"], weight=1.0),
        ]
        normed = planner._normalize_subquery_weights(sqs)
        self.assertAlmostEqual(normed[0].weight / normed[1].weight, 4.0)


# ---------------------------------------------------------------------------
# planner._normalize_weights
# ---------------------------------------------------------------------------

class TestNormalizeWeights(unittest.TestCase):

    def test_sums_to_one(self):
        result = planner._normalize_weights({"a": 3.0, "b": 1.0})
        self.assertAlmostEqual(sum(result.values()), 1.0)

    def test_negative_clamped_to_zero(self):
        result = planner._normalize_weights({"a": 2.0, "b": -1.0})
        self.assertAlmostEqual(result["b"], 0.0)


# ---------------------------------------------------------------------------
# planner._trim_subqueries_for_depth
# ---------------------------------------------------------------------------

class TestTrimSubqueriesForDepth(unittest.TestCase):

    def _sq(self, label: str = "primary", sources: list[str] = None) -> schema.SubQuery:
        return schema.SubQuery(
            label=label, search_query="test", ranking_query="test?",
            sources=sources or ["reddit", "x", "grounding", "youtube", "hackernews", "polymarket"],
            weight=1.0,
        )

    def test_quick_limits_sources(self):
        sqs = [self._sq()]
        result = planner._trim_subqueries_for_depth(sqs, "comparison", "quick", ["reddit", "x", "grounding"])
        self.assertLessEqual(len(result[0].sources), 2)

    def test_default_comparison_expands_via_capabilities(self):
        available = ["reddit", "x", "grounding", "youtube", "hackernews", "tiktok", "instagram"]
        sqs = [self._sq(sources=available)]
        result = planner._trim_subqueries_for_depth(sqs, "comparison", "default", available)
        # Comparison should use all capability-matched sources, not top-3
        self.assertGreater(len(result[0].sources), 3)

    def test_deep_expands_via_capabilities(self):
        available = ["reddit", "x", "youtube", "hackernews", "polymarket"]
        sqs = [self._sq(sources=available)]
        result = planner._trim_subqueries_for_depth(sqs, "comparison", "deep", available)
        # Deep comparison should also use capability expansion, not trim
        self.assertGreaterEqual(len(result[0].sources), 4)


# ---------------------------------------------------------------------------
# signals.annotate_stream
# ---------------------------------------------------------------------------

class TestAnnotateStream(unittest.TestCase):

    def test_attaches_metadata(self):
        items = [
            _item(engagement={"score": 100, "num_comments": 50, "upvote_ratio": 0.9}),
        ]
        annotated = signals.annotate_stream(items, "test query", "balanced_recent")
        item = annotated[0]
        self.assertIsNotNone(item.local_relevance)
        self.assertIsNotNone(item.freshness)
        self.assertIsNotNone(item.engagement_score)
        self.assertIsNotNone(item.source_quality)
        self.assertIsNotNone(item.local_rank_score)

    def test_sorted_by_local_rank_score(self):
        items = [
            _item(item_id="low", title="irrelevant stuff", engagement={}),
            _item(item_id="high", title="test query exact match test query", engagement={"score": 500, "num_comments": 200}),
        ]
        annotated = signals.annotate_stream(items, "test query", "balanced_recent")
        self.assertEqual(annotated[0].item_id, "high")


# ---------------------------------------------------------------------------
# signals.prune_low_relevance
# ---------------------------------------------------------------------------

class TestPruneLowRelevance(unittest.TestCase):

    def test_removes_low_relevance_items(self):
        items = [
            _item(item_id="good"),
            _item(item_id="bad"),
        ]
        items[0].local_relevance = 0.8
        items[1].local_relevance = 0.01
        result = signals.prune_low_relevance(items, minimum=0.1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].item_id, "good")

    def test_keeps_all_if_all_below_minimum(self):
        items = [_item(item_id="only")]
        items[0].local_relevance = 0.05
        result = signals.prune_low_relevance(items, minimum=0.1)
        self.assertEqual(len(result), 1)  # fallback keeps all


# ---------------------------------------------------------------------------
# Bug fixes found by PR review agents
# ---------------------------------------------------------------------------

class TestDaysAgoZeroFalsy(unittest.TestCase):
    """render._assess_data_freshness must not treat days_ago=0 as falsy."""

    def _report_with_items(self, dates_list: list[str]) -> schema.Report:
        items = [_item(published_at=d) for d in dates_list]
        return schema.Report(
            topic="test", range_from="2026-02-15", range_to="2026-03-17",
            generated_at="2026-03-17T00:00:00Z",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="test", planner_model="test", rerank_model="test",
            ),
            query_plan=schema.QueryPlan(
                intent="comparison", freshness_mode="balanced_recent",
                cluster_mode="debate", raw_topic="test", subqueries=[],
                source_weights={},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source={"reddit": items}, errors_by_source={},
        )

    def test_items_from_today_count_as_recent(self):
        from datetime import date
        today = date.today().isoformat()
        report = self._report_with_items([today] * 5)
        warning = render._assess_data_freshness(report)
        self.assertIsNone(warning, f"Items from today should be recent, got warning: {warning}")


class TestRerankBoundary(unittest.TestCase):
    """Rerank demotion must have a clean boundary at exactly 20.0."""

    def test_score_at_exactly_20_is_not_demoted(self):
        c = _candidate()
        c.rerank_score = 20.0
        score_at_20 = rerank._final_score(c)
        c.rerank_score = 50.0
        score_at_50 = rerank._final_score(c)
        self.assertGreater(score_at_20 / score_at_50, 0.3,
                           "Score at 20.0 should not be demoted")

    def test_score_at_19_99_is_demoted(self):
        c = _candidate()
        c.rerank_score = 19.99
        score_demoted = rerank._final_score(c)
        c.rerank_score = 20.0
        score_not_demoted = rerank._final_score(c)
        self.assertLess(score_demoted, score_not_demoted * 0.5,
                        "Score at 19.99 should be heavily demoted vs 20.0")


class TestSlashFalsePositives(unittest.TestCase):
    """Slash regex must not misclassify compound terms as comparisons."""

    def test_ci_cd_is_not_comparison(self):
        self.assertNotEqual(planner._infer_intent("CI/CD pipeline setup"), "comparison")

    def test_tcp_ip_is_not_comparison(self):
        self.assertNotEqual(planner._infer_intent("TCP/IP networking guide"), "comparison")

    def test_io_is_not_comparison(self):
        self.assertNotEqual(planner._infer_intent("I/O performance tuning"), "comparison")

    def test_os_kernel_is_not_comparison(self):
        self.assertNotEqual(planner._infer_intent("input/output buffering"), "comparison")

    def test_proper_noun_slash_still_works(self):
        self.assertEqual(planner._infer_intent("React/Vue/Svelte"), "comparison")


class TestGenericEngagementFormatter(unittest.TestCase):
    """Generic formatter must not garble output for unknown sources."""

    def test_xiaohongshu_engagement_not_garbled(self):
        item = _item(source="xiaohongshu", engagement={"likes": 500, "views": 10000})
        result = render._format_engagement(item)
        if result is not None:
            self.assertNotIn("likes500", result, "Key used as value prefix")
            self.assertNotIn("views10000", result, "Key used as value prefix")
            # Should contain numeric values, not dict keys as numbers
            self.assertIn("500", result)


if __name__ == "__main__":
    unittest.main()


class TestDefaultDepthDoesNotCapSources(unittest.TestCase):
    """Default depth must not aggressively limit sources for any intent.

    E2E testing showed factual/opinion/prediction/concept queries getting
    0-1 sources because SOURCE_LIMITS["default"] capped them at 2-3,
    and those 2-3 sources returned empty. v2.9.5 searched all available
    sources and let scoring handle quality.
    """

    ALL_SOURCES = ["reddit", "x", "grounding", "youtube", "hackernews",
                   "tiktok", "instagram", "polymarket"]

    def _plan_sources(self, topic: str) -> list[str]:
        plan = planner.plan_query(
            topic=topic,
            available_sources=self.ALL_SOURCES,
            requested_sources=None,
            depth="default",
            provider=None,
            model=None,
        )
        return plan.subqueries[0].sources

    def test_factual_gets_more_than_2_sources(self):
        sources = self._plan_sources("what is quantum computing")
        self.assertGreater(len(sources), 2,
                           f"Factual query capped at {len(sources)} sources: {sources}")

    def test_opinion_gets_more_than_3_sources(self):
        sources = self._plan_sources("thoughts on Rust")
        self.assertGreater(len(sources), 3,
                           f"Opinion query capped at {len(sources)} sources: {sources}")

    def test_prediction_gets_more_than_3_sources(self):
        sources = self._plan_sources("odds of recession")
        self.assertGreater(len(sources), 3,
                           f"Prediction query capped at {len(sources)} sources: {sources}")

    def test_breaking_news_gets_more_than_4_sources(self):
        sources = self._plan_sources("kanye west")
        self.assertGreater(len(sources), 4,
                           f"Breaking news capped at {len(sources)} sources: {sources}")

    def test_concept_gets_more_than_3_sources(self):
        sources = self._plan_sources("explain transformer architecture")
        self.assertGreater(len(sources), 3,
                           f"Concept query capped at {len(sources)} sources: {sources}")

    def test_quick_mode_still_limited(self):
        """Quick mode should remain tight for latency."""
        plan = planner.plan_query(
            topic="what is quantum computing",
            available_sources=self.ALL_SOURCES,
            requested_sources=None,
            depth="quick",
            provider=None,
            model=None,
        )
        self.assertLessEqual(len(plan.subqueries[0].sources), 3)



class TestRerankWeightBalance(unittest.TestCase):
    """Reranker weight must dominate over RRF when candidates have divergent quality."""

    def test_rerank_gap_dominates_with_identical_rrf(self):
        """Two candidates with identical RRF but rerank_scores of 80 and 40 should have a meaningful final_score gap (rerank still dominates)."""
        high = _candidate(rrf_score=0.03, freshness=50, source_quality=0.7)
        high.rerank_score = 80.0
        high.final_score = rerank._final_score(high)

        low = _candidate(rrf_score=0.03, freshness=50, source_quality=0.7)
        low.rerank_score = 40.0
        low.final_score = rerank._final_score(low)

        gap = high.final_score - low.final_score
        # Rerank weight is 0.60, so gap = 0.60 * 40 = 24 points.
        # Engagement boost may add a small delta but rerank remains dominant.
        self.assertGreaterEqual(gap, 23.0,
                                f"Rerank gap should be >= 23 points, got {gap:.1f}")


class TestXaiModelDefault(unittest.TestCase):
    """XAI_DEFAULT must be a model that xAI's API actually accepts."""

    def test_default_is_not_grok_3(self):
        from lib import providers
        self.assertNotEqual(providers.XAI_DEFAULT, "grok-3-fast",
                            "grok-3-fast returns HTTP 400 from xAI API")
        self.assertNotEqual(providers.XAI_DEFAULT, "grok-3-mini-fast",
                            "grok-3-mini-fast returns HTTP 400 from xAI API")

    def test_default_is_grok_4_generation(self):
        from lib import providers
        self.assertIn("grok-4", providers.XAI_DEFAULT,
                      f"XAI_DEFAULT should be a grok-4 model, got: {providers.XAI_DEFAULT}")

