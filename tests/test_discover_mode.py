import argparse
import contextlib
import inspect
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import dates, discovery_handoff, pipeline, planner, reddit_listing, render, rerank, schema


REPO_ROOT = Path(__file__).resolve().parents[1]


def _item(
    item_id: str,
    source: str,
    title: str,
    *,
    published_at: str = "2026-07-09",
    engagement: dict[str, int | float] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=f"https://{source}.example/{item_id}",
        published_at=published_at,
        engagement=engagement or {},
        snippet=f"Evidence about {title}",
    )


def _candidate(item: schema.SourceItem) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=f"candidate-{item.item_id}",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["discovery-listings"],
        native_ranks={f"discovery-listings:{item.source}": 1},
        local_relevance=0.9,
        freshness=95,
        engagement=100,
        source_quality=0.8,
        rrf_score=0.1,
        sources=[item.source],
        source_items=[item],
        final_score=80,
    )


def test_discovery_plan_reuses_category_peer_mapping():
    plan = planner.build_discovery_plan(
        "AI agents",
        available_sources=["reddit", "hackernews"],
    )

    assert plan.category == "ai_agent_framework"
    assert plan.subreddits == ["LangChain", "LocalLLaMA", "AI_Agents", "MachineLearning"]
    assert plan.sources == ["reddit", "hackernews"]


def test_discovery_plan_keeps_keyless_reddit_for_unknown_domains():
    plan = planner.build_discovery_plan(
        "urban gardening",
        available_sources=["reddit", "hackernews"],
    )

    assert plan.category is None
    assert plan.subreddits == ["all"]
    assert plan.sources == ["reddit", "hackernews"]


def test_discovery_plan_empty_domain_is_global_trending():
    """Bare --discover: sweep every river feed's hot list; X sits out of the
    nominate stage because its search lane needs a keyword."""
    plan = planner.build_discovery_plan(
        "",
        available_sources=["reddit", "hackernews", "digg", "x"],
    )

    assert plan.domain == ""
    assert plan.category is None
    assert plan.subreddits == ["all"]
    assert plan.sources == ["reddit", "hackernews", "digg"]
    assert "x" not in plan.sources


