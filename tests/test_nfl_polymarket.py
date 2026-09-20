"""nfl30 NFL Polymarket lane: game/future/award/roster cards, header line, wiring."""

import json
from datetime import datetime, timezone
from unittest import mock

from lib import nfl, nfl_polymarket as npm, normalize, pipeline, schema

NOW = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
KC = nfl.resolve("Chiefs").as_dict()
GB_NYJ = nfl.resolve("Packers vs Jets").as_dict()
LEAGUE = nfl.resolve("NFL MVP odds").as_dict()


def mk(outcomes, prices, **kw):
    base = {"outcomes": json.dumps(outcomes), "outcomePrices": json.dumps([str(p) for p in prices]),
            "closed": False, "active": True}
    base.update(kw)
    return base


def game_event(slug="nfl-ind-kc-2026-09-21", start="2026-09-21 00:20:00+00", ml=(0.295, 0.705), closed=False):
    return {
        "slug": slug, "title": "Colts vs. Chiefs", "closed": closed, "eventWeek": 2,
        "endDate": "2026-09-21T00:20:00Z", "volume24hr": 434125.0,
        "markets": [
            mk(["Colts", "Chiefs"], list(ml), slug=slug, sportsMarketType="moneyline",
               gameStartTime=start, oneWeekPriceChange=0.05),
            mk(["Chiefs", "Colts"], [0.52, 0.48], sportsMarketType="spreads", line=-3.5,
               question="Spread: Chiefs (-3.5)", gameStartTime=start),
            mk(["Chiefs", "Colts"], [0.455, 0.545], sportsMarketType="spreads", line=-6.5,
               question="Spread: Chiefs (-6.5)", gameStartTime=start),
            mk(["Over", "Under"], [0.49, 0.51], sportsMarketType="totals", line=45.5, gameStartTime=start),
            mk(["Over", "Under"], [0.7, 0.3], sportsMarketType="totals", line=40.5, gameStartTime=start),
        ],
    }


def group_event(title, slug, groups, closed=False, **kw):
    """negRisk-style futures event: one Yes/No market per groupItemTitle."""
    markets = [mk(["Yes", "No"], [p, round(1 - p, 4)], groupItemTitle=g, question=f"Will {g} ...?",
                  oneWeekPriceChange=chg, volumeNum=vol)
               for g, p, chg, vol in groups]
    return {"slug": slug, "title": title, "closed": closed, "endDate": "2027-01-04T00:00:00Z",
            "volume24hr": 1000.0, "markets": markets, **kw}


DIVISION = group_event("Pro Football: AFC West Champion", "pro-football-afc-west-champion",
                       [("Kansas City Chiefs", 0.54, 0.195, 15000), ("Denver Broncos", 0.24, -0.075, 9000)])
POSTSEASON = group_event("Pro Football: Team to Make Postseason", "nfl-team-to-make-postseason",
                         [("Kansas City Chiefs", 0.745, 0.125, 600), ("Buffalo Bills", 0.85, 0.115, 1300)])
AFC = group_event("Pro Football: 2027 AFC Champion", "pro-football-2027-afc-champion",
                  [("Kansas City Chiefs", 0.135, 0.035, 160000), ("Buffalo Bills", 0.205, 0.05, 27000)])
AFC_GAME = group_event("Pro Football: AFC Team to advance to AFC Championship Game", "x-afc-champ-game",
                       [("Kansas City Chiefs", 0.21, 0.0, 10)])
CHAMP = group_event("Pro Football: 2027 Champion", "pro-football-2027-champion-20260729",
                    [("Buffalo Bills", 0.12, 0.035, 5), ("Kansas City Chiefs", 0.074, 0.025, 4)])
MVP = group_event("Pro Football: 2026 MVP Winner", "pro-football-2026-mvp-winner",
                  [("Josh Allen", 0.295, 0.18, 43000), ("Patrick Mahomes", 0.045, 0.0, 9000),
                   ("Lamar Jackson", 0.12, -0.01, 52000), ("Travis Kelce", 0.001, 0.0, 1)])
OLD_MVP = group_event("NFL MVP", "nfl-mvp-355", [("Matthew Stafford", 1.0, 0.1, 100)], closed=True)


# === parsers ===


