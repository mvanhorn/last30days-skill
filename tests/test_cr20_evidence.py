import pytest

from lib import cluster, fusion, normalize, render, rerank, schema, signals


@pytest.mark.parametrize("source, field, value", [
    ("youtube", "transcript_highlights", ["{entity} handles long sessions well."]),
    ("youtube", "transcript_snippet", "A detailed review of {entity}."),
    ("youtube", "top_comments", [{"text": "I use {entity} every day.", "likes": 25}]),
    ("reddit", "comment_insights", ["The discussion recommends {entity}."]),
    ("reddit", "top_comments", [{"excerpt": "{entity} works well.", "score": 25}]),
])
@pytest.mark.parametrize("entity, grounded", [("Hermes Agent", True), ("Unrelated Toolkit", False)])
def test_retained_enrichment_controls_entity_grounding_and_visible_evidence(
    source, field, value, entity, grounded,
):
    import json

    enrichment = json.loads(json.dumps(value).replace("{entity}", entity))
    items = normalize.normalize_source_items(
        source,
        [{
            "id": "weekly-notes",
            "title": "Weekly field notes",
            "url": "https://example.com/weekly-notes",
            "date": "2026-10-20",
            "engagement": {"views": 1000000, "score": 100},
            field: enrichment,
        }],
        "2026-09-22", "2026-10-22",
    )
    items[0].metadata["unrelated_provider_field"] = "Hermes Agent"
    plan = schema.QueryPlan(
        intent="research", freshness_mode="balanced_recent", cluster_mode="none",
        raw_topic="Hermes Agent",
        subqueries=[schema.SubQuery(
            label="primary", search_query="Hermes Agent", ranking_query="Hermes Agent",
            sources=[source],
        )],
        source_weights={source: 1.0},
    )
    items = signals.annotate_stream(
        items, "Hermes Agent", plan.freshness_mode, reference_date="2026-10-22",
    )
    candidates = fusion.weighted_rrf(
        {("primary", source): items}, plan, pool_limit=10,
        range_from="2026-09-22", range_to="2026-10-22",
    )
    ranked = rerank.rerank_candidates(
        topic="Hermes Agent", plan=plan, candidates=candidates,
        provider=None, model=None, shortlist_size=10,
    )
    report = schema.Report(
        topic="Hermes Agent", range_from="2026-09-22", range_to="2026-10-22",
        generated_at="2026-10-22T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider=None, planner_model=None, rerank_model=None,
        ),
        query_plan=plan, ranked_candidates=ranked,
        clusters=cluster.cluster_candidates(ranked, plan),
        items_by_source={source: items}, errors_by_source={},
    )

    text = render.render_compact(report)

    assert len(ranked) == 1
    assert ("entity-miss" not in ranked[0].explanation) is grounded
    assert ("Weekly field notes" in text) is grounded
    assert ("Nothing solid this window" not in text) is grounded
