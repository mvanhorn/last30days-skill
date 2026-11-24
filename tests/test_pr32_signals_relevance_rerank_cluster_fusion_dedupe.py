"""Regression tests for PR 32: scoring monotonicity, video floors, tie-aware rescue,
cluster partitioning/merge safety, Reddit score merge, URL dedupe, and generic relevance cap.

Covers:
- HIGH-097: Monotonic engagement scoring for threads/pinterest/linkedin/linkedin_profile/github/xiaohongshu & generic fallback
- HIGH-098: Enforce 1,000-view TikTok/Instagram spam floor on single-source streams + safe view coercion
- HIGH-102: Tie-aware percentile rank in _apply_engagement_rescue (especially 100-cap viral ties) + skip <=0
- HIGH-103: Partition out-of-window clusters below in-window, in-window cluster score, sorted merged candidate_ids, empty uncertainty guard
- HIGH-104: Preserve numeric Reddit scores across archive/live/unlabelled merges and allow Arctic backfill on None-score copies
- HIGH-109: Normalized URL dedupe across all sources (including HN hn_url, Reddit thread_key, empty-text items, mixed Polymarket)
- HIGH-110: Generic-only relevance cap strictly below RELEVANCE_FLOOR (0.10) and prune_low_relevance floors
"""

from __future__ import annotations

import math

import pytest

from lib import cluster, dedupe, fusion, reddit_refs, relevance, rerank, schema, signals


def _source_item(
    item_id: str,
    source: str,
    *,
    title: str = "Title",
    body: str = "Body",
    url: str | None = None,
    author: str | None = None,
    container: str | None = None,
    published_at: str | None = "2026-10-20",
    date_confidence: str = "high",
    engagement: dict | None = None,
    metadata: dict | None = None,
    snippet: str = "",
    local_relevance: float | None = None,
    freshness: int | None = None,
    source_quality: float | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=body,
        url=f"https://example.com/{source}/{item_id}" if url is None else url,
        author=author,
        container=container,
        published_at=published_at,
        date_confidence=date_confidence,
        engagement=dict(engagement) if engagement is not None else {},
        snippet=snippet,
        metadata=dict(metadata) if metadata is not None else {},
        local_relevance=local_relevance,
        freshness=freshness,
        source_quality=source_quality,
    )


def _plan(topic: str = "Hermes Agent", sources: list[str] | None = None, intent: str = "breaking_news") -> schema.QueryPlan:
    srcs = sources or ["reddit", "x", "youtube"]
    return schema.QueryPlan(
        intent=intent,
        freshness_mode="strict_recent",
        cluster_mode="story",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=topic,
                sources=srcs,
                weight=1.0,
            ),
            schema.SubQuery(
                label="secondary",
                search_query=f"{topic} update",
                ranking_query=f"{topic} update",
                sources=srcs,
                weight=0.8,
            ),
        ],
        source_weights={s: 1.0 for s in srcs},
    )


def _candidate(
    cid: str,
    *,
    source: str = "x",
    title: str = "Hermes Agent release",
    snippet: str = "Hermes Agent notes",
    author: str | None = "observer",
    published_at: str | None = "2026-10-20",
    date_confidence: str = "high",
    range_from: str = "2026-10-01",
    range_to: str = "2026-10-31",
    engagement: float | int | None = 50,
    final_score: float = 50.0,
    local_relevance: float = 0.7,
) -> schema.Candidate:
    item = _source_item(
        cid,
        source,
        title=title,
        body=f"{title} {snippet}",
        author=author,
        published_at=published_at,
        date_confidence=date_confidence,
        snippet=snippet,
    )
    return schema.Candidate(
        candidate_id=cid,
        item_id=cid,
        source=source,
        title=title,
        url=item.url,
        snippet=snippet,
        subquery_labels=["primary"],
        native_ranks={f"primary:{source}": 1},
        local_relevance=local_relevance,
        freshness=80,
        engagement=engagement,
        source_quality=0.68,
        rrf_score=0.02,
        sources=[source],
        source_items=[item],
        final_score=final_score,
        metadata={"range_from": range_from, "range_to": range_to},
    )


# ---------------------------------------------------------------------------
# HIGH-097: Engagement monotonicity across active sources & generic fallback
# ---------------------------------------------------------------------------


