"""Tests for polymarket.py - Polymarket prediction market search."""

import copy
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

# === Tests for filter_items_against_topic() ===


def test_topic_filter_blocks_shared_domain_token_when_hard_word_misses():
    """Mixed topics must not accept unrelated markets via a shared domain token."""
    items = [{"title": "Will the Kyoto Protocol survive?"}]
    assert polymarket.filter_items_against_topic("MCP protocol benchmark", items) == []

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

# Gamma public-search, answered by search term.
def _gamma(responses):
    """A stand-in for http.request that answers each public-search by its q."""
    calls = []

    def request(method, url, **_kwargs):
        from urllib.parse import parse_qs, urlsplit
        params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls.append(params)
        return copy.deepcopy(responses.get(params["q"], {"events": []}))

    return request, calls


def _search_event(event_id, title, labels=(), closed=False):
    markets = [create_mock_market(market_id=f"{event_id}-{n}", question=f"Will {label} win?")
               for n, label in enumerate(labels)]
    for market, label in zip(markets, labels):
        market["groupItemTitle"] = label
    return {**create_mock_event(event_id=event_id, title=title, closed=closed,
                                markets=markets or None)}


def test_search_issues_one_volume_sorted_call_per_term(monkeypatch):
    request, calls = _gamma({"49ers": {"events": [_search_event("1", "49ers vs Vikings")]}})
    monkeypatch.setattr(polymarket.http, "request", request)

    result = polymarket.search_polymarket("49ers", "2026-10-17", "2026-11-16")

    assert calls == [{
        "q": "49ers", "sort": "volume", "events_status": "active",
        "keep_closed_markets": "0", "limit_per_type": "20",
    }]
    assert [event["id"] for event in result["events"]] == ["1"]


@pytest.mark.parametrize("depth, limit", [("quick", "10"), ("deep", "50")])
def test_search_depth_sets_the_page_size(monkeypatch, depth, limit):
    request, calls = _gamma({})
    monkeypatch.setattr(polymarket.http, "request", request)
    polymarket.search_polymarket("49ers", "2026-10-17", "2026-11-16", depth=depth)
    assert [call["limit_per_type"] for call in calls] == [limit]


def test_search_runs_each_alias_and_merges_events_by_id(monkeypatch):
    shared = _search_event("7", "F1: Las Vegas Grand Prix Winner")
    request, calls = _gamma({
        "Formula 1": {"events": [shared]},
        "F1": {"events": [copy.deepcopy(shared), _search_event("8", "F1: Action of the Year")]},
    })
    monkeypatch.setattr(polymarket.http, "request", request)

    result = polymarket.search_polymarket(
        "Formula 1", "2026-10-17", "2026-11-16",
        hints=polymarket.PolymarketHints.coerce({"aliases": ["F1"]}),
    )

    assert sorted(call["q"] for call in calls) == ["F1", "Formula 1"]
    assert [event["id"] for event in result["events"]] == ["7", "8"]


def test_search_looks_up_a_related_name_only_when_the_pool_lacks_it(monkeypatch):
    rookie = _search_event("2", "NBA: 2026-27 Rookie of the Year",
                           labels=["Yaxel Lendeborg", "Cameron Boozer"])
    request, calls = _gamma({"Warriors": {"events": [
        _search_event("1", "NBA: 2027 Champion", labels=["Golden State Warriors"]), rookie,
    ]}})
    monkeypatch.setattr(polymarket.http, "request", request)

    polymarket.search_polymarket(
        "Warriors", "2026-10-17", "2026-11-16",
        hints=polymarket.PolymarketHints.coerce({"related": [
            {"name": "Yaxel Lendeborg", "relation": "player"},
            {"name": "Stephen Curry", "relation": "player"},
        ]}),
    )

    assert [call["q"] for call in calls] == ["Warriors", "Stephen Curry"]


def test_search_drops_events_gamma_returns_closed(monkeypatch):
    request, _calls = _gamma({"49ers": {"events": [
        _search_event("1", "49ers open"), _search_event("2", "49ers closed", closed=True),
    ]}})
    monkeypatch.setattr(polymarket.http, "request", request)
    result = polymarket.search_polymarket("49ers", "2026-10-17", "2026-11-16")
    assert [event["id"] for event in result["events"]] == ["1"]


class _Response:
    status = 200

    def __init__(self, body):
        self._body = json.dumps(body).encode()
        self.headers = {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _rate_limited(url):
    import io
    import urllib.error
    return urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b""))


def test_search_retries_a_rate_limited_call(monkeypatch):
    answers = [_rate_limited, lambda _url: _Response({"events": [_search_event("1", "49ers")]})]

    def urlopen(req, timeout=None):
        answer = answers.pop(0)(req.full_url)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("lib.http.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("lib.http.time.sleep", lambda _seconds: None)

    result = polymarket.search_polymarket("49ers", "2026-10-17", "2026-11-16")

    assert [event["id"] for event in result["events"]] == ["1"]
    assert "error" not in result


def test_search_reports_repeated_rate_limits_as_an_error(monkeypatch):
    def urlopen(req, timeout=None):
        raise _rate_limited(req.full_url)

    monkeypatch.setattr("lib.http.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("lib.http.time.sleep", lambda _seconds: None)

    result = polymarket.search_polymarket("49ers", "2026-10-17", "2026-11-16")

    assert result["events"] == []
    assert "429" in result["error"]


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

    The downtime title names 'Claude' but not the whole phrase 'Claude Code
    subagents workflow', so the entity matcher drops it.
    """
    items = polymarket.parse_polymarket_response(
        _claude_downtime_response(), topic="Claude Code subagents workflow"
    )
    assert items == []


def test_parse_polymarket_response_narrow_subquery_leaks_noise():
    """The SAME off-topic market leaks through a single-word subquery.

    'claude' is named in the title, so the downtime market passes. Because the
    pipeline previously fed the per-subquery search_query, filtering swung
    between these two outcomes across the fanout; keying off the stable
    original topic makes it consistent.
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

# === Tests for per-item relevance floor ===


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
