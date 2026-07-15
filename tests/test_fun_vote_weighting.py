"""Tests for crowd-vote weighting in the fun judge (Best Takes).

Covers:
- U2: signals.top_comment_vote_signal (per-platform normalized [0,1] vote signal)
- U1: rerank._extract_comment_text_scored / _build_fun_prompt (votes in the LLM prompt)
- U4: rerank._apply_single_fun_fallback (fallback uses the vote signal)
- U3: render._render_best_takes (relevance-gated, confidence-scaled, bounded nudge)
"""

import pytest

from lib import schema, signals
from lib.rerank import (
    _apply_single_fun_fallback,
    _build_fun_prompt,
    _extract_comment_text_scored,
)
from lib import render


def _candidate(
    *,
    source: str = "reddit",
    title: str = "Some Title",
    snippet: str = "",
    top_comments=None,
    fun_score=None,
    local_relevance: float = 0.8,
    explanation: str | None = None,
    final_score: float = 50.0,
    engagement=0.0,
) -> schema.Candidate:
    source_items = []
    if top_comments is not None:
        source_items.append(
            schema.SourceItem(
                item_id="si-1",
                source=source,
                title=title,
                body="",
                url=f"https://example.com/{source}/1",
                metadata={"top_comments": top_comments},
            )
        )
    c = schema.Candidate(
        candidate_id="c-1",
        item_id="i-1",
        source=source,
        title=title,
        url=f"https://example.com/{source}/1",
        snippet=snippet,
        subquery_labels=["q1"],
        native_ranks={source: 1},
        local_relevance=local_relevance,
        freshness=50,
        engagement=engagement,
        source_quality=0.5,
        rrf_score=0.01,
        source_items=source_items,
    )
    c.fun_score = fun_score
    c.explanation = explanation
    c.final_score = final_score
    return c


# --------------------------------------------------------------------------- U2

class TestTopCommentVoteSignal:
    def test_cross_platform_comparable(self):
        """A 66-upvote Reddit comment and a 22,821-like TikTok comment land on a
        comparable scale -- neither platform dominates by raw count."""
        reddit = _candidate(source="reddit", top_comments=[{"body": "x", "score": 66}])
        tiktok = _candidate(source="tiktok", top_comments=[{"body": "x", "score": 22821}])
        rs = signals.top_comment_vote_signal(reddit)
        ts = signals.top_comment_vote_signal(tiktok)
        # Both substantial, and TikTok's 22k does not swamp Reddit's 66 by 100x.
        assert 0.3 < rs < 1.0
        assert 0.3 < ts <= 1.0
        assert ts / max(rs, 1e-9) < 2.5

    def test_zero_or_missing_votes(self):
        assert signals.top_comment_vote_signal(_candidate(top_comments=[{"body": "x"}])) == 0.0
        assert signals.top_comment_vote_signal(_candidate(top_comments=[{"body": "x", "score": 0}])) == 0.0
        assert signals.top_comment_vote_signal(_candidate(top_comments=None)) == 0.0

    def test_monotonic_within_platform(self):
        low = _candidate(source="reddit", top_comments=[{"body": "x", "score": 10}])
        high = _candidate(source="reddit", top_comments=[{"body": "x", "score": 5000}])
        assert signals.top_comment_vote_signal(high) > signals.top_comment_vote_signal(low)

    def test_bounded_zero_to_one(self):
        for score in (1, 100, 6100, 39000, 10_000_000):
            sig = signals.top_comment_vote_signal(
                _candidate(source="tiktok", top_comments=[{"body": "x", "score": score}])
            )
            assert 0.0 <= sig <= 1.0


# --------------------------------------------------------------------------- U1

class TestVotesInFunPrompt:
    def test_scored_extract_prefixes_vote_count(self):
        c = _candidate(top_comments=[{"body": "the crowd loved this", "score": 14200}])
        text = _extract_comment_text_scored(c)
        assert "[+14200]" in text
        assert "the crowd loved this" in text

    def test_scored_extract_no_score_no_prefix(self):
        c = _candidate(top_comments=[{"body": "no score here"}])
        text = _extract_comment_text_scored(c)
        assert "no score here" in text
        assert "[+" not in text

    def test_scored_extract_negative_score_no_malformed_prefix(self):
        """A negative score must not emit a misleading [+-N] prefix (Greptile #592)."""
        c = _candidate(top_comments=[{"body": "downvoted line", "score": -3}])
        text = _extract_comment_text_scored(c)
        assert "downvoted line" in text
        assert "[+-3]" not in text
        assert "[+" not in text

    def test_prompt_contains_traction_guidance(self):
        c = _candidate(top_comments=[{"body": "lol", "score": 99}])
        prompt = _build_fun_prompt("test topic", [c])
        assert "[+99]" in prompt
        # The judge is told votes = traction, not funniness.
        assert "TRACTION" in prompt


