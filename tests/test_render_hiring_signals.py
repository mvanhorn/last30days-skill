import unittest

from lib import render, schema


class RenderHiringSignalsTests(unittest.TestCase):
    def test_render_hiring_signals_block_with_citations(self):
        report = schema.Report(
            topic="Listen Labs",
            range_from="2026-05-16",
            range_to="2026-06-16",
            generated_at="2026-06-16T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
            query_plan=schema.QueryPlan(
                intent="product",
                freshness_mode="balanced_recent",
                cluster_mode="none",
                raw_topic="Listen Labs",
                subqueries=[],
                source_weights={},
            ),
            clusters=[],
            ranked_candidates=[],
            items_by_source={},
            errors_by_source={},
            artifacts={
                "hiring_signals": {
                    "mode": "standard",
                    "company_size_tier": "startup",
                    "include": True,
                    "signals": [
                        {
                            "theme": "enterprise readiness",
                            "interpretation": "appears to be increasing focus on enterprise readiness",
                            "confidence": "medium",
                            "evidence_count": 2,
                            "evidence": [
                                {
                                    "title": "Enterprise Security Engineer",
                                    "url": "https://example.com/jobs/1",
                                    "department": "Engineering",
                                    "published_at": "2026-06-01",
                                }
                            ],
                        }
                    ],
                }
            },
        )
        block = "\n".join(render._render_hiring_signals(report))
        self.assertIn("Hiring Signals", block)
        self.assertIn("[Enterprise Security Engineer](https://example.com/jobs/1)", block)
        self.assertIn("not exact roadmap predictions", block)
        html_md = render.render_for_html(report)
        self.assertIn("Hiring Signals", html_md)
        self.assertIn("[Enterprise Security Engineer](https://example.com/jobs/1)", html_md)
        synthesized_html_md = render.render_for_html(
            report,
            synthesis_md="What I learned:\n\nHiring points toward enterprise readiness.",
        )
        self.assertIn("What I learned", synthesized_html_md)
        self.assertIn("Hiring Signals", synthesized_html_md)
        self.assertIn("[Enterprise Security Engineer](https://example.com/jobs/1)", synthesized_html_md)
        context = render.render_context(report)
        self.assertIn("Hiring Signals", context)
        self.assertIn("[Enterprise Security Engineer](https://example.com/jobs/1)", context)

    def test_standard_mode_omits_weak_signal(self):
        report = schema.Report(
            topic="Apple",
            range_from="2026-05-16",
            range_to="2026-06-16",
            generated_at="2026-06-16T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
            query_plan=schema.QueryPlan(
                intent="product",
                freshness_mode="balanced_recent",
                cluster_mode="none",
                raw_topic="Apple",
                subqueries=[],
                source_weights={},
            ),
            clusters=[],
            ranked_candidates=[],
            items_by_source={},
            errors_by_source={},
            artifacts={
                "hiring_signals": {
                    "mode": "standard",
                    "company_size_tier": "mega-cap",
                    "include": False,
                    "signals": [],
                    "omitted_reason": "jobs evidence is too diffuse",
                }
            },
        )
        self.assertEqual([], render._render_hiring_signals(report))


if __name__ == "__main__":
    unittest.main()


class JobsFooterTests(unittest.TestCase):
    def test_jobs_only_run_still_emits_law5_footer(self):
        report = schema.Report(
            topic="Listen Labs",
            range_from="2026-05-16",
            range_to="2026-06-16",
            generated_at="2026-06-16T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
            query_plan=schema.QueryPlan(
                intent="product", freshness_mode="balanced_recent", cluster_mode="none",
                raw_topic="Listen Labs", subqueries=[], source_weights={},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source={
                "jobs": [
                    schema.SourceItem(
                        item_id="AB1", source="jobs",
                        title="Founding Research Scientist, Human Simulation",
                        body="", url="https://jobs.ashbyhq.com/listenlabs/abc",
                        engagement={"open_roles": 1},
                    )
                ]
            },
            errors_by_source={},
            artifacts={},
        )
        footer = "\n".join(render._render_emoji_footer(report, "/tmp/x.md"))
        self.assertIn("All agents reported back", footer)
        self.assertIn("Jobs: 1 role", footer)


class HiringSignalsBannerSuppressionTests(unittest.TestCase):
    def _report(self):
        return schema.Report(
            topic="Listen Labs", range_from="2026-05-16", range_to="2026-06-16",
            generated_at="2026-06-16T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
            query_plan=schema.QueryPlan(
                intent="concept", freshness_mode="evergreen_ok", cluster_mode="none",
                raw_topic="Listen Labs", subqueries=[], source_weights={}),
            clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
            artifacts={"plan_source": "deterministic", "hiring_signals_mode": True},
        )

    def test_hiring_signals_suppresses_degraded_and_pre_research_banners(self):
        report = self._report()
        self.assertEqual([], render._render_degraded_run_warning(report))
        self.assertEqual([], render._render_pre_research_warning(report))

    def test_non_hiring_named_entity_still_warns(self):
        report = self._report()
        report.artifacts["hiring_signals_mode"] = False
        self.assertTrue(render._render_degraded_run_warning(report))