def test_global_discovery_disables_keyword_gate():
    """Global trending fetches with keyword_gate=False; domain runs keep it on."""
    seen: dict[str, bool] = {}

    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        seen[plan.domain or "global"] = keyword_gate
        return [], None

    with mock.patch.object(pipeline, "available_sources", return_value=["hackernews"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        pipeline.run_discover(domain="", config={}, as_of_date="2026-07-10")
        pipeline.run_discover(domain="AI agents", config={}, as_of_date="2026-07-10")

    assert seen["global"] is False
    assert seen["AI agents"] is True


def test_uncategorized_discovery_uses_parseable_r_all_listing_paths():
    card = (
        '<shreddit-post permalink="/r/gardening/comments/abc123/urban_garden/" '
        'post-title="Urban gardening is taking off" score="42" comment-count="7" '
        'author="gardener" subreddit-name="gardening" '
        'created-timestamp="2026-07-09T12:00:00+00:00">'
    )
    requested_urls: list[str] = []

    def fake_get(url, **_kwargs):
        requested_urls.append(url)
        return card

    with mock.patch.object(reddit_listing.http, "reddit_keyless_get_text", side_effect=fake_get):
        result = reddit_listing.fetch_discovery_listings(
            ["all"], query="urban gardening",
        )

    assert len(result["items"]) == 1
    assert any("/r/all/rising/" in url for url in requested_urls)
    assert any("/r/all/top/?t=week" in url for url in requested_urls)
    assert all("name=all" not in url for url in requested_urls)


def test_velocity_scoring_favors_a_recent_spike_over_static_bigness():
    recent = _item(
        "recent",
        "reddit",
        "Recent spike",
        published_at="2026-07-09",
        engagement={"score": 100, "num_comments": 10},
    )
    old = _item(
        "old",
        "reddit",
        "Older large thread",
        published_at="2026-06-20",
        engagement={"score": 300, "num_comments": 10},
    )

    assert rerank.engagement_velocity_score(recent, as_of_date="2026-07-10") > (
        rerank.engagement_velocity_score(old, as_of_date="2026-07-10")
    )


def test_domain_filter_ignores_generic_ai_only_matches():
    assert pipeline._matches_discovery_domain(
        "AI agents", "An AI agent bankrupted its operator"
    )
    assert not pipeline._matches_discovery_domain(
        "AI agents", "Global dialogue on AI governance"
    )


@pytest.mark.parametrize(
    ("domain", "listing_title"),
    [
        ("城市园艺", "城市园艺技巧与社区花园"),
        ("גינון עירוני", "מדריך חדש לגינון עירוני"),
    ],
)
def test_domain_filter_tokenizes_non_latin_domains(domain, listing_title):
    assert pipeline._matches_discovery_domain(domain, listing_title)


def test_x_velocity_excludes_views_and_bookmarks():
    xquik_item = _item(
        "xquik",
        "x",
        "X backend reach",
        engagement={
            "likes": 10,
            "reposts": 3,
            "replies": 2,
            "quotes": 1,
            "views": 100_000,
            "bookmarks": 5_000,
        },
    )
    standard_item = _item(
        "standard",
        "x",
        "X backend interactions",
        engagement={"likes": 10, "reposts": 3, "replies": 2, "quotes": 1},
    )

    assert rerank.discovery_engagement_total(xquik_item) == 16
    assert rerank.engagement_velocity_score(
        xquik_item, as_of_date="2026-07-10"
    ) == rerank.engagement_velocity_score(standard_item, as_of_date="2026-07-10")


def test_discovery_renderer_snapshot():
    report = schema.DiscoveryReport(
        domain="AI agents",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        plan=schema.DiscoveryPlan(
            domain="AI agents",
            category="ai_agent_framework",
            subreddits=["AI_Agents"],
            sources=["reddit", "hackernews"],
        ),
        topics=[schema.DiscoveryTopic(
            rank=1,
            name="Agent memory protocols",
            why_spiking="Two independent listing items accelerated this week.",
            momentum="new-this-week",
            velocity_score=123.45,
            sources=["hackernews", "reddit"],
            engagement_by_source={
                "reddit": {"score": 120, "num_comments": 30},
                "hackernews": {"points": 80},
            },
            command='/last30days "Agent memory protocols"',
        )],
    )

    with mock.patch.object(render, "_render_badge", return_value=["BADGE", ""]):
        rendered = render.render_discovery(report)

    assert rendered == (
        "BADGE\n\n"
        "# Trending discovery: AI agents\n\n"
        "Window: 2026-06-10 to 2026-07-10\n"
        "Feeds: reddit, hackernews\n"
        "Communities: r/AI_Agents\n\n"
        "## 1. Agent memory protocols\n\n"
        "**Momentum:** New this week · velocity 123.45\n\n"
        "Two independent listing items accelerated this week.\n\n"
        "**Evidence:** Reddit: score 120, num comments 30 · Hacker News: points 80\n\n"
        "**Research next:** `/last30days \"Agent memory protocols\"`\n"
    )


def test_keyless_discovery_degrades_without_digg():
    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        return pipeline._mock_discovery_items(source, plan.domain, to_date), None

    with mock.patch.object(pipeline, "available_sources", return_value=["reddit", "hackernews"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        report = pipeline.run_discover(
            domain="AI agents",
            config={},
            as_of_date="2026-07-10",
        )

    assert 5 <= len(report.topics) <= 10
    assert report.source_status["reddit"].state == "ok"
    assert report.source_status["hackernews"].state == "ok"
    assert report.source_status["digg"].state == "skipped-unconfigured"
    assert report.source_status["x"].state == "skipped-unconfigured"
    assert all(topic.command.startswith('/last30days "') for topic in report.topics)


def test_discovery_drops_zero_velocity_clusters():
    raw_item = {
        "id": "zero-engagement",
        "text": "AI agent launch with no interactions",
        "url": "https://x.com/example/status/1",
        "author_handle": "example",
        "date": "2026-07-09",
        "engagement": {"likes": 0, "reposts": 0, "replies": 0, "quotes": 0},
        "relevance": 0.9,
    }

    with mock.patch.object(pipeline, "available_sources", return_value=["x"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", return_value=([raw_item], None)):
        report = pipeline.run_discover(
            domain="AI agents",
            config={},
            as_of_date="2026-07-10",
        )

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert any("confidence floor" in warning for warning in report.warnings)


def test_explicit_unavailable_discovery_source_does_not_widen_to_other_sources():
    with mock.patch.object(pipeline, "available_sources", return_value=[]), \
         mock.patch.object(pipeline, "_fetch_discovery_source") as fetch:
        with pytest.raises(ValueError, match="No listing sources are available"):
            pipeline.run_discover(
                domain="AI agents",
                config={},
                requested_sources=["digg"],
                as_of_date="2026-07-10",
            )

    fetch.assert_not_called()


def test_discovery_reads_browser_credentials_and_does_not_schedule_pending_x():
    parser = cli.build_parser()
    args, extra = parser.parse_known_args(["--discover", "AI agents"])
    assert cli._config_policy_for_args(args, "", extra).browser_cookies == "read"

    no_cookies_args, extra = parser.parse_known_args(
        ["--no-browser-cookies", "--discover", "AI agents"]
    )
    assert cli._config_policy_for_args(no_cookies_args, "", extra).browser_cookies == "off"

    fetched_sources: list[str] = []

    def fake_available_sources(config, requested_sources, *, x_pending=None, local_only=False):
        assert x_pending is False
        return ["reddit", "hackernews"] + (["x"] if x_pending is not False else [])

    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        fetched_sources.append(source)
        return pipeline._mock_discovery_items(source, plan.domain, to_date), None

    with mock.patch.object(pipeline, "available_sources", side_effect=fake_available_sources), \
         mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        report = pipeline.run_discover(
            domain="AI agents",
            config={"FROM_BROWSER": "firefox", "_BROWSER_COOKIE_MODE": "plan_only"},
            as_of_date="2026-07-10",
        )

    assert "x" not in fetched_sources
    assert report.source_status["x"].state == "skipped-unconfigured"


def test_authenticated_x_discovery_uses_available_backend():
    plan = planner.build_discovery_plan(
        "AI agents",
        available_sources=["x"],
    )
    raw = pipeline._mock_discovery_items("x", plan.domain, "2026-07-10")
    with mock.patch.object(pipeline.env, "x_backend_chain", return_value=["bird"]), \
         mock.patch.object(pipeline, "_fetch_x_backend", return_value=(raw, "")) as fetch:
        items, error = pipeline._fetch_discovery_source(
            "x",
            plan,
            from_date="2026-06-10",
            to_date="2026-07-10",
            depth="default",
            mock=False,
            config={"AUTH_TOKEN": "dummy", "CT0": "dummy"},
        )

    assert error is None
    assert len(items) == 6
    fetch.assert_called_once()


def test_listing_failure_is_not_reported_as_clean_no_results():
    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        if source == "reddit":
            return [], "connection timed out"
        return pipeline._mock_discovery_items(source, plan.domain, to_date), None

    with mock.patch.object(pipeline, "available_sources", return_value=["reddit", "hackernews"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        report = pipeline.run_discover(
            domain="AI agents",
            config={},
            as_of_date="2026-07-10",
        )

    assert report.source_status["reddit"].state == "timeout"
    assert report.source_status["reddit"].detail == "connection timed out"


def test_discovery_listing_block_is_reported_as_rate_limited():
    # get_text swallows the 429 and hands back None; without the tee the lane
    # could not tell that apart from an empty listing and the sweep reported a
    # clean no-results (issue #899).
    blocked = urllib.error.HTTPError(
        "https://www.reddit.com/svc/shreddit/community-more-posts/rising/",
        429,
        "Too Many Requests",
        {},
        None,
    )

    with mock.patch.object(pipeline, "available_sources", return_value=["reddit"]), \
         mock.patch("lib.http.time.sleep"), \
         mock.patch("lib.http.urllib.request.urlopen", side_effect=blocked):
        report = pipeline.run_discover(
            domain="AI agents",
            config={},
            as_of_date="2026-07-10",
            subreddits=["AI_Agents"],
        )

    outcome = report.source_status["reddit"]
    assert outcome.state == "rate-limited"
    assert "429" in (outcome.detail or "")


def test_reddit_discovery_adapter_preserves_partial_feed_errors():
    """When one shreddit sort lane fails and another succeeds, the failed lane's error is kept.

    Errors are cleared per (sub, sort) pair, not per subreddit. Arctic-shift cannot
    recover a specific sort lane since it's recency-only.
    """
    item = {
        "url": "https://reddit.com/r/example/comments/1",
        "title": "AI agent launch",
        "subreddit": "AI_Agents",  # Required for error-clearing logic.
    }
    with mock.patch.object(
        reddit_listing,
        "_fetch_one_with_status",
        side_effect=[([], "rising timed out"), ([item], None)],
    ), mock.patch(
        "lib.reddit_arctic.fetch_listings",
        return_value=[],  # Arctic supplement returns nothing.
    ):
        result = reddit_listing.fetch_discovery_listings(
            ["AI_Agents"], query="AI agents",
        )

    assert result["items"] == [item]
    # Shreddit top succeeded → no error for top.
    # Shreddit rising failed → error for rising is preserved.
    # Error-clearing is per (sub, sort) pair, not per subreddit.
    assert len(result["errors"]) == 1
    assert "rising" in result["errors"][0].lower()


def test_discovery_cli_json_contract_and_mutual_exclusion():
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--mock",
            "--emit=json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "1.1"
    assert payload["kind"] == "discovery"
    assert 5 <= len(payload["results"]) <= 10
    assert payload["results"][0]["command"].startswith('/last30days "')
    # 1.1 fields ship in every result, with defaults when nothing set them.
    for topic in payload["results"]:
        assert topic["podcast_angle"] is None
        assert topic["x_article_angle"] is None
        assert topic["previously_surfaced_count"] == 0
        assert topic["last_surfaced"] is None
        assert topic["covered"] is False

    invalid = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "topic",
            "--discover",
            "AI agents",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode == 2
    assert "cannot be combined with a positional topic" in invalid.stderr

    drill_conflict = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--drill",
            "1",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drill_conflict.returncode == 2
    assert "mutually exclusive" in drill_conflict.stderr


def _discovery_report(topic: schema.DiscoveryTopic) -> schema.DiscoveryReport:
    return schema.DiscoveryReport(
        domain="AI agents",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        plan=schema.DiscoveryPlan(
            domain="AI agents",
            category="ai_agent_framework",
            subreddits=["AI_Agents"],
            sources=["reddit", "hackernews"],
        ),
        topics=[topic],
    )


def test_discovery_export_round_trips_angles_and_queue_annotations():
    """The 1.1 fields must carry real values through to_discovery_export."""
    payload = schema.to_discovery_export(_discovery_report(schema.DiscoveryTopic(
        rank=1,
        name="Agent memory protocols",
        why_spiking="Two independent listing items accelerated this week.",
        momentum="new-this-week",
        velocity_score=123.45,
        sources=["hackernews", "reddit"],
        engagement_by_source={"reddit": {"score": 120, "num_comments": 30}},
        command='/last30days "Agent memory protocols"',
        podcast_angle="Why agent memory is the next context-window fight",
        x_article_angle="Agent memory protocols, explained through this week's launches",
        previously_surfaced_count=2,
        last_surfaced="2026-07-03",
        covered=True,
    )))

    assert payload["schema_version"] == "1.1"
    result = payload["results"][0]
    assert result["podcast_angle"] == "Why agent memory is the next context-window fight"
    assert result["x_article_angle"] == (
        "Agent memory protocols, explained through this week's launches"
    )
    assert result["previously_surfaced_count"] == 2
    assert result["last_surfaced"] == "2026-07-03"
    assert result["covered"] is True


def test_discovery_topic_constructs_with_only_pre_existing_fields():
    """Pre-1.1 constructor calls must keep working; new fields default."""
    topic = schema.DiscoveryTopic(
        rank=1,
        name="Agent memory protocols",
        why_spiking="Two independent listing items accelerated this week.",
        momentum="building",
        velocity_score=10.0,
        sources=["reddit"],
        engagement_by_source={"reddit": {"score": 120}},
        command='/last30days "Agent memory protocols"',
    )

    assert topic.podcast_angle is None
    assert topic.x_article_angle is None
    assert topic.previously_surfaced_count == 0
    assert topic.last_surfaced is None
    assert topic.covered is False

    result = schema.to_discovery_export(_discovery_report(topic))["results"][0]
    assert result["podcast_angle"] is None
    assert result["x_article_angle"] is None
    assert result["previously_surfaced_count"] == 0
    assert result["last_surfaced"] is None
    assert result["covered"] is False


def test_discovery_cli_mock_render_has_no_angle_or_pipeline_lines():
    """--mock runs never resolve a reasoning provider, so rendered cards must
    omit the U5 angle and Pipeline lines entirely - and stay deterministic
    across runs (same-day mock fixtures)."""
    def _run_once() -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                sys.executable,
                "skills/last30days/scripts/last30days.py",
                "--discover",
                "AI agents",
                "--mock",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    first = _run_once()
    second = _run_once()
    assert first.returncode == 0, first.stderr
    assert "**Podcast angle:**" not in first.stdout
    assert "**X article angle:**" not in first.stdout
    assert "**Pipeline:**" not in first.stdout
    assert first.stdout == second.stdout


def test_discovery_cli_bare_discover_is_global_trending():
    """Bare --discover (no domain) must run global trending, not error."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "--mock",
            "--emit=json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["kind"] == "discovery"
    assert payload["domain"] == ""
    assert payload["outcome"] in {"ok", "nothing-solid"}


def test_discovery_cli_shallow_skips_enrichment():
    """--discover-shallow ranks on listing evidence only (still floored)."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover", "AI agents",
            "--discover-shallow",
            "--mock",
            "--emit=json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["results"], "shallow mock sweep should still rank mock topics"
    assert all(
        "listing item" in topic["why_spiking"] for topic in payload["results"]
    ), "shallow mode must be judged on listing evidence, not enriched corpora"


def test_discovery_cli_rejects_shallow_without_discover():
    """--discover-shallow on a normal topic run must error, not silently no-op
    into a full research pass (P2 from PR #816 review)."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "AI agents",
            "--discover-shallow",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "--discover-shallow only applies to --discover runs" in result.stderr


def test_discovery_cli_rejects_historical_as_of():
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--as-of",
            "2026-06-01",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--as-of cannot be used with --discover" in result.stderr
    assert "current live listings" in result.stderr


def test_discovery_filters_incompatible_default_sources_but_rejects_explicit_only():
    default_result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--mock",
            "--emit=json",
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "LAST30DAYS_DEFAULT_SEARCH": "reddit,x,youtube,hn"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert default_result.returncode == 0, default_result.stderr

    explicit_result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--search=youtube",
            "--mock",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert explicit_result.returncode == 2
    assert "unsupported: youtube" in explicit_result.stderr


def test_detect_category_rejects_suffix_false_positives():
    from lib import categories

    assert categories.detect_category("Dubai agents") is None
    assert categories.detect_category("Thai agents real estate") is None
    assert categories.detect_category("AI agents") == "ai_agent_framework"
    assert categories.detect_category("what's new in ai agent frameworks") == "ai_agent_framework"


def test_discovery_engagement_excludes_rank_metadata():
    from lib import pipeline, schema

    items = [
        schema.SourceItem(
            item_id=f"digg-{i}", source="digg", title="t", body="b",
            url=f"https://di.gg/{i}", published_at="2026-07-05", snippet="s",
            engagement={"postCount": 5, "rank": 100 * (i + 1), "rank_score": 0.5},
        )
        for i in range(3)
    ]
    totals = pipeline._discovery_engagement(items)
    assert totals["digg"]["postCount"] == 15
    assert "rank" not in totals["digg"]
    assert "rank_score" not in totals["digg"]


def test_domain_matching_preserves_non_plural_anchors():
    from lib import pipeline

    assert pipeline._matches_discovery_domain("AI bias", "Addressing AI bias in models")
    assert pipeline._matches_discovery_domain("supply chain crisis", "The crisis deepens for chip supply")
    # Plural matching still works both directions.
    assert pipeline._matches_discovery_domain("AI agents", "The best AI agent stacks")


def test_x_fallback_success_is_clean(monkeypatch):
    from lib import pipeline, env

    calls = []

    def fake_fetch(backend, subquery, from_date, to_date, depth, config):
        calls.append(backend)
        if backend == "bird":
            return [], "cookie expired"
        return [object()], None

    monkeypatch.setattr(pipeline, "_fetch_x_backend", fake_fetch)
    monkeypatch.setattr(env, "x_backend_chain", lambda config: ["bird", "xquik"])
    plan = pipeline.schema.DiscoveryPlan(
        domain="ai agents", category=None, subreddits=[], sources=["x"],
    )
    items, error = pipeline._fetch_discovery_source(
        "x", plan,
        from_date="2026-06-11", to_date="2026-07-11", depth="quick",
        mock=False, config={},
    )
    assert error is None
    assert len(items) == 1
    assert calls == ["bird", "xquik"]


def _digg_envelope(*clusters: dict) -> dict:
    return {"results": list(clusters)}


def _digg_cluster(cluster_id: str, title: str, tldr: str = "") -> dict:
    return {
        "clusterUrlId": cluster_id,
        "title": title,
        "tldr": tldr,
        "rank": 5,
        "postCount": 12,
        "uniqueAuthors": 8,
    }


def test_digg_discovery_drops_off_domain_clusters(monkeypatch):
    """Regression: a crypto sweep surfaced AI stories because the Digg
    branch (an AI-only leaderboard feed) applied no domain filter."""
    envelope = _digg_envelope(
        _digg_cluster("c1", "Bitcoin crypto rally accelerates"),
        _digg_cluster("c2", "OpenAI ships a new frontier model"),
    )
    monkeypatch.setattr(pipeline.digg, "search_digg", lambda *a, **k: envelope)
    plan = schema.DiscoveryPlan(
        domain="crypto", category=None, subreddits=[], sources=["digg"],
    )
    items, error = pipeline._fetch_discovery_source(
        "digg", plan,
        from_date="2026-06-11", to_date="2026-07-11", depth="quick",
        mock=False, config={},
    )
    assert error is None
    titles = [item["title"] for item in items]
    assert titles == ["Bitcoin crypto rally accelerates"]


def test_digg_discovery_keeps_domain_matching_clusters(monkeypatch):
    envelope = _digg_envelope(
        _digg_cluster("c1", "AI agents reshape support desks"),
        _digg_cluster("c2", "The best AI agent stacks compared"),
    )
    monkeypatch.setattr(pipeline.digg, "search_digg", lambda *a, **k: envelope)
    plan = schema.DiscoveryPlan(
        domain="AI agents", category=None, subreddits=[], sources=["digg"],
    )
    items, error = pipeline._fetch_discovery_source(
        "digg", plan,
        from_date="2026-06-11", to_date="2026-07-11", depth="quick",
        mock=False, config={},
    )
    assert error is None
    assert len(items) == 2


def test_digg_discovery_all_filtered_is_clean_no_results(monkeypatch):
    envelope = _digg_envelope(
        _digg_cluster("c1", "OpenAI ships a new frontier model"),
        _digg_cluster("c2", "Anthropic updates its agent SDK"),
    )
    monkeypatch.setattr(pipeline.digg, "search_digg", lambda *a, **k: envelope)
    plan = schema.DiscoveryPlan(
        domain="crypto", category=None, subreddits=[], sources=["digg"],
    )
    items, error = pipeline._fetch_discovery_source(
        "digg", plan,
        from_date="2026-06-11", to_date="2026-07-11", depth="quick",
        mock=False, config={},
    )
    assert error is None
    assert items == []


def test_x_discovery_preserves_producing_backends_own_error(monkeypatch):
    """A backend that returns items plus its own error is a partial outcome;
    only earlier failed-over backends' errors are observability-only."""
    monkeypatch.setattr(
        pipeline, "_fetch_x_backend",
        lambda *a, **k: ([{"id": "x-1", "title": "t"}], "rate limited after page 1"),
    )
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda config: ["bird"])
    plan = schema.DiscoveryPlan(
        domain="ai agents", category=None, subreddits=[], sources=["x"],
    )
    items, error = pipeline._fetch_discovery_source(
        "x", plan,
        from_date="2026-06-11", to_date="2026-07-11", depth="quick",
        mock=False, config={},
    )
    assert len(items) == 1
    assert error == "rate limited after page 1"


# --- U6 persistent topic queue: discovery persistence hook + queue CLI -------


def _queue_topic(rank: int, name: str) -> schema.DiscoveryTopic:
    return schema.DiscoveryTopic(
        rank=rank,
        name=name,
        why_spiking=f"Listing evidence about {name}.",
        momentum="building",
        velocity_score=42.5,
        sources=["reddit"],
        engagement_by_source={"reddit": {"score": 120}},
        command=f'/last30days "{name}"',
    )


def _queue_report(names: list[str]) -> schema.DiscoveryReport:
    return schema.DiscoveryReport(
        domain="AI agents",
        range_from="2026-06-20",
        range_to="2026-07-20",
        generated_at="2026-07-20T00:00:00+00:00",
        plan=schema.DiscoveryPlan(
            domain="AI agents", category=None, subreddits=["all"],
            sources=["reddit"],
        ),
        topics=[_queue_topic(rank, name) for rank, name in enumerate(names, start=1)],
    )


def _run_scoped_discover(save_dir, config=None, names=("Gemma 4 chat templates",)):
    import datetime as _datetime

    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(
        ["--discover", "AI agents", "--save-dir", str(save_dir), "--save-suffix", os.urandom(4).hex()]
    )
    report = _queue_report(list(names))
    # Stamp a real, distinct run identity per mocked run: generated_at is
    # runtime-stamped in reality, and a fixed fixture timestamp would make two
    # "separate" runs share a run_ref and trip the retry idempotency guard.
    report.generated_at = _datetime.datetime.now(_datetime.timezone.utc).isoformat()
    with mock.patch.object(pipeline, "run_discover", return_value=report):
        return cli._run_discover(args, dict(config or {}))


def test_discovery_run_records_surfacings_in_scoped_db_only(tmp_path, monkeypatch, capsys):
    import store

    monkeypatch.setattr(store, "DB_PATH", tmp_path / "global" / "research.db")
    save_dir = tmp_path / "client"
    save_dir.mkdir()

    assert _run_scoped_discover(save_dir) == 0
    first = capsys.readouterr().out
    assert "**Pipeline:**" not in first  # nothing prior to annotate from

    scoped_db = save_dir / "research.db"
    assert scoped_db.is_file()
    assert not (tmp_path / "global" / "research.db").exists()

    import sqlite3
    conn = sqlite3.connect(scoped_db)
    rows = conn.execute(
        "SELECT name, surface_count, status FROM discovery_topics"
    ).fetchall()
    conn.close()
    assert rows == [("Gemma 4 chat templates", 1, "surfaced")]


def test_second_discovery_run_annotates_from_prior_state_then_records(tmp_path, capsys):
    save_dir = tmp_path / "client"
    save_dir.mkdir()

    assert _run_scoped_discover(save_dir) == 0
    capsys.readouterr()
    assert _run_scoped_discover(save_dir) == 0
    second = capsys.readouterr().out

    # Annotation reflects the state BEFORE this run's own surfacing was
    # recorded: one prior surfacing means this appearance is the 2nd.
    assert "**Pipeline:** surfaced 2nd time" in second

    import sqlite3
    conn = sqlite3.connect(save_dir / "research.db")
    count = conn.execute(
        "SELECT surface_count FROM discovery_topics WHERE name = ?",
        ("Gemma 4 chat templates",),
    ).fetchone()[0]
    conn.close()
    assert count == 2


def test_covered_topic_resurfacing_renders_marked_covered(tmp_path, capsys):
    import store

    save_dir = tmp_path / "client"
    save_dir.mkdir()

    assert _run_scoped_discover(save_dir) == 0
    with store.scoped_db(save_dir / "research.db"):
        assert store.mark_discovery_covered(
            "Gemma 4 chat templates", as_of="2026-07-20"
        ) is not None
    capsys.readouterr()

    assert _run_scoped_discover(save_dir) == 0
    rendered = capsys.readouterr().out
    assert "marked covered" in rendered
    assert "**Pipeline:** surfaced 2nd time, marked covered" in rendered


def test_queue_opt_out_via_process_env_seam(tmp_path, monkeypatch, capsys):
    from lib import env

    monkeypatch.setenv("LAST30DAYS_DISCOVERY_QUEUE", "off")
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        config = env.get_config()
    assert config["LAST30DAYS_DISCOVERY_QUEUE"] == "off"

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    assert _run_scoped_discover(save_dir, config=config) == 0
    assert "**Pipeline:**" not in capsys.readouterr().out
    assert not (save_dir / "research.db").exists()


def test_queue_opt_out_via_env_file_seam(tmp_path, monkeypatch, capsys):
    from lib import env

    monkeypatch.delenv("LAST30DAYS_DISCOVERY_QUEUE", raising=False)
    env_file = tmp_path / "config.env"
    env_file.write_text("LAST30DAYS_DISCOVERY_QUEUE=off\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", env_file)
    monkeypatch.chdir(tmp_path)
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        config = env.get_config()
    assert config["LAST30DAYS_DISCOVERY_QUEUE"] == "off"

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    assert _run_scoped_discover(save_dir, config=config) == 0
    assert not (save_dir / "research.db").exists()


def test_discovery_queue_failure_never_crashes_a_finished_run(tmp_path, monkeypatch, capsys):
    """P0: a broken research.db (locked, read-only, corrupt) must not destroy
    a finished multi-minute pipeline run - the brief still renders (exit 0)
    with a stderr warning, and queue fields keep their defaults."""
    import sqlite3

    import store

    def _locked(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "record_discovery_surfacing", _locked)
    save_dir = tmp_path / "client"
    save_dir.mkdir()

    assert _run_scoped_discover(save_dir) == 0
    captured = capsys.readouterr()
    assert "## 1. Gemma 4 chat templates" in captured.out
    assert "**Pipeline:**" not in captured.out
    assert "[last30days] Warning:" in captured.err
    assert "database is locked" in captured.err


def test_sibling_topics_in_same_run_do_not_cross_annotate(tmp_path, capsys):
    """Annotations describe the queue state BEFORE this run: two same-anchor
    siblings surfaced by ONE run must not fuzzy-match each other's rows and
    render a false 'surfaced 2nd time' on first-ever topics."""
    import sqlite3

    save_dir = tmp_path / "client"
    save_dir.mkdir()

    assert _run_scoped_discover(
        save_dir, names=("Gemma 4 chat templates", "Gemma 4 enterprise")
    ) == 0
    rendered = capsys.readouterr().out
    assert "## 1. Gemma 4 chat templates" in rendered
    assert "## 2. Gemma 4 enterprise" in rendered
    assert "**Pipeline:**" not in rendered

    conn = sqlite3.connect(save_dir / "research.db")
    rows = conn.execute(
        "SELECT name, surface_count FROM discovery_topics ORDER BY name"
    ).fetchall()
    conn.close()
    assert rows == [("Gemma 4 chat templates", 1), ("Gemma 4 enterprise", 1)]


def test_discovery_mock_run_writes_no_research_db(tmp_path):
    """--mock stays 100% side-effect-free: no queue writes, no research.db."""
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "--discover",
            "AI agents",
            "--mock",
            "--save-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "research.db").exists()


def test_queue_list_shows_uncovered_only_by_default(tmp_path, monkeypatch, capsys):
    import store

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    with store.scoped_db(save_dir / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="r1", as_of="2026-07-19",
        )
        store.record_discovery_surfacing(
            "OpenAI Agent SDK", domain="AI agents", run_ref="r1", as_of="2026-07-20",
        )
        store.mark_discovery_covered("OpenAI Agent SDK", as_of="2026-07-20")

    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys, "argv", ["last30days.py", "queue", "list", "--save-dir", str(save_dir)]
    )
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "Gemma 4 chat templates" in out
    assert "OpenAI Agent SDK" not in out
    for column in ("name", "domain", "surface_count", "last_surfaced", "status"):
        assert column in out


def test_queue_list_empty_db_reports_no_recorded_runs(tmp_path, monkeypatch, capsys):
    """A db that exists but has zero discovery rows (e.g. created via --store
    by a topic run) must not claim every topic is covered."""
    import store

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    store.init_db(save_dir / "research.db")

    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys, "argv", ["last30days.py", "queue", "list", "--save-dir", str(save_dir)]
    )
    assert cli.main() == 0
    out = capsys.readouterr().out
    assert "no discovery run has recorded topics yet" in out
    assert "marked covered" not in out


def test_queue_cover_marks_topic_covered(tmp_path, monkeypatch, capsys):
    import sqlite3

    import store

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    with store.scoped_db(save_dir / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="r1", as_of="2026-07-19",
        )

    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "queue", "cover", "Gemma 4 chat templates", "--save-dir", str(save_dir)],
    )
    assert cli.main() == 0

    conn = sqlite3.connect(save_dir / "research.db")
    status, covered_at = conn.execute(
        "SELECT status, covered_at FROM discovery_topics WHERE name = ?",
        ("Gemma 4 chat templates",),
    ).fetchone()
    conn.close()
    assert status == "covered"
    assert covered_at


def test_queue_cover_unknown_name_exits_2_with_stderr(tmp_path, monkeypatch, capsys):
    import store

    save_dir = tmp_path / "client"
    save_dir.mkdir()
    with store.scoped_db(save_dir / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="r1", as_of="2026-07-19",
        )

    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["last30days.py", "queue", "cover", "No Such Topic", "--save-dir", str(save_dir)],
    )
    assert cli.main() == 2
    err = capsys.readouterr().err
    assert "No Such Topic" in err


def test_queue_cover_cli_unknown_name_subprocess_exit_code(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "skills/last30days/scripts/last30days.py",
            "queue",
            "cover",
            "No Such Topic",
            "--save-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "No Such Topic" in result.stderr


def test_discovery_exits_when_configured_sources_have_no_discovery_feed(monkeypatch, capsys):
    """A configured source boundary must hold: never silently widen a sweep
    to feeds the user filtered out."""
    monkeypatch.setattr(
        cli.env, "get_config", lambda **_kwargs: {"LAST30DAYS_DEFAULT_SEARCH": "youtube"}
    )
    monkeypatch.setattr(sys, "argv", ["last30days.py", "--discover", "AI agents", "--mock"])
    with mock.patch.object(pipeline, "run_discover") as run:
        assert cli.main() == 2

    run.assert_not_called()
    err = capsys.readouterr().err
    assert "no discovery-capable sources" in err
    assert "reddit" in err


# --- U2: three-command protocol CLI surface (flags, scoping, dispatch) ---


def _run_protocol_cli(argv: list[str], env_overrides: dict[str, str] | None = None):
    """Run the real CLI entry point; env overrides layer onto the test env."""
    return subprocess.run(
        [sys.executable, "skills/last30days/scripts/last30days.py", *argv],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env_overrides or {})},
    )


@pytest.mark.parametrize(
    ("argv", "fragment"),
    [
        # Orphans: protocol flags without --discover reject loudly (never a
        # silent no-op into a normal research run - same rule as
        # --discover-shallow).
        (
            ["AI agents", "--nominate-only", "--mock"],
            "--nominate-only only applies to --discover runs",
        ),
        (
            ["AI agents", "--judgments", "judgments.json", "--mock"],
            "--judgments only applies to --discover runs",
        ),
        (
            ["AI agents", "--finalize", "--mock"],
            "--finalize only applies to --discover runs",
        ),
        (
            ["AI agents", "--angles", "angles.json", "--mock"],
            "--angles only applies to --discover --finalize runs",
        ),
        # --angles is a --finalize modifier even when --discover is present.
        (
            ["--discover", "AI agents", "--angles", "angles.json", "--mock"],
            "--angles only applies to --discover --finalize runs",
        ),
        # The three legs are mutually exclusive: one leg per invocation.
        (
            ["--discover", "AI agents", "--nominate-only", "--judgments", "j.json"],
            "--nominate-only and --judgments are mutually exclusive",
        ),
        (
            ["--discover", "AI agents", "--nominate-only", "--finalize"],
            "--nominate-only and --finalize are mutually exclusive",
        ),
        (
            ["--discover", "AI agents", "--judgments", "j.json", "--finalize"],
            "--judgments and --finalize are mutually exclusive",
        ),
    ],
)
def test_discovery_cli_protocol_flag_combinations_exit_2(argv, fragment):
    """Every orphan/mutual-exclusion combination names the offending flags."""
    result = _run_protocol_cli(argv)
    assert result.returncode == 2, result.stderr
    assert fragment in result.stderr


@pytest.mark.parametrize(
    "leg_argv",
    [
        ["--nominate-only"],
        ["--judgments", "judgments.json"],
        ["--finalize"],
    ],
)
def test_discovery_cli_mock_protocol_leg_requires_save_dir(leg_argv):
    """--mock protocol legs without --save-dir would write handoff state into
    the real config dir; reject before any leg runs. LAST30DAYS_MEMORY_DIR is
    pinned empty so a dev machine's save-dir fallback can't mask the check."""
    result = _run_protocol_cli(
        ["--discover", "AI agents", "--mock", *leg_argv],
        env_overrides={"LAST30DAYS_MEMORY_DIR": ""},
    )
    assert result.returncode == 2, result.stderr
    assert "mock protocol legs require --save-dir to stay side-effect-free" in result.stderr


def test_discovery_cli_finalize_flag_reaches_leg_3(tmp_path):
    """With --save-dir, --finalize routes to the real leg 3 (formerly the U5
    stub): an empty state dir is a contract failure naming the pending report
    and the resume-leg remedy - proof the dispatch reached the finalize body."""
    result = _run_protocol_cli(
        ["--discover", "AI agents", "--mock", "--save-dir", str(tmp_path), "--finalize"],
    )
    assert result.returncode == 2, result.stderr
    assert "No pending discovery report found" in result.stderr
    assert str(tmp_path / discovery_handoff.PENDING_REPORT_FILENAME) in result.stderr
    assert "--discover --judgments" in result.stderr


# --- U3 leg 1: --discover --nominate-only (sweep, bundle, digest) -------------


def _run_nominate_only(save_dir, *extra_argv):
    return _run_protocol_cli(
        [
            "--discover", "AI agents", "--mock",
            "--save-dir", str(save_dir), "--nominate-only", *extra_argv,
        ],
        # Pin the default-search seam empty so a dev machine's configured
        # boundary cannot leak into the bundle-context assertions.
        env_overrides={"LAST30DAYS_DEFAULT_SEARCH": ""},
    )


def test_discovery_cli_nominate_only_writes_bundle_and_digest(tmp_path):
    """Leg 1 exits 0, writes the nominations bundle under the save dir, and
    prints the host digest naming the bundle path with one line per
    nomination - no judging, enrichment, floor, or queue on this leg."""
    result = _run_nominate_only(tmp_path)
    assert result.returncode == 0, result.stderr
    bundle_path = tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME
    assert bundle_path.is_file()
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert payload["kind"] == schema.DISCOVERY_NOMINATIONS_KIND
    assert payload["schema_version"] == schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION
    assert payload["domain"] == "AI agents"
    assert payload["tier"] == "deep"
    # Leg-1 invocation context rides along for leg 2.
    assert payload["context"]["lookback_days"] == 30
    assert payload["context"]["requested_sources"] is None
    assert payload["context"]["enrichment_source_boundary"] is None
    # Momentum window matches the sweep dates (computed the same day).
    assert (payload["from_date"], payload["to_date"]) == dates.get_date_range(30)
    assert payload["nominations"]
    for row in payload["nominations"]:
        # Heuristic fallbacks: no provider ran, so the nomination's own
        # name/junk ARE the topic_shape heuristics.
        assert row["heuristic_name"] == row["nomination"]["name"]
        assert row["heuristic_junk"] == row["nomination"]["junk_shape"]
        assert row["sources"] == sorted({
            item["source"] for item in row["nomination"]["items"]
        })
    # Digest on stdout: names the bundle file, instructs reading it before
    # judging, and carries exactly one line per nomination id.
    assert str(bundle_path) in result.stdout
    assert "before judging" in result.stdout
    for row in payload["nominations"]:
        matching = [
            line for line in result.stdout.splitlines()
            if line.startswith(f"{row['id']} | ")
        ]
        assert len(matching) == 1
    # No queue writes on leg 1.
    assert not (tmp_path / "research.db").exists()


def test_discovery_cli_nominate_only_source_boundary_rides_into_bundle(tmp_path):
    result = _run_nominate_only(tmp_path, "--search", "reddit")
    assert result.returncode == 0, result.stderr
    payload = json.loads(
        (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert payload["context"]["requested_sources"] == ["reddit"]
    assert payload["context"]["enrichment_source_boundary"] == ["reddit"]
    for row in payload["nominations"]:
        assert row["sources"] == ["reddit"]


def test_discovery_cli_nominate_only_mock_is_deterministic(tmp_path):
    """Two mock leg-1 runs agree on every nomination row and digest line
    (only bundle_id/generated_at/path may differ)."""
    def run_leg(save_dir):
        save_dir.mkdir()
        result = _run_nominate_only(save_dir)
        assert result.returncode == 0, result.stderr
        payload = json.loads(
            (save_dir / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        return payload, result.stdout

    first_payload, first_out = run_leg(tmp_path / "a")
    second_payload, second_out = run_leg(tmp_path / "b")
    assert first_payload["nominations"] == second_payload["nominations"]
    assert (first_payload["from_date"], first_payload["to_date"]) == (
        second_payload["from_date"], second_payload["to_date"],
    )
    ids = [row["id"] for row in first_payload["nominations"]]

    def digest_lines(out: str) -> list[str]:
        return [
            line for line in out.splitlines()
            if any(line.startswith(f"{row_id} | ") for row_id in ids)
        ]

    assert digest_lines(first_out) == digest_lines(second_out)


def test_discovery_cli_nominate_only_shallow_marks_tier(tmp_path):
    result = _run_nominate_only(tmp_path, "--discover-shallow")
    assert result.returncode == 0, result.stderr
    payload = json.loads(
        (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert payload["tier"] == "shallow"


def test_discovery_cli_nominate_only_zero_nominations_nothing_solid(tmp_path, capsys):
    """An empty sweep short-circuits to the existing nothing-solid brief:
    exit 0, NO bundle file, nothing for later legs."""
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(
        ["--discover", "AI agents", "--save-dir", str(tmp_path), "--nominate-only"]
    )

    def empty_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        return [], None

    with mock.patch.object(
        pipeline, "available_sources", return_value=["hackernews"],
    ), mock.patch.object(
        pipeline, "_fetch_discovery_source", side_effect=empty_fetch,
    ), mock.patch.object(pipeline, "enrich_nominations") as enrich:
        assert cli._run_discover_protocol_leg(args, {}) == 0

    enrich.assert_not_called()
    out = capsys.readouterr().out
    assert "Nothing solid this window." in out
    assert not (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).exists()
    assert not (tmp_path / "research.db").exists()


def test_discovery_cli_nominate_only_skips_enrichment_providers_and_queue(tmp_path):
    """Spies on the leg-1 boundary: enrichment, provider resolution, and the
    queue hook are never touched, and no research.db appears."""
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(
        ["--discover", "AI agents", "--mock", "--save-dir", str(tmp_path), "--nominate-only"]
    )
    with mock.patch.object(pipeline, "enrich_nominations") as enrich, \
         mock.patch.object(pipeline.providers, "resolve_runtime") as resolve, \
         mock.patch.object(cli, "_annotate_and_record_discovery_queue") as queue_hook:
        assert cli._run_discover_protocol_leg(args, {}) == 0
    enrich.assert_not_called()
    resolve.assert_not_called()
    queue_hook.assert_not_called()
    assert (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).is_file()
    assert not (tmp_path / "research.db").exists()


def test_discover_handoff_state_dir_scopes_to_save_dir_then_config(tmp_path, monkeypatch):
    """One resolver for all three legs: save dir when given, else config dir
    (the same base the report cache uses)."""
    save_dir = tmp_path / "client"
    resolved = cli._discover_handoff_state_dir(argparse.Namespace(save_dir=str(save_dir)))
    assert resolved == save_dir.resolve()

    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path / "config")
    resolved = cli._discover_handoff_state_dir(argparse.Namespace(save_dir=None))
    assert resolved == tmp_path / "config"


def test_discovery_protocol_dispatch_maps_contract_error_to_exit_2(monkeypatch, capsys):
    """HandoffContractError raised inside any leg body maps to stderr + exit 2
    at the dispatch layer, so U3-U5 leg bodies can raise it freely."""

    def _stale_bundle(_args, _config):
        raise discovery_handoff.HandoffContractError(
            "Nominations bundle is stale; run a fresh re-sweep."
        )

    monkeypatch.setattr(cli, "_run_discover_nominate", _stale_bundle)
    args = argparse.Namespace(nominate_only=True, judgments=None, finalize=False)
    assert cli._run_discover_protocol_leg(args, {}) == 2
    err = capsys.readouterr().err
    assert "Nominations bundle is stale; run a fresh re-sweep." in err


# --- U4 leg 2: --discover --judgments (resume, enrich, pending report) --------


def _leg1_bundle_payload(save_dir) -> dict:
    """Run leg 1 in-process against the save dir and return the bundle JSON."""
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args(
        ["--discover", "AI agents", "--mock", "--save-dir", str(save_dir),
         "--nominate-only"]
    )
    assert cli._run_discover_protocol_leg(args, {}) == 0
    return json.loads(
        (Path(save_dir) / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )


def _run_leg2(save_dir, judgments_payload, config=None) -> int:
    judgments_path = Path(save_dir) / "judgments.json"
    judgments_path.write_text(json.dumps(judgments_payload), encoding="utf-8")
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(save_dir),
        "--judgments", str(judgments_path),
    ])
    return cli._run_discover_protocol_leg(args, dict(config or {}))


def _rich_enrichment_report(topic: str) -> schema.Report:
    """A per-topic fake enrichment corpus with topic-unique URLs so distinct
    topics never trip the same-story fold."""
    import datetime as _datetime

    slug = "".join(ch if ch.isalnum() else "-" for ch in topic.lower())
    published = (_datetime.date.today() - _datetime.timedelta(days=1)).isoformat()
    items = {
        "reddit": [schema.SourceItem(
            item_id=f"r-{slug}", source="reddit", title=topic, body=topic,
            url=f"https://reddit.com/r/x/{slug}", published_at=published,
            engagement={"score": 800, "num_comments": 300}, snippet=topic,
        )],
        "hackernews": [schema.SourceItem(
            item_id=f"h-{slug}", source="hackernews", title=topic, body=topic,
            url=f"https://example.com/{slug}", published_at=published,
            engagement={"points": 400, "comments": 150}, snippet=topic,
        )],
    }
    return schema.Report(
        topic=topic,
        range_from="2026-06-10", range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="none",
            planner_model="deterministic",
            rerank_model="deterministic",
        ),
        query_plan=schema.QueryPlan(
            intent="factual", freshness_mode="balanced_recent",
            cluster_mode="none", raw_topic=topic, subqueries=[],
            source_weights={},
        ),
        clusters=[], ranked_candidates=[],
        items_by_source=items, errors_by_source={},
    )


def test_discovery_cli_resume_pending_report_round_trip(tmp_path, capsys):
    """Scenario 8: leg 2 persists ONE pending report - bundle_id binding, a
    fresh generated_at (the leg-3 TTL clock), the queue's run_ref format, the
    full report with host names and contiguous ranks, and angle inputs keyed
    by surviving nomination ids - and prints the angle inputs plus the
    finalize instructions. No queue writes, no artifact saves."""
    from lib import env as lib_env

    bundle_payload = _leg1_bundle_payload(tmp_path)
    capsys.readouterr()
    ids = [row["id"] for row in bundle_payload["nominations"]]
    assert "n1" in ids
    judgments = {
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [
            {"id": "n1", "name": "Renamed Topic One", "junk": False,
             "worthiness": 90},
        ],
    }

    def fake_run(*, topic, **_kwargs):
        return _rich_enrichment_report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        assert _run_leg2(tmp_path, judgments) == 0
    out = capsys.readouterr().out

    pending_path = tmp_path / discovery_handoff.PENDING_REPORT_FILENAME
    assert pending_path.is_file()
    payload = json.loads(pending_path.read_text(encoding="utf-8"))
    assert payload["bundle_id"] == bundle_payload["bundle_id"]
    # Fresh TTL clock: generated_at is the resume run's, not the sweep's.
    assert lib_env.is_timestamp_fresh(payload["generated_at"], 3600)
    assert payload["generated_at"] != bundle_payload["generated_at"]
    assert payload["run_ref"] == f"discover:AI agents:{payload['generated_at']}"

    report_dict = payload["report"]
    topic_names = [topic["name"] for topic in report_dict["topics"]]
    assert "Renamed Topic One" in topic_names
    assert [topic["rank"] for topic in report_dict["topics"]] == list(
        range(1, len(topic_names) + 1)
    )
    assert all(topic["evidence_urls"] for topic in report_dict["topics"])

    angle_inputs = payload["angle_inputs"]
    assert angle_inputs
    assert set(angle_inputs) <= set(ids)
    assert angle_inputs["n1"]["name"] == "Renamed Topic One"
    for entry in angle_inputs.values():
        assert set(entry) == {"name", "titles", "top_comment", "engagement"}

    # stdout: angle inputs + instruction block with the bundle_id echo.
    assert "Renamed Topic One" in out
    assert bundle_payload["bundle_id"] in out
    assert "--discover --finalize" in out
    # No queue writes and no artifact saves on this leg.
    assert not (tmp_path / "research.db").exists()
    assert not list(tmp_path.glob("*discover-raw*"))


def test_discovery_cli_resume_zero_survivors_renders_nothing_solid(tmp_path, capsys):
    """Scenario 9: every nomination host-junked - leg 2 renders the honest
    nothing-solid brief itself, exits 0, and leaves NO pending file (there is
    nothing for leg 3 to finalize)."""
    bundle_payload = _leg1_bundle_payload(tmp_path)
    capsys.readouterr()
    judgments = {
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [
            {"id": row["id"], "junk": True}
            for row in bundle_payload["nominations"]
        ],
    }
    with mock.patch.object(pipeline, "enrich_nominations") as enrich:
        assert _run_leg2(tmp_path, judgments) == 0
    enrich.assert_not_called()
    out = capsys.readouterr().out
    assert "Nothing solid this window." in out
    assert not (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).exists()
    assert not (tmp_path / "research.db").exists()


def test_discovery_cli_resume_never_resolves_providers_or_queue(tmp_path, capsys):
    """Leg 2 has no LLM pass (the host IS the judge) and no queue write (the
    queue belongs to leg 3): provider resolution and the queue hook are never
    touched on the resume leg."""
    bundle_payload = _leg1_bundle_payload(tmp_path)
    capsys.readouterr()
    judgments = {"bundle_id": bundle_payload["bundle_id"], "judgments": []}

    def fake_run(*, topic, **_kwargs):
        return _rich_enrichment_report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run), \
         mock.patch.object(pipeline.providers, "resolve_runtime") as resolve, \
         mock.patch.object(cli, "_annotate_and_record_discovery_queue") as queue_hook:
        assert _run_leg2(tmp_path, judgments) == 0

    resolve.assert_not_called()
    queue_hook.assert_not_called()


def test_discovery_cli_resume_without_bundle_exits_2(tmp_path):
    """A resume against an empty state dir is a contract failure: exit 2 with
    the searched locations and the re-sweep remedy on stderr."""
    judgments_path = tmp_path / "judgments.json"
    judgments_path.write_text(
        json.dumps({"bundle_id": "deadbeef", "judgments": []}), encoding="utf-8"
    )
    result = _run_protocol_cli([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--judgments", str(judgments_path),
    ])
    assert result.returncode == 2, result.stderr
    assert "No discovery nominations bundle found" in result.stderr
    assert "--discover --nominate-only" in result.stderr


def test_discovery_cli_resume_full_mock_offline_is_deterministic(tmp_path):
    """Scenario 10: a full mock leg 2 (mock judgments against a mock leg-1
    bundle, --save-dir scoped) runs offline end to end - exit 0, a pending
    report bound to the bundle, deterministic angle inputs across two resumes,
    and zero queue writes."""
    leg1 = _run_nominate_only(tmp_path)
    assert leg1.returncode == 0, leg1.stderr
    bundle_payload = json.loads(
        (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    rows = bundle_payload["nominations"]
    keep = [row["id"] for row in rows if not row["heuristic_junk"]][:2]
    assert keep, "mock sweep should nominate at least one non-junk topic"
    judgments_path = tmp_path / "judgments.json"
    judgments_path.write_text(json.dumps({
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [
            {"id": keep[0], "name": "Renamed Mock Topic", "junk": False,
             "worthiness": 90},
            *[
                {"id": row["id"], "junk": True}
                for row in rows if row["id"] not in keep
            ],
        ],
    }), encoding="utf-8")

    def run_leg2():
        return _run_protocol_cli(
            [
                "--discover", "AI agents", "--mock",
                "--save-dir", str(tmp_path),
                "--judgments", str(judgments_path),
            ],
            env_overrides={"LAST30DAYS_DEFAULT_SEARCH": ""},
        )

    first = run_leg2()
    assert first.returncode == 0, first.stderr
    pending_path = tmp_path / discovery_handoff.PENDING_REPORT_FILENAME
    first_payload = json.loads(pending_path.read_text(encoding="utf-8"))
    second = run_leg2()
    assert second.returncode == 0, second.stderr
    second_payload = json.loads(pending_path.read_text(encoding="utf-8"))

    assert first_payload["bundle_id"] == bundle_payload["bundle_id"]
    assert set(first_payload["angle_inputs"]) <= set(keep)
    # Deterministic: two mock resumes agree on every angle input.
    assert first_payload["angle_inputs"] == second_payload["angle_inputs"]
    assert "Renamed Mock Topic" in first.stdout
    assert bundle_payload["bundle_id"] in first.stdout
    assert "--discover --finalize" in first.stdout
    assert not (tmp_path / "research.db").exists()


# --- U5 leg 3: --discover --finalize (angles, render, artifacts, queue) --------


def _fresh_generated_at() -> str:
    import datetime as _datetime

    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _write_pending_report(
    save_dir,
    names=("Gemma 4 chat templates",),
    bundle_id="cafe1234cafe1234",
) -> dict:
    """Write a synthetic (schema-true) leg-2 pending report into save_dir and
    return its payload. Mirrors the exact U4 writer shape: full schema round
    trip of the report plus angle_inputs keyed by surviving nomination id."""
    generated_at = _fresh_generated_at()
    report = _queue_report(list(names))
    report.generated_at = generated_at
    payload = {
        "kind": schema.DISCOVERY_PENDING_KIND,
        "schema_version": schema.DISCOVERY_PENDING_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "generated_at": generated_at,
        "run_ref": f"discover:{report.domain or 'trending'}:{generated_at}",
        "report": schema.to_dict(report),
        "angle_inputs": {
            f"n{position}": {
                "name": name,
                "titles": f"Listing title about {name}",
                "top_comment": "",
                "engagement": "120 native interactions across reddit",
            }
            for position, name in enumerate(names, start=1)
        },
    }
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / discovery_handoff.PENDING_REPORT_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return payload


def _write_angles_file(save_dir, bundle_id, rows) -> Path:
    path = Path(save_dir) / "angles.json"
    path.write_text(
        json.dumps({"bundle_id": bundle_id, "angles": rows}), encoding="utf-8"
    )
    return path


def _run_leg3(save_dir, config=None, *extra_argv) -> int:
    """Run leg 3 in-process, non-mock (finalize is offline by design)."""
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--save-dir", str(save_dir),
        "--save-suffix", os.urandom(4).hex(), "--finalize", *extra_argv,
    ])
    return cli._run_discover_protocol_leg(args, dict(config or {}))


def test_discovery_report_round_trips_through_schema_dicts():
    """to_dict -> JSON -> discovery_report_from_dict is lossless: the exact
    round trip leg 3 performs on the pending report."""
    import dataclasses

    original = _queue_report(["Gemma 4 chat templates"])
    original.topics[0] = dataclasses.replace(
        original.topics[0],
        top_comment='"Sharp take" - u/dev (1,200 votes)',
        corroboration_count=2,
        evidence_urls=["https://reddit.com/r/x/1"],
    )
    payload = json.loads(json.dumps(schema.to_dict(original)))
    assert schema.discovery_report_from_dict(payload) == original


def test_discovery_cli_finalize_applies_host_angles_and_records_queue(tmp_path, capsys):
    """Scenario: finalize with an angles file renders host angle lines
    verbatim, saves the discovery artifact, and records the queue under the
    LEG-2 run identity (the pending report's run_ref)."""
    import sqlite3 as _sqlite3

    pending = _write_pending_report(tmp_path)
    angles_path = _write_angles_file(tmp_path, pending["bundle_id"], [
        {"id": "n1",
         "podcast": "Is Gemma 4 chat templating a lock-in play?",
         "x_article": "Five Gemma 4 template changes worth writing about."},
    ])

    assert _run_leg3(tmp_path, None, "--angles", str(angles_path)) == 0
    out = capsys.readouterr().out
    assert "## 1. Gemma 4 chat templates" in out
    assert (
        "**Podcast angle:** Is Gemma 4 chat templating a lock-in play?" in out
    )
    assert (
        "**X article angle:** Five Gemma 4 template changes worth writing about."
        in out
    )
    # First-ever topic: nothing prior to annotate from.
    assert "**Pipeline:**" not in out
    # Artifact saved via the existing O_EXCL discovery path.
    assert list(tmp_path.glob("*discover-raw*"))
    # Queue row recorded under the pending report's run_ref.
    conn = _sqlite3.connect(tmp_path / "research.db")
    conn.row_factory = _sqlite3.Row
    row = dict(conn.execute("SELECT * FROM discovery_topics").fetchone())
    conn.close()
    assert row["name"] == "Gemma 4 chat templates"
    assert row["surface_count"] == 1
    assert row["last_run_ref"] == pending["run_ref"]
    # Pending file left in place: idempotent retries are a design requirement.
    assert (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).is_file()


def test_discovery_cli_finalize_without_angles_renders_angle_less_brief(tmp_path, capsys):
    """Omitting --angles is legal: the brief ships without angle lines and the
    queue still records the surfacing."""
    _write_pending_report(tmp_path)

    assert _run_leg3(tmp_path) == 0
    out = capsys.readouterr().out
    assert "## 1. Gemma 4 chat templates" in out
    assert "**Podcast angle:**" not in out
    assert "**X article angle:**" not in out

    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(tmp_path / "research.db")
    rows = conn.execute(
        "SELECT name, surface_count FROM discovery_topics"
    ).fetchall()
    conn.close()
    assert rows == [("Gemma 4 chat templates", 1)]


def test_discovery_cli_double_finalize_same_run_ref_counts_once(tmp_path, capsys):
    """AE6: a finalize retry (same pending report, same run_ref) neither
    double-counts the surfacing nor annotates the retry as a resurfacing -
    the rendered brief is stable across retries."""
    import sqlite3 as _sqlite3

    _write_pending_report(tmp_path)

    assert _run_leg3(tmp_path) == 0
    first = capsys.readouterr().out
    assert _run_leg3(tmp_path) == 0
    second = capsys.readouterr().out

    assert "## 1. Gemma 4 chat templates" in second
    # The retry must not describe itself as a 2nd surfacing.
    assert "**Pipeline:**" not in second
    assert first == second

    conn = _sqlite3.connect(tmp_path / "research.db")
    row = conn.execute(
        "SELECT surface_count, covered_at FROM discovery_topics"
    ).fetchone()
    conn.close()
    assert row == (1, None)


def test_discovery_cli_finalize_retry_with_prior_history_renders_identically(tmp_path, capsys):
    """F3: a finalize retry over a topic WITH pre-run history reconstructs
    the pre-run queue state (surface_count minus this run's own recording)
    instead of nulling the prior - the retry renders the same 'surfaced Nth
    time' line as the first attempt."""
    import sqlite3 as _sqlite3

    import store

    with store.scoped_db(tmp_path / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="run-old",
            as_of="2026-07-13",
        )
    _write_pending_report(tmp_path)

    assert _run_leg3(tmp_path) == 0
    first = capsys.readouterr().out
    assert "**Pipeline:** surfaced 2nd time" in first

    assert _run_leg3(tmp_path) == 0
    second = capsys.readouterr().out
    assert "**Pipeline:** surfaced 2nd time" in second
    assert first == second

    # The retry never double-counted the surfacing.
    conn = _sqlite3.connect(tmp_path / "research.db")
    count = conn.execute(
        "SELECT surface_count FROM discovery_topics"
    ).fetchone()[0]
    conn.close()
    assert count == 2


def test_discovery_cli_finalize_retry_keeps_covered_history(tmp_path, capsys):
    """F3: the reconstructed pre-run state keeps the prior's covered mark
    (covered_at intact), so a retry still renders 'marked covered'."""
    import store

    with store.scoped_db(tmp_path / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="run-old",
            as_of="2026-07-13",
        )
        store.mark_discovery_covered("Gemma 4 chat templates", as_of="2026-07-14")
    _write_pending_report(tmp_path)

    assert _run_leg3(tmp_path) == 0
    first = capsys.readouterr().out
    assert "**Pipeline:** surfaced 2nd time, marked covered" in first

    assert _run_leg3(tmp_path) == 0
    second = capsys.readouterr().out
    assert "**Pipeline:** surfaced 2nd time, marked covered" in second
    assert first == second

    with store.scoped_db(tmp_path / "research.db"):
        row = store.match_discovery_topic("Gemma 4 chat templates")
    assert row is not None
    assert row["status"] == "covered"
    assert row["covered_at"] == "2026-07-14"


def test_discovery_cli_finalize_new_run_ref_still_increments(tmp_path, capsys):
    """Scenario 8: the guard is per-run. A LATER protocol round (fresh pending
    report, fresh run_ref) increments and annotates normally."""
    import sqlite3 as _sqlite3

    _write_pending_report(tmp_path)
    assert _run_leg3(tmp_path) == 0
    capsys.readouterr()

    # A new leg-2 round over the same story: new generated_at -> new run_ref.
    _write_pending_report(tmp_path)
    assert _run_leg3(tmp_path) == 0
    out = capsys.readouterr().out
    assert "**Pipeline:** surfaced 2nd time" in out

    conn = _sqlite3.connect(tmp_path / "research.db")
    count = conn.execute(
        "SELECT surface_count FROM discovery_topics"
    ).fetchone()[0]
    conn.close()
    assert count == 2


def test_discovery_cli_finalize_queue_failure_degrades_to_warning(tmp_path, monkeypatch, capsys):
    """The finalize queue call sits behind the same guarded hook as the
    one-shot: a broken research.db degrades to a stderr warning and the brief
    still prints (exit 0)."""
    import sqlite3 as _sqlite3

    import store

    def _locked(*_args, **_kwargs):
        raise _sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "record_discovery_surfacing", _locked)
    _write_pending_report(tmp_path)

    assert _run_leg3(tmp_path) == 0
    captured = capsys.readouterr()
    assert "## 1. Gemma 4 chat templates" in captured.out
    assert "**Pipeline:**" not in captured.out
    assert "[last30days] Warning:" in captured.err
    assert "database is locked" in captured.err


def test_discovery_cli_finalize_covered_inheritance_on_rename_drift(tmp_path, capsys):
    """A host-authored name that fuzzy-matches a covered prior inherits the
    covered mark (rename-drift scenario) - same convention as the one-shot."""
    import store

    with store.scoped_db(tmp_path / "research.db"):
        store.record_discovery_surfacing(
            "Gemma 4 chat templates", domain="AI agents", run_ref="run-old",
            as_of="2026-07-13",
        )
        store.mark_discovery_covered("Gemma 4 chat templates", as_of="2026-07-14")

    _write_pending_report(tmp_path, names=("Gemma 4 template fixes",))
    assert _run_leg3(tmp_path) == 0
    out = capsys.readouterr().out
    assert "marked covered" in out

    with store.scoped_db(tmp_path / "research.db"):
        fresh = store.match_discovery_topic("Gemma 4 template fixes")
    assert fresh is not None
    assert fresh["status"] == "covered"
    assert fresh["covered_at"] == "2026-07-14"


def test_discovery_cli_finalize_is_offline(tmp_path, capsys):
    """Leg 3 is the cheap leg: no sweep, no enrichment, no provider
    resolution - only the pending report, the angles file, and the queue."""
    _write_pending_report(tmp_path)
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--save-dir", str(tmp_path), "--finalize",
    ])
    with mock.patch.object(pipeline, "run_discover") as sweep, \
         mock.patch.object(pipeline, "run") as research, \
         mock.patch.object(pipeline, "enrich_nominations") as enrich, \
         mock.patch.object(pipeline.providers, "resolve_runtime") as resolve:
        assert cli._run_discover_protocol_leg(args, {}) == 0
    sweep.assert_not_called()
    research.assert_not_called()
    enrich.assert_not_called()
    resolve.assert_not_called()


