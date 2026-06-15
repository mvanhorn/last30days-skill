"""Tests for the extended Chromium-family browser cookie support.

Covers the three layers wired up for Brave/Edge/Vivaldi/Opera/Arc/Chromium:
  - env.extract_browser_credentials  (which browsers FROM_BROWSER selects)
  - cookie_extract                   (routing browser name -> extractor)
  - chrome_cookies                   (registry, profile finder, extraction)
"""

import sqlite3
from unittest.mock import patch

import pytest

from lib.env import extract_browser_credentials
from lib.cookie_extract import extract_cookies
from lib.chrome_cookies import (
    CHROMIUM_BROWSER_PROFILES,
    _find_chromium_cookies_db,
    extract_chromium_browser_cookies_macos,
)

# The Chromium-based browsers added on top of the original Chrome support.
NEW_CHROMIUM_BROWSERS = ["brave", "edge", "vivaldi", "opera", "arc", "chromium"]
ALL_AUTO_BROWSERS = ["firefox", "safari", "chrome", *NEW_CHROMIUM_BROWSERS]


def _base_config(**overrides):
    cfg = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "TRUTHSOCIAL_TOKEN": None,
        "FROM_BROWSER": None,
        "SETUP_COMPLETE": None,
    }
    cfg.update(overrides)
    return cfg