def test_season_year_rolls_over_in_march():
    assert npm.season_year(datetime(2026, 9, 20, tzinfo=timezone.utc)) == 2026
    assert npm.season_year(datetime(2027, 2, 14, tzinfo=timezone.utc)) == 2026
    assert npm.season_year(datetime(2027, 3, 1, tzinfo=timezone.utc)) == 2027


def test_market_outcomes_and_yes_price():
    m = mk(["Yes", "No"], [0.54, 0.46])
    assert npm.market_outcomes(m) == [("Yes", 0.54), ("No", 0.46)]
    assert npm.yes_price(m) == 0.54
    assert npm.yes_price(mk(["Colts", "Chiefs"], [0.3, 0.7])) == 0.3  # falls back to first outcome
    assert npm.market_outcomes({"outcomes": "junk", "outcomePrices": None}) == []


def test_parse_game_start_reads_market_time():
    dt = npm.parse_game_start(game_event())
    assert dt == datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    assert npm.parse_game_start({"markets": [{"gameStartTime": "garbage"}]}) is None


# === games ===


def test_select_game_events_matches_pm_abbr_and_filters():
    events = [
        game_event(),
        game_event("nfl-kc-mia-2026-09-27", "2026-09-27 17:00:00+00"),
        game_event("nfl-gb-nyj-2026-09-20", "2026-09-20 17:00:00+00"),         # other teams
        game_event("nfl-den-kc-2026-09-14", "2026-09-14 00:15:00+00"),          # >6h ago
        game_event("nfl-lac-kc-2026-10-18", "2026-10-18 17:00:00+00", closed=True),
        game_event("nfl-ind-kc-2026-09-21-player-props"),                        # not a moneyline slug
    ]
    picked = npm.select_game_events(events, KC, NOW)
    assert [e["slug"] for e, _ in picked] == ["nfl-ind-kc-2026-09-21", "nfl-kc-mia-2026-09-27"]


def test_select_game_events_exact_pairing_for_game_entity_and_rams_abbr():
    events = [game_event("nfl-gb-nyj-2026-09-20", "2026-09-20 17:00:00+00"),
              game_event("nfl-gb-min-2026-09-27", "2026-09-27 17:00:00+00")]
    assert [e["slug"] for e, _ in npm.select_game_events(events, GB_NYJ, NOW)] == ["nfl-gb-nyj-2026-09-20"]
    rams = nfl.resolve("Rams").as_dict()
    la = game_event("nfl-nyg-la-2026-09-22", "2026-09-22 00:15:00+00")
    assert len(npm.select_game_events([la], rams, NOW)) == 1  # Polymarket says "la", not "lar"


def test_build_game_cards_prices_lines_and_live_flag():
    cards = npm.build_game_cards([game_event()], KC, NOW)
    assert len(cards) == 1
    card = cards[0]
    assert (card["team"], card["opp"], card["team_prob"], card["opp_prob"]) == ("Chiefs", "Colts", 0.705, 0.295)
    # Team is outcome index 1, so the outcome-0 weekly change flips sign.
    assert card["week_change"] == -0.05
    assert card["spread"] == "Chiefs (-3.5)"   # closest to 50%
    assert card["total"] == 45.5
    assert card["live"] is False and card["week"] == 2
    live = npm.build_game_cards([game_event("nfl-ind-kc-2026-09-20", "2026-09-20 17:00:00+00")], KC, NOW)
    assert live[0]["live"] is True


def test_build_game_cards_keeps_next_game_only_for_team():
    events = [game_event(), game_event("nfl-kc-mia-2026-09-27", "2026-09-27 17:00:00+00")]
    assert [c["event_slug"] for c in npm.build_game_cards(events, KC, NOW)] == ["nfl-ind-kc-2026-09-21"]
    assert npm.build_game_cards([], KC, NOW) == []
    assert npm.build_game_cards([game_event()], LEAGUE, NOW) == []


# === futures ===


def test_future_kind_distinguishes_champion_from_championship_game():
    assert npm._future_kind("Pro Football: 2027 Champion") == ("champion", "Champion")
    assert npm._future_kind("Pro Football: 2027 AFC Champion ") == ("conference", "AFC")
    assert npm._future_kind("Pro Football: AFC West Champion") == ("division", "AFC West")
    assert npm._future_kind("Pro Football: Team to Make Postseason") == ("playoffs", "Playoffs")
    assert npm._future_kind("Pro Football: AFC Team to advance to AFC Championship Game") is None
    assert npm._future_kind("Pro Football: 2026 MVP Winner") is None


