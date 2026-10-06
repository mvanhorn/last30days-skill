"""Tests for browser cookie extraction integration in env.py."""

import os
from unittest.mock import patch

import pytest

from lib.env import ConfigLoadPolicy, extract_browser_credentials, COOKIE_DOMAINS


def _base_config(**overrides):
    """Return a minimal config dict with common defaults."""
    cfg = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "TRUTHSOCIAL_TOKEN": None,
        "FROM_BROWSER": None,
        "SETUP_COMPLETE": None,
    }
    cfg.update(overrides)
    return cfg


class TestExtractBrowserCredentials:
    """Unit tests for extract_browser_credentials()."""

    @patch("lib.cookie_extract.extract_cookies")
    def test_auto_populates_credentials(self, mock_extract):
        mock_extract.return_value = {"auth_token": "tok123", "ct0": "ct0val"}
        config = _base_config(FROM_BROWSER="auto")
        result = extract_browser_credentials(config)
        assert result["AUTH_TOKEN"] == "tok123"
        assert result["CT0"] == "ct0val"
        # auto mode tries firefox first, then safari, then chrome
        mock_extract.assert_any_call("firefox", ".x.com", ["auth_token", "ct0"])

    @patch("lib.cookie_extract.extract_cookies")
    def test_explicit_auth_token_skips_x_extraction(self, mock_extract):
        mock_extract.return_value = None
        config = _base_config(
            AUTH_TOKEN="explicit_token", CT0="explicit_ct0",
            FROM_BROWSER="auto",
        )
        result = extract_browser_credentials(config)
        assert "AUTH_TOKEN" not in result
        assert "CT0" not in result
        for call in mock_extract.call_args_list:
            assert call[0][1] != ".x.com"

    @patch("lib.cookie_extract.extract_cookies")
    def test_from_browser_off_skips_all(self, mock_extract):
        config = _base_config(FROM_BROWSER="off")
        result = extract_browser_credentials(config)
        assert result == {}
        mock_extract.assert_not_called()

    @patch("lib.cookie_extract.extract_cookies")
    def test_no_from_browser_skips_all(self, mock_extract):
        """Default (no FROM_BROWSER): reads no browser cookies."""
        mock_extract.return_value = None
        config = _base_config()
        result = extract_browser_credentials(config)
        assert result == {}
        mock_extract.assert_not_called()

    @patch("lib.cookie_extract.extract_cookies")
    def test_from_browser_firefox_only(self, mock_extract):
        mock_extract.return_value = {"auth_token": "ff_tok", "ct0": "ff_ct0"}
        config = _base_config(FROM_BROWSER="firefox")
        result = extract_browser_credentials(config)
        assert result["AUTH_TOKEN"] == "ff_tok"
        for call in mock_extract.call_args_list:
            assert call[0][0] == "firefox"

    @patch("lib.cookie_extract.extract_cookies")
    def test_extraction_returns_none_config_unchanged(self, mock_extract):
        mock_extract.return_value = None
        config = _base_config(FROM_BROWSER="auto")
        result = extract_browser_credentials(config)
        assert "AUTH_TOKEN" not in result
        assert "CT0" not in result

    @patch("lib.cookie_extract.extract_cookies")
    def test_extraction_raises_exception_caught(self, mock_extract):
        mock_extract.side_effect = RuntimeError("database locked")
        config = _base_config(FROM_BROWSER="auto")
        result = extract_browser_credentials(config)
        assert "AUTH_TOKEN" not in result
        assert "CT0" not in result

    @patch("lib.cookie_extract.extract_cookies")
    def test_partial_credentials_only_fills_missing(self, mock_extract):
        mock_extract.return_value = {"auth_token": "cookie_tok", "ct0": "cookie_ct0"}
        config = _base_config(
            AUTH_TOKEN="explicit", CT0=None,
            FROM_BROWSER="auto",
        )
        result = extract_browser_credentials(config)
        assert "AUTH_TOKEN" not in result
        assert result["CT0"] == "cookie_ct0"

    @patch("lib.cookie_extract.extract_cookies")
    def test_partial_pair_does_not_shadow_complete_browser(self, mock_extract):
        """A partial X pair (lone ct0 from a logged-out session) must not
        stop the scan: the complete pair from a later browser wins."""
        def side_effect(browser, domain, cookie_names):
            if domain == ".x.com":
                if browser == "firefox":
                    return {"ct0": "logged_out_ct0"}
                if browser == "safari":
                    return {"auth_token": "tok", "ct0": "ct0"}
            if domain == ".truthsocial.com":
                return {"_session_id": "sess"}
            return None
        mock_extract.side_effect = side_effect
        config = _base_config(FROM_BROWSER="firefox,safari")
        result = extract_browser_credentials(config)
        assert result["AUTH_TOKEN"] == "tok"
        assert result["CT0"] == "ct0"

    @patch("lib.cookie_extract.extract_cookies")
    def test_complementary_partials_never_merge_across_browsers(self, mock_extract):
        """auth_token from one browser and ct0 from another can belong to
        different X sessions: only the first browser's partial is kept."""
        def side_effect(browser, domain, cookie_names):
            if domain == ".x.com":
                if browser == "firefox":
                    return {"auth_token": "tok"}
                if browser == "safari":
                    return {"ct0": "other_session_ct0"}
            return None
        mock_extract.side_effect = side_effect
        config = _base_config(FROM_BROWSER="firefox,safari")
        result = extract_browser_credentials(config)
        assert result["AUTH_TOKEN"] == "tok"
        assert "CT0" not in result

    @patch("lib.cookie_extract.extract_cookies")
    def test_partial_then_complete_uses_only_complete_browser(self, mock_extract):
        """A complete pair from a later browser replaces an earlier partial
        wholesale; no value from the partial browser survives."""
        def side_effect(browser, domain, cookie_names):
            if domain == ".x.com":
                if browser == "firefox":
                    return {"auth_token": "stale_tok"}
                if browser == "safari":
                    return {"auth_token": "tok", "ct0": "ct0"}
            return None
        mock_extract.side_effect = side_effect
        config = _base_config(FROM_BROWSER="firefox,safari")
        result = extract_browser_credentials(config)
        assert result == {"AUTH_TOKEN": "tok", "CT0": "ct0"}


