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


def _run_with_plan(topic, plan, **kwargs):
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic=topic, config={}, mock=True, web_backend="none",
            depth="default", external_plan=plan, **kwargs,
        )
    return report, {call.kwargs["source"] for call in retrieve.call_args_list}


def test_host_plan_on_company_topic_does_not_gain_jobs():
    report, fetched = _run_with_plan(
        "Listen Labs", plan_with(["reddit", "youtube"], ["hackernews"]),
    )
    assert [sq.sources for sq in report.query_plan.subqueries] == [
        ["reddit", "youtube"], ["hackernews"],
    ]
    assert "jobs" not in fetched
    assert "jobs" not in report.source_status


def test_host_plan_naming_jobs_keeps_it_on_non_company_topic():
    report, fetched = _run_with_plan(
        "test topic", plan_with(["reddit", "jobs"], ["hackernews"]),
    )
    assert [sq.sources for sq in report.query_plan.subqueries] == [
        ["reddit", "jobs"], ["hackernews"],
    ]
    assert "jobs" in fetched


def test_host_plan_naming_jobs_does_not_override_excluded_jobs():
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic="test topic", config={"EXCLUDE_SOURCES": "jobs"}, mock=True,
            web_backend="none", depth="default",
            external_plan=plan_with(["reddit", "jobs"], ["hackernews"]),
        )
    fetched = {call.kwargs["source"] for call in retrieve.call_args_list}
    assert [sq.sources for sq in report.query_plan.subqueries] == [
        ["reddit"], ["hackernews"],
    ]
    assert "jobs" not in fetched


def test_hiring_mode_adds_jobs_to_host_plan_without_it():
    report, fetched = _run_with_plan(
        "Listen Labs", plan_with(["reddit"], ["hackernews"]),
        hiring_signals_mode=True,
    )
    assert len(report.query_plan.subqueries) == 2
    assert all("jobs" in sq.sources for sq in report.query_plan.subqueries)
    assert "jobs" in fetched


def test_hiring_mode_does_not_duplicate_jobs_already_in_host_plan():
    report, _ = _run_with_plan(
        "Listen Labs", plan_with(["reddit", "jobs"], ["jobs"]),
        hiring_signals_mode=True,
    )
    assert len(report.query_plan.subqueries) == 2
    assert all(sq.sources.count("jobs") == 1 for sq in report.query_plan.subqueries)
