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


def _planned(report):
    """Subquery sources minus the installed/opted-in lanes the engine backfills."""
    return [
        [s for s in sq.sources if s not in pipeline.HOST_PLAN_BACKFILL_SOURCES]
        for sq in report.query_plan.subqueries
    ]


def _run_with_plan(topic, plan, depth="default", **kwargs):
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic=topic, config={}, mock=True, web_backend="none",
            depth=depth, external_plan=plan, **kwargs,
        )
    return report, {call.kwargs["source"] for call in retrieve.call_args_list}


def test_host_plan_on_company_topic_does_not_gain_jobs():
    report, fetched = _run_with_plan(
        "Listen Labs", plan_with(["reddit", "youtube"], ["hackernews"]),
    )
    assert _planned(report) == [["reddit", "youtube"], ["hackernews"]]
    assert "jobs" not in fetched
    assert "jobs" not in report.source_status


def test_host_plan_naming_jobs_keeps_it_on_non_company_topic():
    report, fetched = _run_with_plan(
        "test topic", plan_with(["reddit", "jobs"], ["hackernews"]),
    )
    assert _planned(report) == [["reddit", "jobs"], ["hackernews"]]
    assert "jobs" in fetched


def test_host_plan_naming_jobs_does_not_override_excluded_jobs():
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        report = pipeline.run(
            topic="test topic", config={"EXCLUDE_SOURCES": "jobs"}, mock=True,
            web_backend="none", depth="default",
            external_plan=plan_with(["reddit", "jobs"], ["hackernews"]),
        )
    fetched = {call.kwargs["source"] for call in retrieve.call_args_list}
    assert _planned(report) == [["reddit"], ["hackernews"]]
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


def test_host_plan_gains_installed_lanes_it_never_named(capsys):
    report, fetched = _run_with_plan(
        "test topic", plan_with(["reddit", "youtube"], ["hackernews"]),
        requested_sources=["reddit", "youtube", "hackernews", "digg", "arxiv"],
    )
    assert [sq.sources for sq in report.query_plan.subqueries] == [
        ["reddit", "youtube", "digg", "arxiv"], ["hackernews"],
    ]
    assert {"digg", "arxiv"} <= fetched
    assert "digg, arxiv" in capsys.readouterr().err


def test_host_plan_lane_named_in_secondary_subquery_is_not_duplicated():
    report, _ = _run_with_plan(
        "test topic", plan_with(["reddit"], ["digg"]),
        requested_sources=["reddit", "digg"],
    )
    assert [sq.sources for sq in report.query_plan.subqueries] == [["reddit"], ["digg"]]


def test_host_plan_backfill_only_adds_available_lanes():
    # EXCLUDE_SOURCES and --search filter ``available`` before the backfill runs.
    plan = planner._sanitize_plan(
        plan_with(["reddit"]), "test topic", ["reddit"], None, "default",
        honor_plan_sources=True,
    )
    pipeline._ensure_unplanned_lanes_in_host_plan(plan, ["reddit"])
    assert plan.subqueries[0].sources == ["reddit"]


def test_host_plan_backfill_skips_paid_decision_lanes():
    plan = planner._sanitize_plan(
        plan_with(["reddit"]), "test topic", ["reddit", "amazon", "meta_ads"], None, "default",
        honor_plan_sources=True,
    )
    pipeline._ensure_unplanned_lanes_in_host_plan(plan, ["reddit", "amazon", "meta_ads"])
    assert plan.subqueries[0].sources == ["reddit"]


def test_exclude_sources_keeps_an_installed_lane_out_of_the_backfill():
    def which(name):
        return "/fake/bin/digg-pp-cli" if name == "digg-pp-cli" else None

    with patch.object(pipeline, "which", side_effect=which):
        assert "digg" in pipeline.available_sources({})
        assert "digg" not in pipeline.available_sources({"EXCLUDE_SOURCES": "digg"})


def test_quick_host_plan_keeps_its_source_budget():
    report, fetched = _run_with_plan(
        "test topic", plan_with(["reddit", "youtube"]),
        requested_sources=["reddit", "youtube", "digg", "arxiv"], depth="quick",
    )
    assert [sq.sources for sq in report.query_plan.subqueries] == [["reddit", "youtube"]]
    assert not {"digg", "arxiv"} & fetched