class TestGetConfigCookieIntegration:
    """Integration tests for policy-gated cookie extraction in get_config()."""

    @patch("lib.cookie_extract.extract_cookies")
    @patch("lib.env._find_project_env", return_value=None)
    @patch("lib.env.load_env_file", return_value={})
    @patch("lib.env._load_keychain", return_value={})
    @patch("lib.env.get_openai_auth")
    def test_get_config_default_does_not_extract_cookies(
        self, mock_openai, mock_keychain, mock_load, mock_proj, mock_extract
    ):
        from lib.env import get_config, OpenAIAuth
        mock_openai.return_value = OpenAIAuth(
            token=None, source="none", status="missing",
        )
        mock_extract.return_value = {"auth_token": "browser_tok", "ct0": "browser_ct0"}
        env_patch = {
            "SETUP_COMPLETE": "true",
            "FROM_BROWSER": "auto",
            "LAST30DAYS_CONFIG_DIR": "",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            config = get_config()
        assert config["AUTH_TOKEN"] is None
        assert config["CT0"] is None
        mock_extract.assert_not_called()

    @patch("lib.cookie_extract.extract_cookies")
    @patch("lib.env._find_project_env", return_value=None)
    @patch("lib.env.load_env_file", return_value={})
    @patch("lib.env._load_keychain", return_value={})
    @patch("lib.env.get_openai_auth")
    def test_get_config_with_cookie_policy_injects_cookies(
        self, mock_openai, mock_keychain, mock_load, mock_proj, mock_extract
    ):
        from lib.env import get_config, OpenAIAuth
        mock_openai.return_value = OpenAIAuth(
            token=None, source="none", status="missing",
        )
        mock_extract.return_value = {"auth_token": "browser_tok", "ct0": "browser_ct0"}
        env_patch = {
            "SETUP_COMPLETE": "true",
            "FROM_BROWSER": "auto",
            "LAST30DAYS_CONFIG_DIR": "",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            config = get_config(policy=ConfigLoadPolicy(browser_cookies="read"))
        assert config["AUTH_TOKEN"] == "browser_tok"
        assert config["CT0"] == "browser_ct0"
