import unittest

from lib import render, schema


def sample_report() -> schema.Report:
    primary_item = schema.SourceItem(
        item_id="i1",
        source="grounding",
        title="Grounded result",
        body="A grounded body with useful detail.",
        url="https://example.com",
        container="example.com",
        published_at="2026-03-15",
        date_confidence="high",
        snippet="A grounded snippet about the topic.",
        metadata={},
    )
    reddit_item = schema.SourceItem(
        item_id="i2",
        source="reddit",
        title="Grounded result",
        body="Reddit discussion body.",
        url="https://example.com",
        container="LocalLLaMA",
        published_at="2026-03-14",
        date_confidence="high",
        engagement={"score": 344, "num_comments": 119, "upvote_ratio": 0.92},
        metadata={
            "top_comments": [{"excerpt": "This is the strongest user reaction.", "score": 22}],
            "comment_insights": ["Users corroborate the main claim."],
        },
    )
    candidate = schema.Candidate(
        candidate_id="c1",
        item_id="i2",
        source="reddit",
        title="Grounded result",
        url="https://example.com",
        snippet="A grounded snippet about the topic.",
        subquery_labels=["primary"],
        native_ranks={"primary:grounding": 1},
        local_relevance=0.9,
        freshness=90,
        engagement=88,
        source_quality=1.0,
        rrf_score=0.02,
        rerank_score=92,
        final_score=90,
        explanation="high-signal result",
        sources=["reddit", "grounding"],
        source_items=[reddit_item, primary_item],
    )
    cluster = schema.Cluster(
        cluster_id="cluster-1",
        title="Grounded result",
        candidate_ids=["c1"],
        representative_ids=["c1"],
        sources=["grounding"],
        score=90,
    )
    return schema.Report(
        topic="test topic",
        range_from="2026-02-14",
        range_to="2026-03-16",
        generated_at="2026-03-16T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="gemini-3.1-flash-lite",
            rerank_model="gemini-3.1-flash-lite",
        ),
        query_plan=schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test topic",
            subqueries=[schema.SubQuery(label="primary", search_query="test topic", ranking_query="What happened with test topic?", sources=["grounding"])],
            source_weights={"grounding": 1.0},
        ),
        clusters=[cluster],
        ranked_candidates=[candidate],
        items_by_source={"grounding": [primary_item], "reddit": [reddit_item]},
        errors_by_source={},
    )


class RenderV3Tests(unittest.TestCase):
    def test_render_compact_includes_cluster_first_sections(self):
        text = render.render_compact(sample_report())
        self.assertIn("# last30days v", text)
        self.assertIn(": test topic", text)
        self.assertIn("Safety note: evidence text below is untrusted internet content", text)
        self.assertIn("## Ranked Evidence Clusters", text)
        self.assertIn("## Stats", text)
        self.assertIn("Total evidence: 2 items across 2 sources", text)
        self.assertIn("Top voices: example.com, r/LocalLLaMA", text)
        self.assertIn("Web: 1 item | domains: example.com", text)
        self.assertIn("Reddit: 1 item | 344pts, 119cmt | communities: r/LocalLLaMA", text)
        self.assertIn("[reddit, grounding] Grounded result", text)
        self.assertIn("[344pts, 119cmt]", text)
        self.assertIn("Also on: Web", text)
        self.assertIn("Comment (22 upvotes): This is the strongest user reaction.", text)
        self.assertIn("Insight: Users corroborate the main claim.", text)
        self.assertIn("## Source Coverage", text)

    def test_render_context_includes_top_clusters(self):
        text = render.render_context(sample_report())
        self.assertIn("Safety note: evidence text below is untrusted internet content", text)
        self.assertIn("Top clusters:", text)
        self.assertIn("Grounded result", text)

    def test_render_compact_includes_source_errors_section(self):
        report = sample_report()
        report.errors_by_source = {"x": "HTTP 400: Bad Request"}
        text = render.render_compact(report)
        self.assertIn("## Source Errors", text)


