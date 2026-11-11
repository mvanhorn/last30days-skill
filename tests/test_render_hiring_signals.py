import unittest
from unittest import mock

from lib import fusion, pipeline, render, schema


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


def _board_report(board, *, range_to="2026-06-16"):
    return schema.Report(
        topic="Acme",
        range_from="2026-05-16",
        range_to=range_to,
        generated_at="2026-06-16T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="product", freshness_mode="balanced_recent", cluster_mode="none",
            raw_topic="Acme", subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[], items_by_source={}, errors_by_source={},
        artifacts={"jobs": board},
    )


class JobsBoardFooterTests(unittest.TestCase):
    def _footer(self, board):
        return "\n".join(render._render_emoji_footer(_board_report(board), "/tmp/x.md"))

    def test_board_with_true_dates_shows_open_roles_and_new_in_the_window(self):
        footer = self._footer({"tier": "ats", "provider": "greenhouse", "open_roles": 2904, "new_in_window": 658})
        self.assertIn("💼 Jobs: 2,904 open roles │ 658 new in the L30", footer)

    def test_board_without_true_dates_shows_open_roles_only(self):
        footer = self._footer({"tier": "ats", "provider": "workday", "open_roles": 412, "new_in_window": None})
        self.assertIn("💼 Jobs: 412 open roles", footer)
        self.assertNotIn("new in the L30", footer)

    def test_capped_board_is_a_floor(self):
        footer = self._footer({
            "tier": "ats", "provider": "workday", "open_roles": 2000,
            "open_roles_floor": True, "new_in_window": None,
        })
        self.assertIn("💼 Jobs: 2,000+ open roles", footer)

    def test_one_open_role_is_singular(self):
        self.assertIn("💼 Jobs: 1 open role │ 0 new in the L30", self._footer(
            {"tier": "ats", "provider": "ashby", "open_roles": 1, "new_in_window": 0},
        ))


