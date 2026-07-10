import json
from pathlib import Path

import last30days as cli
from lib import health, schema


GOLDEN = Path(__file__).parent / "fixtures" / "agent_export_v1.json"


def _report() -> schema.Report:
    reddit_item = schema.SourceItem(
        item_id="reddit-1",
        source="reddit",
        title="Agents move into daily coding workflows",
        body="Developers described where coding agents save time.",
        url="https://www.reddit.com/r/programming/comments/agent-workflows",
        published_at="2026-06-28",
        snippet="Developers shared concrete agent workflows.",
        engagement={"score": 1543, "num_comments": 201},
    )
    x_item = schema.SourceItem(
        item_id="x-1",
        source="x",
        title="Teams compare coding-agent review loops",
        body="A thread compared review loops across several tools.",
        url="https://x.com/example/status/123",
        published_at="2026-07-02",
        snippet="Teams compared how agents fit into code review.",
        engagement={"likes": 800, "reposts": 50},
    )
    reddit_candidate = schema.Candidate(
        candidate_id="candidate-reddit",
        item_id=reddit_item.item_id,
        source="reddit",
        title=reddit_item.title,
        url=reddit_item.url,
        snippet=reddit_item.snippet,
        subquery_labels=["workflows"],
        native_ranks={"workflows:reddit": 1},
        local_relevance=0.95,
        freshness=85,
        engagement=100,
        source_quality=0.6,
        rrf_score=0.02,
        final_score=92,
        cluster_id="cluster-workflows",
        source_items=[reddit_item],
    )
    x_candidate = schema.Candidate(
        candidate_id="candidate-x",
        item_id=x_item.item_id,
        source="x",
        title=x_item.title,
        url=x_item.url,
        snippet=x_item.snippet,
        subquery_labels=["reviews"],
        native_ranks={"reviews:x": 1},
        local_relevance=0.88,
        freshness=92,
        engagement=80,
        source_quality=0.68,
        rrf_score=0.018,
        final_score=84,
        source_items=[x_item],
    )
    return schema.Report(
        topic="AI coding agents",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="fixture-planner",
            rerank_model="fixture-reranker",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="AI coding agents",
            subqueries=[
                schema.SubQuery(
                    label="workflows",
                    search_query="AI coding agent workflows",
                    ranking_query="How are developers using AI coding agents?",
                    sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0, "x": 0.8},
        ),
        clusters=[
            schema.Cluster(
                cluster_id="cluster-workflows",
                title="Agents move into daily coding workflows",
                candidate_ids=[reddit_candidate.candidate_id],
                representative_ids=[reddit_candidate.candidate_id],
                sources=["reddit"],
                score=92,
            ),
            schema.Cluster(
                cluster_id="cluster-reviews",
                title="Teams compare coding-agent review loops",
                candidate_ids=[x_candidate.candidate_id],
                representative_ids=[x_candidate.candidate_id],
                sources=["x"],
                score=84,
            ),
        ],
        ranked_candidates=[reddit_candidate, x_candidate],
        items_by_source={"reddit": [reddit_item], "x": [x_item]},
        errors_by_source={
            "youtube": "HTTP 429",
            "github": "HTTP 401",
            "grounding": "DNS failure",
        },
        source_status={
            "reddit": schema.SourceOutcome(source="reddit", state=health.OK, items_returned=1),
            "x": schema.SourceOutcome(source="x", state=health.OK, items_returned=1),
            "hackernews": schema.SourceOutcome(source="hackernews", state=schema.NO_RESULTS),
            "youtube": schema.SourceOutcome(source="youtube", state=schema.RATE_LIMITED),
            "grounding": schema.SourceOutcome(source="grounding", state=schema.UNREACHABLE),
            "github": schema.SourceOutcome(source="github", state=schema.AUTH_FAILED),
        },
    )


def test_agent_export_matches_v1_golden_contract():
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert schema.to_agent_export(_report()) == expected


def test_agent_export_maps_per_run_source_outcomes_to_states():
    exported = schema.to_agent_export(_report())

    assert exported["source_status"] == {
        "github": "auth-failed",
        "grounding": "unreachable",
        "hackernews": "no-results",
        "reddit": "ok",
        "x": "ok",
        "youtube": "rate-limited",
    }


def test_raw_profile_is_byte_identical_to_legacy_report_dump():
    report = _report()
    legacy = json.dumps(schema.to_dict(report), indent=2, sort_keys=True)

    assert cli.emit_output(report, "json", json_profile="raw") == legacy


def test_raw_comparison_profile_is_byte_identical_to_legacy_wrapper():
    report = _report()
    reports = [("AI coding agents", report)]
    legacy = json.dumps(
        {
            "comparison": True,
            "entities": ["AI coding agents"],
            "reports": [{"entity": "AI coding agents", "report": schema.to_dict(report)}],
        },
        indent=2,
        sort_keys=True,
    )

    assert cli.emit_comparison_output(reports, "json", json_profile="raw") == legacy


def test_json_profile_parser_defaults_to_agent_and_accepts_raw():
    parser = cli.build_parser()

    assert parser.parse_args(["topic", "--emit=json"]).json_profile == "agent"
    assert parser.parse_args(["topic", "--emit=json", "--json-profile=raw"]).json_profile == "raw"
