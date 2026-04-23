# ruff: noqa: E402
"""Tests for render.render_comparison_multi and emit_comparison_output."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import last30days as cli
from lib import render, schema


def _build_report(topic: str, cluster_titles: list[str]) -> schema.Report:
    query_plan = schema.QueryPlan(
        intent="comparison",
        freshness_mode="balanced_recent",
        cluster_mode="debate",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=topic,
                sources=["grounding"],
            )
        ],
        source_weights={"grounding": 1.0},
    )
    clusters: list[schema.Cluster] = []
    candidates: list[schema.Candidate] = []
    for idx, title in enumerate(cluster_titles):
        candidate_id = f"{topic.lower().replace(' ', '-')}-c{idx}"
        item = schema.SourceItem(
            source="grounding",
            item_id=f"g-{candidate_id}",
            title=f"{title} evidence",
            body=f"Body for {title}",
            url=f"https://example.test/{candidate_id}",
            snippet=f"Snippet for {title}",
            published_at="2026-04-20",
        )
        candidate = schema.Candidate(
            candidate_id=candidate_id,
            item_id=item.item_id,
            source="grounding",
            title=item.title,
            url=item.url,
            snippet=item.snippet,
            subquery_labels=["primary"],
            native_ranks={"grounding": idx + 1},
            local_relevance=0.8 - idx * 0.1,
            freshness=5,
            engagement=10,
            source_quality=0.9,
            rrf_score=0.6 - idx * 0.05,
            sources=["grounding"],
            source_items=[item],
            final_score=80.0 - idx * 5,
        )
        candidates.append(candidate)
        clusters.append(
            schema.Cluster(
                cluster_id=f"cl-{idx}",
                title=title,
                candidate_ids=[candidate_id],
                representative_ids=[candidate_id],
                score=80.0 - idx * 5,
                sources=["grounding"],
            )
        )
    return schema.Report(
        topic=topic,
        range_from="2026-03-23",
        range_to="2026-04-22",
        generated_at="2026-04-22T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="mock",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=query_plan,
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source={"grounding": [c.source_items[0] for c in candidates]},
        errors_by_source={},
    )


class RenderComparisonMultiTests(unittest.TestCase):
    def test_three_entity_table(self):
        reports = [
            ("OpenAI", _build_report("OpenAI", ["GPT-5 drop", "API pricing cut"])),
            ("Anthropic", _build_report("Anthropic", ["Claude 4.7 ship", "MCP rollout"])),
            ("xAI", _build_report("xAI", ["Grok 4 release", "Memphis cluster"])),
        ]
        rendered = render.render_comparison_multi(reports)
        # All three entities appear in the header
        self.assertIn("OpenAI vs Anthropic vs xAI", rendered)
        # Each entity has its own evidence section
        self.assertIn("## OpenAI", rendered)
        self.assertIn("## Anthropic", rendered)
        self.assertIn("## xAI", rendered)
        # Scaffold table header has a column per entity
        self.assertIn("| Dimension | OpenAI | Anthropic | xAI |", rendered)
        # Envelope scaffolding present
        self.assertIn("EVIDENCE FOR SYNTHESIS", rendered)
        self.assertIn("END OF last30days CANONICAL OUTPUT", rendered)

    def test_two_entity_table_has_two_columns(self):
        reports = [
            ("Kanye West", _build_report("Kanye West", ["Donda 2 release"])),
            ("Drake", _build_report("Drake", ["For All The Dogs"])),
        ]
        rendered = render.render_comparison_multi(reports)
        self.assertIn("| Dimension | Kanye West | Drake |", rendered)
        self.assertIn("## Kanye West", rendered)
        self.assertIn("## Drake", rendered)

    def test_empty_clusters_renders_placeholder(self):
        reports = [
            ("OpenAI", _build_report("OpenAI", ["GPT-5 drop"])),
            ("ObscureCompetitor", _build_report("ObscureCompetitor", [])),
        ]
        rendered = render.render_comparison_multi(reports)
        self.assertIn("## ObscureCompetitor", rendered)
        self.assertIn("no significant discussion this month", rendered)
        # Main still has its cluster
        self.assertIn("GPT-5 drop", rendered)

    def test_warnings_aggregated_and_labeled(self):
        report_a = _build_report("OpenAI", ["GPT-5 drop"])
        report_b = _build_report("Anthropic", ["Claude 4.7"])
        report_a.warnings.append("Brave quota exhausted")
        report_b.warnings.append("Exa returned 0 results")
        rendered = render.render_comparison_multi(
            [("OpenAI", report_a), ("Anthropic", report_b)]
        )
        self.assertIn("[OpenAI] Brave quota exhausted", rendered)
        self.assertIn("[Anthropic] Exa returned 0 results", rendered)

    def test_raises_on_empty_input(self):
        with self.assertRaises(ValueError):
            render.render_comparison_multi([])

    def test_context_emit(self):
        reports = [
            ("OpenAI", _build_report("OpenAI", ["GPT-5 drop"])),
            ("Anthropic", _build_report("Anthropic", ["Claude 4.7"])),
        ]
        out = render.render_comparison_multi_context(reports)
        self.assertIn("Comparison: OpenAI vs Anthropic", out)
        self.assertIn("## OpenAI", out)
        self.assertIn("## Anthropic", out)
        self.assertIn("GPT-5 drop", out)


class EmitComparisonOutputTests(unittest.TestCase):
    def test_json_emit_nests_per_entity(self):
        reports = [
            ("OpenAI", _build_report("OpenAI", ["GPT-5 drop"])),
            ("Anthropic", _build_report("Anthropic", ["Claude 4.7"])),
        ]
        out = cli.emit_comparison_output(reports, emit="json")
        payload = json.loads(out)
        self.assertTrue(payload["comparison"])
        self.assertEqual(payload["entities"], ["OpenAI", "Anthropic"])
        self.assertEqual(len(payload["reports"]), 2)
        self.assertEqual(payload["reports"][0]["entity"], "OpenAI")
        self.assertIn("topic", payload["reports"][0]["report"])

    def test_compact_and_md_both_route_to_multi(self):
        reports = [
            ("A", _build_report("A", ["Thing A"])),
            ("B", _build_report("B", ["Thing B"])),
        ]
        compact = cli.emit_comparison_output(reports, emit="compact")
        md = cli.emit_comparison_output(reports, emit="md")
        self.assertIn("| Dimension | A | B |", compact)
        self.assertEqual(compact, md)

    def test_context_emit_goes_to_context_renderer(self):
        reports = [
            ("A", _build_report("A", ["Thing A"])),
            ("B", _build_report("B", ["Thing B"])),
        ]
        out = cli.emit_comparison_output(reports, emit="context")
        self.assertIn("Comparison: A vs B", out)

    def test_unsupported_emit_raises(self):
        reports = [("A", _build_report("A", ["Thing A"]))]
        with self.assertRaises(SystemExit):
            cli.emit_comparison_output(reports, emit="xml")


if __name__ == "__main__":
    unittest.main()
