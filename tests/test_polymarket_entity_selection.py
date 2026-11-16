"""Entity-level Polymarket selection: which events a topic keeps, how they rank,
and which market each one headlines.

The fixtures under ``fixtures/polymarket`` are live Gamma ``public-search``
events from 2026-11-16, trimmed to the fields the parser reads. Prices are the
ones Polymarket displays (see ``_quoted_price``): the midpoint of a tight book,
otherwise the last trade.
"""

import copy
import json
import math
from pathlib import Path

import pytest

from lib import polymarket

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket"
TO_DATE = "2026-11-16"


def _fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _parse(topic, *events, to_date=TO_DATE, cap=None, **hints):
    response = {"events": list(events)}
    if cap is not None:
        response["_cap"] = cap
    return polymarket.parse_polymarket_response(
        response, topic=topic, to_date=to_date,
        hints=polymarket.PolymarketHints.coerce(hints or None),
    )


def _by_title(items):
    return {item["title"]: item for item in items}


def _market(question, label, yes, volume, end="2027-03-01", **extra):
    return {
        "id": f"m-{label}", "question": question, "groupItemTitle": label,
        "outcomes": '["Yes", "No"]', "outcomePrices": json.dumps([str(yes), str(round(1 - yes, 4))]),
        "volume": str(volume), "volumeNum": volume, "liquidity": "5000",
        "endDate": f"{end}T00:00:00Z", "active": True, "closed": False, **extra,
    }


def _event(event_id, title, markets, neg_risk=False, volume1mo=1000):
    return {
        "id": event_id, "title": title, "slug": event_id, "active": True, "closed": False,
        "negRisk": neg_risk, "volume1mo": volume1mo, "liquidity": 10000,
        "updatedAt": "2026-11-16T00:00:00Z", "markets": markets,
    }


NFL_CHAMPION = "Pro Football: 2027 Champion"
NBA_CHAMPION = "NBA: 2027 Champion"
ROOKIE = "NBA: 2026-27 Rookie of the Year"
WIN_TOTAL = "Pro Football: 49ers 2026 Win Total"


# --- Headline: the entity's own market ---


def test_champion_event_headlines_the_entitys_own_outcome():
    [item] = _parse("49ers", _fixture("pro_football_2027_champion"))

    assert item["title"] == NFL_CHAMPION
    assert item["matched_market"]["label"] == "San Francisco 49ers"
    assert item["headline"]["price"] == 0.145
    assert item["question"] == "Will the San Francisco 49ers win the 2027 NFL league championship?"
    assert item["price_movement"] == "up 1.8% this month"
    assert item["end_date"] == "2027-04-01"
    assert item["outcome_prices"][0] == ("San Francisco 49ers", 0.145)
    matched = item["matched_market"]
    assert (matched["label"], matched["rank"], matched["contender"]) == ("San Francisco 49ers", 1, True)
    assert "Chargers" not in item["question"]


def test_near_settled_win_total_ranks_after_the_live_champion_market():
    win_total = _fixture("pro_football_49ers_2026_win_total")
    win_total["markets"] = [m for m in win_total["markets"] if m["groupItemTitle"] == "8.5+ Wins"]

    items = _parse("49ers", win_total, _fixture("pro_football_2027_champion"))

    assert [item["title"] for item in items] == [NFL_CHAMPION, WIN_TOTAL]
    assert [item["selection_tier"] for item in items] == [1, 0]


def test_value_ladder_headlines_the_live_rung_closest_to_even():
    items = _parse("49ers", _fixture("pro_football_49ers_2026_win_total"),
                   _fixture("pro_football_2027_champion"))

    assert items[0]["title"] == NFL_CHAMPION
    win_total = _by_title(items)[WIN_TOTAL]
    assert win_total["selection_tier"] == 1
    assert win_total["matched_market"]["label"] == "12.5+ Wins"
    assert win_total["headline"]["price"] == 0.775


# --- Price floors ---


def test_long_shot_in_a_huge_race_ranks_after_a_smaller_live_market():
    items = _parse("Elon Musk", _fixture("republican_presidential_nominee_2028"),
                   _fixture("who_will_trump_pardon_before_2027"))

    assert [item["title"] for item in items] == [
        "Who will Trump pardon before 2027?", "Republican Presidential Nominee 2028",
    ]
    pardon, nominee = items
    assert pardon["selection_tier"] == 1
    assert pardon["matched_market"]["label"] == "Elon Musk"
    # 0.25% with $32M on it: under the 0.5% race floor.
    assert nominee["selection_tier"] == 0
    assert nominee["matched_market"]["price"] == 0.0025


