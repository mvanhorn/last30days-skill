"""Provider JSON may decode successfully without matching the plan shape."""

from unittest.mock import Mock

import pytest

from lib import planner


def _plan(payload, depth="quick"):
    provider = Mock()
    provider.generate_json.return_value = payload
    return planner.plan_query(
        topic="SQLite incremental vacuum",
        available_sources=["hackernews", "reddit"],
        requested_sources=["hackernews"],
        depth=depth,
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


def _query(text, **kwargs):
    return {
        "search_query": text,
        "ranking_query": f"What can we learn about {text}?",
        "sources": ["hackernews"],
        **kwargs,
    }


def test_invalid_rows_do_not_take_slots_from_usable_provider_queries():
    plan = _plan({
        "intent": "concept",
        "subqueries": [
            None,
            _query({"query": "wrong shape"}),
            _query("usable first"),
            _query("usable second"),
            _query("beyond the cap"),
        ],
    }, depth="default")
    assert [query.search_query for query in plan.subqueries] == [
        "usable first", "usable second",
    ]


@pytest.mark.parametrize("invalid_weight", [float("inf"), "invalid"])
def test_invalid_weight_normalizes_like_an_omitted_weight(invalid_weight):
    control = _query("first")
    second = _query("second", weight=3.0)
    expected = _plan({"subqueries": [control, second]}, depth="default")
    actual = _plan(
        {"subqueries": [{**control, "weight": invalid_weight}, second]},
        depth="default",
    )
    assert [query.weight for query in actual.subqueries] == [
        query.weight for query in expected.subqueries
    ] == [0.25, 0.75]