def test_discovery_cli_finalize_emit_json_carries_host_angles(tmp_path, capsys):
    """--emit json respects the same export contract as the one-shot; host
    angles ride in the discovery export fields."""
    pending = _write_pending_report(tmp_path)
    angles_path = _write_angles_file(tmp_path, pending["bundle_id"], [
        {"id": "n1", "podcast": "A hook worth exporting"},
    ])
    assert _run_leg3(
        tmp_path, None, "--angles", str(angles_path), "--emit", "json",
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kind"] == "discovery"
    assert payload["results"][0]["podcast_angle"] == "A hook worth exporting"
    assert payload["results"][0]["x_article_angle"] is None


def test_discovery_cli_finalize_rejects_html_like_the_one_shot(tmp_path):
    """The HTML guard is hoisted to the shared --discover dispatch: finalize
    rejects --emit=html before touching any handoff state, even when a valid
    pending report exists."""
    _write_pending_report(tmp_path)
    result = _run_protocol_cli([
        "--discover", "AI agents", "--save-dir", str(tmp_path), "--finalize",
        "--emit", "html",
    ])
    assert result.returncode == 2, result.stderr
    assert "does not support HTML publishing" in result.stderr


def test_discovery_cli_protocol_legs_reject_as_of_and_html(tmp_path):
    """F4: the one-shot's --as-of and HTML guards bind on EVERY protocol leg
    before dispatch - legs 1 and 2 must not sweep historical dates or accept
    an HTML pipeline discovery does not have."""
    nominate = _run_protocol_cli([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--nominate-only", "--as-of", "2026-06-01",
    ])
    assert nominate.returncode == 2, nominate.stderr
    assert "--as-of cannot be used with --discover" in nominate.stderr
    assert "current live listings" in nominate.stderr

    # The judgments file deliberately does not exist: the guard must fire
    # before any handoff file is read.
    judgments = _run_protocol_cli([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--judgments", str(tmp_path / "missing-judgments.json"), "--emit=html",
    ])
    assert judgments.returncode == 2, judgments.stderr
    assert "does not support HTML publishing" in judgments.stderr


def test_discovery_cli_finalize_stale_pending_exits_2(tmp_path, capsys):
    """TTL is measured from the PENDING report's generated_at (the leg-2
    write started a fresh window); a stale one names the resume remedy."""
    import datetime as _datetime

    stale = (
        _datetime.datetime.now(_datetime.timezone.utc)
        - _datetime.timedelta(
            seconds=discovery_handoff.DISCOVERY_HANDOFF_TTL_SECONDS + 60
        )
    ).isoformat()
    payload = _write_pending_report(tmp_path)
    pending_path = tmp_path / discovery_handoff.PENDING_REPORT_FILENAME
    payload["generated_at"] = stale
    pending_path.write_text(json.dumps(payload), encoding="utf-8")

    assert _run_leg3(tmp_path) == 2
    err = capsys.readouterr().err
    assert "stale" in err
    assert "--discover --judgments" in err


def test_discovery_cli_finalize_missing_pending_names_save_dir_only(tmp_path, monkeypatch, capsys):
    """With an explicit --save-dir the not-found message names ONLY the
    save-dir location (the single handoff store - no config-dir fallback)
    plus the resume-leg remedy."""
    config_dir = tmp_path / "config"
    monkeypatch.setattr(cli.env, "CONFIG_DIR", config_dir)
    save_dir = tmp_path / "client"
    assert _run_leg3(save_dir) == 2
    err = capsys.readouterr().err
    assert str(save_dir.resolve() / discovery_handoff.PENDING_REPORT_FILENAME) in err
    assert str(config_dir) not in err
    assert "--discover --judgments" in err
    assert "--discover --nominate-only" in err


def test_discovery_cli_finalize_angles_bundle_mismatch_exits_2(tmp_path, capsys):
    pending = _write_pending_report(tmp_path)
    angles_path = _write_angles_file(tmp_path, "deadbeefdeadbeef", [
        {"id": "n1", "podcast": "Bound to the wrong bundle"},
    ])
    assert _run_leg3(tmp_path, None, "--angles", str(angles_path)) == 2
    err = capsys.readouterr().err
    assert "deadbeefdeadbeef" in err
    assert pending["bundle_id"] in err


def test_discovery_cli_finalize_invalid_pending_json_exits_2(tmp_path, capsys):
    (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).write_text(
        "{not json", encoding="utf-8"
    )
    assert _run_leg3(tmp_path) == 2
    assert "JSON" in capsys.readouterr().err


def test_discovery_cli_finalize_malformed_pending_report_body_exits_2(tmp_path, capsys):
    """F7: a pending file whose top-level envelope validates but whose report
    body is structurally incomplete (missing required report keys) is a
    contract failure with the resume remedy - exit 2, never a traceback."""
    payload = _write_pending_report(tmp_path)
    payload["report"] = {"domain": "AI agents", "topics": []}  # no range/dates
    (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    assert _run_leg3(tmp_path) == 2
    err = capsys.readouterr().err
    assert "malformed" in err
    assert "--discover --judgments" in err


def test_discovery_cli_finalize_wrong_kind_or_version_exits_2(tmp_path, capsys):
    payload = _write_pending_report(tmp_path)
    pending_path = tmp_path / discovery_handoff.PENDING_REPORT_FILENAME

    payload["kind"] = "discovery-nominations"
    pending_path.write_text(json.dumps(payload), encoding="utf-8")
    assert _run_leg3(tmp_path) == 2
    assert "discovery-nominations" in capsys.readouterr().err

    payload["kind"] = schema.DISCOVERY_PENDING_KIND
    payload["schema_version"] = "99.0"
    pending_path.write_text(json.dumps(payload), encoding="utf-8")
    assert _run_leg3(tmp_path) == 2
    assert "99.0" in capsys.readouterr().err


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores directory permission bits",
)
def test_discovery_cli_resume_unwritable_pending_write_is_contract_error(tmp_path, capsys):
    """F9: the leg-2 pending-report write gets the same fail-closed treatment
    as the bundle write - a read-only state dir is HandoffContractError
    (exit 2 naming the path), never a raw OSError traceback."""
    bundle_payload = _leg1_bundle_payload(tmp_path)
    capsys.readouterr()
    judgments_path = tmp_path / "judgments.json"
    judgments_path.write_text(json.dumps({
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [],
    }), encoding="utf-8")
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--judgments", str(judgments_path),
    ])

    def fake_run(*, topic, **_kwargs):
        return _rich_enrichment_report(topic)

    tmp_path.chmod(0o500)
    try:
        with mock.patch.object(pipeline, "run", side_effect=fake_run):
            assert cli._run_discover_protocol_leg(args, {}) == 2
    finally:
        tmp_path.chmod(0o700)
    err = capsys.readouterr().err
    assert "Could not write pending discovery report" in err
    assert str(
        tmp_path.resolve() / discovery_handoff.PENDING_REPORT_FILENAME
    ) in err