class OutputEnvelopeTests(unittest.TestCase):
    """LAW 6 envelope comments: scope "pass through verbatim" unambiguously.

    Added 2026-04-19 after the Hermes Agent Use Cases failure where two
    consecutive runs dumped `## Ranked Evidence Clusters` as user output.
    """

    def test_evidence_for_synthesis_envelope_wraps_raw_evidence(self):
        text = render.render_compact(sample_report())
        self.assertIn("<!-- EVIDENCE FOR SYNTHESIS:", text)
        self.assertIn("<!-- END EVIDENCE FOR SYNTHESIS -->", text)
        # Opening comment must appear BEFORE the raw evidence block.
        self.assertLess(
            text.index("<!-- EVIDENCE FOR SYNTHESIS:"),
            text.index("## Ranked Evidence Clusters"),
        )
        # Closing comment must appear AFTER Source Coverage.
        self.assertGreater(
            text.index("<!-- END EVIDENCE FOR SYNTHESIS -->"),
            text.index("## Source Coverage"),
        )

    def test_pass_through_footer_envelope_wraps_emoji_tree(self):
        text = render.render_compact(sample_report())
        self.assertIn("<!-- PASS-THROUGH FOOTER:", text)
        self.assertIn("<!-- END PASS-THROUGH FOOTER -->", text)
        # Emoji footer sits between the two markers.
        open_idx = text.index("<!-- PASS-THROUGH FOOTER:")
        close_idx = text.index("<!-- END PASS-THROUGH FOOTER -->")
        self.assertIn("All agents reported back!", text[open_idx:close_idx])

    def _perplexity_item(self, item_id: str, citations: int) -> schema.SourceItem:
        return schema.SourceItem(
            item_id=item_id,
            source="perplexity",
            title=f"Perplexity Sonar Pro: test topic ({item_id})",
            body="AI synthesis body.",
            url="",
            container="perplexity.ai",
            published_at="2026-03-16",
            date_confidence="high",
            engagement={"citations": citations},
            metadata={},
        )

    def test_emoji_footer_includes_perplexity_when_present(self):
        # Regression: Perplexity items survived retrieval/normalize/dedup but
        # were dropped from the emoji-tree footer because _FOOTER_SOURCES
        # omitted perplexity. The synthesis LLM that consumes the pass-through
        # block then had no Perplexity signal, and users reasonably concluded
        # the source was broken.
        report = sample_report()
        report.items_by_source["perplexity"] = [self._perplexity_item("px1", 7)]
        text = render.render_compact(report)
        self.assertIn("🧠 Perplexity:", text)
        self.assertIn("7 citations", text)

    def test_emoji_footer_perplexity_pluralizes_correctly(self):
        # The footer line helper appends a literal "s" for plurals, so the
        # item_word must pluralize regularly. Multi-item runs must produce
        # "results", not "synthesiss" or other malformed forms.
        report = sample_report()
        report.items_by_source["perplexity"] = [
            self._perplexity_item("px1", 4),
            self._perplexity_item("px2", 3),
            self._perplexity_item("px3", 2),
        ]
        text = render.render_compact(report)
        self.assertIn("3 results", text)
        self.assertNotIn("3 synthesiss", text)
        self.assertNotIn("3 syntheses", text)
        # Aggregate of all citation counts (4+3+2 = 9) — confirms multi-item
        # engagement summation also lands correctly.
        self.assertIn("9 citations", text)

    def test_canonical_boundary_scopes_pass_through_to_footer(self):
        text = render.render_compact(sample_report())
        # New boundary text scopes verbatim to the PASS-THROUGH FOOTER block,
        # not everything above.
        self.assertIn("Pass through ONLY the PASS-THROUGH FOOTER block verbatim", text)
        # Self-check string is present so the model has a concrete failure signal.
        self.assertIn("### 1.", text)
        self.assertIn("LAW 6", text)
        # The prior ambiguous phrasing is gone.
        self.assertNotIn("Pass through the lines ABOVE this boundary verbatim", text)

    def test_envelopes_appear_in_md_emit_mode(self):
        # --emit md and --emit compact both route to render_compact, so the
        # same envelopes apply. Guard against future divergence.
        text = render.render_compact(sample_report())
        self.assertEqual(text.count("<!-- EVIDENCE FOR SYNTHESIS:"), 1)
        self.assertEqual(text.count("<!-- END EVIDENCE FOR SYNTHESIS -->"), 1)
        self.assertEqual(text.count("<!-- PASS-THROUGH FOOTER:"), 1)
        self.assertEqual(text.count("<!-- END PASS-THROUGH FOOTER -->"), 1)

    def test_no_dangling_envelope_open_without_close(self):
        # Open/close counts must always match, even for empty clusters.
        report = sample_report()
        report.clusters = []
        text = render.render_compact(report)
        self.assertEqual(
            text.count("<!-- EVIDENCE FOR SYNTHESIS:"),
            text.count("<!-- END EVIDENCE FOR SYNTHESIS -->"),
        )
        self.assertEqual(
            text.count("<!-- PASS-THROUGH FOOTER:"),
            text.count("<!-- END PASS-THROUGH FOOTER -->"),
        )


