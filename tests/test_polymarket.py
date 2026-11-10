"""Tests for polymarket.py - Polymarket prediction market search."""

import json
from unittest.mock import Mock, patch

import pytest

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
    # LOCAL DIVERGENCE from upstream: this asserted False, on the rule that a
    # domain word ("ai") is too broad to be the sole match signal. That rule
    # made domain sweeps impossible — an AI-news topic matched zero AI markets
    # ever, because real titles say "AI" and never "artificial intelligence".
    # _passes_topic_filter now falls back to _DOMAIN_WORDS when the informative
    # words miss, so "AI models" does match an AI market.
    assert polymarket._passes_topic_filter("AI models", "New AI prediction") is True


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



def test_domain_fallback_keeps_soft_sweep_ai_markets():
    """Soft sweep residue may match via domain words (the #859 fix)."""
    assert polymarket._passes_topic_filter(
        "AI frontier developments", "Will AI models beat humans at coding?"
    ) is True



def test_domain_fallback_treats_plural_domain_terms_as_domain():
    """Plural domain tokens (models) must not block soft AI sweeps."""
    assert polymarket._passes_topic_filter(
        "AI models frontier developments",
        "Will AI beat humans at coding by 2027?",
    ) is True
    assert polymarket._passes_topic_filter(
        "AI models", "New AI prediction"
    ) is True


def test_domain_fallback_blocked_when_hard_informative_misses():
    """Mixed topics must not accept unrelated markets via a shared domain token."""
    assert polymarket._passes_topic_filter(
        "MCP protocol benchmark", "Will the Kyoto Protocol survive?"
    ) is False
    assert polymarket._passes_any_informative_word(
        "MCP protocol benchmark", "Will the Kyoto Protocol survive?"
    ) is False


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


def test_shorten_question_fallback_strips_leading_article():
    """The truncation fallback must not keep a leading article like 'an' or 'the'.

    Without stripping, a question like 'an Anthropic Claude model scores...' yields
    a lead name of just 'an', which renders as the mangled 'an 19%' footer fragment.
    """
    result = polymarket._shorten_question(
        "an Anthropic Claude model scores at the top of the leaderboard this month"
    )
    lower = result.lower()
    assert not lower.startswith("a ")
    assert not lower.startswith("an ")
    assert not lower.startswith("the ")


def test_shorten_question_fallback_keeps_non_article_lead():
    """Stripping only removes a leading article, not the first informative word."""
    result = polymarket._shorten_question(
        "Anthropic ships a major Claude model update before the end of this month"
    )
    assert result.lower().startswith("anthropic")

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

@patch('lib.polymarket.http.request')
def test_search_polymarket_http_error_handling(mock_request):
    """Test graceful handling of HTTP errors."""
    from lib.http import HTTPError
    mock_request.side_effect = HTTPError("HTTP 429: Rate limit", status_code=429)
    
    result = polymarket.search_polymarket("test", "2026-01-01", "2026-01-31")
    
    assert mock_request.call_count > 0
    assert all(call.args[0] == "GET" for call in mock_request.call_args_list)
    assert result["events"] == []
    assert "HTTP 429: Rate limit" in result["error"]

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
    
    assert len(items) == 1
    assert items[0]["event_id"] == "evt-123"
    assert items[0]["title"] == "Will AI surpass humans?"
    assert items[0]["outcome_prices"] == [("Yes", 0.6), ("No", 0.4)]


def test_parse_polymarket_response_filters_closed():
    """Test that closed events are filtered."""
    response = {
        "events": [
            create_mock_event(event_id="open", slug="open-event", closed=False,
                              markets=[create_mock_market(market_id="open-market")]),
            create_mock_event(event_id="closed", slug="closed-event", closed=True,
                              markets=[create_mock_market(market_id="still-open-market")]),
        ]
    }
    
    items = polymarket.parse_polymarket_response(response)
    
    assert [item["event_id"] for item in items] == ["open"]
    historical = polymarket.parse_polymarket_response(response, include_closed=True)
    assert [item["event_id"] for item in historical] == ["open", "closed"]


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
    
    assert len(items) == 1
    assert items[0]["url"] == "https://polymarket.com/event/test-event-slug"


