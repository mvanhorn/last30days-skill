import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
        clusters=[cluster],
        ranked_candidates=[candidate],
        items_by_source={"grounding": [primary_item], "reddit": [reddit_item]},
        errors_by_source={},
    )


class RenderV3Tests(unittest.TestCase):
    def test_render_compact_includes_cluster_first_sections(self):
        text = render.render_compact(sample_report())
        self.assertIn("# last30days v3.0.0: test topic", text)
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
        self.assertIn("HTTP 400: Bad Request", text)
        self.assertIn("X:", text)


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
                planner_model="gemini-3.1-flash-lite-preview",
                rerank_model="gemini-3.1-flash-lite-preview",
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
        self.assertIn("Comment (500 upvotes):", text)
        self.assertIn("Comment (200 upvotes):", text)
        self.assertIn("Comment (50 upvotes):", text)
        self.assertNotIn("Comment (8 upvotes):", text)
        self.assertNotIn("Comment (3 upvotes):", text)

    def test_reddit_1_comment_renders_1(self):
        """Reddit candidate with 1 comment renders 1."""
        comments = [{"score": 100, "excerpt": "Single comment", "author": "user1"}]
        report = self._make_report_with_comments(top_comments=comments)
        text = render.render_compact(report)
        self.assertIn("Comment (100 upvotes): Single comment", text)

    def test_reddit_0_comments_no_section(self):
        """Reddit candidate with 0 comments renders no comment section."""
        report = self._make_report_with_comments(top_comments=[])
        text = render.render_compact(report)
        self.assertNotIn("Comment (", text)
        self.assertNotIn("upvotes)", text)

    def test_non_reddit_no_comments(self):
        """Non-Reddit candidate doesn't render comments when metadata has none."""
        report = self._make_report_with_comments(source="grounding", top_comments=[])
        text = render.render_compact(report)
        self.assertNotIn("Comment (", text)
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
        self.assertNotIn("Comment (", text)
        self.assertNotIn("upvotes)", text)

    def test_youtube_comments_use_likes_label_and_50_threshold(self):
        comments = [
            {"score": 120, "excerpt": "legit fire tutorial", "author": "alice"},
            {"score": 60, "excerpt": "saved me hours", "author": "bob"},
            {"score": 10, "excerpt": "below threshold", "author": "carol"},
        ]
        report = self._make_report_with_comments(source="youtube", top_comments=comments)
        text = render.render_compact(report)
        self.assertIn("Comment (120 likes): legit fire tutorial", text)
        self.assertIn("Comment (60 likes): saved me hours", text)
        self.assertNotIn("Comment (10 likes)", text)
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
        self.assertIn("Comment (2000 likes): this aged well", text)
        self.assertIn("Comment (600 likes): so real", text)
        self.assertNotIn("Comment (400 likes)", text)
        self.assertNotIn("Comment (50 likes)", text)


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
                planner_model="gemini-3.1-flash-lite-preview",
                rerank_model="gemini-3.1-flash-lite-preview",
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
        self.assertIn("(fun:85)", text)
        self.assertIn("(fun:75)", text)

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

    def test_best_takes_with_1_high_fun_candidate(self):
        """Best Takes section appears even with only 1 candidate above threshold.

        The single-gem floor means a single viral quote is enough to show the block,
        which is essential for the default medium level on most research topics.
        """
        candidates = [
            self._make_candidate("c1", fun_score=80),
            self._make_candidate("c2", fun_score=40),
        ]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertIn("## Best Takes", text)
        self.assertIn("(fun:80)", text)

    def test_best_takes_renders_above_clusters(self):
        """Best Takes section appears before the Ranked Evidence Clusters header."""
        candidates = [
            self._make_candidate("c1", fun_score=85),
            self._make_candidate("c2", fun_score=75),
        ]
        report = self._make_report_with_candidates(candidates)
        text = render.render_compact(report)
        self.assertLess(text.index("## Best Takes"), text.index("## Ranked Evidence Clusters"))

    def test_comment_level_gem_appears_in_best_takes(self):
        """A high-fun top_comment on a low-fun parent candidate still qualifies."""
        item = schema.SourceItem(
            item_id="item-c1",
            source="reddit",
            title="Post c1",
            body="Body.",
            url="https://reddit.com/r/test/comments/c1/",
            container="test",
            published_at="2026-03-15",
            date_confidence="high",
            engagement={"score": 300, "num_comments": 30},
            metadata={
                "top_comments": [{
                    "body": "WHAT?! I reached my monthly limit just reading this post",
                    "excerpt": "WHAT?! I reached my monthly limit just reading this post",
                    "score": 2304,
                    "fun_score": 88.0,
                }],
            },
        )
        candidate = schema.Candidate(
            candidate_id="c1",
            item_id="item-c1",
            source="reddit",
            title="Post c1",
            url="https://reddit.com/r/test/comments/c1/",
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=90,
            engagement=88,
            source_quality=1.0,
            rrf_score=0.02,
            rerank_score=92,
            final_score=90,
            sources=["reddit"],
            source_items=[item],
            fun_score=35.0,  # parent below threshold
        )
        report = self._make_report_with_candidates([candidate])
        text = render.render_compact(report)
        self.assertIn("## Best Takes", text)
        self.assertIn("WHAT?! I reached my monthly limit", text)
        self.assertIn("2,304", text)
        self.assertIn("r/test", text)
        self.assertIn('in "Post c1"', text)

    def test_comment_and_candidate_gems_sorted_by_fun_score(self):
        """Higher fun score comes first, regardless of whether it's a candidate or a comment."""
        item = schema.SourceItem(
            item_id="item-c1",
            source="reddit",
            title="Post c1",
            body="Body.",
            url="https://reddit.com/r/test/comments/c1/",
            container="test",
            engagement={"score": 100},
            metadata={
                "top_comments": [{
                    "body": "higher-ranked comment",
                    "score": 800,
                    "fun_score": 90.0,
                }],
            },
        )
        comment_candidate = schema.Candidate(
            candidate_id="cc",
            item_id="item-c1",
            source="reddit",
            title="Parent thread title",
            url="https://reddit.com/r/test/comments/c1/",
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=90,
            engagement=10,
            source_quality=1.0,
            rrf_score=0.02,
            sources=["reddit"],
            source_items=[item],
            fun_score=30.0,
        )
        lower_candidate = self._make_candidate("cl", fun_score=70)
        report = self._make_report_with_candidates([comment_candidate, lower_candidate])
        text = render.render_compact(report)
        comment_pos = text.find("higher-ranked comment")
        lower_pos = text.find("(fun:70)")
        self.assertNotEqual(comment_pos, -1)
        self.assertNotEqual(lower_pos, -1)
        self.assertLess(comment_pos, lower_pos)


if __name__ == "__main__":
    unittest.main()