class RenderTopCommentsTests(unittest.TestCase):
    """Tests for the top-3 comments rendering in compact cluster view."""

    def _make_report_with_comments(self, source="reddit", top_comments=None, comment_insights=None):
        """Helper: build a report with a single candidate carrying given comments."""
        item = schema.SourceItem(
            item_id="i1",
            source=source,
            title="Test post",
            body="Body text.",
            url="https://reddit.com/r/test/comments/abc/test/",
            container="test",
            published_at="2026-03-15",
            date_confidence="high",
            engagement={"score": 100, "num_comments": 50},
            metadata={
                "top_comments": top_comments or [],
                "comment_insights": comment_insights or [],
            },
        )
        candidate = schema.Candidate(
            candidate_id="c1",
            item_id="i1",
            source=source,
            title="Test post",
            url="https://reddit.com/r/test/comments/abc/test/",
            snippet="A test snippet.",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=90,
            engagement=88,
            source_quality=1.0,
            rrf_score=0.02,
            rerank_score=92,
            final_score=90,
            sources=[source],
            source_items=[item],
        )
        cluster = schema.Cluster(
            cluster_id="cluster-1",
            title="Test cluster",
            candidate_ids=["c1"],
            representative_ids=["c1"],
            sources=[source],
            score=90,
        )
        return schema.Report(
            topic="test topic",
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite",
                rerank_model="gemini-3.1-flash-lite",
            ),
            query_plan=schema.QueryPlan(
                intent="breaking_news",
                freshness_mode="strict_recent",
                cluster_mode="story",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test?", sources=[source])],
                source_weights={source: 1.0},
            ),
            clusters=[cluster],
            ranked_candidates=[candidate],
            items_by_source={source: [item]},
            errors_by_source={},
        )

    def test_reddit_5_comments_renders_top_3(self):
        """Reddit candidate with 5 comments (scores 500, 200, 50, 8, 3) renders 3."""
        comments = [
            {"score": 500, "excerpt": "Comment with 500 upvotes", "author": "user1"},
            {"score": 200, "excerpt": "Comment with 200 upvotes", "author": "user2"},
            {"score": 50, "excerpt": "Comment with 50 upvotes", "author": "user3"},
            {"score": 8, "excerpt": "Comment with 8 upvotes", "author": "user4"},
            {"score": 3, "excerpt": "Comment with 3 upvotes", "author": "user5"},
        ]
        report = self._make_report_with_comments(top_comments=comments)
        text = render.render_compact(report)
        # Reddit authors render with u/ prefix now.
        self.assertIn("u/user1 (500 upvotes):", text)
        self.assertIn("u/user2 (200 upvotes):", text)
        self.assertIn("u/user3 (50 upvotes):", text)
        self.assertNotIn("u/user4 (8 upvotes):", text)
        self.assertNotIn("u/user5 (3 upvotes):", text)

    def test_reddit_1_comment_renders_1(self):
        """Reddit candidate with 1 comment renders 1."""
        comments = [{"score": 100, "excerpt": "Single comment", "author": "user1"}]
        report = self._make_report_with_comments(top_comments=comments)
        text = render.render_compact(report)
        self.assertIn("u/user1 (100 upvotes): Single comment", text)

    def test_reddit_0_comments_no_section(self):
        """Reddit candidate with 0 comments renders no comment section."""
        report = self._make_report_with_comments(top_comments=[])
        text = render.render_compact(report)
        self.assertNotIn("upvotes)", text)

    def test_non_reddit_no_comments(self):
        """Non-Reddit candidate doesn't render comments when metadata has none."""
        report = self._make_report_with_comments(source="grounding", top_comments=[])
        text = render.render_compact(report)
        self.assertNotIn("upvotes)", text)
        self.assertIn("Test cluster", text)

    def test_all_comments_below_score_10_no_section(self):
        """All comments below score 10 renders no comment section."""
        comments = [
            {"score": 9, "excerpt": "Low score 1", "author": "user1"},
            {"score": 5, "excerpt": "Low score 2", "author": "user2"},
            {"score": 1, "excerpt": "Low score 3", "author": "user3"},
        ]
        report = self._make_report_with_comments(top_comments=comments)
        text = render.render_compact(report)
        self.assertNotIn("upvotes)", text)

    def test_youtube_comments_use_likes_label_and_50_threshold(self):
        comments = [
            {"score": 120, "excerpt": "legit fire tutorial", "author": "alice"},
            {"score": 60, "excerpt": "saved me hours", "author": "bob"},
            {"score": 10, "excerpt": "below threshold", "author": "carol"},
        ]
        report = self._make_report_with_comments(source="youtube", top_comments=comments)
        text = render.render_compact(report)
        # YouTube authors render with @ prefix now.
        self.assertIn("@alice (120 likes): legit fire tutorial", text)
        self.assertIn("@bob (60 likes): saved me hours", text)
        self.assertNotIn("@carol (10 likes)", text)

    def test_reddit_comment_without_author_falls_back_to_legacy_label(self):
        """When author is missing or [deleted], render falls back to 'Comment (...)'."""
        comments = [
            {"score": 500, "excerpt": "No author field", "author": ""},
            {"score": 200, "excerpt": "Deleted user", "author": "[deleted]"},
            {"score": 50, "excerpt": "Removed user", "author": "[removed]"},
        ]
        report = self._make_report_with_comments(top_comments=comments)
        text = render.render_compact(report)
        # Legacy format preserved - no u/ prefix leaks with empty/deleted handles.
        self.assertIn("Comment (500 upvotes): No author field", text)
        self.assertIn("Comment (200 upvotes): Deleted user", text)
        self.assertIn("Comment (50 upvotes): Removed user", text)
        self.assertNotIn("u/ (", text)
        self.assertNotIn("u/[deleted]", text)
        self.assertNotIn("u/[removed]", text)

    def test_tiktok_comments_render_with_at_handle(self):
        """TikTok source renders @handle attribution on comment lines."""
        comments = [
            {"score": 3986, "excerpt": "oh no. who's going to make the same phone every year now..", "author": "moosanoormahomed"},
            {"score": 925, "excerpt": "This is either going to go so well or so bad", "author": "Muna9e"},
        ]
        report = self._make_report_with_comments(source="tiktok", top_comments=comments)
        text = render.render_compact(report)
        self.assertIn("@moosanoormahomed (3986 likes):", text)
        self.assertIn("@Muna9e (925 likes):", text)
        # Render must not silently label YT as upvotes.
        self.assertNotIn("Comment (120 upvotes)", text)

    def test_tiktok_comments_use_likes_label_and_500_threshold(self):
        comments = [
            {"score": 2000, "excerpt": "this aged well", "author": "a"},
            {"score": 600, "excerpt": "so real", "author": "b"},
            {"score": 400, "excerpt": "below tt threshold", "author": "c"},
            {"score": 50, "excerpt": "way below", "author": "d"},
        ]
        report = self._make_report_with_comments(source="tiktok", top_comments=comments)
        text = render.render_compact(report)
        self.assertIn("@a (2000 likes): this aged well", text)
        self.assertIn("@b (600 likes): so real", text)
        self.assertNotIn("@c (400 likes)", text)
        self.assertNotIn("@d (50 likes)", text)


