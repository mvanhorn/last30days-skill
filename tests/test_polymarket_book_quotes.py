"""Polymarket prices read the way Polymarket displays them (issue #1449).

A live run for "Microsoft" on 2026-11-15 printed:

    📊 Polymarket · Microsoft (MSFT) closes week of Nov 16 at: <$480 at 44%
        · $560-$570 at 43% · $490-$500 at 28%

for a negRisk range set whose Yes prices summed to about 2.7. The top
ranges were midpoints of books with no bid (ask 0.82, spread 0.81).
Polymarket itself shows the midpoint only for a bid and an ask within 10
cents and the last trade otherwise.

The events below are the Gamma API's ``/events?slug=...`` responses for
those markets, read on 2026-11-15: questions, labels, ``outcomePrices``
and book fields as served (no ``lastTradePrice`` and no ``volume`` on any
range), other fields trimmed.
"""

from types import SimpleNamespace

import pytest

from lib import freshness, normalize, polymarket, render
from tests.test_freshness import _item, _report

TODAY = "2026-11-15"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(polymarket, "_today", lambda: TODAY, raising=False)


def _market(question, label, yes, *, bid=None, ask=None, spread=None, last=None,
            liquidity=500, volume=None, neg_risk=None, end="2026-11-20"):
    market = {
        "question": question, "groupItemTitle": label,
        "outcomes": '["Yes", "No"]', "outcomePrices": f'["{yes}", "{round(1 - yes, 4)}"]',
        "liquidity": liquidity, "endDate": f"{end}T21:00:00Z",
        "active": True, "closed": False,
    }
    for key, value in (("bestBid", bid), ("bestAsk", ask), ("spread", spread),
                       ("lastTradePrice", last), ("volume", volume), ("negRisk", neg_risk)):
        if value is not None:
            market[key] = value
    return market


def _event(event_id, title, markets, *, slug, neg_risk=None):
    event = {
        "id": event_id, "title": title, "slug": slug, "active": True, "closed": False,
        "liquidity": 5000, "updatedAt": "2026-11-15T18:04:09Z", "markets": markets,
    }
    if neg_risk is not None:
        event["negRisk"] = neg_risk
    return event


RANGE = ("Will Microsoft (MSFT) close at {} on the final day of trading of the week "
         "of Nov 16 – Nov 20?")


def _range(label, yes, bid, ask, spread, liquidity):
    return _market(RANGE.format(label), label, yes, bid=bid, ask=ask, spread=spread,
                   liquidity=liquidity, neg_risk=True)


MSFT_WEEK_RANGES = _event("1382631", "Microsoft (MSFT) closes week of Nov 16 at ___?", [
    _range("<$480", 0.415, 0.01, 0.82, 0.81, 381.2697),
    _range("$480-$490", 0.26, 0.01, 0.51, 0.5, 616.7017),
    _range("$490-$500", 0.08, 0.03, 0.13, 0.1, 569.0307),
    _range("$500-$510", 0.14, 0.07, 0.21, 0.14, 531.6885),
    _range("$510-$520", 0.19, 0.11, 0.27, 0.16, 484.4909),
    _range("$520-$530", 0.2, 0.15, 0.25, 0.1, 514.0269),
    _range("$530-$540", 0.145, 0.1, 0.19, 0.09, 417.869),
    _range("$540-$550", 0.08, 0.04, 0.12, 0.08, 543.6644),
    _range("$550-$560", 0.265, 0.01, 0.52, 0.51, 598.6817),
    # No bid at all: Gamma omits ``bestBid``.
    _range("$560-$570", 0.475, None, 0.95, 0.95, 35.5963),
    _range(">$570", 0.255, None, 0.51, 0.51, 173.7698),
], slug="msft-week-november-20-2026", neg_risk=True)

