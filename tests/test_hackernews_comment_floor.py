"""Hacker News comments must survive the per-source top-comment floor.

HN comments arrive as ``{author, text, points}`` while every downstream reader
keys on ``score``/``excerpt``. Before the fix, ``_normalize_hackernews`` stored
them raw, so ``render._top_comments_list`` evaluated ``(c.get("score") or 0) >= 5``
against a key that was never present and rejected the entire source.
"""

from unittest.mock import patch

from lib import hackernews, normalize, render, schema

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


def _report(item, candidate):
    return schema.Report(
        topic="HN comments",
        range_from=FROM_DATE,
        range_to=TO_DATE,
        generated_at=f"{TO_DATE}T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="local",
            rerank_model="local",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic="HN comments",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="HN comments",
                    ranking_query="What are the top HN comments?",
                    sources=["hackernews"],
                )
            ],
            source_weights={"hackernews": 1.0},
        ),
        clusters=[],
        ranked_candidates=[candidate],
        items_by_source={"hackernews": [item]},
        errors_by_source={},
    )


def _render_vote_paths(points):
    raw = _hn_item()
    with patch("lib.hackernews.http.request") as request:
        request.return_value = {
            "children": [
                {
                    "author": "alice",
                    "text": raw["top_comments"][0]["text"],
                    "points": 93,
                },
                {
                    "author": "bob",
                    "text": raw["top_comments"][1]["text"],
                    "points": points,
                },
            ]
        }
        raw["top_comments"] = hackernews._fetch_item_comments("42")["comments"]
    hn = normalize._normalize_hackernews(
        "hackernews", raw, 0, FROM_DATE, TO_DATE
    )
    candidate = _candidate("hackernews", hn)
    report = _report(hn, candidate)
    return hn.metadata["top_comments"][1]["score"], {
        "full": render.render_full(report),
        "candidate": "\n".join(render._render_candidate(candidate, prefix="1.")),
        "top_comments": "\n".join(render._render_top_comments(report)),
    }


def test_absent_hn_vote_is_omitted_across_comment_renderers():
    """A missing vote must not display a fabricated numeric measurement."""
    normalized_score, rendered_paths = _render_vote_paths(None)
    assert normalized_score is None
    for path, rendered in rendered_paths.items():
        assert "(0 points)" not in rendered, f"{path}:\n{rendered}"
        assert "(93 points)" in rendered, f"{path} dropped a real vote count"


def test_explicit_zero_hn_vote_is_rendered_across_comment_renderers():
    """A measured zero remains visible in every zero-admitting comment path."""
    normalized_score, rendered_paths = _render_vote_paths(0)
    assert normalized_score == 0
    for path, rendered in rendered_paths.items():
        assert "(0 points)" in rendered, f"{path} hid an explicit numeric zero"


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
        normalize._normalize_hackernews(
            "hackernews", first_raw, 0, FROM_DATE, TO_DATE
        ),
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


def test_best_takes_uses_hn_comment_longer_than_story_title():
    """HN comment excerpts remain the take even when longer than the title."""
    first_raw = _hn_item()
    first_raw["title"] = "Short story"
    first_raw["top_comments"] = [
        {
            "author": "alice",
            "text": "This longer Hacker News comment is the actual sharp take.",
            "points": None,
        }
    ]
    second_raw = _hn_item()
    second_raw["id"] = "43"
    second_raw["top_comments"] = [
        {"author": "bob", "text": "Another good take.", "points": None}
    ]
    first = _candidate(
        "hackernews",
        normalize._normalize_hackernews(
            "hackernews", first_raw, 0, FROM_DATE, TO_DATE
        ),
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
    assert "This longer Hacker News comment is the actual sharp take." in rendered
    assert '"Short story"' not in rendered


def test_best_takes_uses_hn_excerpt_retained_by_fused_candidate():
    """A non-HN representative must not hide its retained HN comment."""
    reddit = normalize._normalize_reddit(
        "reddit", _reddit_item(), 0, FROM_DATE, TO_DATE
    )
    hn_raw = _hn_item()
    hn_raw["top_comments"] = [
        {"author": "alice", "text": "The retained HN take.", "points": 93}
    ]
    hn = normalize._normalize_hackernews(
        "hackernews", hn_raw, 0, FROM_DATE, TO_DATE
    )
    fused = _candidate("reddit", reddit)
    fused.source_items = [reddit, hn]
    assert fused.source != "hackernews"
    control_raw = _reddit_item()
    control_raw["id"] = "r2"
    control_raw["title"] = "a separate reddit thread"
    control = _candidate(
        "reddit",
        normalize._normalize_reddit(
            "reddit", control_raw, 1, FROM_DATE, TO_DATE
        ),
    )
    fused.fun_score = 80.0
    control.fun_score = 80.0

    with patch(
        "lib.render.signals.normalized_comment_vote",
        wraps=render.signals.normalized_comment_vote,
    ) as normalized_vote:
        top_comments = "\n".join(
            render._render_top_comments(
                _report(reddit, fused),
                candidates=[fused, control],
            )
        )
    rendered = "\n".join(
        render._render_best_takes([fused, control], threshold=70.0, vote_weight=0.0)
    )
    assert "The retained HN take." in rendered
    fused_line = next(
        line for line in rendered.splitlines() if "The retained HN take." in line
    )
    assert "-- Hacker News " in fused_line
    assert "Reddit" not in fused_line
    assert "r/" not in fused_line
    retained_line = next(
        line for line in top_comments.splitlines() if "The retained HN take." in line
    )
    assert "— alice (93 points)" in retained_line
    assert "upvotes" not in retained_line
    assert "u/alice" not in retained_line
    assert any(
        call.args == ("hackernews", 93) for call in normalized_vote.call_args_list
    )
