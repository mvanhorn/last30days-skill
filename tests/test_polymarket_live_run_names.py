"""Polymarket names from five live runs on 2026-11-10.

Footer lines those runs printed:

    Anthropic's market cap be between $2.0T 42%, Anthropic IPO 97%,
        Anthropic's valuation hit (HIGH) $3.0T 70%
    Waymo operate in 12-15 cities on 79%, Waymo launch in Denver, CO by 74%
    Jensen Huang announced resignation or removal by December 31, 2027 38%,
        Jensen Huang divorce by March 31, 2027 18%,
        WTT Contender: Anders Lind vs Youzheng: Youzheng Huang 50%

and evidence lines in the saved files:

    Odds: Waymo: 79% | Waymo: 12% | Waymo: 4.6%
    Odds: Anthropic's market cap: 42% | Anthropic's market cap: 24% | ...

The events below are those events as the Gamma API returns them: titles,
questions, sub-market labels (``groupItemTitle``) and end dates as served,
prices as the saved files hold them, cut to a few sub-markets each.
"""

import pytest

from lib import normalize, polymarket, render

TODAY = "2026-11-10"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(polymarket, "_today", lambda: TODAY, raising=False)


def _market(question, label, yes, volume, end="2027-01-01"):
    return {
        "question": question, "groupItemTitle": label,
        "outcomes": '["Yes", "No"]', "outcomePrices": f'["{yes}", "{round(1 - yes, 4)}"]',
        "volume": volume, "liquidity": 5000, "endDate": f"{end}T00:00:00Z",
        "active": True, "closed": False,
    }


def _event(event_id, title, markets, slug="event"):
    return {
        "id": event_id, "title": title, "slug": slug, "active": True, "closed": False,
        "volume1mo": sum(float(market.get("volume") or 0) for market in markets),
        "liquidity": 100000, "updatedAt": "2026-11-11T00:00:00Z", "markets": markets,
    }