ABOVE = "Will Microsoft (MSFT) finish week of November 16 above {}?"
# The sibling cumulative-threshold event: independent markets, not negRisk.
MSFT_WEEK_ABOVE = _event("1382632", "Will Microsoft (MSFT) finish week of November 16 above___?", [
    _market(ABOVE.format(f"${strike}"), f"${strike}", yes, bid=bid, ask=ask, spread=spread,
            neg_risk=False)
    for strike, yes, bid, ask, spread in [
        (460, 0.555, 0.12, 0.99, 0.87),
        (480, 0.935, 0.88, 0.99, 0.11),
        (490, 0.93, 0.89, 0.97, 0.08),
        (500, 0.85, 0.8, 0.9, 0.1),
        (520, 0.48, 0.44, 0.52, 0.08),
        (540, 0.14, 0.1, 0.18, 0.08),
        (570, 0.495, None, 0.99, 0.99),
    ]
], slug="msft-above-on-november-20-2026", neg_risk=False)


def _parsed(*events, topic="Microsoft", **kwargs):
    return polymarket.parse_polymarket_response({"events": list(events)}, topic=topic, **kwargs)


def _items(*events, topic="Microsoft"):
    return normalize.normalize_source_items(
        "polymarket", _parsed(*events, topic=topic), "2026-10-16", "2026-11-15",
    )


# --- R1: the displayed price of one market ---


def _single(**book):
    return _market("Will it happen?", None, 0.5, **book)


def test_tight_book_uses_the_midpoint():
    market = _single(bid=0.46, ask=0.54, spread=0.08, last=0.70)
    assert polymarket._parse_outcome_prices(market) == [("Yes", 0.5), ("No", 0.5)]


def test_spread_of_exactly_ten_cents_is_still_tight():
    # 0.13 - 0.03 is 0.09999... in floats; no spread field to lean on.
    market = _single(bid=0.03, ask=0.13)
    assert polymarket._parse_outcome_prices(market) == [("Yes", 0.5), ("No", 0.5)]


def test_empty_bid_with_a_wide_ask_uses_the_last_trade():
    market = _single(ask=0.99, spread=0.99, last=0.12)
    assert polymarket._parse_outcome_prices(market) == [("Yes", 0.12), ("No", 0.88)]


def test_wide_two_sided_book_uses_the_last_trade():
    market = _single(bid=0.10, ask=0.90, spread=0.80, last=0.30)
    assert polymarket._parse_outcome_prices(market)[0] == ("Yes", 0.30)


def test_wide_book_with_no_trade_drops_the_outcome():
    assert polymarket._parse_outcome_prices(_single(ask=0.99, spread=0.99)) == []
    assert polymarket._parse_outcome_prices(_single(bid=0.01, ask=0.82, spread=0.81, last=0)) == []


def test_missing_book_fields_keep_the_served_price():
    market = _single()
    assert polymarket._parse_outcome_prices(market) == [("Yes", 0.5), ("No", 0.5)]
    assert polymarket._parse_outcome_prices(dict(market, lastTradePrice=0.9)) == [
        ("Yes", 0.5), ("No", 0.5),
    ]


def test_closed_market_keeps_its_resolution_price():
    market = dict(_market("Did it happen?", None, 1.0, ask=0.99, spread=0.99), closed=True)
    assert polymarket._parse_outcome_prices(market)[0] == ("Yes", 1.0)


def test_empty_book_binary_shows_no_coin_flip():
    event = _event("9", "Microsoft (MSFT) Up or Down on November 16?", [
        _market("Microsoft (MSFT) Up or Down on November 16?", None, 0.45,
                bid=0.07, ask=0.83, spread=0.76),
    ], slug="msft-up-or-down")
    [item] = _parsed(event)
    assert item["outcome_prices"] == []
    assert item["odds"] is None
    assert item["headline"] is None


# --- The live MSFT range event ---


def test_msft_ranges_drop_every_empty_book_midpoint():
    [item] = _parsed(MSFT_WEEK_RANGES, include_all_outcomes=True)
    names = [name for name, _price in item["outcome_prices"]]
    assert names == ["$520-$530", "$530-$540", "$490-$500", "$540-$550"]
    assert sum(price for _name, price in item["outcome_prices"]) <= 1.10


