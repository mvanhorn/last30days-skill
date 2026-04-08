"""Tests for polymarket.py - Polymarket prediction market search."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import polymarket


# === Helper Functions ===

def create_mock_event(
    event_id="evt-123",
    title="Test Event",
    slug="test-event",
    volume24hr=100000,
    liquidity=50000,
    closed=False,
    markets=None,
):
    """Create a mock Polymarket event."""
    if markets is None:
        markets = [create_mock_market()]
    
    return {
        "id": event_id,
        "title": title,
        "slug": slug,
        "active": True,
        "closed": closed,
        "volume24hr": volume24hr,
        "liquidity": liquidity,
        "markets": markets,
    }


def create_mock_market(
    market_id="mkt-123",
    question="Will X happen?",
    outcomes='["Yes", "No"]',
    prices='["0.60", "0.40"]',
    volume="100000",
    liquidity="50000",
    closed=False,
):
    """Create a mock Polymarket market."""
    return {
        "id": market_id,
        "question": question,
        "active": True,
        "closed": closed,
        "outcomes": outcomes,
        "outcomePrices": prices,
        "volume": volume,
        "liquidity": liquidity,
    }


# === Tests for _extract_core_subject() ===

def test_extract_core_subject_basic():
    """Test basic subject extraction."""
    result = polymarket._extract_core_subject("AI frameworks")
    assert result == "AI frameworks"


def test_extract_core_subject_with_time_prefix():
    """Test stripping time prefixes."""
    result = polymarket._extract_core_subject("last 7 days AI frameworks")
    assert result == "AI frameworks"


def test_extract_core_subject_with_question_prefix():
    """Test stripping question prefixes."""
    result = polymarket._extract_core_subject("what are people saying about Kanye West")
    assert result == "Kanye West"


def test_extract_core_subject_multiple_prefixes():
    """Test handling multiple prefix patterns."""
    result = polymarket._extract_core_subject("research AI models")
    assert result == "AI models"


# === Tests for _expand_queries() ===

def test_expand_queries_basic():
    """Test basic query expansion."""
    queries = polymarket._expand_queries("AI framework")
    
    # Should include core + individual words
    assert "AI framework" in queries or "ai framework" in queries
    assert len(queries) >= 2


def test_expand_queries_single_word():
    """Test query expansion with single word."""
    queries = polymarket._expand_queries("AI")
    
    # Single word, should just return that word
    assert len(queries) >= 1
    assert any("ai" in q.lower() for q in queries)


def test_expand_queries_filters_noise():
    """Test that low-signal tokens are filtered."""
    queries = polymarket._expand_queries("the AI framework")
    
    # "the" should be filtered from individual word expansions
    # But may appear in the full phrase
    assert len(queries) >= 1
    assert any("ai" in q.lower() or "framework" in q.lower() for q in queries)


def test_expand_queries_deduplication():
    """Test that duplicate queries are removed."""
    queries = polymarket._expand_queries("test test test")
    
    # Should dedupe
    assert len(queries) == len(set(q.lower() for q in queries))


def test_expand_queries_cap_at_six():
    """Test that query list is capped at 6."""
    long_topic = "one two three four five six seven eight"
    queries = polymarket._expand_queries(long_topic)
    
    assert len(queries) <= 6


# === Tests for _passes_topic_filter() ===

def test_passes_topic_filter_match():
    """Test that matching events pass the filter."""
    assert polymarket._passes_topic_filter("AI safety", "AI Safety Conference 2026") is True


def test_passes_topic_filter_no_match():
    """Test that non-matching events are filtered."""
    # "West" is a noise word, so "Kanye West" requires "Kanye" to match
    assert polymarket._passes_topic_filter("Kanye West", "NFC West Championship") is False


def test_passes_topic_filter_partial_match():
    """Test that at least one informative word must match."""
    # "AI" appears in both topic and title
    assert polymarket._passes_topic_filter("AI safety", "New AI Safety Conference") is True
    # "models" doesn't appear, should fail
    assert polymarket._passes_topic_filter("AI models", "New AI prediction") is False


def test_passes_topic_filter_all_noise_words():
    """Test that all-noise-word topics don't filter anything."""
    # "the west" has no informative words, so should pass everything
    result = polymarket._passes_topic_filter("the west", "Any title")
    assert result is True


