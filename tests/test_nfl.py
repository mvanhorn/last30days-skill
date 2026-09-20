"""nfl30 entity resolver, beat-writer roster, and static-table integrity."""

import json
from pathlib import Path

import pytest

from lib import nfl


# === Resolution ===


def test_explicit_team_wins():
    ent = nfl.resolve("some topic", explicit_team="kc")
    assert (ent.kind, ent.team.abbr, ent.how) == ("team", "KC", "explicit")
    ent = nfl.resolve("whatever", explicit_team="Packers")
    assert ent.team.abbr == "GB"


def test_unknown_explicit_team_falls_through_to_topic():
    assert nfl.resolve("Chiefs", explicit_team="ZZZ").team.abbr == "KC"
    assert nfl.resolve("cooking pasta", explicit_team="ZZZ") is None


def test_explicit_player_resolves_team_from_map():
    ent = nfl.resolve("x", explicit_player="Patrick Mahomes")
    assert (ent.kind, ent.person, ent.team.abbr) == ("player", "Patrick Mahomes", "KC")
    ent = nfl.resolve("x", explicit_player="Andy Reid")
    assert (ent.kind, ent.team.abbr) == ("coach", "KC")
    ent = nfl.resolve("x", explicit_player="Unknown Guy")
    assert (ent.kind, ent.person, ent.team) == ("player", "Unknown Guy", None)


def test_abbreviation_and_word_shaped_guard():
    assert nfl.resolve("KC injury report").team.abbr == "KC"
    assert nfl.resolve("GB").team.abbr == "GB"
    assert nfl.resolve("NO saints").team.abbr == "NO"
    assert nfl.resolve("No way this works out for anyone") is None
    assert nfl.resolve("LA") is None


def test_nickname_city_and_alias():
    assert nfl.resolve("Chiefs").team.abbr == "KC"
    assert nfl.resolve("what are the niners doing").team.abbr == "SF"
    assert nfl.resolve("Kansas City Chiefs offense").team.abbr == "KC"
    assert nfl.resolve("Chicago Bears game").team.abbr == "CHI"
    # A bare city needs NFL vocabulary.
    assert nfl.resolve("Chicago restaurants") is None
    assert nfl.resolve("Chicago quarterback").team.abbr == "CHI"
    # Cities shared by two franchises never resolve alone.
    assert nfl.resolve("New York football") is None
    assert nfl.resolve("Los Angeles Rams").team.abbr == "LAR"


def test_game_resolution():
    ent = nfl.resolve("Packers vs Lions")
    assert (ent.kind, ent.team.abbr, ent.opponent.abbr) == ("game", "GB", "DET")
    ent = nfl.resolve("Chiefs at Bills")
    assert (ent.kind, ent.team.abbr, ent.opponent.abbr) == ("game", "KC", "BUF")
    assert nfl.resolve("KC-BUF").kind == "game"
    assert nfl.resolve("Chiefs vs Chiefs") .kind == "team"
    assert nfl.resolve("Packers vs the world").team.abbr == "GB"


def test_player_and_last_name_resolution():
    ent = nfl.resolve("Mahomes injury")
    assert (ent.kind, ent.person, ent.team.abbr, ent.how) == (
        "player", "Patrick Mahomes", "KC", "player_map",
    )
    assert nfl.resolve("Patrick Mahomes").person == "Patrick Mahomes"
    # Unique last name with NFL context.
    assert nfl.resolve("Burrow contract").person == "Joe Burrow"
    # Coach map entries carry kind=coach.
    assert nfl.resolve("Andy Reid presser").kind == "coach"


def test_league_and_none():
    assert nfl.resolve("NFL MVP odds").kind == "league"
    assert nfl.resolve("week 3 power rankings").kind == "league"
    assert nfl.resolve("best pizza in kansas city") is None
    assert nfl.resolve("") is None


def test_entity_dict_roundtrip_shape():
    d = nfl.resolve("Packers vs Lions").as_dict()
    assert d["kind"] == "game"
    assert d["team"]["abbr"] == "GB" and d["opponent"]["abbr"] == "DET"
    assert d["label"] == "Packers vs Lions"
    assert isinstance(d["team"]["aliases"], list)


# === Beat writers ===


def test_beat_handles_caps_by_depth():
    ent = nfl.resolve("Chiefs")
    quick = nfl.beat_handles(ent, depth="quick")
    default = nfl.beat_handles(ent, depth="default")
    deep = nfl.beat_handles(ent, depth="deep")
    # The team's official account rides along uncapped: caps are 2+2 / 6+4 beat/national.
    assert quick[0] == default[0] == deep[0] == "Chiefs"
    assert len(quick) == 5 and len(default) == 11
    assert len(deep) > len(default)
    assert quick[1] == default[1] == "adamteicher"  # team writers next, in roster order
    assert "AdamSchefter" in quick  # national insiders always included
    assert len(set(h.lower() for h in deep)) == len(deep)


def test_beat_handles_game_splits_team_budget_and_league_is_national_only():
    game = nfl.beat_handles(nfl.resolve("Packers vs Lions"), depth="default")
    assert game[:2] == ["packers", "Lions"]  # both official accounts first
    roster = nfl.load_beat_writers()
    gb = {r["handle"].lower() for r in roster["teams"]["GB"]}
    det = {r["handle"].lower() for r in roster["teams"]["DET"]}
    assert any(h.lower() in gb for h in game) and any(h.lower() in det for h in game)
    league = nfl.beat_handles(nfl.resolve("NFL MVP odds"), depth="default")
    national = {r["handle"].lower() for r in roster["national"]}
    assert league and all(h.lower() in national for h in league)
    assert nfl.beat_handles(None) == []


