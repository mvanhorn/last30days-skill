"""Regression set for Polymarket entity market selection, on live data.

``fixtures/polymarket/regression`` holds live Gamma ``public-search`` events
captured on 2026-11-16 for 25 research topics and 3 negative controls.
``events.json`` maps an event key to the event, trimmed to the fields the
parser reads (a market that does not name the subject keeps only its price
fields). ``topics.json`` lists each topic's events in search order. Events
that name nothing for a topic were dropped, and so was each topic's long tail
below its tenth-ranked event, except ground-truth events. An event captured at
two moments with different prices is stored once per capture (``"<id>.2"``).

Ground truth was labelled by hand after seeing which markets exist and before
scoring: the event-title prefixes a knowledgeable fan would want shown, the
first group being the headline question. Hints are what the host planner would
pass as ``polymarket_hints``. The negative controls are topics whose saved runs
showed only off-topic markets.
"""

import json
from pathlib import Path

import pytest

from lib import polymarket

FIXTURES = Path(__file__).parent / "fixtures" / "polymarket" / "regression"
TO_DATE = "2026-11-16"

TOP1_THRESHOLD = 22
TOP3_THRESHOLD = 23

_WARRIORS = ["Stephen Curry", "Draymond Green", "Jimmy Butler", "Yaxel Lendeborg", "Jonathan Kuminga"]
_GRIZZLIES = ["Ja Morant", "Jaren Jackson Jr.", "Ty Jerome", "Zach Edey", "Santi Aldama"]

HINTS = {
    "49ers": {"kind": "team", "aliases": ["San Francisco 49ers"]},
    "Benfica": {"kind": "team"},
    "Lakers": {"kind": "team", "aliases": ["Los Angeles Lakers"]},
    "Real Madrid": {"kind": "team"},
    "Formula 1": {"aliases": ["F1"]},
    "Anthropic": {"kind": "company"},
    "OpenAI": {"kind": "company"},
    "Nvidia": {"kind": "company"},
    "Microsoft": {"kind": "company"},
    "Apple": {"kind": "company"},
    "Tesla": {"kind": "company"},
    "Palantir": {"kind": "company"},
    "Netflix": {"kind": "company"},
    "Waymo": {"kind": "company"},
    "Elon Musk": {"kind": "person"},
    "Jensen Huang": {"kind": "person"},
    "Taylor Swift": {"kind": "person"},
    "Kanye West": {"kind": "person"},
    "Bitcoin": {"kind": "other"},
    "Claude Code": {"kind": "product", "aliases": ["Claude"]},
    "Gavin Newsom": {"kind": "person"},
    "Ukraine": {"kind": "other"},
    "Iran": {"kind": "other"},
    "Warriors": {
        "kind": "team", "aliases": ["Golden State Warriors"],
        "related": [{"name": name, "relation": "player"} for name in _WARRIORS],
    },
    "Memphis Grizzlies": {
        "kind": "team", "aliases": ["Grizzlies"],
        "related": [{"name": name, "relation": "player"} for name in _GRIZZLIES],
    },
}

GROUND_TRUTH = {
    "49ers": ["Pro Football: 2027 Champion", "Pro Football: 2027 NFC Champion", "Pro Football: NFC West Champion"],
    "Benfica": ["Primeira Liga: 2027 Champion", "UEFA Europa League: 2027 Champion", "UEFA Europa League: League Phase Winner"],
    "Lakers": ["NBA: 2027 Champion", "NBA: 2027 Western Conference Champion", "NBA: Team to Make Playoffs"],
    "Real Madrid": ["UEFA Champions League: 2027 Champion", "LALIGA: 2027 Champion"],
    "Formula 1": ["Las Vegas Grand Prix: Driver Winner", "F1 Drivers' Champion: 2nd Place"],
    "Anthropic": ["Anthropic IPO Closing Market Cap", "Which company has best AI model end of 2026", "Will Anthropic’s valuation hit"],
    "OpenAI": ["OpenAI IPO closing market cap above", "Which company has best AI model end of 2026", "OpenAI IPO by"],
    "Nvidia": ["Next CEO of NVIDIA", "Largest Company end of December 2026", "Nvidia’s Market Cap end of 2026"],
    "Microsoft": ["Largest Company end of December 2026", "3rd Largest Company end of December 2026", "2nd Largest Company end of December 2026"],
    "Apple": ["Largest Company end of December 2026", "Apple’s Market Cap end of 2026"],
    "Tesla": ["Tesla and SpaceX merger officially announced", "How many Tesla deliveries in Q4 2026", "Will Tesla sell a Cybercab"],
    "Palantir": ["What will Palantir Technologies Inc. (PLTR) hit in November 2026", "Will Palantir (PLTR) close above ___ end of November"],
    "Netflix": ["What will Netflix, Inc. (NFLX) hit in November 2026", "What will be the top global Netflix movie this week"],
    "Waymo": ["How many cities will Waymo operate in", "Which cities will Waymo launch in"],
    "Elon Musk": ["Elon Musk Net Worth on November 30", "Richest person on December 31, 2026", "Tesla and SpaceX merger officially announced"],
    "Jensen Huang": ["Jensen Huang rejoins NVIDIA", "Next CEO of NVIDIA", "Where will Jensen Huang work next"],
    "Taylor Swift": ["Grammys 2027: Album of the Year Winner", "Top Spotify Artist 2026", "Taylor Swift pregnant by"],
    "Kanye West": ["Will Kanye West and Bianca Censori separate", "Who will be in the 2027 Big Game halftime show"],
    "Bitcoin": ["What price will Bitcoin hit in 2026", "What price will Bitcoin hit in November"],
    "Claude Code": ["Next Claude Opus (5.6+) released by", "Claude 6 released by", "Which company has the best Code Arena WebDev AI model end of"],
    "Gavin Newsom": ["Democratic Presidential Nominee 2028", "Presidential Election Winner 2028"],
    "Ukraine": ["Russia x Ukraine ceasefire agreement by", "Ukraine signs peace deal with Russia by", "Zelenskyy out as Ukraine president"],
    "Iran": ["Will the U.S. invade Iran before 2027", "US-Iran Final Nuclear Deal", "Iran leader end of 2026", "US announces end of Iranian blockade"],
    "Warriors": ["NBA: 2027 Champion", "NBA: 2026-27 Rookie of the Year", "NBA: 2027 Western Conference Champion", "NBA: Team to Make Playoffs"],
    "Memphis Grizzlies": ["NBA: 2027 Champion", "NBA: 2026-27 Most Improved Player", "NBA: 2027 Western Conference Champion", "NBA: Team to Make Playoffs"],
}