class RenderBestTakesCompactTests(unittest.TestCase):
    """Tests for Best Takes section in compact output and fun tags on candidates."""

    def _make_candidate(self, cid, fun_score=None, fun_explanation=None, final_score=80):
        """Helper: build a candidate with a given fun_score."""
        item = schema.SourceItem(
            item_id=f"item-{cid}",
            source="reddit",
            title=f"Post {cid}",
            body="Body text.",
            url=f"https://reddit.com/r/test/comments/{cid}/",
            container="test",
            published_at="2026-03-15",
            date_confidence="high",
            engagement={"score": 200, "num_comments": 30},
            metadata={
                "top_comments": [{"excerpt": "Funny comment", "score": 50, "body": "lmao this is gold"}],
            },
        )
        return schema.Candidate(
            candidate_id=cid,
            item_id=f"item-{cid}",
            source="reddit",
            title=f"Post {cid}",
            url=f"https://reddit.com/r/test/comments/{cid}/",
            snippet="A test snippet.",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=90,
            engagement=88,
            source_quality=1.0,
            rrf_score=0.02,
            rerank_score=92,
            final_score=final_score,
            sources=["reddit"],
            source_items=[item],
            fun_score=fun_score,
            fun_explanation=fun_explanation,
        )

    def _make_report_with_candidates(self, candidates):
        """Helper: build a report with given candidates."""
        items = []
        for c in candidates:
            items.extend(c.source_items)
        cluster = schema.Cluster(
            cluster_id="cluster-1",
            title="Test cluster",
            candidate_ids=[c.candidate_id for c in candidates],
            representative_ids=[c.candidate_id for c in candidates],
            sources=["reddit"],
            score=90,
        )
        return schema.Report(
            topic="test topic",
            range_from="2026-02-14",
            range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini-3.1-flash-lite",
                rerank_model="gemini-3.1-flash-lite",
            ),
            query_plan=schema.QueryPlan(
                intent="breaking_news",
                freshness_mode="strict_recent",
                cluster_mode="story",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test?", sources=["reddit"])],
                source_weights={"reddit": 1.0},
            ),
            clusters=[cluster],
            ranked_candidates=candidates,
            items_by_source={"reddit": items},
            errors_by_source={},
        )

    def test_compact_includes_best_takes_with_2_high_fun_candidates(self):
        """Compact output includes Best Takes section when 2+ candidates score >= 70."""
        candidates = [
            self._make_candidate("c1", fun_score=85, fun_explanation="hilarious comment"),
            self._make_candidate("c2", fun_score=75, fun_explanation="witty remark"),
            self._make_candidate("c3", fun_score=40),
        ]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertIn("## Best Takes", text)
        # fun: tag may carry a " +crowd" suffix when votes lifted the ranking,
        # so match the score substring rather than the exact closing paren.
        self.assertIn("fun:85", text)
        self.assertIn("fun:75", text)

    def test_candidate_with_fun_score_85_shows_fun_tag(self):
        """Candidate with fun_score=85 shows 'fun:85' in its detail line."""
        candidates = [self._make_candidate("c1", fun_score=85)]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertIn("fun:85", text)

    def test_candidate_with_fun_score_40_no_fun_tag(self):
        """Candidate with fun_score=40 does NOT show fun tag (below 50 threshold)."""
        candidates = [self._make_candidate("c1", fun_score=40)]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertNotIn("fun:40", text)
        self.assertNotIn("fun:", text)

    def test_no_best_takes_with_0_high_fun_candidates(self):
        """No Best Takes section when 0 candidates above threshold."""
        candidates = [
            self._make_candidate("c1", fun_score=50),
            self._make_candidate("c2", fun_score=40),
        ]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertNotIn("## Best Takes", text)

    def test_no_best_takes_with_1_high_fun_candidate(self):
        """No Best Takes section when only 1 candidate above threshold."""
        candidates = [
            self._make_candidate("c1", fun_score=80),
            self._make_candidate("c2", fun_score=50),
        ]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertNotIn("## Best Takes", text)