CITIES = "Will Waymo operate in {} cities on December 31 2026?"
WAYMO_CITIES = _event("638395", "How many cities will Waymo operate in by December 31?", [
    _market(CITIES.format("fewer than 12"), "<12", 0.046, 43441),
    _market(CITIES.format("12-15"), "12-15", 0.79, 72439),
    _market(CITIES.format("16-19"), "16-19", 0.12, 55018),
    _market(CITIES.format("20-23"), "20-23", 0.03, 25429),
])
LAUNCH = "Will Waymo launch in {} by December 31 2026?"
WAYMO_LAUNCH = _event("638245", "Which cities will Waymo launch in by December 31?", [
    _market(LAUNCH.format("Denver, CO"), "Denver, CO", 0.74, 13209),
    _market(LAUNCH.format("Las Vegas, NV"), "Las Vegas, NV", 0.70, 12360),
    _market(LAUNCH.format("Detroit, MI"), "Detroit, MI", 0.08, 5812),
    _market(LAUNCH.format("Sacramento, CA"), "Sacramento, CA", 0.065, 12707),
])
CAP = "Will Anthropic's market cap be {} at market close on IPO day?"
ANTHROPIC_CAP = _event("548858", "Anthropic IPO Closing Market Cap", [
    _market(CAP.format("less than $1.25T"), "<$1.25T", 0.24, 598704, "2028-01-01"),
    _market(CAP.format("between $1.25T and $1.5T"), "$1.25–$1.5T", 0.42, 460473, "2028-01-01"),
    _market(CAP.format("between $1.5T and $1.75T"), "$1.5–$1.75T", 0.21, 576313, "2028-01-01"),
    # The busiest sub-market, so the event's own question: a 2% bracket.
    _market(CAP.format("between $2.0T and $2.25T"), "$2.0–$2.25T", 0.02, 938512, "2028-01-01"),
])
HIT = "Will Anthropic's valuation hit (HIGH) {} by December 31?"
ANTHROPIC_HIT = _event("500753", "Will Anthropic’s valuation hit __ by December 31?", [
    _market(HIT.format("$3.0T"), "↑$3.0T", 0.10, 1828316, "2027-01-05"),
    _market(HIT.format("$2.0T"), "↑$2.0T", 0.28, 1054604, "2027-01-05"),
    _market(HIT.format("$1.5T"), "↑$1.5T", 0.70, 461457, "2027-01-05"),
])
RESIGN = "Jensen Huang announced resignation or removal by {}?"
JENSEN_RESIGN = _event("1343940", "Jensen Huang announced resignation or removal by...?", [
    _market(RESIGN.format("December 31, 2026"), "December 31, 2026", 0.16, 743121, "2027-01-01"),
    _market(RESIGN.format("March 31, 2027"), "March 31, 2027", 0.24, 95669, "2027-04-01"),
    _market(RESIGN.format("December 31, 2027"), "December 31, 2027", 0.38, 10374, "2028-01-01"),
])
DIVORCE = "Jensen Huang divorce by {}?"
JENSEN_DIVORCE = _event("1344318", "Jensen Huang divorce by...?", [
    _market(DIVORCE.format("December 31, 2026"), "December 31, 2026", 0.095, 150000, "2027-01-01"),
    _market(DIVORCE.format("March 31, 2027"), "March 31, 2027", 0.18, 80000, "2027-04-01"),
])
TABLE_TENNIS = _event("1358638", "WTT Contender: Anders Lind vs Youzheng Huang", [{
    "question": "WTT Contender: Anders Lind vs Youzheng Huang",
    "outcomes": '["Youzheng Huang", "Anders Lind"]', "outcomePrices": '["0.5", "0.5"]',
    "volume": 0, "liquidity": 42.7597, "endDate": "2026-11-19T00:00:00Z",
    "active": True, "closed": False,
}])
IPO_ON = "Will Anthropic IPO on {}?"
ANTHROPIC_IPO_DATE = _event("1262075", "Anthropic IPO Date", [
    _market(IPO_ON.format("November 19, 2026"), "November 19", 0.15, 9000, "2026-11-20"),
    _market(IPO_ON.format("November 20, 2026"), "November 20", 0.72, 8000, "2026-11-21"),
])


def _parsed(topic, *events):
    return polymarket.parse_polymarket_response({"events": list(events)}, topic=topic)


def _items(topic, *events):
    return normalize.normalize_source_items(
        "polymarket", _parsed(topic, *events), "2026-10-12", "2026-11-11",
    )


def _footer(topic, *events):
    return render._polymarket_top_markets(_items(topic, *events), limit=3, topic=topic)


# --- Evidence lines: each outcome keeps its own label ---


def test_bracket_sub_markets_keep_their_own_labels():
    names = [name for name, _price in _parsed("Waymo", WAYMO_CITIES)[0]["outcome_prices"]]
    assert names == ["12-15", "16-19", "<12"]


def test_city_and_price_sub_markets_keep_their_own_labels():
    launch = _parsed("Waymo", WAYMO_LAUNCH)[0]["outcome_prices"]
    cap = _parsed("Anthropic", ANTHROPIC_CAP)[0]["outcome_prices"]
    assert launch == [("Denver, CO", 0.74), ("Las Vegas, NV", 0.70), ("Detroit, MI", 0.08)]
    assert [name for name, _price in cap] == ["$1.25–$1.5T", "<$1.25T", "$1.5–$1.75T"]


def test_saved_file_odds_line_names_each_outcome():
    text = render.render_full(_report("Waymo", WAYMO_CITIES))
    assert "  Odds: 12-15: 79% | 16-19: 12% | <12: 4.6%" in text
    assert "Waymo: 79%" not in text


