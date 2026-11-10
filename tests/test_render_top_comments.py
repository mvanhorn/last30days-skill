"""Top Community Comments lines carry what a pulled quote's attribution needs.

Each line names the platform with the footer's dot, the author, the subreddit
for Reddit, and the count, so the writer copies them without looking anything
up elsewhere in the evidence.
"""

from lib import fusion, render, schema


def _item(source, url, comments, container=None):
    return schema.SourceItem(
        item_id=f"i-{url}",
        source=source,
        title="Post",
        body="",
        url=url,
        container=container,
        metadata={"top_comments": comments} if comments else {},
    )


def _candidate(item):
    candidate = schema.Candidate(
        candidate_id=f"c-{item.url}",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet="",
        subquery_labels=["q1"],
        native_ranks={item.source: 1},
        local_relevance=0.8,
        freshness=50,
        engagement=0,
        source_quality=0.5,
        rrf_score=0.01,
        source_items=[item],
    )
    candidate.final_score = 50.0
    return candidate


def _report(*items):
    return schema.Report(
        topic="Kanye West",
        range_from="2026-10-11",
        range_to="2026-11-10",
        generated_at="2026-11-10T00:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="test", planner_model="test", rerank_model="test"
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="debate",
            raw_topic="Kanye West",
            subqueries=[],
            source_weights={},
        ),
        ranked_candidates=[_candidate(item) for item in items],
        clusters=[],
        items_by_source={},
        errors_by_source={},
    )


def _line(lines, needle):
    (match,) = [line for line in lines if needle in line]
    return match


TIKTOK = _item(
    "tiktok",
    "https://www.tiktok.com/@north/video/1",
    [{"author": "@haktimel2", "score": 825451, "excerpt": "Ofc shes literally him😭"}],
)
REDDIT = _item(
    "reddit",
    "https://www.reddit.com/r/hiphopheads/comments/abc/thread/",
    [{"author": "CappaccinoJay", "score": 1315, "excerpt": "Internet feelings vs reality."}],
    container="hiphopheads",
)
def test_reddit_line_carries_dot_author_subreddit_and_upvotes():
    lines = render._render_top_comments(_report(REDDIT, TIKTOK))
    line = _line(lines, "Internet feelings")
    assert line == (
        '- "Internet feelings vs reality." — 🟠 u/CappaccinoJay on r/hiphopheads'
        " (1,315 upvotes) — https://www.reddit.com/r/hiphopheads/comments/abc/thread/"
    )


def test_tiktok_line_carries_dot_handle_and_likes_without_a_community():
    lines = render._render_top_comments(_report(REDDIT, TIKTOK))
    line = _line(lines, "literally him")
    assert line == (
        '- "Ofc shes literally him😭" — 🎵 @haktimel2 (825,451 likes)'
        " — https://www.tiktok.com/@north/video/1"
    )


def test_evidence_lines_hold_every_part_of_a_pulled_quote_attribution():
    lines = render._render_top_comments(_report(TIKTOK, REDDIT))
    for needle, parts in (
        ("literally him", ('"Ofc shes literally him😭"', "🎵", "@haktimel2", "825,451 likes")),
        (
            "Internet feelings",
            ('"Internet feelings vs reality."', "🟠", "u/CappaccinoJay", "on r/hiphopheads", "1,315 upvotes"),
        ),
    ):
        line = _line(lines, needle)
        for part in parts:
            assert part in line, f"{part!r} missing from {line!r}"


def test_subreddit_already_prefixed_is_not_doubled():
    prefixed = _item("reddit", REDDIT.url, REDDIT.metadata["top_comments"], container="r/hiphopheads")
    line = _line(render._render_top_comments(_report(prefixed, TIKTOK)), "Internet feelings")
    assert "on r/hiphopheads (" in line
    assert "r/r/" not in line


def test_reddit_line_without_a_subreddit_names_no_community():
    bare = _item("reddit", REDDIT.url, REDDIT.metadata["top_comments"])
    line = _line(render._render_top_comments(_report(bare, TIKTOK)), "Internet feelings")
    assert "— 🟠 u/CappaccinoJay (1,315 upvotes)" in line
    assert " on r/" not in line


def test_deleted_or_missing_author_keeps_the_comment_fallback():
    item = _item(
        "reddit",
        REDDIT.url,
        [
            {"author": "[deleted]", "score": 900, "excerpt": "A deleted account wrote this line."},
            {"score": 400, "excerpt": "Nobody is recorded as writing this one."},
        ],
        container="hiphopheads",
    )
    lines = render._render_top_comments(_report(item))
    assert "— 🟠 Comment on r/hiphopheads (900 upvotes)" in _line(lines, "deleted account")
    assert "— 🟠 Comment on r/hiphopheads (400 upvotes)" in _line(lines, "Nobody is recorded")
    assert "u/" not in "\n".join(lines)


def test_source_without_a_footer_dot_gets_no_marker():
    item = _item(
        "grounding",
        "https://example.com/post",
        [{"author": "ada", "score": 12, "excerpt": "A reader comment on a web page."}],
    )
    line = _line(render._render_top_comments(_report(item, TIKTOK)), "reader comment")
    assert '" — ada (12 votes) — https://example.com/post' in line


def test_dots_come_from_the_footer_table():
    footer = {key: emoji for key, emoji, *_ in render._FOOTER_SOURCES}
    for source in ("reddit", "x", "youtube", "tiktok", "instagram", "threads", "hackernews", "bluesky"):
        item = _item(
            source,
            f"https://example.com/{source}",
            [{"author": "ada", "score": 5, "excerpt": f"A comment found on {source} today."}],
        )
        line = _line(render._render_top_comments(_report(item, TIKTOK)), f"found on {source} ")
        assert f"— {footer[source]} " in line, line


def test_author_score_and_subreddit_survive_a_duplicate_merge():
    """The copy that won the comment slot arrives second; nothing is lost."""
    first = _item("reddit", REDDIT.url, None, container="hiphopheads")
    second = _item(
        "reddit", REDDIT.url, [dict(c) for c in REDDIT.metadata["top_comments"]], container="hiphopheads"
    )
    (kept,) = fusion.collapse_duplicate_urls([first, second])
    assert kept is first
    assert kept.metadata["top_comments"] == [
        {"author": "CappaccinoJay", "score": 1315, "excerpt": "Internet feelings vs reality."}
    ]
    line = _line(render._render_top_comments(_report(kept, TIKTOK)), "Internet feelings")
    assert "🟠 u/CappaccinoJay on r/hiphopheads (1,315 upvotes)" in line


def test_merge_keeps_the_fuller_comment_list_with_its_authors():
    first = _item(
        "tiktok", TIKTOK.url, [{"author": "@early", "score": 10, "excerpt": "An early comment on the video."}]
    )
    second = _item(
        "tiktok",
        TIKTOK.url,
        [
            {"author": "@haktimel2", "score": 825451, "excerpt": "Ofc shes literally him😭"},
            {"author": "@early", "score": 10, "excerpt": "An early comment on the video."},
        ],
    )
    merged = fusion.merge_source_items(first, second)
    assert [(c["author"], c["score"]) for c in merged.metadata["top_comments"]] == [
        ("@haktimel2", 825451),
        ("@early", 10),
    ]
