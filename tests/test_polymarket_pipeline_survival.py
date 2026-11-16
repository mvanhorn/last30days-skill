"""A matched Polymarket market survives the pipeline and renders in selection order.

The parser judges each market against the research subject (``EntityMatcher``)
and ranks events by ``selection_score``. These tests pin what happens after
parse: the matched market's question reaches the normalized item, the generic
relevance floor does not drop an item the matcher kept, the post-merge topic
and keyword filters read the matched market and not only the event title, and
the Markets block and footer list items by the same score.

Fixtures are the live Gamma events under ``fixtures/polymarket``.
"""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from lib import normalize, pipeline, polymarket, render, schema, signals

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
TO_DATE = "2026-11-16"
FROM_DATE = "2026-10-17"
NFL_CHAMPION = "Pro Football: 2027 Champion"
WIN_TOTAL = "Pro Football: 49ers 2026 Win Total"
ROOKIE = "NBA: 2026-27 Rookie of the Year"
GAME = "Vikings vs. 49ers"
SEASON_QUERY = "What are people saying about the San Francisco 49ers this season?"
LENDEBORG = {"name": "Yaxel Lendeborg", "relation": "player"}


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _win_total_with_more_volume():
    """The win-total event with more 30-day money than the champion event, so
    volume order and selection order disagree."""
    event = copy.deepcopy(_fixture("pro_football_49ers_2026_win_total"))
    event["volume1mo"] = 50_000_000
    return event


def _game_event():
    return {
        "id": "g1", "title": GAME, "slug": "nfl-min-sf-2026-11-22",
        "active": True, "closed": False, "negRisk": False,
        "volume1mo": 900_000, "liquidity": 200_000, "endDate": "2026-11-23T01:00:00Z",
        "markets": [{
            "id": "m1", "question": GAME, "outcomes": '["Vikings", "49ers"]',
            "outcomePrices": '["0.31", "0.69"]', "volume": "850000", "volumeNum": 850_000,
            "liquidity": "200000", "endDate": "2026-11-23T01:00:00Z",
            "active": True, "closed": False,
        }],
    }


def _parse(topic, *events, hints=None):
    return polymarket.parse_polymarket_response(
        {"events": list(events)}, topic=topic, to_date=TO_DATE, hints=hints,
    )


def _normalized(topic, *events, hints=None):
    return normalize.normalize_source_items(
        "polymarket", _parse(topic, *events, hints=hints), FROM_DATE, TO_DATE,
    )


def _by_title(items):
    return {item.title: item for item in items}


def _report(topic, items):
    return schema.Report(
        topic=topic, range_from=FROM_DATE, range_to=TO_DATE,
        generated_at="2026-11-16T12:00:00Z", provider_runtime=None,
        query_plan=None, clusters=[], ranked_candidates=[],
        items_by_source={"polymarket": items}, errors_by_source={},
    )


# --- Normalize ---


def test_normalized_item_carries_the_matched_market_and_its_score():
    item = _by_title(_normalized("49ers", _fixture("pro_football_2027_champion")))[NFL_CHAMPION]

    assert "Will the San Francisco 49ers win the 2027 NFL league championship?" in item.body
    assert item.metadata["matched_market"]["label"] == "San Francisco 49ers"
    assert item.metadata["selection_tier"] == 1
    assert item.metadata["selection_score"] > 0


# --- The relevance floor ---


def test_game_market_survives_the_floor_under_a_wordy_ranking_query():
    items = _normalized("49ers", _game_event(), _fixture("pro_football_2027_champion"))
    for item in items:
        # The game scored 0.15-0.21 against the planner's phrasing; pin it under.
        item.local_relevance = 0.1 if item.title == GAME else 0.6

    kept = signals.prune_low_relevance(items)

    assert GAME in {item.title for item in kept}


def test_unmatched_polymarket_item_is_still_held_to_the_floor():
    items = _normalized("49ers", _fixture("pro_football_2027_champion"))
    legacy = schema.SourceItem(
        item_id="legacy", source="polymarket", title="Some other market", body="",
        url="https://polymarket.com/event/other", engagement={"volume": 1000},
    )
    legacy.local_relevance = 0.05
    items[0].local_relevance = 0.6

    kept = signals.prune_low_relevance([*items, legacy])

    assert "legacy" not in {item.item_id for item in kept}


# --- Post-merge topic and keyword filters ---