class TestHigh097EngagementMonotonicity:
    def test_every_scored_source_has_explicit_engagement_formula(self):
        custom_sources = {"reddit", "youtube", "tiktok", "instagram", "digg"}
        missing = [
            src for src in signals._ENGAGEMENT_LOG_REFERENCE
            if src not in custom_sources and src not in signals.ENGAGEMENT_WEIGHTS
        ]
        assert missing == []

    @pytest.mark.parametrize(
        ("source", "base_eng", "higher_eng"),
        [
            (
                "threads",
                {"likes": 1000, "replies": 0, "reposts": 0, "quotes": 0},
                {"likes": 1000, "replies": 1, "reposts": 1, "quotes": 1},
            ),
            (
                "pinterest",
                {"saves": 1000, "comments": 0},
                {"saves": 1000, "comments": 1},
            ),
            (
                "linkedin",
                {"likes": 500, "comments": 0, "reposts": 0},
                {"likes": 500, "comments": 2, "reposts": 1},
            ),
            (
                "linkedin_profile",
                {"likes": 500, "comments": 0, "shares": 0},
                {"likes": 500, "comments": 2, "shares": 1},
            ),
            (
                "github",
                {"reactions": 100, "comments": 0},
                {"reactions": 100, "comments": 1},
            ),
            (
                "xiaohongshu",
                {"likes": 1000, "comments": 0, "favorites": 0},
                {"likes": 1000, "comments": 1, "favorites": 1},
            ),
        ],
    )
    def test_adding_secondary_engagement_never_lowers_score(self, source, base_eng, higher_eng):
        base_item = _source_item("base", source, engagement=base_eng)
        higher_item = _source_item("higher", source, engagement=higher_eng)

        base_raw = signals.engagement_raw(base_item)
        higher_raw = signals.engagement_raw(higher_item)
        assert base_raw is not None and higher_raw is not None
        assert higher_raw > base_raw

        base_score = signals.engagement_score(base_item)
        higher_score = signals.engagement_score(higher_item)
        assert base_score is not None and higher_score is not None
        assert higher_score > base_score

    @pytest.mark.parametrize(
        ("source", "partial_eng", "expanded_eng"),
        [
            ("threads", {"likes": 1000}, {"likes": 1000, "replies": 1}),
            ("pinterest", {"saves": 1000}, {"saves": 1000, "comments": 1}),
            ("linkedin", {"likes": 500}, {"likes": 500, "comments": 1}),
            ("linkedin_profile", {"likes": 500}, {"likes": 500, "comments": 1}),
            ("github", {"reactions": 100}, {"reactions": 100, "comments": 1}),
            ("xiaohongshu", {"likes": 1000}, {"likes": 1000, "favorites": 1}),
        ],
    )
    def test_partial_dict_adding_field_is_strictly_increasing(self, source, partial_eng, expanded_eng):
        partial_item = _source_item("p", source, engagement=partial_eng)
        expanded_item = _source_item("e", source, engagement=expanded_eng)
        assert signals.engagement_raw(expanded_item) > signals.engagement_raw(partial_item)
        assert signals.engagement_score(expanded_item) > signals.engagement_score(partial_item)

    @pytest.mark.parametrize(
        ("source", "zero_eng"),
        [
            ("threads", {"likes": 0, "replies": 0, "reposts": 0, "quotes": 0}),
            ("pinterest", {"saves": 0, "comments": 0}),
            ("linkedin", {"likes": 0, "comments": 0, "reposts": 0}),
            ("linkedin_profile", {"likes": 0, "comments": 0, "shares": 0}),
            ("github", {"reactions": 0, "comments": 0}),
            ("xiaohongshu", {"likes": 0, "comments": 0, "favorites": 0}),
        ],
    )
    def test_all_zero_engagement_returns_none(self, source, zero_eng):
        item = _source_item("z", source, engagement=zero_eng)
        assert signals.engagement_raw(item) is None
        assert signals.engagement_score(item) is None

    def test_generic_engagement_is_monotonic_on_sparse_and_fixed_schemas(self):
        sparse = _source_item("gs", "custom_src", engagement={"likes": 1000})
        with_none = _source_item("g0", "custom_src", engagement={"likes": 1000, "replies": None})
        with_zero = _source_item("g1", "custom_src", engagement={"likes": 1000, "replies": 0})
        with_one = _source_item("g2", "custom_src", engagement={"likes": 1000, "replies": 1})
        with_two = _source_item("g3", "custom_src", engagement={"likes": 1000, "replies": 1, "reposts": 1})

        raw_sparse = signals.engagement_raw(sparse)
        raw_none = signals.engagement_raw(with_none)
        raw_zero = signals.engagement_raw(with_zero)
        raw_one = signals.engagement_raw(with_one)
        raw_two = signals.engagement_raw(with_two)
        assert raw_sparse == pytest.approx(math.log1p(1000))
        assert raw_none == pytest.approx(raw_sparse)
        assert raw_zero == pytest.approx(raw_sparse)
        assert raw_one > raw_zero
        assert raw_two > raw_one

        # Non-numeric / bool / NaN keys do not affect the score or crash.
        mixed = _source_item(
            "gm",
            "custom_src",
            engagement={"a": 100, "flag": True, "label": "hot", "nan": float("nan")},
        )
        assert signals.engagement_raw(mixed) == pytest.approx(math.log1p(100))

        all_zero = _source_item("gz", "custom_src", engagement={"a": 0, "b": -3, "c": None})
        assert signals.engagement_raw(all_zero) is None

    def test_github_person_velocity_card_keeps_monotonic_engagement_and_snapshot_stays_none(self):
        vel_low = _source_item(
            "gh-vel-1",
            "github",
            engagement={"merged_prs": 10, "total_prs": 0},
            metadata={"labels": ["person-profile", "velocity"], "is_pr": False},
        )
        vel_mid = _source_item(
            "gh-vel-2",
            "github",
            engagement={"merged_prs": 10, "total_prs": 12},
            metadata={"labels": ["person-profile", "velocity"], "is_pr": False},
        )
        vel_high = _source_item(
            "gh-vel-3",
            "github",
            engagement={"merged_prs": 25, "total_prs": 30},
            metadata={"labels": ["person-profile", "velocity"], "is_pr": False},
        )
        vel_zero = _source_item(
            "gh-vel-0",
            "github",
            engagement={"merged_prs": 0, "total_prs": 0},
            metadata={"labels": ["person-profile", "velocity"], "is_pr": False},
        )
        repo_card = _source_item(
            "gh-repo",
            "github",
            engagement={"stars": 50_000, "open_issues": 40},
            metadata={"labels": ["person-profile", "own-repo"], "is_pr": False},
        )

        raw_low = signals.engagement_raw(vel_low)
        raw_mid = signals.engagement_raw(vel_mid)
        raw_high = signals.engagement_raw(vel_high)
        assert raw_low == pytest.approx(0.70 * math.log1p(10))
        assert raw_mid == pytest.approx(0.70 * math.log1p(10) + 0.30 * math.log1p(12))
        assert raw_high > raw_mid > raw_low > 0
        assert signals.engagement_score(vel_high) > signals.engagement_score(vel_mid) > signals.engagement_score(vel_low) > 0
        assert signals.engagement_raw(vel_zero) is None
        assert signals.engagement_score(vel_zero) is None
        assert signals.engagement_score(repo_card) is None


# ---------------------------------------------------------------------------
# HIGH-098: Single-source TikTok/Instagram 1,000-view spam floor
# ---------------------------------------------------------------------------