def test_sub_market_with_no_label_of_its_own_is_named_by_its_question():
    event = _event("1", "How many cities will Waymo operate in by December 31?", [
        {**_market(CITIES.format("12-15"), "", 0.79, 100)},
        {**_market(CITIES.format("16-19"), "", 0.12, 90)},
    ])
    names = [name for name, _price in _parsed("Waymo", event)[0]["outcome_prices"]]
    assert names == [
        "Will Waymo operate in 12-15 cities on December 31 2026",
        "Will Waymo operate in 16-19 cities on December 31 2026",
    ]


def test_subjects_that_tell_the_outcomes_apart_are_kept():
    event = _event("2", "2027 NCAA Tournament Winner", [
        _market("Will Arizona win the 2027 NCAA Tournament?", "Arizona", 0.2, 100),
        _market("Will Duke win the 2027 NCAA Tournament?", "Duke", 0.3, 90),
    ])
    assert _parsed("NCAA Tournament", event)[0]["outcome_prices"] == [("Duke", 0.3), ("Arizona", 0.2)]


# --- The footer: whole phrases, and the price of the market that is named ---


def test_waymo_footer_names_end_with_their_bracket_place_and_date():
    assert _footer("Waymo", WAYMO_CITIES, WAYMO_LAUNCH) == [
        "Waymo operate in 12-15 cities on Dec 31, 2026 79%",
        "Waymo launch in Denver, CO by Dec 31, 2026 74%",
    ]


def test_footer_price_is_the_named_brackets_own_price():
    # The live footer put the 42% of the $1.25T-$1.5T bracket beside the
    # question of the $2.0T-$2.25T one, and the 70% of $1.5T beside $3.0T.
    assert _footer("Anthropic", ANTHROPIC_CAP, ANTHROPIC_HIT) == [
        "Anthropic's valuation hit (HIGH) $1.5T by Dec 31 70%",
        "Anthropic's market cap between $1.25T and $1.5T at market close on IPO day 42%",
    ]


def test_footer_names_never_end_mid_phrase():
    for line in _footer("Waymo", WAYMO_CITIES, WAYMO_LAUNCH) + _footer(
        "Anthropic", ANTHROPIC_CAP, ANTHROPIC_HIT,
    ):
        name = line.rsplit(" ", 1)[0]
        assert name.split()[-1].lower() not in {"on", "by", "between", "in", "at", "and", "be"}


# --- A deadline ladder shows its nearest open deadline ---


def test_deadline_ladder_shows_the_nearest_deadline_that_has_not_passed():
    assert _footer("Jensen Huang", JENSEN_RESIGN, JENSEN_DIVORCE) == [
        "Jensen Huang announced resignation or removal by Dec 31, 2026 16%",
        "Jensen Huang divorce by Dec 31, 2026 9.5%",
    ]


def test_deadline_that_has_passed_is_skipped(monkeypatch):
    monkeypatch.setattr(polymarket, "_today", lambda: "2027-02-01", raising=False)
    assert _footer("Jensen Huang", JENSEN_RESIGN) == [
        "Jensen Huang announced resignation or removal by Mar 31, 2027 24%",
    ]


def test_outcomes_on_different_days_are_not_a_deadline_ladder():
    # "IPO on November 20" excludes "on November 19": the likeliest day leads.
    assert _footer("Anthropic", ANTHROPIC_IPO_DATE) == ["Anthropic IPO on Nov 20, 2026 72%"]


def test_deadline_ladder_keeps_its_saved_names_and_price_order():
    # The names and order saved runs are compared by (tests/test_freshness.py).
    assert _parsed("Jensen Huang", JENSEN_RESIGN)[0]["outcome_prices"] == [
        (RESIGN.format("December 31, 2027").rstrip("?"), 0.38),
        (RESIGN.format("March 31, 2027").rstrip("?"), 0.24),
        (RESIGN.format("December 31, 2026").rstrip("?"), 0.16),
    ]


# --- A market must name the whole topic to be named in the footer ---


