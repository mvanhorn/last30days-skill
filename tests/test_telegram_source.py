"""Tests for the Telegram source adapter (lib/telegram.py).

Covers handle parsing, availability gating, pagination, date filtering,
and the channels-required constraint.
"""

from __future__ import annotations

import pytest

from lib import pipeline, telegram


# ---- handle parsing ----

@pytest.mark.parametrize("raw,expected", [
    ("aipost", "aipost"),
    ("@aipost", "aipost"),
    ("https://t.me/aipost", "aipost"),
    ("https://t.me/s/aipost", "aipost"),
    ("http://t.me/durov", "durov"),
    ("t.me/channel", "channel"),
    ("  @spaced  ", "spaced"),
])
def test_parse_channel_handle_valid(raw, expected):
    assert telegram.parse_channel_handle(raw) == expected


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "-1001234567890",
    "-100999888777",
    "https://t.me/joinchat/abcdef",
    "joinchat/xyz",
])
def test_parse_channel_handle_invalid(raw):
    with pytest.raises(telegram.InvalidChannelHandle):
        telegram.parse_channel_handle(raw)


def test_parse_channel_sources_filters_invalid():
    raw = "aipost, @durov, https://t.me/joinchat/secret, -1001234, valid"
    handles = telegram.parse_channel_sources(raw)
    assert handles == ["aipost", "durov", "valid"]


def test_parse_channel_sources_dedupes():
    raw = "aipost, @aipost, https://t.me/aipost, AIPOST"
    handles = telegram.parse_channel_sources(raw)
    assert handles == ["aipost"]


# ---- opt-in availability gating ----

def test_not_available_by_default():
    config = {"SCRAPECREATORS_API_KEY": "test-key"}
    avail = pipeline.available_sources(config)
    assert "telegram" not in avail


def test_not_available_without_channels():
    config = {
        "SCRAPECREATORS_API_KEY": "test-key",
        "INCLUDE_SOURCES": "telegram",
    }
    avail = pipeline.available_sources(config)
    assert "telegram" not in avail


def test_available_when_fully_configured():
    config = {
        "SCRAPECREATORS_API_KEY": "test-key",
        "INCLUDE_SOURCES": "telegram",
        "TELEGRAM_SOURCES": "aipost,durov",
    }
    avail = pipeline.available_sources(config)
    assert "telegram" in avail


def test_available_when_requested():
    config = {
        "SCRAPECREATORS_API_KEY": "test-key",
        "TELEGRAM_SOURCES": "aipost",
    }
    avail = pipeline.available_sources(config, requested_sources=["telegram"])
    assert "telegram" in avail


def test_excluded_when_in_exclude_sources():
    config = {
        "SCRAPECREATORS_API_KEY": "test-key",
        "INCLUDE_SOURCES": "telegram",
        "TELEGRAM_SOURCES": "aipost",
        "EXCLUDE_SOURCES": "telegram",
    }
    avail = pipeline.available_sources(config)
    assert "telegram" not in avail


# ---- is_telegram_configured ----

def test_is_telegram_configured_true():
    config = {
        "SCRAPECREATORS_API_KEY": "test-key",
        "TELEGRAM_SOURCES": "aipost",
    }
    assert telegram.is_telegram_configured(config) is True


def test_is_telegram_configured_false_no_key():
    config = {"TELEGRAM_SOURCES": "aipost"}
    assert telegram.is_telegram_configured(config) is False


def test_is_telegram_configured_false_no_channels():
    config = {"SCRAPECREATORS_API_KEY": "test-key"}
    assert telegram.is_telegram_configured(config) is False


# ---- search without channels returns error ----

def test_search_returns_error_without_channels():
    result = telegram.search_telegram(
        "test topic",
        "2026-08-01",
        "2026-08-21",
        token="test-key",
        config={},
    )
    assert result["items"] == []
    assert "channel list required" in result.get("error", "").lower()


def test_search_returns_error_without_token():
    result = telegram.search_telegram(
        "test topic",
        "2026-08-01",
        "2026-08-21",
        token=None,
        config={"TELEGRAM_SOURCES": "aipost"},
    )
    assert result["items"] == []
    assert "SCRAPECREATORS_API_KEY" in result.get("error", "")


# ---- pagination and date filtering (mocked HTTP) ----

MOCK_CHANNEL = {
    "handle": "testchannel",
    "name": "Test Channel",
    "subscriber_count": 10000,
}

MOCK_POSTS_PAGE1 = [
    {
        "id": "100",
        "channel_handle": "testchannel",
        "url": "https://t.me/testchannel/100",
        "author_name": "Test Channel",
        "text": "Recent post about AI agents",
        "published_at": "2026-08-20T10:00:00+00:00",
        "view_count": 5000,
        "reaction_count": 100,
    },
    {
        "id": "99",
        "channel_handle": "testchannel",
        "url": "https://t.me/testchannel/99",
        "author_name": "Test Channel",
        "text": "Another recent post",
        "published_at": "2026-08-19T10:00:00+00:00",
        "view_count": 3000,
        "reaction_count": 50,
    },
]