class TestHigh098VideoSpamFloor:
    @pytest.mark.parametrize("source", ["tiktok", "instagram"])
    def test_single_source_stream_prunes_low_view_spam_when_valid_clips_exist(self, source):
        spam = _source_item(
            "spam", source, local_relevance=0.6, engagement={"views": 5, "likes": 1, "comments": 0}
        )
        just_under = _source_item(
            "under", source, local_relevance=0.6, engagement={"views": 999, "likes": 50, "comments": 5}
        )
        boundary = _source_item(
            "boundary", source, local_relevance=0.6, engagement={"views": 1000, "likes": 50, "comments": 5}
        )
        viral = _source_item(
            "viral", source, local_relevance=0.6, engagement={"views": 500_000, "likes": 20_000, "comments": 400}
        )

        kept = signals.prune_low_relevance([spam, just_under, boundary, viral])
        assert [i.item_id for i in kept] == ["boundary", "viral"]

    def test_all_below_floor_falls_back_unless_passing_only(self):
        clips = [
            _source_item(f"tt-{i}", "tiktok", local_relevance=0.5, engagement={"views": 100 + i * 100, "likes": 5})
            for i in range(3)
        ]
        assert len(signals.prune_low_relevance(clips, passing_only=False)) == 3
        assert signals.prune_low_relevance(clips, passing_only=True) == []

    @pytest.mark.parametrize("source", ["tiktok", "instagram"])
    def test_single_source_all_low_views_mixed_relevance_drops_off_topic_items(self, source):
        off_topic = _source_item(
            "off", source, local_relevance=0.01, engagement={"views": 200, "likes": 5}
        )
        on_topic = _source_item(
            "on", source, local_relevance=0.60, engagement={"views": 300, "likes": 10}
        )
        kept = signals.prune_low_relevance([off_topic, on_topic], passing_only=False)
        assert [i.item_id for i in kept] == ["on"]
        assert signals.prune_low_relevance([off_topic, on_topic], passing_only=True) == []

    def test_multi_source_all_low_views_mixed_relevance_drops_off_topic_items(self):
        tt_off = _source_item("tt-off", "tiktok", local_relevance=0.01, engagement={"views": 250, "likes": 3})
        ig_on = _source_item("ig-on", "instagram", local_relevance=0.60, engagement={"views": 400, "likes": 12})
        kept = signals.prune_low_relevance([tt_off, ig_on], passing_only=False)
        assert [i.item_id for i in kept] == ["ig-on"]
        assert signals.prune_low_relevance([tt_off, ig_on], passing_only=True) == []

        # When every item fails relevance AND views, keep-all fallback still prevents empty output.
        tt_off2 = _source_item("tt-off2", "tiktok", local_relevance=0.02, engagement={"views": 100, "likes": 1})
        assert [i.item_id for i in signals.prune_low_relevance([tt_off, tt_off2], passing_only=False)] == [
            "tt-off", "tt-off2",
        ]

    def test_first_party_creator_still_exempt_from_video_floor_scoped_by_source(self):
        ig_creator = _source_item(
            "ig-fp", "instagram", author="@creator", local_relevance=0.5, engagement={"views": 40, "likes": 3}
        )
        ig_viral = _source_item(
            "ig-viral", "instagram", author="@other", local_relevance=0.5, engagement={"views": 50_000, "likes": 1000}
        )
        kept = signals.prune_low_relevance(
            [ig_creator, ig_viral],
            first_party_by_source={"instagram": ["creator"]},
        )
        assert [i.item_id for i in kept] == ["ig-fp", "ig-viral"]

        # Same handle on TikTok must NOT be exempted by an Instagram creator flag.
        tt_same_name = _source_item(
            "tt-collision", "tiktok", author="@creator", local_relevance=0.5, engagement={"views": 40, "likes": 3}
        )
        tt_viral = _source_item(
            "tt-viral", "tiktok", author="@other", local_relevance=0.5, engagement={"views": 50_000, "likes": 1000}
        )
        kept_tt = signals.prune_low_relevance(
            [tt_same_name, tt_viral],
            first_party_by_source={"instagram": ["creator"]},
        )
        assert [i.item_id for i in kept_tt] == ["tt-viral"]

    def test_unreported_views_survive_floor_while_measured_zero_and_low_views_are_pruned(self):
        from lib import instagram, shortform_oss, tiktok

        # Instagram/TikTok/yt-dlp parsers preserve missing play counts as None, distinct from measured 0.
        ig_missing = instagram._parse_items(
            [{"id": "1", "code": "A1", "caption": "great reel", "like_count": 400, "comment_count": 20}],
            "great reel",
        )[0]
        ig_zero = instagram._parse_items(
            [{"id": "2", "code": "A2", "caption": "zero reel", "video_play_count": 0, "like_count": 1}],
            "zero reel",
        )[0]
        assert ig_missing["engagement"]["views"] is None
        assert ig_zero["engagement"]["views"] == 0

        tt_missing = tiktok._parse_items(
            [{"aweme_id": "1", "desc": "clip", "statistics": {"digg_count": 300}}],
            "clip",
        )[0]
        tt_zero = tiktok._parse_items(
            [{"aweme_id": "2", "desc": "clip", "statistics": {"play_count": 0, "digg_count": 1}}],
            "clip",
        )[0]
        assert tt_missing["engagement"]["views"] is None
        assert tt_zero["engagement"]["views"] == 0

        oss_missing = shortform_oss._item_from_ytdlp(
            "instagram",
            {"id": "R1", "title": "reel", "like_count": 200, "webpage_url": "https://www.instagram.com/reel/R1/"},
            "reel",
        )
        oss_zero = shortform_oss._item_from_ytdlp(
            "instagram",
            {"id": "R2", "title": "reel", "view_count": 0, "webpage_url": "https://www.instagram.com/reel/R2/"},
            "reel",
        )
        assert oss_missing["engagement"]["views"] is None
        assert oss_zero["engagement"]["views"] == 0

        # A reel with unreported views (None or omitted) survives alongside a 50k-view reel,
        # while a reel with measured 0 or 500 views is pruned.
        unreported_none = _source_item(
            "ig-none", "instagram", local_relevance=0.5, engagement={"views": None, "likes": 400, "comments": 20}
        )
        unreported_omitted = _source_item(
            "ig-omitted", "instagram", local_relevance=0.5, engagement={"likes": 300, "comments": 15}
        )
        measured_zero = _source_item(
            "ig-zero", "instagram", local_relevance=0.5, engagement={"views": 0, "likes": 5, "comments": 0}
        )
        measured_low = _source_item(
            "ig-low", "instagram", local_relevance=0.5, engagement={"views": 500, "likes": 10, "comments": 1}
        )
        measured_high = _source_item(
            "ig-high", "instagram", local_relevance=0.5, engagement={"views": 50_000, "likes": 1200, "comments": 30}
        )
        kept = signals.prune_low_relevance(
            [unreported_none, unreported_omitted, measured_zero, measured_low, measured_high]
        )
        assert [i.item_id for i in kept] == ["ig-none", "ig-omitted", "ig-high"]

    def test_views_coercion_handles_strings_bools_none_and_non_finite(self):
        good_str = _source_item("s-ok", "tiktok", local_relevance=0.5, engagement={"views": "5000", "likes": 10})
        low_str = _source_item("s-low", "tiktok", local_relevance=0.5, engagement={"views": "500", "likes": 10})
        zero_num = _source_item("z", "tiktok", local_relevance=0.5, engagement={"views": 0, "likes": 10})
        bad_str = _source_item("s-bad", "tiktok", local_relevance=0.5, engagement={"views": "not-a-num", "likes": 10})
        bool_val = _source_item("b", "tiktok", local_relevance=0.5, engagement={"views": True, "likes": 10})
        nan_val = _source_item("nan", "tiktok", local_relevance=0.5, engagement={"views": float("nan"), "likes": 10})
        inf_val = _source_item("inf", "tiktok", local_relevance=0.5, engagement={"views": float("inf"), "likes": 10})
        none_val = _source_item("none", "tiktok", local_relevance=0.5, engagement={"views": None, "likes": 10})

        kept = signals.prune_low_relevance([good_str, low_str, zero_num, bad_str, bool_val, nan_val, inf_val, none_val])
        # Only reported numeric counts below 1000 ("500" and 0) are pruned; unreported/unparseable never crash or vanish.
        assert [i.item_id for i in kept] == ["s-ok", "s-bad", "b", "nan", "inf", "none"]