class DegradedRunBannerTests(unittest.TestCase):
    """Unit 1: DEGRADED RUN WARNING surfaces bare named-entity invocations
    in user-visible stdout. LAW 7 backstop. 2026-04-19 Hermes Agent Use
    Cases Run 1 failure mode.
    """

    def _bare_named_entity_report(self) -> schema.Report:
        report = sample_report()
        report.topic = "Hermes Agent"
        report.artifacts["plan_source"] = "deterministic"
        report.artifacts["pre_research_flags_present"] = False
        return report

    def test_banner_appears_on_bare_named_entity_deterministic_run(self):
        text = render.render_compact(self._bare_named_entity_report())
        self.assertIn("## DEGRADED RUN WARNING", text)
        self.assertIn("<!-- USER-VISIBLE BANNER:", text)
        self.assertIn("<!-- END USER-VISIBLE BANNER -->", text)
        self.assertIn("YOU ARE", text)
        # Runtime-agnostic enumeration: all host runtimes appear.
        for runtime_name in ("Claude Code", "Codex", "Hermes", "Gemini"):
            self.assertIn(runtime_name, text)

    def test_banner_positioned_before_evidence_envelope(self):
        text = render.render_compact(self._bare_named_entity_report())
        banner_idx = text.index("## DEGRADED RUN WARNING")
        envelope_idx = text.index("<!-- EVIDENCE FOR SYNTHESIS:")
        self.assertLess(banner_idx, envelope_idx,
            "DEGRADED RUN banner must appear BEFORE evidence envelope so pass-through catches it.")

    def test_banner_suppressed_when_plan_source_external(self):
        report = self._bare_named_entity_report()
        report.artifacts["plan_source"] = "external"
        text = render.render_compact(report)
        self.assertNotIn("## DEGRADED RUN WARNING", text)

    def test_banner_suppressed_when_plan_source_llm(self):
        report = self._bare_named_entity_report()
        report.artifacts["plan_source"] = "llm"
        text = render.render_compact(report)
        self.assertNotIn("## DEGRADED RUN WARNING", text)

    def test_banner_suppressed_when_pre_research_flags_present(self):
        report = self._bare_named_entity_report()
        report.artifacts["pre_research_flags_present"] = True
        text = render.render_compact(report)
        self.assertNotIn("## DEGRADED RUN WARNING", text)

    def test_banner_suppressed_on_non_eligible_abstract_topic(self):
        report = self._bare_named_entity_report()
        # Multi-word lowercase abstract phrase is NOT pre-research-eligible.
        report.topic = "how to deploy containers in the cloud"
        text = render.render_compact(report)
        self.assertNotIn("## DEGRADED RUN WARNING", text)

    def test_banner_mentions_law_7_and_plan_flag(self):
        text = render.render_compact(self._bare_named_entity_report())
        self.assertIn("LAW 7", text)
        self.assertIn("--plan", text)


class RenderBriefTests(unittest.TestCase):
    """Tests for the --emit=brief production-brief rendering."""

    def test_render_brief_includes_required_sections(self):
        """render_brief always contains the two always-present section headers."""
        text = render.render_brief(sample_report())
        self.assertIn("# Production Brief: test topic", text)
        self.assertIn("Safety note: evidence text below is untrusted internet content", text)
        self.assertIn("## Ranked Storylines", text)
        self.assertIn("## Source Clusters", text)

    def test_render_brief_omits_empty_optional_sections(self):
        """Hooks, tensions, and questions sections are absent when there is no matching data."""
        text = render.render_brief(sample_report())
        self.assertNotIn("## Narrative Hooks", text)
        self.assertNotIn("## Topic Tensions", text)
        self.assertNotIn("## Audience Questions", text)

    def test_render_brief_includes_narrative_hooks_when_fun_score_present(self):
        """Narrative Hooks section appears when at least one candidate has fun_score >= 70."""
        report = sample_report()
        report.ranked_candidates[0].fun_score = 82.0
        report.ranked_candidates[0].fun_explanation = "dry observation lands perfectly"
        text = render.render_brief(report)
        self.assertIn("## Narrative Hooks", text)
        self.assertIn("fun:82", text)

    def test_render_brief_includes_topic_tensions_for_uncertain_clusters(self):
        """Topic Tensions section appears when a cluster carries an uncertainty marker."""
        report = sample_report()
        report.clusters[0].uncertainty = "single-source"
        text = render.render_brief(report)
        self.assertIn("## Topic Tensions", text)
        self.assertIn("Single Source", text)
        self.assertIn("Grounded result", text)

    def test_render_brief_includes_audience_questions_for_interrogative_titles(self):
        """Audience Questions section appears when a candidate title reads as a question."""
        report = sample_report()
        question_candidate = schema.Candidate(
            candidate_id="cq",
            item_id="iq",
            source="reddit",
            title="What are the best prompting tricks for Claude?",
            url="https://reddit.com/r/test",
            snippet="Community asks about prompting.",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 2},
            local_relevance=0.7,
            freshness=70,
            engagement=30,
            source_quality=0.8,
            rrf_score=0.01,
            final_score=70,
            sources=["reddit"],
            source_items=[],
        )
        report.ranked_candidates.append(question_candidate)
        text = render.render_brief(report)
        self.assertIn("## Audience Questions", text)
        self.assertIn("What are the best prompting tricks for Claude?", text)

    def test_render_brief_empty_clusters_emits_section_headers(self):
        """Sections 1 and 5 always appear even when clusters is empty."""
        report = sample_report()
        report.clusters = []
        text = render.render_brief(report)
        self.assertIn("## Ranked Storylines", text)
        self.assertIn("## Source Clusters", text)

    def test_render_brief_hooks_omit_heuristic_fallback_reason(self):
        """Narrative Hooks omit the reason string when fun_explanation is 'heuristic-fallback'."""
        report = sample_report()
        report.ranked_candidates[0].fun_score = 75.0
        report.ranked_candidates[0].fun_explanation = "heuristic-fallback"
        text = render.render_brief(report)
        self.assertIn("## Narrative Hooks", text)
        self.assertNotIn("heuristic-fallback", text)

    def test_render_brief_audience_questions_are_deduped(self):
        """Duplicate question titles appear only once in the Audience Questions section."""
        report = sample_report()
        for i in range(2):
            report.ranked_candidates.append(schema.Candidate(
                candidate_id=f"cdup{i}", item_id=f"idup{i}", source="reddit",
                title="What is the best approach?",
                url="https://reddit.com/r/test", snippet="...",
                subquery_labels=["primary"], native_ranks={},
                local_relevance=0.7, freshness=70, engagement=30,
                source_quality=0.8, rrf_score=0.01, final_score=70,
                sources=["reddit"], source_items=[],
            ))
        text = render.render_brief(report)
        self.assertEqual(text.count("What is the best approach?"), 1)


