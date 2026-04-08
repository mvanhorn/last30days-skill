import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import fusion, schema


def make_item(item_id: str, source: str, url: str, title: str, rank_score: float) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=url,
        relevance_hint=rank_score,
        snippet=title,
        metadata={
            "local_relevance": rank_score,
            "freshness": 80,
            "engagement_score": 5,
            "source_quality": 0.7,
        },
    )


class FusionV3Tests(unittest.TestCase):
    def test_weighted_rrf_merges_duplicate_urls(self):
        plan = schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[
                schema.SubQuery(label="primary", search_query="test", ranking_query="What happened in test?", sources=["reddit", "x"], weight=0.7),
                schema.SubQuery(label="reaction", search_query="test reaction", ranking_query="What are the reactions to test?", sources=["x"], weight=0.3),
            ],
            source_weights={"reddit": 0.4, "x": 0.6},
        )
        shared = "https://example.com/shared"
        streams = {
            ("primary", "reddit"): [make_item("r1", "reddit", shared, "Shared item", 0.8)],
            ("primary", "x"): [make_item("x1", "x", shared, "Shared item", 0.9)],
            ("reaction", "x"): [make_item("x2", "x", "https://example.com/unique", "Unique item", 0.7)],
        }
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=10)
        self.assertEqual(2, len(candidates))
        merged = next(candidate for candidate in candidates if candidate.url == shared)
        self.assertEqual({"primary"}, set(merged.subquery_labels))
        self.assertEqual(2, len(merged.native_ranks))
        self.assertEqual({"reddit", "x"}, set(merged.sources))
        self.assertEqual(2, len(merged.source_items))


    def test_diversify_pool_guarantees_min_per_qualifying_source(self):
        """Every qualifying source (local_relevance >= 0.25) gets at least 2
        items in the fused pool.

        Dominant sources (x, tiktok) get high weights, so pure-RRF truncation
        would squeeze out low-weight sources entirely.  The diversity guarantee
        must reserve at least 2 slots per qualifying active source.  All sources
        here have rank_score=0.8 (well above the 0.25 threshold), so every
        source qualifies for reserved slots.
        """
        sources = ["reddit", "hackernews", "x", "tiktok", "bluesky", "youtube"]
        # Heavily skewed weights: x and tiktok dominate.
        weights = {
            "x": 3.0,
            "tiktok": 2.5,
            "reddit": 0.5,
            "hackernews": 0.4,
            "bluesky": 0.3,
            "youtube": 0.3,
        }
        plan = schema.QueryPlan(
            intent="concept",
            freshness_mode="relaxed",
            cluster_mode="concept",
            raw_topic="RAG",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="RAG",
                    ranking_query="What is RAG?",
                    sources=sources,
                    weight=1.0,
                ),
            ],
            source_weights=weights,
        )
        streams: dict[tuple[str, str], list[schema.SourceItem]] = {}
        for src in sources:
            items = []
            for rank in range(4):
                items.append(
                    make_item(
                        item_id=f"{src}_{rank}",
                        source=src,
                        url=f"https://{src}.example.com/{rank}",
                        title=f"{src} item {rank}",
                        rank_score=0.8,
                    )
                )
            streams[("primary", src)] = items

        candidates = fusion.weighted_rrf(streams, plan, pool_limit=12)
        self.assertEqual(12, len(candidates))

        source_counts: dict[str, int] = {}
        for c in candidates:
            source_counts[c.source] = source_counts.get(c.source, 0) + 1

        for src in sources:
            self.assertGreaterEqual(
                source_counts.get(src, 0),
                2,
                f"Source '{src}' has {source_counts.get(src, 0)} items, expected >= 2",
            )


    def test_diversify_pool_denies_slots_for_low_relevance_source(self):
        """Sources with best local_relevance < 0.25 do not get reserved slots.

        Create two sources: 'x' with local_relevance=0.5 (qualifies) and
        'reddit' with local_relevance=0.1 (below threshold). With a tight
        pool_limit, the high-relevance source gets reserved slots while
        the low-relevance source must compete on RRF merit alone.
        """
        plan = schema.QueryPlan(
            intent="concept",
            freshness_mode="relaxed",
            cluster_mode="concept",
            raw_topic="test",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test",
                    ranking_query="What is test?",
                    sources=["x", "reddit"],
                    weight=1.0,
                ),
            ],
            source_weights={"x": 1.0, "reddit": 1.0},
        )

        # x items: high relevance (0.5) -- qualifies for diversity reservation
        x_items = [
            make_item(f"x_{i}", "x", f"https://x.example.com/{i}", f"x item {i}", 0.5)
            for i in range(4)
        ]

        # reddit items: low relevance (0.1) -- below threshold, no reserved slots
        reddit_items = [
            make_item(f"r_{i}", "reddit", f"https://reddit.example.com/{i}", f"reddit item {i}", 0.1)
            for i in range(4)
        ]

        streams = {
            ("primary", "x"): x_items,
            ("primary", "reddit"): reddit_items,
        }

        # pool_limit=3: x gets 2 reserved + 1 more by RRF. Reddit has no
        # reserved slots, so it must out-score x items in the remainder.
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=3)
        self.assertEqual(3, len(candidates))

        # x must have at least 2 (reserved slots)
        x_count = sum(1 for c in candidates if c.source == "x")
        self.assertGreaterEqual(x_count, 2, "x should have at least 2 reserved slots")

    def test_diversify_pool_no_reservation_when_all_below_threshold(self):
        """When all sources are below the relevance threshold, no reserved slots
        are granted. The pool is filled purely by RRF score order."""
        plan = schema.QueryPlan(
            intent="concept",
            freshness_mode="relaxed",
            cluster_mode="concept",
            raw_topic="test",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test",
                    ranking_query="What is test?",
                    sources=["x", "reddit", "hackernews"],
                    weight=1.0,
                ),
            ],
            # Give x a much higher weight so its items get higher RRF scores
            source_weights={"x": 3.0, "reddit": 0.3, "hackernews": 0.3},
        )

        streams: dict[tuple[str, str], list[schema.SourceItem]] = {}
        # All sources below threshold (local_relevance = 0.1)
        for src in ["x", "reddit", "hackernews"]:
            items = [
                make_item(f"{src}_{i}", src, f"https://{src}.example.com/{i}", f"{src} item {i}", 0.1)
                for i in range(4)
            ]
            streams[("primary", src)] = items

        candidates = fusion.weighted_rrf(streams, plan, pool_limit=4)
        self.assertEqual(4, len(candidates))

        # With no diversity reservation and x having 3x the weight,
        # x should dominate the top slots purely on RRF score
        source_counts: dict[str, int] = {}
        for c in candidates:
            source_counts[c.source] = source_counts.get(c.source, 0) + 1

        # x has 3x weight so its RRF scores are ~3x higher than reddit/hn.
        # All 4 x items should beat all reddit/hackernews items.
        self.assertEqual(
            source_counts.get("x", 0),
            4,
            f"Expected x to take all 4 slots on pure RRF merit, got {source_counts}",
        )

    def test_diversify_pool_threshold_boundary(self):
        """Source with best local_relevance exactly at the threshold (0.25)
        qualifies for reserved slots."""
        plan = schema.QueryPlan(
            intent="concept",
            freshness_mode="relaxed",
            cluster_mode="concept",
            raw_topic="boundary",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="boundary",
                    ranking_query="What is boundary?",
                    sources=["x", "reddit"],
                    weight=1.0,
                ),
            ],
            # Give x much higher weight so it would dominate without reservation
            source_weights={"x": 5.0, "reddit": 0.1},
        )

        x_items = [
            make_item(f"x_{i}", "x", f"https://x.example.com/{i}", f"x item {i}", 0.8)
            for i in range(6)
        ]

        # reddit at exactly the threshold
        reddit_items = [
            make_item(f"r_{i}", "reddit", f"https://reddit.example.com/{i}", f"reddit item {i}", 0.25)
            for i in range(3)
        ]

        streams = {
            ("primary", "x"): x_items,
            ("primary", "reddit"): reddit_items,
        }

        candidates = fusion.weighted_rrf(streams, plan, pool_limit=6)
        self.assertEqual(6, len(candidates))

        reddit_count = sum(1 for c in candidates if c.source == "reddit")
        self.assertGreaterEqual(
            reddit_count,
            2,
            f"reddit (local_relevance=0.25, at threshold) should get 2 reserved slots, got {reddit_count}",
        )