def test_largest_company_race_is_kept_for_the_leader_and_a_long_shot():
    [apple] = _parse("Apple", _fixture("largest_company_end_of_december_2026"))
    assert apple["selection_tier"] == 1
    assert apple["matched_market"]["label"] == "Apple"
    assert apple["headline"]["price"] == 0.9055
    assert apple["matched_market"]["contender"] is True

    items = _by_title(_parse("Microsoft", _fixture("largest_company_end_of_december_2026"),
                             _fixture("third_largest_company_end_of_december_2026")))
    largest = items["Largest Company end of December 2026?"]
    third = items["3rd Largest Company end of December 2026?"]
    # 0.55% sits just above the race floor; fourth of eight is no contender.
    assert (largest["selection_tier"], largest["matched_market"]["rank"]) == (1, 4)
    assert largest["matched_market"]["contender"] is False
    assert largest["selection_score"] == pytest.approx(math.log1p(1335787.56), abs=0.01)
    # 35.5% and first of nine: the contender bonus.
    assert (third["matched_market"]["rank"], third["matched_market"]["contender"]) == (1, True)
    assert third["selection_score"] == pytest.approx(math.log1p(38086.96) + 1, abs=0.01)


def test_team_title_odds_count_down_to_the_race_floor():
    [item] = _parse("Warriors", _fixture("nba_2027_champion"))
    assert item["selection_tier"] == 1
    assert item["matched_market"]["label"] == "Golden State Warriors"
    assert item["headline"]["price"] == 0.0225
    assert item["matched_market"]["rank"] == 13


def test_a_teams_biggest_race_has_no_floor_when_the_host_says_team():
    # The Wizards at 0.25% are under the 0.5% race floor.
    [plain] = _parse("Washington Wizards", _fixture("nba_2027_champion"))
    assert plain["selection_tier"] == 0
    [team] = _parse("Washington Wizards", _fixture("nba_2027_champion"), kind="team")
    assert team["selection_tier"] == 1
    assert team["matched_market"]["label"] == "Washington Wizards"


# --- Related names and the contender bonus ---

LENDEBORG = {"name": "Yaxel Lendeborg", "relation": "player"}


def test_related_name_leading_a_race_brings_the_race_in():
    items = _parse("Warriors", _fixture("nba_2027_champion"),
                   _fixture("nba_2026_27_rookie_of_the_year"),
                   kind="team", related=[LENDEBORG])

    assert [item["title"] for item in items] == [NBA_CHAMPION, ROOKIE]
    rookie = items[1]
    assert rookie["matched_market"]["label"] == "Yaxel Lendeborg"
    # A 10.7-cent spread: Polymarket shows the last trade.
    assert rookie["headline"]["price"] == 0.346
    matched = rookie["matched_market"]
    assert (matched["related_name"], matched["rank"], matched["contender"]) == (
        "Yaxel Lendeborg", 1, True,
    )
    assert rookie["selection_score"] == pytest.approx(math.log1p(49097.83) + 1, abs=0.01)


def test_race_with_no_entity_or_related_name_is_not_matched():
    assert _parse("Warriors", _fixture("nba_2026_27_rookie_of_the_year")) == []


def test_related_name_outside_the_top_three_contributes_nothing():
    # Darryn Peterson is fourth at 8.4% with $45k traded.
    assert _parse("Warriors", _fixture("nba_2026_27_rookie_of_the_year"),
                  related=[{"name": "Darryn Peterson", "relation": "player"}]) == []


def test_related_name_wins_a_large_sparse_race():
    [item] = _parse("Memphis Grizzlies", _fixture("nba_2026_27_most_improved_player"),
                    kind="team", related=[{"name": "Ty Jerome", "relation": "player"}])
    assert item["matched_market"]["label"] == "Ty Jerome"
    assert item["matched_market"]["contender"] is True


def _placeholder_race(volume):
    markets = [_market("Will Acme be the top lab?", "Acme", 0.5, volume)] + [
        _market(f"Will Lab {n} be the top lab?", f"Lab {n}", 0.1, 50000) for n in range(1, 5)
    ]
    return _event("race", "Top lab end of 2026?", markets, neg_risk=True)


