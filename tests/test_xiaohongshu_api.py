"""Xiaohongshu source tests.

These stay fully mocked because the real source depends on a logged-in local
xiaohongshu-mcp service, which CI and contributors will not have by default.
"""

from datetime import datetime, timezone
from unittest import mock

import pytest

import last30days as cli
from lib import env, http, pipeline, xiaohongshu_api


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


def test_xiaohongshu_default_api_base_is_localhost():
    assert env.get_xiaohongshu_api_base({}) == "http://localhost:18060"


def test_xiaohongshu_availability_prefers_localhost_and_caches_base():
    config = {}

    def fake_get(url, **kwargs):
        # This mimics an x-mcp/browser-backed service running in the user's
        # normal local browser context.
        assert url.startswith("http://localhost:18060/")
        if url.endswith("/health"):
            return {"success": True}
        if url.endswith("/api/v1/login/status"):
            return {"data": {"is_logged_in": True}}
        raise AssertionError(f"unexpected URL: {url}")

    with mock.patch.object(http, "get", side_effect=fake_get) as get_mock:
        assert env.is_xiaohongshu_available(config) is True

    assert get_mock.call_count == 2
    assert config[env.XIAOHONGSHU_RESOLVED_API_BASE_KEY] == "http://localhost:18060"
    assert env.get_xiaohongshu_api_base(config) == "http://localhost:18060"


def test_xiaohongshu_availability_falls_back_to_docker_host():
    config = {}

    def fake_get(url, **kwargs):
        if url.startswith("http://localhost:18060/"):
            raise http.HTTPError("not running")
        if url == "http://host.docker.internal:18060/health":
            return {"success": True}
        if url == "http://host.docker.internal:18060/api/v1/login/status":
            return {"data": {"is_logged_in": True}}
        raise AssertionError(f"unexpected URL: {url}")

    with mock.patch.object(http, "get", side_effect=fake_get):
        assert env.is_xiaohongshu_available(config) is True

    assert (
        config[env.XIAOHONGSHU_RESOLVED_API_BASE_KEY]
        == "http://host.docker.internal:18060"
    )
    assert env.get_xiaohongshu_api_base(config) == "http://host.docker.internal:18060"


def test_xiaohongshu_explicit_api_base_skips_default_probe():
    config = {"XIAOHONGSHU_API_BASE": "http://custom.local:18060/"}
    seen_urls = []

    def fake_get(url, **kwargs):
        seen_urls.append(url)
        assert url.startswith("http://custom.local:18060/")
        if url.endswith("/health"):
            return {"success": True}
        if url.endswith("/api/v1/login/status"):
            return {"data": {"is_logged_in": True}}
        raise AssertionError(f"unexpected URL: {url}")

    with mock.patch.object(http, "get", side_effect=fake_get):
        assert env.is_xiaohongshu_available(config) is True

    assert seen_urls == [
        "http://custom.local:18060/health",
        "http://custom.local:18060/api/v1/login/status",
    ]
    assert config[env.XIAOHONGSHU_RESOLVED_API_BASE_KEY] == "http://custom.local:18060"


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


def test_xiaohongshu_activates_via_persisted_include_sources(monkeypatch):
    from unittest import mock as _mock
    from lib import env as _env, pipeline as _pipeline

    probe = _mock.Mock(return_value=True)
    monkeypatch.setattr(_env, "is_xiaohongshu_available", probe)

    available = _pipeline.available_sources(
        {"INCLUDE_SOURCES": "xiaohongshu"}, None, x_pending=False
    )

    assert "xiaohongshu" in available
    probe.assert_called_once()


def test_xiaohongshu_probe_never_fires_without_any_opt_in(monkeypatch):
    from unittest import mock as _mock
    from lib import env as _env, pipeline as _pipeline

    probe = _mock.Mock(return_value=True)
    monkeypatch.setattr(_env, "is_xiaohongshu_available", probe)

    available = _pipeline.available_sources({}, None, x_pending=False)

    assert "xiaohongshu" not in available
    probe.assert_not_called()
