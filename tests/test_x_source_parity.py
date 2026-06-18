"""U1: X-shaped source abstraction parity.

Key-based X sources (xquik today; xurl/xai variants) must receive the same
ranking treatment as native ``x`` — engagement weights, source quality, the
social-prune gate, the rerank X-candidate gate / engagement rescue, and a
rendered source section. Native ``x`` behavior must be unchanged.
"""

import math
import unittest

from lib import rerank, render, schema, signals


def _x_item(source: str, engagement: dict) -> schema.SourceItem:
    return schema.SourceItem(
        item_id="i1",
        source=source,
        title="Grok 4 ships",
        body="Grok 4 ships",
        url="https://x.com/a/status/1",
        author="someone",
        engagement=engagement,
    )


class TestIsXSource(unittest.TestCase):
    def test_native_and_key_based_x_are_x(self):
        for slug in ("x", "xquik", "xurl", "xai"):
            self.assertTrue(schema.is_x_source(slug), slug)

    def test_non_x_and_empty_are_not_x(self):
        for slug in ("reddit", "tiktok", "digg", "", None, "twitter"):
            self.assertFalse(schema.is_x_source(slug), repr(slug))


class TestEngagementParity(unittest.TestCase):
    def test_xquik_uses_x_engagement_weights(self):
        eng = {"likes": 1000, "reposts": 50, "replies": 20, "quotes": 5}
        x_score = signals.engagement_raw(_x_item("x", eng))
        xquik_score = signals.engagement_raw(_x_item("xquik", eng))
        self.assertIsNotNone(xquik_score)
        self.assertAlmostEqual(x_score, xquik_score)

    def test_xquik_score_matches_x_weight_formula(self):
        eng = {"likes": 1000, "reposts": 50, "replies": 20, "quotes": 5}
        expected = (
            0.55 * math.log1p(1000)
            + 0.25 * math.log1p(50)
            + 0.15 * math.log1p(20)
            + 0.05 * math.log1p(5)
        )
        self.assertAlmostEqual(expected, signals.engagement_raw(_x_item("xquik", eng)))

    def test_native_x_unchanged_regression(self):
        eng = {"likes": 500, "reposts": 10}
        expected = 0.55 * math.log1p(500) + 0.25 * math.log1p(10)
        self.assertAlmostEqual(expected, signals.engagement_raw(_x_item("x", eng)))

    def test_unknown_source_still_generic(self):
        # A non-X unknown source must NOT pick up the X weights.
        eng = {"likes": 1000, "reposts": 50}
        mystery = _x_item("mystery", eng)
        # generic = mean of positive log1p values
        expected = (math.log1p(1000) + math.log1p(50)) / 2
        self.assertAlmostEqual(expected, signals.engagement_raw(mystery))


class TestSourceQualityParity(unittest.TestCase):
    def test_xquik_inherits_x_quality(self):
        self.assertEqual(signals.source_quality("x"), signals.source_quality("xquik"))

    def test_unknown_source_default(self):
        self.assertEqual(0.6, signals.source_quality("mystery"))


class TestIsXCandidate(unittest.TestCase):
    def _cand(self, source: str, item_source: str | None = None) -> schema.Candidate:
        items = [_x_item(item_source or source, {"likes": 1})]
        return schema.Candidate(
            candidate_id="c", item_id="i1", source=source, title="t",
            url="https://x.com/a/status/1", snippet="t",
            subquery_labels=["primary"], native_ranks={"primary:" + source: 1},
            local_relevance=0.5, freshness=50, engagement=10, source_quality=0.6,
            rrf_score=0.02, source_items=items,
        )

    def test_xquik_and_x_are_x_candidates(self):
        self.assertTrue(rerank._is_x_candidate(self._cand("x")))
        self.assertTrue(rerank._is_x_candidate(self._cand("xquik")))

    def test_xquik_via_source_item(self):
        c = self._cand("reddit", item_source="xquik")
        self.assertTrue(rerank._is_x_candidate(c))

    def test_reddit_is_not_x_candidate(self):
        self.assertFalse(rerank._is_x_candidate(self._cand("reddit", item_source="reddit")))


class TestEngagementRescueIncludesXquik(unittest.TestCase):
    """The X engagement-rescue floor must include xquik items in its X pool."""

    def _xq(self, *, author, text, engagement, final_score, explanation):
        item = schema.SourceItem(
            item_id="x", source="xquik", title=text, body=text,
            url="https://x.com/a/status/1", author=author, snippet=text,
        )
        c = schema.Candidate(
            candidate_id=f"xq-{author}-{engagement}", item_id="x", source="xquik",
            title=text, url=item.url, snippet=text, subquery_labels=["primary"],
            native_ranks={"primary:xquik": 1}, local_relevance=0.5, freshness=50,
            engagement=engagement, source_quality=0.6, rrf_score=0.02, source_items=[item],
        )
        c.final_score = final_score
        c.explanation = explanation
        return c

    def test_top_engagement_first_party_xquik_is_floored(self):
        low = self._xq(author="other", text="Matt Van Horn news", engagement=1,
                       final_score=10, explanation="fallback-local-score")
        mid = self._xq(author="other2", text="Matt Van Horn update", engagement=50,
                       final_score=10, explanation="fallback-local-score")
        top = self._xq(author="mvanhorn", text="quick reply no entity", engagement=100,
                       final_score=3, explanation="fallback-local-score (first-party authorship)")
        rerank._apply_engagement_rescue(
            [low, mid, top], primary_entity="Matt Van Horn", resolved_handles={"mvanhorn"}
        )
        self.assertGreaterEqual(top.final_score, rerank.RESCUE_FLOOR_MAX - 0.001)


class TestRenderXquikSection(unittest.TestCase):
    def test_full_dump_emits_xquik_section(self):
        item = schema.SourceItem(
            item_id="xq1", source="xquik", title="Grok 4 ships", body="Grok 4 ships",
            url="https://x.com/grok/status/1", author="grok",
            engagement={"likes": 100},
        )
        report = schema.Report(
            topic="Grok 4", range_from="2026-05-19", range_to="2026-06-18",
            generated_at="2026-06-18T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini", planner_model="m", rerank_model="m",
            ),
            query_plan=schema.QueryPlan(
                intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
                raw_topic="Grok 4",
                subqueries=[schema.SubQuery(label="primary", search_query="Grok 4",
                                            ranking_query="What is new with Grok 4?", sources=["xquik"])],
                source_weights={"xquik": 1.0},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source={"xquik": [item]}, errors_by_source={},
        )
        text = render.render_full(report)
        self.assertIn("### Xquik (1 items)", text)


if __name__ == "__main__":
    unittest.main()