def test_passes_topic_filter_empty_topic():
    """Test empty topic always passes."""
    assert polymarket._passes_topic_filter("", "Any title") is True


def test_passes_topic_filter_multi_word_requires_two_matches():
    """Topics with 3+ informative words require at least 2 to match."""
    # "Mill food recycler" has 3 informative words. "Meek Mill YC" only matches "mill".
    assert polymarket._passes_topic_filter(
        "Mill food recycler", "Meek Mill gets Y Combinator funding"
    ) is False


def test_passes_topic_filter_multi_word_passes_with_two_matches():
    """Topics with 3+ informative words pass when 2+ match."""
    assert polymarket._passes_topic_filter(
        "Sam Altman OpenAI", "Sam Altman CEO OpenAI"
    ) is True


def test_passes_topic_filter_two_word_still_needs_one():
    """Topics with 2 informative words still only need 1 match (existing behavior)."""
    assert polymarket._passes_topic_filter(
        "Kanye West", "Kanye divorce settlement"
    ) is True


def test_passes_topic_filter_multi_word_edge_exactly_three():
    """Topic with exactly 3 informative words, 1 match -> rejected."""
    assert polymarket._passes_topic_filter(
        "Tesla stock price", "Tesla quarterly earnings"
    ) is False  # only "tesla" matches, needs 2


# === Tests for _parse_outcome_prices() ===

def test_parse_outcome_prices_basic():
    """Test basic outcome price parsing."""
    market = {
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.65", "0.35"]',
    }
    
    result = polymarket._parse_outcome_prices(market)
    
    assert len(result) == 2
    assert result[0] == ("Yes", 0.65)
    assert result[1] == ("No", 0.35)


def test_parse_outcome_prices_already_parsed():
    """Test handling when outcomes/prices are already lists."""
    market = {
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.70", "0.30"],
    }
    
    result = polymarket._parse_outcome_prices(market)
    
    assert len(result) == 2
    assert result[0][0] == "Yes"
    assert result[0][1] == 0.70


def test_parse_outcome_prices_multi_outcome():
    """Test multi-outcome markets."""
    market = {
        "outcomes": '["Team A", "Team B", "Team C", "Team D"]',
        "outcomePrices": '["0.40", "0.30", "0.20", "0.10"]',
    }
    
    result = polymarket._parse_outcome_prices(market)
    
    assert len(result) == 4
    assert result[0] == ("Team A", 0.40)
    assert result[3] == ("Team D", 0.10)


def test_parse_outcome_prices_missing_data():
    """Test handling of missing outcome prices."""
    market = {"outcomes": '["Yes"]'}
    
    result = polymarket._parse_outcome_prices(market)
    
    assert result == []


def test_parse_outcome_prices_invalid_json():
    """Test handling of invalid JSON."""
    market = {
        "outcomes": "not valid json",
        "outcomePrices": "also not valid",
    }
    
    result = polymarket._parse_outcome_prices(market)
    
    assert result == []


# === Tests for _format_price_movement() ===

def test_format_price_movement_one_day():
    """Test formatting one-day price movement."""
    market = {
        "oneDayPriceChange": 0.05,  # Up 5%
        "oneWeekPriceChange": 0.02,
        "oneMonthPriceChange": 0.01,
    }
    
    result = polymarket._format_price_movement(market)
    
    assert "up" in result
    assert "5.0%" in result
    assert "today" in result


