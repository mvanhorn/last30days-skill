import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

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
    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config):
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
    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config):
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
            "--as-of",
            "2026-07-10",
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
