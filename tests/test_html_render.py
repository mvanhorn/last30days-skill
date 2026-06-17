"""Tests for the HTML emit renderer."""

from __future__ import annotations

import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import last30days as cli
from lib import html_render, schema


def _report(topic: str, cluster_titles: list[str]) -> schema.Report:
    items: list[schema.SourceItem] = []
    candidates: list[schema.Candidate] = []
    clusters: list[schema.Cluster] = []

    for index, title in enumerate(cluster_titles, start=1):
        item = schema.SourceItem(
            item_id=f"item-{index}",
            source="grounding",
            title=title,
            body=f"Body for {title}",
            url=f"https://example.test/{index}",
            container="example.test",
            published_at="2026-04-20",
            date_confidence="high",
            engagement={"views": index * 100},
            snippet=f"Snippet for {title}",
        )
        candidate = schema.Candidate(
            candidate_id=f"candidate-{index}",
            item_id=item.item_id,
            source="grounding",
            title=title,
            url=item.url,
            snippet=item.snippet,
            subquery_labels=["primary"],
            native_ranks={"primary:grounding": index},
            local_relevance=0.9,
            freshness=80,
            engagement=50,
            source_quality=1.0,
            rrf_score=0.5,
            final_score=90 - index,
            sources=["grounding"],
            source_items=[item],
        )
        cluster = schema.Cluster(
            cluster_id=f"cluster-{index}",
            title=title,
            candidate_ids=[candidate.candidate_id],
            representative_ids=[candidate.candidate_id],
            sources=["grounding"],
            score=90 - index,
        )
        items.append(item)
        candidates.append(candidate)
        clusters.append(cluster)

    return schema.Report(
        topic=topic,
        range_from="2026-03-30",
        range_to="2026-04-29",
        generated_at="2026-04-29T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-rerank",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="balanced_recent",
            cluster_mode="story",
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
        ),
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source={"grounding": items},
        errors_by_source={},
        artifacts={"pre_research_flags_present": True},
    )


def _assert_parses(test_case: unittest.TestCase, html: str) -> None:
    parser = HTMLParser()
    parser.feed(html)
    parser.close()
    test_case.assertIn("</html>", html)


class HtmlRenderSnapshotTests(unittest.TestCase):
    def test_rich_cluster_fixture_snapshot(self):
        rendered = html_render.render_html(
            _report("AI agent frameworks", ["OpenClaw ships containers", "Skills marketplace grows"])
        )
        snapshot_markers = [
            "<!DOCTYPE html>",
            "<title>last30days · AI agent frameworks</title>",
            '<div class="badge"><span class="accent">🌐</span> last30days v',
            '<div class="meta">2026-03-30 to 2026-04-29',
            '<div class="engine-footer"><pre>---\n✅ All agents reported back!',
            'Generated 2026-04-29 by /last30days v',
            '<span class="rerun">/last30days AI agent frameworks</span>',
        ]
        for marker in snapshot_markers:
            self.assertIn(marker, rendered)
        self.assertNotIn("EVIDENCE FOR SYNTHESIS", rendered)
        self.assertNotIn("END OF last30days CANONICAL OUTPUT", rendered)

    def test_thin_cluster_fixture_snapshot(self):
        rendered = html_render.render_html(_report("obscure topic", []))
        snapshot_markers = [
            "<title>last30days · obscure topic</title>",
            "no active sources",
            "topic: obscure topic",
        ]
        for marker in snapshot_markers:
            self.assertIn(marker, rendered)

    def test_comparison_mode_snapshot(self):
        reports = [
            ("OpenClaw", _report("OpenClaw", ["Containers"])),
            ("Hermes", _report("Hermes", ["Memory"])),
        ]
        rendered = html_render.render_html_comparison(reports)
        snapshot_markers = [
            "<title>last30days · OpenClaw vs Hermes</title>",
            'comparing 2: OpenClaw, Hermes</div>',
            '<div class="meta">2026-03-30 to 2026-04-29',
            '<span class="rerun">/last30days OpenClaw vs Hermes</span>',
        ]
        for marker in snapshot_markers:
            self.assertIn(marker, rendered)


