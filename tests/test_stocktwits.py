"""Tests for stocktwits.py - StockTwits source (ticker/crypto topics only).

All hermetic: symbol-search and stream HTTP calls are patched, so no network.
"""

from __future__ import annotations

from unittest.mock import patch

from lib import stocktwits, normalize, planner, pipeline


# === Gating: is_financial_topic / detect_symbols ===

def test_cashtag_is_financial():
    assert stocktwits.is_financial_topic("$NVDA earnings")
    assert stocktwits.detect_symbols("$NVDA earnings", resolve=False) == ["NVDA"]


def test_crypto_alias_resolves_with_dot_x_suffix():
    assert stocktwits.detect_symbols("bitcoin price", resolve=False) == ["BTC.X"]
    assert stocktwits.detect_symbols("should I buy ethereum", resolve=False) == ["ETH.X"]


def test_non_financial_topic_resolves_to_nothing():
    # The whole point of the gate: a person/recipe must never resolve a ticker.
    assert not stocktwits.is_financial_topic("Kanye West")
    assert stocktwits.detect_symbols("Kanye West", resolve=True) == []
    assert stocktwits.detect_symbols("Apple pie recipe", resolve=True) == []


def test_general_topics_with_ambiguous_words_do_not_trip_the_gate():
    # Regression guard for the tightened _FINANCE_HINTS: these everyday phrases
    # contain words that USED to trip the gate (share/token/coin/bear) and must
    # never register stocktwits for a non-financial run.
    for topic in (
        "how to share files on iPhone",
        "Claude token limits",
        "coin collecting for beginners",
        "bear attacks in Yellowstone",
        "bull riding championship",
    ):
        assert not stocktwits.is_financial_topic(topic), topic
        assert stocktwits.detect_symbols(topic, resolve=False) == [], topic


def test_finance_vocabulary_still_trips_the_gate():
    for topic in (
        "bullish on NVDA earnings",
        "TSLA stock forecast",
        "best dividend stocks",
        "altcoin season predictions",
        "bitcoin price",
    ):
        assert stocktwits.is_financial_topic(topic), topic


def test_name_resolution_only_fires_for_financial_topics():
    # "Apple pie recipe" trips no finance hint -> no symbol-search call at all.
    with patch.object(stocktwits, "_get_json") as mock_get:
        assert stocktwits.detect_symbols("Apple pie recipe", resolve=True) == []
        mock_get.assert_not_called()

    # "ServiceNow stock" trips the gate -> symbol search runs and resolves.
    with patch.object(stocktwits, "_get_json", return_value={"results": [{"symbol": "NOW"}]}) as mock_get:
        assert stocktwits.detect_symbols("ServiceNow stock", resolve=True) == ["NOW"]
        mock_get.assert_called_once()


# === Parsing + sentiment aggregation ===

def _msg(mid, user, body, sentiment=None, likes=0, created="2026-06-20T12:00:00Z"):
    m = {
        "id": mid,
        "body": body,
        "created_at": created,
        "user": {"username": user, "followers": 1000},
        "likes": {"total": likes},
    }
    if sentiment:
        m["entities"] = {"sentiment": {"basic": sentiment}}
    return m


def _response():
    return {
        "symbols": ["NOW"],
        "watchlist": 41000,
        "messages": [
            _msg(1, "alice", "$NOW buy the dip", "Bullish", likes=5),
            _msg(2, "bob", "$NOW going to zero", "Bearish", likes=2),
            _msg(3, "carol", "$NOW holding", None, likes=0),
        ],
    }


def test_aggregate_sentiment_counts_and_ratio():
    agg = stocktwits.aggregate_sentiment(_response()["messages"])
    assert agg["bullish"] == 1
    assert agg["bearish"] == 1
    assert agg["untagged"] == 1
    assert agg["pct_bullish"] == 50
    assert agg["sample"] == 3


def test_aggregate_sentiment_no_tagged_messages():
    agg = stocktwits.aggregate_sentiment([_msg(1, "x", "$NOW", None)])
    assert agg["pct_bullish"] is None  # no division by zero


def test_parse_builds_well_formed_items():
    items = stocktwits.parse_stocktwits_response(_response(), query="ServiceNow")
    assert len(items) == 3
    first = items[0]
    assert first["url"] == "https://stocktwits.com/alice/message/1"
    assert first["author"] == "alice"
    assert first["metadata"]["sentiment"] == "Bullish"
    assert first["metadata"]["symbol"] == "NOW"
    # The bull/bear aggregate rides on every item so synthesis can cite the ratio.
    assert first["metadata"]["sentiment_aggregate"]["pct_bullish"] == 50
    assert first["engagement"]["likes"] == 5


# === Normalize wiring ===

def test_normalizer_registered_and_maps_fields():
    items = stocktwits.parse_stocktwits_response(_response(), query="ServiceNow")
    normalized = normalize.normalize_source_items(
        "stocktwits", items, from_date="2026-06-01", to_date="2026-06-30")
    assert len(normalized) == 3
    item = normalized[0]
    assert item.source == "stocktwits"
    assert item.container == "NOW"           # symbol -> container
    assert item.author == "alice"
    assert item.metadata["sentiment"] == "Bullish"


# === Planner + pipeline gate ===

def test_planner_capability_and_priority():
    assert planner.SOURCE_CAPABILITIES["stocktwits"] == {"social", "market", "finance_social"}
    assert "stocktwits" in planner.SOURCE_PRIORITY["breaking_news"]
    assert "stocktwits" in planner.SOURCE_PRIORITY["prediction"]


def test_pipeline_availability_is_gated_by_financial_flag():
    assert "stocktwits" in pipeline.available_sources({"_financial_topic": True})
    assert "stocktwits" not in pipeline.available_sources({"_financial_topic": False})
    assert "stocktwits" not in pipeline.available_sources({})  # default off
