"""Reddit engagement keepers: the month's most-discussed on-topic threads survive
per-stream truncation, and the fused pool reserves slots for them."""

from lib import fusion, pipeline, schema


def _reddit_item(i, *, score=1, ncmt=0, relevance=0.5, title="Kanye West thread", rank=None):
    item = schema.SourceItem(
        item_id=f"R{i}",
        source="reddit",
        title=f"{title} {i}",
        body=f"{title} {i}",
        url=f"https://www.reddit.com/r/Kanye/comments/t{i:03d}/",
        engagement={"score": score, "num_comments": ncmt},
    )
    item.local_relevance = relevance
    item.local_rank_score = rank if rank is not None else relevance
    return item


def _stream(n=36):
    items = [_reddit_item(i, relevance=0.5 - i * 0.01, rank=0.5 - i * 0.01) for i in range(n)]
    return items


class TestStreamKeepers:
    def test_high_engagement_thread_ranked_23rd_survives_truncation(self):
        items = _stream()
        big = _reddit_item(99, score=16180, ncmt=1450, relevance=0.19, rank=0.36,
                           title="Kanye West Heads to Putin's Russia")
        items.insert(22, big)
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "Kanye West")
        assert len(kept) == 12
        assert big in kept

    def test_zero_relevance_thread_is_not_kept(self):
        items = _stream()
        big = _reddit_item(99, score=16180, ncmt=1450, relevance=0.0, rank=0.0, title="Something else")
        items.insert(22, big)
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "Kanye West")
        assert big not in kept

    def test_thread_without_entity_head_token_is_not_kept(self):
        items = _stream()
        big = _reddit_item(99, score=16180, ncmt=1450, relevance=0.19, rank=0.36,
                           title="Ye Heads to Putin's Russia")
        items.insert(22, big)
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "Kanye West")
        assert big not in kept

    def test_short_stream_is_returned_whole(self):
        items = _stream(2)
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "Kanye West")
        assert kept == items

    def test_non_reddit_stream_is_plain_truncation(self):
        items = _stream()
        for it in items:
            it.source = "x"
        kept = pipeline._apply_reddit_stream_keepers("x", items, 12, "Kanye West")
        assert kept == items[:12]

    def test_generic_head_token_requires_higher_relevance(self):
        items = _stream()
        big = _reddit_item(99, score=9000, ncmt=900, relevance=0.15, rank=0.2,
                           title="AI second brain setups")
        items.insert(22, big)
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "AI second brain")
        assert big not in kept
        big.local_relevance = 0.3
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 12, "AI second brain")
        assert big in kept


def _cand(i, source, rrf, *, relevance=0.5, score=0, ncmt=0, title="Kanye West item"):
    item = schema.SourceItem(
        item_id=f"{source}{i}",
        source=source,
        title=f"{title} {i}",
        body=f"{title} {i}",
        url=f"https://example.com/{source}/{i}",
        engagement={"score": score, "num_comments": ncmt},
    )
    return schema.Candidate(
        candidate_id=f"{source}-{i}",
        item_id=item.item_id,
        source=source,
        title=item.title,
        url=item.url,
        snippet="",
        subquery_labels=["primary"],
        native_ranks={f"primary:{source}": i + 1},
        local_relevance=relevance,
        freshness=80,
        engagement=5.0,
        source_quality=0.7,
        rrf_score=rrf,
        sources=[source],
        source_items=[item],
    )


class TestPoolReservation:
    def _fused(self):
        fused = [_cand(i, "tiktok", 0.05 - i * 0.0005) for i in range(45)]
        fused += [_cand(i, "reddit", 0.001 - i * 0.00001, score=1, ncmt=1) for i in range(5)]
        return fused

    def test_top_engagement_reddit_candidate_is_reserved_at_default_depth(self):
        fused = self._fused()
        big = _cand(99, "reddit", 0.0001, relevance=0.19, score=16180, ncmt=1450)
        fused.append(big)
        pool = fusion._diversify_pool(fused, 40, entity="kanye west")
        assert len(pool) == 40
        assert big in pool

    def test_out_of_window_reddit_candidate_is_not_reserved(self):
        fused = self._fused()
        big = _cand(99, "reddit", 0.0001, relevance=0.19, score=16180, ncmt=1450)
        big.metadata["range_from"] = "2026-08-01"
        big.metadata["range_to"] = "2026-08-31"
        big.source_items[0].published_at = "2025-01-01"
        big.source_items[0].date_confidence = "high"
        fused.append(big)
        pool = fusion._diversify_pool(fused, 40, entity="kanye west")
        assert big not in pool

    def test_entity_miss_reddit_candidate_is_not_reserved(self):
        fused = self._fused()
        big = _cand(99, "reddit", 0.0001, relevance=0.19, score=16180, ncmt=1450, title="Unrelated viral thread")
        fused.append(big)
        pool = fusion._diversify_pool(fused, 40, entity="kanye west")
        assert big not in pool

    def test_reservation_scales_with_pool_limit(self):
        assert fusion._reddit_reserve_for(15) == 2
        assert fusion._reddit_reserve_for(40) == 3
        assert fusion._reddit_reserve_for(60) == 4


class TestKeeperAndReservationBounds:
    def test_keepers_never_exceed_the_stream_limit(self):
        items = [_reddit_item(i, score=1000 - i, ncmt=50, relevance=0.5, rank=0.5 - i * 0.01) for i in range(10)]
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 1, "Kanye West")
        assert len(kept) == 1
        kept = pipeline._apply_reddit_stream_keepers("reddit", items, 0, "Kanye West")
        assert kept == []

    def test_reservation_survives_many_qualifying_sources(self):
        fused = []
        for source in ("tiktok", "x", "youtube", "hackernews", "grounding", "instagram", "github", "linkedin"):
            fused += [_cand(i, source, 0.05 - i * 0.0005) for i in range(4)]
        big = _cand(99, "reddit", 0.0001, relevance=0.19, score=16180, ncmt=1450)
        fused.append(big)
        pool = fusion._diversify_pool(fused, 15, entity="kanye west")
        assert len(pool) == 15
        assert big in pool