def test_msft_ranges_odds_line_shows_no_impossible_distribution():
    [item] = _items(MSFT_WEEK_RANGES)
    line = render._polymarket_odds_line(item, "2026")
    assert "$520-$530 at 20%" in line
    assert "$530-$540 at 14%" in line
    assert "<$480" not in line
    assert "$560-$570" not in line


def test_one_priced_bracket_keeps_its_own_label():
    # Live 2026-11-15: only the $510 bracket had a last trade (0.85).
    title = "Microsoft (MSFT) closes above ___ on November 16?"
    question = "Will Microsoft (MSFT) close above {} on November 16?"
    event = _event("1382600", title, [
        _market(question.format("$500"), "$500", 0.87, bid=0.742, ask=0.998, spread=0.256,
                volume=347.4),
        _market(question.format("$510"), "$510", 0.78, bid=0.61, ask=0.95, spread=0.34,
                last=0.85, volume=22.25),
        _market(question.format("$520"), "$520", 0.555, bid=0.15, ask=0.96, spread=0.81,
                volume=22.07),
    ], slug="msft-close-above-on-november-16-2026", neg_risk=False)
    [item] = _parsed(event)
    assert item["outcome_prices"] == [("$510", 0.85)]


# --- R2: a mutually exclusive set that still sums well over 1 ---


def _tight_ranges(prices, *, neg_risk):
    return _event("77", "Microsoft (MSFT) closes week of Nov 23 at ___?", [
        _range_tight(label, yes, neg_risk) for label, yes in prices
    ], slug="msft-week-november-27-2026", neg_risk=neg_risk)


def _range_tight(label, yes, neg_risk):
    return _market(RANGE.format(label), label, yes, bid=round(yes - 0.02, 4),
                   ask=round(yes + 0.02, 4), spread=0.04, neg_risk=neg_risk)


def test_neg_risk_set_summing_to_about_one_is_shown_unchanged():
    event = _tight_ranges([("<$500", 0.5), ("$500-$520", 0.3), (">$520", 0.2)], neg_risk=True)
    [item] = _parsed(event)
    assert item["outcome_prices"] == [("<$500", 0.5), ("$500-$520", 0.3), (">$520", 0.2)]
    assert [part["price"] for part in item["odds"]["parts"]] == [0.5, 0.3, 0.2]


def test_neg_risk_set_summing_over_one_point_one_has_no_odds_line():
    event = _tight_ranges([("<$500", 0.6), ("$500-$520", 0.4), (">$520", 0.3)], neg_risk=True)
    [item] = _parsed(event)
    assert item["odds"] is None
    assert item["headline"] is None
    assert item["outcome_prices"] == []
    # Still evidence, and the footer and odds line say nothing about it.
    [normalized] = _items(event)
    assert normalized.title == event["title"]
    assert render._polymarket_odds_line(normalized, "2026") == ""


def test_neg_risk_flag_on_the_markets_alone_counts():
    event = _tight_ranges([("<$500", 0.6), ("$500-$520", 0.4), (">$520", 0.3)], neg_risk=None)
    for market in event["markets"]:
        market["negRisk"] = True
    assert _parsed(event)[0]["odds"] is None


def test_suppressed_set_still_verifies_each_markets_quote():
    event = _tight_ranges([("<$500", 0.6), ("$500-$520", 0.4), (">$520", 0.3)], neg_risk=True)
    [item] = _parsed(event, include_all_outcomes=True)
    assert dict(item["outcome_prices"]) == {"<$500": 0.6, "$500-$520": 0.4, ">$520": 0.3}


def test_independent_markets_are_not_held_to_the_sum_check():
    event = _tight_ranges([("<$500", 0.6), ("$500-$520", 0.4), (">$520", 0.3)], neg_risk=False)
    [item] = _parsed(event)
    assert item["odds"] is not None
    assert [part["price"] for part in item["odds"]["parts"]] == [0.6, 0.4, 0.3]


