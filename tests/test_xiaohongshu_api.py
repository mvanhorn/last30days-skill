"""Xiaohongshu source tests.

These stay fully mocked because the real source depends on a logged-in local
xiaohongshu-mcp service, which CI and contributors will not have by default.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest

import last30days as cli
from lib import http, pipeline, xiaohongshu_api


def test_search_flag_accepts_xhs_alias():
    assert cli.parse_search_flag("xhs") == ["xiaohongshu"]
    assert pipeline.normalize_requested_sources(["xhs"]) == ["xiaohongshu"]


def test_xiaohongshu_requested_source_requires_live_logged_in_service():
    with mock.patch.object(pipeline.env, "is_xiaohongshu_available", return_value=False):
        assert "xiaohongshu" not in pipeline.available_sources(
            {}, requested_sources=["xiaohongshu"],
        )

    with mock.patch.object(pipeline.env, "is_xiaohongshu_available", return_value=True):
        assert "xiaohongshu" in pipeline.available_sources(
            {}, requested_sources=["xiaohongshu"],
        )


def test_to_int_accepts_chinese_count_suffixes():
    assert xiaohongshu_api._to_int("1.2万") == 12000
    assert xiaohongshu_api._to_int("3亿") == 300000000
    assert xiaohongshu_api._to_int("42") == 42
    assert xiaohongshu_api._to_int(None) == 0


def test_search_feeds_normalizes_xiaohongshu_response():
    timestamp_ms = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    response = {
        "data": {
            "feeds": [
                {
                    "id": "note-1",
                    "xsecToken": "token-1",
                    "noteCard": {
                        "displayTitle": "Popular matcha latte",
                        "desc": "A creator review with useful comments.",
                        "time": timestamp_ms,
                        "interactInfo": {
                            "likedCount": "1.2万",
                            "commentCount": "345",
                            "collectedCount": "6,789",
                        },
                    },
                }
            ]
        }
    }

    with mock.patch.object(xiaohongshu_api.http, "get", return_value={"data": {"is_logged_in": True}}) as get_mock, \
            mock.patch.object(xiaohongshu_api.http, "post", return_value=response) as post_mock:
        items = xiaohongshu_api.search_feeds(
            "matcha latte", "2026-06-01", "2026-07-01",
            "http://localhost:18060/", depth="default",
        )

    assert get_mock.call_args.args[0] == "http://localhost:18060/api/v1/login/status"
    assert post_mock.call_args.args[0] == "http://localhost:18060/api/v1/feeds/search"
    payload = post_mock.call_args.args[1]
    assert payload["keyword"] == "matcha latte"
    assert payload["filters"]["publish_time"] == "一周内"

    assert items == [
        {
            "id": "XHS1",
            "title": "Popular matcha latte",
            "url": "https://www.xiaohongshu.com/explore/note-1?xsec_token=token-1",
            "source_domain": "xiaohongshu.com",
            "snippet": "A creator review with useful comments.",
            "date": "2026-07-01",
            "date_confidence": "high",
            "relevance": 1.0,
            "why_relevant": "Xiaohongshu engagement: likes=12000, comments=345, favorites=6789",
            "engagement": {
                "likes": 12000,
                "comments": 345,
                "favorites": 6789,
            },
        }
    ]


def test_search_feeds_requires_logged_in_xiaohongshu_session():
    with mock.patch.object(xiaohongshu_api.http, "get", return_value={"data": {"is_logged_in": False}}):
        with pytest.raises(http.HTTPError, match="not logged in"):
            xiaohongshu_api.search_feeds(
                "matcha latte", "2026-06-01", "2026-07-01",
                "http://localhost:18060",
            )