def test_untraded_favorite_earns_no_contender_bonus():
    [item] = _parse("Acme", _placeholder_race(volume=400))
    assert item["matched_market"]["rank"] == 1
    assert item["matched_market"]["contender"] is False
    assert item["selection_score"] == pytest.approx(math.log1p(400))
    assert _parse("Widgets", _placeholder_race(volume=400),
                  related=[{"name": "Acme", "relation": "product"}]) == []


# --- Large events contribute one market ---


def test_game_sheet_contributes_only_its_largest_live_entity_market():
    props = [
        _market(f"Benfica to score {n}+ goals?", f"Benfica {n}+", 0.3, 1000 * n, end="2026-11-26")
        for n in range(1, 34)
    ]
    other = _market("Exact Score: Any Other Score?", "Any Other Score", 0.2, 900000, end="2026-11-26")
    event = _event("game", "FC Viktoria Plzen vs. Sport Lisboa e Benfica - More Markets",
                   props + [other])

    [item] = _parse("Benfica", event)

    assert item["matched_market"]["label"] == "Benfica 33+"
    assert item["selection_score"] == pytest.approx(math.log1p(33000))


def test_live_exact_score_sheet_cannot_ride_its_any_other_score_prop():
    # Every score line names Benfica but none has a price or a trade; only
    # "Any Other Score" (Benfica not in its own question) has $35.7k traded.
    [item] = _parse("Benfica", _fixture("plzen_vs_benfica_exact_score"))
    assert item["selection_tier"] == 0
    assert item["matched_market"]["label"] != "Any Other Score"
    assert item["selection_score"] == 0.0
    assert item["headline"] is None


def test_title_level_topic_takes_one_market_from_a_league_wide_race():
    [item] = _parse("NFL", _fixture("pro_football_2027_champion"))
    # The busiest outcome at or above the 0.5% race floor.
    assert item["matched_market"]["label"] == "Tampa Bay Buccaneers"
    assert item["matched_market"]["label"] == "Tampa Bay Buccaneers"
    assert item["selection_score"] == pytest.approx(math.log1p(4684832.49), abs=0.01)


def test_best_ai_model_race_is_matched_on_the_outcome():
    [item] = _parse("Anthropic", _fixture("best_ai_model_end_of_2026"))
    assert item["matched_market"]["label"] == "Anthropic"
    assert item["headline"]["price"] == 0.18
    assert item["matched_market"]["rank"] == 2


# --- Reference date and cap ---


def test_days_to_close_count_from_the_runs_end_date():
    event = _event("soon", "Will Acme ship by November 21?",
                   [_market("Will Acme ship by November 21?", "", 0.4, 5000, end="2026-11-21")])
    [late] = _parse("Acme", event, to_date="2026-11-16")
    assert late["selection_tier"] == 0
    [early] = _parse("Acme", event, to_date="2026-11-06")
    assert early["selection_tier"] == 1


def test_quick_cap_keeps_the_top_scored_event():
    minor = [
        _event(f"minor{n}", f"Will the 49ers sign free agent {n}?",
               [_market(f"Will the 49ers sign free agent {n}?", "", 0.4, 500)],
               volume1mo=50_000_000)
        for n in range(8)
    ]
    items = _parse("49ers", *minor, _fixture("pro_football_2027_champion"), cap=5)
    assert len(items) == 5
    assert items[0]["title"] == NFL_CHAMPION


# --- Re-checking saved claims ---


def _refetch(monkeypatch, event, key):
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = type("Item", (), {"metadata": {"event_id": event["id"]}, "url": "", "item_id": ""})()
    return polymarket.refetch_datum(item, key)["value"]


def _champion_with_niners_at(price):
    event = _fixture("pro_football_2027_champion")
    for market in event["markets"]:
        if market["groupItemTitle"] == "San Francisco 49ers":
            market.update(outcomePrices=json.dumps([str(price), str(round(1 - price, 4))]),
                          bestBid=price - 0.001, bestAsk=price + 0.001, spread=0.002)
    return event


def test_refetch_keeps_an_outcome_that_has_since_settled(monkeypatch):
    event = _champion_with_niners_at(0.98)
    assert _refetch(monkeypatch, event, "San Francisco 49ers") == 0.98


def test_refetch_resolves_a_claim_saved_under_the_old_label(monkeypatch):
    event = _champion_with_niners_at(0.145)
    assert _refetch(monkeypatch, copy.deepcopy(event), "the San Francisco 49ers") == 0.145
