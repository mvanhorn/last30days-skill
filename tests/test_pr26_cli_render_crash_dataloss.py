"""Regression tests for PR 26 CLI and renderer crash/data-loss fixes.

Covers:
- HIGH-005: _finalization_deadline in --discover --judgments and --deep-research
- HIGH-011: comparison runs persisting all entities' findings to research.db
- HIGH-040: normalized TikTok/Instagram/Threads handles in Top voices + Truth Social prefix
- HIGH-044: bot author filter stripping leading @ / u/ / /u/ prefixes
- HIGH-045: malformed/None comment, insight, highlight, and outcome_prices metadata;
            Best Takes excerpt support and Reddit r/sub attribution without author
- HIGH-048: unguarded dict/type crashes in engagement, top comments, market windows,
            freshness verdicts, and resolved entities rendering
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import last30days as cli
from lib import discovery_handoff, env, normalize, pipeline, render, schema


def _make_item(
    item_id: str,
    source: str,
    title: str = "Test title about Acme",
    *,
    body: str = "Body text about Acme",
    url: str | None = None,
    author: str | None = None,
    container: str | None = None,
    engagement: dict | None = None,
    metadata: dict | None = None,
    published_at: str = "2026-10-20",
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=body,
        url=url or f"https://example.test/{source}/{item_id}",
        author=author,
        container=container,
        published_at=published_at,
        date_confidence="high",
        engagement={} if engagement is None else engagement,
        snippet=body,
        metadata={} if metadata is None else metadata,
    )


def _make_candidate(
    candidate_id: str,
    item: schema.SourceItem,
    *,
    fun_score: float | None = None,
    fun_explanation: str | None = None,
    local_relevance: float = 0.85,
    final_score: float = 80.0,
    cluster_id: str | None = None,
) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=candidate_id,
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet or item.body,
        subquery_labels=["primary"],
        native_ranks={"primary": 1},
        local_relevance=local_relevance,
        freshness=85.0,
        engagement=50.0,
        source_quality=0.8,
        rrf_score=0.05,
        final_score=final_score,
        sources=[item.source],
        source_items=[item],
        fun_score=fun_score,
        fun_explanation=fun_explanation,
        cluster_id=cluster_id,
    )


def _make_report(
    topic: str,
    *,
    items_by_source: dict[str, list[schema.SourceItem]] | None = None,
    candidates: list[schema.Candidate] | None = None,
    clusters: list[schema.Cluster] | None = None,
    artifacts: dict | None = None,
    freshness_verdicts: list[schema.FreshnessVerdict] | None = None,
) -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-10-01",
        range_to="2026-10-31",
        generated_at="2026-10-31T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock",
            rerank_model="mock",
        ),
        query_plan=schema.QueryPlan(
            intent="breaking_news",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[],
            source_weights={},
        ),
        clusters=clusters or [],
        ranked_candidates=candidates or [],
        items_by_source=items_by_source or {},
        errors_by_source={},
        artifacts=artifacts or {},
        freshness_verdicts=freshness_verdicts or [],
    )


# ---------------------------------------------------------------------------
# HIGH-005: _finalization_deadline in --discover --judgments & --deep-research
# ---------------------------------------------------------------------------


def test_high005_discover_judgments_omits_280s_finalization_deadline(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    def fake_run_discover_resume(bundle, judgments, *, config, mock=False):
        captured["config"] = dict(config)
        return SimpleNamespace(
            report=schema.DiscoveryReport(
                domain="ai",
                range_from="2026-10-01",
                range_to="2026-10-31",
                generated_at="2026-10-31T12:00:00+00:00",
                plan=schema.DiscoveryPlan(domain="ai", category=None, subreddits=[], sources=[]),
                topics=[],
                source_status={},
                warnings=[],
                outcome="nothing-solid",
            ),
            angle_inputs={},
        )

    bundle = SimpleNamespace(mock=True, path=tmp_path / "bundle.json", bundle_id="b1")
    monkeypatch.setattr(env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(discovery_handoff, "read_nominations_bundle", lambda **kwargs: bundle)
    monkeypatch.setattr(discovery_handoff, "read_judgments", lambda *args, **kwargs: {})
    monkeypatch.setattr(pipeline, "run_discover_resume", fake_run_discover_resume)
    monkeypatch.setattr(render, "render_discovery", lambda report: "nothing solid\n")

    parser = cli.build_parser()
    args, extra = parser.parse_known_args(
        ["--discover", "--judgments", str(tmp_path / "j.json"), "--mock", "--save-dir", str(tmp_path)]
    )
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0
    assert "_finalization_deadline" not in captured["config"]

    # Verify downstream arithmetic: a wave-2 topic starting past command_started + 280s
    # still gets its full finalization window up to enrich_deadline - reserve.
    worker_config = {**captured["config"], "_research_deadline": 1450.0}
    assert pipeline._finalization_deadline(worker_config, run_started=1300.0) == pytest.approx(1440.0)


def test_high005_one_shot_discover_keeps_280s_finalization_deadline(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run_discover(args, config):
        captured["config"] = dict(config)
        return 0

    monkeypatch.setattr(env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(cli, "_run_discover", fake_run_discover)

    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["--discover", "ai", "--mock"])
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0
    assert captured["config"]["_finalization_deadline"] == pytest.approx(
        1000.0 + pipeline.FINALIZATION_WORK_CUTOFF_SECONDS
    )


@pytest.mark.parametrize("timeout_env,expected_timeout", [(None, 600), ("720", 720)])
def test_high005_deep_research_derives_finalization_deadline_from_deep_timeout(
    monkeypatch, timeout_env: str | None, expected_timeout: int
):
    captured: dict[str, object] = {}

    def fake_pipeline_run(**kwargs):
        captured["config"] = dict(kwargs["config"])
        return _make_report(kwargs["topic"])

    base_config = {"PERPLEXITY_API_KEY": "dummy-pplx-key"}
    if timeout_env is not None:
        base_config["LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS"] = timeout_env

    monkeypatch.setattr(env, "get_config", lambda **kwargs: dict(base_config))
    monkeypatch.setattr(env, "hosted_backend_configured", lambda: False)
    monkeypatch.setattr(cli, "_law7_host_plan_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_x_handle_decision_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "diagnose", lambda *args, **kwargs: {"available_sources": ["perplexity"]})
    monkeypatch.setattr(pipeline, "run", fake_pipeline_run)
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "_write_last_run", lambda *args, **kwargs: True)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")

    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["Acme widgets", "--deep-research", "--mock"])
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0
    assert captured["config"]["_finalization_deadline"] == pytest.approx(
        1000.0
        + expected_timeout
        + pipeline.DEEP_RESEARCH_RECEIPT_SECONDS
        + pipeline.FINALIZATION_WORK_CUTOFF_SECONDS
    )


def test_high005_standard_research_keeps_280s_finalization_deadline(monkeypatch):
    captured: dict[str, object] = {}

    def fake_pipeline_run(**kwargs):
        captured["config"] = dict(kwargs["config"])
        return _make_report(kwargs["topic"])

    monkeypatch.setattr(env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(env, "hosted_backend_configured", lambda: False)
    monkeypatch.setattr(cli, "_law7_host_plan_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_x_handle_decision_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "diagnose", lambda *args, **kwargs: {"available_sources": ["reddit"]})
    monkeypatch.setattr(pipeline, "run", fake_pipeline_run)
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "_write_last_run", lambda *args, **kwargs: True)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")

    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["Acme widgets", "--mock"])
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0
    assert captured["config"]["_finalization_deadline"] == pytest.approx(
        1000.0 + pipeline.FINALIZATION_WORK_CUTOFF_SECONDS
    )


# ---------------------------------------------------------------------------
# HIGH-011: comparison runs persist all entities' findings to SQLite store
# ---------------------------------------------------------------------------


def test_high011_comparison_run_persists_all_entities_to_sqlite_store(
    monkeypatch, tmp_path: Path, capsys
):
    from lib import fanout

    openai_items = [
        _make_item("o1", "reddit", "OpenAI ships new model", url="https://reddit.com/r/ai/o1"),
        _make_item("o2", "x", "OpenAI pricing update", url="https://x.com/user/status/101"),
    ]
    anthropic_items = [
        _make_item("a1", "reddit", "Anthropic Claude release", url="https://reddit.com/r/ai/a1"),
        _make_item("a2", "x", "Anthropic developer tools", url="https://x.com/user/status/201"),
        _make_item("a3", "hackernews", "Anthropic paper", url="https://news.ycombinator.com/item?id=301"),
    ]
    google_items = [
        _make_item("g1", "reddit", "Google Gemini update", url="https://reddit.com/r/ai/g1"),
    ]

    entity_reports = [
        ("OpenAI", _make_report("OpenAI", items_by_source={"reddit": [openai_items[0]], "x": [openai_items[1]]})),
        (
            "Anthropic",
            _make_report(
                "Anthropic",
                items_by_source={
                    "reddit": [anthropic_items[0]],
                    "x": [anthropic_items[1]],
                    "hackernews": [anthropic_items[2]],
                },
            ),
        ),
        ("Google", _make_report("Google", items_by_source={"reddit": [google_items[0]]})),
    ]

    monkeypatch.setattr(env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(env, "hosted_backend_configured", lambda: False)
    monkeypatch.setattr(cli, "_law7_host_plan_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_x_handle_decision_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "diagnose", lambda *args, **kwargs: {"available_sources": ["reddit", "x"]})
    monkeypatch.setattr(fanout, "run_competitor_fanout", lambda **kwargs: list(entity_reports))
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "_write_last_run", lambda *args, **kwargs: True)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")

    parser = cli.build_parser()
    argv = [
        "OpenAI vs Anthropic vs Google",
        "--mock",
        "--store",
        "--save-dir",
        str(tmp_path),
    ]
    args, extra = parser.parse_known_args(argv)
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0

    err = capsys.readouterr().err
    assert "[last30days] Stored 6 new, 0 updated findings" in err

    db_path = tmp_path / "research.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        topics = {row[0] for row in conn.execute("SELECT name FROM topics").fetchall()}
        urls = {row[0] for row in conn.execute("SELECT source_url FROM findings").fetchall()}

    assert topics == {"OpenAI", "Anthropic", "Google"}
    assert urls == {
        "https://reddit.com/r/ai/o1",
        "https://x.com/user/status/101",
        "https://reddit.com/r/ai/a1",
        "https://x.com/user/status/201",
        "https://news.ycombinator.com/item?id=301",
        "https://reddit.com/r/ai/g1",
    }

    # Second run updates all 6 findings across all 3 entities.
    args2, extra2 = parser.parse_known_args(argv)
    rc2 = cli._main(parser, args2, extra2, command_started=1000.0)
    assert rc2 == 0
    err2 = capsys.readouterr().err
    assert "[last30days] Stored 0 new, 6 updated findings" in err2


# ---------------------------------------------------------------------------
# HIGH-040: normalized TikTok/Instagram/Threads handles in Top voices
# ---------------------------------------------------------------------------


def test_high040_normalized_shortform_and_threads_accounts_included_in_footer_and_stats():
    tiktok_item = normalize._normalize_shortform_video(
        "tiktok",
        {
            "id": "tt1",
            "text": "Acme launch breakdown",
            "author_name": "clipper",
            "url": "https://www.tiktok.com/@clipper/video/1",
            "date": "2026-10-20",
            "engagement": {"views": 50000, "likes": 400, "shares": 20, "comments": 15},
        },
        0,
        "2026-10-01",
        "2026-10-31",
        "TT",
        "TikTok",
    )
    ig_item = normalize._normalize_shortform_video(
        "instagram",
        {
            "id": "ig1",
            "text": "Acme reel demo",
            "author_name": "reelmaker",
            "url": "https://www.instagram.com/reel/1/",
            "date": "2026-10-21",
            "engagement": {"views": 20000, "likes": 250, "comments": 10},
        },
        0,
        "2026-10-01",
        "2026-10-31",
        "IG",
        "Instagram",
    )
    threads_item = normalize._normalize_microblog(
        "threads",
        {
            "id": "th1",
            "text": "Acme threads review",
            "handle": "@threader",
            "url": "https://www.threads.net/@threader/post/1",
            "date": "2026-10-22",
            "engagement": {"likes": 120, "replies": 8},
        },
        0,
        "2026-10-01",
        "2026-10-31",
        "TH",
        "Threads",
    )
    # Zero-like spammer on TikTok must not slip into ## Stats Top voices as a non-handle.
    spam_items = [
        normalize._normalize_shortform_video(
            "tiktok",
            {
                "id": f"spam{i}",
                "text": f"Acme spam {i}",
                "author_name": "spammer",
                "url": f"https://www.tiktok.com/@spammer/video/{i}",
                "date": "2026-10-20",
                "engagement": {"views": 10, "likes": 0, "shares": 0, "comments": 0},
            },
            i + 1,
            "2026-10-01",
            "2026-10-31",
            "TT",
            "TikTok",
        )
        for i in range(10)
    ]
    # A multi-word display name without @ must not be treated as a @handle.
    display_name_item = _make_item(
        "tiktok-name", "tiktok", "Acme clip", author="Jane Doe", engagement={"likes": 999}
    )

    assert tiktok_item.author == "clipper"
    assert ig_item.author == "reelmaker"
    assert threads_item.author == "threader"

    report = _make_report(
        "Acme",
        items_by_source={
            "tiktok": [tiktok_item, *spam_items, display_name_item],
            "instagram": [ig_item],
            "threads": [threads_item],
        },
    )

    ranked = render._rank_voice_handles([tiktok_item, ig_item, threads_item, *spam_items, display_name_item])
    assert ranked == ["@clipper", "@reelmaker", "@threader"]

    footer_voices = render._top_voices_footer_line(report)
    assert footer_voices == "🗣️ Top voices: @clipper, @reelmaker, @threader"

    overall = render._top_voices_overall(report.items_by_source)
    assert "spammer" not in overall
    assert "@spammer" not in overall
    assert "@clipper" in overall
    assert "@reelmaker" in overall
    assert "@threader" in overall


def test_high040_truthsocial_comment_attribution_has_at_prefix():
    assert render._comment_attribution("truthsocial", "realuser") == "@realuser"
    assert render._comment_attribution("truthsocial", "@realuser") == "@realuser"


# ---------------------------------------------------------------------------
# HIGH-044: Bot filter strips leading @ / u/ / /u/ prefixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bot_author",
    [
        "u/AutoModerator",
        "/u/AutoModerator",
        "U/AutoModerator",
        "@bot",
        "u/bot",
        "/u/bot",
        "@bot_helper",
        "@dependabot",
        "@github-actions",
        "@u/AutoModerator",
    ],
)
def test_high044_prefixed_bot_authors_rejected_by_is_community_comment(bot_author: str):
    item = _make_item("r1", "reddit", "Acme release", container="python")
    comment = {"author": bot_author, "score": 500, "excerpt": "Pinned thread notice about Acme."}
    assert render._is_community_comment(item, comment, comment["excerpt"]) is False


@pytest.mark.parametrize("human_author", ["u/robotics", "@abbot", "@real_user"])
def test_high044_non_bot_prefixed_authors_still_allowed(human_author: str):
    item = _make_item("r1", "reddit", "Acme release", container="python")
    comment = {"author": human_author, "score": 20, "excerpt": "Great release from Acme."}
    assert render._is_community_comment(item, comment, comment["excerpt"]) is True


def test_high044_prefixed_bots_excluded_from_quote_pool_and_next_comment_promoted():
    item1 = _make_item(
        "r1",
        "reddit",
        "Acme release discussion",
        container="python",
        engagement={"score": 200},
        metadata={
            "top_comments": [
                {"author": "u/AutoModerator", "score": 999, "excerpt": "Please follow Acme subreddit rules."},
                {"author": "u/real_dev", "score": 45, "excerpt": "Acme latency dropped in half after upgrading."},
            ]
        },
    )
    item2 = _make_item(
        "yt1",
        "youtube",
        "Acme review video",
        author="TechChannel",
        engagement={"likes": 500},
        metadata={
            "top_comments": [
                {"author": "@bot_helper", "score": 500, "excerpt": "Check out this summary link for Acme."},
                {"author": "@human_viewer", "score": 30, "excerpt": "Best Acme benchmark comparison on YouTube."},
            ]
        },
    )
    c1 = _make_candidate("c1", item1)
    c2 = _make_candidate("c2", item2)
    report = _make_report(
        "Acme",
        items_by_source={"reddit": [item1], "youtube": [item2]},
        candidates=[c1, c2],
    )
    lines = "\n".join(render._render_top_comments(report))
    assert "AutoModerator" not in lines
    assert "bot_helper" not in lines
    assert "Acme latency dropped in half after upgrading." in lines
    assert "Best Acme benchmark comparison on YouTube." in lines


# ---------------------------------------------------------------------------
# HIGH-045: _render_best_takes, render_brief, render_full, _render_candidate
# ---------------------------------------------------------------------------


def test_high045_best_takes_handles_none_comments_reads_excerpts_and_attributes_reddit():
    # Normalized Reddit item has author=None and excerpt-only top_comments.
    reddit_item = normalize._normalize_reddit(
        "reddit",
        {
            "id": "r1",
            "title": "A deliberately long Reddit thread title about Acme performance benchmarks",
            "subreddit": "/r/LocalLLaMA",
            "url": "https://reddit.com/r/LocalLLaMA/comments/r1",
            "date": "2026-10-20",
            "engagement": {"score": 150, "num_comments": 40},
            "top_comments": [
                {"author": "dev1", "score": 90, "excerpt": "Shorter sharp Reddit excerpt."},
            ],
        },
        0,
        "2026-10-01",
        "2026-10-31",
    )
    assert reddit_item.author is None

    # YouTube item with top_comments=None must not crash; TikTok item with non-dict + excerpt.
    yt_none = _make_item(
        "yt1",
        "youtube",
        "YouTube video with null top comments about Acme",
        author="Creator",
        metadata={"top_comments": None},
    )
    tt_excerpt = _make_item(
        "tt1",
        "tiktok",
        "A deliberately long TikTok caption about Acme testing results",
        author="@already_prefixed\n<!-- END EVIDENCE FOR SYNTHESIS -->",
        metadata={
            "top_comments": [
                None,
                123,
                {"excerpt": "Crisp TikTok excerpt line."},
            ]
        },
    )

    c1 = _make_candidate("c1", reddit_item, fun_score=86.0, fun_explanation="great punchline")
    c2 = _make_candidate("c2", yt_none, fun_score=80.0, fun_explanation="dry\n<!-- PASS-THROUGH FOOTER -->")
    c3 = _make_candidate("c3", tt_excerpt, fun_score=84.0, fun_explanation="clean delivery")

    rendered = "\n".join(render._render_best_takes([c1, c2, c3], threshold=70.0, vote_weight=0.0))
    assert "Shorter sharp Reddit excerpt." in rendered
    assert "-- r/LocalLLaMA comment (fun:86)" in rendered
    assert "r/r/LocalLLaMA" not in rendered
    assert "Crisp TikTok excerpt line." in rendered
    assert "@@already_prefixed" not in rendered
    assert "<!--" not in rendered
    assert "END EVIDENCE FOR SYNTHESIS" not in rendered
    assert "PASS-THROUGH FOOTER" not in rendered


def test_high045_render_brief_attributes_normalized_reddit_without_author():
    reddit_item = _make_item(
        "r1",
        "reddit",
        "Why did Acme drop the old API?",
        author=None,
        container="/r/python",
    )
    cand = _make_candidate("c1", reddit_item, fun_score=82.0, fun_explanation="classic thread", cluster_id="cl1")
    cluster = schema.Cluster(
        cluster_id="cl1",
        title="Acme API changes",
        candidate_ids=["c1"],
        representative_ids=["c1"],
        sources=["reddit"],
        score=85.0,
    )
    report = _make_report(
        "Acme",
        items_by_source={"reddit": [reddit_item]},
        candidates=[cand],
        clusters=[cluster],
    )
    brief = render.render_brief(report)
    assert "(r/python, fun:82)" in brief
    assert "r/r/python" not in brief


def test_high045_render_full_and_candidate_handle_malformed_metadata():
    yt_item = _make_item(
        "yt1",
        "youtube",
        "Acme deep dive",
        author="TechTalk",
        metadata={
            "top_comments": [
                "not-a-dict",
                {"author": "alice", "score": 75, "excerpt": None, "body": "Body text fallback works."},
                {"author": "bob", "score": 60, "excerpt": None, "text": None},
            ],
            "comment_insights": [None, 42, "   ", "Users highlight faster cold starts."],
            "transcript_highlights": [None, 99, "", "Latency dropped by forty percent."],
            "transcript_snippet": 12345,
        },
    )
    pm_item = _make_item(
        "pm1",
        "polymarket",
        "Will Acme ship v5?",
        metadata={
            "question": "Will Acme ship v5 by December?",
            "outcome_prices": [
                ("BoolPrice", True),
                ("ShortTuple",),
                ("LongTuple", 0.5, "extra"),
                ("NaNPrice", float("nan")),
                ("InfPrice", float("inf")),
                ("Yes", 0.65),
            ],
        },
    )
    only_none_item = _make_item(
        "r2",
        "reddit",
        "Acme thread with null insights",
        container="python",
        metadata={"top_comments": None, "comment_insights": [None, ""]},
    )

    c_yt = _make_candidate("c1", yt_item, cluster_id="cl1")
    cluster = schema.Cluster(
        cluster_id="cl1",
        title="Acme updates",
        candidate_ids=["c1"],
        representative_ids=["c1"],
        sources=["youtube"],
        score=80.0,
    )
    report = _make_report(
        "Acme",
        items_by_source={"youtube": [yt_item], "polymarket": [pm_item], "reddit": [only_none_item]},
        candidates=[c_yt],
        clusters=[cluster],
    )

    full = render.render_full(report)
    assert "Body text fallback works." in full
    assert "Users highlight faster cold starts." in full
    assert "Latency dropped by forty percent." in full
    assert "Odds: Yes: 65%" in full
    assert "BoolPrice" not in full
    assert "NaNPrice" not in full

    cand_lines = "\n".join(render._render_candidate(c_yt, prefix="1.", report=report))
    assert "Body text fallback works." in cand_lines
    assert "Insight: Users highlight faster cold starts." in cand_lines
    assert "Latency dropped by forty percent." in cand_lines


# ---------------------------------------------------------------------------
# HIGH-048: Unguarded dict/type crashes in render helpers
# ---------------------------------------------------------------------------


def test_high048_aggregate_engagement_handles_none_strings_bools_and_nonfinite():
    items = [
        _make_item("x1", "x", engagement={"likes": "42", "reposts": "5"}),
        _make_item("x2", "x", engagement={"likes": 8, "reposts": True}),
        _make_item("x3", "x", engagement={"likes": float("nan"), "reposts": float("inf")}),
        _make_item("x4", "x", engagement={"likes": "not-a-number"}),
    ]
    none_eng = _make_item("x5", "x")
    none_eng.engagement = None  # type: ignore[assignment]
    bad_eng = _make_item("x6", "x")
    bad_eng.engagement = "corrupt"  # type: ignore[assignment]
    items.extend([none_eng, bad_eng])

    assert render._aggregate_engagement("x", items) == "50likes, 5rt"


def test_high048_top_comments_list_handles_leading_non_dict_and_string_or_bad_scores():
    reddit_item = _make_item(
        "r1",
        "reddit",
        metadata={
            "top_comments": [
                "not-a-dict",
                {"author": "ok_str", "score": "15", "excerpt": "Valid string score"},
                {"author": "too_low", "score": "4", "excerpt": "Below floor"},
                {"author": "bad_str", "score": "twelve", "excerpt": "Non-numeric"},
                {"author": "bool_score", "score": True, "excerpt": "Bool score"},
                {"author": "nan_score", "score": float("nan"), "excerpt": "NaN score"},
            ]
        },
    )
    selected = render._top_comments_list(reddit_item)
    assert [c["author"] for c in selected] == ["ok_str"]

    hn_item = _make_item(
        "hn1",
        "hackernews",
        metadata={"top_comments": ["junk", {"author": "pg", "score": None, "excerpt": "HN comment"}]},
    )
    assert [c["author"] for c in render._top_comments_list(hn_item)] == ["pg"]


def test_high048_market_windows_and_yahoo_footer_handle_partial_and_malformed_dicts():
    assert render._is_market_window({"symbol": "ACME", "start_close": True, "end_close": 2.0, "change_pct": 1.0}) is False
    assert render._is_market_window({"symbol": "ACME", "start_close": float("nan"), "end_close": 2.0, "change_pct": 1.0}) is False
    assert render._market_window_lines({"symbol": "ACME"}) == []

    partial_window = {
        "symbol": "ACME",
        "company": "Acme Corp",
        "currency": "USD",
        "range_from": "2026-10-01",
        "range_to": "2026-10-31",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "start_close": 100.0,
        "end_close": 115.0,
        "change_pct": 15.0,
        "benchmark": {"name": "S&P 500", "change_pct": True},
        "top_moves": [
            "not-a-dict",
            {"date": "2026-10-05"},
            {"date": "2026-10-06", "previous_close": 100.0, "close": 102.0, "change_pct": float("nan")},
            {"date": "2026-10-12", "previous_close": 102.0, "close": 112.2, "change_pct": 10.0},
        ],
    }
    report = _make_report("Acme", artifacts={"yahoo_finance": partial_window})

    footer_line = render._yahoo_finance_footer_line(report)
    assert footer_line is not None
    assert "ACME +15.0%" in footer_line
    assert "S&P 500" not in footer_line
    assert "best day Oct 12 +10.0%" in footer_line

    market_lines = "\n".join(render._market_window_lines(partial_window))
    assert "First close 100.00" in market_lines
    assert "S&P 500 over the same window" not in market_lines
    assert "2026-10-12: 102.00 to 112.20, +10.0%" in market_lines
    assert "2026-10-05" not in market_lines


def test_high048_candidate_freshness_flag_handles_unknown_verdict_state():
    report = _make_report(
        "Acme",
        freshness_verdicts=[
            schema.FreshnessVerdict(
                claim_id="clm1",
                candidate_id="c1",
                claim="Claim 1",
                source="x",
                source_item_id="x1",
                verdict="future_state",  # type: ignore[arg-type]
                checked_at="2026-10-31T12:00:00Z",
                source_url="https://example.test/1",
                evidence_url="https://example.test/1",
            ),
            schema.FreshnessVerdict(
                claim_id="clm2",
                candidate_id="c1",
                claim="Claim 2",
                source="x",
                source_item_id="x2",
                verdict="stale",
                checked_at="2026-10-31T12:00:00Z",
                source_url="https://example.test/2",
                evidence_url="https://example.test/2",
            ),
        ],
    )
    assert render._candidate_freshness_flag(report, "c1") == " [freshness:stale,future_state]"


def test_high048_resolved_entities_block_handles_non_dict_resolved_artifact():
    good = _make_report(
        "Acme",
        artifacts={"resolved": {"x_handle": "acme", "subreddits": ["acme"]}},
    )
    non_dict = _make_report(
        "Beta",
        artifacts={"resolved": "corrupted-non-dict"},
    )
    malformed_fields = _make_report(
        "Gamma",
        artifacts={
            "resolved": {
                "subreddits": "not-a-list",
                "github_repos": 123,
                "ticker": "GAM",
                "ticker_source": "host",
                "tickers": "GAM",
                "context": 999,
            }
        },
    )
    lines = render._render_resolved_entities_block(
        [("Acme", good), ("Beta", non_dict), ("Gamma", malformed_fields)]
    )
    rendered = "\n".join(lines)
    assert "- **Acme**: X @acme | Subs r/acme" in rendered
    assert "- **Beta**: X - | Subs - | GitHub - | Context: -" in rendered
    assert "- **Gamma**: X - | Subs - | GitHub - | Ticker GAM (host search) | Context: 999" in rendered


# ---------------------------------------------------------------------------
# Adversarial review regressions: huge integers, chained prefixes, Polymarket
# non-string labels / metadata=None, defensive persist_report return handling
# ---------------------------------------------------------------------------


def test_review_huge_integers_do_not_overflow_or_exceed_str_digit_limit():
    huge_400 = 10**400
    huge_5000 = 10**5000
    near_max = 10**308

    assert render._is_finite_number(huge_400) is False
    assert render._is_finite_number(huge_5000) is False
    assert render._is_finite_number(near_max) is True

    assert render._coerce_numeric(huge_400) is None
    assert render._coerce_numeric(huge_5000) is None
    assert render._coerce_numeric("1" + "0" * 5000) is None
    assert render._coerce_numeric(near_max) == near_max

    assert render._format_number(huge_400) == ""
    assert render._format_number(huge_5000) == ""
    assert render._format_number(float("inf")) == ""
    assert render._format_number(float("nan")) == ""
    assert render._fmt_pairs([(huge_5000, "likes"), (25, "rt")]) == "25rt"

    # Two 10**308 items overflow when summed; huge_400 alone is skipped without hiding valid items.
    assert render._aggregate_engagement(
        "x",
        [
            _make_item("a", "x", engagement={"likes": huge_400}),
            _make_item("b", "x", engagement={"likes": 42}),
        ],
    ) == "42likes"
    assert render._aggregate_engagement(
        "x",
        [
            _make_item("a", "x", engagement={"likes": near_max}),
            _make_item("b", "x", engagement={"likes": near_max}),
        ],
    ) is None

    # Market windows reject huge_400 / huge_5000 without OverflowError.
    assert render._is_market_window(
        {"symbol": "ACME", "start_close": huge_400, "end_close": 100.0, "change_pct": 1.0}
    ) is False
    window = {
        "symbol": "ACME",
        "company": "Acme Corp",
        "currency": "USD",
        "range_from": "2026-10-01",
        "range_to": "2026-10-31",
        "start_date": "2026-10-01",
        "end_date": "2026-10-31",
        "start_close": 100.0,
        "end_close": 110.0,
        "change_pct": 10.0,
        "benchmark": {"name": "S&P 500", "start_close": huge_400, "end_close": 100.0, "change_pct": 1.0},
        "top_moves": [
            {"date": "2026-10-05", "previous_close": huge_5000, "close": 100.0, "change_pct": 1.0},
        ],
    }
    market_lines = "\n".join(render._market_window_lines(window))
    assert "First close 100.00" in market_lines
    assert "S&P 500 over the same window" not in market_lines
    assert "Largest daily moves" not in market_lines

    # Polymarket price=10**308 overflows price*100; huge_5000 comment score must not raise ValueError.
    pm_overflow = _make_item(
        "pm-huge",
        "polymarket",
        "Will Acme overflow?",
        metadata={
            "question": "Will Acme overflow?",
            "outcome_prices": [
                ("Overflow", near_max),
                ("Huge", huge_400),
                ("Yes", 0.42),
            ],
        },
    )
    yt_huge_comment = _make_item(
        "yt-huge",
        "youtube",
        "Acme video",
        metadata={
            "top_comments": [
                {"author": "alice", "score": huge_5000, "score_pending": True, "excerpt": "Pending huge score"},
                {"author": "bob", "score": huge_5000, "excerpt": "Regular huge score"},
            ]
        },
    )
    cand = _make_candidate("c-huge", yt_huge_comment)
    report = _make_report(
        "Acme",
        items_by_source={"polymarket": [pm_overflow], "youtube": [yt_huge_comment]},
        candidates=[cand],
    )
    full = render.render_full(report)
    assert "Odds: Yes: 42%" in full
    assert "Overflow:" not in full
    assert "Pending huge score" in full
    cand_text = "\n".join(render._render_candidate(cand, prefix="1.", report=report))
    assert "Pending huge score" in cand_text
    assert render._polymarket_top_markets([pm_overflow]) == ["Acme overflow 42%"]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("u/@bot", "bot"),
        ("/u/@AutoModerator", "AutoModerator"),
        ("@u/@bot", "bot"),
        ("u/@Alice", "Alice"),
        ("  /u/u/@dependabot  ", "dependabot"),
    ],
)
def test_review_strip_author_prefix_loops_until_stable(raw: str, expected: str):
    assert render._strip_author_prefix(raw) == expected


def test_review_chained_bot_prefixes_blocked_and_author_key_normalized():
    item = _make_item("r1", "reddit", "Acme release", author="Alice", container="python")
    for bot_author in ("u/@bot", "/u/@AutoModerator", "@u/@dependabot"):
        assert render._is_community_comment(item, {"author": bot_author}, "Automated note.") is False
    assert render._author_key("u/@Alice") == "alice"
    assert render._is_community_comment(item, {"author": "u/@Alice"}, "OP reply here.") is False


def test_review_polymarket_top_markets_handles_non_string_label_and_none_metadata():
    non_string_label = _make_item(
        "pm1",
        "polymarket",
        "Will Acme hit target?",
        metadata={
            "question": "Will Acme hit target?",
            "outcome_prices": [(123, 0.5)],
        },
    )
    none_metadata = _make_item("pm2", "polymarket", "Will Acme ship?")
    none_metadata.metadata = None  # type: ignore[assignment]

    summaries = render._polymarket_top_markets([non_string_label, none_metadata], topic="Acme")
    assert summaries == ["Acme hit target: 123 50%"]


def test_review_comparison_store_handles_none_or_partial_persist_report_return(
    monkeypatch, tmp_path: Path, capsys
):
    from lib import fanout

    entity_reports = [
        ("OpenAI", _make_report("OpenAI")),
        ("Anthropic", _make_report("Anthropic")),
    ]
    returns = iter([None, {"new": 3}])

    monkeypatch.setattr(env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(env, "hosted_backend_configured", lambda: False)
    monkeypatch.setattr(cli, "_law7_host_plan_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_x_handle_decision_signal", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "diagnose", lambda *args, **kwargs: {"available_sources": ["reddit"]})
    monkeypatch.setattr(fanout, "run_competitor_fanout", lambda **kwargs: list(entity_reports))
    monkeypatch.setattr(cli, "persist_report", lambda *args, **kwargs: next(returns))
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, "_write_last_run", lambda *args, **kwargs: True)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")

    parser = cli.build_parser()
    args, extra = parser.parse_known_args(
        ["OpenAI vs Anthropic", "--mock", "--store", "--save-dir", str(tmp_path)]
    )
    rc = cli._main(parser, args, extra, command_started=1000.0)
    assert rc == 0
    assert "[last30days] Stored 3 new, 0 updated findings" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Round 2 adversarial review regressions: Polymarket Markets helpers,
# voice ranking overflow/NaN/Inf, _sum_engagement, _format_item_engagement
# ---------------------------------------------------------------------------


def test_review2_polymarket_markets_helpers_and_render_compact_handle_huge_and_none_metadata():
    huge_400 = 10**400
    huge_5000 = 10**5000
    near_max = 10**308

    # Direct calls with metadata=None must not crash.
    none_meta = _make_item("pm-none", "polymarket", "Will Acme ship?")
    none_meta.metadata = None  # type: ignore[assignment]
    assert render._polymarket_odds(none_meta) is None
    assert render._polymarket_matched_part(none_meta, []) is None
    assert render._polymarket_odds_line(none_meta, "2026") == ""

    # Pre-stored odds + matched_market + move with huge / overflowing / NaN values.
    pm_stored = _make_item(
        "pm-stored",
        "polymarket",
        "Will Acme launch v5?",
        metadata={
            "odds": {
                "kind": "outcomes",
                "subject": "Acme launch v5",
                "parts": [
                    {"label": "Overflow", "price": near_max, "move": [huge_400, "today"]},
                    {"label": "Huge", "price": huge_400, "move": [float("nan"), "today"]},
                    {"label": "Yes", "price": 0.5, "move": [2.0, "today"]},
                ],
            },
            "matched_market": {
                "label": "Acme",
                "price": near_max,
                "contender": True,
                "rank": huge_5000,
                "race_size": huge_5000,
            },
            "outcomes_remaining": huge_5000,
        },
    )
    assert render._polymarket_matched_part(pm_stored, []) is None
    assert render._odds_points([huge_400, "today"]) == ""
    assert render._odds_points([float("nan"), "today"]) == ""
    assert render._odds_points([float("inf"), "today"]) == ""
    odds_line = render._polymarket_odds_line(pm_stored, "2026")
    assert "Yes at 50% (up 2 pts today)" in odds_line
    assert "Overflow" not in odds_line

    # Compact + full rendering on outcome_prices with 10**400 and outcomes_remaining=10**5000.
    pm_raw = _make_item(
        "pm-raw",
        "polymarket",
        "Will Acme hit target?",
        metadata={
            "question": "Will Acme hit target?",
            "outcome_prices": [("Huge", huge_400), ("Overflow", near_max), ("Yes", 0.5)],
            "outcomes_remaining": huge_5000,
        },
    )
    report = _make_report("Acme", items_by_source={"polymarket": [pm_raw, pm_stored, none_meta]})
    compact = render.render_compact(report)
    assert "## Polymarket Markets" in compact
    assert "Yes at 50%" in compact
    assert "more outcome" not in compact

    full = render.render_full(report)
    assert "Odds: Yes: 50%" in full
    assert "more outcomes" not in full


def test_review2_voice_ranking_handles_huge_nan_inf_and_overflowing_engagement():
    huge_400 = 10**400
    near_max = 10**308

    items = [
        _make_item("x1", "x", "Acme news 1", author="huge_voice", engagement={"likes": huge_400}),
        _make_item("x2", "x", "Acme news 2", author="nan_voice", engagement={"likes": float("nan")}),
        _make_item("x3", "x", "Acme news 3", author="inf_voice", engagement={"likes": float("inf")}),
        _make_item(
            "x4",
            "x",
            "Acme news 4",
            author="sum_overflow",
            engagement={"likes": near_max, "reposts": near_max},
        ),
        _make_item("x5", "x", "Acme news 5", author="normal_voice", engagement={"likes": 120}),
    ]
    assert render._voice_post_engagement(items[0]) is None
    assert render._voice_post_engagement(items[1]) is None
    assert render._voice_post_engagement(items[2]) is None
    assert render._voice_post_engagement(items[3]) == near_max
    assert render._voice_post_engagement(items[4]) == 120

    report = _make_report("Acme", items_by_source={"x": items})
    ranked = render._rank_voice_handles(items)
    assert "@normal_voice" in ranked
    assert "@sum_overflow" in ranked
    compact = render.render_compact(report)
    assert "@normal_voice" in compact


def test_review2_sum_engagement_footer_and_format_item_engagement_handle_huge_ints():
    huge_5000 = 10**5000
    near_max = 10**308

    hn_huge = _make_item(
        "hn-huge",
        "hackernews",
        "Acme on HN",
        engagement={"points": huge_5000, "comments": 12},
    )
    hn_ok = _make_item(
        "hn-ok",
        "hackernews",
        "Acme HN follow-up",
        engagement={"points": 45, "comments": 3},
    )
    x_overflow_a = _make_item("xa", "x", "Acme A", author="alice", engagement={"likes": near_max})
    x_overflow_b = _make_item("xb", "x", "Acme B", author="bob", engagement={"likes": near_max})
    custom_huge = _make_item(
        "tp1",
        "trustpilot",
        "Acme Trustpilot",
        engagement={"custom_metric": huge_5000, "other": 7},
    )

    assert render._sum_engagement([hn_huge, hn_ok], "points") == 45
    assert render._sum_engagement([x_overflow_a, x_overflow_b], "likes") == 0
    assert render._format_item_engagement(hn_huge) == "12 comments"
    assert render._format_item_engagement(custom_huge) == "7 other"

    report = _make_report(
        "Acme",
        items_by_source={
            "hackernews": [hn_huge, hn_ok],
            "x": [x_overflow_a, x_overflow_b],
            "trustpilot": [custom_huge],
        },
    )
    footer_lines = "\n".join(render._build_source_footer_lines(report))
    assert "45 points" in footer_lines
    assert "15 comments" in footer_lines
    full = render.render_full(report)
    assert "[12 comments]" in full
    assert "[7 other]" in full


# ---------------------------------------------------------------------------
# Greptile review regressions: pipeline Deep Research finalization deadline,
# Best Takes comment-author attribution, signals non-list/huge top_comments,
# and fractional Trustpilot score precision in _format_item_engagement
# ---------------------------------------------------------------------------


def test_greptile_p1_pipeline_deep_research_finalization_not_clamped_to_280s(monkeypatch):
    from concurrent.futures import Future
    from contextlib import contextmanager

    # Direct _finalization_deadline check: explicit CLI deadline and implicit deep timeout.
    deep_cli_cfg = {
        "_deep_research": True,
        "_finalization_deadline": 1000.0 + 600 + 1.0 + 280.0,
    }
    assert pipeline._finalization_deadline(deep_cli_cfg, run_started=1005.0) == pytest.approx(1881.0)
    assert pipeline._finalization_deadline(
        {"_deep_research": True, "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "600"},
        run_started=1000.0,
    ) == pytest.approx(1881.0)
    # Still respects an earlier inherited _research_deadline minus reserve.
    assert pipeline._finalization_deadline(
        {**deep_cli_cfg, "_research_deadline": 1400.0},
        run_started=1005.0,
    ) == pytest.approx(1390.0)
    # Normal runs stay clamped at run_started + 280s.
    assert pipeline._finalization_deadline(
        {"_finalization_deadline": 1881.0}, run_started=1000.0
    ) == pytest.approx(1280.0)

    # End-to-end simulated pipeline.run where Perplexity Deep Research returns at t=300s (>280s):
    # YouTube transcript backfill and reasoning-provider rerank budget must BOTH see >300s.
    clock = 0.0
    backfill_called = []
    rerank_budget_seen = []

    class StreamFuture(Future):
        def __init__(self, source):
            super().__init__()
            self.source = source

    class FakeExecutor:
        def submit(self, fn, **kwargs):
            return StreamFuture(kwargs["source"])

    @contextmanager
    def fake_executor(max_workers):
        yield FakeExecutor()

    def fake_wait(futures, *, timeout, return_when):
        nonlocal clock
        clock += timeout
        done = set()
        for fut in futures:
            if fut.source == "youtube" and clock >= 1.0:
                fut.set_result((
                    [{
                        "video_id": "v1",
                        "title": "Deep deadline probe video",
                        "url": "https://youtube.com/watch?v=v1",
                        "channel_name": "TechChannel",
                        "date": "2026-08-20",
                        "engagement": {"views": 5000, "likes": 100},
                    }],
                    {},
                ))
                done.add(fut)
            elif fut.source == "perplexity" and clock >= 300.0:
                fut.set_result((
                    [{
                        "title": "Deep deadline probe research",
                        "url": "https://example.test/deep-300s",
                        "date": "2026-08-20",
                        "snippet": "Completed at 300 seconds.",
                    }],
                    {"label": "perplexity", "responseId": "deep-300"},
                ))
                done.add(fut)
        return done, futures - done

    class FakeProvider:
        total_usage = None

        def set_request_budget(self, deadline, cancel=None):
            rerank_budget_seen.append((clock, deadline))

        def generate_json(self, model, prompt):
            return {}

    plan = schema.QueryPlan(
        raw_topic="deep deadline probe",
        intent="breaking_news",
        freshness_mode="strict_recent",
        cluster_mode="story",
        source_weights={"perplexity": 1.0, "youtube": 1.0},
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query="deep deadline probe",
                ranking_query="deep deadline probe",
                sources=["youtube", "perplexity"],
            )
        ],
    )
    runtime = schema.ProviderRuntime(
        reasoning_provider="openai",
        planner_model="mock",
        rerank_model="mock",
    )
    monkeypatch.setattr(pipeline, "time", SimpleNamespace(monotonic=lambda: clock))
    monkeypatch.setattr(pipeline, "_abandoning_executor", fake_executor)
    monkeypatch.setattr(pipeline, "futures_wait", fake_wait)
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *a, **k: (runtime, FakeProvider()))
    monkeypatch.setattr(pipeline, "available_sources", lambda *a, **k: ["youtube", "perplexity"])
    monkeypatch.setattr(pipeline.planner, "plan_query", lambda **k: plan)
    monkeypatch.setattr(pipeline, "_retry_thin_sources", lambda **k: None)
    monkeypatch.setattr(
        pipeline.youtube_yt,
        "backfill_transcripts",
        lambda items, **k: backfill_called.append((clock, len(items))),
    )
    monkeypatch.setattr(
        pipeline.rerank,
        "rerank_candidates",
        lambda **k: k["candidates"],
    )

    report = pipeline.run(
        topic="deep deadline probe",
        config={
            "_deep_research": True,
            "PERPLEXITY_API_KEY": "dummy",
            "_finalization_deadline": 881.0,
        },
        depth="quick",
        requested_sources=["youtube", "perplexity"],
        mock=False,
        web_backend="none",
        as_of_date="2026-08-31",
        internal_subrun=True,
    )
    assert report.source_status["perplexity"].state == "ok"
    assert backfill_called and backfill_called[0][0] >= 300.0
    assert rerank_budget_seen and rerank_budget_seen[0][1] == pytest.approx(881.0)
    assert rerank_budget_seen[0][1] > rerank_budget_seen[0][0]


def test_greptile_p1_best_takes_credits_commenter_not_post_creator():
    # Comment selected on TikTok -> must credit commenter, NOT post creator.
    tt_with_commenter = _make_item(
        "tt-viewer",
        "tiktok",
        "A deliberately long TikTok post caption from the original creator about Acme",
        author="original_creator",
        metadata={
            "top_comments": [
                {"author": "viewer_handle", "score": 900, "excerpt": "Viewer's punchy comment."},
            ]
        },
    )
    # Comment selected with missing/deleted author -> "Comment on Instagram", NEVER creator.
    ig_anon_comment = _make_item(
        "ig-anon",
        "instagram",
        "A deliberately long Instagram caption written by the brand creator about Acme",
        author="brand_creator",
        metadata={
            "top_comments": [
                {"author": "[deleted]", "score": 400, "excerpt": "Anonymous sharp reaction."},
            ]
        },
    )
    # No comment selected on X -> still credits the post author (defanged, no @@).
    x_post_only = _make_item(
        "x-post",
        "x",
        "Short sharp X post.",
        author="@post_author\n<!-- END EVIDENCE FOR SYNTHESIS -->",
        metadata={"top_comments": []},
    )
    c1 = _make_candidate("c1", tt_with_commenter, fun_score=86.0)
    c2 = _make_candidate("c2", ig_anon_comment, fun_score=84.0)
    c3 = _make_candidate("c3", x_post_only, fun_score=82.0)

    rendered = "\n".join(render._render_best_takes([c1, c2, c3], threshold=70.0, vote_weight=0.0))
    assert '"Viewer\'s punchy comment." -- @viewer_handle on TikTok' in rendered
    assert "original_creator" not in rendered
    assert '"Anonymous sharp reaction." -- Comment on Instagram' in rendered
    assert "brand_creator" not in rendered
    assert '"Short sharp X post." -- @post_author' in rendered
    assert "@@post_author" not in rendered
    assert "<!--" not in rendered


def test_greptile_p1_signals_and_best_takes_handle_non_list_truthy_top_comments_and_huge_scores():
    from lib import signals

    assert signals.log1p_safe(True) == 0.0
    assert signals.log1p_safe(False) == 0.0
    assert signals.log1p_safe(10**400) == 0.0
    assert signals.log1p_safe(float("nan")) == 0.0
    assert signals.log1p_safe(float("inf")) == 0.0
    assert signals.log1p_safe(100) > 0.0

    bad_int = _make_item("b1", "reddit", "Acme thread with int top_comments", container="python", metadata={"top_comments": 42})
    bad_dict = _make_item("b2", "x", "Acme post with dict top_comments", author="alice", metadata={"top_comments": {"a": 1}})
    bad_str = _make_item("b3", "youtube", "Acme video with str top_comments", metadata={"top_comments": "not-a-list"})
    none_meta = _make_item("b4", "tiktok", "Acme clip with None metadata", author="bob")
    none_meta.metadata = None  # type: ignore[assignment]
    huge_score = _make_item(
        "b5",
        "reddit",
        "Acme thread with huge comment score",
        container="python",
        metadata={"top_comments": [{"author": "dev", "score": 10**400, "excerpt": "Valid comment text."}]},
    )

    cands = [
        _make_candidate(f"c{i}", it, fun_score=85.0, cluster_id="cl1")
        for i, it in enumerate((bad_int, bad_dict, bad_str, none_meta, huge_score), start=1)
    ]
    for item in (bad_int, bad_dict, bad_str, none_meta, huge_score):
        assert signals._top_comment_score(item) == 0.0
    for cand in cands:
        assert signals.top_comment_vote_signal(cand) == 0.0

    best_takes = "\n".join(render._render_best_takes(cands, threshold=70.0))
    assert "## Best Takes" in best_takes
    assert "Valid comment text." in best_takes

    cluster = schema.Cluster(
        cluster_id="cl1",
        title="Acme discussion",
        candidate_ids=[c.candidate_id for c in cands],
        representative_ids=["c1", "c5"],
        sources=["reddit", "x", "youtube", "tiktok"],
        score=85.0,
    )
    report = _make_report(
        "Acme",
        items_by_source={
            "reddit": [bad_int, huge_score],
            "x": [bad_dict],
            "youtube": [bad_str],
            "tiktok": [none_meta],
        },
        candidates=cands,
        clusters=[cluster],
    )
    assert "## Best Takes" in render.render_compact(report)
    assert "## All Items by Source" in render.render_full(report)


def test_greptile_p2_format_item_engagement_preserves_fractional_scores_and_skips_bad_values():
    tp_float = _make_item(
        "tp-float",
        "trustpilot",
        "Acme Trustpilot 4.24",
        engagement={"trustScore": 4.24, "reviews": 1200},
    )
    tp_str = _make_item(
        "tp-str",
        "trustpilot",
        "Acme Trustpilot string 4.24",
        engagement={"trustScore": "4.24", "bad_nan": "NaN", "bad_inf": "inf", "bad_huge": "1e500"},
    )
    tp_bad = _make_item(
        "tp-bad",
        "trustpilot",
        "Acme Trustpilot bad",
        engagement={
            "huge": 10**5000,
            "nan": float("nan"),
            "inf": float("inf"),
            "flag": True,
            "trustScore": 4.24,
        },
    )

    assert render._format_item_engagement(tp_float) == "1200 reviews, 4.24 trustScore"
    assert render._format_item_engagement(tp_str) == "4.24 trustScore"
    assert render._format_item_engagement(tp_bad) == "4.24 trustScore"

    report = _make_report("Acme", items_by_source={"trustpilot": [tp_float, tp_str, tp_bad]})
    full = render.render_full(report)
    assert "[1200 reviews, 4.24 trustScore]" in full
    assert "[4.24 trustScore]" in full
    assert "4.2 trustScore" not in full
