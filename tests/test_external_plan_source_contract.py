"""Unavailable external-plan sources must never broaden retrieval."""

from unittest.mock import patch

import pytest

from lib import pipeline, planner


def plan_with(*source_lists):
    return {
        "intent": "opinion",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "debate",
        "subqueries": [
            {"label": f"part-{i}", "search_query": "test topic", "ranking_query": "test topic",
             "sources": sources, "weight": 1.0}
            for i, sources in enumerate(source_lists)
        ],
    }


@pytest.mark.parametrize("depth", ["quick", "default", "deep"])
def test_all_unavailable_fails_before_retrieval(depth, capsys):
    with patch.object(pipeline, "_retrieve_stream") as retrieve:
        with pytest.raises(ValueError, match="No available planned sources"):
            pipeline.run(
                topic="test topic", config={}, mock=True, web_backend="none",
                requested_sources=["reddit", "hackernews"], depth=depth,
                external_plan=plan_with(["instagram"]),
            )
    retrieve.assert_not_called()
    assert "part-0" in capsys.readouterr().err


@pytest.mark.parametrize("depth", ["quick", "default", "deep"])
def test_mixed_plan_skips_empty_subquery_without_expansion(depth, capsys):
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic="test topic", config={}, mock=True, web_backend="none",
            requested_sources=["reddit", "hackernews"], depth=depth,
            external_plan=plan_with(["instagram"], ["reddit", "instagram"]),
        )
    assert report.query_plan.subqueries[0].label == "part-1"
    assert report.query_plan.subqueries[0].sources == ["reddit"]
    assert retrieve.call_args_list
    assert {call.kwargs["source"] for call in retrieve.call_args_list} == {"reddit"}
    assert "part-0" in capsys.readouterr().err


def test_internal_empty_intersection_retains_fallback():
    plan = planner._sanitize_plan(
        plan_with(["instagram"]), "test topic", ["reddit", "hackernews"], None, "default",
    )
    assert plan.subqueries[0].sources == ["reddit", "hackernews"]
