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
    def test_half_pair_from_browser_is_rejected(self, mock_extract):
        """No half-pair: a browser with only auth_token contributes nothing for X."""
        mock_extract.return_value = {"auth_token": "cookie_tok"}  # ct0 missing
        config = _base_config(FROM_BROWSER="firefox")
        result = extract_browser_credentials(config)
        assert "AUTH_TOKEN" not in result
        assert "CT0" not in result


class TestDiscoverXCredentials:
    """Ordered X cookie discovery: agentcookie -> Chrome CDP (complete pair)."""

    def test_agentcookie_wins_first(self):
        from lib.env import discover_x_credentials
        with (
            patch("lib.agentcookie.read_x_cookies",
                  return_value={"auth_token": "test-auth-token", "ct0": "test-ct0"}),
            patch("lib.chrome_cdp.read_x_cookies",
                  side_effect=AssertionError("CDP must not run once agentcookie wins")),
        ):
            creds, source = discover_x_credentials(_base_config())
        assert creds == {"AUTH_TOKEN": "test-auth-token", "CT0": "test-ct0"}
        assert source == "agentcookie"

    def test_falls_through_to_cdp(self):
        from lib.env import discover_x_credentials
        with (
            patch("lib.agentcookie.read_x_cookies", return_value=None),
            patch("lib.chrome_cdp.read_x_cookies",
                  return_value={"auth_token": "test-auth-token", "ct0": "test-ct0"}),
        ):
            creds, source = discover_x_credentials(_base_config())
        assert creds == {"AUTH_TOKEN": "test-auth-token", "CT0": "test-ct0"}
        assert source == "chrome-cdp"

    def test_empty_when_no_source_yields_a_pair(self):
        from lib.env import discover_x_credentials
        with (
            patch("lib.agentcookie.read_x_cookies", return_value=None),
            patch("lib.chrome_cdp.read_x_cookies", return_value=None),
        ):
            creds, source = discover_x_credentials(_base_config())
        assert creds == {}
        assert source == ""


class TestGetConfigDiscoveryIntegration:
    """get_config read-mode wiring of the new discovery sources."""

    @patch("lib.chrome_cdp.read_x_cookies", return_value=None)
    @patch("lib.agentcookie.read_x_cookies",
           return_value={"auth_token": "test-auth-token", "ct0": "test-ct0"})
    @patch("lib.cookie_extract.extract_cookies", return_value=None)
    @patch("lib.env._find_project_env", return_value=None)
    @patch("lib.env.load_env_file", return_value={})
    @patch("lib.env._load_keychain", return_value={})
    @patch("lib.env.get_openai_auth")
    def test_get_config_reads_agentcookie_pair(
        self, mock_openai, mock_keychain, mock_load, mock_proj,
        mock_extract, mock_agentcookie, mock_cdp,
    ):
        from lib.env import get_config, ConfigLoadPolicy, OpenAIAuth
        mock_openai.return_value = OpenAIAuth(token=None, source="none", status="missing")
        env_patch = {"SETUP_COMPLETE": "true", "LAST30DAYS_CONFIG_DIR": ""}
        with patch.dict(os.environ, env_patch, clear=False):
            config = get_config(policy=ConfigLoadPolicy(browser_cookies="read"))
        assert config["AUTH_TOKEN"] == "test-auth-token"
        assert config["CT0"] == "test-ct0"
        assert config["_AUTH_TOKEN_SOURCE"] == "agentcookie"

    @patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("no discovery when pair present"))
    @patch("lib.agentcookie.read_x_cookies", side_effect=AssertionError("no discovery when pair present"))
    @patch("lib.cookie_extract.extract_cookies", return_value=None)
    @patch("lib.env._find_project_env", return_value=None)
    @patch("lib.env.load_env_file", return_value={})
    @patch("lib.env._load_keychain", return_value={})
    @patch("lib.env.get_openai_auth")
    def test_explicit_pair_not_overwritten_by_discovery(
        self, mock_openai, mock_keychain, mock_load, mock_proj,
        mock_extract, mock_agentcookie, mock_cdp,
    ):
        from lib.env import get_config, ConfigLoadPolicy, OpenAIAuth
        mock_openai.return_value = OpenAIAuth(token=None, source="none", status="missing")
        env_patch = {
            "SETUP_COMPLETE": "true",
            "LAST30DAYS_CONFIG_DIR": "",
            "AUTH_TOKEN": "explicit-token",
            "CT0": "explicit-ct0",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            config = get_config(policy=ConfigLoadPolicy(browser_cookies="read"))
        assert config["AUTH_TOKEN"] == "explicit-token"
        assert config["CT0"] == "explicit-ct0"


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