class YoutubeFooterTranscriptRatioTests(unittest.TestCase):
    """The YouTube footer line must surface the transcript-fetch ratio in all
    cases where videos were returned. Pre-fix the segment was suppressed when
    transcripts == 0, which converted the canonical stale-yt-dlp failure mode
    into a silent absence at the footer (the very surface users read for
    'did this work?'). Always-render the ratio so zero is loud.
    """

    def _build_youtube_report(self, transcript_flags: list[bool]) -> schema.Report:
        """Build a Report with one YouTube item per entry in transcript_flags.
        True means the item has transcript data; False means it does not.
        """
        items = []
        for idx, has_transcript in enumerate(transcript_flags):
            metadata = {"views": 1000}
            if has_transcript:
                metadata["transcript_highlights"] = ["Some pre-extracted quote."]
            items.append(schema.SourceItem(
                item_id=f"yt{idx}",
                source="youtube",
                title=f"Video {idx}",
                body=f"Description for video {idx}.",
                url=f"https://youtube.com/watch?v=v{idx}",
                container="some-channel",
                published_at="2026-04-15",
                date_confidence="high",
                engagement={"views": 1000, "likes": 100},
                metadata=metadata,
            ))
        return schema.Report(
            topic="test topic",
            range_from="2026-04-01",
            range_to="2026-05-01",
            generated_at="2026-05-01T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini",
                rerank_model="gemini",
            ),
            query_plan=schema.QueryPlan(
                intent="general",
                freshness_mode="balanced_recent",
                cluster_mode="none",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(
                    label="primary", search_query="test topic",
                    ranking_query="What about test topic?", sources=["youtube"],
                )],
                source_weights={"youtube": 1.0},
            ),
            clusters=[],
            ranked_candidates=[],
            items_by_source={"youtube": items},
            errors_by_source={},
        )

    def test_zero_transcripts_with_videos_present_renders_zero_over_total(self):
        # The canonical stale-yt-dlp case: 6 videos found, 0 transcripts captured.
        # Pre-fix the footer hid this entirely; post-fix it must say "0/6 with transcripts".
        report = self._build_youtube_report([False] * 6)
        text = render.render_compact(report)
        self.assertIn("0/6 with transcripts", text)

    def test_partial_transcripts_renders_ratio(self):
        # 5 of 6 transcripts captured - shows ratio so user knows one was missed.
        report = self._build_youtube_report([True] * 5 + [False])
        text = render.render_compact(report)
        self.assertIn("5/6 with transcripts", text)

    def test_full_transcripts_renders_ratio(self):
        # All 3 transcripts captured - still shows ratio for consistency.
        report = self._build_youtube_report([True] * 3)
        text = render.render_compact(report)
        self.assertIn("3/3 with transcripts", text)

    def test_no_videos_no_transcript_segment(self):
        # When YouTube has no items at all, the YouTube footer line is
        # suppressed entirely (existing behavior) - the transcript segment
        # should not appear without a parent line.
        report = self._build_youtube_report([])
        text = render.render_compact(report)
        # No YouTube footer line at all - so no transcript segment either
        self.assertNotIn("with transcripts", text)


