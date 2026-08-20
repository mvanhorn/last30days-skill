"""U6 - rich discovery rendering: enriched trend cards, the global-trending
header, and the honest nothing-solid empty state."""

from unittest import mock

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


# --- U5 angle and pipeline card lines ----------------------------------------


def test_angle_lines_render_in_order_between_voice_and_evidence():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK",
        top_comment='"This changes agent tooling" - dev_voice (1,200 votes)',
        podcast_angle="Is the Agent SDK a platform play or a lock-in play?",
        x_article_angle="Five signs the agent stack just consolidated around one SDK.",
    )])
    rendered = render.render_discovery(report)
    assert (
        "**Podcast angle:** Is the Agent SDK a platform play or a lock-in play?"
        in rendered
    )
    assert (
        "**X article angle:** Five signs the agent stack just consolidated around one SDK."
        in rendered
    )
    voice = rendered.index("**Community voice:**")
    podcast = rendered.index("**Podcast angle:**")
    article = rendered.index("**X article angle:**")
    evidence = rendered.index("**Evidence:**")
    assert voice < podcast < article < evidence


def test_host_authored_angles_render_verbatim_and_capped(tmp_path):
    """Finalize-leg path: host angle sentences pass through the handoff
    reader (word-boundary capped at 200 chars) and render verbatim on the
    card - relayable text, never paraphrased or re-wrapped."""
    import dataclasses
    import json

    from lib import discovery_handoff

    pending = discovery_handoff.PendingReport(
        schema_version="1.0",
        bundle_id="cafe1234cafe1234",
        generated_at="2026-07-10T00:00:00+00:00",
        run_ref="discover:AI agents:2026-07-10T00:00:00+00:00",
        report={},
        angle_inputs={"n1": {"name": "OpenAI Agent SDK"}},
    )
    long_angle = " ".join(["angle"] * 60)  # well over the 200-char cap
    angles_path = tmp_path / "angles.json"
    angles_path.write_text(json.dumps({
        "bundle_id": pending.bundle_id,
        "angles": [{
            "id": "n1",
            "podcast": "Is the Agent SDK a platform play or a lock-in play?",
            "x_article": long_angle,
        }],
    }), encoding="utf-8")
    host = discovery_handoff.read_angles(angles_path, pending)["n1"]

    topic = dataclasses.replace(
        _topic(1, "OpenAI Agent SDK"),
        podcast_angle=host.podcast,
        x_article_angle=host.x_article,
    )
    rendered = render.render_discovery(_report(topics=[topic]))
    assert (
        "**Podcast angle:** Is the Agent SDK a platform play or a lock-in play?"
        in rendered
    )
    capped = host.x_article
    assert capped is not None
    assert len(capped) <= 200
    assert f"**X article angle:** {capped}" in rendered


def test_no_angle_lines_when_fields_none():
    rendered = render.render_discovery(_report())
    assert "**Podcast angle:**" not in rendered
    assert "**X article angle:**" not in rendered


def test_single_angle_renders_without_empty_sibling_label():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK",
        podcast_angle="What breaks first when every agent shares one SDK?",
    )])
    rendered = render.render_discovery(report)
    assert (
        "**Podcast angle:** What breaks first when every agent shares one SDK?"
        in rendered
    )
    assert "**X article angle:**" not in rendered


def test_pipeline_line_renders_surfaced_and_covered_comma_joined():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK",
        previously_surfaced_count=3,
        last_surfaced="2026-07-14",
        covered=True,
    )])
    rendered = render.render_discovery(report)
    assert (
        "**Pipeline:** surfaced 4th time, marked covered"
        in rendered
    )
    assert "marked covered 2026" not in rendered  # covered date is never shown


def test_pipeline_line_surfaced_only():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK", previously_surfaced_count=1,
    )])
    rendered = render.render_discovery(report)
    assert "**Pipeline:** surfaced 2nd time" in rendered
    assert "marked covered" not in rendered


def test_pipeline_line_covered_only_without_date():
    report = _report(topics=[_topic(1, "OpenAI Agent SDK", covered=True)])
    rendered = render.render_discovery(report)
    assert "**Pipeline:** marked covered" in rendered
    assert "surfaced" not in rendered


def test_pipeline_line_sits_after_angles_before_evidence():
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK",
        podcast_angle="A tension to talk through?",
        x_article_angle="A claim worth writing down.",
        previously_surfaced_count=2,
    )])
    rendered = render.render_discovery(report)
    assert (
        rendered.index("**X article angle:**")
        < rendered.index("**Pipeline:**")
        < rendered.index("**Evidence:**")
    )


def test_no_pipeline_line_for_fresh_topic():
    """previously_surfaced_count=0 and covered=False never render the line."""
    rendered = render.render_discovery(_report())
    assert "**Pipeline:**" not in rendered


# --- _ordinal (Pipeline card line regression guard) ------------------------


def test_ordinal_teens_branch_always_th():
    """10-20 (mod 100) are the '11st'-style regression risk: %10 alone would
    misclassify 11/12/13 as 'st'/'nd'/'rd'. All of 10-13 and the 20 boundary
    must render 'th'."""
    cases = {10: "10th", 11: "11th", 12: "12th", 13: "13th", 20: "20th"}
    for count, expected in cases.items():
        assert render._ordinal(count) == expected


def test_ordinal_regular_suffixes():
    assert render._ordinal(2) == "2nd"
    assert render._ordinal(3) == "3rd"
    assert render._ordinal(21) == "21st"


def test_ordinal_hundreds_teens_still_th():
    """111 falls in the 10 <= n % 100 <= 20 band (111 % 100 == 11), so it must
    render 'th', not 'st' from a naive %10 check."""
    assert render._ordinal(111) == "111th"


def test_pipeline_line_ordinal_teens_in_rendered_card():
    """End-to-end: previously_surfaced_count=10 means this is appearance 11,
    which must render 'surfaced 11th time', not 'surfaced 11st time'."""
    report = _report(topics=[_topic(
        1, "OpenAI Agent SDK", previously_surfaced_count=10,
    )])
    rendered = render.render_discovery(report)
    assert "surfaced 11th time" in rendered


def test_nothing_solid_output_stays_byte_identical():
    """The empty state predates U5 and must not grow angle or pipeline text."""
    report = _report(
        topics=[],
        outcome="nothing-solid",
        weak_signal="Wii Sports nostalgia thread",
        warnings=[
            "No topic cleared the discovery confidence floor this window; "
            "reporting nothing solid instead of ranked noise."
        ],
    )
    with mock.patch.object(render, "_render_badge", return_value=["BADGE", ""]):
        rendered = render.render_discovery(report)
    assert rendered == (
        "BADGE\n\n"
        "# Trending discovery: AI agents\n\n"
        "Window: 2026-06-10 to 2026-07-10\n"
        "Feeds: reddit, hackernews\n"
        "Communities: r/all\n\n"
        "**Nothing solid this window.** No topic cleared the confidence "
        "floor - not enough cross-source confirmation or engagement to "
        "call anything a trend, and ranked noise would be worse than an "
        "honest empty result.\n\n"
        "Closest weak signal: Wii Sports nostalgia thread (sub-floor; "
        "single-source or too little engagement).\n\n"
        "### Coverage notes\n\n"
        "- No topic cleared the discovery confidence floor this window; "
        "reporting nothing solid instead of ranked noise.\n"
    )
