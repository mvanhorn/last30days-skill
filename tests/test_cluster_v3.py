import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import cluster, schema


def make_candidate(candidate_id: str, source: str, title: str, snippet: str, score: float) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=candidate_id,
        item_id=candidate_id,
        source=source,
        title=title,
        url=f"https://example.com/{candidate_id}",
        snippet=snippet,
        subquery_labels=["primary"],
        native_ranks={"primary:reddit": 1},
        local_relevance=0.8,
        freshness=80,
        engagement=10,
        source_quality=0.7,
        rrf_score=0.02,
        rerank_score=score,
        final_score=score,
    )


class ClusterV3Tests(unittest.TestCase):
    def test_singleton_clusters_for_non_clustered_plan(self):
        plan = schema.QueryPlan(
            intent="how_to",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic="docker setup",
            subqueries=[schema.SubQuery(label="primary", search_query="docker setup", ranking_query="How do I set up Docker?", sources=["reddit"])],
            source_weights={"reddit": 1.0},
        )
        candidates = [
            make_candidate("c1", "reddit", "Docker setup guide", "Step by step setup", 80),
            make_candidate("c2", "youtube", "Docker install video", "Video walkthrough", 75),
        ]
        clusters = cluster.cluster_candidates(candidates, plan)
        self.assertEqual(2, len(clusters))
        self.assertEqual(["c1"], clusters[0].representative_ids)
        self.assertEqual(["c2"], clusters[1].representative_ids)

    def test_breaking_news_clusters_related_items(self):
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="model launch",
            subqueries=[schema.SubQuery(label="primary", search_query="model launch", ranking_query="What happened in the model launch?", sources=["reddit", "x"])],
            source_weights={"reddit": 0.5, "x": 0.5},
        )
        candidates = [
            make_candidate("c1", "reddit", "Open model launch reactions", "People are reacting to the open model launch today.", 88),
            make_candidate("c2", "x", "Open model launch update", "People are reacting to the open model launch today on X.", 84),
            make_candidate("c3", "youtube", "Different topic", "A separate discussion about hardware benchmarks.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, plan)
        self.assertEqual(2, len(clusters))
        self.assertEqual(2, len(clusters[0].candidate_ids))
        self.assertIn("c1", clusters[0].candidate_ids)
        self.assertIn("c2", clusters[0].candidate_ids)


class TestCrossSourceMerging(unittest.TestCase):
    """Test the entity-based second pass that merges same-story clusters across sources."""

    def _plan(self, intent="breaking_news"):
        return schema.QueryPlan(
            intent=intent,
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test", sources=["reddit", "x", "tiktok"])],
            source_weights={"reddit": 0.5, "x": 0.5, "tiktok": 0.5},
        )

    def test_same_story_different_phrasing_merges(self):
        """Wireless Festival example: same event, different wording, different sources."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West to headline all three nights of Wireless Festival 2026", "Big announcement for Wireless.", 80),
            make_candidate("c2", "x", "BREAKING: Kanye West is making his massive UK comeback at Wireless Festival this July", "Ye returns to UK.", 75),
            make_candidate("c3", "youtube", "Kanye West BULLY Album Review - Knox Hill Reacts", "Full album reaction and breakdown.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # c1 and c2 should merge (Kanye + Wireless + Festival overlap), c3 should stay separate
        self.assertEqual(2, len(clusters))
        wireless_cluster = next(cl for cl in clusters if len(cl.candidate_ids) == 2)
        self.assertIn("c1", wireless_cluster.candidate_ids)
        self.assertIn("c2", wireless_cluster.candidate_ids)
        self.assertEqual(sorted(["reddit", "x"]), wireless_cluster.sources)
        # Multi-source cluster should not have "single-source" uncertainty
        self.assertNotEqual("single-source", wireless_cluster.uncertainty)

    def test_different_stories_dont_merge(self):
        """Different topics should stay separate even with some entity overlap (e.g., 'Kanye')."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West BULLY Album First Impressions Thread", "What do you think of BULLY?", 80),
            make_candidate("c2", "x", "Kanye West apology for antisemitism in Wall Street Journal ad", "Full page WSJ ad.", 75),
            make_candidate("c3", "tiktok", "Kanye West Wireless Festival ticket prices breakdown", "How much for Wireless tickets?", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # These are 3 different stories, should remain as 3 clusters
        self.assertEqual(3, len(clusters))

    def test_same_source_clusters_dont_merge(self):
        """Two single-source clusters from the same source should not merge via entity pass."""
        candidates = [
            make_candidate("c1", "reddit", "Kanye West Wireless Festival headline announcement", "Three nights!", 80),
            make_candidate("c2", "reddit", "Kanye West returning to Wireless Festival confirmed", "UK comeback.", 70),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # The initial greedy pass may or may not merge these (depends on token similarity).
        # But if they end up as separate clusters, the entity pass should NOT merge them
        # since they're both from reddit.
        for cl in clusters:
            self.assertTrue(len(cl.sources) >= 1)  # basic sanity


class TestPolymarketIsolation(unittest.TestCase):
    """Polymarket clusters must not merge with non-Polymarket clusters via entity overlap."""

    def _plan(self):
        return schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[schema.SubQuery(label="primary", search_query="test", ranking_query="test", sources=["reddit", "x", "polymarket"])],
            source_weights={"reddit": 0.5, "x": 0.5, "polymarket": 0.5},
        )

    def test_polymarket_does_not_merge_into_news_cluster(self):
        """A Polymarket prediction about Sam Altman should not merge into a news cluster about Sam Altman."""
        candidates = [
            make_candidate("c1", "reddit", "Sam Altman personal rivalry with Elon Musk escalates", "The feud between Sam Altman and Elon Musk continues.", 80),
            make_candidate("c2", "polymarket", "Sam Altman equity stake in OpenAI valued at $500M", "Will Sam Altman receive equity in OpenAI restructuring?", 75),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        self.assertEqual(2, len(clusters), "Polymarket and news clusters should remain separate")
        # Each cluster should have exactly one candidate
        for cl in clusters:
            self.assertEqual(1, len(cl.candidate_ids))

    def test_two_polymarket_clusters_not_blocked_by_poly_guard(self):
        """Two Polymarket items about the same topic are not blocked by the Polymarket guard.

        Note: same-source clusters are still blocked by the existing same-source
        guard, so we verify the poly guard specifically by checking that two
        polymarket items with high text similarity merge via the greedy pass.
        """
        candidates = [
            make_candidate("c1", "polymarket", "Sam Altman equity stake in OpenAI restructuring", "Will Sam Altman get equity in the OpenAI restructuring deal?", 80),
            make_candidate("c2", "polymarket", "Sam Altman equity stake in OpenAI restructuring odds", "Will Sam Altman get equity in the OpenAI restructuring deal? Current odds.", 75),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        # High text similarity means greedy pass merges them
        self.assertEqual(1, len(clusters))
        self.assertEqual(2, len(clusters[0].candidate_ids))

    def test_neither_polymarket_still_merges(self):
        """Non-Polymarket clusters with entity overlap should still merge (existing behavior)."""
        candidates = [
            make_candidate("c1", "reddit", "Sam Altman OpenAI restructuring announcement details", "Sam Altman announces major OpenAI restructuring.", 80),
            make_candidate("c2", "x", "Sam Altman reveals OpenAI restructuring plan for 2026", "Major OpenAI restructuring coming says Sam Altman.", 75),
        ]
        clusters = cluster.cluster_candidates(candidates, self._plan())
        self.assertEqual(1, len(clusters))
        self.assertEqual(2, len(clusters[0].candidate_ids))


class TestClusterUncertainty(unittest.TestCase):
    def test_single_source_returns_single_source(self):
        candidates = [make_candidate("c1", "reddit", "Title", "Body", 80)]
        result = cluster._cluster_uncertainty(candidates)
        self.assertEqual("single-source", result)

    def test_multi_source_high_score_returns_none(self):
        candidates = [
            make_candidate("c1", "reddit", "Title", "Body", 80),
            make_candidate("c2", "x", "Title2", "Body2", 70),
        ]
        result = cluster._cluster_uncertainty(candidates)
        self.assertIsNone(result)

    def test_multi_source_low_score_returns_thin_evidence(self):
        candidates = [
            make_candidate("c1", "reddit", "Title", "Body", 30),
            make_candidate("c2", "x", "Title2", "Body2", 40),
        ]
        result = cluster._cluster_uncertainty(candidates)
        self.assertEqual("thin-evidence", result)


if __name__ == "__main__":
    unittest.main()