# --------------------------------------------------------------------------- U4

class TestFallbackUsesVoteSignal:
    def test_high_vote_top_comment_scores_higher(self):
        low = _candidate(source="reddit", title="t", snippet="s", top_comments=[{"body": "ok", "score": 5}])
        high = _candidate(source="reddit", title="t", snippet="s", top_comments=[{"body": "ok", "score": 4000}])
        _apply_single_fun_fallback(low)
        _apply_single_fun_fallback(high)
        assert high.fun_score > low.fun_score

    def test_fallback_bounded(self):
        c = _candidate(source="tiktok", top_comments=[{"body": "lol bruh", "score": 10_000_000}])
        _apply_single_fun_fallback(c)
        assert 0.0 <= c.fun_score <= 100.0

    def test_fallback_without_votes_still_scores(self):
        c = _candidate(title="hilarious bit", snippet="", top_comments=[{"body": "bruh"}])
        _apply_single_fun_fallback(c)
        assert c.fun_score is not None and c.fun_score > 0


# --------------------------------------------------------------------------- U3

def _render(cands, level="medium"):
    p = render._FUN_LEVELS[level]
    return "\n".join(
        render._render_best_takes(cands, limit=p["limit"], threshold=p["threshold"], vote_weight=p["vote_weight"])
    )