NEGATIVE_CONTROLS = {
    "June Oven": {"kind": "product"},
    "Fox One": {"kind": "product"},
    "Nothing Android": {"kind": "product"},
}

BEST_AI_MODEL = "Which company has best AI model end of 2026"
CLAUDE_RELEASES = ("Next Claude Opus (5.6+) released", "Claude 6 released by")


@pytest.fixture(scope="module")
def runs():
    events = json.loads((FIXTURES / "events.json").read_text())
    topics = json.loads((FIXTURES / "topics.json").read_text())
    out = {}
    for topic, hints in {**HINTS, **NEGATIVE_CONTROLS}.items():
        out[topic] = polymarket.parse_polymarket_response(
            {"events": [events[key] for key in topics[topic]]},
            topic, hints=hints, to_date=TO_DATE,
        )
    return out


def _is_ground_truth(topic, title):
    title = title.strip().casefold()
    return any(title.startswith(prefix.casefold()) for prefix in GROUND_TRUTH[topic])


def _titles(items, n=3):
    return [item["title"].strip() for item in items[:n]]


def _top(runs, topic, title_prefix, n=3):
    return next(
        (item for item in runs[topic][:n] if item["title"].startswith(title_prefix)), None,
    )


def _misses(runs, k):
    return {
        topic: _titles(runs[topic])
        for topic in GROUND_TRUTH
        if not any(_is_ground_truth(topic, title) for title in _titles(runs[topic], k))
    }


def test_every_topic_has_fixture_events():
    topics = json.loads((FIXTURES / "topics.json").read_text())
    assert set(topics) == set(HINTS) | set(NEGATIVE_CONTROLS)
    assert all(topics[topic] for topic in topics)


def test_top_event_matches_ground_truth(runs):
    misses = _misses(runs, 1)
    hits = len(GROUND_TRUTH) - len(misses)
    assert hits >= TOP1_THRESHOLD, f"top-1 {hits}/{len(GROUND_TRUTH)}; misses: {misses}"


def test_ground_truth_in_top_three(runs):
    misses = _misses(runs, 3)
    hits = len(GROUND_TRUTH) - len(misses)
    assert hits >= TOP3_THRESHOLD, f"top-3 {hits}/{len(GROUND_TRUTH)}; misses: {misses}"


def test_49ers_lead_with_the_champion_market(runs):
    first = runs["49ers"][0]
    assert first["title"].startswith("Pro Football: 2027 Champion")
    assert "San Francisco 49ers" in first["headline"]["question"]
    assert first["matched_market"]["label"] == "San Francisco 49ers"


def test_warriors_top_three_has_title_odds_and_lendeborg(runs):
    champion = _top(runs, "Warriors", "NBA: 2027 Champion")
    rookie = _top(runs, "Warriors", "NBA: 2026-27 Rookie of the Year")
    assert champion is not None, _titles(runs["Warriors"])
    assert rookie is not None, _titles(runs["Warriors"])
    assert rookie["headline"] is not None and "Yaxel Lendeborg" in rookie["headline"]["question"]
    assert rookie["matched_market"]["related_name"] == "Yaxel Lendeborg"


def test_grizzlies_top_three_has_title_odds_and_jerome(runs):
    champion = _top(runs, "Memphis Grizzlies", "NBA: 2027 Champion")
    improved = _top(runs, "Memphis Grizzlies", "NBA: 2026-27 Most Improved Player")
    assert champion is not None, _titles(runs["Memphis Grizzlies"])
    assert improved is not None, _titles(runs["Memphis Grizzlies"])
    assert improved["matched_market"]["related_name"] == "Ty Jerome"
    assert improved["headline"] is not None and "Ty Jerome" in improved["headline"]["question"]


def test_claude_code_top_three_are_claude_model_markets_led_by_a_release(runs):
    titles = _titles(runs["Claude Code"])
    assert len(titles) == 3
    assert titles[0].startswith(CLAUDE_RELEASES), titles
    assert all("Claude" in title and "Giroux" not in title for title in titles), titles


@pytest.mark.parametrize("topic", ["Anthropic", "OpenAI"])
def test_ai_labs_show_the_best_ai_model_market(runs, topic):
    titles = [item["title"] for item in runs[topic][: polymarket.RESULT_CAP["default"]]]
    assert any(title.startswith(BEST_AI_MODEL) for title in titles), titles


@pytest.mark.parametrize("topic", sorted(NEGATIVE_CONTROLS))
def test_negative_controls_show_no_markets(runs, topic):
    assert runs[topic] == []
