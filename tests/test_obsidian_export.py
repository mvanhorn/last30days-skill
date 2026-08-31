"""Tests for the additive Obsidian vault export path."""

from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
cli = importlib.import_module("last30days")
obsidian_export = importlib.import_module("lib.obsidian_export")
schema = importlib.import_module("lib.schema")


def make_report(topic: str = "OpenClaw vs NanoClaw"):
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
        self.assertEqual(
            obsidian_export.wikilink_alias("2026-03-16-topic-1", "Topic — 2026-03-16"),
            "2026-03-16-topic-1|Topic — 2026-03-16",
        )

    def test_wikilinks_resolve_against_note_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            paths = obsidian_export.resolve_paths(vault)

            first = make_report("OpenClaw agents")
            result1 = obsidian_export.export_report_to_obsidian(first, paths=paths)

            second = make_report("OpenClaw latency")
            result2 = obsidian_export.export_report_to_obsidian(second, paths=paths)

            run_stem = result1.run_note.stem
            run2 = result2.run_note.read_text(encoding="utf-8")
            self.assertIn(f"[[{run_stem}|", run2, "related links must target the note file")

            briefing2 = result2.briefing_note.read_text(encoding="utf-8")
            self.assertIn(f"[[{result2.run_note.stem}|", briefing2, "briefing must link the run file")
            self.assertIn(f"[[{run_stem}|", briefing2)

            index = result2.index_path.read_text(encoding="utf-8")
            self.assertIn(f"[[{run_stem}|", index)

            dashboard = result2.dashboard_path.read_text(encoding="utf-8")
            self.assertIn(f"[[{result2.briefing_note.stem}|", dashboard)

            stdout = obsidian_export.render_obsidian_stdout(result2, second)
            self.assertIn(f"[[{run_stem}|", stdout)

    def test_render_run_note_has_frontmatter_and_sections(self) -> None:
        body = obsidian_export.render_run_note(make_report())
        self.assertIn("type: obsidian2date-run", body)
        self.assertIn("skill: obsidian2date", body)
        self.assertIn("## Briefing", body)
        self.assertIn("## Evidence index", body)
        self.assertIn("Latency debate", body)
        self.assertIn("https://reddit.com/r/LocalLLaMA/1", body)
        self.assertIn("Users compare OpenClaw and NanoClaw on latency.", body)

    def test_blurb_skips_title_only_source_text(self) -> None:
        report = make_report()
        item = report.ranked_candidates[0].source_items[0]
        item.snippet = item.title
        item.body = item.title
        item.why_relevant = "HN story about OpenClaw threads explode"
        report.ranked_candidates[0].snippet = item.title
        self.assertEqual(obsidian_export._candidate_blurb(report.ranked_candidates[0]), "")

    def test_cluster_summary_uses_evidence_fallback_without_title_echo(self) -> None:
        report = make_report()
        cluster = report.clusters[0]
        candidate = report.ranked_candidates[0]
        candidate.source_items[0].snippet = candidate.title
        candidate.source_items[0].body = candidate.title
        candidate.source_items[0].why_relevant = "HN story about OpenClaw threads explode"
        candidate.snippet = candidate.title
        self.assertEqual(
            obsidian_export._cluster_summary(report, cluster),
            "Cluster across Reddit; open the evidence index for links.",
        )

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

    def test_resolve_vault_root_explicit_missing_path_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "missing-vault"
            self.assertEqual(
                obsidian_export.resolve_vault_root(str(explicit), env={}),
                explicit.resolve(),
            )

    def test_resolve_vault_root_blank_environment_blocks_implicit_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            fallback = home / "Desktop" / "brain-paul"
            fallback.mkdir(parents=True)
            with mock.patch.object(Path, "home", return_value=home), self.assertRaisesRegex(
                FileNotFoundError,
                r"^No Obsidian vault found\. Pass --obsidian-vault or set OBSIDIAN2DATE_VAULT\.$",
            ):
                obsidian_export.resolve_vault_root(
                    None,
                    env={"OBSIDIAN2DATE_VAULT": "", "LAST30DAYS_OBSIDIAN_VAULT": "  "},
                )


if __name__ == "__main__":
    unittest.main()
