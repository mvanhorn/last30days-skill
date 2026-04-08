import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import schema


class SchemaV3Tests(unittest.TestCase):
    def test_report_roundtrip(self):
        report = schema.Report(
            topic="test topic",
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite-preview",
                rerank_model="gemini-3.1-flash-lite-preview",
            ),
            query_plan=schema.QueryPlan(
                intent="breaking_news",
                freshness_mode="strict_recent",
                cluster_mode="story",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(label="primary", search_query="test topic", ranking_query="What happened with test topic?", sources=["grounding"])],
                source_weights={"grounding": 1.0},
            ),
            clusters=[schema.Cluster(cluster_id="cluster-1", title="Title", candidate_ids=["c1"], representative_ids=["c1"], sources=["grounding"], score=90)],
            ranked_candidates=[schema.Candidate(
                candidate_id="c1",
                item_id="i1",
                source="grounding",
                sources=["grounding", "reddit"],
                title="Title",
                url="https://example.com",
                snippet="Snippet",
                subquery_labels=["primary"],
                native_ranks={"primary:grounding": 1},
                local_relevance=0.8,
                freshness=90,
                engagement=None,
                source_quality=1.0,
                rrf_score=0.02,
                rerank_score=91,
                final_score=90,
                source_items=[
                    schema.SourceItem(item_id="i1", source="grounding", title="Title", body="Body", url="https://example.com", published_at="2026-03-16")
                ],
            )],
            items_by_source={"grounding": [schema.SourceItem(item_id="i1", source="grounding", title="Title", body="Body", url="https://example.com")]},
            errors_by_source={},
            warnings=["warning"],
            artifacts={"grounding": []},
        )
        restored = schema.report_from_dict(schema.to_dict(report))
        self.assertEqual(report.topic, restored.topic)
        self.assertEqual(report.provider_runtime.planner_model, restored.provider_runtime.planner_model)
        self.assertEqual(report.ranked_candidates[0].candidate_id, restored.ranked_candidates[0].candidate_id)
        self.assertEqual(report.ranked_candidates[0].sources, restored.ranked_candidates[0].sources)
        self.assertEqual(report.items_by_source["grounding"][0].title, restored.items_by_source["grounding"][0].title)

    def test_source_item_from_dict_preserves_zero_valued_signals(self):
        item = schema.source_item_from_dict(
            {
                "item_id": "x1",
                "source": "x",
                "title": "Title",
                "body": "Body",
                "url": "https://example.com",
                "relevance_hint": 0.0,
                "local_relevance": 0.0,
                "freshness": 0,
                "engagement_score": 0,
                "source_quality": 0.0,
                "local_rank_score": 0.0,
            }
        )
        self.assertEqual(0.0, item.relevance_hint)
        self.assertEqual(0.0, item.local_relevance)
        self.assertEqual(0, item.freshness)
        self.assertEqual(0, item.engagement_score)
        self.assertEqual(0.0, item.source_quality)
        self.assertEqual(0.0, item.local_rank_score)


if __name__ == "__main__":
    unittest.main()
