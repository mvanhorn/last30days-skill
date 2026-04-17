"""Tests for the fun judge heuristic fallback in rerank.py."""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib import schema
from lib.rerank import (
    _apply_fun_fallback,
    _apply_single_fun_fallback,
    _extract_comment_text,
    _score_comments_per_candidate,
)


def _make_candidate(
    title: str = "Some Title",
    snippet: str = "",
    engagement: float | None = 0.0,
    top_comments: list[dict] | None = None,
    parent_raw_engagement: int | None = None,
) -> schema.Candidate:
    """Build a minimal Candidate with optional source_items carrying top_comments."""
    source_items = []
    if top_comments is not None:
        item_engagement = {"score": parent_raw_engagement} if parent_raw_engagement is not None else {}
        source_items.append(
            schema.SourceItem(
                item_id="si-1",
                source="reddit",
                title=title,
                body="",
                url="https://reddit.com/r/test/1",
                engagement=item_engagement,
                metadata={"top_comments": top_comments},
            )
        )
    return schema.Candidate(
        candidate_id="c-1",
        item_id="i-1",
        source="reddit",
        title=title,
        url="https://reddit.com/r/test/1",
        snippet=snippet,
        subquery_labels=["q1"],
        native_ranks={"reddit": 1},
        local_relevance=0.5,
        freshness=50,
        engagement=engagement,
        source_quality=0.5,
        rrf_score=0.01,
        source_items=source_items,
    )


class TestFunFallbackCommentText:
    """Heuristic fallback reads comment text, not just title+snippet."""

    def test_comment_with_lmao_gets_marker_bonus(self):
        """A candidate with 'lmao' in a top_comment should get the marker bonus."""
        candidate = _make_candidate(
            title="Boring press conference recap",
            snippet="Coach talked about the game plan.",
            top_comments=[{"body": "lmao this is gold"}],
        )
        _apply_single_fun_fallback(candidate)
        # marker_bonus = 10, should be reflected in fun_score
        assert candidate.fun_score is not None
        assert candidate.fun_score >= 10.0
        assert candidate.fun_explanation == "heuristic-fallback"

    def test_short_punchy_comment_higher_shortness(self):
        """A candidate with a short punchy comment should score higher shortness
        bonus compared to one with a very long title and snippet."""
        short_candidate = _make_candidate(
            title="Hot dogs",
            snippet="",
            top_comments=[{"body": "bro what"}],
        )
        long_candidate = _make_candidate(
            title="A very long and detailed analysis of the upcoming season with comprehensive breakdown of every roster move and coaching decision that happened over the past thirty days",
            snippet="This extensive report covers all aspects of the team performance including advanced metrics and historical comparisons going back several decades.",
            top_comments=[{"body": "bro what"}],
        )
        _apply_single_fun_fallback(short_candidate)
        _apply_single_fun_fallback(long_candidate)
        # Both get marker bonus from "bro", but short one gets higher shortness bonus
        assert short_candidate.fun_score > long_candidate.fun_score

    def test_no_comments_falls_back_to_title_snippet(self):
        """A candidate with no source_items/comments still scores based on title+snippet."""
        candidate = _make_candidate(
            title="This is hilarious content",
            snippet="Very funny stuff",
            top_comments=None,  # no source_items at all
        )
        _apply_single_fun_fallback(candidate)
        assert candidate.fun_score is not None
        assert candidate.fun_score >= 10.0  # marker bonus from "hilarious"
        assert candidate.fun_explanation == "heuristic-fallback"

    def test_empty_comment_bodies_no_crash(self):
        """Candidates with empty comment bodies should not crash."""
        candidate = _make_candidate(
            title="Normal title",
            snippet="Normal snippet",
            top_comments=[
                {"body": ""},
                {"body": None},
                {},
                {"body": "actual comment"},
            ],
        )
        _apply_single_fun_fallback(candidate)
        assert candidate.fun_score is not None
        assert candidate.fun_explanation == "heuristic-fallback"