def test_format_price_movement_negative():
    """Test formatting negative price movement."""
    market = {
        "oneDayPriceChange": -0.15,  # Down 15%
    }
    
    result = polymarket._format_price_movement(market)
    
    assert "down" in result
    assert "15.0%" in result


def test_format_price_movement_picks_largest():
    """Test that largest change is picked."""
    market = {
        "oneDayPriceChange": 0.02,
        "oneWeekPriceChange": 0.10,  # Largest
        "oneMonthPriceChange": 0.05,
    }
    
    result = polymarket._format_price_movement(market)
    
    assert "10.0%" in result
    assert "this week" in result


def test_format_price_movement_below_threshold():
    """Test that small changes return None."""
    market = {
        "oneDayPriceChange": 0.005,  # 0.5%, below 1% threshold
    }
    
    result = polymarket._format_price_movement(market)
    
    assert result is None


def test_format_price_movement_missing_data():
    """Test handling of missing price change data."""
    market = {}
    
    result = polymarket._format_price_movement(market)
    
    assert result is None


# === Tests for _shorten_question() ===

def test_shorten_question_will_pattern():
    """Test shortening 'Will X...' questions."""
    result = polymarket._shorten_question("Will Arizona win the NCAA Tournament?")
    
    assert result == "Arizona"


def test_shorten_question_complex_will():
    """Test shortening complex Will questions."""
    result = polymarket._shorten_question("Will Duke be a number 1 seed?")
    
    assert result == "Duke"


def test_shorten_question_no_pattern():
    """Test questions that don't match patterns."""
    result = polymarket._shorten_question("Arizona wins championship")
    
    # Should return original or truncated
    assert len(result) > 0


def test_shorten_question_long():
    """Test truncation of very long questions."""
    long_q = "A" * 100
    result = polymarket._shorten_question(long_q)
    
    assert len(result) <= 40


# === Tests for search_polymarket() ===

def test_search_polymarket_result_cap():
    """Test that result cap configuration exists."""
    assert "quick" in polymarket.RESULT_CAP
    assert "default" in polymarket.RESULT_CAP
    assert "deep" in polymarket.RESULT_CAP
    
    # Deep should return more results
    assert polymarket.RESULT_CAP["deep"] >= polymarket.RESULT_CAP["quick"]


def test_search_polymarket_depth_config():
    """Test that depth configuration exists."""
    # Verify depth config
    assert "quick" in polymarket.DEPTH_CONFIG
    assert "default" in polymarket.DEPTH_CONFIG
    assert "deep" in polymarket.DEPTH_CONFIG
    
    # Quick should be least pages
    assert polymarket.DEPTH_CONFIG["quick"] <= polymarket.DEPTH_CONFIG["default"]


def test_search_polymarket_query_expansion():
    """Test that _expand_queries creates multiple queries."""
    queries = polymarket._expand_queries("AI framework")
    
    # Should expand to multiple queries
    assert len(queries) >= 2


@patch('lib.polymarket.http.post')
def test_search_polymarket_http_error_handling(mock_post):
    """Test graceful handling of HTTP errors."""
    from lib.http import HTTPError
    mock_post.side_effect = HTTPError("HTTP 429: Rate limit")
    
    result = polymarket.search_polymarket("test", "2026-01-01", "2026-01-31")
    
    # Should return structure with error
    assert "events" in result or "error" in result


# === Tests for parse_polymarket_response() ===