# ---------------------------------------------------------------------------
# HIGH-102: Tie-aware percentile rank in _apply_engagement_rescue
# ---------------------------------------------------------------------------


class TestHigh102EngagementRescueTies:
    def test_multiple_viral_posts_tied_at_100_cap_all_get_rescue_floor(self):
        cands = [
            _candidate(f"x{i}", engagement=eng, final_score=2.0)
            for i, eng in enumerate([40, 60, 100, 100, 100, 100])
        ]
        rerank._apply_engagement_rescue(cands, primary_entity="Hermes Agent", resolved_handles=set())
        assert cands[0].final_score == 2.0
        assert cands[1].final_score == 2.0
        for viral in cands[2:]:
            assert viral.final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)

    def test_two_of_three_tied_at_100_and_all_tied_at_100_both_rescued(self):
        pool_3 = [_candidate(f"a{i}", engagement=e, final_score=1.0) for i, e in enumerate([50, 100, 100])]
        rerank._apply_engagement_rescue(pool_3, primary_entity="Hermes Agent", resolved_handles=set())
        assert pool_3[0].final_score == 1.0
        assert pool_3[1].final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)
        assert pool_3[2].final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)

        all_cap = [_candidate(f"b{i}", engagement=100, final_score=1.0) for i in range(3)]
        rerank._apply_engagement_rescue(all_cap, primary_entity="Hermes Agent", resolved_handles=set())
        for c in all_cap:
            assert c.final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)

    def test_midrank_handles_sub_cap_ties_without_rescuing_low_or_zero_ties(self):
        # Upper-half ties below 100 cap: [40, 60, 80, 80, 80, 80] -> midrank (2 + 1.5)/5 = 0.70 -> floor 16.0
        upper_ties = [_candidate(f"u{i}", engagement=e, final_score=1.0) for i, e in enumerate([40, 60, 80, 80, 80, 80])]
        rerank._apply_engagement_rescue(upper_ties, primary_entity="Hermes Agent", resolved_handles=set())
        assert upper_ties[0].final_score == 1.0
        assert upper_ties[1].final_score == 1.0
        expected_floor = rerank._rescue_floor(0.70)
        for c in upper_ties[2:]:
            assert c.final_score == pytest.approx(expected_floor)

        # Bottom ties must NOT get rescued: [1, 1, 1, 100] -> e=1 has midrank 1/3 <= 0.5 -> floor 0
        bottom_ties = [_candidate(f"l{i}", engagement=e, final_score=1.0) for i, e in enumerate([1, 1, 1, 100])]
        rerank._apply_engagement_rescue(bottom_ties, primary_entity="Hermes Agent", resolved_handles=set())
        for c in bottom_ties[:3]:
            assert c.final_score == 1.0
        assert bottom_ties[3].final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)

        # Equal modest ties [5, 5, 5] -> midrank 0.5 -> floor 0
        modest = [_candidate(f"m{i}", engagement=5, final_score=1.0) for i in range(3)]
        rerank._apply_engagement_rescue(modest, primary_entity="Hermes Agent", resolved_handles=set())
        for c in modest:
            assert c.final_score == 1.0

    def test_zero_negative_none_nan_engagement_never_rescued(self):
        pool = [
            _candidate("z1", engagement=0, final_score=1.0),
            _candidate("z2", engagement=0, final_score=1.0),
            _candidate("neg", engagement=-5, final_score=1.0),
            _candidate("none", engagement=None, final_score=1.0),
            _candidate("nan", engagement=float("nan"), final_score=1.0),
        ]
        rerank._apply_engagement_rescue(pool, primary_entity="Hermes Agent", resolved_handles=set())
        for c in pool:
            assert c.final_score == 1.0

    def test_off_topic_viral_tie_stays_excluded_while_grounded_peer_rescued(self):
        low = _candidate("low", engagement=20, final_score=1.0)
        grounded = _candidate("ok", title="Hermes Agent launch", snippet="Hermes Agent", engagement=100, final_score=1.0)
        offtopic = _candidate("off", title="Unrelated lunch photo", snippet="Tasty salad", engagement=100, final_score=1.0)
        rerank._apply_engagement_rescue([low, grounded, offtopic], primary_entity="Hermes Agent", resolved_handles=set())
        assert grounded.final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)
        assert offtopic.final_score == 1.0


