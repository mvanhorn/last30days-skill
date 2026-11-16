"""The host plan's Polymarket hints: validation, plan round-trip, and per-entity runs."""

import json
from dataclasses import asdict
from unittest.mock import patch

import pytest

import last30days as cli
from lib import pipeline, planner, polymarket, schema
from tests.competitor_cli_audit_helpers import run_competitor_cli


def host_plan(**extra):
    return {
        "intent": "factual",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "none",
        "subqueries": [{
            "label": "primary", "search_query": "Formula 1", "ranking_query": "Formula 1",
            "sources": ["polymarket"], "weight": 1.0,
        }],
        **extra,
    }


WARRIORS = {
    "kind": "team",
    "aliases": ["Golden State Warriors"],
    "related": [
        {"name": "Yaxel Lendeborg", "relation": "player"},
        {"name": "Stephen Curry", "relation": "player"},
    ],
}


def sanitize(raw, topic="Formula 1"):
    return planner._sanitize_plan(raw, topic=topic, depth="default",
        available_sources=["polymarket"], requested_sources=["polymarket"])


def polymarket_config(external_plan, topic="Formula 1"):
    with patch.object(pipeline, "_retrieve_stream", return_value=([], {})) as retrieve:
        pipeline.run(topic=topic, config={}, mock=True, web_backend="none",
            requested_sources=["polymarket"], depth="default", external_plan=external_plan)
    configs = [call.kwargs["config"] for call in retrieve.call_args_list
               if call.kwargs["source"] == "polymarket"]
    assert configs
    return configs[0]


def test_alias_hint_reaches_the_polymarket_fetch():
    raw = host_plan(polymarket_hints={"aliases": ["F1"]})
    planner.validate_external_plan(raw)
    config = polymarket_config(raw)
    hints = polymarket.PolymarketHints.coerce(config["_polymarket_hints"])
    assert hints.aliases == ("F1",)
    assert hints.kind is None
    assert hints.related == ()


def test_team_hint_with_related_players_reaches_the_fetch():
    raw = host_plan(polymarket_hints=WARRIORS)
    planner.validate_external_plan(raw)
    hints = polymarket.PolymarketHints.coerce(polymarket_config(raw, "Warriors")["_polymarket_hints"])
    assert hints.kind == "team"
    assert hints.aliases == ("Golden State Warriors",)
    assert hints.related == (
        polymarket.RelatedName("Yaxel Lendeborg", "player"),
        polymarket.RelatedName("Stephen Curry", "player"),
    )


def test_hints_survive_sanitize_and_round_trip():
    plan = sanitize(host_plan(polymarket_hints=WARRIORS), "Warriors")
    assert plan.polymarket_hints == WARRIORS
    assert schema.query_plan_from_dict(asdict(plan)) == plan
    assert schema.query_plan_from_dict(schema.to_dict(plan)) == plan


@pytest.mark.parametrize("hints,message", [
    ("F1", "polymarket_hints must be an object"),
    ({"kind": "league"}, "polymarket_hints.kind"),
    ({"aliases": "F1"}, "polymarket_hints.aliases"),
    ({"aliases": ["F1", ""]}, "polymarket_hints.aliases"),
    ({"aliases": ["x" * 81]}, "polymarket_hints.aliases"),
    ({"aliases": ["a", "b", "c", "d", "e", "f"]}, "polymarket_hints.aliases"),
    ({"related": {"name": "Stephen Curry", "relation": "player"}}, "polymarket_hints.related"),
    ({"related": [{"name": "Stephen Curry", "relation": "coach"}]}, "polymarket_hints.related"),
    ({"related": [{"name": "", "relation": "player"}]}, "polymarket_hints.related"),
    ({"related": [{"name": f"P{i}", "relation": "player"} for i in range(6)]},
     "polymarket_hints.related"),
    ({"aliases": ["F1"], "teams": ["Ferrari"]}, "polymarket_hints"),
])
def test_invalid_hints_are_rejected_with_a_clear_message(hints, message):
    with pytest.raises(ValueError, match=message):
        planner.validate_external_plan(host_plan(polymarket_hints=hints))


def test_plan_without_hints_is_unchanged():
    raw = host_plan()
    planner.validate_external_plan(raw)
    plan = sanitize(raw)
    assert plan.polymarket_hints is None
    assert "polymarket_hints" not in schema.to_dict(plan)
    legacy = schema.to_dict(plan)
    assert schema.query_plan_from_dict(legacy) == plan
    assert "_polymarket_hints" not in polymarket_config(raw)


def test_internal_planner_drops_malformed_hints_and_keeps_its_plan():
    plan = sanitize(host_plan(polymarket_hints={"kind": "league", "aliases": "F1"}))
    assert plan.subqueries
    assert plan.polymarket_hints is None


def test_competitors_plan_accepts_and_validates_peer_hints(capsys):
    out = cli.parse_competitors_plan(json.dumps({"Warriors": {"polymarket_hints": WARRIORS}}))
    assert out["warriors"]["polymarket_hints"] == WARRIORS
    assert "Unknown fields" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.parse_competitors_plan(json.dumps({"Warriors": {"polymarket_hints": {"kind": "league"}}}))
    assert "polymarket_hints.kind" in capsys.readouterr().err


def test_main_hints_and_keywords_do_not_leak_and_peer_hints_apply(monkeypatch, tmp_path):
    main_plan = host_plan(polymarket_hints={"kind": "team", "aliases": ["MainBrand FC"]})
    plan_file = tmp_path / "main-plan.json"
    plan_file.write_text(json.dumps(main_plan), encoding="utf-8")
    peer_hints = {"kind": "company", "aliases": ["Peer One Inc"]}
    observed = run_competitor_cli(monkeypatch, tmp_path, args=[
        "--plan", str(plan_file),
        "--polymarket-keywords", "mainbrand",
        "--competitors-plan", json.dumps({"PeerOne": {"polymarket_hints": peer_hints}}),
    ])

    assert observed.calls["MainBrand"]["external_plan"]["polymarket_hints"] == main_plan["polymarket_hints"]
    assert observed.calls["MainBrand"]["config"]["_polymarket_keywords"] == ["mainbrand"]
    for peer in ("PeerOne", "PeerTwo"):
        assert "_polymarket_keywords" not in observed.calls[peer]["config"]
    assert observed.calls["PeerOne"]["config"]["_polymarket_hints"] == {**peer_hints, "related": []}
    assert "_polymarket_hints" not in observed.calls["PeerTwo"]["config"]