# --- F11: cross-round pending invalidation ------------------------------------


def test_discovery_cli_nominate_clears_stale_pending_from_prior_round(tmp_path, capsys):
    """F11: a fresh leg-1 bundle starts a NEW protocol round - any pending
    report left by a prior round is deleted, so a bare --finalize afterwards
    is not-found (exit 2) instead of silently consuming stale state."""
    _write_pending_report(tmp_path)  # prior round's leg-2 output
    _leg1_bundle_payload(tmp_path)   # fresh round: leg 1 writes a new bundle
    capsys.readouterr()
    assert not (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).exists()

    assert _run_leg3(tmp_path) == 2
    assert "No pending discovery report found" in capsys.readouterr().err


def test_discovery_cli_resume_nothing_solid_clears_stale_pending(tmp_path, capsys):
    """F11: a zero-survivor leg 2 wrote no pending file THIS round, so a
    stale one from an earlier round must not survive it - bare --finalize
    afterwards exits 2 not-found."""
    bundle_payload = _leg1_bundle_payload(tmp_path)
    _write_pending_report(tmp_path)  # stale pending from an earlier round
    judgments = {
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [
            {"id": row["id"], "junk": True}
            for row in bundle_payload["nominations"]
        ],
    }
    with mock.patch.object(pipeline, "enrich_nominations") as enrich:
        assert _run_leg2(tmp_path, judgments) == 0
    enrich.assert_not_called()
    capsys.readouterr()
    assert not (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).exists()

    assert _run_leg3(tmp_path) == 2
    assert "No pending discovery report found" in capsys.readouterr().err


