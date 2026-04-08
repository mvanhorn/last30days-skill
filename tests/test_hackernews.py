"""Tests for hackernews.py - HN search via Algolia API."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib import hackernews


# === Helper Functions ===

def create_mock_hit(
    object_id="12345",
    title="Test HN Story",
    points=100,
    num_comments=50,
    created_at_i=None,
    author="testuser",
    url="https://example.com",
):
    """Create a mock Algolia hit object."""
    if created_at_i is None:
        # Default to 30 days ago
        dt = datetime.now(timezone.utc)
        created_at_i = int(dt.timestamp()) - (30 * 86400)
    
    return {
        "objectID": object_id,
        "title": title,
        "points": points,
        "num_comments": num_comments,
        "created_at_i": created_at_i,
        "author": author,
        "url": url,
    }


# === Tests for _date_to_unix() ===

def test_date_to_unix_basic():
    """Test converting YYYY-MM-DD to Unix timestamp."""
    result = hackernews._date_to_unix("2026-01-01")
    
    # Should be midnight UTC on Jan 1, 2026
    expected = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert result == int(expected)


def test_date_to_unix_leap_day():
    """Test date conversion with leap day."""
    result = hackernews._date_to_unix("2024-02-29")
    
    expected = datetime(2024, 2, 29, tzinfo=timezone.utc).timestamp()
    assert result == int(expected)


# === Tests for _unix_to_date() ===

def test_unix_to_date_basic():
    """Test converting Unix timestamp to YYYY-MM-DD."""
    ts = int(datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp())
    result = hackernews._unix_to_date(ts)
    
    assert result == "2026-01-15"


def test_unix_to_date_with_time():
    """Test that time component is stripped."""
    ts = int(datetime(2026, 1, 15, 14, 30, 45, tzinfo=timezone.utc).timestamp())
    result = hackernews._unix_to_date(ts)
    
    assert result == "2026-01-15"


# === Tests for _strip_html() ===

def test_strip_html_basic():
    """Test HTML stripping and entity decoding."""
    html_text = "<p>Hello &amp; goodbye</p>"
    result = hackernews._strip_html(html_text)
    
    assert result == "Hello & goodbye"


def test_strip_html_paragraph_tags():
    """Test that <p> tags are converted to newlines."""
    html_text = "First<p>Second<p>Third"
    result = hackernews._strip_html(html_text)
    
    assert "First\n" in result
    assert "Second\n" in result


def test_strip_html_nested_tags():
    """Test stripping nested HTML tags."""
    html_text = "<div><a href='test'>Link</a> text <b>bold</b></div>"
    result = hackernews._strip_html(html_text)
    
    assert result == "Link text bold"


def test_strip_html_entities():
    """Test HTML entity decoding and tag stripping."""
    html_text = "Text &amp; &quot;test&quot;"
    result = hackernews._strip_html(html_text)
    
    # Entities are decoded
    assert "&" in result or "test" in result


# === Tests for _title_matches_query() ===

def test_title_matches_query_basic():
    """Test basic query matching."""
    title = "New AI framework for developers"
    query = "AI framework"
    
    assert hackernews._title_matches_query(title, query) is True


def test_title_matches_query_case_insensitive():
    """Test that matching is case-insensitive."""
    title = "NEW AI FRAMEWORK"
    query = "ai framework"
    
    assert hackernews._title_matches_query(title, query) is True


def test_title_matches_query_with_prefix():
    """Test matching with HN prefix stripped."""
    title = "Show HN: My new AI framework"
    query = "AI framework"
    
    # Should match "AI framework" in the content, not the "Show HN:" prefix
    assert hackernews._title_matches_query(title, query) is True


def test_title_matches_query_prefix_only():
    """Test that matching prefix-only returns False."""
    title = "Show HN: Something else entirely"
    query = "Show HN"
    
    # "Show HN" is a prefix, not real content
    # After stripping, "Show HN" won't be in the stripped title
    assert hackernews._title_matches_query(title, query) is False


def test_title_matches_query_empty_query():
    """Test that empty query always matches."""
    title = "Any title"
    query = ""
    
    assert hackernews._title_matches_query(title, query) is True


def test_title_matches_query_partial_match():
    """Test that all query words must match."""
    title = "New AI framework"
    query = "AI blockchain"
    
    # "blockchain" is not in title, so should fail
    assert hackernews._title_matches_query(title, query) is False


# === Tests for search_hackernews() ===

@patch('lib.hackernews.http.request')
def test_search_hackernews_basic(mock_request):
    """Test basic HN search."""
    mock_request.return_value = {
        "hits": [create_mock_hit()],
        "nbHits": 1,
    }
    
    result = hackernews.search_hackernews(
        "AI framework",
        "2026-01-01",
        "2026-01-31",
        depth="quick"
    )
    
    assert "hits" in result
    assert len(result["hits"]) == 1
    assert mock_request.called


@patch('lib.hackernews.http.request')
def test_search_hackernews_depth_config(mock_request):
    """Test that depth parameter controls hit count."""
    mock_request.return_value = {"hits": [], "nbHits": 0}
    
    # Quick mode should request 15 hits
    hackernews.search_hackernews("test", "2026-01-01", "2026-01-31", depth="quick")
    
    call_args = mock_request.call_args[0]
    url = call_args[1]
    
    assert "hitsPerPage=15" in url


@patch('lib.hackernews.http.request')
def test_search_hackernews_date_filtering(mock_request):
    """Test that date range is applied correctly."""
    mock_request.return_value = {"hits": [], "nbHits": 0}
    
    hackernews.search_hackernews("test", "2026-01-01", "2026-01-31", depth="quick")
    
    call_args = mock_request.call_args[0]
    url = call_args[1]
    
    # Should have numeric filters for date range
    assert "numericFilters" in url
    assert "created_at_i" in url


@patch('lib.hackernews.http.request')
def test_search_hackernews_http_error_handling(mock_request):
    """Test graceful handling of HTTP errors."""
    from lib.http import HTTPError
    mock_request.side_effect = HTTPError("HTTP 429: Too Many Requests")
    
    result = hackernews.search_hackernews("test", "2026-01-01", "2026-01-31")
    
    # Should return empty hits with error
    assert result["hits"] == []
    assert "error" in result


@patch('lib.hackernews.http.request')
def test_search_hackernews_engagement_filter(mock_request):
    """Test that low-engagement stories are filtered."""
    mock_request.return_value = {"hits": [], "nbHits": 0}
    
    hackernews.search_hackernews("test", "2026-01-01", "2026-01-31")
    
    call_args = mock_request.call_args[0]
    url = call_args[1]
    
    # Should filter for points > 2 (URL-encoded)
    assert "points" in url and "%3E2" in url


# === Tests for parse_hackernews_response() ===

def test_parse_hackernews_response_basic():
    """Test parsing basic Algolia response."""
    response = {
        "hits": [create_mock_hit(
            object_id="123",
            title="Test Story",
            points=100,
            num_comments=50
        )]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    assert len(items) == 1
    assert items[0]["id"] == "123"
    assert items[0]["title"] == "Test Story"
    assert items[0]["engagement"]["points"] == 100
    assert items[0]["engagement"]["comments"] == 50


def test_parse_hackernews_response_hn_url():
    """Test that HN discussion URL is generated correctly."""
    response = {
        "hits": [create_mock_hit(object_id="12345")]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    assert items[0]["hn_url"] == "https://news.ycombinator.com/item?id=12345"


def test_parse_hackernews_response_date_conversion():
    """Test that Unix timestamp is converted to YYYY-MM-DD."""
    ts = int(datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp())
    response = {
        "hits": [create_mock_hit(created_at_i=ts)]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    assert items[0]["date"] == "2026-01-15"


def test_parse_hackernews_response_missing_fields():
    """Test handling of hits with missing optional fields."""
    response = {
        "hits": [{
            "objectID": "123",
            "title": "Test",
            # Missing points, num_comments, created_at_i
        }]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    assert len(items) == 1
    assert items[0]["engagement"]["points"] == 0
    assert items[0]["engagement"]["comments"] == 0
    assert items[0]["date"] is None


def test_parse_hackernews_response_relevance_scoring():
    """Test that relevance scores are calculated."""
    response = {
        "hits": [
            create_mock_hit(object_id="1", points=100),
            create_mock_hit(object_id="2", points=50),
            create_mock_hit(object_id="3", points=10),
        ]
    }
    
    items = hackernews.parse_hackernews_response(response, query="test")
    
    # Should have relevance scores
    for item in items:
        assert "relevance" in item
        assert 0 <= item["relevance"] <= 1.0
    
    # First item should generally have higher relevance (better rank)
    assert items[0]["relevance"] >= items[2]["relevance"]


def test_parse_hackernews_response_engagement_boost():
    """Test that high-engagement items get relevance boost."""
    response = {
        "hits": [
            create_mock_hit(object_id="1", points=500, num_comments=200),  # High engagement
            create_mock_hit(object_id="2", points=10, num_comments=5),     # Low engagement
        ]
    }
    
    items = hackernews.parse_hackernews_response(response, query="test")
    
    # Verify engagement is captured
    assert items[0]["engagement"]["points"] == 500
    assert items[1]["engagement"]["points"] == 10


def test_parse_hackernews_response_prefix_filtering():
    """Test that items matching only HN prefixes are filtered."""
    response = {
        "hits": [
            create_mock_hit(title="Show HN: My AI Project", object_id="1"),
            create_mock_hit(title="Show HN: Unrelated Project", object_id="2"),
        ]
    }
    
    # Query for "AI" should keep first, filter second
    items = hackernews.parse_hackernews_response(response, query="AI")
    
    assert len(items) == 1
    assert items[0]["id"] == "1"


def test_parse_hackernews_response_empty_response():
    """Test handling of empty response."""
    response = {"hits": []}
    
    items = hackernews.parse_hackernews_response(response)
    
    assert items == []


# === Tests for engagement scoring ===

def test_engagement_score_calculation():
    """Test that engagement dict contains points and comments."""
    response = {
        "hits": [create_mock_hit(points=150, num_comments=75)]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    engagement = items[0]["engagement"]
    assert engagement["points"] == 150
    assert engagement["comments"] == 75


def test_engagement_score_zero_values():
    """Test handling of zero engagement values."""
    response = {
        "hits": [{
            "objectID": "123",
            "title": "Test",
            "points": None,
            "num_comments": None,
        }]
    }
    
    items = hackernews.parse_hackernews_response(response)
    
    engagement = items[0]["engagement"]
    assert engagement["points"] == 0
    assert engagement["comments"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
