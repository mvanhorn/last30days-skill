"""Tests for taxminator.py - Taxminator (Uzbek) prediction market search."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import http, taxminator

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "taxminator_markets_sample.json"


@pytest.fixture(autouse=True)
def _clear_fetch_cache():
    """The module caches its one fetch per process; never leak it across tests."""
    taxminator.clear_cache()
    yield
    taxminator.clear_cache()


@pytest.fixture
def sample_markets():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["markets"]


def make_market(
    market_id="tx-1",
    slug="test-market",
    uz="Özbekiston ğalaba qozonadimi?",
    ru="Узбекистан победит?",
    en="Will Uzbekistan win?",
    status="OPEN",
    total_predictions=50,
    crowd_revealed=True,
    outcomes=None,
    updated_at="2026-09-05T10:00:00.000Z",
    resolved_at=None,
    closes_at="2026-09-10T14:00:00.000Z",
):
    if outcomes is None:
        outcomes = [
            {
                "id": "o-yes",
                "label": {"uz": "Ha", "ru": "Да", "en": "Yes"},
                "emoji": "✅",
                "isWinner": False,
                "votes": 30 if crowd_revealed else None,
                "votePercent": 60 if crowd_revealed else None,
            },
            {
                "id": "o-no",
                "label": {"uz": "Yöq", "ru": "Нет", "en": "No"},
                "emoji": "❌",
                "isWinner": False,
                "votes": 20 if crowd_revealed else None,
                "votePercent": 40 if crowd_revealed else None,
            },
        ]
    return {
        "id": market_id,
        "slug": slug,
        "url": f"https://taxminator.uz/markets/{slug}",
        "category": "FOOTBALL",
        "status": status,
        "title": {"uz": uz, "ru": ru, "en": en},
        "group": None,
        "opensAt": "2026-08-28T09:00:00.000Z",
        "closesAt": closes_at,
        "resolveAt": "2026-09-10T20:00:00.000Z",
        "resolvedAt": resolved_at,
        "updatedAt": updated_at,
        "createdAt": "2026-08-28T09:00:00.000Z",
        "pointsForCorrect": 10,
        "totalPredictions": total_predictions,
        "crowdRevealed": crowd_revealed,
        "outcomes": outcomes,
    }


def parse(markets, topic="", **kwargs):
    return taxminator.parse_taxminator_response(
        {"markets": markets, "_cap": 25}, topic=topic, **kwargs
    )


# === Trilingual topic matching ===


def test_matches_english_title():
    items = parse([make_market()], topic="Uzbekistan")
    assert len(items) == 1
    assert items[0]["title"] == "Will Uzbekistan win?"


def test_matches_russian_title():
    """A Russian topic matches the Russian title even though the item is English."""
    items = parse([make_market()], topic="Узбекистан")
    assert len(items) == 1
    # The normalized item always carries the English title.
    assert items[0]["title"] == "Will Uzbekistan win?"


def test_matches_uzbek_title():
    items = parse([make_market()], topic="Özbekiston")
    assert len(items) == 1


def test_matches_uzbek_reformed_orthography_token():
    """Reformed-orthography letters (ö/ğ/ş/ç) are word characters, not noise."""
    items = parse([make_market()], topic="ğalaba")
    assert len(items) == 1


def test_matches_outcome_label_language():
    """Outcome labels feed the similarity score in all three languages."""
    market = make_market(
        en="Who wins the title?",
        ru="Кто выиграет титул?",
        uz="Kim ğalaba qozonadi?",
        outcomes=[
            {
                "id": "o-1",
                "label": {"uz": "Pakhtakor", "ru": "Пахтакор", "en": "Pakhtakor"},
                "emoji": "⚽",
                "isWinner": False,
                "votes": 30,
                "votePercent": 60,
            },
            {
                "id": "o-2",
                "label": {"uz": "Navbahor", "ru": "Навбахор", "en": "Navbahor"},
                "emoji": "⚽",
                "isWinner": False,
                "votes": 20,
                "votePercent": 40,
            },
        ],
    )
    # The title alone shares no informative word with the topic, so the title
    # filter drops it; that is the same conservative rule Polymarket uses.
    assert parse([market], topic="Pakhtakor") == []
    # But when the title does match, outcome labels lift the similarity score.
    scored = taxminator._compute_text_similarity("Pakhtakor title", market)
    assert scored > 0


def test_off_topic_is_silent(sample_markets):
    """Nothing on topic -> zero items, never padding."""
    assert parse(sample_markets, topic="sourdough bread starter") == []


def test_relevance_floor_drops_everything_below_threshold():
    market = make_market()
    with patch.object(taxminator, "_compute_text_similarity", return_value=0.05):
        assert parse([market], topic="anything") == []


def test_per_item_relevance_floor_keeps_the_strong_one():
    strong = make_market(market_id="tx-strong", slug="strong")
    weak = make_market(market_id="tx-weak", slug="weak", en="Will Uzbekistan win?")
    scores = {"tx-strong": 0.9, "tx-weak": 0.05}

    def fake_score(topic, market):
        return scores[market["id"]]

    with patch.object(taxminator, "_compute_text_similarity", side_effect=fake_score):
        items = parse([strong, weak], topic="Uzbekistan")
    assert [item["market_id"] for item in items] == ["tx-strong"]


# === Normalization ===


def test_url_carries_utm_tags():
    items = parse([make_market()], topic="Uzbekistan")
    assert items[0]["url"] == (
        "https://taxminator.uz/markets/test-market"
        "?utm_source=last30days&utm_medium=skill"
    )


def test_url_tags_append_to_existing_query_string():
    market = make_market()
    market["url"] = "https://taxminator.uz/markets/test-market?ref=tg"
    items = parse([market], topic="Uzbekistan")
    assert items[0]["url"].endswith("?ref=tg&utm_source=last30days&utm_medium=skill")


def test_outcome_shares_are_fractions_sorted_by_support():
    items = parse([make_market()], topic="Uzbekistan")
    assert items[0]["outcome_prices"] == [("Yes", 0.6), ("No", 0.4)]


def test_volume_is_the_predictor_count():
    items = parse([make_market(total_predictions=184)], topic="Uzbekistan")
    assert items[0]["volume"] == 184
    assert items[0]["predictors"] == 184


def test_dates_map_from_updated_and_closes():
    items = parse([make_market()], topic="Uzbekistan")
    assert items[0]["date"] == "2026-09-05"
    assert items[0]["end_date"] == "2026-09-10"


def test_resolved_market_dates_from_resolved_at():
    market = make_market(
        status="RESOLVED",
        resolved_at="2026-09-08T13:00:00.000Z",
        updated_at="2026-09-08T13:00:00.000Z",
    )
    items = parse([market], topic="Uzbekistan")
    assert items[0]["date"] == "2026-09-08"


# === Crowd reveal floor ===


def test_crowd_not_revealed_keeps_item_without_a_split():
    market = make_market(total_predictions=3, crowd_revealed=False)
    items = parse([market], topic="Uzbekistan")
    assert len(items) == 1
    assert items[0]["outcome_prices"] == []
    assert items[0]["crowd_revealed"] is False
    assert items[0]["crowd_note"] == "3 predictors, split withheld"


def test_crowd_revealed_note_has_no_withheld_wording():
    items = parse([make_market(total_predictions=50)], topic="Uzbekistan")
    assert items[0]["crowd_note"] == "50 predictors"


def test_single_predictor_note_is_singular():
    market = make_market(total_predictions=1, crowd_revealed=False)
    items = parse([market], topic="Uzbekistan")
    assert items[0]["crowd_note"] == "1 predictor, split withheld"


# === Date filtering in search ===


def _patched_search(markets, topic, from_date, to_date, depth="default"):
    with patch.object(http, "request", return_value={"markets": markets}):
        return taxminator.search_taxminator(topic, from_date, to_date, depth=depth)


def test_search_keeps_open_market_updated_before_the_window():
    """An open market is a live forecast: current evidence whenever it was made."""
    market = make_market(updated_at="2026-05-01T10:00:00.000Z")
    result = _patched_search([market], "Uzbekistan", "2026-08-08", "2026-09-07")
    assert len(result["markets"]) == 1


def test_search_drops_resolved_market_before_the_window():
    market = make_market(
        status="RESOLVED",
        resolved_at="2026-05-01T10:00:00.000Z",
        updated_at="2026-05-01T10:00:00.000Z",
    )
    result = _patched_search([market], "Uzbekistan", "2026-08-08", "2026-09-07")
    assert result["markets"] == []


def test_search_drops_market_dated_after_the_window():
    market = make_market(updated_at="2027-01-01T10:00:00.000Z")
    result = _patched_search([market], "Uzbekistan", "2026-08-08", "2026-09-07")
    assert result["markets"] == []


def test_search_dedupes_open_and_resolved_fetches():
    market = make_market()
    with patch.object(http, "request", return_value={"markets": [market]}) as request:
        result = taxminator.search_taxminator(
            "Uzbekistan", "2026-08-08", "2026-09-07"
        )
    # Two fetches (open + resolved), one merged market.
    assert request.call_count == 2
    assert len(result["markets"]) == 1


def test_search_caches_the_fetch_within_the_process():
    market = make_market()
    with patch.object(http, "request", return_value={"markets": [market]}) as request:
        taxminator.search_taxminator("Uzbekistan", "2026-08-08", "2026-09-07")
        taxminator.search_taxminator("chess", "2026-08-08", "2026-09-07")
    assert request.call_count == 2  # not 4: the second run reuses the cache


def test_search_respects_the_depth_cap():
    result = _patched_search([make_market()], "Uzbekistan", "2026-08-08", "2026-09-07", depth="quick")
    assert result["_cap"] == taxminator.RESULT_CAP["quick"]


# === Network failure ===


def test_network_error_yields_zero_items_and_an_error():
    with patch.object(http, "request", side_effect=http.HTTPError("connection reset")):
        result = taxminator.search_taxminator(
            "Uzbekistan", "2026-08-08", "2026-09-07"
        )
    assert result["markets"] == []
    assert "connection reset" in result["error"]
    assert taxminator.parse_taxminator_response(result, topic="Uzbekistan") == []


def test_unexpected_exception_is_contained():
    with patch.object(http, "request", side_effect=ValueError("bad json")):
        result = taxminator.search_taxminator(
            "Uzbekistan", "2026-08-08", "2026-09-07"
        )
    assert result["markets"] == []
    assert "bad json" in result["error"]


def test_malformed_payload_yields_zero_items():
    with patch.object(http, "request", return_value=["not", "a", "dict"]):
        result = taxminator.search_taxminator(
            "Uzbekistan", "2026-08-08", "2026-09-07"
        )
    assert result["markets"] == []


def test_partial_failure_still_returns_the_working_half():
    market = make_market()

    def flaky(method, url, **kwargs):
        if kwargs.get("params", {}).get("status") == "resolved":
            raise http.HTTPError("resolved lane down")
        return {"markets": [market]}

    with patch.object(http, "request", side_effect=flaky):
        result = taxminator.search_taxminator(
            "Uzbekistan", "2026-08-08", "2026-09-07"
        )
    assert len(result["markets"]) == 1
    assert "error" not in result


# === Refetch ===


class _Item:
    def __init__(self, url, metadata):
        self.url = url
        self.metadata = metadata


def test_refetch_datum_returns_the_current_share():
    market = make_market(market_id="tx-42", slug="uzb-iran")
    item = _Item("https://taxminator.uz/markets/uzb-iran", {"market_id": "tx-42"})
    with patch.object(http, "request", return_value={"markets": [market]}):
        result = taxminator.refetch_datum(item, "Yes")
    assert result["value"] == 0.6
    assert result["values"]["No"] == 0.4
    assert result["values"]["end_date"] == "2026-09-10"


def test_refetch_datum_end_date():
    market = make_market(market_id="tx-42", slug="uzb-iran")
    item = _Item("https://taxminator.uz/markets/uzb-iran", {"market_id": "tx-42"})
    with patch.object(http, "request", return_value={"markets": [market]}):
        result = taxminator.refetch_datum(item, "end_date")
    assert result["value"] == "2026-09-10"


def test_refetch_datum_falls_back_to_the_slug():
    market = make_market(market_id="tx-42", slug="uzb-iran")
    item = _Item("https://taxminator.uz/markets/uzb-iran?utm_source=last30days", {})
    with patch.object(http, "request", return_value={"markets": [market]}):
        result = taxminator.refetch_datum(item, "Yes")
    assert result["value"] == 0.6


def test_refetch_datum_raises_when_identity_does_not_match():
    """A market id that is no longer present must never resolve to another market."""
    other = make_market(market_id="tx-99", slug="other")
    item = _Item("https://taxminator.uz/markets/uzb-iran", {"market_id": "tx-42"})
    with patch.object(http, "request", return_value={"markets": [other]}):
        with pytest.raises(KeyError):
            taxminator.refetch_datum(item, "Yes")


def test_refetch_datum_raises_without_any_identity():
    item = _Item("", {})
    with pytest.raises(ValueError):
        taxminator.refetch_datum(item, "Yes")


def test_refetch_datum_raises_for_an_unknown_datum():
    market = make_market(market_id="tx-42", slug="uzb-iran")
    item = _Item("https://taxminator.uz/markets/uzb-iran", {"market_id": "tx-42"})
    with patch.object(http, "request", return_value={"markets": [market]}):
        with pytest.raises(KeyError):
            taxminator.refetch_datum(item, "Maybe")


# === Fixture end-to-end ===


def test_fixture_parses_into_normalized_items(sample_markets):
    items = parse(sample_markets, topic="Özbekiston")
    assert items, "the Uzbek-language topic must match the Uzbek titles"
    for item in items:
        assert item["url"].startswith("https://taxminator.uz/markets/")
        assert "utm_source=last30days" in item["url"]
        assert isinstance(item["predictors"], int)
        assert 0.0 <= item["relevance"] <= 1.0


def test_fixture_esports_market_matches_in_english(sample_markets):
    items = parse(sample_markets, topic="Esports World Cup Dota 2")
    assert [item["category"] for item in items] == ["ESPORTS"]
    assert items[0]["outcome_prices"][0] == ("Team Falcons", 0.54)


def test_fixture_economy_market_withholds_its_split(sample_markets):
    items = parse(sample_markets, topic="dollar kursi")
    assert len(items) == 1
    assert items[0]["outcome_prices"] == []
    assert "split withheld" in items[0]["crowd_note"]


# === Post-merge filter ===


def test_filter_items_against_topic_drops_off_topic_survivors():
    class Stub:
        def __init__(self, title):
            self.title = title

    items = [Stub("Will Uzbekistan beat Iran?"), Stub("Will Bitcoin go up today?")]
    kept = taxminator.filter_items_against_topic("Uzbekistan Iran qualifier", items)
    assert [item.title for item in kept] == ["Will Uzbekistan beat Iran?"]


def test_filter_items_against_topic_keeps_a_russian_match():
    """The item's title is always English; a Russian topic matched the Russian
    title at retrieval time and must not be dropped post-merge."""

    class Stub:
        title = "Getafe - Celta Vigo: result"
        metadata = {
            "titles": {
                "en": "Getafe - Celta Vigo: result",
                "ru": "Хетафе — Сельта: результат",
                "uz": "Xetafe — Selta: natija",
            }
        }

    items = [Stub()]
    assert taxminator.filter_items_against_topic("Хетафе Сельта", items) == items


def test_filter_items_against_topic_keeps_an_uzbek_match():
    class Stub:
        title = "Will Uzbekistan beat Iran?"
        metadata = {"titles": {"uz": "Özbekiston Eronni yengadimi?"}}

    items = [Stub()]
    assert taxminator.filter_items_against_topic("Özbekiston Eron", items) == items


def test_filter_items_against_topic_is_a_noop_without_a_topic():
    items = [{"title": "anything"}]
    assert taxminator.filter_items_against_topic("", items) is items


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
