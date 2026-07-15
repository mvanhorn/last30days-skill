"""U6 - rich discovery rendering: enriched trend cards, the global-trending
header, and the honest nothing-solid empty state."""

from lib import pipeline, render, schema


def _topic(rank: int, name: str, **overrides) -> schema.DiscoveryTopic:
    fields = dict(
        rank=rank,
        name=name,
        why_spiking=f"3 evidence items about {name}.",
        momentum="building",
        velocity_score=42.5,
        sources=["hackernews", "reddit"],
        engagement_by_source={"hackernews": {"points": 500}},
        command=f'/last30days "{name}"',
        evidence_urls=[f"https://example.com/{rank}"],
    )
    fields.update(overrides)
    return schema.DiscoveryTopic(**fields)


def _report(**overrides) -> schema.DiscoveryReport:
    fields = dict(
        domain="AI agents",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        plan=schema.DiscoveryPlan(
            domain="AI agents", category=None, subreddits=["all"],
            sources=["reddit", "hackernews"],
        ),
        topics=[_topic(1, "OpenAI Agent SDK")],
        source_status={},
        warnings=[],
    )
    fields.update(overrides)
    return schema.DiscoveryReport(**fields)


def test_global_trending_header():
    report = _report(
        domain="",
        plan=schema.DiscoveryPlan(
            domain="", category=None, subreddits=["all"],
            sources=["reddit", "hackernews", "digg"],
        ),
    )
    rendered = render.render_discovery(report)
    assert "# Trending now" in rendered
    assert "Trending discovery:" not in rendered


def test_domain_header_unchanged():
    rendered = render.render_discovery(_report())
    assert "# Trending discovery: AI agents" in rendered


def test_nothing_solid_renders_honest_empty_state():
    report = _report(
        topics=[],
        outcome="nothing-solid",
        weak_signal="Wii Sports nostalgia thread",
        warnings=["No topic cleared the discovery confidence floor this window."],
    )
    rendered = render.render_discovery(report)
    assert "Nothing solid this window." in rendered
    assert "Wii Sports nostalgia thread" in rendered
    assert "## 1." not in rendered  # no fabricated topic cards


def test_community_voice_and_corroboration_render():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK",
        top_comment='"This changes everything about agent tooling" - dev_voice (1,200 votes)',
        corroboration_count=3,
    )])
    rendered = render.render_discovery(report)
    assert "**Community voice:**" in rendered
    assert "dev_voice" in rendered
    assert "confirmed across 3 sources" in rendered


def test_no_voice_line_when_topic_has_no_comment():
    rendered = render.render_discovery(_report())
    assert "**Community voice:**" not in rendered


def test_best_community_comment_prefers_platform_normalized_strength():
    items = [
        schema.SourceItem(
            item_id="a", source="reddit", title="t", body="t",
            url="https://reddit.com/a", metadata={"top_comments": [
                {"text": "the sharpest take in the thread by far", "score": 4000, "author": "u/sharp"},
                {"text": "short", "score": 9999},
            ]},
        ),
        schema.SourceItem(
            item_id="b", source="hackernews", title="t", body="t",
            url="https://news.ycombinator.com/b", metadata={"top_comments": [
                {"text": "a modest but thoughtful comment here", "score": 3, "author": "hn_user"},
            ]},
        ),
    ]
    comment = pipeline._best_community_comment(items)
    assert comment is not None
    assert "u/sharp" in comment
    assert "4,000 votes" in comment
    # Sub-12-char comment bodies never surface.
    assert "short" not in comment


def test_best_community_comment_strips_leading_quote_chars():
    """A comment body that itself starts with a quote must not render as
    doubled quotes inside the wrapping quotes."""
    items = [schema.SourceItem(
        item_id="a", source="reddit", title="t", body="t",
        url="https://r.example/a", metadata={"top_comments": [
            {"text": '"This much is clear: the quote should not double up', "score": 100, "author": "u/q"},
        ]},
    )]
    comment = pipeline._best_community_comment(items)
    assert comment is not None
    assert '""' not in comment
    assert comment.startswith('"This much is clear')


def test_best_community_comment_none_when_no_comments():
    items = [schema.SourceItem(
        item_id="a", source="reddit", title="t", body="t", url="https://r.example/a",
    )]
    assert pipeline._best_community_comment(items) is None
