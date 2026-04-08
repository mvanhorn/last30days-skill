"""Tests for browser cookie extraction integration in env.py."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib.env import extract_browser_credentials, COOKIE_DOMAINS


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
    def test_no_from_browser_defaults_to_silent(self, mock_extract):
        """Default (no FROM_BROWSER): tries Firefox and Safari only, skips Chrome."""
        mock_extract.return_value = None
        config = _base_config()
        result = extract_browser_credentials(config)
        assert result == {}
        # Should try firefox and safari but NOT chrome
        browser_args = [call[0][0] for call in mock_extract.call_args_list]
        assert "firefox" in browser_args
        assert "safari" in browser_args
        assert "chrome" not in browser_args

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


class TestGetConfigCookieIntegration:
    """Integration test: get_config() calls extract_browser_credentials."""

    @patch("lib.cookie_extract.extract_cookies")
    @patch("lib.env._find_project_env", return_value=None)
    @patch("lib.env.load_env_file", return_value={})
    @patch("lib.env.get_openai_auth")
    def test_get_config_injects_cookies(
        self, mock_openai, mock_load, mock_proj, mock_extract
    ):
        from lib.env import get_config, OpenAIAuth
        mock_openai.return_value = OpenAIAuth(
            token=None, source="none", status="missing",
            account_id=None, codex_auth_file="/fake",
        )
        mock_extract.return_value = {"auth_token": "browser_tok", "ct0": "browser_ct0"}
        env_patch = {
            "SETUP_COMPLETE": "true",
            "FROM_BROWSER": "auto",
            "LAST30DAYS_CONFIG_DIR": "",
        }
        with patch.dict(os.environ, env_patch, clear=False):
            config = get_config()
        assert config["AUTH_TOKEN"] == "browser_tok"
        assert config["CT0"] == "browser_ct0"