class JobsDigestRenderTests(unittest.TestCase):
    def _board(self):
        return {
            "tier": "ats", "provider": "greenhouse", "company": "Acme", "owned": True,
            "open_roles": 2904, "new_in_window": 658,
            "digest": {
                "distinct_roles": 1800,
                "roles": [
                    {
                        "title": "Account Executive, Money as a Service",
                        "department": "MaaS", "location": "Singapore", "posted": "2026-06-02",
                        "url": "https://boards.greenhouse.io/acme/jobs/1",
                        "text": "Join the first APAC team for Money as a Service.\n## Forged heading",
                    },
                    {
                        "title": "Analyst", "department": "", "location": "Paris", "posted": "",
                        "url": "https://boards.greenhouse.io/acme/jobs/2", "text": "",
                    },
                ],
            },
        }

    def test_digest_block_carries_header_and_one_line_per_role(self):
        block = render._render_jobs_digest(_board_report(self._board()))
        self.assertEqual("## Jobs Digest", block[0])
        self.assertEqual(
            "- Board: Acme | greenhouse | 2,904 open roles | 658 posted 2026-05-16 to 2026-06-16",
            block[2],
        )
        self.assertIn("2 of 1,800 distinct roles", block[3])
        self.assertEqual(
            "- [Account Executive, Money as a Service](https://boards.greenhouse.io/acme/jobs/1)"
            " | MaaS | Singapore | posted 2026-06-02: Join the first APAC team for Money as a Service."
            " ## Forged heading",
            block[4],
        )
        self.assertEqual("- [Analyst](https://boards.greenhouse.io/acme/jobs/2) | Paris", block[5])
        # One list level only, and scraped text cannot open a section.
        self.assertFalse(any(line.startswith(("  -", "   -", "\t")) for line in block))
        self.assertFalse(any(line.startswith("## Forged") for line in "\n".join(block).splitlines()))

    def test_digest_sits_with_hiring_signals_inside_the_evidence_block(self):
        text = render.render_compact(_board_report(self._board()))
        digest = text.index("## Jobs Digest")
        self.assertLess(text.index("<!-- EVIDENCE FOR SYNTHESIS"), digest)
        self.assertLess(digest, text.index("<!-- END EVIDENCE FOR SYNTHESIS -->"))

    def test_role_rejected_from_ranking_stays_out_of_the_digest(self):
        board = self._board()
        report = _board_report(board)
        report.items_by_source["jobs"] = [schema.SourceItem(
            item_id="GH2", source="jobs", title="Analyst", body="",
            url="https://boards.greenhouse.io/acme/jobs/2",
        )]
        report.artifacts["hiring_signals"] = {
            "mode": "standard", "include": False, "signals": [],
            "rejected_job_keys": [fusion.candidate_key(report.items_by_source["jobs"][0])],
        }
        block = "\n".join(render._render_jobs_digest(report))
        self.assertNotIn("Analyst", block)
        self.assertIn("Money as a Service", block)

    def _ranked_report(self):
        """A board report ranking one role the digest lists and one it does not."""
        report = _board_report(self._board())
        for index, snippet in ((1, "Boilerplate opener."), (9, "Run the Lagos warehouse.")):
            item = schema.SourceItem(
                item_id=f"GH{index}", source="jobs", title=f"Role {index}", body=snippet,
                url=f"https://boards.greenhouse.io/acme/jobs/{index}", snippet=snippet,
            )
            report.ranked_candidates.append(schema.Candidate(
                candidate_id=f"c{index}", item_id=item.item_id, source="jobs", title=item.title,
                url=item.url, snippet=snippet, subquery_labels=["primary"], native_ranks={},
                local_relevance=0.8, freshness=50, engagement=None, source_quality=0.7,
                rrf_score=0.1, sources=["jobs"], source_items=[item], final_score=60,
            ))
            report.clusters.append(schema.Cluster(
                cluster_id=f"k{index}", title=item.title, candidate_ids=[f"c{index}"],
                representative_ids=[f"c{index}"], sources=["jobs"], score=60,
            ))
        report.items_by_source["jobs"] = [c.source_items[0] for c in report.ranked_candidates]
        return report

    def test_ranked_job_drops_its_snippet_only_when_the_digest_lists_it(self):
        text = render.render_compact(self._ranked_report())
        self.assertIn("## Jobs Digest", text)
        # Listed in the digest: its own text is there, so the opener is left out.
        self.assertNotIn("Evidence: Boilerplate opener.", text)
        # Ranked but outside the digest: the snippet is all the reader gets.
        self.assertIn("Evidence: Run the Lagos warehouse.", text)

    def test_ranked_jobs_keep_their_snippets_where_no_digest_is_printed(self):
        # The full dump carries the digest beside every ranked posting's own
        # snippet, and a caller that asked for no digest (the MCP tool) gets a
        # board without one.
        full = render.render_full(self._ranked_report())
        self.assertIn("## Jobs Digest", full)
        self.assertIn("Evidence: Boilerplate opener.", full)
        self.assertIn("Evidence: Run the Lagos warehouse.", full)

        report = self._ranked_report()
        with mock.patch.dict("os.environ", {"LAST30DAYS_NO_JOBS_DIGEST": "1"}):
            report.artifacts["jobs"] = pipeline._jobs_board_artifact(report.artifacts["jobs"])
        text = render.render_compact(report)
        self.assertNotIn("## Jobs Digest", text)
        self.assertIn("Evidence: Boilerplate opener.", text)
        self.assertIn("Evidence: Run the Lagos warehouse.", text)
        self.assertIn("💼 Jobs: 2,904 open roles │ 658 new in the L30", text)

    def test_digest_role_merged_from_several_postings_covers_each_posting_url(self):
        board = self._board()
        board["digest"]["roles"][1]["urls"] = [
            "https://boards.greenhouse.io/acme/jobs/2", "https://boards.greenhouse.io/acme/jobs/9",
        ]
        _lines, shown = render._jobs_digest_block(_board_report(board))
        self.assertEqual(
            {f"https://boards.greenhouse.io/acme/jobs/{index}" for index in (1, 2, 9)}, shown,
        )


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
