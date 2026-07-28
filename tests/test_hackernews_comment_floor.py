"""Hacker News comments must survive the per-source top-comment floor.

HN comments arrive as ``{author, text, points}`` while every downstream reader
keys on ``score``/``excerpt``. Before the fix, ``_normalize_hackernews`` stored
them raw, so ``render._top_comments_list`` evaluated ``(c.get("score") or 0) >= 5``
against a key that was never present and rejected the entire source.
"""

from types import SimpleNamespace

from lib import normalize, render, schema

FROM_DATE = "2026-06-26"
TO_DATE = "2026-07-26"


def _hn_item():
    return {
        "id": "42",
        "title": "Show HN: a thing",
        "url": "https://example.test/thing",
        "hn_url": "https://news.ycombinator.com/item?id=42",
        "author": "pg",
        "date": "2026-07-20",
        "engagement": {"points": 420, "comments": 2},
        "top_comments": [
            {
                "author": "alice",
                "text": "The single clearest explanation I have read.",
                "points": 93,
            },
            {
                "author": "bob",
                "text": "Counterpoint: the benchmark skips the hard cases.",
                "points": None,
            },
        ],
    }


def _reddit_item():
    return {
        "id": "r1",
        "title": "a reddit thread",
        "url": "https://reddit.test/r1",
        "author": "carol",
        "date": "2026-07-20",
        "engagement": {"upvotes": 300, "comments": 2},
        "top_comments": [
            {
                "author": "alice",
                "excerpt": "The single clearest explanation I have read.",
                "score": 93,
            },
            {
                "author": "bob",
                "excerpt": "Counterpoint: the benchmark skips the hard cases.",
                "score": 51,
            },
        ],
    }


def test_hn_comments_are_remapped_to_the_shared_shape():
    item = normalize._normalize_hackernews(
        "hackernews", _hn_item(), 0, FROM_DATE, TO_DATE
    )
    comments = item.metadata["top_comments"]
    assert comments, "HN comments should survive normalisation"
    for comment in comments:
        assert "score" in comment, (
            f"expected the shared score key, got {sorted(comment)}"
        )
        assert "excerpt" in comment, (
            f"expected the shared excerpt key, got {sorted(comment)}"
        )
    assert comments[0]["score"] == 93, "points must carry through as score"


def test_hn_comments_clear_the_per_source_floor():
    item = normalize._normalize_hackernews(
        "hackernews", _hn_item(), 0, FROM_DATE, TO_DATE
    )
    assert render._top_comments_list(item), (
        "HN comments must not be filtered out by a floor keyed on a field the "
        "source never populated"
    )


def test_hn_floor_is_zero_because_hn_has_no_per_comment_points():
    """The Algolia items endpoint returns points=null for every comment child.

    Any positive threshold therefore rejects the whole source rather than
    filtering it, so this constant is load-bearing rather than a tuning knob.
    """
    assert render._TOP_COMMENT_MIN_SCORE["hackernews"] == 0


def test_reddit_control_is_unchanged():
    item = normalize._normalize_reddit("reddit", _reddit_item(), 0, FROM_DATE, TO_DATE)
    assert len(render._top_comments_list(item)) == 2


def _candidate(source, item):
    return schema.Candidate(
        candidate_id="c1",
        item_id=item.item_id,
        source=source,
        title=item.title,
        url=item.url,
        snippet="",
        subquery_labels=[],
        native_ranks={},
        local_relevance=0.9,
        freshness=1,
        engagement=100,
        source_quality=0.9,
        rrf_score=1.0,
        source_items=[item],
        final_score=50.0,
        explanation="llm-rerank",
    )


def test_absent_vote_signal_renders_no_zero_points_parenthetical():
    """A scoreless comment must not display a fabricated "(0 points)"."""
    hn = normalize._normalize_hackernews(
        "hackernews", _hn_item(), 0, FROM_DATE, TO_DATE
    )
    # _render_top_comments reads only ranked_candidates, so a stand-in avoids
    # constructing a full Report with its unrelated required fields.
    report = SimpleNamespace(ranked_candidates=[_candidate("hackernews", hn)])
    lines = render._render_top_comments(report)
    rendered = "\n".join(lines)
    assert "0 points" not in rendered, rendered
    assert "93 points" in rendered, "a real vote count must still be shown"


def test_ranked_candidate_omits_absent_vote_signal():
    """Ranked Evidence Clusters must not turn a missing HN score into zero."""
    hn = normalize._normalize_hackernews(
        "hackernews", _hn_item(), 0, FROM_DATE, TO_DATE
    )
    rendered = "\n".join(
        render._render_candidate(_candidate("hackernews", hn), prefix="1.")
    )
    assert "0 points" not in rendered, rendered
    assert "93 points" in rendered, "a real vote count must still be shown"


def test_best_takes_uses_normalized_hn_comment_excerpt():
    """Best Takes should display HN comment text, not only the story title."""
    first_raw = _hn_item()
    first_raw["title"] = "Show HN: a deliberately long story title for testing"
    first_raw["top_comments"] = [
        {"author": "alice", "text": "A sharp HN take.", "points": None}
    ]
    second_raw = _hn_item()
    second_raw["id"] = "43"
    second_raw["title"] = "Ask HN: another deliberately long story title for testing"
    second_raw["top_comments"] = [
        {"author": "bob", "text": "Another good take.", "points": None}
    ]
    first = _candidate(
        "hackernews",
        normalize._normalize_hackernews("hackernews", first_raw, 0, FROM_DATE, TO_DATE),
    )
    second = _candidate(
        "hackernews",
        normalize._normalize_hackernews(
            "hackernews", second_raw, 1, FROM_DATE, TO_DATE
        ),
    )
    first.fun_score = 80.0
    second.fun_score = 80.0

    rendered = "\n".join(
        render._render_best_takes([first, second], threshold=70.0, vote_weight=0.0)
    )
    assert "A sharp HN take." in rendered
    assert "Another good take." in rendered
