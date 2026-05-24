"""Tests for the GetXAPI X search source."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from skills.last30days.scripts.lib import getxapi


SAMPLE_RESPONSE = {
    "tweets": [
        {
            "id": "123456789",
            "text": "Testing GetXAPI integration with last30days",
            "url": "https://x.com/testuser/status/123456789",
            "createdAt": "Mon May 19 12:00:00 +0000 2026",
            "retweetCount": 5,
            "replyCount": 2,
            "likeCount": 42,
            "quoteCount": 1,
            "viewCount": 1000,
            "bookmarkCount": 3,
            "author": {
                "userName": "testuser",
                "name": "Test User",
            },
        },
        {
            "id": "987654321",
            "text": "Another tweet about the topic",
            "url": "https://x.com/anotheruser/status/987654321",
            "createdAt": "Tue May 20 14:30:00 +0000 2026",
            "retweetCount": 10,
            "replyCount": 5,
            "likeCount": 100,
            "quoteCount": 3,
            "viewCount": 5000,
            "bookmarkCount": 8,
            "author": {
                "userName": "anotheruser",
                "name": "Another User",
            },
        },
    ],
    "tweet_count": 2,
    "has_more": False,
}


def test_no_token_returns_error():
    result = getxapi.search_getxapi("test", "2026-05-01", "2026-05-19", token="")
    assert result["error"] == "No GETXAPI_API_KEY configured"
    assert result["items"] == []


def test_parse_tweet_builds_url():
    tweet = {
        "id": "111",
        "text": "hello world",
        "createdAt": "Mon May 19 12:00:00 +0000 2026",
        "author": {"userName": "bob"},
        "likeCount": 5,
        "retweetCount": 1,
        "replyCount": 0,
        "quoteCount": 0,
        "viewCount": 100,
        "bookmarkCount": 0,
    }
    item = getxapi._parse_tweet(tweet, 0, "hello")
    assert item is not None
    assert item["url"] == "https://x.com/bob/status/111"
    assert item["id"] == "GX1"
    assert item["engagement"]["likes"] == 5
    assert item["date"] == "2026-05-19"


def test_parse_tweet_missing_author_returns_none():
    tweet = {"id": "222", "text": "no author", "author": {}}
    item = getxapi._parse_tweet(tweet, 0, "test")
    assert item is None


def test_parse_tweet_iso_date():
    tweet = {
        "id": "333",
        "text": "iso date tweet",
        "createdAt": "2026-05-19T12:00:00Z",
        "author": {"userName": "alice"},
        "likeCount": 1,
        "retweetCount": 0,
        "replyCount": 0,
        "quoteCount": 0,
        "viewCount": 50,
        "bookmarkCount": 0,
    }
    item = getxapi._parse_tweet(tweet, 0, "test")
    assert item["date"] == "2026-05-19"


def test_expand_queries_quick():
    queries = getxapi.expand_queries("artificial intelligence news", "quick")
    assert len(queries) == 1


def test_expand_queries_default():
    queries = getxapi.expand_queries("artificial intelligence news", "default")
    assert len(queries) <= 2


def test_parse_response():
    response = {"items": [{"id": "GX1"}, {"id": "GX2"}]}
    items = getxapi.parse_response(response)
    assert len(items) == 2


def test_dedup_seen_ids():
    """Duplicate tweet IDs should be skipped."""
    tweets = [
        {
            "id": "same_id",
            "text": "first",
            "createdAt": "Mon May 19 12:00:00 +0000 2026",
            "author": {"userName": "user1"},
            "likeCount": 1, "retweetCount": 0, "replyCount": 0,
            "quoteCount": 0, "viewCount": 10, "bookmarkCount": 0,
        },
        {
            "id": "same_id",
            "text": "duplicate",
            "createdAt": "Mon May 19 12:00:00 +0000 2026",
            "author": {"userName": "user1"},
            "likeCount": 1, "retweetCount": 0, "replyCount": 0,
            "quoteCount": 0, "viewCount": 10, "bookmarkCount": 0,
        },
    ]
    with patch.object(getxapi.http, "get", return_value={"tweets": tweets}):
        result = getxapi.search_getxapi(
            "test", "2026-05-01", "2026-05-19",
            depth="quick", token="test-key",
        )
    assert len(result["items"]) == 1