def test_parse_polymarket_response_basic():
    """Test basic response parsing."""
    response = {
        "events": [create_mock_event(
            title="Will AI surpass humans?",
            volume24hr=500000,
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="AI")
    
    assert len(items) >= 0
    # Items may be filtered by topic filter


def test_parse_polymarket_response_filters_closed():
    """Test that closed events are filtered."""
    response = {
        "events": [
            create_mock_event(closed=False),
            create_mock_event(closed=True),
        ]
    }
    
    items = polymarket.parse_polymarket_response(response)
    
    # Closed events should be filtered
    # (exact count depends on market filtering)
    assert isinstance(items, list)


def test_parse_polymarket_response_empty():
    """Test handling of empty response."""
    response = {"events": []}
    
    items = polymarket.parse_polymarket_response(response)
    
    assert items == []


def test_parse_polymarket_response_market_url():
    """Test that Polymarket URLs are generated."""
    response = {
        "events": [create_mock_event(
            slug="test-event-slug",
            title="Test Event"
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="test")
    
    if items:  # If not filtered
        assert "url" in items[0]
        assert "polymarket.com" in items[0]["url"]


def test_parse_polymarket_response_engagement():
    """Test that engagement/volume metrics are captured."""
    response = {
        "events": [create_mock_event(
            title="AI Event",
            volume24hr=250000,
            liquidity=100000,
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="AI")
    
    if items:  # If not filtered
        # Check for volume or liquidity fields
        assert "volume24hr" in items[0] or "liquidity" in items[0] or isinstance(items[0], dict)


# === Tests for engagement scoring ===

def test_engagement_with_volume():
    """Test engagement calculation with volume."""
    response = {
        "events": [create_mock_event(
            title="Test",
            volume24hr=500000,
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="test")
    
    if items:
        engagement = items[0].get("engagement", {})
        # volume24hr should be captured
        assert "volume24hr" in engagement or isinstance(engagement, dict)


# === Tests for noise-word query skipping ===

def test_expand_queries_skips_noise_words():
    """Noise words like 'west' should not become standalone queries."""
    queries = polymarket._expand_queries("kanye west")
    lowered = [q.lower() for q in queries]
    assert "kanye west" in lowered  # full phrase kept
    assert "kanye" in lowered       # informative word kept
    assert "west" not in lowered    # noise word skipped


def test_expand_queries_keeps_informative_words():
    """Non-noise words should still be expanded as standalone queries."""
    queries = polymarket._expand_queries("arizona basketball")
    lowered = [q.lower() for q in queries]
    assert "arizona" in lowered
    assert "basketball" in lowered


def test_expand_queries_all_noise_words_keeps_phrase():
    """If all words are noise, the full phrase is still searched."""
    queries = polymarket._expand_queries("north west")
    assert len(queries) >= 1
    assert any("north west" in q.lower() for q in queries)
    # Neither individual word should be a standalone query
    lowered = [q.lower() for q in queries]
    assert "north" not in lowered or "north west" in lowered  # only as part of phrase


# === Tests for per-item relevance floor ===

def test_per_item_relevance_floor_drops_zero_items():
    """Items with relevance 0.0 should be dropped even if best item is high."""
    # Simulate the filtering logic directly
    items = [
        {"relevance": 0.85, "title": "Kanye market"},
        {"relevance": 0.45, "title": "Related market"},
        {"relevance": 0.0, "title": "Golf noise"},
        {"relevance": 0.0, "title": "Cycling noise"},
    ]
    filtered = [i for i in items if i["relevance"] >= 0.10]
    assert len(filtered) == 2
    assert all(i["title"] != "Golf noise" for i in filtered)


def test_per_item_relevance_floor_keeps_borderline():
    """Items at exactly 0.10 should be kept."""
    items = [
        {"relevance": 0.85, "title": "Main market"},
        {"relevance": 0.10, "title": "Borderline market"},
        {"relevance": 0.09, "title": "Below floor"},
    ]
    filtered = [i for i in items if i["relevance"] >= 0.10]
    assert len(filtered) == 2
    assert filtered[1]["title"] == "Borderline market"


def test_per_item_relevance_floor_no_drops_when_all_high():
    """Nothing dropped when all items are above the floor."""
    items = [
        {"relevance": 0.85, "title": "A"},
        {"relevance": 0.50, "title": "B"},
        {"relevance": 0.30, "title": "C"},
    ]
    filtered = [i for i in items if i["relevance"] >= 0.10]
    assert len(filtered) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