class HtmlRenderBehaviorTests(unittest.TestCase):
    def test_prose_label_promotion(self):
        md = html_render._promote_prose_labels("What I learned:")
        rendered = html_render._markdown_to_html(md)
        self.assertIn("<h2>What I learned</h2>", rendered)
        self.assertNotIn("What I learned:", rendered)

    def test_invitation_strip(self):
        md = "---\nI'm now an expert on OpenClaw. Some things you could ask:\n\nJust ask."
        self.assertNotIn("I'm now an expert", html_render._strip_invitation(md))

    def test_evidence_block_strip(self):
        md = "keep\n<!-- EVIDENCE FOR SYNTHESIS -->\nsecret\n<!-- END EVIDENCE FOR SYNTHESIS -->"
        stripped = html_render._strip_evidence_block(md)
        self.assertIn("keep", stripped)
        self.assertNotIn("EVIDENCE FOR SYNTHESIS", stripped)
        self.assertNotIn("secret", stripped)

    def test_engine_footer_wrapping_preserves_tree(self):
        md = (
            "<!-- PASS-THROUGH FOOTER: emit verbatim. -->\n"
            "✅ All agents reported back!\n"
            "├─ 🔵 X: 2 posts\n"
            "└─ 🌐 Web: 1 result\n"
            "<!-- END PASS-THROUGH FOOTER -->"
        )
        body = html_render._wrap_engine_footer(html_render._markdown_to_html(md))
        self.assertIn('<div class="engine-footer"><pre>✅ All agents reported back!', body)
        self.assertIn("├─ 🔵 X: 2 posts", body)
        self.assertIn("└─ 🌐 Web: 1 result", body)

    def test_colophon_contains_topic_and_rerun_command(self):
        rendered = html_render.render_html(_report("AI agent frameworks", []))
        self.assertIn("topic: AI agent frameworks", rendered)
        self.assertIn("/last30days AI agent frameworks", rendered)

    def test_parseability(self):
        _assert_parses(self, html_render.render_html(_report("parse me", ["One"])))

    def test_self_containedness(self):
        rendered = html_render.render_html(_report("self contained", []))
        self.assertNotIn("<script", rendered.lower())
        self.assertEqual(0, rendered.count('rel="stylesheet"'))
        self.assertNotIn("fonts.googleapis.com", rendered)
        self.assertNotIn("fonts.gstatic.com", rendered)
        self.assertNotIn('rel="preconnect"', rendered)

    def test_markdown_links_convert(self):
        rendered = html_render._markdown_to_html("[name](https://example.test/path)")
        self.assertIn(
            '<a href="https://example.test/path" rel="noopener noreferrer">name</a>',
            rendered,
        )

    def test_markdown_links_reject_javascript_scheme(self):
        """A `[label](javascript:...)` link must NOT render as a clickable href.

        The HTML artifact is opened in a browser, so allowing arbitrary URL
        schemes turns synthesized markdown into a stored-XSS surface. External
        sources (Reddit, X, HN, etc.) can plant such links and the LLM may
        carry them through synthesis; the renderer is the last gate.
        """
        for payload in (
            "[click](javascript:alert(1))",
            "[click](JAVASCRIPT:alert(1))",
            "[click](vbscript:alert(1))",
            "[click](file:///etc/passwd)",
        ):
            rendered = html_render._markdown_to_html(payload)
            self.assertNotIn("<a ", rendered, msg=f"payload accepted: {payload}")
            self.assertNotIn("href=", rendered, msg=f"payload accepted: {payload}")
            # The label still surfaces as plain text so context isn't lost.
            self.assertIn("click", rendered, msg=f"label dropped: {payload}")

    def test_markdown_links_reject_data_uri(self):
        rendered = html_render._markdown_to_html(
            "[click](data:text/html,<svg/onload=alert(1)>)"
        )
        self.assertNotIn("<a ", rendered)
        self.assertNotIn("href=", rendered)
        # The label still surfaces as plain text so context isn't lost.
        self.assertIn("click", rendered)

    def test_markdown_links_strip_leading_whitespace_javascript(self):
        """Leading whitespace in the URL must not let a `javascript:` payload
        bypass the scheme check (browsers strip leading whitespace before
        parsing the scheme)."""
        rendered = html_render._markdown_to_html("[click](\tjavascript:alert(1))")
        # `[^)\\s]+` already rejects whitespace inside the URL, so the regex
        # doesn't match and the label is left as plain text. The point of the
        # assertion is to pin that behavior: NO `<a href>` is produced.
        self.assertNotIn("<a ", rendered)
        self.assertNotIn("href=", rendered)

    def test_markdown_links_allow_relative_url(self):
        rendered = html_render._markdown_to_html("[home](/path?x=1#section)")
        self.assertIn(
            '<a href="/path?x=1#section" rel="noopener noreferrer">home</a>',
            rendered,
        )

    def test_markdown_links_allow_mailto(self):
        rendered = html_render._markdown_to_html("[mail](mailto:a@example.com)")
        self.assertIn(
            '<a href="mailto:a@example.com" rel="noopener noreferrer">mail</a>',
            rendered,
        )

    def test_no_file_header_h1(self):
        rendered = html_render.render_html(_report("AI agent frameworks", ["One"]))
        self.assertNotIn("<h1>last30days v", rendered)

    def test_no_safety_note(self):
        rendered = html_render.render_html(_report("AI agent frameworks", ["One"]))
        self.assertNotIn("Safety note", rendered)

    def test_synthesis_md_embedded(self):
        synthesis = "**Test brief** - body content per [@example](https://example.com)"
        rendered = html_render.render_html(
            _report("AI agent frameworks", ["One"]),
            synthesis_md=synthesis,
        )
        self.assertIn("<strong>Test brief</strong> - body content per", rendered)
        self.assertIn(
            '<a href="https://example.com" rel="noopener noreferrer">@example</a>',
            rendered,
        )
        metadata_index = rendered.index('<div class="meta">')
        synthesis_index = rendered.index("<strong>Test brief</strong>")
        footer_index = rendered.index('<div class="engine-footer">')
        self.assertLess(metadata_index, synthesis_index)
        self.assertLess(synthesis_index, footer_index)

    def test_warnings_excluded_from_html_artifact(self):
        """Data quality warnings must NOT appear in the shareable HTML.

        Recipients of a shared HTML brief don't have context to act on
        warnings about pre-flight resolution / engine state. The HTML is the
        artifact; warnings stay in the engine's stderr logs where the
        generator (not the recipient) sees them.
        """
        report = _report("OpenClaw", ["Containers"])
        report.artifacts["pre_research_flags_present"] = False
        report.artifacts["plan_source"] = "deterministic"
        report.warnings.append("Brave quota exhausted")
        rendered = html_render.render_html(report)
        # Warning text variations must all be absent from the artifact.
        self.assertNotIn("Data quality note", rendered)
        self.assertNotIn("Brave quota exhausted", rendered)
        self.assertNotIn("DEGRADED RUN WARNING", rendered)
        self.assertNotIn("Pre-Research Status", rendered)
        # No blockquote at all in mock output - just badge + meta + footer + colophon
        self.assertEqual(0, rendered.count("<blockquote>"))