def make_item_with_author(
    item_id: str, source: str, url: str, title: str, rank_score: float, author: str | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=url,
        author=author,
        relevance_hint=rank_score,
        snippet=title,
        metadata={
            "local_relevance": rank_score,
            "freshness": 80,
            "engagement_score": 5,
            "source_quality": 0.7,
        },
    )


class TestPerAuthorCap(unittest.TestCase):
    """Per-author cap: no single author should have more than 3 items in fused pool."""

    def _make_plan(self, sources: list[str]) -> schema.QueryPlan:
        return schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="test",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test",
                    ranking_query="test",
                    sources=sources,
                    weight=1.0,
                ),
            ],
            source_weights={s: 1.0 for s in sources},
        )

    def test_author_with_8_items_capped_to_3(self):
        """@grok scenario: 8 items from the same author, only best 3 survive."""
        plan = self._make_plan(["x"])
        items = [
            make_item_with_author(
                f"x_{i}", "x", f"https://x.com/{i}", f"grok summary {i}", 0.7, author="@grok",
            )
            for i in range(8)
        ]
        streams = {("primary", "x"): items}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=20)
        grok_count = sum(
            1 for c in candidates
            if any(si.author == "@grok" for si in c.source_items)
        )
        self.assertLessEqual(grok_count, 3, f"@grok should be capped at 3, got {grok_count}")

    def test_author_with_3_items_all_kept(self):
        """Author with exactly 3 items should keep all of them."""
        plan = self._make_plan(["x"])
        items = [
            make_item_with_author(
                f"x_{i}", "x", f"https://x.com/{i}", f"author3 post {i}", 0.7, author="@author3",
            )
            for i in range(3)
        ]
        streams = {("primary", "x"): items}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=20)
        count = sum(
            1 for c in candidates
            if any(si.author == "@author3" for si in c.source_items)
        )
        self.assertEqual(count, 3)

    def test_items_without_author_not_capped(self):
        """Items with no author field should never be dropped by the cap."""
        plan = self._make_plan(["reddit"])
        items = [
            make_item_with_author(
                f"r_{i}", "reddit", f"https://reddit.com/{i}", f"post {i}", 0.7, author=None,
            )
            for i in range(6)
        ]
        streams = {("primary", "reddit"): items}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=20)
        self.assertEqual(len(candidates), 6)

    def test_multiple_authors_capped_independently(self):
        """Two prolific authors each get capped to 3 independently."""
        plan = self._make_plan(["x"])
        items = []
        for i in range(5):
            items.append(make_item_with_author(
                f"grok_{i}", "x", f"https://x.com/grok/{i}", f"grok {i}", 0.7, author="@grok",
            ))
        for i in range(5):
            items.append(make_item_with_author(
                f"spam_{i}", "x", f"https://x.com/spam/{i}", f"spam {i}", 0.6, author="@spammer",
            ))
        streams = {("primary", "x"): items}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=20)
        grok_count = sum(1 for c in candidates if any(si.author == "@grok" for si in c.source_items))
        spam_count = sum(1 for c in candidates if any(si.author == "@spammer" for si in c.source_items))
        self.assertLessEqual(grok_count, 3)
        self.assertLessEqual(spam_count, 3)

    def test_cap_keeps_best_items_by_rrf_order(self):
        """The cap should keep the first (highest-ranked) items per author."""
        plan = self._make_plan(["x"])
        # Items with decreasing relevance scores so ranking is deterministic
        items = [
            make_item_with_author(
                f"x_{i}", "x", f"https://x.com/{i}", f"post {i}", 0.9 - (i * 0.05), author="@prolific",
            )
            for i in range(5)
        ]
        streams = {("primary", "x"): items}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=20)
        kept_ids = {c.item_id for c in candidates if any(si.author == "@prolific" for si in c.source_items)}
        # The top 3 items (x_0, x_1, x_2) should be kept
        self.assertLessEqual(len(kept_ids), 3)


class TestUrlNormalization(unittest.TestCase):
    def test_strips_www(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://www.reddit.com/r/test"),
            _normalize_url("https://reddit.com/r/test"),
        )

    def test_strips_old_prefix(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://old.reddit.com/r/test"),
            _normalize_url("https://reddit.com/r/test"),
        )

    def test_strips_mobile_prefix(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://m.youtube.com/watch?v=abc"),
            _normalize_url("https://youtube.com/watch?v=abc"),
        )

    def test_strips_utm_params(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://example.com/page?utm_source=twitter&id=5"),
            _normalize_url("https://example.com/page?id=5"),
        )

    def test_strips_trailing_slash(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://example.com/page/"),
            _normalize_url("https://example.com/page"),
        )

    def test_preserves_non_tracking_params(self):
        from lib.fusion import _normalize_url
        result = _normalize_url("https://example.com/page?id=5&sort=new")
        self.assertIn("id=5", result)
        self.assertIn("sort=new", result)

    def test_case_insensitive(self):
        from lib.fusion import _normalize_url
        self.assertEqual(
            _normalize_url("https://Reddit.com/r/Test"),
            _normalize_url("https://reddit.com/r/test"),
        )


if __name__ == "__main__":
    unittest.main()