def test_msft_above_thresholds_keep_tight_markets_without_a_sum_check():
    [item] = _parsed(MSFT_WEEK_ABOVE, include_all_outcomes=True)
    # Their Yes prices sum to 3.3; thresholds are not exclusive.
    assert item["odds"] is not None
    assert dict(item["outcome_prices"]) == {"$490": 0.93, "$500": 0.85, "$520": 0.48, "$540": 0.14}


# --- Verification of a claim saved against a market now dropped ---


def test_saved_claim_on_a_dropped_market_is_unsupported(monkeypatch):
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: [MSFT_WEEK_RANGES])
    saved = _item(
        "polymarket",
        title=MSFT_WEEK_RANGES["title"],
        url="https://polymarket.com/event/msft-week-november-20-2026",
        metadata={
            "event_id": "1382631",
            "outcome_prices": [["<$480", 0.435], ["$520-$530", 0.2]],
        },
    )
    report = _report(saved)
    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-15T19:00:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    by_key = {
        claim.datum_key: claim.claim_id
        for claim in freshness.extract_claims(report)
        if claim.datum_kind == "polymarket_probability"
    }
    by_id = {verdict.claim_id: verdict for verdict in verdicts}
    dropped = by_id[by_key["<$480"]]
    assert dropped.verdict == "unsupported"
    assert "Re-check failed: " in dropped.detail
    assert "was not found" in dropped.detail
    assert by_id[by_key["$520-$530"]].verdict == "current"


def test_refetch_of_a_dropped_market_raises_not_found(monkeypatch):
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: [MSFT_WEEK_RANGES])
    item = SimpleNamespace(metadata={"event_id": "1382631"}, url="", item_id="pm-1")
    with pytest.raises(KeyError, match="was not found"):
        polymarket.refetch_datum(item, "<$480")
    assert polymarket.refetch_datum(item, "$530-$540")["value"] == 0.145


def test_dropped_repeat_does_not_let_a_saved_occurrence_read_its_sibling(monkeypatch):
    question = "Will Bitcoin reach $150k in November?"
    event = _event(
        "77", "Bitcoin in November",
        [
            _market(question, "A", 0.6, bid=0.01, ask=0.9, spread=0.89),  # dropped
            _market(question, "B", 0.3, bid=0.29, ask=0.31, spread=0.02),  # priced
        ],
        slug="bitcoin-november",
    )
    monkeypatch.setattr(polymarket.http, "request", lambda *_a, **_k: event)
    item = SimpleNamespace(metadata={"event_id": "77"}, url="", item_id="pm-77")
    legacy = polymarket._legacy_shorten_question(question).strip()
    for occurrence in (0, 1):
        with pytest.raises(KeyError, match="was not found"):
            polymarket.refetch_datum(item, f"{legacy}\x1f{occurrence}")


def test_missing_claims_reuse_one_event_fetch(monkeypatch):
    calls = []

    def _request(*args, **kwargs):
        calls.append(args)
        return [MSFT_WEEK_RANGES]

    monkeypatch.setattr(polymarket.http, "request", _request)
    saved = _item(
        "polymarket",
        title=MSFT_WEEK_RANGES["title"],
        url="https://polymarket.com/event/msft-week-november-20-2026",
        metadata={
            "event_id": "1382631",
            "outcome_prices": [["<$480", 0.435], ["$560-$570", 0.4], ["$520-$530", 0.2]],
        },
    )
    report = _report(saved)
    verdicts = freshness.verify_report(
        report,
        checked_at="2026-11-15T19:00:00Z",
        refetchers={"polymarket": polymarket.refetch_datum},
    )
    by_key = {
        claim.datum_key: claim.claim_id
        for claim in freshness.extract_claims(report)
        if claim.datum_kind == "polymarket_probability"
    }
    by_id = {verdict.claim_id: verdict for verdict in verdicts}
    assert by_id[by_key["$520-$530"]].verdict == "current"
    assert by_id[by_key["<$480"]].verdict == "unsupported"
    assert len(calls) == 1
