"""Regression tests for PR 25: ranking, fusion, normalization & grounding integrity.

Covers:
- HIGH-099: Out-of-window floor bypass, sort partitioning, and 0-vs-None engagement tie-break
- HIGH-100: Forward primary_entity and subject_handles on LLM-omitted fallback rows
- HIGH-101: Hardened LLM score parsing (non-list scores, TypeError, NaN/inf, per-row fallback)
- HIGH-105: True primary_scores tracking, url/source_items[0] sync, same-stream RRF guard, spoken_caption
- HIGH-106: Recompute engagement_score on merge, longer enrichment strings, non-mutating weighted_rrf
- HIGH-107: Preserve Arctic Reddit top_comments/attribution/dates through grounding -> normalize
- HIGH-108: UTC clock in grounding._now() so east-of-UTC users keep fresh Serper relative results
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from lib import fusion, grounding, normalize, pipeline, reddit_hydrate, reddit_refs, render, rerank, schema, signals


def _plan(topic: str = "Hermes Agent", sources: list[str] | None = None) -> schema.QueryPlan:
    srcs = sources or ["reddit", "x", "hackernews", "instagram"]
    return schema.QueryPlan(
        intent="breaking_news",
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
                search_query=f"{topic} discussion",
                ranking_query=f"{topic} discussion",
                sources=srcs,
                weight=0.8,
            ),
        ],
        source_weights={s: 1.0 for s in srcs},
    )


def _cand(
    cid: str,
    *,
    source: str = "x",
    title: str = "Hermes Agent update",
    snippet: str = "Hermes Agent release notes",
    author: str | None = "someone",
    published_at: str | None = "2026-10-20",
    date_confidence: str = "high",
    range_from: str = "2026-10-01",
    range_to: str = "2026-10-31",
    local_relevance: float = 0.6,
    freshness: int = 80,
    engagement: float | int | None = 50,
    source_quality: float = 0.68,
    rrf_score: float = 0.02,
    mentions: list[str] | None = None,
) -> schema.Candidate:
    item = schema.SourceItem(
        item_id=cid,
        source=source,
        title=title,
        body=f"{title} {snippet}",
        url=f"https://example.com/{source}/{cid}",
        author=author,
        published_at=published_at,
        date_confidence=date_confidence,
        snippet=snippet,
        metadata={"mentioned_handles": list(mentions)} if mentions else {},
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
        freshness=freshness,
        engagement=engagement,
        source_quality=source_quality,
        rrf_score=rrf_score,
        sources=[source],
        source_items=[item],
        metadata={"range_from": range_from, "range_to": range_to},
    )


class _FakeProvider:
    def __init__(self, payload=None, *, exc: Exception | None = None):
        self.payload = payload
        self.exc = exc

    def generate_json(self, _model: str, _prompt: str):
        if self.exc is not None:
            raise self.exc
        return self.payload


# ---------------------------------------------------------------------------
# HIGH-099: Out-of-window floor bypass, sort partitioning & 0-vs-None tiebreak
# ---------------------------------------------------------------------------


class TestHigh099OutOfWindowAndTieBreaker:
    def test_post_scoring_floors_never_lift_out_of_window_candidates(self):
        stale_fp = _cand(
            "stale-fp",
            author="steipete",
            title="Old post",
            snippet="Old post",
            published_at="2024-05-01",
            engagement=100,
            mentions=["friend"],
        )
        stale_fp.final_score = 12.0
        assert schema.candidate_out_of_window(stale_fp)

        in_win_low = _cand(
            "fresh-low",
            author="observer",
            title="Peter Steinberger update",
            snippet="Peter Steinberger update",
            published_at="2026-10-20",
            engagement=10,
        )
        in_win_low.final_score = 5.0
        in_win_high = _cand(
            "fresh-high",
            author="observer2",
            title="Peter Steinberger release",
            snippet="Peter Steinberger release",
            published_at="2026-10-22",
            engagement=80,
        )
        in_win_high.final_score = 5.0

        rerank._apply_first_party_floor(
            [stale_fp],
            resolved_handles={"steipete"},
            primary_entity="Peter Steinberger",
            topic="Peter Steinberger",
            primary_handle="steipete",
        )
        assert stale_fp.final_score == 12.0

        rerank._apply_interaction_signal(
            [stale_fp],
            resolved_handles={"steipete"},
            primary_entity="Peter Steinberger",
            topic="Peter Steinberger",
            primary_handle="steipete",
        )
        assert stale_fp.final_score == 12.0
        assert stale_fp.metadata.get("interaction_targets") == ["friend"]

        # Stale viral X post is excluded from engagement rescue AND must not
        # suppress the in-window top X post's rescue floor.
        rerank._apply_engagement_rescue(
            [stale_fp, in_win_low, in_win_high],
            primary_entity="Peter Steinberger",
            resolved_handles={"steipete"},
            topic="Peter Steinberger",
            primary_handle="steipete",
        )
        assert stale_fp.final_score == 12.0
        assert in_win_high.final_score == pytest.approx(rerank.RESCUE_FLOOR_MAX)

    def test_rerank_candidates_partitions_in_window_ahead_of_stale_viral_subject_post(self):
        stale_viral = _cand(
            "stale-viral",
            source="x",
            author="steipete",
            title="Peter Steinberger viral 2024 thread",
            snippet="Huge viral thread from two years ago",
            published_at="2024-06-15",
            local_relevance=0.95,
            freshness=90,
            engagement=100,
            mentions=["someone"],
        )
        stale_low = _cand(
            "stale-low",
            source="x",
            author="steipete",
            title="Old short post",
            snippet="Old short post",
            published_at="2024-06-10",
            local_relevance=0.1,
            freshness=20,
            engagement=5,
            mentions=["friend"],
        )
        fresh_modest = _cand(
            "fresh-modest",
            source="x",
            author="observer",
            title="Peter Steinberger note today",
            snippet="Short note mentioning Peter Steinberger",
            published_at="2026-10-25",
            local_relevance=0.3,
            freshness=60,
            engagement=5,
        )
        ranked = rerank.rerank_candidates(
            topic="Peter Steinberger",
            plan=_plan("Peter Steinberger", ["x"]),
            candidates=[stale_viral, stale_low, fresh_modest],
            provider=None,
            model=None,
            shortlist_size=10,
            resolved_handles={"steipete"},
            primary_handle="steipete",
        )
        # In-window candidate always partitions ahead of out-of-window candidates,
        # even when stale_viral has a higher scaled final_score than fresh_modest.
        assert [c.candidate_id for c in ranked] == ["fresh-modest", "stale-viral", "stale-low"]
        # Neither INTERACTION_FLOOR (35) nor RESCUE_FLOOR_MAX (40) nor FIRST_PARTY_FLOOR (25)
        # lifted the out-of-window candidates above their 0.35x-scaled _final_score.
        assert stale_viral.final_score == pytest.approx(rerank._final_score(stale_viral))
        assert stale_viral.final_score < rerank.INTERACTION_FLOOR
        assert stale_low.final_score == pytest.approx(rerank._final_score(stale_low))
        assert stale_low.final_score < rerank.FIRST_PARTY_FLOOR

    def test_engagement_tie_breaker_prefers_measured_zero_over_unknown_none(self):
        measured_zero = _cand(
            "z-measured",
            source="reddit",
            title="ZZZ Hermes Agent thread",
            engagement=0,
            local_relevance=0.5,
            freshness=70,
        )
        unknown_none = _cand(
            "a-unknown",
            source="reddit",
            title="AAA Hermes Agent thread",
            engagement=None,
            local_relevance=0.5,
            freshness=70,
        )
        ranked = rerank.rerank_candidates(
            topic="Hermes Agent",
            plan=_plan("Hermes Agent", ["reddit"]),
            candidates=[unknown_none, measured_zero],
            provider=None,
            model=None,
            shortlist_size=10,
        )
        assert measured_zero.final_score == pytest.approx(unknown_none.final_score)
        assert [c.candidate_id for c in ranked] == ["z-measured", "a-unknown"]

    def test_pipeline_final_sort_shares_candidate_sort_key_zero_beats_none(self):
        measured_zero = _cand(
            "measured-zero",
            source="reddit",
            title="ZZZ Hermes Agent thread",
            engagement=0,
            local_relevance=0.5,
            freshness=70,
        )
        measured_zero.native_ranks = {"primary:reddit": 5}
        measured_zero.final_score = 45.0

        unknown_none = _cand(
            "unknown-none",
            source="reddit",
            title="AAA Hermes Agent thread",
            engagement=None,
            local_relevance=0.5,
            freshness=70,
        )
        unknown_none.native_ranks = {"primary:reddit": 1}
        unknown_none.final_score = 45.0

        assert rerank.candidate_sort_key(measured_zero) < rerank.candidate_sort_key(unknown_none)

        with patch.object(pipeline, "_retrieve_stream", return_value=([], {})), \
             patch.object(rerank, "rerank_candidates", side_effect=[[unknown_none, measured_zero], []]):
            report = pipeline.run(
                topic="Hermes Agent",
                config={},
                depth="quick",
                mock=True,
                requested_sources=["reddit"],
                web_backend="none",
                as_of_date="2026-10-31",
            )
        assert [c.candidate_id for c in report.ranked_candidates] == [
            "measured-zero",
            "unknown-none",
        ]


# ---------------------------------------------------------------------------
# HIGH-100: Forward primary_entity and subject_handles on LLM-omitted rows
# ---------------------------------------------------------------------------


class TestHigh100LlmOmittedFallbackGrounding:
    def test_omitted_off_topic_candidate_gets_entity_miss_and_is_pruned(self):
        scored = _cand("on-topic", source="youtube", title="Hermes Agent guide", snippet="Hermes Agent")
        omitted_off = _cand(
            "omitted-off",
            source="youtube",
            title="Managed Agents by Anthropic",
            snippet="Completely unrelated workflow",
            local_relevance=0.3,
        )
        provider = _FakeProvider(
            {"scores": [{"candidate_id": "on-topic", "relevance": 85, "reason": "direct match"}]}
        )
        ranked = rerank.rerank_candidates(
            topic="Hermes Agent use cases",
            plan=_plan("Hermes Agent use cases", ["youtube"]),
            candidates=[scored, omitted_off],
            provider=provider,
            model="test-model",
            shortlist_size=10,
        )
        assert omitted_off.explanation == rerank._FALLBACK_ENTITY_MISS_EXPLANATION
        assert rerank._is_fallback_scored(omitted_off)
        pruned = rerank.prune_fallback_entity_misses(ranked, topic="Hermes Agent use cases")
        assert [c.candidate_id for c in pruned] == ["on-topic"]

    def test_omitted_off_topic_related_handle_does_not_get_first_party_credit(self):
        scored = _cand("scored", source="x", author="observer", title="Ghostty 1.3", snippet="Ghostty")
        related_off = _cand(
            "related-off",
            source="x",
            author="mitchellh",
            title="@friend Nanit",
            snippet="@friend Nanit",
            local_relevance=0.0,
            freshness=70,
            engagement=10,
        )
        subject_omitted = _cand(
            "subject-omitted",
            source="x",
            author="ghostty",
            title="shipping tonight",
            snippet="shipping tonight",
            local_relevance=0.0,
            freshness=70,
            engagement=10,
        )
        provider = _FakeProvider(
            {"scores": [{"candidate_id": "scored", "relevance": 90, "reason": "on topic"}]}
        )
        rerank.rerank_candidates(
            topic="Ghostty terminal",
            plan=_plan("Ghostty terminal", ["x"]),
            candidates=[scored, related_off, subject_omitted],
            provider=provider,
            model="test-model",
            shortlist_size=10,
            resolved_handles={"mitchellh", "ghostty"},
            primary_handle="ghostty",
        )
        # Related off-topic handle omits entity -> no +5.0 credit, no floor lift.
        expected_base = (0.0 * 70.0) + (related_off.source_quality * 10.0)
        assert related_off.rerank_score == pytest.approx(expected_base)
        assert related_off.final_score < rerank.FIRST_PARTY_FLOOR
        # Subject handle ("ghostty" matches "Ghostty terminal") keeps +5.0 credit.
        assert subject_omitted.rerank_score == pytest.approx(expected_base + rerank.FIRST_PARTY_AUTHOR_CREDIT)
        assert subject_omitted.final_score >= rerank.FIRST_PARTY_FLOOR


# ---------------------------------------------------------------------------
# HIGH-101: Hardened LLM score parsing and TypeError / NaN / inf safety
# ---------------------------------------------------------------------------


class TestHigh101MalformedLlmPayloads:
    @pytest.mark.parametrize("bad_payload", [
        {"scores": 123},
        {"scores": True},
        {"scores": "not-a-list"},
        {"scores": {"c1": 90}},
        None,
        ["not-a-dict"],
    ])
    def test_non_list_scores_and_non_dict_payloads_fall_back_without_crashing(self, bad_payload):
        c1 = _cand("c1", title="Hermes Agent news", snippet="Hermes Agent")
        provider = _FakeProvider(bad_payload)
        ranked = rerank.rerank_candidates(
            topic="Hermes Agent",
            plan=_plan("Hermes Agent", ["x"]),
            candidates=[c1],
            provider=provider,
            model="test-model",
            shortlist_size=5,
        )
        assert len(ranked) == 1
        assert rerank._is_fallback_scored(c1)
        assert math.isfinite(c1.final_score)

        rerank.score_fun(
            topic="Hermes Agent",
            candidates=[c1],
            provider=provider,
            model="test-model",
        )
        assert c1.fun_explanation == "heuristic-fallback"
        assert c1.fun_score is not None and math.isfinite(c1.fun_score)

    def test_provider_raising_type_error_is_caught_by_rerank_and_score_fun(self):
        c1 = _cand("c1", title="Hermes Agent", snippet="Hermes Agent")
        provider = _FakeProvider(exc=TypeError("bad wire payload"))
        ranked = rerank.rerank_candidates(
            topic="Hermes Agent",
            plan=_plan("Hermes Agent", ["x"]),
            candidates=[c1],
            provider=provider,
            model="test-model",
            shortlist_size=5,
        )
        assert len(ranked) == 1
        assert rerank._is_fallback_scored(c1)

        rerank.score_fun(topic="Hermes Agent", candidates=[c1], provider=provider, model="test-model")
        assert c1.fun_explanation == "heuristic-fallback"

    @pytest.mark.parametrize("bad_val", [
        "high",
        "85/100",
        [85],
        {"score": 90},
        True,
        False,
        None,
        "NaN",
        float("nan"),
        "inf",
        "-inf",
        float("inf"),
        "1e500",
        10**400,
    ])
    def test_bad_row_only_falls_back_that_candidate_and_preserves_batch(self, bad_val):
        good = _cand("good", title="Hermes Agent launch", snippet="Hermes Agent")
        zero = _cand("zero", title="Hermes Agent note", snippet="Hermes Agent")
        bad = _cand("bad", title="Hermes Agent misc", snippet="Hermes Agent", local_relevance=0.2)
        payload = {
            "scores": [
                {"candidate_id": "good", "relevance": 88, "reason": "strong hit"},
                {"candidate_id": "zero", "relevance": 0, "reason": "explicit zero"},
                {"candidate_id": "bad", "relevance": bad_val, "reason": "corrupt"},
            ]
        }
        rerank._apply_llm_scores([good, zero, bad], payload, primary_entity="Hermes Agent")
        assert good.rerank_score == 88.0
        assert good.explanation == "strong hit"
        assert not rerank._is_fallback_scored(good)
        assert zero.rerank_score == 0.0
        assert zero.explanation == "explicit zero"
        assert not rerank._is_fallback_scored(zero)
        # Corrupted row falls back instead of becoming 100.0 or aborting the batch.
        assert rerank._is_fallback_scored(bad)
        assert bad.rerank_score is not None
        assert math.isfinite(bad.rerank_score)
        assert bad.rerank_score < 50.0

        fun_payload = {
            "scores": [
                {"candidate_id": "good", "fun": 77, "reason": "witty"},
                {"candidate_id": "zero", "fun": 0, "reason": "dry"},
                {"candidate_id": "bad", "fun": bad_val, "reason": "corrupt"},
            ]
        }
        rerank._apply_fun_scores([good, zero, bad], fun_payload)
        assert good.fun_score == 77.0
        assert good.fun_explanation == "witty"
        assert zero.fun_score == 0.0
        assert zero.fun_explanation == "dry"
        assert bad.fun_explanation == "heuristic-fallback"
        assert bad.fun_score is not None and math.isfinite(bad.fun_score) and bad.fun_score < 100.0


# ---------------------------------------------------------------------------
# HIGH-105: Primary scores, url/source_items sync, same-stream RRF, spoken_caption
# ---------------------------------------------------------------------------


class TestHigh105FusionMultiItemIntegrity:
    def _item(
        self,
        item_id: str,
        source: str,
        url: str,
        title: str,
        *,
        rel: float,
        fresh: int,
        qual: float,
        snippet: str = "",
        engagement: dict | None = None,
        metadata: dict | None = None,
    ) -> schema.SourceItem:
        it = schema.SourceItem(
            item_id=item_id,
            source=source,
            title=title,
            body=title,
            url=url,
            snippet=snippet or title,
            engagement=dict(engagement or {}),
            metadata=dict(metadata or {}),
        )
        it.local_relevance = rel
        it.freshness = fresh
        it.source_quality = qual
        return it

    def test_true_primary_score_prevents_synthetic_max_lockout(self):
        # Item 1: 90 + 10 + 6.8 = 106.8
        # Item 2: 20 + 90 + 6.0 = 116.0 (beats Item 1; synthetic max would be 90+90+6.8=186.8!)
        # Item 3: 85 + 85 + 8.0 = 178.0 (must beat Item 2's true 116.0!)
        shared_url = "https://example.com/story"
        i1 = self._item("x1", "x", f"{shared_url}?utm_source=x", "X copy", rel=0.9, fresh=10, qual=0.68)
        i2 = self._item("r1", "reddit", f"{shared_url}?utm_source=r", "Reddit copy", rel=0.2, fresh=90, qual=0.60)
        i3 = self._item("hn1", "hackernews", shared_url, "HN best copy", rel=0.85, fresh=85, qual=0.80,
                        metadata={"hn_url": "https://news.ycombinator.com/item?id=999"})
        plan = _plan("story", ["x", "reddit", "hackernews"])
        streams = {
            ("primary", "x"): [i1],
            ("primary", "reddit"): [i2],
            ("primary", "hackernews"): [i3],
        }
        [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
        assert cand.item_id == "hn1"
        assert cand.source == "hackernews"
        assert cand.title == "HN best copy"
        assert cand.url == shared_url
        assert cand.source_items[0].item_id == "hn1"
        assert schema.candidate_primary_item(cand).item_id == "hn1"

    def test_higher_scoring_hn_sibling_syncs_url_source_items_and_snippet(self):
        hn1 = self._item(
            "100", "hackernews", "https://sqlite.org/release.html?utm_source=first", "Weak HN title",
            rel=0.3, fresh=60, qual=0.8, snippet="Weak first submission snippet",
            engagement={"points": 5, "comments": 1},
            metadata={"hn_url": "https://news.ycombinator.com/item?id=100"},
        )
        hn2 = self._item(
            "200", "hackernews", "https://sqlite.org/release.html", "Strong HN title",
            rel=0.9, fresh=85, qual=0.8, snippet="Strong winning submission snippet",
            engagement={"points": 400, "comments": 120},
            metadata={"hn_url": "https://news.ycombinator.com/item?id=200"},
        )
        plan = _plan("sqlite", ["hackernews"])
        streams = {
            ("primary", "hackernews"): [hn1],
            ("secondary", "hackernews"): [hn2],
        }
        [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
        assert cand.item_id == "200"
        assert cand.title == "Strong HN title"
        assert cand.url == "https://sqlite.org/release.html"
        assert cand.source_items[0].item_id == "200"
        primary = schema.candidate_primary_item(cand)
        assert primary is not None
        assert primary.item_id == "200"
        assert cand.snippet == "Strong winning submission snippet"

    def test_same_stream_duplicate_candidate_key_does_not_double_rrf_or_worsen_rank(self):
        hn1 = self._item(
            "100", "hackernews", "https://example.com/post", "Article (submission 1)",
            rel=0.8, fresh=80, qual=0.8,
            metadata={"hn_url": "https://news.ycombinator.com/item?id=100"},
        )
        other = self._item(
            "150", "hackernews", "https://example.com/other", "Other article",
            rel=0.7, fresh=80, qual=0.8,
            metadata={"hn_url": "https://news.ycombinator.com/item?id=150"},
        )
        hn2 = self._item(
            "200", "hackernews", "https://example.com/post", "Article (submission 2)",
            rel=0.6, fresh=80, qual=0.8,
            metadata={"hn_url": "https://news.ycombinator.com/item?id=200"},
        )
        plan = _plan("test", ["hackernews"])
        streams = {("primary", "hackernews"): [hn1, other, hn2]}
        cands = fusion.weighted_rrf(streams, plan, pool_limit=10)
        merged = next(c for c in cands if c.candidate_id == "https://example.com/post")
        expected_rrf = 1.0 / (fusion.RRF_K + 1)
        assert merged.rrf_score == pytest.approx(expected_rrf)
        assert merged.native_ranks["primary:hackernews"] == 1
        assert len(merged.source_items) == 2

    def test_weighted_rrf_preserves_spoken_caption_in_both_orders(self):
        url = "https://www.instagram.com/reel/abc123/"
        spoken_text = "Short spoken quote."
        written_text = "Very long written description full of hashtags and filler words for engagement."
        plan = _plan("topic", ["instagram"])

        for order in ("spoken_first", "written_first"):
            spoken = self._item(
                "ig-s", "instagram", url, "Reel", rel=0.4, fresh=80, qual=0.58,
                snippet=spoken_text, metadata={"spoken_caption": True},
            )
            written = self._item(
                "ig-w", "instagram", url, "Reel", rel=0.9, fresh=90, qual=0.58,
                snippet=written_text, metadata={"spoken_caption": False},
            )
            streams = (
                {("primary", "instagram"): [spoken], ("secondary", "instagram"): [written]}
                if order == "spoken_first"
                else {("primary", "instagram"): [written], ("secondary", "instagram"): [spoken]}
            )
            [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
            assert cand.snippet == spoken_text, order
            assert cand.source_items[0].snippet == spoken_text, order
            assert cand.source_items[0].metadata.get("spoken_caption") is True, order

    def test_same_key_reddit_promotion_syncs_primary_item_and_preserves_enrichment(self):
        comment_url = "https://www.reddit.com/r/ghostty/comments/abc123/release/c999/"
        thread_url = "https://www.reddit.com/r/ghostty/comments/abc123/release/"
        comment_copy = self._item(
            "R1",
            "reddit",
            comment_url,
            "Comment permalink title",
            rel=0.3,
            fresh=60,
            qual=0.6,
            snippet="Earlier comment copy snippet",
            engagement={"score": 412, "num_comments": 25},
            metadata={
                "engagement_source": reddit_refs.LIVE,
                "top_comments": [
                    {"id": "c1", "score": 80, "author": "alice", "excerpt": "great release", "depth": 0}
                ],
            },
        )
        thread_copy = self._item(
            "R2",
            "reddit",
            thread_url,
            "Ghostty 2.0 Released",
            rel=0.9,
            fresh=90,
            qual=0.6,
            snippet="Canonical thread snippet with full context",
            engagement={"score": None, "num_comments": 0},
            metadata={
                "engagement_source": reddit_refs.ARCTIC_PENDING,
                "top_comments": [
                    {"id": "c2", "score": 30, "author": "bob", "excerpt": "fast rendering", "depth": 0}
                ],
            },
        )
        assert fusion.source_item_key(comment_copy) == fusion.source_item_key(thread_copy)

        plan = _plan("ghostty", ["reddit"])
        streams = {
            ("primary", "reddit"): [comment_copy],
            ("secondary", "reddit"): [thread_copy],
        }
        [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)

        assert len(cand.source_items) == 1
        primary = schema.candidate_primary_item(cand)
        assert primary is cand.source_items[0]
        # Finding and citation agree on the promoted winning copy.
        assert cand.item_id == primary.item_id == "R2"
        assert cand.title == primary.title == "Ghostty 2.0 Released"
        assert cand.url == primary.url == thread_url
        # Live engagement and comments from the demoted copy were folded into primary.
        assert primary.engagement["score"] == 412
        assert primary.engagement["num_comments"] == 25
        assert primary.metadata["engagement_source"] == reddit_refs.LIVE
        assert [c["id"] for c in primary.metadata["top_comments"]] == ["c1", "c2"]
        expected_score = signals.engagement_score(primary)
        assert primary.engagement_score == expected_score
        assert cand.engagement == expected_score
        # Caller-owned top_comments dicts are isolated from candidate mutations.
        primary.metadata["top_comments"][0]["author"] = "mutated"
        assert comment_copy.metadata["top_comments"][0]["author"] == "alice"
        assert thread_copy.metadata["top_comments"][0]["author"] == "bob"

    def test_lower_scoring_reddit_duplicate_merge_isolates_caller_comment_dicts(self):
        url = "https://www.reddit.com/r/ghostty/comments/iso123/update/"
        high = self._item(
            "R1",
            "reddit",
            url,
            "Ghostty update",
            rel=0.9,
            fresh=90,
            qual=0.6,
            engagement={"score": 300, "num_comments": 20},
            metadata={
                "engagement_source": reddit_refs.LIVE,
                "top_comments": [
                    {"id": "c1", "score": 90, "author": "alice", "excerpt": "first comment", "depth": 0}
                ],
            },
        )
        low = self._item(
            "R2",
            "reddit",
            url,
            "Ghostty update",
            rel=0.3,
            fresh=60,
            qual=0.6,
            engagement={"score": 100, "num_comments": 10},
            metadata={
                "engagement_source": reddit_refs.ARCTIC,
                "top_comments": [
                    {"id": "c2", "score": 40, "author": "bob", "excerpt": "second comment", "depth": 0}
                ],
            },
        )
        plan = _plan("ghostty", ["reddit"])
        streams = {("primary", "reddit"): [high], ("secondary", "reddit"): [low]}
        [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
        fused_comments = cand.source_items[0].metadata["top_comments"]
        assert [c["id"] for c in fused_comments] == ["c1", "c2"]
        for c in fused_comments:
            c["author"] = "corrupted"
        assert high.metadata["top_comments"][0]["author"] == "alice"
        assert low.metadata["top_comments"][0]["author"] == "bob"

        # Direct merge_source_items also copies comment dicts returned by union_comments.
        merged = fusion.merge_source_items(high, low)
        for c in merged.metadata["top_comments"]:
            c["excerpt"] = "overwritten"
        assert low.metadata["top_comments"][0]["excerpt"] == "second comment"

    def test_reddit_promotion_and_merge_preserve_paired_matched_comment_date_and_url(self):
        thread_url = "https://www.reddit.com/r/python/comments/abc123/old_thread/"
        comment_url = "https://www.reddit.com/r/python/comments/abc123/old_thread/c1/"
        matched_copy = schema.SourceItem(
            item_id="R-comment",
            source="reddit",
            title="Old thread with fresh comment",
            body="Old thread with fresh comment",
            url=comment_url,
            published_at="2024-01-01",
            date_confidence="high",
            local_relevance=0.4,
            freshness=50,
            source_quality=0.6,
            engagement={"score": 20, "num_comments": 5},
            metadata={
                "matched_comment_date": "2026-11-20",
                "matched_comment_url": comment_url,
            },
        )
        higher_copy = schema.SourceItem(
            item_id="R-thread",
            source="reddit",
            title="Old thread with fresh comment (higher rank)",
            body="Old thread with fresh comment",
            url=thread_url,
            published_at="2024-01-01",
            date_confidence="high",
            local_relevance=0.95,
            freshness=50,
            source_quality=0.6,
            engagement={"score": 300, "num_comments": 50},
            metadata={},
        )
        plan = _plan("python", ["reddit"])
        streams = {
            ("primary", "reddit"): [matched_copy],
            ("secondary", "reddit"): [higher_copy],
        }
        [cand] = fusion.weighted_rrf(
            streams,
            plan,
            pool_limit=10,
            range_from="2026-10-25",
            range_to="2026-11-24",
        )
        primary = schema.candidate_primary_item(cand)
        assert primary is cand.source_items[0]
        assert primary.item_id == "R-thread"
        assert primary.metadata["matched_comment_date"] == "2026-11-20"
        assert primary.metadata["matched_comment_url"] == comment_url
        assert not schema.candidate_out_of_window(cand)

        # Newer matched_comment_date wins together with its paired matched_comment_url.
        older = schema.SourceItem(
            item_id="R-old",
            source="reddit",
            title="Old thread",
            body="Old thread",
            url=thread_url,
            published_at="2024-01-01",
            metadata={
                "matched_comment_date": "2026-11-10",
                "matched_comment_url": "https://www.reddit.com/r/python/comments/abc123/old_thread/c_old/",
            },
        )
        newer = schema.SourceItem(
            item_id="R-new",
            source="reddit",
            title="Old thread",
            body="Old thread",
            url=thread_url,
            published_at="2024-01-01",
            metadata={
                "matched_comment_date": "2026-11-22",
                "matched_comment_url": "https://www.reddit.com/r/python/comments/abc123/old_thread/c_new/",
            },
        )
        merged_newer = fusion.merge_source_items(older, newer)
        assert merged_newer.metadata["matched_comment_date"] == "2026-11-22"
        assert merged_newer.metadata["matched_comment_url"] == (
            "https://www.reddit.com/r/python/comments/abc123/old_thread/c_new/"
        )

        # When a newer date has no URL, stale URL from the older reply is cleared (never mismatched).
        newer_no_url = schema.SourceItem(
            item_id="R-new-nourl",
            source="reddit",
            title="Old thread",
            body="Old thread",
            url=thread_url,
            published_at="2024-01-01",
            metadata={"matched_comment_date": "2026-11-24"},
        )
        merged_no_url = fusion.merge_source_items(merged_newer, newer_no_url)
        assert merged_no_url.metadata["matched_comment_date"] == "2026-11-24"
        assert "matched_comment_url" not in merged_no_url.metadata

        # Same date backfills a missing URL; an older incoming date never overwrites a newer one.
        same_date_with_url = schema.SourceItem(
            item_id="R-same",
            source="reddit",
            title="Old thread",
            body="Old thread",
            url=thread_url,
            published_at="2024-01-01",
            metadata={
                "matched_comment_date": "2026-11-24",
                "matched_comment_url": "https://www.reddit.com/r/python/comments/abc123/old_thread/c_24/",
            },
        )
        fusion.merge_source_items(merged_no_url, same_date_with_url)
        assert merged_no_url.metadata["matched_comment_url"] == (
            "https://www.reddit.com/r/python/comments/abc123/old_thread/c_24/"
        )
        stale_incoming = schema.SourceItem(
            item_id="R-stale",
            source="reddit",
            title="Old thread",
            body="Old thread",
            url=thread_url,
            published_at="2024-01-01",
            metadata={
                "matched_comment_date": "2026-11-01",
                "matched_comment_url": "https://www.reddit.com/r/python/comments/abc123/old_thread/c_01/",
            },
        )
        fusion.merge_source_items(merged_no_url, stale_incoming)
        assert merged_no_url.metadata["matched_comment_date"] == "2026-11-24"
        assert merged_no_url.metadata["matched_comment_url"] == (
            "https://www.reddit.com/r/python/comments/abc123/old_thread/c_24/"
        )

    def test_as_of_window_blocks_post_cutoff_matched_comment_on_merge_and_normalize(self):
        thread_url = "https://www.reddit.com/r/python/comments/asof99/release/"
        url_in_window = "https://www.reddit.com/r/python/comments/asof99/release/c_feb20/"
        url_post_cutoff = "https://www.reddit.com/r/python/comments/asof99/release/c_mar10/"
        plan = _plan("python", ["reddit"])

        def _make_copy(item_id: str, rel: float, m_date: str, m_url: str) -> schema.SourceItem:
            return schema.SourceItem(
                item_id=item_id,
                source="reddit",
                title="Python release notes",
                body="Python release notes",
                url=thread_url,
                published_at="2026-02-10",
                date_confidence="high",
                local_relevance=rel,
                freshness=80,
                source_quality=0.6,
                engagement={"score": 150, "num_comments": 25},
                metadata={
                    "matched_comment_date": m_date,
                    "matched_comment_url": m_url,
                },
            )

        # Case 1a: Promoted higher-scoring copy carries post-cutoff reply (2026-03-10);
        # fused candidate MUST keep the in-window reply (2026-02-20) and its URL.
        copy_in = _make_copy("R-in", 0.4, "2026-02-20", url_in_window)
        copy_post_high = _make_copy("R-post-hi", 0.95, "2026-03-10", url_post_cutoff)
        [cand_promoted] = fusion.weighted_rrf(
            {("primary", "reddit"): [copy_in], ("secondary", "reddit"): [copy_post_high]},
            plan,
            pool_limit=10,
            range_from="2026-02-01",
            range_to="2026-03-01",
        )
        assert cand_promoted.source_items[0].item_id == "R-post-hi"
        assert cand_promoted.source_items[0].metadata["matched_comment_date"] == "2026-02-20"
        assert cand_promoted.source_items[0].metadata["matched_comment_url"] == url_in_window

        # Case 1b: Non-promoted lower-scoring copy carries post-cutoff reply;
        # fused candidate MUST keep the in-window reply and its URL.
        copy_in_high = _make_copy("R-in-hi", 0.95, "2026-02-20", url_in_window)
        copy_post_low = _make_copy("R-post-lo", 0.4, "2026-03-10", url_post_cutoff)
        [cand_non_promoted] = fusion.weighted_rrf(
            {("primary", "reddit"): [copy_in_high], ("secondary", "reddit"): [copy_post_low]},
            plan,
            pool_limit=10,
            range_from="2026-02-01",
            range_to="2026-03-01",
        )
        assert cand_non_promoted.source_items[0].metadata["matched_comment_date"] == "2026-02-20"
        assert cand_non_promoted.source_items[0].metadata["matched_comment_url"] == url_in_window

        # Case 2: Reverse order — post-cutoff arrives first, in-window arrives second ->
        # in-window pair replaces the out-of-window pair.
        [cand_reverse] = fusion.weighted_rrf(
            {
                ("primary", "reddit"): [_make_copy("R-post-first", 0.9, "2026-03-10", url_post_cutoff)],
                ("secondary", "reddit"): [_make_copy("R-in-second", 0.5, "2026-02-20", url_in_window)],
            },
            plan,
            pool_limit=10,
            range_from="2026-02-01",
            range_to="2026-03-01",
        )
        assert cand_reverse.source_items[0].metadata["matched_comment_date"] == "2026-02-20"
        assert cand_reverse.source_items[0].metadata["matched_comment_url"] == url_in_window

        # Case 2b: Single copy carrying ONLY a post-cutoff reply has both fields stripped.
        [cand_only_post] = fusion.weighted_rrf(
            {("primary", "reddit"): [_make_copy("R-only-post", 0.8, "2026-03-10", url_post_cutoff)]},
            plan,
            pool_limit=10,
            range_from="2026-02-01",
            range_to="2026-03-01",
        )
        assert "matched_comment_date" not in cand_only_post.source_items[0].metadata
        assert "matched_comment_url" not in cand_only_post.source_items[0].metadata

        # Case 3: _normalize_reddit drops post-cutoff and pre-window matched_comment metadata
        # while keeping in-window matched_comment metadata.
        raw_items = [
            {
                "id": "R-norm-in",
                "title": "In-window thread with in-window reply",
                "url": thread_url,
                "subreddit": "python",
                "date": "2026-02-10",
                "matched_comment": {"date": "2026-02-20", "url": url_in_window},
            },
            {
                "id": "R-norm-post",
                "title": "In-window thread with post-cutoff reply",
                "url": "https://www.reddit.com/r/python/comments/asof98/release/",
                "subreddit": "python",
                "date": "2026-02-10",
                "matched_comment": {"date": "2026-03-10", "url": url_post_cutoff},
            },
            {
                "id": "R-norm-pre",
                "title": "In-window thread with pre-window reply",
                "url": "https://www.reddit.com/r/python/comments/asof97/release/",
                "subreddit": "python",
                "date": "2026-02-10",
                "matched_comment": {
                    "date": "2026-01-15",
                    "url": "https://www.reddit.com/r/python/comments/asof97/release/c_jan15/",
                },
            },
        ]
        normalized = normalize.normalize_source_items(
            "reddit", raw_items, "2026-02-01", "2026-03-01",
        )
        by_id = {si.item_id: si for si in normalized}
        assert by_id["R-norm-in"].metadata["matched_comment_date"] == "2026-02-20"
        assert by_id["R-norm-in"].metadata["matched_comment_url"] == url_in_window
        assert "matched_comment_date" not in by_id["R-norm-post"].metadata
        assert "matched_comment_url" not in by_id["R-norm-post"].metadata
        assert "matched_comment_date" not in by_id["R-norm-pre"].metadata
        assert "matched_comment_url" not in by_id["R-norm-pre"].metadata


# ---------------------------------------------------------------------------
# HIGH-106: Recompute engagement_score, longer enrichment strings, no input mutation
# ---------------------------------------------------------------------------


class TestHigh106MergeAndImmutability:
    def test_arctic_pending_to_live_merge_recomputes_engagement_score(self):
        url = "https://www.reddit.com/r/python/comments/abc123/test/"
        pending = schema.SourceItem(
            item_id="R1",
            source="reddit",
            title="Python 3.15 released",
            body="Python 3.15 released",
            url=url,
            published_at="2026-10-25",
            date_confidence="high",
            engagement={"score": None, "num_comments": 0},
            metadata={"engagement_source": reddit_refs.ARCTIC_PENDING},
            engagement_score=None,
        )
        live = schema.SourceItem(
            item_id="R2",
            source="reddit",
            title="Python 3.15 released",
            body="Python 3.15 released",
            url=url,
            published_at="2026-10-25",
            date_confidence="high",
            engagement={"score": 500, "num_comments": 40},
            metadata={"engagement_source": reddit_refs.LIVE},
            engagement_score=None,
        )
        merged = fusion.merge_source_items(pending, live)
        expected = signals.engagement_score(merged)
        assert expected is not None and expected > 40
        assert merged.engagement["score"] == 500
        assert merged.metadata["engagement_source"] == reddit_refs.LIVE
        assert merged.engagement_score == expected

        # Verify downstream rerank credits the live score instead of treating it as 0.
        cand = schema.Candidate(
            candidate_id=url, item_id="R1", source="reddit", title=merged.title, url=url,
            snippet="", subquery_labels=["primary"], native_ranks={"primary:reddit": 1},
            local_relevance=0.8, freshness=80, engagement=merged.engagement_score,
            source_quality=0.6, rrf_score=0.02, sources=["reddit"], source_items=[merged],
            rerank_score=60.0,
        )
        zero_cand = schema.Candidate(
            candidate_id=url + "z", item_id="R0", source="reddit", title=merged.title, url=url,
            snippet="", subquery_labels=["primary"], native_ranks={"primary:reddit": 1},
            local_relevance=0.8, freshness=80, engagement=0,
            source_quality=0.6, rrf_score=0.02, sources=["reddit"], source_items=[merged],
            rerank_score=60.0,
        )
        assert not rerank._engagement_pending(cand)
        assert rerank._final_score(cand) > rerank._final_score(zero_cand) + 5.0

    def test_merge_replaces_shorter_string_enrichment_stubs_and_ignores_whitespace(self):
        a = schema.SourceItem(
            item_id="YT1", source="youtube", title="Demo", body="Demo",
            url="https://youtube.com/watch?v=1",
            metadata={"transcript_snippet": "short stub", "transcript": "   "},
        )
        b = schema.SourceItem(
            item_id="YT2", source="youtube", title="Demo", body="Demo",
            url="https://youtube.com/watch?v=1",
            metadata={
                "transcript_snippet": "full detailed transcript snippet covering Hermes Agent",
                "transcript": "complete transcript text",
            },
        )
        merged = fusion.merge_source_items(a, b)
        assert merged.metadata["transcript_snippet"] == "full detailed transcript snippet covering Hermes Agent"
        assert merged.metadata["transcript"] == "complete transcript text"

        # A shorter stub or whitespace-only copy must not overwrite the richer transcript.
        c = schema.SourceItem(
            item_id="YT3", source="youtube", title="Demo", body="Demo",
            url="https://youtube.com/watch?v=1",
            metadata={"transcript_snippet": "tiny", "transcript": " " * 200},
        )
        fusion.merge_source_items(merged, c)
        assert merged.metadata["transcript_snippet"] == "full detailed transcript snippet covering Hermes Agent"
        assert merged.metadata["transcript"] == "complete transcript text"

    def test_weighted_rrf_does_not_mutate_caller_source_items_and_syncs_live_engagement(self):
        url = "https://www.reddit.com/r/ghostty/comments/xyz789/release/"
        pending = schema.SourceItem(
            item_id="R1", source="reddit", title="Ghostty release", body="Ghostty release",
            url=url, published_at="2026-10-25", date_confidence="high",
            engagement={"score": None, "num_comments": 0},
            metadata={"engagement_source": reddit_refs.ARCTIC_PENDING},
            local_relevance=0.8, freshness=80, source_quality=0.6, engagement_score=None,
        )
        live = schema.SourceItem(
            item_id="R2", source="reddit", title="Ghostty release", body="Ghostty release",
            url=url, published_at="2026-10-25", date_confidence="high",
            engagement={"score": 412, "num_comments": 25},
            metadata={"engagement_source": reddit_refs.LIVE},
            local_relevance=0.7, freshness=80, source_quality=0.6, engagement_score=None,
        )
        stale_archive = schema.SourceItem(
            item_id="R3", source="reddit", title="Ghostty release", body="Ghostty release",
            url=url, published_at="2026-10-25", date_confidence="high",
            engagement={"score": 9000, "num_comments": 25},
            metadata={"engagement_source": reddit_refs.ARCTIC},
            local_relevance=0.6, freshness=80, source_quality=0.6, engagement_score=95,
        )
        plan = schema.QueryPlan(
            intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
            raw_topic="ghostty",
            subqueries=[
                schema.SubQuery(label=lbl, search_query="ghostty", ranking_query="ghostty",
                                sources=["reddit"], weight=1.0)
                for lbl in ("q1", "q2", "q3")
            ],
            source_weights={"reddit": 1.0},
        )
        streams = {
            ("q1", "reddit"): [pending],
            ("q2", "reddit"): [live],
            ("q3", "reddit"): [stale_archive],
        }
        [cand] = fusion.weighted_rrf(streams, plan, pool_limit=10)
        # Caller's input SourceItem was NOT mutated in place.
        assert pending.engagement == {"score": None, "num_comments": 0}
        assert pending.metadata["engagement_source"] == reddit_refs.ARCTIC_PENDING
        assert pending.engagement_score is None
        # Candidate's merged item upgraded pending -> live and rejected stale archive's 9000.
        [merged_item] = cand.source_items
        assert merged_item.engagement["score"] == 412
        assert merged_item.metadata["engagement_source"] == reddit_refs.LIVE
        expected_live_score = signals.engagement_score(merged_item)
        assert merged_item.engagement_score == expected_live_score
        assert cand.engagement == expected_live_score


# ---------------------------------------------------------------------------
# HIGH-107: Grounding Reddit enrichment attribution, dates, safe metadata & normalize
# ---------------------------------------------------------------------------


class TestHigh107GroundingRedditCommentsAndDates:
    def test_enrich_reddit_items_preserves_attribution_dates_and_handles_missing_metadata(self):
        hydrated = reddit_hydrate.Hydration(
            items=[
                # Malformed hydrated rows without metadata/post_id must not raise KeyError.
                {"engagement_source": reddit_refs.ARCTIC},
                {"engagement_source": reddit_refs.ARCTIC, "metadata": None},
                {"engagement_source": reddit_refs.ARCTIC, "metadata": {}},
                {
                    "engagement_source": reddit_refs.ARCTIC,
                    "metadata": {"post_id": "abc123"},
                    "date": "2026-10-18",
                    "selftext": "Full Reddit post body about Hermes Agent.",
                    "top_comments": [
                        {
                            "id": "c1",
                            "score": 142,
                            "author": "alice",
                            "date": "2026-10-19",
                            "url": "https://reddit.com/r/python/comments/abc123/test/c1/",
                            "is_uploader": False,
                            "body": "Hermes Agent works great in production!",
                            "depth": 0,
                        },
                        {
                            "id": "c2",
                            "score": 50,
                            "author": "op_user",
                            "date": "2026-10-19",
                            "permalink": "/r/python/comments/abc123/test/c2/",
                            "is_uploader": True,
                            "excerpt": "Thanks everyone, OP here.",
                            "depth": 1,
                        },
                        {
                            "id": "c3",
                            "score": 38,
                            "author": "bob",
                            "date": "2026-10-19",
                            "url": "https://reddit.com/r/python/comments/abc123/test/c3/",
                            "is_uploader": False,
                            "body": "Second community reaction on Hermes Agent latency.",
                            "depth": 0,
                        },
                    ],
                },
                {
                    "engagement_source": reddit_refs.ARCTIC,
                    "metadata": {"post_id": "old999"},
                    "date": "2022-03-01",
                    "selftext": "Ancient 2022 thread.",
                    "top_comments": [],
                },
            ],
            detail="arctic served 2",
            arctic_state="ok",
        )
        raw_items = [
            {
                "id": "W1",
                "title": "Undated in-window Reddit thread",
                "url": "https://www.reddit.com/r/python/comments/abc123/test/",
                "snippet": "search snippet",
                "date": None,
                "metadata": {"date_window_basis": "server_bounds"},
            },
            {
                "id": "W2",
                "title": "Undated ancient Reddit thread",
                "url": "https://www.reddit.com/r/python/comments/old999/old/",
                "snippet": "ancient snippet",
                "date": None,
                "metadata": {"date_window_basis": "keyless_current"},
            },
        ]
        with patch.object(reddit_hydrate, "hydrate", return_value=hydrated):
            enriched = grounding._enrich_reddit_items(raw_items)

        assert enriched[0]["date"] == "2026-10-18"
        assert enriched[0]["enriched_via"] == "arctic"
        assert len(enriched[0]["top_comments"]) == 3
        c1, c2, c3 = enriched[0]["top_comments"]
        assert c1 == {
            "id": "c1",
            "score": 142,
            "author": "alice",
            "date": "2026-10-19",
            "url": "https://reddit.com/r/python/comments/abc123/test/c1/",
            "is_uploader": False,
            "excerpt": "Hermes Agent works great in production!",
            "depth": 0,
        }
        assert c2["id"] == "c2"
        assert c2["author"] == "op_user"
        assert c2["is_uploader"] is True
        assert c2["url"] == "https://reddit.com/r/python/comments/abc123/test/c2/"
        assert c3["author"] == "bob"
        assert c3["is_uploader"] is False
        assert enriched[1]["date"] == "2022-03-01"

        # Normalize through grounding: ancient backfilled thread is dropped,
        # in-window thread keeps top_comments, enriched_via, body text, and copies metadata.
        orig_meta = enriched[0]["metadata"]
        normalized = normalize.normalize_source_items(
            "grounding", enriched, "2026-10-01", "2026-10-31"
        )
        assert len(normalized) == 1
        si = normalized[0]
        assert si.published_at == "2026-10-18"
        assert si.metadata["enriched_via"] == "arctic"
        assert si.metadata["date_window_basis"] == "server_bounds"
        assert len(si.metadata["top_comments"]) == 3
        assert si.metadata["top_comments"][0]["author"] == "alice"
        assert si.metadata["top_comments"][0]["is_uploader"] is False
        assert si.metadata["top_comments"][1]["is_uploader"] is True
        assert "Hermes Agent works great in production!" in si.body
        # Metadata was copied (no shared reference mutation).
        assert si.metadata is not orig_meta
        si.metadata["extra"] = "mutated"
        assert "extra" not in orig_meta

        # End-to-end render quote check: OP comment (is_uploader=True) is skipped,
        # while community comments by alice and bob surface with full attribution!
        report = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["grounding"]),
            clusters=[],
            ranked_candidates=[
                schema.Candidate(
                    candidate_id=si.url, item_id=si.item_id, source="grounding",
                    title=si.title, url=si.url, snippet=si.snippet,
                    subquery_labels=["primary"], native_ranks={"primary:grounding": 1},
                    local_relevance=0.8, freshness=80, engagement=None,
                    source_quality=1.0, rrf_score=0.02, sources=["grounding"],
                    source_items=[si], final_score=60.0,
                )
            ],
            items_by_source={"grounding": [si]},
            errors_by_source={},
        )
        lines = render._render_top_comments(report, limit=5)
        assert any("Hermes Agent works great in production!" in ln and "alice" in ln for ln in lines)
        assert any("Second community reaction on Hermes Agent latency." in ln and "bob" in ln for ln in lines)
        assert not any("op_user" in ln or "OP here" in ln for ln in lines)

    def test_enrich_reddit_items_preserves_existing_item_date(self):
        hydrated = reddit_hydrate.Hydration(
            items=[{
                "engagement_source": reddit_refs.ARCTIC,
                "metadata": {"post_id": "abc123"},
                "date": "2026-10-18",
                "selftext": "body",
                "top_comments": [],
            }],
            detail="arctic served 1",
            arctic_state="ok",
        )
        items = [{
            "url": "https://www.reddit.com/r/python/comments/abc123/test/",
            "date": "2026-10-19",
            "snippet": "orig",
        }]
        with patch.object(reddit_hydrate, "hydrate", return_value=hydrated):
            out = grounding._enrich_reddit_items(items)
        assert out[0]["date"] == "2026-10-19"

    def test_arctic_web_reddit_comments_enforce_reddit_topic_and_vote_floors(self):
        hydrated = reddit_hydrate.Hydration(
            items=[
                {
                    "engagement_source": reddit_refs.ARCTIC,
                    "metadata": {"post_id": "small1"},
                    "date": "2026-10-20",
                    "selftext": "Small Hermes Agent thread body",
                    "engagement": {"score": 40, "num_comments": 10},
                    "top_comments": [
                        {
                            "id": "offtopic_nested",
                            "score": 50,
                            "author": "tangent",
                            "date": "2026-10-20",
                            "permalink": "https://www.reddit.com/r/python/comments/small1/t/offtopic_nested/",
                            "is_uploader": False,
                            "body": "Totally unrelated lunch recipe with great sourdough crust.",
                            "depth": 1,
                        },
                        {
                            "id": "good1",
                            "score": 3,
                            "author": "alice",
                            "date": "2026-10-20",
                            "permalink": "https://www.reddit.com/r/python/comments/small1/t/good1/",
                            "is_uploader": False,
                            "body": "Hermes Agent works reliably on small workflows.",
                            "depth": 0,
                        },
                        {
                            "id": "zero_vote",
                            "score": 0,
                            "author": "zero_user",
                            "date": "2026-10-20",
                            "permalink": "/r/python/comments/small1/t/zero_vote/",
                            "is_uploader": False,
                            "body": "Hermes Agent zero-vote comment should be filtered out.",
                            "depth": 0,
                        },
                        {
                            # Non-string body must not raise TypeError during enrichment.
                            "id": "non_str",
                            "score": 1,
                            "author": "num_user",
                            "body": 12345,
                            "depth": 0,
                        },
                    ],
                },
                {
                    "engagement_source": reddit_refs.ARCTIC,
                    "metadata": {"post_id": "small3"},
                    "date": "2026-10-20",
                    "selftext": "Second small Hermes Agent thread",
                    "engagement": {"score": 20, "num_comments": 4},
                    "top_comments": [
                        {
                            "id": "good2",
                            "score": 2,
                            "author": "bob",
                            "date": "2026-10-20",
                            "permalink": "/r/python/comments/small3/t/good2/",
                            "is_uploader": False,
                            "body": "Second reply on Hermes Agent memory behavior.",
                            "depth": 0,
                        },
                    ],
                },
                {
                    "engagement_source": reddit_refs.ARCTIC,
                    "metadata": {"post_id": "big2"},
                    "date": "2026-10-20",
                    "selftext": "Big Hermes Agent thread body",
                    "engagement": {"score": 200, "num_comments": 50},
                    "top_comments": [
                        {
                            "id": "under_floor",
                            "score": 3,
                            "author": "carol",
                            "date": "2026-10-20",
                            "permalink": "/r/python/comments/big2/t/under_floor/",
                            "is_uploader": False,
                            "body": "Hermes Agent comment below 5-vote floor for large thread.",
                            "depth": 0,
                        },
                    ],
                },
            ],
            detail="arctic served 3",
            arctic_state="ok",
        )
        raw_items = [
            {
                "id": "W1",
                "title": "Hermes Agent small thread",
                "url": "https://www.reddit.com/r/python/comments/small1/t/",
                "snippet": "search snippet",
                "date": None,
                "metadata": {"date_window_basis": "server_bounds"},
            },
            {
                "id": "W2",
                "title": "Hermes Agent second small thread",
                "url": "https://www.reddit.com/r/python/comments/small3/t/",
                "snippet": "search snippet",
                "date": None,
                "metadata": {"date_window_basis": "server_bounds"},
            },
            {
                "id": "W3",
                "title": "Hermes Agent big thread",
                "url": "https://www.reddit.com/r/python/comments/big2/t/",
                "snippet": "search snippet",
                "date": None,
                "metadata": {"date_window_basis": "server_bounds"},
            },
        ]
        with patch.object(reddit_hydrate, "hydrate", return_value=hydrated):
            enriched = grounding._enrich_reddit_items(raw_items)

        assert enriched[0]["reddit_thread_score"] == 40
        assert "engagement" not in enriched[0]
        assert enriched[0]["top_comments"][0]["url"] == (
            "https://www.reddit.com/r/python/comments/small1/t/offtopic_nested/"
        )
        assert enriched[0]["top_comments"][3]["excerpt"] == "12345"

        normalized = normalize.normalize_source_items(
            "grounding", enriched, "2026-10-01", "2026-10-31"
        )
        assert len(normalized) == 3
        small_si, small3_si, big_si = normalized
        assert small_si.source == "grounding"
        assert small_si.metadata["reddit_thread_score"] == 40
        assert big_si.metadata["reddit_thread_score"] == 200
        assert render._quote_pool_item_floor(small_si) == 2
        assert render._quote_pool_item_floor(small3_si) == 2
        assert render._quote_pool_item_floor(big_si) == 5

        report = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["grounding"]),
            clusters=[],
            ranked_candidates=[
                schema.Candidate(
                    candidate_id=si.url, item_id=si.item_id, source="grounding",
                    title=si.title, url=si.url, snippet=si.snippet,
                    subquery_labels=["primary"], native_ranks={"primary:grounding": idx},
                    local_relevance=0.8, freshness=80, engagement=None,
                    source_quality=1.0, rrf_score=0.02, sources=["grounding"],
                    source_items=[si], final_score=60.0 - idx,
                )
                for idx, si in enumerate(normalized, start=1)
            ],
            items_by_source={"grounding": normalized},
            errors_by_source={},
        )
        lines = render._render_top_comments(report, limit=10)
        joined = "\n".join(lines)
        # Nested off-topic reply, 0-vote comment, and big-thread under-floor comment are excluded.
        assert "sourdough crust" not in joined
        assert "zero-vote comment" not in joined
        assert "below 5-vote floor" not in joined
        # Valid top-level comments on the small threads clear the scaled floor (2) and use 'upvotes'.
        assert any(
            "Hermes Agent works reliably on small workflows." in ln
            and "🟠 u/alice on r/python · 3 upvotes" in ln
            for ln in lines
        )
        assert any(
            "Second reply on Hermes Agent memory behavior." in ln
            and "🟠 u/bob on r/python · 2 upvotes" in ln
            for ln in lines
        )

    def test_fused_unenriched_and_arctic_web_copies_keep_reddit_quote_checks_and_byline(self):
        short_url = "https://redd.it/fus123"
        bare_web = schema.SourceItem(
            item_id="W1",
            source="grounding",
            title="Hermes Agent on redd.it",
            body="Hermes Agent on redd.it",
            url=short_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={"date_window_basis": "server_bounds"},
        )
        arctic_web = schema.SourceItem(
            item_id="W2",
            source="grounding",
            title="Hermes Agent on redd.it",
            body="Hermes Agent on redd.it",
            url=short_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "enriched_via": "arctic",
                "reddit_thread_score": 40,
                "reddit_subreddit": "LocalLLaMA",
                "top_comments": [
                    {
                        "id": "off1",
                        "score": 90,
                        "author": "tangent",
                        "excerpt": "Completely unrelated baking recipe talk.",
                        "depth": 1,
                    },
                    {
                        "id": "ok1",
                        "score": 3,
                        "author": "alice",
                        "excerpt": "Hermes Agent runs smoothly on local hardware.",
                        "depth": 0,
                    },
                    {
                        "id": "zero1",
                        "score": 0,
                        "author": "nobody",
                        "excerpt": "Hermes Agent zero-vote reply must be dropped.",
                        "depth": 0,
                    },
                ],
            },
        )
        merged = fusion.merge_source_items(bare_web, arctic_web)
        assert merged.source == "grounding"
        assert merged.metadata["enriched_via"] == "arctic"
        assert merged.metadata["reddit_thread_score"] == 40
        assert merged.metadata["reddit_subreddit"] == "LocalLLaMA"

        # A Reddit URL web item WITHOUT enriched_via still triggers Reddit quote checks + fallback byline.
        untagged_reddit_web = schema.SourceItem(
            item_id="W3",
            source="grounding",
            title="Hermes Agent short link",
            body="Hermes Agent short link",
            url="https://redd.it/untag9",
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "top_comments": [
                    {
                        "id": "off2",
                        "score": 70,
                        "author": "offuser",
                        "excerpt": "Unrelated movie review nested reply.",
                        "depth": 1,
                    },
                    {
                        "id": "ok2",
                        "score": 12,
                        "author": "bob",
                        "excerpt": "Hermes Agent handles tool calls cleanly.",
                        "depth": 0,
                    },
                ],
            },
        )
        assert render._comment_source(untagged_reddit_web) == "reddit"

        report = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["grounding"]),
            clusters=[],
            ranked_candidates=[
                schema.Candidate(
                    candidate_id=si.url, item_id=si.item_id, source="grounding",
                    title=si.title, url=si.url, snippet=si.snippet,
                    subquery_labels=["primary"], native_ranks={"primary:grounding": idx},
                    local_relevance=0.8, freshness=80, engagement=None,
                    source_quality=1.0, rrf_score=0.02, sources=["grounding"],
                    source_items=[si], final_score=60.0 - idx,
                )
                for idx, si in enumerate([merged, untagged_reddit_web], start=1)
            ],
            items_by_source={"grounding": [merged, untagged_reddit_web]},
            errors_by_source={},
        )
        lines = render._render_top_comments(report, limit=10)
        joined = "\n".join(lines)
        assert "baking recipe" not in joined
        assert "zero-vote reply" not in joined
        assert "movie review" not in joined
        assert any(
            "Hermes Agent runs smoothly on local hardware." in ln
            and "🟠 u/alice on r/LocalLLaMA · 3 upvotes" in ln
            for ln in lines
        )
        assert any(
            "Hermes Agent handles tool calls cleanly." in ln
            and "🟠 u/bob on Reddit · 12 upvotes" in ln
            for ln in lines
        )

    def test_same_reddit_thread_capped_at_two_quotes_across_native_and_web_copies(self):
        thread_url = "https://www.reddit.com/r/python/comments/cap123/hermes_agent/"
        native_reddit = schema.SourceItem(
            item_id="R1",
            source="reddit",
            title="Hermes Agent release thread",
            body="Hermes Agent release thread",
            url=thread_url,
            container="python",
            published_at="2026-10-20",
            date_confidence="high",
            engagement={"score": 200, "num_comments": 40},
            metadata={
                "top_comments": [
                    {"id": "n1", "score": 80, "author": "native_one", "excerpt": "Native top comment about Hermes Agent speed.", "depth": 0},
                    {"id": "n2", "score": 45, "author": "native_two", "excerpt": "Native second comment about Hermes Agent setup.", "depth": 0},
                ]
            },
        )
        web_reddit = schema.SourceItem(
            item_id="W1",
            source="grounding",
            title="Hermes Agent release thread",
            body="Hermes Agent release thread",
            url=thread_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "enriched_via": "arctic",
                "reddit_thread_score": 200,
                "reddit_subreddit": "python",
                "top_comments": [
                    {"id": "w1", "score": 70, "author": "web_one", "excerpt": "Web Arctic comment one on Hermes Agent.", "depth": 0},
                    {"id": "w2", "score": 35, "author": "web_two", "excerpt": "Web Arctic comment two on Hermes Agent.", "depth": 0},
                ],
            },
        )

        # Case 1: Both items fused into the same candidate -> merged comments sort by votes
        # (80, 70, 45, 35), so the top 2 highest-voted win and total is capped at 2.
        fused_cand = schema.Candidate(
            candidate_id=thread_url,
            item_id="R1",
            source="reddit",
            title="Hermes Agent release thread",
            url=thread_url,
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=80,
            engagement=60,
            source_quality=0.6,
            rrf_score=0.03,
            sources=["reddit", "grounding"],
            source_items=[native_reddit, web_reddit],
            final_score=70.0,
        )
        report_fused = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["reddit", "grounding"]),
            clusters=[],
            ranked_candidates=[fused_cand],
            items_by_source={"reddit": [native_reddit], "grounding": [web_reddit]},
            errors_by_source={},
        )
        fused_lines = render._render_top_comments(report_fused, limit=10)
        quote_lines_fused = [ln for ln in fused_lines if ln.startswith("- ")]
        assert len(quote_lines_fused) == 2
        assert "native_one" in quote_lines_fused[0]
        assert "web_one" in quote_lines_fused[1]
        assert not any("native_two" in ln or "web_two" in ln for ln in quote_lines_fused)

        # Case 2: Two separate candidates referencing the same Reddit thread -> still capped at 2 quotes total.
        cand_a = schema.Candidate(
            candidate_id="cand-native",
            item_id="R1",
            source="reddit",
            title="Hermes Agent release thread",
            url=thread_url,
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=80,
            engagement=60,
            source_quality=0.6,
            rrf_score=0.03,
            sources=["reddit"],
            source_items=[native_reddit],
            final_score=70.0,
        )
        cand_b = schema.Candidate(
            candidate_id="cand-web",
            item_id="W1",
            source="grounding",
            title="Hermes Agent release thread",
            url=thread_url,
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:grounding": 1},
            local_relevance=0.8,
            freshness=80,
            engagement=None,
            source_quality=1.0,
            rrf_score=0.02,
            sources=["grounding"],
            source_items=[web_reddit],
            final_score=65.0,
        )
        report_split = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["reddit", "grounding"]),
            clusters=[],
            ranked_candidates=[cand_a, cand_b],
            items_by_source={"reddit": [native_reddit], "grounding": [web_reddit]},
            errors_by_source={},
        )
        split_lines = render._render_top_comments(report_split, limit=10)
        quote_lines_split = [ln for ln in split_lines if ln.startswith("- ")]
        assert len(quote_lines_split) == 2

    def test_merged_reddit_group_sorts_by_votes_and_places_pending_last(self):
        # Case 1: Native Reddit copy has a 2-upvote reply (below the 5-upvote item floor for a
        # 200-score thread), while the web copy has a 20-upvote reply. Merged comments must sort
        # by descending score so the 20-upvote reply sits at index 0 and clears the item floor!
        thread_url = "https://www.reddit.com/r/python/comments/sort123/hermes_agent/"
        native_low = schema.SourceItem(
            item_id="R-low",
            source="reddit",
            title="Hermes Agent benchmark thread",
            body="Hermes Agent benchmark thread",
            url=thread_url,
            container="python",
            published_at="2026-10-20",
            date_confidence="high",
            engagement={"score": 200, "num_comments": 50},
            metadata={
                "top_comments": [
                    {
                        "id": "low1",
                        "score": 2,
                        "author": "low_voter",
                        "excerpt": "Low-vote native reply on Hermes Agent benchmarks.",
                        "depth": 0,
                    },
                ]
            },
        )
        web_high = schema.SourceItem(
            item_id="W-high",
            source="grounding",
            title="Hermes Agent benchmark thread",
            body="Hermes Agent benchmark thread",
            url=thread_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "enriched_via": "arctic",
                "reddit_thread_score": 200,
                "reddit_subreddit": "python",
                "top_comments": [
                    {
                        "id": "high1",
                        "score": 20,
                        "author": "high_voter",
                        "excerpt": "High-vote web reply proving Hermes Agent speed.",
                        "depth": 0,
                    },
                ],
            },
        )
        cand_votes = schema.Candidate(
            candidate_id=thread_url,
            item_id="R-low",
            source="reddit",
            title="Hermes Agent benchmark thread",
            url=thread_url,
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=80,
            engagement=60,
            source_quality=0.6,
            rrf_score=0.03,
            sources=["reddit", "grounding"],
            source_items=[native_low, web_high],
            final_score=70.0,
        )
        report_votes = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["reddit", "grounding"]),
            clusters=[],
            ranked_candidates=[cand_votes],
            items_by_source={"reddit": [native_low], "grounding": [web_high]},
            errors_by_source={},
        )
        lines_votes = render._render_top_comments(report_votes, limit=10)
        quote_lines_votes = [ln for ln in lines_votes if ln.startswith("- ")]
        assert len(quote_lines_votes) == 2
        assert "high_voter" in quote_lines_votes[0]
        assert "20 upvotes" in quote_lines_votes[0]
        assert "low_voter" in quote_lines_votes[1]
        assert "2 upvotes" in quote_lines_votes[1]

        # Case 2: Native Reddit copy has two score_pending replies while web copy has a scored
        # 15-upvote reply -> scored reply must lead ahead of pending replies (never crowded out).
        pending_url = "https://www.reddit.com/r/python/comments/pend456/hermes_agent/"
        native_pending = schema.SourceItem(
            item_id="R-pend",
            source="reddit",
            title="Hermes Agent launch discussion",
            body="Hermes Agent launch discussion",
            url=pending_url,
            container="python",
            published_at="2026-10-20",
            date_confidence="high",
            engagement={"score": None, "num_comments": 10},
            metadata={
                "engagement_source": reddit_refs.ARCTIC_PENDING,
                "top_comments": [
                    {
                        "id": "p1",
                        "score": None,
                        "score_pending": True,
                        "author": "pending_one",
                        "excerpt": "First pending comment about Hermes Agent launch.",
                        "depth": 0,
                    },
                    {
                        "id": "p2",
                        "score": None,
                        "score_pending": True,
                        "author": "pending_two",
                        "excerpt": "Second pending comment about Hermes Agent launch.",
                        "depth": 0,
                    },
                ],
            },
        )
        web_scored = schema.SourceItem(
            item_id="W-scored",
            source="grounding",
            title="Hermes Agent launch discussion",
            body="Hermes Agent launch discussion",
            url=pending_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "enriched_via": "arctic",
                "reddit_thread_score": 150,
                "reddit_subreddit": "python",
                "top_comments": [
                    {
                        "id": "s1",
                        "score": 15,
                        "author": "scored_user",
                        "excerpt": "Scored 15-upvote comment on Hermes Agent launch.",
                        "depth": 0,
                    },
                ],
            },
        )
        cand_pending = schema.Candidate(
            candidate_id=pending_url,
            item_id="R-pend",
            source="reddit",
            title="Hermes Agent launch discussion",
            url=pending_url,
            snippet="",
            subquery_labels=["primary"],
            native_ranks={"primary:reddit": 1},
            local_relevance=0.9,
            freshness=80,
            engagement=50,
            source_quality=0.6,
            rrf_score=0.03,
            sources=["reddit", "grounding"],
            source_items=[native_pending, web_scored],
            final_score=70.0,
        )
        report_pending = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["reddit", "grounding"]),
            clusters=[],
            ranked_candidates=[cand_pending],
            items_by_source={"reddit": [native_pending], "grounding": [web_scored]},
            errors_by_source={},
        )
        lines_pending = render._render_top_comments(report_pending, limit=10)
        quote_lines_pending = [ln for ln in lines_pending if ln.startswith("- ")]
        assert len(quote_lines_pending) == 2
        assert "scored_user" in quote_lines_pending[0]
        assert "15 upvotes" in quote_lines_pending[0]
        assert "pending_one" in quote_lines_pending[1]
        assert not any("pending_two" in ln for ln in quote_lines_pending)

    def test_arctic_web_reddit_comments_apply_reddit_rules_in_cards_and_render_full(self):
        thread_url = "https://www.reddit.com/r/python/comments/card99/hermes_agent/"
        web_reddit = schema.SourceItem(
            item_id="W-card",
            source="grounding",
            title="Hermes Agent Reddit discussion found via web",
            body="Hermes Agent Reddit discussion found via web",
            snippet="Web snippet for Hermes Agent thread",
            url=thread_url,
            published_at="2026-10-20",
            date_confidence="high",
            metadata={
                "enriched_via": "arctic",
                "reddit_thread_score": 120,
                "reddit_subreddit": "python",
                "top_comments": [
                    {
                        "id": "c0",
                        "score": 0,
                        "author": "zero_user",
                        "excerpt": "Zero-vote reply should never appear on ranked cards.",
                        "depth": 0,
                    },
                    {
                        "id": "c5",
                        "score": 5,
                        "author": "five_user",
                        "excerpt": "Five-vote reply is below Reddit card floor.",
                        "depth": 0,
                    },
                    {
                        "id": "c14",
                        "score": 14,
                        "author": "alice",
                        "excerpt": "Fourteen-vote reply clears Reddit card floor cleanly.",
                        "depth": 0,
                    },
                ],
            },
        )
        # _top_comments_list enforces Reddit's 10-upvote floor for web-found Reddit threads.
        filtered = render._top_comments_list(web_reddit)
        assert [c["id"] for c in filtered] == ["c14"]

        cand = schema.Candidate(
            candidate_id=thread_url,
            item_id="W-card",
            source="grounding",
            title=web_reddit.title,
            url=thread_url,
            snippet=web_reddit.snippet,
            subquery_labels=["primary"],
            native_ranks={"primary:grounding": 1},
            local_relevance=0.9,
            freshness=85,
            engagement=None,
            source_quality=1.0,
            rrf_score=0.03,
            sources=["grounding"],
            source_items=[web_reddit],
            final_score=72.0,
        )
        cluster = schema.Cluster(
            cluster_id="cl1",
            title=web_reddit.title,
            candidate_ids=[cand.candidate_id],
            representative_ids=[cand.candidate_id],
            sources=["grounding"],
            score=cand.final_score,
        )
        report = schema.Report(
            topic="Hermes Agent",
            range_from="2026-10-01",
            range_to="2026-10-31",
            generated_at="2026-10-31T00:00:00Z",
            provider_runtime=schema.ProviderRuntime("local", "m", "m"),
            query_plan=_plan("Hermes Agent", ["grounding"]),
            clusters=[cluster],
            ranked_candidates=[cand],
            items_by_source={"grounding": [web_reddit]},
            errors_by_source={},
        )

        # Ranked card keeps [grounding] badge (item.source stays "grounding", never rewritten to reddit),
        # rejects <10-vote replies, and formats qualifying replies with Reddit's u/ byline and "upvotes" label.
        card_text = "\n".join(render._render_candidate(cand, "1.", report))
        assert "1. [grounding]" in card_text
        assert "1. [reddit]" not in card_text
        assert "zero_user" not in card_text
        assert "five_user" not in card_text
        assert "u/alice (14 upvotes):" in card_text
        assert "14 votes" not in card_text

        compact_text = render.render_compact(report)
        clusters_section = compact_text.split("## Top Community Comments")[0]
        assert "[grounding]" in clusters_section
        assert "[reddit]" not in clusters_section
        assert "u/alice (14 upvotes):" in clusters_section
        assert "zero_user" not in compact_text
        assert "five_user" not in clusters_section

        # render_full also uses Reddit's u/ byline and "upvotes" label while keeping source="grounding".
        full_text = render.render_full(report)
        assert "Top comment u/alice (14 upvotes):" in full_text
        assert "Top comment u/zero_user (0 upvotes):" in full_text
        assert "14 votes" not in full_text
        assert web_reddit.source == "grounding"


# ---------------------------------------------------------------------------
# HIGH-108: UTC clock in grounding._now() for east-of-UTC Serper relative dates
# ---------------------------------------------------------------------------


class TestHigh108GroundingUtcClock:
    def test_now_returns_timezone_aware_utc(self):
        now = grounding._now()
        assert now.tzinfo is timezone.utc

    def test_serper_relative_dates_stay_in_utc_window_for_east_of_utc_users(self):
        # 2026-03-27T20:00:00Z is already 2026-03-28 05:00 in UTC+9 (Tokyo).
        # With UTC _now(), fresh Serper relative results resolve to 2026-03-27
        # and stay inside the UTC date_range ("2026-02-25", "2026-03-27").
        utc_now = datetime(2026, 3, 27, 20, 0, tzinfo=timezone.utc)
        mock_response = {
            "organic": [
                {"title": "Breaking Now", "link": "https://example.com/1", "snippet": "s", "date": "just now"},
                {"title": "Breaking Today", "link": "https://example.com/2", "snippet": "s", "date": "today"},
                {"title": "Breaking Mins", "link": "https://example.com/3", "snippet": "s", "date": "15 minutes ago"},
                {"title": "Breaking Hours", "link": "https://example.com/4", "snippet": "s", "date": "2 hours ago"},
            ]
        }
        with patch("lib.grounding.http.request", return_value=mock_response), \
             patch("lib.grounding._now", return_value=utc_now):
            items, _artifact = grounding.serper_search(
                "breaking", ("2026-02-25", "2026-03-27"), "fake-key"
            )
        assert len(items) == 4
        assert all(item["date"] == "2026-03-27" for item in items)