# --- F19: mock/real handoff provenance must match across legs ------------------


def test_discovery_cli_resume_rejects_mock_state_mismatch(tmp_path, capsys):
    """F19: a leg-2 run whose --mock flag disagrees with the bundle's stamped
    provenance exits 2 before any resume work; matching modes still pass."""
    bundle_payload = _leg1_bundle_payload(tmp_path)  # mock-born bundle
    capsys.readouterr()
    judgments_path = tmp_path / "judgments.json"
    judgments_path.write_text(json.dumps({
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [],
    }), encoding="utf-8")
    parser = cli.build_parser()
    bundle_path = tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME

    # Real leg 2 over the mock-born bundle: exit 2, the resume never runs.
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--save-dir", str(tmp_path),
        "--judgments", str(judgments_path),
    ])
    with mock.patch.object(pipeline, "run_discover_resume") as resume:
        assert cli._run_discover_protocol_leg(args, {}) == 2
    resume.assert_not_called()
    err = capsys.readouterr().err
    assert "mock-born state cannot be finalized by a real run" in err

    # Mock leg 2 over a real bundle: the inverse also exits 2.
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    payload["mock"] = False
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--judgments", str(judgments_path),
    ])
    with mock.patch.object(pipeline, "run_discover_resume") as resume:
        assert cli._run_discover_protocol_leg(args, {}) == 2
    resume.assert_not_called()
    err = capsys.readouterr().err
    assert "cannot be finalized by a --mock run" in err

    # Matching modes pass the parity gate and reach the resume engine.
    payload["mock"] = True
    bundle_path.write_text(json.dumps(payload), encoding="utf-8")
    with mock.patch.object(pipeline, "enrich_nominations", return_value=[]):
        assert cli._run_discover_protocol_leg(args, {}) == 0
    capsys.readouterr()