def test_build_future_cards_for_team_skips_other_divisions_and_non_futures():
    other_div = group_event("Pro Football: NFC East Champion", "pro-football-nfc-east-champion",
                            [("Kansas City Chiefs", 0.01, 0.0, 1)])  # bogus but must be ignored
    cards = npm.build_future_cards([DIVISION, POSTSEASON, AFC, AFC_GAME, CHAMP, other_div, OLD_MVP], KC)
    by_type = {c["market_type"]: c for c in cards}
    assert set(by_type) == {"division", "playoffs", "conference", "champion"}
    assert by_type["division"]["team_prob"] == 0.54 and by_type["division"]["week_change"] == 0.195
    assert by_type["conference"]["label"] == "AFC" and by_type["champion"]["team_prob"] == 0.074
    assert all(c["team"] == "Chiefs" for c in cards)


def test_build_future_cards_league_takes_top_n_of_champion_and_conference_only():
    cards = npm.build_future_cards([CHAMP, AFC, DIVISION, POSTSEASON], LEAGUE, league_top=1)
    assert {(c["market_type"], c["team"]) for c in cards} == {
        ("champion", "Buffalo Bills"), ("conference", "Buffalo Bills")}


def test_build_win_total_prefers_traded_lines():
    ev = {"slug": "pro-football-kansas-city-chiefs-2026-win-total", "title": "Pro Football: Chiefs 2026 Win Total",
          "closed": False, "endDate": "2027-02-16T00:00:00Z", "markets": [
              mk(["Yes", "No"], [0.5, 0.5], groupItemTitle="12.5+ Wins", oneWeekPriceChange=None),
              mk(["Yes", "No"], [0.515, 0.485], groupItemTitle="9.5+ Wins", oneWeekPriceChange=0.015),
              mk(["Yes", "No"], [0.7, 0.3], groupItemTitle="7.5+ Wins", oneWeekPriceChange=0.02)]}
    (card,) = npm.build_win_total_cards([ev], KC)
    assert card["label"] == "9.5+ Wins" and card["team_prob"] == 0.515 and card["market_type"] == "win_total"
    assert npm.build_win_total_cards([ev], nfl.resolve("Bills").as_dict()) == []


# === awards and roster ===


def test_build_award_cards_team_players_person_and_league():
    team_cards = npm.build_award_cards([MVP, OLD_MVP], KC)
    assert [(c["team"], c["label"]) for c in team_cards] == [("Patrick Mahomes", "MVP"), ("Travis Kelce", "MVP")]
    person = nfl.resolve("Josh Allen").as_dict()
    assert [c["team"] for c in npm.build_award_cards([MVP], person)] == ["Josh Allen"]
    league = npm.build_award_cards([MVP], LEAGUE, top_league=2)
    assert [c["team"] for c in league] == ["Josh Allen", "Lamar Jackson"]


def test_build_roster_cards_filters_noise_and_orders_by_volume():
    vrabel = {"slug": "mike-vrabel-out", "title": "Mike Vrabel out as Patriots Head Coach by Dec 31, 2026?",
              "closed": False, "volume24hr": 50.0, "markets": [mk(["Yes", "No"], [0.0345, 0.9655], groupItemTitle="",
                                                                   oneWeekPriceChange=-0.0255, question="Vrabel out?")]}
    qb = {"slug": "pats-qb", "title": "Patriots starting QB in 2026?", "closed": False, "volume24hr": 500.0,
          "markets": [mk(["Yes", "No"], [0.9, 0.1], groupItemTitle="Drake Maye"),
                      mk(["Yes", "No"], [0.05, 0.95], groupItemTitle="Someone Else")]}
    noise = [
        {"slug": "s1", "title": "Pro Football: Patriots vs. Jets Season Series Winner", "closed": False, "markets": [mk(["Yes", "No"], [.5, .5])]},
        {"slug": "s2", "title": "What will the announcers say during the Patriots game?", "closed": False, "markets": [mk(["Yes", "No"], [.5, .5])]},
        {"slug": "s3", "title": "Broncos head coach fired?", "closed": False, "markets": [mk(["Yes", "No"], [.5, .5])]},  # wrong team
        {"slug": "nfl-ne-nyj-2026-09-27", "title": "Patriots trade", "closed": False, "markets": [mk(["Yes", "No"], [.5, .5])]},
    ]
    pats = nfl.resolve("Patriots").as_dict()
    cards = npm.build_roster_cards([vrabel, qb, *noise], pats, cap=5)
    assert [c["event_slug"] for c in cards] == ["pats-qb", "mike-vrabel-out"]
    assert cards[0]["outcomes"][0] == ("Drake Maye", 0.9)
    assert npm.build_roster_cards([vrabel], pats, cap=0) == []
    assert npm.build_roster_cards([vrabel], LEAGUE, cap=5) == []


