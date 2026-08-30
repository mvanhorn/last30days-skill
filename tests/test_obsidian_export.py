"""Tests for the additive Obsidian vault export path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import last30days as cli
from lib import obsidian_export, schema


def make_report(topic: str = "OpenClaw vs NanoClaw") -> schema.Report:
    item = schema.SourceItem(
        item_id="i1",
        source="reddit",
        title="OpenClaw threads explode",
        body="Users compare OpenClaw and NanoClaw on latency.",
        url="https://reddit.com/r/LocalLLaMA/1",
        author="alice",
        published_at="2026-03-10T00:00:00Z",
        engagement={"upvotes": 120, "comments": 40},
        snippet="Users compare OpenClaw and NanoClaw on latency.",
    )
    candidate = schema.Candidate(
        candidate_id="c1",
        item_id="i1",
        source="reddit",
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["compare"],
        native_ranks={"reddit": 1},
        local_relevance=0.9,
        freshness=1,
        engagement=160,
        source_quality=0.8,
        rrf_score=0.5,
        sources=["reddit"],
        source_items=[item],
        final_score=0.9,
        cluster_id="cl1",
    )
    cluster = schema.Cluster(
        cluster_id="cl1",
        title="Latency debate",
        candidate_ids=["c1"],
        representative_ids=["c1"],
        sources=["reddit"],
        score=0.9,
    )
    return schema.Report(
        topic=topic,
        range_from="2026-02-14",
        range_to="2026-03-16",
        generated_at="2026-03-16T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="gemini-3.1-flash-lite",
            rerank_model="gemini-3.1-flash-lite",
        ),
        query_plan=schema.QueryPlan(
            intent="comparison",
            freshness_mode="balanced_recent",
            cluster_mode="debate",
            raw_topic=topic,
            subqueries=[],
            source_weights={"reddit": 1.0},
        ),
        clusters=[cluster],
        ranked_candidates=[candidate],
        items_by_source={"reddit": [item]},
        errors_by_source={},
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state="ok", items_returned=1),
        },
        warnings=[],
        artifacts={},
        library_context=[],
    )


class ObsidianExportTests(unittest.TestCase):
    def test_slugify_and_wikilink(self) -> None:
        self.assertEqual(obsidian_export.slugify_topic("Hello, World!"), "hello-world")
        self.assertEqual(obsidian_export.wikilink_title("A [B] | C"), "A B  C")

    def test_render_run_note_has_frontmatter_and_sections(self) -> None:
        body = obsidian_export.render_run_note(make_report())
        self.assertIn("type: obsidian2date-run", body)
        self.assertIn("skill: obsidian2date", body)
        self.assertIn("## Briefing", body)
        self.assertIn("## Evidence index", body)
        self.assertIn("Latency debate", body)
        self.assertIn("https://reddit.com/r/LocalLLaMA/1", body)

    def test_export_writes_notes_index_dashboard_and_links_related(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            paths = obsidian_export.resolve_paths(vault)

            first = make_report("OpenClaw agents")
            result1 = obsidian_export.export_report_to_obsidian(first, paths=paths)
            self.assertTrue(result1.run_note.is_file())
            self.assertTrue(result1.briefing_note.is_file())
            self.assertTrue(result1.index_path.is_file())
            self.assertTrue(result1.dashboard_path.is_file())

            run1 = result1.run_note.read_text(encoding="utf-8")
            self.assertIn("type: obsidian2date-run", run1)
            self.assertIn("OpenClaw agents", run1)

            second = make_report("OpenClaw latency")
            result2 = obsidian_export.export_report_to_obsidian(second, paths=paths)
            self.assertTrue(result2.related)
            run2 = result2.run_note.read_text(encoding="utf-8")
            self.assertIn("## Related in vault", run2)
            self.assertIn("[[", run2)

            index = result2.index_path.read_text(encoding="utf-8")
            dashboard = result2.dashboard_path.read_text(encoding="utf-8")
            self.assertIn("OpenClaw latency", index)
            self.assertIn("OpenClaw agents", index)
            self.assertIn("Briefing:", dashboard)

            # Second export same day/topic must not overwrite the first note.
            third = make_report("OpenClaw latency")
            result3 = obsidian_export.export_report_to_obsidian(third, paths=paths)
            self.assertNotEqual(result2.run_note, result3.run_note)
            self.assertTrue(result2.run_note.exists())
            self.assertTrue(result3.run_note.exists())

    def test_emit_output_accepts_obsidian_mode(self) -> None:
        report = make_report()
        text = cli.emit_output(report, "obsidian")
        self.assertIn("OpenClaw", text)

    def test_parser_includes_obsidian_flags(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(
            ["topic here", "--emit", "obsidian", "--obsidian-vault", "/tmp/vault"]
        )
        self.assertEqual(args.emit, "obsidian")
        self.assertEqual(args.obsidian_vault, "/tmp/vault")

    def test_resolve_vault_root_prefers_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            resolved = obsidian_export.resolve_vault_root(
                None,
                env={"OBSIDIAN2DATE_VAULT": str(vault)},
            )
            self.assertEqual(resolved, vault.resolve())


if __name__ == "__main__":
    unittest.main()
