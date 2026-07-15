import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import pipeline, planner, reddit_listing, render, rerank, schema


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


def test_discovery_topic_name_uses_entities_shared_across_sources():
    reddit = _candidate(_item("r1", "reddit", "OpenAI Agent SDK launch details"))
    hn = _candidate(_item("h1", "hackernews", "OpenAI Agent SDK reaches developers"))
    candidates = {reddit.candidate_id: reddit, hn.candidate_id: hn}
    cluster = schema.Cluster(
        cluster_id="cluster-1",
        title=reddit.title,
        candidate_ids=list(candidates),
        representative_ids=[reddit.candidate_id],
        sources=["hackernews", "reddit"],
        score=80,
    )

    assert pipeline.discovery_topic_name(cluster, candidates, "AI agents") == "OpenAI Agent SDK"


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


def test_reddit_discovery_adapter_preserves_partial_feed_errors():
    item = {
        "url": "https://reddit.com/r/example/comments/1",
        "title": "AI agent launch",
    }
    with mock.patch.object(
        reddit_listing,
        "_fetch_one_with_status",
        side_effect=[([], "rising timed out"), ([item], None)],
    ):
        result = reddit_listing.fetch_discovery_listings(
            ["AI_Agents"], query="AI agents",
        )

    assert result["items"] == [item]
    assert result["errors"] == ["r/AI_Agents rising: rising timed out"]


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
    assert payload["schema_version"] == "1.0"
    assert payload["kind"] == "discovery"
    assert 5 <= len(payload["results"]) <= 10
    assert payload["results"][0]["command"].startswith('/last30days "')

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
