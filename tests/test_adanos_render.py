import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import render, schema


class AdanosRenderTests(unittest.TestCase):
    def test_render_compact_includes_adanos_source_dump_and_ticker_stats(self):
        item = schema.SourceItem(
            item_id="ADANOS-reddit-TSLA",
            source="adanos",
            title="TSLA Reddit market sentiment",
            body="Tesla retail sentiment snapshot.",
            url="",
            container="Adanos Market Sentiment",
            published_at="2026-03-30",
            date_confidence="med",
            engagement={"buzz_score": 72.0, "mentions": 140, "total_upvotes": 850},
            metadata={"ticker": "TSLA", "platform": "reddit"},
        )
        candidate = schema.Candidate(
            candidate_id="c1",
            item_id=item.item_id,
            source="adanos",
            title=item.title,
            url="",
            snippet=item.body,
            subquery_labels=["primary"],
            native_ranks={"primary:adanos": 1},
            local_relevance=0.9,
            freshness=80,
            engagement=90,
            source_quality=0.82,
            rrf_score=0.02,
            rerank_score=88,
            final_score=88,
            sources=["adanos"],
            source_items=[item],
        )
        report = schema.Report(
            topic="TSLA stock sentiment",
            range_from="2026-03-01",
            range_to="2026-03-30",
            generated_at="2026-03-30T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="mock",
                planner_model="mock",
                rerank_model="mock",
            ),
            query_plan=schema.QueryPlan(
                intent="prediction",
                freshness_mode="strict_recent",
                cluster_mode="market",
                raw_topic="TSLA stock sentiment",
                subqueries=[
                    schema.SubQuery(
                        label="primary",
                        search_query="TSLA stock sentiment",
                        ranking_query="What recent market sentiment matters for TSLA?",
                        sources=["adanos"],
                    )
                ],
                source_weights={"adanos": 1.0},
            ),
            clusters=[
                schema.Cluster(
                    cluster_id="cluster-1",
                    title=item.title,
                    candidate_ids=["c1"],
                    representative_ids=["c1"],
                    sources=["adanos"],
                    score=88,
                )
            ],
            ranked_candidates=[candidate],
            items_by_source={"adanos": [item]},
            errors_by_source={},
        )

        compact = render.render_compact(report)
        self.assertIn("TSLA on reddit", compact)
        self.assertIn("Adanos Market Sentiment: 1 item | 72buzz", compact)
        self.assertIn("tickers: TSLA", compact)

        full = render.render_full(report)
        self.assertIn("### Adanos Market Sentiment (1 items)", full)
        self.assertIn("72.0 buzz_score", full)


if __name__ == "__main__":
    unittest.main()