MOCK_POSTS_PAGE2 = [
    {
        "id": "98",
        "channel_handle": "testchannel",
        "url": "https://t.me/testchannel/98",
        "author_name": "Test Channel",
        "text": "Old post",
        "published_at": "2026-07-01T10:00:00+00:00",
        "view_count": 1000,
        "reaction_count": 10,
    },
]


def test_pagination_stops_on_old_page(monkeypatch):
    pages_fetched = []

    def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        cursor = params.get("cursor")
        pages_fetched.append(cursor)

        if cursor is None:
            return {
                "success": True,
                "channel": MOCK_CHANNEL,
                "posts": MOCK_POSTS_PAGE1,
                "cursor": "98",
                "has_more": True,
            }
        else:
            return {
                "success": True,
                "channel": MOCK_CHANNEL,
                "posts": MOCK_POSTS_PAGE2,
                "cursor": "50",
                "has_more": True,
            }

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-01",
        "2026-08-21",
        depth="default",
        token="test-key",
        config={"TELEGRAM_SOURCES": "testchannel"},
    )

    assert len(pages_fetched) == 2
    assert len(result["items"]) == 2


def test_date_filtering(monkeypatch):
    def mock_get(url, **kwargs):
        return {
            "success": True,
            "channel": MOCK_CHANNEL,
            "posts": MOCK_POSTS_PAGE1 + MOCK_POSTS_PAGE2,
            "has_more": False,
        }

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-15",
        "2026-08-21",
        token="test-key",
        config={"TELEGRAM_SOURCES": "testchannel"},
    )

    dates = [item["date"] for item in result["items"]]
    assert all(d >= "2026-08-15" for d in dates)
    assert "2026-07-01" not in dates


def test_topic_relevance_scoring(monkeypatch):
    def mock_get(url, **kwargs):
        return {
            "success": True,
            "channel": MOCK_CHANNEL,
            "posts": [
                {
                    "id": "1",
                    "text": "AI agents are transforming software development",
                    "published_at": "2026-08-20T10:00:00+00:00",
                    "view_count": 100,
                    "reaction_count": 10,
                },
                {
                    "id": "2",
                    "text": "Unrelated post about cooking recipes",
                    "published_at": "2026-08-20T10:00:00+00:00",
                    "view_count": 100,
                    "reaction_count": 10,
                },
            ],
            "has_more": False,
        }

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-01",
        "2026-08-21",
        token="test-key",
        config={"TELEGRAM_SOURCES": "testchannel"},
    )

    items = result["items"]
    assert len(items) == 2
    ai_item = next(i for i in items if "AI agents" in i["text"])
    cook_item = next(i for i in items if "cooking" in i["text"])
    assert ai_item["relevance"] > cook_item["relevance"]


def test_depth_page_caps():
    assert telegram.DEPTH_PAGE_CAPS["quick"] == 1
    assert telegram.DEPTH_PAGE_CAPS["default"] == 3
    assert telegram.DEPTH_PAGE_CAPS["deep"] == 6


def test_telegram_max_pages_override(monkeypatch):
    pages_fetched = []

    def mock_get(url, **kwargs):
        params = kwargs.get("params", {})
        pages_fetched.append(params.get("cursor"))
        return {
            "success": True,
            "channel": MOCK_CHANNEL,
            "posts": MOCK_POSTS_PAGE1,
            "cursor": str(len(pages_fetched) * 10),
            "has_more": True,
        }

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-01",
        "2026-08-21",
        depth="quick",
        token="test-key",
        config={
            "TELEGRAM_SOURCES": "testchannel",
            "TELEGRAM_MAX_PAGES": "5",
        },
    )

    assert len(pages_fetched) == 5


def test_http_error_gracefully_handled(monkeypatch):
    def mock_get(url, **kwargs):
        raise telegram.http.HTTPError("Connection failed", status_code=500)

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-01",
        "2026-08-21",
        token="test-key",
        config={"TELEGRAM_SOURCES": "testchannel"},
    )

    assert result["items"] == []


def test_api_error_gracefully_handled(monkeypatch):
    def mock_get(url, **kwargs):
        return {"success": False, "error": "Channel not found"}

    monkeypatch.setattr(telegram.http, "get", mock_get)

    result = telegram.search_telegram(
        "AI agents",
        "2026-08-01",
        "2026-08-21",
        token="test-key",
        config={"TELEGRAM_SOURCES": "testchannel"},
    )

    assert result["items"] == []


# ---- MAX_SOURCE_FETCHES cap ----

def test_telegram_capped_to_single_fetch():
    assert pipeline.MAX_SOURCE_FETCHES.get("telegram") == 1


# ---- normalization ----

def test_normalize_telegram_posts():
    from lib import normalize

    items = [
        {
            "id": "123",
            "handle": "testchannel",
            "display_name": "Test Channel",
            "text": "Test post about AI",
            "url": "https://t.me/testchannel/123",
            "date": "2026-08-20",
            "engagement": {"views": 5000, "reactions": 100},
            "relevance": 0.85,
            "why_relevant": "Telegram @testchannel: Test post about AI",
        },
    ]

    normalized = normalize.normalize_source_items("telegram", items, "2026-08-01", "2026-08-21")
    assert len(normalized) == 1
    item = normalized[0]
    assert item.source == "telegram"
    assert item.item_id == "123"
    assert "Test post about AI" in item.title
