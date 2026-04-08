import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import rerank, schema


def make_candidate(relevance: float) -> schema.Candidate:
    candidate = schema.Candidate(
        candidate_id=f"c-{relevance}",
        item_id="i1",
        source="reddit",
        title="Title",
        url="https://example.com",
        snippet="Snippet",
        subquery_labels=["primary"],
        native_ranks={"primary:reddit": 1},
        local_relevance=0.8,
        freshness=80,
        engagement=50,
        source_quality=0.7,
        rrf_score=0.02,
    )
    candidate.rerank_score = relevance
    return candidate


def make_plan() -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="comparison",
        freshness_mode="balanced_recent",
        cluster_mode="debate",
        raw_topic="openclaw vs nanoclaw",
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query="openclaw vs nanoclaw",
                ranking_query="How does openclaw compare to nanoclaw?",
                sources=["grounding", "reddit"],
            )
        ],
        source_weights={"grounding": 1.0, "reddit": 0.8},
    )


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload

    def generate_json(self, model, prompt):
        self.model = model
        self.prompt = prompt
        return self.payload


class RerankV3Tests(unittest.TestCase):
    def test_low_rerank_score_is_demoted(self):
        low = make_candidate(4.0)
        high = make_candidate(40.0)
        low_score = rerank._final_score(low)
        high_score = rerank._final_score(high)
        self.assertLess(low_score, high_score)
        self.assertLess(low_score, 20.0)

    def test_engagement_boosts_score(self):
        """Items with engagement score higher than those without."""
        candidate = make_candidate(80.0)
        candidate.engagement = None
        score_without = rerank._final_score(candidate)
        candidate.engagement = 50
        score_with = rerank._final_score(candidate)
        self.assertGreater(score_with, score_without)
        # Boost is modest, not dominant
        self.assertLess(score_with - score_without, 10.0)

    def test_build_prompt_includes_source_labels_and_dates(self):
        candidate = make_candidate(80.0)
        candidate.sources = ["grounding", "reddit"]
        candidate.source_items = [
            schema.SourceItem(
                item_id="i1",
                source="grounding",
                title="Title",
                body="Body",
                url="https://example.com",
                published_at="2026-03-16",
            )
        ]
        prompt = rerank._build_prompt("topic", make_plan(), [candidate])
        self.assertIn("sources: grounding, reddit", prompt)
        self.assertIn("date: 2026-03-16", prompt)
        self.assertIn("How does openclaw compare to nanoclaw?", prompt)

    def test_apply_llm_scores_ignores_invalid_rows_and_clamps_scores(self):
        candidate = make_candidate(0.0)
        rerank._apply_llm_scores(
            [candidate],
            {
                "scores": [
                    "bad-row",
                    {"candidate_id": "", "relevance": 99},
                    {"candidate_id": candidate.candidate_id, "relevance": 101, "reason": "  best hit  "},
                ]
            },
        )
        self.assertEqual(100.0, candidate.rerank_score)
        self.assertEqual("best hit", candidate.explanation)
        self.assertGreater(candidate.final_score, 0.0)

    def test_build_prompt_includes_comparison_intent_hint(self):
        plan = make_plan()  # intent="comparison"
        candidate = make_candidate(80.0)
        prompt = rerank._build_prompt("openclaw vs nanoclaw", plan, [candidate])
        self.assertIn("Intent-specific guidance (comparison)", prompt)
        self.assertIn("head-to-head", prompt.lower())

    def test_build_prompt_includes_factual_intent_hint(self):
        plan = make_plan()
        plan.intent = "factual"
        candidate = make_candidate(80.0)
        prompt = rerank._build_prompt("latest GDP numbers", plan, [candidate])
        self.assertTrue(
            "facts" in prompt.lower() or "primary sources" in prompt.lower(),
            "factual intent hint should mention facts or primary sources",
        )

    def test_build_prompt_no_hint_for_unknown_intent(self):
        plan = make_plan()
        plan.intent = "unknown_intent_xyz"
        candidate = make_candidate(80.0)
        prompt = rerank._build_prompt("some topic", plan, [candidate])
        self.assertNotIn("Intent-specific guidance", prompt)

    def test_rerank_candidates_uses_provider_for_shortlist_and_fallback_for_tail(self):
        first = make_candidate(0.0)
        second = make_candidate(0.0)
        second.candidate_id = "tail"
        provider = FakeProvider(
            {"scores": [{"candidate_id": first.candidate_id, "relevance": 95, "reason": "high fit"}]}
        )
        ranked = rerank.rerank_candidates(
            topic="openclaw vs nanoclaw",
            plan=make_plan(),
            candidates=[first, second],
            provider=provider,
            model="gemini-3.1-flash-lite-preview",
            shortlist_size=1,
        )
        self.assertEqual("gemini-3.1-flash-lite-preview", provider.model)
        self.assertEqual(95.0, first.rerank_score)
        self.assertEqual("high fit", first.explanation)
        self.assertEqual("fallback-local-score", second.explanation)
        self.assertEqual(first.candidate_id, ranked[0].candidate_id)


if __name__ == "__main__":
    unittest.main()
