"""Tests for browser cookie extraction module."""

import configparser
import sqlite3
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from scripts.lib.cookie_extract import (
    extract_cookies,
    extract_firefox_cookies,
    _find_default_profile,
    _get_firefox_profiles_dir,
)


@pytest.fixture
def mock_firefox_env(tmp_path):
    """Create a mock Firefox profiles directory with cookies.sqlite.

    Returns (profiles_dir, profile_dir) for patching.
    """

    def _make(
        *,
        profiles_ini=None,        # type: Optional[str]
        profiles=None,             # type: Optional[Dict[str, List[Tuple[str, str, str]]]]
        default_profile="abc123.default-release",  # type: str
    ):
        profiles_dir = tmp_path / "Firefox"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        # Default: one profile with X cookies
        if profiles is None:
            profiles = {
                default_profile: [
                    (".x.com", "auth_token", "tok_abc123"),
                    (".x.com", "ct0", "ct0_xyz789"),
                    (".example.com", "session", "sess_other"),
                ],
            }

        # Create profile directories with cookies databases
        for profile_name, cookies in profiles.items():
            profile_dir = profiles_dir / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)
            db_path = profile_dir / "cookies.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE moz_cookies ("
                "  id INTEGER PRIMARY KEY,"
                "  name TEXT NOT NULL,"
                "  value TEXT NOT NULL,"
                "  host TEXT NOT NULL,"
                "  path TEXT DEFAULT '/',"
                "  expiry INTEGER DEFAULT 0,"
                "  isSecure INTEGER DEFAULT 1,"
                "  isHttpOnly INTEGER DEFAULT 1,"
                "  sameSite INTEGER DEFAULT 0,"
                "  schemeMap INTEGER DEFAULT 0"
                ")"
            )
            for host, name, value in cookies:
                conn.execute(
                    "INSERT INTO moz_cookies (name, value, host) VALUES (?, ?, ?)",
                    (name, value, host),
                )
            conn.commit()
            conn.close()

        # Write profiles.ini
        if profiles_ini is None:
            profiles_ini = textwrap.dedent(f"""\
                [General]
                StartWithLastProfile=1

                [Profile0]
                Name=default-release
                IsRelative=1
                Path={default_profile}
                Default=1
            """)

        (profiles_dir / "profiles.ini").write_text(profiles_ini)

        return profiles_dir

    return _make


class TestExtractFirefoxCookies:
    """Tests for extract_firefox_cookies."""

    def test_valid_cookies_extracted(self, mock_firefox_env):
        """Cookies for the target domain are returned correctly."""
        profiles_dir = mock_firefox_env()

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == "tok_abc123"
        assert result["ct0"] == "ct0_xyz789"
        assert "session" not in result  # different domain cookie not included

    def test_multiple_profiles_selects_default(self, mock_firefox_env):
        """When multiple profiles exist, the one with Default=1 is used."""
        profiles_dir = mock_firefox_env(
            profiles={
                "aaa111.other": [
                    (".x.com", "auth_token", "wrong_token"),
                ],
                "bbb222.default-release": [
                    (".x.com", "auth_token", "correct_token"),
                    (".x.com", "ct0", "correct_ct0"),
                ],
            },
            profiles_ini=textwrap.dedent("""\
                [General]
                StartWithLastProfile=1

                [Profile0]
                Name=other
                IsRelative=1
                Path=aaa111.other

                [Profile1]
                Name=default-release
                IsRelative=1
                Path=bbb222.default-release
                Default=1
            """),
        )

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == "correct_token"
        assert result["ct0"] == "correct_ct0"

    def test_firefox_not_installed(self):
        """Returns None when Firefox profiles directory doesn't exist."""
        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=None,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token"])

        assert result is None

    def test_cookies_sqlite_empty(self, mock_firefox_env):
        """Returns None when cookies.sqlite has no rows."""
        profiles_dir = mock_firefox_env(
            profiles={"abc123.default-release": []},  # no cookies
        )

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is None

    def test_domain_has_no_cookies(self, mock_firefox_env):
        """Returns None when cookies exist but not for the target domain."""
        profiles_dir = mock_firefox_env(
            profiles={
                "abc123.default-release": [
                    (".example.com", "session", "sess_123"),
                ],
            },
        )

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is None

    def test_malformed_profiles_ini_falls_back(self, mock_firefox_env):
        """Falls back to first profile on disk when profiles.ini is garbage."""
        profiles_dir = mock_firefox_env(
            profiles={
                "zzz999.fallback": [
                    (".x.com", "auth_token", "fallback_token"),
                ],
            },
            profiles_ini="this is not valid ini content\n[[[broken",
        )

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token"])

        assert result is not None
        assert result["auth_token"] == "fallback_token"


class TestExtractCookiesAuto:
    """Tests for extract_cookies with browser='auto'."""

    def test_auto_macos_tries_chrome_then_firefox(self, mock_firefox_env):
        """On macOS, auto tries Chrome first, falls back to Firefox if Chrome fails."""
        profiles_dir = mock_firefox_env()

        with (
            patch("scripts.lib.cookie_extract.platform.system", return_value="Darwin"),
            patch(
                "scripts.lib.cookie_extract.extract_chrome_cookies",
                return_value=None,
            ),
            patch(
                "scripts.lib.cookie_extract.extract_safari_cookies",
                return_value=None,
            ),
            patch(
                "scripts.lib.cookie_extract._get_firefox_profiles_dir",
                return_value=profiles_dir,
            ),
        ):
            result = extract_cookies("auto", ".x.com", ["auth_token", "ct0"])

        # Chrome and Safari return None, Firefox succeeds
        assert result is not None
        assert result["auth_token"] == "tok_abc123"
        assert result["ct0"] == "ct0_xyz789"

    def test_auto_linux_tries_firefox_only(self, mock_firefox_env):
        """On Linux, auto only tries Firefox."""
        profiles_dir = mock_firefox_env()

        with (
            patch("scripts.lib.cookie_extract.platform.system", return_value="Linux"),
            patch(
                "scripts.lib.cookie_extract._get_firefox_profiles_dir",
                return_value=profiles_dir,
            ),
        ):
            result = extract_cookies("auto", ".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == "tok_abc123"

    def test_explicit_firefox(self, mock_firefox_env):
        """Explicit browser='firefox' goes directly to Firefox."""
        profiles_dir = mock_firefox_env()

        with patch(
            "scripts.lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_cookies("firefox", ".x.com", ["auth_token"])

        assert result is not None
        assert result["auth_token"] == "tok_abc123"

    def test_unknown_browser_returns_none(self):
        """Unknown browser name returns None."""
        result = extract_cookies("netscape", ".x.com", ["auth_token"])
        assert result is None

    def test_chrome_delegates_to_chrome_module(self):
        """Chrome extraction delegates to chrome_cookies module."""
        with patch(
            "scripts.lib.cookie_extract.extract_chrome_cookies",
            return_value={"auth_token": "chrome_tok"},
        ):
            result = extract_cookies("chrome", ".x.com", ["auth_token"])
        assert result == {"auth_token": "chrome_tok"}

    def test_safari_delegates_to_safari_module(self):
        """Safari extraction delegates to safari_cookies module."""
        with patch(
            "scripts.lib.cookie_extract.extract_safari_cookies",
            return_value={"auth_token": "safari_tok"},
        ):
            result = extract_cookies("safari", ".x.com", ["auth_token"])
        assert result == {"auth_token": "safari_tok"}