class TranscriptCaveatTests(unittest.TestCase):
    """Transcript-derived text must be labelled as auto-generated wherever it
    is emitted, so the synthesizing model does not treat caption homophone
    errors (e.g. "basil fears" for "basal fears") as verbatim quotes (#82).
    """

    def _youtube_item(self) -> schema.SourceItem:
        return schema.SourceItem(
            item_id="yt1",
            source="youtube",
            title="Interview video",
            body="Description.",
            url="https://youtube.com/watch?v=v1",
            container="some-channel",
            published_at="2026-04-15",
            date_confidence="high",
            engagement={"views": 1000, "likes": 100},
            metadata={
                "transcript_highlights": ["She identifies eight basil fears."],
                "transcript_snippet": "And basil you mean like of the body? " * 5,
            },
        )

    def _report(self) -> schema.Report:
        return schema.Report(
            topic="test topic",
            range_from="2026-04-01",
            range_to="2026-05-01",
            generated_at="2026-05-01T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini",
                planner_model="gemini",
                rerank_model="gemini",
            ),
            query_plan=schema.QueryPlan(
                intent="general",
                freshness_mode="balanced_recent",
                cluster_mode="none",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(
                    label="primary", search_query="test topic",
                    ranking_query="What about test topic?", sources=["youtube"],
                )],
                source_weights={"youtube": 1.0},
            ),
            clusters=[],
            ranked_candidates=[],
            items_by_source={"youtube": [self._youtube_item()]},
            errors_by_source={},
        )

    def test_render_full_labels_highlights_and_transcript_as_auto_generated(self):
        text = render.render_full(self._report())
        self.assertIn(
            "Highlights (auto-generated transcript; may contain transcription errors):",
            text,
        )
        self.assertIn("auto-generated — may contain transcription errors)</summary>", text)
        self.assertNotIn("\n  Highlights:\n", text)

    def test_render_candidate_labels_highlights_as_auto_generated(self):
        item = self._youtube_item()
        candidate = schema.Candidate(
            candidate_id="c1",
            item_id=item.item_id,
            source="youtube",
            title=item.title,
            url=item.url,
            snippet="A snippet.",
            subquery_labels=["primary"],
            native_ranks={"youtube": 1},
            local_relevance=1.0,
            freshness=1,
            engagement=1000,
            source_quality=1.0,
            rrf_score=1.0,
            sources=["youtube"],
            source_items=[item],
        )
        lines = render._render_candidate(candidate, "1.")
        text = "\n".join(lines)
        self.assertIn(
            "Highlights (auto-generated transcript; may contain transcription errors):",
            text,
        )


if __name__ == "__main__":
    unittest.main()


class TestRenderTopCommentsBlock(unittest.TestCase):
    """U3: vote-ranked Top Community Comments across ALL candidates, inside the
    EVIDENCE envelope, so the funniest lines reach the synthesizing model even
    when Best Takes is empty (no LLM fun-scorer in the engine subprocess)."""

    def _cand(self, cid, source, score, body, author="u1", url=None):
        u = url or f"https://example.com/{source}/{cid}"
        item = schema.SourceItem(
            item_id=f"i-{cid}", source=source, title=f"Post {cid}", body="b", url=u,
            container="c", published_at="2026-03-15", date_confidence="high",
            engagement={"score": 100, "num_comments": 10},
            metadata={"top_comments": [{"score": score, "excerpt": body, "author": author}]},
        )
        return schema.Candidate(
            candidate_id=cid, item_id=f"i-{cid}", source=source, title=f"Post {cid}", url=u,
            snippet="s", subquery_labels=["primary"], native_ranks={f"primary:{source}": 1},
            local_relevance=0.9, freshness=90, engagement=80, source_quality=1.0,
            rrf_score=0.02, rerank_score=90, final_score=85, sources=[source], source_items=[item],
        )

    def _report(self, candidates, representative_ids):
        cluster = schema.Cluster(
            cluster_id="cl-1", title="Test cluster",
            candidate_ids=[c.candidate_id for c in candidates],
            representative_ids=representative_ids, sources=["reddit"], score=90,
        )
        return schema.Report(
            topic="test topic", range_from="2026-02-14", range_to="2026-03-16",
            generated_at="2026-03-16T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="gemini", planner_model="m", rerank_model="m"),
            query_plan=schema.QueryPlan(
                intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
                raw_topic="test topic",
                subqueries=[schema.SubQuery(label="primary", search_query="t",
                                            ranking_query="t?", sources=["reddit"])],
                source_weights={"reddit": 1.0}),
            clusters=[cluster], ranked_candidates=candidates,
            items_by_source={"reddit": [c.source_items[0] for c in candidates]},
            errors_by_source={},
        )

    def test_block_renders_with_2plus_comments(self):
        report = self._report(
            [self._cand("a", "reddit", 500, "first funny line here"),
             self._cand("b", "reddit", 50, "second funny line here")],
            representative_ids=["a"])
        text = render.render_compact(report)
        self.assertIn("## Top Community Comments", text)
        self.assertIn("first funny line here", text)

    def test_includes_comment_on_non_representative_candidate(self):
        """The headline fix: a funny comment on a candidate NOT chosen as the
        cluster representative still surfaces (the Kanye 'TurkiYe' case)."""
        rep = self._cand("rep", "reddit", 300, "boring representative comment")
        hidden = self._cand("hidden", "reddit", 1335, "Is anyone surprised it is called TurkiYe")
        report = self._report([rep, hidden], representative_ids=["rep"])  # hidden NOT a rep
        text = render.render_compact(report)
        block = text.split("## Top Community Comments", 1)[1]
        self.assertIn("TurkiYe", block)

    def test_block_inside_evidence_envelope(self):
        report = self._report(
            [self._cand("a", "reddit", 500, "first funny line here"),
             self._cand("b", "reddit", 50, "second funny line here")],
            representative_ids=["a"])
        text = render.render_compact(report)
        open_i = text.index("EVIDENCE FOR SYNTHESIS: read this")
        end_i = text.index("END EVIDENCE FOR SYNTHESIS")
        blk_i = text.index("## Top Community Comments")
        self.assertTrue(open_i < blk_i < end_i, "block must sit inside the EVIDENCE envelope")

    def test_sorted_by_normalized_vote_cross_platform(self):
        # Equal raw 600: Reddit normalizes higher than TikTok (smaller reference),
        # so the Reddit gem ranks above the TikTok line despite same raw count.
        # TikTok 600 is above its 500 min-score threshold so it isn't filtered.
        report = self._report(
            [self._cand("r", "reddit", 600, "reddit gem line here"),
             self._cand("t", "tiktok", 600, "low tiktok line here")],
            representative_ids=["r"])
        block = render.render_compact(report).split("## Top Community Comments", 1)[1]
        self.assertLess(block.index("reddit gem"), block.index("low tiktok"))

    def test_entries_carry_url(self):
        report = self._report(
            [self._cand("a", "reddit", 500, "first funny line here", url="https://reddit.com/x"),
             self._cand("b", "reddit", 50, "second funny line here")],
            representative_ids=["a"])
        block = render.render_compact(report).split("## Top Community Comments", 1)[1]
        self.assertIn("https://reddit.com/x", block)

    def test_omitted_when_fewer_than_two(self):
        report = self._report([self._cand("a", "reddit", 500, "only one comment line")],
                              representative_ids=["a"])
        text = render.render_compact(report)
        self.assertNotIn("## Top Community Comments", text)
        # footer/envelope intact
        self.assertIn("END EVIDENCE FOR SYNTHESIS", text)

    def test_dedupes_identical_comments(self):
        report = self._report(
            [self._cand("a", "reddit", 500, "duplicate line text here"),
             self._cand("b", "reddit", 400, "duplicate line text here"),
             self._cand("c", "reddit", 300, "a distinct third comment line")],
            representative_ids=["a"])
        block = render.render_compact(report).split("## Top Community Comments", 1)[1]
        self.assertEqual(block.count("duplicate line text here"), 1)
        self.assertIn("a distinct third comment line", block)


