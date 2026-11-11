"""The Polymarket footer names each market once and whole.

The fixtures follow a saved run: a date-ladder event ("... by ...?") whose
sub-markets are Yes/No markets that differ only in their deadline.
"""

import pytest

from lib import polymarket, render, schema


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    # The day of the saved run: every deadline below is still open.
    monkeypatch.setattr(polymarket, "_today", lambda: "2026-11-10")


RESIGNATION = "Jensen Huang announced resignation or removal by {}?"
DIVORCE = "Jensen Huang divorce by {}?"


def _market(question, yes_price, volume):
    return {
        "question": question,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes_price}", "{1 - yes_price}"]',
        "volume": volume,
        "liquidity": 50000,
        "active": True,
        "closed": False,
    }


def _event(event_id, title, markets):
    return {
        "id": event_id,
        "title": title,
        "slug": f"event-{event_id}",
        "active": True,
        "closed": False,
        "volume1mo": sum(m["volume"] for m in markets),
        "liquidity": 100000,
        "markets": markets,
    }


def _resignation_event():
    return _event(
        "1343940",
        "Jensen Huang announced resignation or removal by...?",
        [
            _market(RESIGNATION.format("December 31, 2026"), 0.14, 400000),
            _market(RESIGNATION.format("March 31, 2027"), 0.22, 200000),
            _market(RESIGNATION.format("June 30, 2027"), 0.32, 150000),
        ],
    )


def _divorce_event():
    return _event(
        "1344318",
        "Jensen Huang divorce by...?",
        [
            _market(DIVORCE.format("December 31, 2026"), 0.095, 150000),
            _market(DIVORCE.format("March 31, 2027"), 0.18, 80000),
        ],
    )


def _source_item(parsed):
    return schema.SourceItem(
        item_id=str(parsed["event_id"]),
        source="polymarket",
        title=parsed["title"],
        body="",
        url=parsed["url"],
        engagement={"volume": parsed["volume1mo"]},
        metadata={
            "question": parsed["question"],
            "outcome_prices": parsed["outcome_prices"],
            "headline": parsed["headline"],
        },
    )


def _pm_item(question, outcome_name, price, volume=1000):
    return schema.SourceItem(
        item_id=f"pm-{volume}",
        source="polymarket",
        title=question,
        body="",
        url="https://polymarket.com/event/x",
        engagement={"volume": volume},
        metadata={
            "question": question,
            "outcome_prices": [(outcome_name, price)],
        },
    )


# --- The parser: sub-market names ---


def test_date_ladder_sub_markets_keep_whole_distinct_names():
    parsed = polymarket.parse_polymarket_response({"events": [_resignation_event()]})

    names = [name for name, _price in parsed[0]["outcome_prices"]]

    assert names == [
        "Jensen Huang announced resignation or removal by June 30, 2027",
        "Jensen Huang announced resignation or removal by March 31, 2027",
        "Jensen Huang announced resignation or removal by December 31, 2026",
    ]


def test_long_question_is_cut_at_a_word_boundary():
    question = (
        "Nvidia board announces a successor to Jensen Huang as chief executive "
        "officer before the annual shareholder meeting in June 2027?"
    )

    name = polymarket._shorten_question(question)

    assert name != question.rstrip("?")
    assert question.startswith(name + " ")
    assert name[-1].isalnum()


# --- The footer text ---


def test_sub_market_named_by_its_question_renders_once():
    parsed = polymarket.parse_polymarket_response({"events": [_resignation_event()]})

    lines = render._polymarket_top_markets([_source_item(parsed[0])])

    # A deadline ladder is named by its nearest open deadline, not by the
    # furthest one, which always prices highest.
    assert lines == [
        "Jensen Huang announced resignation or removal by Dec 31, 2026 14%"
    ]


def test_saved_run_footer_reads_cleanly():
    parsed = polymarket.parse_polymarket_response(
        {"events": [_resignation_event(), _divorce_event()]}
    )

    lines = render._polymarket_top_markets([_source_item(p) for p in parsed], limit=3)

    assert ", ".join(lines) == (
        "Jensen Huang announced resignation or removal by Dec 31, 2026 14%, "
        "Jensen Huang divorce by Dec 31, 2026 9.5%"
    )


def test_label_cut_from_the_question_is_not_repeated():
    # Items saved before the parser kept whole names carry a 40-character cut.
    item = _pm_item(
        RESIGNATION.format("December 31, 2026"),
        "Jensen Huang announced resignation or re",
        0.32,
    )

    lines = render._polymarket_top_markets([item])

    assert lines == ["Jensen Huang announced resignation or removal 32%"]


def test_label_with_its_own_information_is_kept_beside_the_descriptor():
    item = _pm_item("Fed decision in December?", "25 bps increase", 0.12)

    lines = render._polymarket_top_markets([item])

    assert lines == ["Fed decision in December: 25 bps increase 12%"]


def test_long_question_descriptor_holds_only_whole_words():
    question = (
        "Will Nvidia announce a successor to Jensen Huang as chief executive "
        "officer before the annual shareholder meeting?"
    )
    item = _pm_item(question, "Yes", 0.07)

    line = render._polymarket_top_markets([item])[0]

    name = line.removesuffix(" 7.0%")
    assert name != line
    assert set(name.split()) <= set(question.rstrip("?").split())


def test_markets_with_the_same_descriptor_render_as_distinct_text():
    items = [
        _pm_item(DIVORCE.format("December 31, 2026"), "Yes", 0.095, volume=300),
        _pm_item(DIVORCE.format("March 31, 2027"), "Yes", 0.18, volume=200),
        _pm_item(DIVORCE.format("June 30, 2027"), "Yes", 0.25, volume=100),
    ]

    lines = render._polymarket_top_markets(items, limit=3)

    assert lines == [
        "Jensen Huang divorce by December 31, 2026 9.5%",
        "Jensen Huang divorce by March 31, 2027 18%",
        "Jensen Huang divorce by June 30, 2027 25%",
    ]


def test_the_same_market_listed_twice_renders_once():
    items = [
        _pm_item(DIVORCE.format("March 31, 2027"), "Yes", 0.18, volume=300),
        _pm_item(DIVORCE.format("March 31, 2027"), "Yes", 0.18, volume=200),
    ]

    lines = render._polymarket_top_markets(items, limit=3)

    assert lines == ["Jensen Huang divorce 18%"]


def test_different_markets_with_the_same_labelled_name_both_render():
    items = [
        _pm_item("Next president by December 31, 2026?", "Vance", 0.3, volume=300),
        _pm_item("Next president by June 30, 2027?", "Vance", 0.41, volume=200),
    ]

    lines = render._polymarket_top_markets(items, limit=3)

    assert lines == [
        "Next president by December 31, 2026: Vance 30%",
        "Next president by June 30, 2027: Vance 41%",
    ]


def test_the_same_labelled_market_listed_twice_renders_once():
    items = [
        _pm_item("Next president by June 30, 2027?", "Vance", 0.41, volume=300),
        _pm_item("Next president by June 30, 2027?", "Vance", 0.41, volume=200),
    ]

    lines = render._polymarket_top_markets(items, limit=3)

    assert lines == ["Next president: Vance 41%"]