def test_beat_writer_meta_lookup_is_case_insensitive():
    meta = nfl.beat_writer_meta(["ADAMTEICHER", "@RapSheet", "nobody"])
    assert meta["adamteicher"]["team"] == "KC" and meta["adamteicher"]["outlet"]
    assert meta["rapsheet"]["role"] == "insider" and meta["rapsheet"]["team"] is None
    assert "nobody" not in meta
    official = nfl.beat_writer_meta(["Chiefs", "packers"])
    assert official["chiefs"] == {"name": "Kansas City Chiefs", "outlet": "official team account",
                                  "role": "official", "team": "KC"}
    assert official["packers"]["team"] == "GB"


def test_override_merge_replaces_team_unions_national_and_removes(tmp_path):
    override = tmp_path / "beat_writers.json"
    override.write_text(json.dumps({
        "teams": {"kc": [{"handle": "MyLocalGuy", "name": "Local", "outlet": "Blog"}]},
        "national": [{"handle": "AdamSchefter"}, {"handle": "NewInsider", "outlet": "Pod"}],
        "remove": ["RapSheet"],
    }))
    roster = nfl.load_beat_writers(override_path=override)
    assert [r["handle"] for r in roster["teams"]["KC"]] == ["MyLocalGuy"]
    assert len(roster["teams"]["GB"]) > 1  # other teams untouched
    handles = [r["handle"].lower() for r in roster["national"]]
    assert "newinsider" in handles and "rapsheet" not in handles
    assert handles.count("adamschefter") == 1


def test_override_malformed_is_ignored(tmp_path):
    override = tmp_path / "beat_writers.json"
    override.write_text("not json")
    roster = nfl.load_beat_writers(override_path=override)
    assert len(roster["teams"]) == 32


def test_beat_writers_enabled_flag_and_env():
    assert nfl.beat_writers_enabled({})
    assert not nfl.beat_writers_enabled({"_beat_writers": "off"})
    assert nfl.beat_writers_enabled({"_beat_writers": "on", "NFL30_BEAT_WRITERS": "off"})
    assert not nfl.beat_writers_enabled({"NFL30_BEAT_WRITERS": "0"})


def test_default_subreddits():
    assert nfl.default_subreddits(nfl.resolve("Chiefs")) == (["nfl"], ["KansasCityChiefs"])
    broad, dedicated = nfl.default_subreddits(nfl.resolve("Packers vs Lions"))
    assert broad == ["nfl"] and dedicated == ["GreenBayPackers", "detroitlions"]
    assert nfl.default_subreddits(nfl.resolve("NFL MVP odds")) == (["nfl"], [])
    assert nfl.default_subreddits(None) == ([], [])


# === Static table integrity ===


def test_teams_table_has_32_complete_unique_rows():
    teams = nfl.load_teams()
    assert len(teams) == 32
    for field in ("abbr", "pm_abbr", "city", "nickname", "division", "subreddit",
                  "x_handle", "youtube_handle", "domain"):
        assert all(getattr(t, field) for t in teams.values()), field
    for attr in ("subreddit", "nickname", "domain", "pm_abbr"):
        values = [getattr(t, attr).lower() for t in teams.values()]
        assert len(set(values)) == 32, attr
    divisions = {t.division for t in teams.values()}
    assert len(divisions) == 8
    assert all(sum(1 for t in teams.values() if t.division == d) == 4 for d in divisions)
    assert all(t.rss is None or t.rss.startswith("https://") for t in teams.values())


def test_beat_writer_roster_covers_every_team():
    roster = nfl.load_beat_writers()
    teams = nfl.load_teams()
    assert set(roster["teams"]) == set(teams)
    for abbr, rows in roster["teams"].items():
        assert len(rows) >= 4, abbr
        for row in rows:
            assert row["handle"] and row["name"] and row["outlet"], (abbr, row)
            assert not row["handle"].startswith("@")
    assert len(roster["national"]) >= 6


def test_players_map_points_at_real_teams():
    teams = nfl.load_teams()
    for name, row in nfl.load_players().items():
        assert row["team"] in teams, name
        assert row.get("role_kind") in {"player", "coach"}, name


def test_data_files_are_valid_json_with_notes():
    for path in (nfl.TEAMS_FILE, nfl.BEAT_WRITERS_FILE, nfl.PLAYERS_FILE):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data.get("as_of") and data.get("_note")


# === game topics are not comparisons ===


def test_scheduled_game_is_not_a_comparison_but_other_vs_topics_are():
    from lib import planner

    assert planner._comparison_entities("Packers vs Lions") == []
    assert planner._comparison_entities("Packers vs Lions injuries") == []
    assert planner._comparison_entities("Chiefs at Bills") == []  # no vs: never a comparison
    assert len(planner._comparison_entities("Mahomes vs Allen")) == 2
    assert len(planner._comparison_entities("Chiefs vs Bills vs Ravens")) == 3
    assert len(planner._comparison_entities("React vs Vue")) == 2
    assert nfl.resolve("Chiefs vs Bills vs Ravens").kind == "team"