class TestScoreCommentsPerCandidate:
    """Per-comment fun scoring: high-ratio viral comments outrank absolute-high comments on dominant threads."""

    def test_high_ratio_comment_outranks_low_ratio(self):
        """A 2304-upvote comment on a 300-upvote parent should score higher than
        a 400-upvote comment on a 3400-upvote parent (high ratio = viral wit)."""
        viral = _make_candidate(
            parent_raw_engagement=300,
            top_comments=[{"body": "WHAT?! I reached my monthly limit just reading this post", "score": 2304}],
        )
        average = _make_candidate(
            parent_raw_engagement=3400,
            top_comments=[{"body": "we can't trust benchmarks anymore and need to re-run them", "score": 400}],
        )
        _score_comments_per_candidate(viral)
        _score_comments_per_candidate(average)
        viral_score = viral.source_items[0].metadata["top_comments"][0]["fun_score"]
        average_score = average.source_items[0].metadata["top_comments"][0]["fun_score"]
        assert viral_score > average_score

    def test_2304_upvote_comment_on_small_parent_crosses_medium_threshold(self):
        """The exact Opus 4.7 case: the comment should score >= 55 (medium threshold)."""
        candidate = _make_candidate(
            parent_raw_engagement=300,
            top_comments=[{
                "body": "WHAT?! I reached my monthly limit just reading this post",
                "score": 2304,
            }],
        )
        _score_comments_per_candidate(candidate)
        comment = candidate.source_items[0].metadata["top_comments"][0]
        assert comment["fun_score"] >= 55.0

    def test_reddit_excerpt_field_also_works(self):
        """Reddit uses 'excerpt' not 'body'. The scorer must handle that."""
        candidate = _make_candidate(
            parent_raw_engagement=100,
            top_comments=[{"excerpt": "bruh this is gold", "score": 500}],
        )
        _score_comments_per_candidate(candidate)
        comment = candidate.source_items[0].metadata["top_comments"][0]
        assert "fun_score" in comment
        assert comment["fun_score"] > 0

    def test_no_parent_upvotes_does_not_crash(self):
        """A candidate without parent engagement still scores comments via the absolute-upvote fallback."""
        candidate = _make_candidate(
            parent_raw_engagement=None,
            top_comments=[{"body": "lmao", "score": 200}],
        )
        _score_comments_per_candidate(candidate)
        comment = candidate.source_items[0].metadata["top_comments"][0]
        assert "fun_score" in comment

    def test_malformed_comments_skipped(self):
        """Non-dict entries and missing-body entries are skipped without raising."""
        candidate = _make_candidate(
            parent_raw_engagement=500,
            top_comments=[
                "not a dict",  # malformed, still counts toward the top-3 window
                {"body": "", "score": 10},  # empty body within window
                {"body": "valid", "score": 50},  # valid within window
            ],
        )
        _score_comments_per_candidate(candidate)
        comments = candidate.source_items[0].metadata["top_comments"]
        # Only the valid one gets a fun_score
        valid = [c for c in comments if isinstance(c, dict) and c.get("fun_score") is not None]
        assert len(valid) == 1
        assert valid[0]["body"] == "valid"

    def test_score_comments_runs_after_fallback(self):
        """_apply_fun_fallback wires in comment scoring automatically."""
        candidate = _make_candidate(
            parent_raw_engagement=300,
            top_comments=[{"body": "bro what 😭", "score": 1500}],
        )
        _apply_fun_fallback([candidate])
        comment = candidate.source_items[0].metadata["top_comments"][0]
        assert "fun_score" in comment
        assert candidate.fun_score is not None


class TestExtractCommentText:
    """Verify _extract_comment_text handles edge cases."""

    def test_extracts_from_top_comments(self):
        candidate = _make_candidate(
            top_comments=[{"body": "first comment"}, {"body": "second comment"}],
        )
        text = _extract_comment_text(candidate)
        assert "first comment" in text
        assert "second comment" in text

    def test_empty_source_items(self):
        candidate = _make_candidate(top_comments=None)
        text = _extract_comment_text(candidate)
        assert text == ""