def test_discovery_cli_finalize_rejects_mock_state_mismatch(tmp_path, capsys):
    """F19: finalize enforces the same provenance parity against the pending
    report's stamped mock flag, in both directions."""
    _write_pending_report(tmp_path)  # real pending (no mock stamp = real)
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--finalize",
    ])
    assert cli._run_discover_protocol_leg(args, {}) == 2
    assert "cannot be finalized by a --mock run" in capsys.readouterr().err

    # Mock-born pending finalized by a real run: inverse direction.
    pending_path = tmp_path / discovery_handoff.PENDING_REPORT_FILENAME
    payload = json.loads(pending_path.read_text(encoding="utf-8"))
    payload["mock"] = True
    pending_path.write_text(json.dumps(payload), encoding="utf-8")
    assert _run_leg3(tmp_path) == 2
    assert (
        "mock-born state cannot be finalized by a real run"
        in capsys.readouterr().err
    )


# --- F1: degraded sweep state survives the protocol (strict exit on legs) ------


def _degraded_nominate_result() -> pipeline.DiscoverNominateResult:
    from_date, to_date = dates.get_date_range(30)
    nomination = pipeline.Nomination(
        name="Agent SDK Wars",
        seed_score=61.0,
        items=[_item(
            "hn1", "hackernews", "Agent SDK Wars heat up",
            engagement={"points": 900, "comments": 400},
        )],
        summary="Agent SDK Wars heat up across the listings.",
        junk_shape=False,
        worthiness=None,
    )
    return pipeline.DiscoverNominateResult(
        plan=schema.DiscoveryPlan(
            domain="AI agents", category=None, subreddits=[],
            sources=["hackernews"],
        ),
        from_date=from_date,
        to_date=to_date,
        source_status={
            "hackernews": schema.SourceOutcome(
                source="hackernews", state="ok", items_returned=1,
            ),
            "reddit": schema.SourceOutcome(
                source="reddit", state=schema.UNREACHABLE, detail="dns failure",
            ),
        },
        pool=[(nomination, "c-agent")],
    )