def test_parse_polymarket_response_engagement():
    """Test that engagement/volume metrics are captured."""
    response = {
        "events": [create_mock_event(
            title="AI Event",
            volume24hr=250000,
            liquidity=100000,
            markets=[{**create_mock_market(liquidity="1234"), "volume24hr": 5678}],
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="AI")

    assert len(items) == 1
    assert items[0]["volume24hr"] == 250000.0
    assert items[0]["liquidity"] == 100000.0


def _claude_downtime_response():
    """An off-topic Polymarket event that mentions only the generic word 'Claude'."""
    return {
        "events": [
            create_mock_event(
                event_id="evt-noise",
                title="Will Claude go down 3-5 times in June?",
                slug="claude-downtime",
            ),
        ]
    }


def test_parse_polymarket_response_filters_noise_on_full_subquery():
    """A multi-word subquery filters off-topic 'Claude downtime' noise.

    'Claude Code subagents workflow' carries 3 informative words; the downtime
    title matches only one ('claude'), so the min-2 rule drops it.
    """
    items = polymarket.parse_polymarket_response(
        _claude_downtime_response(), topic="Claude Code subagents workflow"
    )
    assert items == []


def test_parse_polymarket_response_narrow_subquery_leaks_noise():
    """The SAME off-topic market leaks through a single-word subquery.

    'claude' has one informative word, so the min-match threshold drops to 1 and
    the downtime market passes. Because the pipeline previously fed the per-subquery
    search_query, filtering swung between these two outcomes across the fanout;
    keying off the stable original topic makes it consistent. This pair pins that
    threshold-by-word-count behavior the wiring fix depends on.
    """
    items = polymarket.parse_polymarket_response(
        _claude_downtime_response(), topic="claude"
    )
    assert len(items) == 1

# === Tests for engagement scoring ===


def test_engagement_with_volume():
    """Missing event metrics fall back to the selected market's values."""
    response = {
        "events": [create_mock_event(
            title="Test",
            volume24hr=0,
            liquidity=0,
            markets=[{**create_mock_market(liquidity="6789"), "volume24hr": "1234"}],
        )]
    }
    
    items = polymarket.parse_polymarket_response(response, topic="test")
    
    assert len(items) == 1
    assert items[0]["volume24hr"] == 1234.0
    assert items[0]["liquidity"] == 6789.0

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
    events = [
        create_mock_event(event_id="golf", title="Golf championship"),
        create_mock_event(event_id="relevant", title="Prediction market"),
        create_mock_event(event_id="cycling", title="Cycling championship"),
    ]
    # A generic topic passes the earlier title gate, leaving the score floor
    # responsible for removing these otherwise valid events.
    assert all(polymarket._passes_topic_filter("market", event["title"]) for event in events)
    items = polymarket.parse_polymarket_response({"events": events}, topic="market")
    assert [item["event_id"] for item in items] == ["relevant"]


def test_per_item_relevance_floor_keeps_borderline(monkeypatch):
    """Items at exactly 0.10 should be kept."""
    scores = {"Main market": 1.0, "Borderline market": 0.13, "Below floor": 0.12}
    monkeypatch.setattr(polymarket, "_compute_text_similarity", lambda _topic, title, _outcomes: scores[title])
    events = [
        create_mock_event(
            event_id=event_id, title=title, volume24hr=0, liquidity=1,
            markets=[create_mock_market(volume="0", liquidity="1")],
        )
        for event_id, title in [("below", "Below floor"), ("main", "Main market"),
                                ("boundary", "Borderline market")]
    ]
    items = polymarket.parse_polymarket_response({"events": events}, topic="market")
    assert [(item["event_id"], item["relevance"]) for item in items] == [
        ("main", 0.75), ("boundary", 0.10),
    ]


def test_per_item_relevance_floor_no_drops_when_all_high():
    """Nothing dropped when all items are above the floor."""
    response = {"events": [
        create_mock_event(event_id=event_id, title=f"Prediction market {event_id}")
        for event_id in ["a", "b", "c"]
    ]}
    items = polymarket.parse_polymarket_response(response, topic="market")
    assert [item["event_id"] for item in items] == ["a", "b", "c"]
    assert all(item["relevance"] >= 0.30 for item in items)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# === Tests for per-market detail ===


def _market(slug, question, *, end_date="2026-12-31T12:00:00Z", **kwargs):
    market = create_mock_market(question=question, **kwargs)
    market["slug"] = slug
    if end_date is not None:
        market["endDate"] = end_date
    return market


def test_multi_market_event_keeps_every_active_market():
    response = {
        "events": [create_mock_event(
            title="Fed decision in December?",
            markets=[
                _market(
                    "fed-cut-25-december",
                    "Will the Fed cut rates by 25 bps in December?",
                    prices='["0.70", "0.30"]',
                    volume="900",
                    end_date="2026-12-10T19:00:00Z",
                ),
                _market(
                    "fed-hold-december",
                    "Will the Fed hold rates in December?",
                    prices='["0.25", "0.75"]',
                    volume="500",
                    end_date="2026-12-11T19:00:00Z",
                ),
                _market(
                    "fed-cut-50-december",
                    "Will the Fed cut rates by 50 bps in December?",
                    prices='["0.05", "0.95"]',
                    volume="100",
                    end_date="2026-12-12T19:00:00Z",
                ),
            ],
        )]
    }

    items = polymarket.parse_polymarket_response(response, topic="Fed")

    assert items[0]["markets"] == [
        {
            "slug": "fed-cut-25-december",
            "question": "Will the Fed cut rates by 25 bps in December?",
            "end_date": "2026-12-10",
            "outcome_prices": [("Yes", 0.7), ("No", 0.3)],
        },
        {
            "slug": "fed-hold-december",
            "question": "Will the Fed hold rates in December?",
            "end_date": "2026-12-11",
            "outcome_prices": [("Yes", 0.25), ("No", 0.75)],
        },
        {
            "slug": "fed-cut-50-december",
            "question": "Will the Fed cut rates by 50 bps in December?",
            "end_date": "2026-12-12",
            "outcome_prices": [("Yes", 0.05), ("No", 0.95)],
        },
    ]


def test_market_keeps_all_outcome_prices_beyond_display_cap():
    response = {
        "events": [create_mock_event(
            title="Who wins the AI race?",
            markets=[_market(
                "ai-race-winner",
                "Who wins the AI race?",
                outcomes='["A", "B", "C", "D", "E"]',
                prices='["0.40", "0.25", "0.20", "0.10", "0.05"]',
            )],
        )]
    }

    items = polymarket.parse_polymarket_response(response)

    assert len(items[0]["outcome_prices"]) == 3
    assert items[0]["markets"][0]["outcome_prices"] == [
        ("A", 0.4), ("B", 0.25), ("C", 0.2), ("D", 0.1), ("E", 0.05),
    ]


def test_single_market_event_matches_top_market_fields():
    response = {
        "events": [create_mock_event(
            title="Will AI surpass humans?",
            markets=[_market(
                "will-ai-surpass-humans",
                "Will AI surpass humans by 2030?",
                end_date="2030-01-01T00:00:00Z",
            )],
        )]
    }

    item = polymarket.parse_polymarket_response(response, topic="AI")[0]

    assert len(item["markets"]) == 1
    market = item["markets"][0]
    assert market["slug"] == "will-ai-surpass-humans"
    assert market["question"] == item["question"]
    assert market["end_date"] == item["end_date"] == "2030-01-01"
    assert market["outcome_prices"] == item["outcome_prices"]


def test_market_without_end_date_is_kept_with_date_absent():
    response = {
        "events": [create_mock_event(
            title="Will AI surpass humans?",
            markets=[
                _market("dated", "Will AI surpass humans by 2030?", volume="900"),
                _market("undated", "Will AI surpass humans ever?", volume="100", end_date=None),
            ],
        )]
    }

    markets = polymarket.parse_polymarket_response(response, topic="AI")[0]["markets"]

    assert [m["slug"] for m in markets] == ["dated", "undated"]
    assert markets[0]["end_date"] == "2026-12-31"
    assert markets[1]["end_date"] is None
    assert markets[1]["outcome_prices"] == [("Yes", 0.6), ("No", 0.4)]


def test_closed_and_zero_liquidity_markets_stay_excluded():
    response = {
        "events": [create_mock_event(
            title="Will AI surpass humans?",
            markets=[
                _market("open", "Will AI surpass humans by 2030?"),
                _market("closed", "Will AI surpass humans by 2025?", closed=True),
                _market("illiquid", "Will AI surpass humans by 2027?", liquidity="0"),
            ],
        )]
    }

    markets = polymarket.parse_polymarket_response(response, topic="AI")[0]["markets"]

    assert [m["slug"] for m in markets] == ["open"]


def _legacy_raw_item():
    """A raw event item shaped as it was before per-market detail existed."""
    return {
        "event_id": "evt-1",
        "title": "Who wins the primary?",
        "question": "Who wins the primary?",
        "url": "https://polymarket.com/event/primary",
        "outcome_prices": [("Kanye", 0.12), ("Other", 0.88)],
        "outcomes_remaining": 0,
        "price_movement": "up 3.0% this week",
        "volume24hr": 1000.0,
        "volume1mo": 50000.0,
        "liquidity": 2000.0,
        "date": "2026-07-01",
        "end_date": "2026-12-31",
        "relevance": 0.8,
        "why_relevant": "Prediction market: Who wins the primary?",
    }


def _normalized(raw):
    from lib import normalize

    return normalize._normalize_polymarket(
        "polymarket", raw, 0, "2026-06-10", "2026-07-10"
    )


def test_normalize_carries_markets_in_metadata():
    markets = [{
        "slug": "primary-kanye",
        "question": "Who wins the primary?",
        "end_date": "2026-12-31",
        "outcome_prices": [("Kanye", 0.12), ("Other", 0.88)],
    }]

    item = _normalized({**_legacy_raw_item(), "markets": markets})

    assert item.metadata["markets"] == markets


def test_footer_and_stats_lines_unchanged_for_legacy_fixture():
    from lib import render
    from tests.test_render_footer import _report

    markets = [{
        "slug": "primary-kanye",
        "question": "Who wins the primary?",
        "end_date": "2026-12-31",
        "outcome_prices": [("Kanye", 0.12), ("Other", 0.88)],
    }]
    legacy = _report(items_by_source={"polymarket": [_normalized(_legacy_raw_item())]})
    current = _report(items_by_source={
        "polymarket": [_normalized({**_legacy_raw_item(), "markets": markets})]
    })

    legacy_footer = render._build_source_footer_lines(legacy)
    legacy_stats = render._render_stats(legacy)

    assert legacy_footer == [
        "📊 Polymarket: 1 market │ Who wins the primary: Kanye 12%"
    ]
    assert "- Polymarket: 1 market | Who wins the primary: Kanye 12%" in legacy_stats
    assert render._build_source_footer_lines(current) == legacy_footer
    assert render._render_stats(current) == legacy_stats