# ---------------------------------------------------------------------------
# HIGH-103: Cluster out-of-window partitioning, sorted merge IDs & empty safety
# ---------------------------------------------------------------------------


class TestHigh103ClusterPartitioningAndMergeSafety:
    def test_all_out_of_window_cluster_sorts_strictly_below_in_window_cluster(self):
        stale_high = _candidate(
            "stale-1",
            source="reddit",
            title="Ancient Kubernetes benchmark roundup",
            snippet="Old 2025 Kubernetes analysis",
            published_at="2025-05-01",
            final_score=95.0,
        )
        stale_low = _candidate(
            "stale-2",
            source="youtube",
            title="Legacy Docker networking overview",
            snippet="Old 2024 Docker networking video",
            published_at="2024-11-01",
            final_score=70.0,
        )
        fresh = _candidate(
            "fresh-1",
            source="x",
            title="Fresh Hermes Agent release notes",
            snippet="New October 2026 release notes",
            published_at="2026-10-25",
            final_score=40.0,
        )
        clusters = cluster.cluster_candidates([stale_high, stale_low, fresh], _plan("Hermes Agent"))
        assert [cl.candidate_ids for cl in clusters] == [["fresh-1"], ["stale-1"], ["stale-2"]]

    def test_mixed_cluster_score_comes_from_in_window_members_not_stale_member(self):
        stale = _candidate(
            "stale",
            source="reddit",
            title="Model launch reactions discussion",
            snippet="Model launch reactions discussion",
            published_at="2025-02-01",
            final_score=95.0,
        )
        fresh_in_mixed = _candidate(
            "fresh-mixed",
            source="x",
            title="Model launch reactions update",
            snippet="Model launch reactions update",
            published_at="2026-10-25",
            final_score=50.0,
        )
        stronger_fresh = _candidate(
            "fresh-strong",
            source="youtube",
            title="Completely different hardware review",
            snippet="GPU benchmarks and power measurements",
            published_at="2026-10-26",
            final_score=70.0,
        )
        clusters = cluster.cluster_candidates([stale, fresh_in_mixed, stronger_fresh], _plan("model launch"))
        assert len(clusters) == 2
        # Pure-fresh cluster (70.0) must beat mixed cluster whose in-window score is 50.0.
        assert clusters[0].candidate_ids == ["fresh-strong"]
        assert clusters[0].score == 70.0
        assert clusters[1].candidate_ids == ["fresh-mixed", "stale"]
        assert clusters[1].score == 50.0
        assert clusters[1].uncertainty == "thin-evidence"

    def test_merge_entity_clusters_stores_sorted_candidate_ids_and_in_window_score(self):
        stale = _candidate(
            "c-stale",
            source="reddit",
            title="Kanye West headline Wireless Festival 2026 announcement",
            snippet="Wireless Festival 2026 details",
            published_at="2025-03-01",
            final_score=92.0,
        )
        fresh = _candidate(
            "c-fresh",
            source="x",
            title="BREAKING: Kanye West returning for Wireless Festival 2026 UK show",
            snippet="Wireless Festival 2026 confirmed",
            published_at="2026-10-22",
            final_score=60.0,
        )
        initial = [
            schema.Cluster(
                cluster_id="cluster-1",
                title=stale.title,
                candidate_ids=["c-stale"],
                representative_ids=["c-stale"],
                sources=["reddit"],
                score=92.0,
            ),
            schema.Cluster(
                cluster_id="cluster-2",
                title=fresh.title,
                candidate_ids=["c-fresh"],
                representative_ids=["c-fresh"],
                sources=["x"],
                score=60.0,
            ),
        ]
        merged = cluster._merge_entity_clusters(initial, [stale, fresh])
        assert len(merged) == 1
        cl = merged[0]
        assert cl.candidate_ids == ["c-fresh", "c-stale"]
        assert cl.representative_ids[0] == "c-fresh"
        assert cl.title == fresh.title
        assert cl.score == 60.0

    def test_cluster_uncertainty_and_entity_merge_do_not_crash_on_empty_candidates(self):
        assert cluster._cluster_uncertainty([]) == "thin-evidence"

        # Simulate entity merge where candidate_map has entities for matching,
        # then invoke with missing candidate IDs in combined pool via _cluster_uncertainty
        # and direct cluster construction edge case.
        c1 = _candidate(
            "c1",
            source="reddit",
            title="Kanye West Wireless Festival 2026 lineup",
            snippet="Wireless Festival",
            final_score=75.0,
        )
        c2 = _candidate(
            "c2",
            source="x",
            title="Kanye West Wireless Festival 2026 tickets",
            snippet="Wireless Festival",
            final_score=70.0,
        )
        initial = [
            schema.Cluster(
                cluster_id="cluster-1",
                title="Fallback Title",
                candidate_ids=["c1", "missing-1"],
                representative_ids=["c1"],
                sources=["reddit"],
                score=75.0,
            ),
            schema.Cluster(
                cluster_id="cluster-2",
                title="Second Title",
                candidate_ids=["c2"],
                representative_ids=["c2"],
                sources=["x"],
                score=70.0,
            ),
        ]
        merged = cluster._merge_entity_clusters(initial, [c1, c2])
        assert len(merged) == 1
        assert merged[0].candidate_ids == ["c1", "c2", "missing-1"]
        assert merged[0].representative_ids[0] == "c1"