def test_discovery_protocol_strict_exit_and_degraded_state_survive_all_legs(tmp_path, capsys):
    """F1: the leg-1 sweep's degraded source outcomes ride the bundle into
    leg 2's report and pending file, and on into leg 3's brief - and under
    LAST30DAYS_STRICT_EXIT every leg renders normally but exits 3, mirroring
    the one-shot's strict-exit contract."""
    strict = {"LAST30DAYS_STRICT_EXIT": "1"}
    parser = cli.build_parser()

    # Leg 1: bundle written (render/output still happens), exit shifts to 3.
    args1, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--nominate-only",
    ])
    with mock.patch.object(
        pipeline, "run_discover_nominate",
        return_value=_degraded_nominate_result(),
    ):
        assert cli._run_discover_protocol_leg(args1, strict) == 3
    captured = capsys.readouterr()
    assert "strict-exit: degraded sources: reddit" in captured.err
    bundle_payload = json.loads(
        (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert bundle_payload["source_status"]["reddit"]["state"] == schema.UNREACHABLE

    # Leg 2: the restored sweep status reaches the pending report (degraded
    # warning included) and the exit code stays strict.
    judgments = {
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [{"id": "n1", "junk": False, "worthiness": 80}],
    }

    def fake_run(*, topic, **_kwargs):
        return _rich_enrichment_report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        assert _run_leg2(tmp_path, judgments, config=strict) == 3
    captured = capsys.readouterr()
    assert "strict-exit: degraded sources: reddit" in captured.err
    pending_payload = json.loads(
        (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert pending_payload["report"]["source_status"]["reddit"]["state"] == (
        schema.UNREACHABLE
    )
    assert any(
        "Some discovery sources degraded: reddit" in warning
        for warning in pending_payload["report"]["warnings"]
    )

    # Leg 3: the brief renders the degraded coverage note and exits 3 too.
    args3, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--finalize",
    ])
    assert cli._run_discover_protocol_leg(args3, strict) == 3
    captured = capsys.readouterr()
    assert "Some discovery sources degraded: reddit" in captured.out
    assert "strict-exit: degraded sources: reddit" in captured.err


def test_discovery_nominate_nothing_solid_applies_strict_exit(tmp_path, capsys):
    """F1c: the leg-1 nothing-solid short-circuit is a terminal return too -
    it renders the brief and still exits 3 under strict exit when the sweep
    itself was degraded."""
    import dataclasses

    strict = {"LAST30DAYS_STRICT_EXIT": "1"}
    result = dataclasses.replace(_degraded_nominate_result(), pool=[])
    parser = cli.build_parser()
    args, _extra = parser.parse_known_args([
        "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
        "--nominate-only",
    ])
    with mock.patch.object(
        pipeline, "run_discover_nominate", return_value=result,
    ):
        assert cli._run_discover_protocol_leg(args, strict) == 3
    captured = capsys.readouterr()
    assert "Nothing solid this window." in captured.out
    assert "strict-exit: degraded sources: reddit" in captured.err
    assert not (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).exists()


def test_discovery_cli_full_mock_protocol_three_legs_end_to_end(tmp_path):
    """The whole protocol offline: nominate -> judgments -> finalize (with
    angles) produces a complete brief. Mock finalize writes NO queue rows and
    renders deterministically across two runs."""
    leg1 = _run_nominate_only(tmp_path)
    assert leg1.returncode == 0, leg1.stderr
    bundle_payload = json.loads(
        (tmp_path / discovery_handoff.NOMINATIONS_BUNDLE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    rows = bundle_payload["nominations"]
    keep = [row["id"] for row in rows if not row["heuristic_junk"]][:1]
    assert keep, "mock sweep should nominate at least one non-junk topic"
    judgments_path = tmp_path / "judgments.json"
    judgments_path.write_text(json.dumps({
        "bundle_id": bundle_payload["bundle_id"],
        "judgments": [
            {"id": keep[0], "name": "Renamed Mock Topic", "junk": False,
             "worthiness": 90},
            *[
                {"id": row["id"], "junk": True}
                for row in rows if row["id"] not in keep
            ],
        ],
    }), encoding="utf-8")
    leg2 = _run_protocol_cli(
        [
            "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
            "--judgments", str(judgments_path),
        ],
        env_overrides={"LAST30DAYS_DEFAULT_SEARCH": ""},
    )
    assert leg2.returncode == 0, leg2.stderr
    pending_payload = json.loads(
        (tmp_path / discovery_handoff.PENDING_REPORT_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    angles_path = _write_angles_file(tmp_path, pending_payload["bundle_id"], [
        {"id": keep[0],
         "podcast": "A mock podcast hook for the renamed topic",
         "x_article": "A mock X-article hook for the renamed topic"},
    ])

    def run_leg3():
        return _run_protocol_cli([
            "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
            "--finalize", "--angles", str(angles_path),
        ])

    first = run_leg3()
    assert first.returncode == 0, first.stderr
    # A complete brief: topic card, host angle lines, research handoff.
    assert "## 1. Renamed Mock Topic" in first.stdout
    assert (
        "**Podcast angle:** A mock podcast hook for the renamed topic"
        in first.stdout
    )
    assert (
        "**X article angle:** A mock X-article hook for the renamed topic"
        in first.stdout
    )
    assert '**Research next:** `/last30days "Renamed Mock Topic"`' in first.stdout
    # Mock stays queue-free and deterministic.
    assert not (tmp_path / "research.db").exists()
    second = run_leg3()
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert not (tmp_path / "research.db").exists()


# --- U6: engine-side LLM judge removed - discovery is provider-free -----------
# The stage-1 judge and stage-2 angle pass are deleted: no discovery code path
# may resolve a provider runtime or construct a provider client, ever.


def _provider_tripwires() -> list:
    """Patches that make ANY provider touch explode: resolve_runtime plus
    every client class on the providers module surface."""
    def _forbid(label: str):
        def _raise(*_args, **_kwargs):
            raise AssertionError(f"{label} touched from a discovery code path")
        return _raise

    return [
        mock.patch.object(pipeline.providers, name, new=_forbid(f"providers.{name}"))
        for name in (
            "resolve_runtime",
            "GeminiClient",
            "OpenAIClient",
            "XAIClient",
            "OpenRouterClient",
        )
    ]


def test_mock_discovery_constructs_no_provider_client(tmp_path, capsys):
    """--mock discovery must stay network-clean across the one-shot path AND
    all three protocol legs: subprocess tests inherit ambient env keys, so a
    single ungated resolve (or a directly constructed client) could let a
    --mock run reach the network. It must not emit the loud one-shot
    heuristics note either - the note is for real runs, not deliberate mock
    runs."""
    with contextlib.ExitStack() as stack:
        for patcher in _provider_tripwires():
            stack.enter_context(patcher)

        # One-shot sweep.
        report = pipeline.run_discover(
            domain="AI agents", config={}, mock=True, as_of_date="2026-07-10",
        )
        assert report.topics

        # Leg 1 (nominate-only) -> leg 2 (real mock enrichment sub-runs) ->
        # leg 3 (finalize, no angles file).
        bundle_payload = _leg1_bundle_payload(tmp_path)
        judgments = {"bundle_id": bundle_payload["bundle_id"], "judgments": []}
        assert _run_leg2(tmp_path, judgments) == 0
        parser = cli.build_parser()
        args, _extra = parser.parse_known_args([
            "--discover", "AI agents", "--mock", "--save-dir", str(tmp_path),
            "--finalize",
        ])
        assert cli._run_discover_protocol_leg(args, {}) == 0

    assert "deterministic heuristics" not in capsys.readouterr().err


def test_discovery_paths_are_provider_free_at_the_source_level():
    """Source-inspection pin (like the enrich ThreadPoolExecutor pin): no
    provider resolution is reachable from any discovery entry point, and the
    deleted judge module is never referenced by the pipeline."""
    for func in (
        pipeline.run_discover,
        pipeline.run_discover_nominate,
        pipeline.run_discover_resume,
        pipeline.nominate_topic_pool,
        pipeline.nominate_topics,
        pipeline.enrich_nominations,
    ):
        assert "resolve_runtime" not in inspect.getsource(func), func.__name__

    needle = "discovery" + "_judge"  # split so this pin never matches itself
    assert needle not in inspect.getsource(pipeline)


def test_no_engine_judge_references_remain_anywhere():
    """Repo-level pin: the engine judge module is deleted and nothing under
    skills/ or tests/ references it by name."""
    needle = "discovery" + "_judge"  # split so this pin never matches itself
    assert not (
        REPO_ROOT / "skills" / "last30days" / "scripts" / "lib" / f"{needle}.py"
    ).exists()
    offenders = [
        str(path)
        for root in ("skills", "tests")
        for path in sorted((REPO_ROOT / root).rglob("*.py"))
        if needle in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