def test_champion_item_survives_the_topic_filter():
    items = _normalized("49ers", _fixture("pro_football_2027_champion"))

    kept = polymarket.filter_items_against_topic("49ers", items)

    assert [item.title for item in kept] == [NFL_CHAMPION]


def test_related_player_award_survives_the_topic_filter_with_hints():
    hints = {"kind": "team", "related": [LENDEBORG]}
    items = _normalized("Warriors", _fixture("nba_2026_27_rookie_of_the_year"), hints=hints)

    assert [item.title for item in polymarket.filter_items_against_topic(
        "Warriors", items, hints)] == [ROOKIE]
    assert polymarket.filter_items_against_topic("Warriors", items) == []


def test_keywords_keep_the_champion_item_through_its_outcome_label():
    items = _normalized("49ers", _fixture("pro_football_2027_champion"))

    kept = polymarket.filter_items_against_keywords(items, ["49ers", "niners"])

    assert [item.title for item in kept] == [NFL_CHAMPION]


# --- Render order and the matched line ---


def _forty_niners_items():
    return _normalized("49ers", _fixture("pro_football_2027_champion"), _win_total_with_more_volume())


def test_markets_block_lists_by_selection_score_not_volume():
    lines = render._render_polymarket_markets(_report("49ers", _forty_niners_items()))

    assert lines[0] == "## Polymarket Markets"
    assert NFL_CHAMPION in lines[2]
    assert WIN_TOTAL in lines[3]


def test_footer_lists_by_selection_score_not_volume():
    summaries = render._polymarket_top_markets(_forty_niners_items(), topic="49ers")

    assert "championship" in summaries[0]
    assert "12.5 wins" in summaries[1]


def test_items_without_a_score_still_sort_by_volume():
    def legacy(item_id, question, volume):
        return schema.SourceItem(
            item_id=item_id, source="polymarket", title=question, body="",
            url=f"https://polymarket.com/event/{item_id}", engagement={"volume": volume},
            metadata={"question": question, "outcome_prices": [("Yes", 0.4)]},
        )

    items = [
        legacy("small", "Will the 49ers make the playoffs?", 100),
        legacy("big", "Will the 49ers win the NFC West?", 900),
    ]

    lines = render._render_polymarket_markets(_report("49ers", items))
    summaries = render._polymarket_top_markets(items, topic="49ers")

    assert "NFC West" in lines[2] and "playoffs" in lines[3]
    assert "NFC West" in summaries[0] and "playoffs" in summaries[1]


def test_matched_line_names_the_entity_and_its_contender_rank():
    lines = render._render_polymarket_markets(_report("49ers", _forty_niners_items()))

    assert lines[2].startswith(
        "- 📊 Polymarket · Pro Football: 2027 Champion: "
        "San Francisco 49ers 14.5%, #1 of 32 (up 1.8 pts this month)"
    )


def test_related_player_line_names_the_player_and_relation():
    hints = {"kind": "team", "related": [LENDEBORG]}
    items = _normalized("Warriors", _fixture("nba_2026_27_rookie_of_the_year"), hints=hints)

    lines = render._render_polymarket_markets(_report("Warriors", items))

    assert len(lines) == 3
    assert "Yaxel Lendeborg (player) 34.6%, #1 of 30" in lines[2]
    # The player's own price is said once.
    assert lines[2].count("34.6%") == 1


def test_alias_matched_market_is_kept_in_the_markets_block():
    event = {
        "id": "f1", "title": "F1 Drivers Champion 2026", "slug": "f1-drivers-champion-2026",
        "active": True, "closed": False, "negRisk": False,
        "volume1mo": 2_000_000, "liquidity": 500_000, "endDate": "2026-12-07T00:00:00Z",
        "markets": [{
            "id": "f1m", "question": "Will Max Verstappen win the 2026 F1 Drivers Championship?",
            "groupItemTitle": "Max Verstappen",
            "outcomes": '["Yes", "No"]', "outcomePrices": '["0.40", "0.60"]',
            "volume": "900000", "volumeNum": 900_000, "liquidity": "100000",
            "endDate": "2026-12-07T00:00:00Z", "active": True, "closed": False,
        }],
    }
    items = _normalized("Formula 1", event, hints={"aliases": ["F1"]})
    assert items

    lines = render._render_polymarket_markets(_report("Formula 1", items))

    assert any("Max Verstappen" in line and "40%" in line for line in lines)