# === search orchestration ===


def _fake_search(query, limit=10, status="active"):
    if query.endswith(" vs"):
        return [game_event()], None
    if "champion" in query:
        return [CHAMP], None
    return [], None


def _fake_slug(slug):
    table = {"pro-football-afc-west-champion": DIVISION, "nfl-team-to-make-postseason": POSTSEASON,
             "pro-football-2027-afc-champion": AFC, "pro-football-2026-mvp-winner": MVP}
    return table.get(slug), None


def test_search_nfl_markets_buckets_team_run():
    with mock.patch.object(npm, "search_events", side_effect=_fake_search), \
         mock.patch.object(npm, "event_by_slug", side_effect=_fake_slug):
        result = npm.search_nfl_markets(KC, "2026-09-13", "2026-09-20", "default", now=NOW)
    buckets = {}
    for c in result["cards"]:
        buckets.setdefault(c["bucket"], []).append(c["market_type"])
    assert buckets["game"] == ["moneyline"]
    assert sorted(buckets["future"]) == ["champion", "conference", "division", "playoffs"]
    assert buckets["award"] == ["award", "award"]
    assert result["errors"] == []


def test_search_nfl_markets_quick_skips_team_search_and_awards():
    calls = []
    def rec_search(q, limit=10, status="active"):
        calls.append(q); return _fake_search(q, limit, status)
    with mock.patch.object(npm, "search_events", side_effect=rec_search), \
         mock.patch.object(npm, "event_by_slug", side_effect=_fake_slug):
        result = npm.search_nfl_markets(KC, "a", "b", "quick", now=NOW)
    assert calls == ["Chiefs vs"]
    assert not any(c["bucket"] == "award" for c in result["cards"])


def test_search_nfl_markets_league_uses_futures_and_awards_only():
    with mock.patch.object(npm, "search_events", side_effect=_fake_search), \
         mock.patch.object(npm, "event_by_slug", side_effect=_fake_slug):
        result = npm.search_nfl_markets(LEAGUE, "a", "b", "default", now=NOW)
    assert {c["bucket"] for c in result["cards"]} == {"future", "award"}


def test_search_nfl_markets_reports_errors_without_raising():
    with mock.patch.object(npm, "search_events", return_value=([], "URLError: down")), \
         mock.patch.object(npm, "event_by_slug", return_value=(None, "HTTPError: 500")):
        result = npm.search_nfl_markets(KC, "a", "b", "default", now=NOW)
    assert result["cards"] == [] and result["errors"] and "error" in result
    assert npm.search_nfl_markets({}, "a", "b")["error"] == "no NFL entity resolved"


def test_http_helpers_never_raise():
    with mock.patch.object(npm.http, "get", side_effect=RuntimeError("boom")):
        assert npm.search_events("chiefs")[0] == []
        assert npm.event_by_slug("x")[0] is None
    with mock.patch.object(npm.http, "get", return_value=[{"slug": "x"}]):
        assert npm.event_by_slug("x")[0] == {"slug": "x"}


# === items and header line ===


def _run_items():
    with mock.patch.object(npm, "search_events", side_effect=_fake_search), \
         mock.patch.object(npm, "event_by_slug", side_effect=_fake_slug):
        result = npm.search_nfl_markets(KC, "a", "b", "default", now=NOW)
    return npm.parse_nfl_polymarket_response(result, "Chiefs", today="2026-09-20")


def test_parse_items_shape_and_headlines():
    items = _run_items()
    game = next(i for i in items if i["bucket"] == "game")
    assert game["title"].startswith("Colts vs. Chiefs: Chiefs 70% to win (Sun 9/20)")  # 00:20Z is Sunday 8:20pm ET; 70.5% rounds to 70
    assert "spread Chiefs (-3.5)" in game["text"] and "total 45.5" in game["text"]
    assert game["engagement"]["volume"] == 434125.0 and game["date"] == "2026-09-20"
    fut = next(i for i in items if i["market_type"] == "division")
    assert fut["title"] == "Chiefs: AFC West 54%" and "▲19.5 7d" in fut["text"]