class TestBestTakesVoteWeighting:
    # Best Takes displays the candidate TITLE when the top comment body is longer
    # than it, so these tests use distinctive long titles + longer comment bodies
    # and assert on the titles.
    def test_relevance_gate_excludes_entity_miss(self):
        """An off-topic-but-viral comment (entity-miss) never reaches Best Takes,
        even with a huge vote count and a high fun_score."""
        offtopic = _candidate(
            source="youtube", title="JamesMayReactsClip", fun_score=85,
            top_comments=[{"body": "James May is a great man and a true friend", "score": 39000}],
            explanation="fallback-local-score (entity-miss demotion)", final_score=0.0,
        )
        ontopic_a = _candidate(source="reddit", title="VelcroShirtReturn", fun_score=88,
                               top_comments=[{"body": "the velcro on my camp chair bag ate it", "score": 66}])
        ontopic_b = _candidate(source="reddit", title="BuriedInBaggies", fun_score=82,
                               top_comments=[{"body": "when i die bury me in my baggies", "score": 73}])
        out = _render([offtopic, ontopic_a, ontopic_b])
        assert "JamesMayReactsClip" not in out
        assert "VelcroShirtReturn" in out

    def test_zero_final_score_excluded(self):
        dead = _candidate(title="DeadZeroScore", fun_score=90, final_score=0.0,
                          top_comments=[{"body": "high voted but score zero item", "score": 100}])
        a = _candidate(title="FunnyAlpha", fun_score=88, top_comments=[{"body": "funny alpha line here", "score": 50}])
        b = _candidate(title="FunnyBeta", fun_score=85, top_comments=[{"body": "funny beta line here", "score": 50}])
        out = _render([dead, a, b])
        assert "DeadZeroScore" not in out

    def test_funny_floor_blocks_high_vote_unfunny(self):
        """A high-voted but unfunny comment (fun below the floor) is excluded."""
        rant = _candidate(title="LawyerRant", fun_score=15,
                          top_comments=[{"body": "pay a lawyer to send a letter to their legal dept", "score": 1720}])
        a = _candidate(title="FunnyAlpha", fun_score=80, top_comments=[{"body": "funny alpha line here", "score": 40}])
        b = _candidate(title="FunnyBeta", fun_score=78, top_comments=[{"body": "funny beta line here", "score": 40}])
        out = _render([rant, a, b])
        assert "LawyerRant" not in out

    def test_votes_promote_funnyish_over_threshold(self):
        """A funny-ish on-topic comment (fun 55) with strong on-topic votes clears
        the medium threshold (70) via the effective score -- the empty-Best-Takes fix."""
        promoted = _candidate(source="reddit", title="PromotedGem", fun_score=55, local_relevance=1.0,
                              top_comments=[{"body": "a promoted gem the crowd loved", "score": 6000}])
        other = _candidate(source="reddit", title="AlreadyFunny", fun_score=72,
                           top_comments=[{"body": "already funny on its own merit", "score": 10}])
        # Without votes, PromotedGem (fun 55) would not clear medium's 70 threshold.
        baseline = _render([
            _candidate(source="reddit", title="PromotedGem", fun_score=55,
                       top_comments=[{"body": "a promoted gem the crowd loved"}]),
            other,
        ])
        assert "PromotedGem" not in baseline
        out = _render([promoted, other])
        assert "PromotedGem" in out

    def test_bounded_amplification_does_not_overturn_humor_gap(self):
        """fun 90 / tiny votes still ranks above fun 55 / max votes at medium."""
        gem = _candidate(source="reddit", title="GhostOfYvonGem", fun_score=90, local_relevance=1.0,
                         top_comments=[{"body": "the ghost of yvon weeps for this funko pop civilization", "score": 26}])
        viral = _candidate(source="tiktok", title="MidButViral", fun_score=55, local_relevance=1.0,
                           top_comments=[{"body": "mid but extremely viral comment here", "score": 50000}])
        out = _render([gem, viral])
        assert out.index("GhostOfYvonGem") < out.index("MidButViral")

    def test_meaningful_at_medium_orders_by_votes(self):
        """Equal fun_score, different votes -> ordered by votes, and the effect is
        more than a hairline tiebreaker at medium."""
        hi = _candidate(source="reddit", title="HighVotedItem", fun_score=72, local_relevance=1.0,
                        top_comments=[{"body": "high voted comment body here", "score": 5000}])
        lo = _candidate(source="reddit", title="LowVotedItem", fun_score=72, local_relevance=1.0,
                        top_comments=[{"body": "low voted comment body here", "score": 5}])
        eff_hi = render._effective_fun_score(hi, render._FUN_LEVELS["medium"]["vote_weight"])
        eff_lo = render._effective_fun_score(lo, render._FUN_LEVELS["medium"]["vote_weight"])
        assert eff_hi - eff_lo > 5.0  # meaningful, not a tiebreaker
        out = _render([hi, lo])
        assert out.index("HighVotedItem") < out.index("LowVotedItem")

    def test_level_scaling_high_more_than_low(self):
        c = _candidate(source="reddit", fun_score=72, local_relevance=1.0,
                       top_comments=[{"body": "x", "score": 5000}])
        eff_low = render._effective_fun_score(c, render._FUN_LEVELS["low"]["vote_weight"])
        eff_high = render._effective_fun_score(c, render._FUN_LEVELS["high"]["vote_weight"])
        base = c.fun_score
        assert (eff_high - base) > (eff_low - base)

    def test_confidence_scaling(self):
        weight = render._FUN_LEVELS["medium"]["vote_weight"]
        high_conf = _candidate(source="reddit", fun_score=72, local_relevance=1.0,
                               top_comments=[{"body": "x", "score": 5000}])
        low_conf = _candidate(source="reddit", fun_score=72, local_relevance=0.2,
                              top_comments=[{"body": "x", "score": 5000}])
        assert render._effective_fun_score(high_conf, weight) > render._effective_fun_score(low_conf, weight)

    def test_crowd_boost_tag_when_votes_lift_ranking(self):
        """A vote-boosted item is flagged '+crowd' so a lower-fun item ranking
        above a higher-fun one reads correctly (Greptile #592)."""
        boosted = _candidate(source="reddit", title="BoostedItem", fun_score=72, local_relevance=1.0,
                             top_comments=[{"body": "crowd loved this line a lot", "score": 6000}])
        plain = _candidate(source="reddit", title="PlainItem", fun_score=90,
                           top_comments=[{"body": "very funny on its own merit here"}])
        out = _render([boosted, plain])
        assert "+crowd" in out
        # The plain (no-votes) item carries no crowd tag.
        plain_line = [ln for ln in out.splitlines() if "PlainItem" in ln][0]
        assert "+crowd" not in plain_line

    def test_no_votes_preserves_pure_fun_ordering(self):
        """With no comment votes, Best Takes ordering matches pure fun_score (no regression)."""
        a = _candidate(source="reddit", title="FunnyAlpha", fun_score=90, top_comments=[{"body": "funny alpha line here"}])
        b = _candidate(source="reddit", title="FunnyBeta", fun_score=80, top_comments=[{"body": "funny beta line here"}])
        out = _render([a, b])
        assert out.index("FunnyAlpha") < out.index("FunnyBeta")
