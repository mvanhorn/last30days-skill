"""Tests for the fun judge heuristic fallback in rerank.py."""

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib import schema
from lib.rerank import _apply_single_fun_fallback, _extract_comment_text


def _make_candidate(
    title: str = "Some Title",
    snippet: str = "",
    engagement: float | None = 0.0,
    top_comments: list[dict] | None = None,
) -> schema.Candidate:
    """Build a minimal Candidate with optional source_items carrying top_comments."""
    source_items = []
    if top_comments is not None:
        source_items.append(
            schema.SourceItem(
                item_id="si-1",
                source="reddit",
                title=title,
                body="",
                url="https://reddit.com/r/test/1",
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
