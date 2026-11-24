"""Provider JSON may decode successfully without matching the plan shape."""

from unittest.mock import Mock

import pytest

from lib import planner


def _plan(payload):
    provider = Mock()
    provider.generate_json.return_value = payload
    return planner.plan_query(
        topic="SQLite incremental vacuum",
        available_sources=["hackernews", "reddit"],
        requested_sources=["hackernews"],
        depth="quick",
        provider=provider,
        model="fixture",
    )


@pytest.mark.parametrize("subqueries", [{"primary": "vacuum"}, 42])
def test_invalid_subquery_collection_falls_back(subqueries):
    plan = _plan({"subqueries": subqueries})
    assert plan.subqueries[0].sources == ["hackernews"]
    assert "fallback-plan" in " ".join(plan.notes)


def test_malformed_fields_do_not_discard_a_valid_provider_query():
    plan = _plan({
        "notes": 42,
        "subqueries": [
            {
                "search_query": {"keywords": "SQLite"},
                "ranking_query": "Wrong shape",
                "sources": ["hackernews"],
            },
            {
                "search_query": "sqlite vacuum api",
                "ranking_query": "How do SQLite vacuum modes work?",
                "sources": [{"name": "reddit"}, "hackernews"],
                "weight": float("inf"),
            },
        ],
    })
    assert plan.notes == []
    assert len(plan.subqueries) == 1
    subquery = plan.subqueries[0]
    assert subquery.search_query == "sqlite vacuum api"
    assert subquery.sources == ["hackernews"]
    assert subquery.weight == 1.0
