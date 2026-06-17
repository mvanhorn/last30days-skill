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


def _make_encrypted_cookies_db(path, rows, db_version: int = 24) -> None:
    """Create a Cookies DB with v10-encrypted_value rows (empty value column)."""
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
    for host_key, name, encrypted_value in rows:
        c.execute(
            "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
            (host_key, name, "", encrypted_value),
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

    def test_prefers_network_cookies_over_flat(self, tmp_path):
        """Modern Chromium (>=96) stores under Default/Network/Cookies."""
        (tmp_path / "Default" / "Network").mkdir(parents=True)
        net = tmp_path / "Default" / "Network" / "Cookies"
        net.touch()
        (tmp_path / "Default" / "Cookies").touch()  # legacy flat also present
        assert _find_chromium_cookies_db(tmp_path) == net

    def test_network_cookies_in_numbered_profile(self, tmp_path):
        (tmp_path / "Profile 1" / "Network").mkdir(parents=True)
        net = tmp_path / "Profile 1" / "Network" / "Cookies"
        net.touch()
        assert _find_chromium_cookies_db(tmp_path) == net


class TestLazyKeychain:
    """The Keychain key is fetched only when an encrypted cookie must be decrypted.

    This keeps FROM_BROWSER=auto from prompting for every installed Chromium
    browser - only the one actually holding the requested cookie prompts.
    """

    def _edge_at(self, tmp_path, rows, encrypted=False):
        base = tmp_path / "Edge"
        (base / "Default").mkdir(parents=True)
        db = base / "Default" / "Cookies"
        if encrypted:
            _make_encrypted_cookies_db(db, rows)
        else:
            _make_cookies_db(db, rows)
        return base

    def test_keychain_not_fetched_for_plain_values(self, tmp_path):
        base = self._edge_at(tmp_path, [(".x.com", "auth_token", "plain_tok")])
        with (
            patch.dict("lib.chrome_cookies.CHROMIUM_BROWSER_PROFILES",
                       {"edge": (base, "Microsoft Edge Safe Storage")}),
            patch("lib.chrome_cookies._get_chromium_encryption_key") as key_mock,
        ):
            result = extract_chromium_browser_cookies_macos("edge", ".x.com", ["auth_token"])
        assert result == {"auth_token": "plain_tok"}
        key_mock.assert_not_called()  # no decryption needed -> no Keychain prompt

    def test_keychain_not_fetched_when_no_match(self, tmp_path):
        base = self._edge_at(tmp_path, [(".other.com", "auth_token", "x")])
        with (
            patch.dict("lib.chrome_cookies.CHROMIUM_BROWSER_PROFILES",
                       {"edge": (base, "Microsoft Edge Safe Storage")}),
            patch("lib.chrome_cookies._get_chromium_encryption_key") as key_mock,
        ):
            result = extract_chromium_browser_cookies_macos("edge", ".x.com", ["auth_token"])
        assert result is None
        key_mock.assert_not_called()  # cookie absent -> no Keychain prompt

    def test_keychain_fetched_and_decrypts_v10(self, tmp_path):
        base = self._edge_at(tmp_path, [(".x.com", "auth_token", b"v10ciphertextbytes")], encrypted=True)
        with (
            patch.dict("lib.chrome_cookies.CHROMIUM_BROWSER_PROFILES",
                       {"edge": (base, "Microsoft Edge Safe Storage")}),
            patch("lib.chrome_cookies._get_chromium_encryption_key", return_value=b"passphrase") as key_mock,
            patch("lib.chrome_cookies._decrypt_v10_value", return_value="decrypted_tok") as dec_mock,
        ):
            result = extract_chromium_browser_cookies_macos("edge", ".x.com", ["auth_token"])
        assert result == {"auth_token": "decrypted_tok"}
        key_mock.assert_called_once_with("Microsoft Edge Safe Storage")
        assert dec_mock.called