class TestCommentAttributionPrefix(unittest.TestCase):
    def test_strips_existing_at_prefix_youtube(self):
        # YouTube/TikTok authors already carry '@' from enrichment -> no '@@'.
        self.assertEqual(render._comment_attribution("youtube", "@ml-dz9ww"), "@ml-dz9ww")
        self.assertEqual(render._comment_attribution("tiktok", "@creator"), "@creator")

    def test_adds_prefix_when_missing(self):
        self.assertEqual(render._comment_attribution("youtube", "alice"), "@alice")
        self.assertEqual(render._comment_attribution("reddit", "bob"), "u/bob")

    def test_deleted_author_is_comment(self):
        self.assertEqual(render._comment_attribution("reddit", "[deleted]"), "Comment")
        self.assertEqual(render._comment_attribution("reddit", None), "Comment")


class TestShortenPolymarketTitle(unittest.TestCase):
    def test_fallback_strips_leading_article(self):
        # A long question that falls through to the 6-word fallback must not keep
        # a leading article -> avoids descriptors like "an Anthropic Claude model".
        title = "Will an Anthropic Claude model score at the top of the leaderboard?"
        result = render._shorten_polymarket_title(title)
        lower = result.lower()
        self.assertFalse(lower.startswith("a "))
        self.assertFalse(lower.startswith("an "))
        self.assertFalse(lower.startswith("the "))

    def test_fallback_keeps_non_article_lead(self):
        title = "Anthropic releases a major Claude model update that changes everything soon"
        result = render._shorten_polymarket_title(title)
        self.assertTrue(result.lower().startswith("anthropic"))


class TestPolymarketTopMarkets(unittest.TestCase):
    @staticmethod
    def _pm_item(question, outcome_name, price, volume=1000):
        return schema.SourceItem(
            item_id="pm1",
            source="polymarket",
            title=question,
            body="",
            url="https://polymarket.com/event/x",
            engagement={"volume": volume},
            metadata={
                "question": question,
                "outcome_prices": [(outcome_name, price)],
            },
        )

    def test_article_outcome_is_suppressed(self):
        # The real-world mangled case: descriptor "...score at" + lead name "an".
        # The outcome label is an article -> render "<descriptor> <pct>", no ": an ".
        item = self._pm_item(
            "Will an Anthropic Claude model score at the top of the leaderboard?",
            "an",
            0.19,
        )
        lines = render._polymarket_top_markets([item])
        self.assertEqual(len(lines), 1)
        line = lines[0]
        self.assertNotIn(": an ", line)
        self.assertIn("19%", line)

    def test_yes_outcome_is_suppressed(self):
        item = self._pm_item("Will the bill pass this session?", "Yes", 0.65)
        line = render._polymarket_top_markets([item])[0]
        self.assertNotIn(": Yes ", line)
        self.assertIn("65%", line)

    def test_no_outcome_is_suppressed(self):
        item = self._pm_item("Will the bill pass this session?", "No", 0.30)
        line = render._polymarket_top_markets([item])[0]
        self.assertNotIn(": No ", line)
        self.assertIn("30%", line)

    def test_redundant_lead_token_is_suppressed(self):
        # Outcome name duplicates the descriptor's first token -> no doubling.
        item = self._pm_item("Arizona wins the tournament", "Arizona", 0.42)
        line = render._polymarket_top_markets([item])[0]
        self.assertNotIn(": Arizona ", line)
        # Descriptor itself still carries the name once.
        self.assertIn("Arizona", line)

    def test_named_outcome_is_kept(self):
        # A genuinely informative multi-way outcome name is preserved.
        item = self._pm_item("Who wins the primary?", "Kanye", 0.12)
        line = render._polymarket_top_markets([item])[0]
        self.assertIn(": Kanye ", line)