def test_format_market_says_line_exact():
    line = npm.format_market_says_line(_run_items())
    assert line == (
        "📊 Market says: Chiefs 70% vs Colts (Sun 9/20), Chiefs (-3.5) │ Champion 7.4% (▲2.5 7d) │ "
        "AFC 14% (▲3.5 7d) │ AFC West 54% (▲19.5 7d) │ Playoffs 74% (▲12.5 7d) │ "
        "MVP: Patrick Mahomes 4.5% │ MVP: Travis Kelce 0.1%"
    )
    assert npm.format_market_says_line([]) is None


def test_format_line_names_teams_when_multiple_and_flags_live():
    live = game_event("nfl-gb-nyj-2026-09-20", "2026-09-20 17:00:00+00", ml=(0.99, 0.01))
    live["title"] = "Packers vs. Jets"
    live["markets"][0]["outcomes"] = json.dumps(["Packers", "Jets"])
    for m in live["markets"][1:3]:
        m["outcomes"] = json.dumps(["Packers", "Jets"])
    gb_div = group_event("Pro Football: NFC North Champion", "d1", [("Green Bay Packers", 0.24, 0.0, 1)])
    nyj_div = group_event("Pro Football: AFC East Champion", "d2", [("New York Jets", 0.056, 0.018, 1)])
    cards = npm.build_game_cards([live], GB_NYJ, NOW) + npm.build_future_cards([gb_div, nyj_div], GB_NYJ)
    line = npm.format_market_says_line(npm.parse_nfl_polymarket_response({"cards": cards}, today="2026-09-20"))
    assert "Packers 99% vs Jets (Sun 9/20, live)" in line
    assert "Packers NFC North 24%" in line and "Jets AFC East 5.6% (▲1.8 7d)" in line


def test_change_string_hides_sub_point_moves():
    assert npm._change_str(0.004) == "" and npm._change_str(None) == ""
    assert npm._change_str(-0.012) == " (▼1.2 7d)"
    assert npm._pct(0.005) == "0.5%" and npm._pct(0.0) == "0%" and npm._pct(0.74) == "74%"


# === pipeline / normalize wiring ===


def test_available_sources_league_has_markets_but_not_team_official():
    avail = pipeline.available_sources({"_nfl": LEAGUE}, local_only=True)
    assert "nfl_polymarket" in avail and "team_official" not in avail
    avail = pipeline.available_sources({"_nfl": KC}, local_only=True)
    assert {"nfl_polymarket", "team_official"} <= set(avail)
    assert "nfl_polymarket" not in pipeline.available_sources({"_nfl": None}, local_only=True)


def test_retrieve_stream_dispatch_and_skip():
    sq = schema.SubQuery(label="p", search_query="Chiefs", ranking_query="Chiefs", sources=["nfl_polymarket"], weight=1.0)
    common = dict(topic="Chiefs", subquery=sq, source="nfl_polymarket", depth="quick",
                  date_range=("2026-09-13", "2026-09-20"), runtime=None, mock=False)
    items, art = pipeline._retrieve_stream_impl(config={"_nfl": None}, **common)
    assert items == [] and art["_source_outcome"]["state"] == schema.SKIPPED_UNCONFIGURED
    fake = {"cards": [{"bucket": "future", "market_type": "division", "label": "AFC West", "team": "Chiefs",
                       "team_prob": 0.54, "title": "Pro Football: AFC West Champion", "event_slug": "d", "url": "u"}]}
    with mock.patch.object(npm, "search_nfl_markets", return_value=fake) as fn:
        items, art = pipeline._retrieve_stream_impl(config={"_nfl": KC}, **common)
    assert fn.call_args.args[0] == KC and len(items) == 1 and not art


def test_normalizer_keeps_card_fields_in_metadata():
    raw = _run_items()
    out = normalize.normalize_source_items("nfl_polymarket", raw, "2026-09-13", "2026-09-20", "strict_recent")
    game = next(i for i in out if i.metadata["bucket"] == "game")
    assert game.container == "Polymarket (NFL)" and game.metadata["team_prob"] == 0.705
    assert game.engagement["volume"] == 434125.0
    assert any(i.metadata["market_type"] == "champion" for i in out)