def test_market_sharing_one_word_of_the_topic_is_not_named_in_the_footer():
    lines = _footer("Jensen Huang", JENSEN_RESIGN, JENSEN_DIVORCE, TABLE_TENNIS)
    assert len(lines) == 2
    assert not any("WTT" in line or "Youzheng" in line for line in lines)


def test_footer_line_still_counts_every_market():
    report = _report("Jensen Huang", JENSEN_RESIGN, JENSEN_DIVORCE, TABLE_TENNIS)
    line = next(line for line in render._render_emoji_footer(report, None) if "Polymarket" in line)
    assert "📊 Polymarket: 3 markets │ " in line
    assert "WTT" not in line


@pytest.mark.parametrize("topic, texts, expected", [
    ("Jensen Huang", ["WTT Contender: Anders Lind vs Youzheng Huang"], False),
    ("Jensen Huang", ["Jensen Huang divorce by...?"], True),
    ("Anthropic", ["Will Anthropic’s valuation hit __ by December 31?"], True),
    ("Anthropic", ["What will Anthropic's public ticker be?"], True),
    ("Kanye West", ["NFC West winner"], False),
    ("OpenClaw vs Hermes", ["Will Hermes ship by June?"], True),
    ("SpaceX", ["Tesla and SpaceX merger officially announced by December 31, 2027"], True),
    ("", ["Anything at all"], True),
])
def test_names_whole_topic(topic, texts, expected):
    assert polymarket.names_whole_topic(topic, *texts) is expected


# --- An event that is one market ---


def test_single_yes_no_market_is_named_by_its_whole_question():
    event = _event("3", "Tesla and SpaceX merger officially announced by December 31, 2027?", [
        _market("Tesla and SpaceX merger officially announced by December 31, 2027?", "", 0.68, 500),
    ])
    assert _footer("SpaceX", event) == [
        "Tesla and SpaceX merger officially announced by Dec 31, 2027 68%",
    ]


def test_single_market_with_named_outcomes_keeps_the_leading_outcome():
    assert render._polymarket_top_markets(_items("Youzheng Huang", TABLE_TENNIS)) == [
        "WTT Contender: Anders Lind vs Youzheng Huang: Youzheng Huang 50%",
    ]


def _report(topic, *events):
    from lib import schema

    return schema.Report(
        topic=topic, range_from="2026-10-12", range_to="2026-11-11",
        generated_at="2026-11-11T00:00:00Z",
        provider_runtime=schema.ProviderRuntime("mock", "mock", "mock"),
        query_plan=schema.QueryPlan(
            intent="company", freshness_mode="balanced_recent", cluster_mode="none",
            raw_topic=topic, subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=[],
        items_by_source={"polymarket": _items(topic, *events)},
        errors_by_source={},
    )


def test_deadline_rung_the_market_gives_almost_no_chance_is_passed_over():
    # Live: "Anthropic IPO by Nov 17" sat at 0.8% beside "by Dec 31" at 97%.
    ipo = "Anthropic IPO by {}?"
    event = _event("77", "Anthropic IPO by...?", [
        _market(ipo.format("November 17, 2026"), "November 17, 2026", 0.008, 900000, "2026-11-18"),
        _market(ipo.format("December 31, 2026"), "December 31, 2026", 0.97, 500000, "2027-01-01"),
    ])
    assert _footer("Anthropic", event) == ["Anthropic IPO by Dec 31, 2026 97%"]


def test_deadline_ladder_with_only_long_shots_keeps_its_nearest_rung():
    q = "Jensen Huang arrested by {}?"
    event = _event("78", "Jensen Huang arrested by...?", [
        _market(q.format("December 31, 2026"), "December 31, 2026", 0.02, 9000, "2027-01-01"),
        _market(q.format("March 31, 2027"), "March 31, 2027", 0.04, 5000, "2027-04-01"),
    ])
    assert _footer("Jensen Huang", event) == ["Jensen Huang arrested by Dec 31, 2026 2.0%"]