class HtmlCliIntegrationTests(unittest.TestCase):
    def test_parser_accepts_html_emit(self):
        args = cli.build_parser().parse_args(["AI agents", "--emit=html"])
        self.assertEqual("html", args.emit)

    def test_synthesis_file_cli(self):
        synthesis = "**Test brief** - body content per [@example](https://example.com)"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tmp:
            tmp.write(synthesis)
            tmp_path = tmp.name
        try:
            args = cli.build_parser().parse_args([
                "OpenClaw",
                "--mock",
                "--emit=html",
                "--synthesis-file",
                tmp_path,
            ])
            rendered = cli.emit_output(
                _report("OpenClaw", ["Containers"]),
                args.emit,
                synthesis_md=cli.read_synthesis_file(args.synthesis_file),
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
        self.assertIn("<strong>Test brief</strong> - body content per", rendered)

    def test_save_output_uses_raw_html_extension_and_suffix(self):
        report = _report("AI Agent Frameworks", [])
        with self.subTest("plain"):
            path = cli.compute_save_path_display("/tmp", report.topic, "", "html")
            self.assertTrue(path.endswith("/ai-agent-frameworks-raw-html.html"))
        with self.subTest("suffix"):
            path = cli.compute_save_path_display("/tmp", report.topic, "v3", "html")
            self.assertTrue(path.endswith("/ai-agent-frameworks-raw-html-v3.html"))

    def test_save_output_can_persist_comparison_html(self):
        reports = [
            ("OpenClaw", _report("OpenClaw", ["Containers"])),
            ("Hermes", _report("Hermes", ["Memory"])),
        ]
        rendered = cli.emit_comparison_output(reports, "html")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = cli.save_output(
                reports[0][1],
                "html",
                tmpdir,
                topic_override=cli.comparison_topic(reports),
                rendered_content=rendered,
            )
            self.assertEqual("openclaw-vs-hermes-raw-html.html", path.name)
            saved = path.read_text(encoding="utf-8")
        self.assertIn("last30days · OpenClaw vs Hermes", saved)
        self.assertIn("comparing 2: OpenClaw, Hermes", saved)
        self.assertNotIn("last30days · OpenClaw</title>", saved)

if __name__ == "__main__":
    unittest.main()