# ---------------------------------------------------------------------------
# HIGH-104: Reddit score merge provenance & None preservation
# ---------------------------------------------------------------------------


class TestHigh104RedditScoreMerge:
    def _reddit(self, item_id: str, *, score, engagement_source, num_comments: int = 10) -> schema.SourceItem:
        meta = {}
        if engagement_source is not None:
            meta["engagement_source"] = engagement_source
        return _source_item(
            item_id,
            "reddit",
            title="Ghostty 2.0 thread",
            body="Ghostty 2.0 release discussion",
            url="https://www.reddit.com/r/ghostty/comments/abc123/ghostty_20/",
            engagement={"score": score, "num_comments": num_comments},
            metadata=meta,
            local_relevance=0.8,
            freshness=80,
            source_quality=0.6,
        )

    @pytest.mark.parametrize("incoming_source", [None, reddit_refs.LIVE, reddit_refs.ARCTIC_PENDING, reddit_refs.UNKNOWN])
    def test_none_score_copy_never_overwrites_numeric_arctic_score(self, incoming_source):
        existing = self._reddit("R1", score=450, engagement_source=reddit_refs.ARCTIC)
        incoming = self._reddit("R2", score=None, engagement_source=incoming_source)

        merged = fusion.merge_source_items(existing, incoming)
        assert merged.engagement["score"] == 450
        assert merged.metadata["engagement_source"] == reddit_refs.ARCTIC

    @pytest.mark.parametrize("existing_source", [None, reddit_refs.LIVE, reddit_refs.UNKNOWN])
    def test_arctic_numeric_score_backfills_none_score_copy(self, existing_source):
        existing = self._reddit("R1", score=None, engagement_source=existing_source)
        incoming = self._reddit("R2", score=450, engagement_source=reddit_refs.ARCTIC)

        merged = fusion.merge_source_items(existing, incoming)
        assert merged.engagement["score"] == 450
        assert merged.metadata["engagement_source"] == reddit_refs.ARCTIC
        assert merged.engagement_score == signals.engagement_score(merged)

    def test_arctic_pending_withholding_preserved_against_placeholders(self):
        for placeholder_src, placeholder_score in [
            (reddit_refs.UNKNOWN, 0),
            (reddit_refs.UNKNOWN, None),
            (None, 0),
            (None, None),
            (reddit_refs.LIVE, None),
        ]:
            existing = self._reddit("R1", score=None, engagement_source=reddit_refs.ARCTIC_PENDING)
            incoming = self._reddit("R2", score=placeholder_score, engagement_source=placeholder_src)
            merged = fusion.merge_source_items(existing, incoming)
            assert merged.engagement["score"] is None
            assert merged.metadata["engagement_source"] == reddit_refs.ARCTIC_PENDING

    @pytest.mark.parametrize(
        ("pair_a", "pair_b", "expected_score", "expected_source"),
        [
            ((0, reddit_refs.LIVE), (450, reddit_refs.ARCTIC), 0, reddit_refs.LIVE),
            ((412, reddit_refs.LIVE), (900, reddit_refs.ARCTIC), 412, reddit_refs.LIVE),
            ((0, reddit_refs.LIVE), (None, reddit_refs.ARCTIC_PENDING), 0, reddit_refs.LIVE),
            ((450, reddit_refs.ARCTIC), (None, reddit_refs.ARCTIC_PENDING), 450, reddit_refs.ARCTIC),
            ((None, reddit_refs.ARCTIC_PENDING), (0, reddit_refs.UNKNOWN), None, reddit_refs.ARCTIC_PENDING),
            ((None, reddit_refs.ARCTIC_PENDING), (0, None), None, reddit_refs.ARCTIC_PENDING),
            ((None, reddit_refs.ARCTIC_PENDING), (None, reddit_refs.LIVE), None, reddit_refs.ARCTIC_PENDING),
            ((450, reddit_refs.ARCTIC), (None, reddit_refs.LIVE), 450, reddit_refs.ARCTIC),
            ((450, reddit_refs.ARCTIC), (0, None), 450, reddit_refs.ARCTIC),
            ((450, reddit_refs.ARCTIC), (999, reddit_refs.UNKNOWN), 450, reddit_refs.ARCTIC),
        ],
    )
    def test_reddit_score_merge_is_order_independent_commutative(
        self, pair_a, pair_b, expected_score, expected_source,
    ):
        a1 = self._reddit("R1", score=pair_a[0], engagement_source=pair_a[1])
        b1 = self._reddit("R2", score=pair_b[0], engagement_source=pair_b[1])
        ab = fusion.merge_source_items(a1, b1)

        b2 = self._reddit("R2", score=pair_b[0], engagement_source=pair_b[1])
        a2 = self._reddit("R1", score=pair_a[0], engagement_source=pair_a[1])
        ba = fusion.merge_source_items(b2, a2)

        assert ab.engagement["score"] == expected_score
        assert ba.engagement["score"] == expected_score
        assert ab.metadata.get("engagement_source") == expected_source
        assert ba.metadata.get("engagement_source") == expected_source

    @pytest.mark.parametrize("live_source", [reddit_refs.LIVE, None])
    def test_numeric_live_score_upgrades_arctic_pending(self, live_source):
        existing = self._reddit("R1", score=None, engagement_source=reddit_refs.ARCTIC_PENDING)
        incoming = self._reddit("R2", score=412, engagement_source=live_source)
        merged = fusion.merge_source_items(existing, incoming)
        assert merged.engagement["score"] == 412
        if live_source is None:
            assert "engagement_source" not in merged.metadata
        else:
            assert merged.metadata["engagement_source"] == reddit_refs.LIVE

    def test_unknown_none_upgrades_to_arctic_pending(self):
        existing = self._reddit("R1", score=None, engagement_source=reddit_refs.UNKNOWN)
        incoming = self._reddit("R2", score=None, engagement_source=reddit_refs.ARCTIC_PENDING)
        merged = fusion.merge_source_items(existing, incoming)
        assert merged.engagement["score"] is None
        assert merged.metadata["engagement_source"] == reddit_refs.ARCTIC_PENDING

    def test_measured_live_zero_beats_arctic_and_invalid_values_ignored(self):
        live_zero = self._reddit("R1", score=0, engagement_source=reddit_refs.LIVE)
        arctic = self._reddit("R2", score=450, engagement_source=reddit_refs.ARCTIC)
        merged = fusion.merge_source_items(live_zero, arctic)
        assert merged.engagement["score"] == 0
        assert merged.metadata["engagement_source"] == reddit_refs.LIVE

        for bad in (float("nan"), float("inf"), True):
            existing = self._reddit("R3", score=450, engagement_source=reddit_refs.ARCTIC)
            incoming = self._reddit("R4", score=bad, engagement_source=reddit_refs.LIVE)
            out = fusion.merge_source_items(existing, incoming)
            assert out.engagement["score"] == 450
            assert out.metadata["engagement_source"] == reddit_refs.ARCTIC

    def test_weighted_rrf_preserves_arctic_score_regardless_of_stream_order(self):
        plan = _plan("ghostty", ["reddit"])
        for order in ("arctic_first", "unlabelled_first"):
            arctic = self._reddit("R1", score=450, engagement_source=reddit_refs.ARCTIC)
            arctic.engagement_score = signals.engagement_score(arctic)
            unlabelled = self._reddit("R2", score=None, engagement_source=None)
            unlabelled.engagement_score = signals.engagement_score(unlabelled)
            streams = (
                {("primary", "reddit"): [arctic], ("secondary", "reddit"): [unlabelled]}
                if order == "arctic_first"
                else {("primary", "reddit"): [unlabelled], ("secondary", "reddit"): [arctic]}
            )
            [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
            [si] = cand.source_items
            assert si.engagement["score"] == 450, order
            assert si.metadata["engagement_source"] == reddit_refs.ARCTIC, order
            assert cand.engagement == signals.engagement_score(si), order


# ---------------------------------------------------------------------------
# HIGH-109: Normalized URL dedupe across all sources & mixed Polymarket
# ---------------------------------------------------------------------------


class TestHigh109DedupeUrlNormalization:
    def test_jobs_url_variants_collapse_while_distinct_urls_survive(self):
        boilerplate = "TL;DR: Acme builds infrastructure. Great benefits, equity, remote."
        items = [
            _source_item(
                "j1", "jobs", title="Staff Engineer", body=boilerplate,
                url="https://jobs.acme.com/roles/42?a=1&b=2",
            ),
            _source_item(
                "j1-dup", "jobs", title="Staff Engineer", body=boilerplate,
                url="HTTPS://WWW.Jobs.Acme.com/roles/42/?b=2&utm_source=linkedin&a=1#apply",
            ),
            _source_item(
                "j2", "jobs", title="Product Designer", body=boilerplate,
                url="https://jobs.acme.com/roles/99",
            ),
        ]
        kept = dedupe.dedupe_items(items)
        assert [i.item_id for i in kept] == ["j1", "j2"]

    def test_mixed_polymarket_matched_and_unmatched_same_event_dedupes_both_orders(self):
        matched = _source_item(
            "pm-matched",
            "polymarket",
            title="Pro Football: 2027 Champion",
            body="Will the 49ers win?",
            url="https://www.polymarket.com/event/nfl-2027/?utm_source=x#markets",
            metadata={"matched_market": {"label": "49ers", "question": "Will 49ers win?"}},
        )
        unmatched = _source_item(
            "pm-unmatched",
            "polymarket",
            title="Totally different enriched summary of event",
            body="Different wording so fuzzy Jaccard is below 0.1",
            url="https://polymarket.com/event/nfl-2027",
            metadata={},
        )
        kept_first = dedupe.dedupe_items([matched, unmatched])
        assert [i.item_id for i in kept_first] == ["pm-matched"]
        assert kept_first[0].metadata["matched_market"]["label"] == "49ers"

        # When the unmatched copy arrives first, merging preserves matched_market on the survivor.
        kept_second = dedupe.dedupe_items([unmatched, matched])
        assert [i.item_id for i in kept_second] == ["pm-unmatched"]
        assert kept_second[0].metadata["matched_market"]["label"] == "49ers"
        # Caller input was not mutated in place.
        assert "matched_market" not in unmatched.metadata

    def test_same_url_with_divergent_enrichment_text_merges_evidence_without_mutating_inputs(self):
        bare_yt = _source_item(
            "yt1",
            "youtube",
            title="Short title",
            body="",
            snippet="",
            url="https://m.youtube.com/watch?t=10&v=AbC123&utm_campaign=drill#chapter1",
            engagement={"views": 1000},
            metadata={},
        )
        enriched_yt = _source_item(
            "yt2",
            "youtube",
            title="Short title",
            body=" ".join(f"transcriptword{i}" for i in range(80)),
            snippet="Key quote from the saved transcript",
            url="https://www.youtube.com/watch?v=AbC123&t=10",
            engagement={"views": 5000, "likes": 200},
            metadata={
                "transcript_snippet": "Saved full transcript snippet",
                "top_comments": [{"author": "alice", "score": 90, "excerpt": "great breakdown"}],
            },
        )
        assert dedupe.hybrid_similarity(dedupe.item_text(bare_yt), dedupe.item_text(enriched_yt)) < 0.1
        kept = dedupe.dedupe_items([bare_yt, enriched_yt])
        assert [i.item_id for i in kept] == ["yt1"]
        merged = kept[0]
        assert merged.snippet == "Key quote from the saved transcript"
        assert merged.metadata["transcript_snippet"] == "Saved full transcript snippet"
        assert merged.metadata["top_comments"][0]["author"] == "alice"
        assert merged.engagement["views"] == 5000
        # Caller's bare_yt object stays untouched.
        assert bare_yt.snippet == ""
        assert "top_comments" not in bare_yt.metadata

    def test_reddit_thread_key_dedupes_different_url_forms_and_divergent_text(self):
        bare = _source_item(
            "r1",
            "reddit",
            title="New release",
            body="",
            url="https://old.reddit.com/r/python/comments/1abc2de/new_release/?utm_source=share",
        )
        enriched = _source_item(
            "r2",
            "reddit",
            title="New release",
            body=" ".join(f"selftextword{i}" for i in range(80)),
            url="https://redd.it/1abc2de",
        )
        kept = dedupe.dedupe_items([bare, enriched])
        assert [i.item_id for i in kept] == ["r1"]

    def test_hackernews_dedupes_by_hn_url_not_article_url(self):
        sub1 = _source_item(
            "hn1",
            "hackernews",
            title="SQLite 3.54 released",
            body="First discussion about WAL mode",
            url="https://sqlite.org/release.html",
            metadata={"hn_url": "https://news.ycombinator.com/item?id=100"},
        )
        sub1_dup = _source_item(
            "hn1-dup",
            "hackernews",
            title="SQLite 3.54 released",
            body=" ".join(f"commentword{i}" for i in range(60)),
            url="https://sqlite.org/release.html",
            metadata={"hn_url": "https://WWW.News.YCombinator.com/item/?id=100&utm_source=hn"},
        )
        sub2 = _source_item(
            "hn2",
            "hackernews",
            title="Show HN: Completely separate submission",
            body="Different angle covering Windows support",
            url="https://sqlite.org/release.html",
            metadata={"hn_url": "https://news.ycombinator.com/item?id=200"},
        )
        kept = dedupe.dedupe_items([sub1, sub1_dup, sub2])
        assert [i.item_id for i in kept] == ["hn1", "hn2"]

    def test_empty_text_items_dedupe_by_normalized_url_and_keep_empty_urls(self):
        e1 = _source_item("e1", "x", title="", body="", url="https://www.x.com/u/status/1/?utm_source=s")
        e2 = _source_item("e2", "x", title="", body="", url="https://x.com/u/status/1")
        e3 = _source_item("e3", "x", title="", body="", url="https://x.com/u/status/2")
        no_url_1 = _source_item("n1", "x", title="", body="", url="")
        no_url_2 = _source_item("n2", "x", title="", body="", url="")

        kept = dedupe.dedupe_items([e1, e2, e3, no_url_1, no_url_2])
        assert [i.item_id for i in kept] == ["e1", "e3", "n1", "n2"]

    def test_cross_source_same_url_not_collapsed_by_within_source_dedupe(self):
        r = _source_item("r1", "reddit", title="Alpha launch", body="Reddit view", url="https://example.com/a")
        g = _source_item("g1", "grounding", title="Beta report", body="Web coverage", url="https://example.com/a")
        kept = dedupe.dedupe_items([r, g])
        assert [i.item_id for i in kept] == ["r1", "g1"]

    def test_normalize_url_sorts_query_param_names_and_preserves_repeated_value_order(self):
        u1 = fusion._normalize_url("HTTPS://old.www.Example.COM/path/?z=9&a=1&utm_term=foo#frag")
        u2 = fusion._normalize_url("https://m.example.com/path?a=1&z=9")
        assert u1 == u2 == "https://example.com/path?a=1&z=9"

        # Repeated parameter names keep their original value order so distinct links do not collide.
        first = fusion._normalize_url("https://example.com/item?z=1&id=42&id=99&utm_source=x")
        second = fusion._normalize_url("https://example.com/item?id=99&z=1&id=42")
        assert first == "https://example.com/item?id=42&id=99&z=1"
        assert second == "https://example.com/item?id=99&id=42&z=1"
        assert first != second

        # Distinct repeated-param orderings stay separate in dedupe_items as well.
        items = [
            _source_item("q1", "jobs", title="Role A", body="desc", url="https://example.com/item?id=42&id=99"),
            _source_item("q2", "jobs", title="Role B", body="desc", url="https://example.com/item?id=99&id=42"),
        ]
        assert [i.item_id for i in dedupe.dedupe_items(items)] == ["q1", "q2"]


# ---------------------------------------------------------------------------
# HIGH-110: Generic-only relevance cap strictly below RELEVANCE_FLOOR
# ---------------------------------------------------------------------------


class TestHigh110GenericOnlyRelevanceCap:
    def test_generic_only_cap_sits_strictly_below_relevance_floor(self):
        assert relevance.GENERIC_ONLY_CAP < relevance.RELEVANCE_FLOOR
        score = relevance.token_overlap_relevance("Stripe review tips", "Great review and tips!")
        assert score <= relevance.GENERIC_ONLY_CAP
        assert score < relevance.RELEVANCE_FLOOR

    def test_generic_only_item_is_pruned_by_prune_low_relevance_even_with_engagement(self):
        query = "Stripe review tips"
        off_topic = _source_item(
            "off",
            "reddit",
            title="Great review and tips!",
            body="Honest review and tips for everyone.",
            engagement={"score": 5000, "num_comments": 400},
        )
        on_topic = _source_item(
            "on",
            "reddit",
            title="Stripe checkout integration",
            body="Using Stripe API webhooks in production.",
            engagement={"score": 15, "num_comments": 4},
        )
        annotated = signals.annotate_stream([off_topic, on_topic], query, "balanced_recent")
        off = next(i for i in annotated if i.item_id == "off")
        on = next(i for i in annotated if i.item_id == "on")
        assert off.local_relevance < relevance.RELEVANCE_FLOOR
        assert on.local_relevance >= 0.15

        kept = signals.prune_low_relevance(annotated)
        assert [i.item_id for i in kept] == ["on"]
        assert signals.prune_low_relevance([off], passing_only=True) == []

    def test_all_generic_query_is_not_capped_down_when_matching(self):
        score = relevance.token_overlap_relevance("review tips", "Great review and tips!")
        assert score >= relevance.RELEVANCE_FLOOR