def test_win_total_line_leads_with_the_matched_rung():
    lines = render._render_polymarket_markets(_report("49ers", _forty_niners_items()))

    assert "Pro Football: 49ers 2026 Win Total: 12.5+ Wins 77.5%" in lines[3]
    assert "#" not in lines[3].split(" — ")[0]


# --- End to end ---


def test_mock_run_for_49ers_leads_the_compact_markets_block_with_the_champion():
    raw = _parse(
        "49ers", _fixture("pro_football_2027_champion"), _win_total_with_more_volume(), _game_event(),
    )
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [{
            "label": "primary",
            "search_query": "49ers",
            "ranking_query": SEASON_QUERY,
            "sources": ["polymarket"],
        }],
        "source_weights": {"polymarket": 1.0},
    }

    def fake_retrieve(**kwargs):
        return (copy.deepcopy(raw), {}) if kwargs.get("source") == "polymarket" else ([], {})

    with patch.object(pipeline, "_retrieve_stream", side_effect=fake_retrieve):
        report = pipeline.run(
            topic="49ers", config={}, depth="quick", mock=True,
            requested_sources=["polymarket"], web_backend="none",
            external_plan=plan, as_of_date=TO_DATE,
        )

    titles = {item.title for item in report.items_by_source["polymarket"]}
    assert {NFL_CHAMPION, WIN_TOTAL, GAME} <= titles

    compact = render.render_compact(report)
    block = compact.split("## Polymarket Markets", 1)[1].strip().splitlines()
    assert block[0].startswith("- 📊 Polymarket · Pro Football: 2027 Champion: San Francisco 49ers 14.5%")


def _small_game(index):
    title = f"Warriors vs. Team{index}"
    return {
        "id": f"wg{index}", "title": title, "slug": f"nba-gsw-team{index}",
        "active": True, "closed": False, "negRisk": False,
        "volume1mo": 5_000_000, "liquidity": 900_000, "endDate": "2026-12-20T01:00:00Z",
        "markets": [{
            "id": f"wm{index}", "question": title, "outcomes": '["Warriors", "Team"]',
            "outcomePrices": '["0.55", "0.45"]', "volume": "1500", "volumeNum": 1_500,
            "liquidity": "1000", "endDate": "2026-12-20T01:00:00Z",
            "active": True, "closed": False,
        }],
    }


def test_related_player_race_survives_the_stream_cut_at_quick_depth():
    hints = {"kind": "team", "related": [LENDEBORG]}
    raw = _parse(
        "Warriors", _fixture("nba_2026_27_rookie_of_the_year"),
        *(_small_game(index) for index in range(8)), hints=hints,
    )
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [{
            "label": "primary",
            "search_query": "Warriors",
            "ranking_query": "What are people saying about Warriors?",
            "sources": ["polymarket"],
        }],
        "source_weights": {"polymarket": 1.0},
        "polymarket_hints": hints,
    }

    def fake_retrieve(**kwargs):
        return (copy.deepcopy(raw), {}) if kwargs.get("source") == "polymarket" else ([], {})

    with patch.object(pipeline, "_retrieve_stream", side_effect=fake_retrieve):
        report = pipeline.run(
            topic="Warriors", config={}, depth="quick", mock=True,
            requested_sources=["polymarket"], web_backend="none",
            external_plan=plan, as_of_date=TO_DATE,
        )

    titles = [item.title for item in report.items_by_source["polymarket"]]
    assert ROOKIE in titles
    compact = render.render_compact(report)
    block = compact.split("## Polymarket Markets", 1)[1].strip().splitlines()
    assert ROOKIE in block[0]


def test_race_with_suppressed_odds_still_lists_the_matched_player():
    """When a race's Yes prices sum well over one, the parser withholds the
    event's odds and outcome list but keeps the subject's own market, which
    is priced on its own book. The Markets block still lists it."""
    hints = {"kind": "team", "related": [{"name": "Ty Jerome", "relation": "player"}]}
    items = _normalized(
        "Memphis Grizzlies", _fixture("nba_2026_27_most_improved_player"), hints=hints,
    )
    for item in items:
        # What the parser stores for a race whose Yes prices are impossible.
        item.metadata["odds"] = None
        item.metadata["outcome_prices"] = []

    lines = render._render_polymarket_markets(_report("Memphis Grizzlies", items))

    assert len(lines) == 3
    assert ": Ty Jerome (player) 34" in lines[2]
    assert ", #1 of " in lines[2]
