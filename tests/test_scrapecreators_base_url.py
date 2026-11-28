"""Tests for configurable SCRAPECREATORS_BASE_URL (#1367).

Verifies that ScrapeCreators API calls across all modules respect the
SCRAPECREATORS_BASE_URL override (env var or config) while defaulting
to https://api.scrapecreators.com and supporting legacy aliases and mocks.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from lib import (
    env,
    http,
    instagram,
    linkedin,
    meta_ads,
    permission_preflight,
    pinterest,
    reddit,
    setup_wizard,
    telegram,
    tiktok,
    usage,
    youtube_yt,
)


class TestScrapeCreatorsBaseUrlResolution:
    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("SCRAPECREATORS_BASE_URL", raising=False)
        monkeypatch.delenv("SCRAPE_CREATORS_BASE_URL", raising=False)
        assert env.get_scrapecreators_base_url() == "https://api.scrapecreators.com"
        assert env.get_scrapecreators_base_url({}) == "https://api.scrapecreators.com"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "https://api.lurkapi.com")
        assert env.get_scrapecreators_base_url() == "https://api.lurkapi.com"

    def test_trailing_slash_and_whitespace_stripped(self, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "  https://api.lurkapi.com/  ")
        assert env.get_scrapecreators_base_url() == "https://api.lurkapi.com"

    def test_empty_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "   ")
        assert env.get_scrapecreators_base_url() == "https://api.scrapecreators.com"

    def test_config_dict_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "https://env.example.com")
        config = {"SCRAPECREATORS_BASE_URL": "http://127.0.0.1:8080"}
        assert env.get_scrapecreators_base_url(config) == "http://127.0.0.1:8080"

    def test_legacy_alias_fallback(self, monkeypatch):
        monkeypatch.delenv("SCRAPECREATORS_BASE_URL", raising=False)
        monkeypatch.setenv("SCRAPE_CREATORS_BASE_URL", "https://legacy.example.com/")
        assert env.get_scrapecreators_base_url() == "https://legacy.example.com"

    def test_remote_http_override_is_refused_with_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "http://proxy.example.com/v1")
        assert env.get_scrapecreators_base_url() == "https://api.scrapecreators.com"
        assert "SCRAPECREATORS_BASE_URL" in capsys.readouterr().err

    def test_remote_http_legacy_alias_is_refused_with_warning(self, monkeypatch, capsys):
        monkeypatch.delenv("SCRAPECREATORS_BASE_URL", raising=False)
        monkeypatch.setenv("SCRAPE_CREATORS_BASE_URL", "http://proxy.example.com")
        assert env.get_scrapecreators_base_url() == "https://api.scrapecreators.com"
        assert "SCRAPECREATORS_BASE_URL" in capsys.readouterr().err

    @pytest.mark.parametrize("loopback", [
        "http://localhost:8080",
        "http://127.0.0.1:9000",
        "http://[::1]:8080",
    ])
    def test_loopback_http_override_is_allowed(self, loopback, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", loopback)
        assert env.get_scrapecreators_base_url() == loopback

    def test_permission_preflight_endpoint_override_key(self):
        assert "SCRAPECREATORS_BASE_URL" in permission_preflight.ENDPOINT_OVERRIDE_KEYS

    def test_permission_preflight_rejects_remote_http_override(self):
        preflight = permission_preflight.build(
            {"SCRAPECREATORS_BASE_URL": "http://proxy.example.com"},
            {"has_scrapecreators": True},
        )
        assert "SCRAPECREATORS_BASE_URL" in preflight["network"]["ignored_endpoint_overrides"]
        assert "SCRAPECREATORS_BASE_URL" not in preflight["network"]["endpoint_overrides"]
        assert preflight["status"] == "action_needed"


class TestModuleUrlConstructionWithOverride:
    @pytest.fixture(autouse=True)
    def set_custom_base(self, monkeypatch):
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "https://mock.scrapecreators.local")

    def test_tiktok_endpoints(self):
        assert tiktok._sc_base() == "https://mock.scrapecreators.local/v1/tiktok"
        assert tiktok._sc_root() == "https://mock.scrapecreators.local"

    def test_instagram_endpoints(self):
        assert instagram._sc_base() == "https://mock.scrapecreators.local"

    def test_reddit_endpoints(self):
        assert reddit._sc_base() == "https://mock.scrapecreators.local/v1/reddit"

    def test_youtube_endpoints(self):
        assert youtube_yt._sc_yt_base() == "https://mock.scrapecreators.local/v1/youtube"

    def test_linkedin_endpoints(self):
        assert linkedin._sc_base() == "https://mock.scrapecreators.local/v1/linkedin"

    def test_telegram_endpoints(self):
        assert telegram._sc_base() == "https://mock.scrapecreators.local/v1/telegram"

    def test_pinterest_endpoints(self):
        assert pinterest._sc_base() == "https://mock.scrapecreators.local/v1/pinterest"

    def test_meta_ads_endpoints(self):
        assert meta_ads._sc_base() == "https://mock.scrapecreators.local/v1/facebook/adLibrary"
        assert meta_ads._search_ads_url() == "https://mock.scrapecreators.local/v1/facebook/adLibrary/search/ads"
        assert meta_ads._search_companies_url() == "https://mock.scrapecreators.local/v1/facebook/adLibrary/search/companies"
        assert meta_ads._company_ads_url() == "https://mock.scrapecreators.local/v1/facebook/adLibrary/company/ads"
        assert meta_ads._ad_transcript_url() == "https://mock.scrapecreators.local/v1/facebook/adLibrary/ad/transcript"

    def test_setup_wizard_endpoints(self):
        assert setup_wizard._device_base() == "https://mock.scrapecreators.local/v1/github/device"


class TestMonkeypatchBackwardCompatibility:
    """Existing tests monkeypatch module-level constants; ensure that still works."""

    def test_tiktok_base_override(self, monkeypatch):
        monkeypatch.setattr(tiktok, "SCRAPECREATORS_BASE", "https://custom.test/tiktok")
        assert tiktok._sc_base() == "https://custom.test/tiktok"

    def test_instagram_base_override(self, monkeypatch):
        monkeypatch.setattr(instagram, "SCRAPECREATORS_BASE", "https://custom.test/instagram")
        assert instagram._sc_base() == "https://custom.test/instagram"

    def test_reddit_base_override(self, monkeypatch):
        monkeypatch.setattr(reddit, "SCRAPECREATORS_BASE", "https://custom.test/reddit")
        assert reddit._sc_base() == "https://custom.test/reddit"

    def test_youtube_base_override(self, monkeypatch):
        monkeypatch.setattr(youtube_yt, "SCRAPECREATORS_YT_BASE", "https://custom.test/youtube")
        assert youtube_yt._sc_yt_base() == "https://custom.test/youtube"

    def test_linkedin_base_override(self, monkeypatch):
        monkeypatch.setattr(linkedin, "SC_BASE", "https://custom.test/linkedin")
        assert linkedin._sc_base() == "https://custom.test/linkedin"

    def test_telegram_base_override(self, monkeypatch):
        monkeypatch.setattr(telegram, "SCRAPECREATORS_BASE", "https://custom.test/telegram")
        assert telegram._sc_base() == "https://custom.test/telegram"

    def test_pinterest_base_override(self, monkeypatch):
        monkeypatch.setattr(pinterest, "SCRAPECREATORS_BASE", "https://custom.test/pinterest")
        assert pinterest._sc_base() == "https://custom.test/pinterest"

    def test_meta_ads_base_override(self, monkeypatch):
        monkeypatch.setattr(meta_ads, "SC_BASE", "https://custom.test/meta")
        assert meta_ads._sc_base() == "https://custom.test/meta"
        assert meta_ads._search_ads_url() == "https://custom.test/meta/search/ads"

    def test_meta_ads_direct_url_override(self, monkeypatch):
        monkeypatch.setattr(meta_ads, "SEARCH_ADS_URL", "https://custom.test/direct/search/ads")
        assert meta_ads._search_ads_url() == "https://custom.test/direct/search/ads"


class TestUsageJournalWithCustomBaseUrl:
    def test_usage_journal_recognizes_custom_base_url(self, monkeypatch):
        monkeypatch.setenv(usage.JOURNAL_ENV, "/dev/null")
        monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "https://api.lurkapi.com")
        with patch.object(usage, "begin", return_value=("path", "attempt_1", "scrapecreators")) as mock_begin:
            res = usage.begin_http("https://api.lurkapi.com/v1/reddit/search", "GET")
            assert res is not None
            mock_begin.assert_called_once_with("scrapecreators")


class TestPropagateConfigToEnviron:
    def test_propagates_scrapecreators_base_url(self, monkeypatch):
        import importlib
        import sys

        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "skills", "last30days", "scripts"))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import last30days

        monkeypatch.delenv("SCRAPECREATORS_BASE_URL", raising=False)
        last30days._propagate_config_to_environ({"SCRAPECREATORS_BASE_URL": "https://api.lurkapi.com"})
        assert os.environ.get("SCRAPECREATORS_BASE_URL") == "https://api.lurkapi.com"