def _make_cookies_db(path, rows, db_version: int = 20) -> None:
    """Create a minimal Chromium Cookies SQLite DB with plain (unencrypted) values."""
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)", (str(db_version),))
    c.execute(
        "CREATE TABLE cookies ("
        "  host_key TEXT NOT NULL,"
        "  name TEXT NOT NULL,"
        "  value TEXT NOT NULL DEFAULT '',"
        "  encrypted_value BLOB NOT NULL DEFAULT x''"
        ")"
    )
    for host_key, name, value in rows:
        c.execute(
            "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
            (host_key, name, value, b""),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# env.py: FROM_BROWSER selects the right browsers
# ---------------------------------------------------------------------------


class TestEnvBrowserSelection:
    @pytest.mark.parametrize("browser", NEW_CHROMIUM_BROWSERS)
    @patch("lib.cookie_extract.extract_cookies")
    def test_explicit_chromium_browser_is_used(self, mock_extract, browser):
        """FROM_BROWSER=<chromium browser> routes extraction to that browser."""
        mock_extract.return_value = {"auth_token": "tok", "ct0": "ct0val"}
        config = _base_config(FROM_BROWSER=browser)

        result = extract_browser_credentials(config)

        assert result["AUTH_TOKEN"] == "tok"
        assert result["CT0"] == "ct0val"
        # Every extraction call targeted exactly the requested browser.
        assert mock_extract.call_args_list
        for call in mock_extract.call_args_list:
            assert call[0][0] == browser

    @patch("lib.cookie_extract.extract_cookies")
    def test_auto_tries_every_chromium_browser(self, mock_extract):
        """FROM_BROWSER=auto tries Firefox/Safari plus the whole Chromium family."""
        mock_extract.return_value = None  # force it to try them all
        config = _base_config(FROM_BROWSER="auto")

        extract_browser_credentials(config)

        tried = {call[0][0] for call in mock_extract.call_args_list}
        for browser in ALL_AUTO_BROWSERS:
            assert browser in tried, f"auto should try {browser}"

    @patch("lib.cookie_extract.extract_cookies")
    def test_default_still_silent_only(self, mock_extract):
        """Default (no FROM_BROWSER) stays Firefox+Safari - no Keychain prompt."""
        mock_extract.return_value = None
        config = _base_config()

        extract_browser_credentials(config)

        tried = {call[0][0] for call in mock_extract.call_args_list}
        assert tried == {"firefox", "safari"}


# ---------------------------------------------------------------------------
# cookie_extract.py: browser name routes to the chrome_cookies registry
# ---------------------------------------------------------------------------


class TestCookieExtractRouting:
    @pytest.mark.parametrize("browser", ["edge", "vivaldi", "opera", "arc", "chromium"])
    def test_routes_to_registry(self, browser):
        with (
            patch("lib.cookie_extract.platform.system", return_value="Darwin"),
            patch(
                "lib.chrome_cookies.extract_chromium_browser_cookies_macos",
                return_value={"auth_token": f"{browser}_tok"},
            ) as mock_macos,
        ):
            result = extract_cookies(browser, ".x.com", ["auth_token"])

        assert result == {"auth_token": f"{browser}_tok"}
        # The browser key is threaded through to the macOS extractor.
        assert mock_macos.call_args[0][0] == browser

    @pytest.mark.parametrize("browser", ["edge", "vivaldi", "opera", "arc", "chromium"])
    def test_non_macos_returns_none(self, browser):
        with patch("lib.cookie_extract.platform.system", return_value="Linux"):
            assert extract_cookies(browser, ".x.com", ["auth_token"]) is None

    def test_auto_macos_order_includes_chromium_family(self):
        """auto on macOS calls every Chromium-family extractor when all miss."""
        with (
            patch("lib.cookie_extract.platform.system", return_value="Darwin"),
            patch("lib.cookie_extract._extract_firefox_with_source", return_value=None),
            patch("lib.cookie_extract.extract_chrome_cookies", return_value=None) as m_chrome,
            patch("lib.cookie_extract.extract_brave_cookies", return_value=None) as m_brave,
            patch("lib.cookie_extract.extract_edge_cookies", return_value=None) as m_edge,
            patch("lib.cookie_extract.extract_vivaldi_cookies", return_value=None) as m_viv,
            patch("lib.cookie_extract.extract_opera_cookies", return_value=None) as m_opera,
            patch("lib.cookie_extract.extract_arc_cookies", return_value=None) as m_arc,
            patch("lib.cookie_extract.extract_chromium_cookies", return_value=None) as m_chr,
            patch("lib.cookie_extract.extract_safari_cookies", return_value=None),
        ):
            result = extract_cookies("auto", ".x.com", ["auth_token"])

        assert result is None
        for mock_fn in (m_chrome, m_brave, m_edge, m_viv, m_opera, m_arc, m_chr):
            mock_fn.assert_called_once_with(".x.com", ["auth_token"])


# ---------------------------------------------------------------------------
# chrome_cookies.py: registry, profile finder, generic extraction
# ---------------------------------------------------------------------------


class TestChromiumRegistry:
    def test_registry_has_expected_browsers(self):
        assert set(CHROMIUM_BROWSER_PROFILES) == {"edge", "vivaldi", "opera", "arc", "chromium"}
        for base_dir, service in CHROMIUM_BROWSER_PROFILES.values():
            assert service.endswith("Safe Storage")
            assert base_dir is not None

    def test_unknown_browser_returns_none(self):
        assert extract_chromium_browser_cookies_macos("netscape", ".x.com", ["auth_token"]) is None

    def test_generic_extraction_plain_values(self, tmp_path):
        """A registry browser extracts unencrypted cookies via the shared core."""
        base = tmp_path / "Edge"
        (base / "Default").mkdir(parents=True)
        _make_cookies_db(
            base / "Default" / "Cookies",
            [
                (".x.com", "auth_token", "edge_auth"),
                (".x.com", "ct0", "edge_ct0"),
                (".other.com", "session", "nope"),
            ],
        )

        with (
            patch.dict(
                "lib.chrome_cookies.CHROMIUM_BROWSER_PROFILES",
                {"edge": (base, "Microsoft Edge Safe Storage")},
            ),
            # Plain values need no Keychain; ensure we never prompt.
            patch("lib.chrome_cookies._get_chromium_encryption_key", return_value=None),
        ):
            result = extract_chromium_browser_cookies_macos("edge", ".x.com", ["auth_token", "ct0"])

        assert result == {"auth_token": "edge_auth", "ct0": "edge_ct0"}

    def test_db_not_found_returns_none(self, tmp_path):
        empty = tmp_path / "Vivaldi"
        empty.mkdir()
        with patch.dict(
            "lib.chrome_cookies.CHROMIUM_BROWSER_PROFILES",
            {"vivaldi": (empty, "Vivaldi Safe Storage")},
        ):
            assert extract_chromium_browser_cookies_macos("vivaldi", ".x.com", ["auth_token"]) is None


class TestFindChromiumCookiesDb:
    def test_prefers_default_profile(self, tmp_path):
        (tmp_path / "Default").mkdir()
        default_db = tmp_path / "Default" / "Cookies"
        default_db.touch()
        (tmp_path / "Cookies").touch()  # direct file should be ignored
        assert _find_chromium_cookies_db(tmp_path) == default_db

    def test_falls_back_to_direct_cookies(self, tmp_path):
        """Opera-style layout: Cookies directly under the base dir."""
        direct = tmp_path / "Cookies"
        direct.touch()
        assert _find_chromium_cookies_db(tmp_path) == direct

    def test_falls_back_to_numbered_profile(self, tmp_path):
        prof = tmp_path / "Profile 2"
        prof.mkdir()
        db = prof / "Cookies"
        db.touch()
        assert _find_chromium_cookies_db(tmp_path) == db

    def test_returns_none_when_missing(self, tmp_path):
        assert _find_chromium_cookies_db(tmp_path) is None
