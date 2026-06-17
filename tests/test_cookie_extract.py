"""Tests for browser cookie extraction module."""

import configparser
import os
import sqlite3
import textwrap
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unittest.mock import patch

import pytest

from lib.cookie_extract import (
    extract_cookies,
    extract_firefox_cookies,
    _query_cookies_db,
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

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not reliable on Windows")
    def test_temp_cookie_db_copy_is_owner_only(self, tmp_path):
        """Copied cookie DB temp files are chmodded owner-only before read."""
        db_path = tmp_path / "cookies.sqlite"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE moz_cookies (name TEXT NOT NULL, value TEXT NOT NULL, host TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO moz_cookies (name, value, host) VALUES (?, ?, ?)",
            ("auth_token", "tok_abc123", ".x.com"),
        )
        conn.commit()
        conn.close()
        os.chmod(db_path, 0o644)

        real_connect = sqlite3.connect

        def assert_temp_copy_locked(path, *args, **kwargs):
            if Path(str(path)) != db_path:
                assert Path(str(path)).stat().st_mode & 0o777 == 0o600
            return real_connect(path, *args, **kwargs)

        with patch("lib.cookie_extract.sqlite3.connect", side_effect=assert_temp_copy_locked):
            result = _query_cookies_db(db_path, ".x.com", ["auth_token"])

        assert result == {"auth_token": "tok_abc123"}

    def test_valid_cookies_extracted(self, mock_firefox_env):
        """Cookies for the target domain are returned correctly."""
        profiles_dir = mock_firefox_env()

        with patch(
            "lib.cookie_extract._get_firefox_profiles_dir",
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
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == "correct_token"
        assert result["ct0"] == "correct_ct0"

    def test_firefox_not_installed(self):
        """Returns None when Firefox profiles directory doesn't exist."""
        with patch(
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=None,
        ), patch(
            "lib.cookie_extract._is_wsl",
            return_value=False,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token"])

        assert result is None

    def test_cookies_sqlite_empty(self, mock_firefox_env):
        """Returns None when cookies.sqlite has no rows."""
        profiles_dir = mock_firefox_env(
            profiles={"abc123.default-release": []},  # no cookies
        )

        with patch(
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ), patch(
            "lib.cookie_extract._is_wsl",
            return_value=False,
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
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ), patch(
            "lib.cookie_extract._is_wsl",
            return_value=False,
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
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token"])

        assert result is not None
        assert result["auth_token"] == "fallback_token"

    def test_non_default_profile_with_cookies(self, mock_firefox_env):
        """Falls back to non-default profile when default has no X cookies."""
        profiles_dir = mock_firefox_env(
            profiles={
                "aaa111.default": [
                    (".example.com", "session", "sess_other"),
                ],
                "bbb222.release": [
                    (".x.com", "auth_token", "tok_nondefault"),
                    (".x.com", "ct0", "ct0_nondefault"),
                ],
            },
            profiles_ini=textwrap.dedent("""\
                [General]
                StartWithLastProfile=1

                [Profile0]
                Name=default
                IsRelative=1
                Path=aaa111.default
                Default=1

                [Profile1]
                Name=release
                IsRelative=1
                Path=bbb222.release
            """),
        )

        with patch(
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == "tok_nondefault"
        assert result["ct0"] == "ct0_nondefault"

    def test_multiple_profiles_none_have_cookies(self, mock_firefox_env):
        """Returns None when no profile has matching cookies."""
        profiles_dir = mock_firefox_env(
            profiles={
                "aaa111.default": [
                    (".example.com", "session", "sess_a"),
                ],
                "bbb222.release": [
                    (".other.com", "other", "val_b"),
                ],
            },
            profiles_ini=textwrap.dedent("""\
                [General]
                StartWithLastProfile=1

                [Profile0]
                Name=default
                IsRelative=1
                Path=aaa111.default
                Default=1

                [Profile1]
                Name=release
                IsRelative=1
                Path=bbb222.release
            """),
        )

        with patch(
            "lib.cookie_extract._get_firefox_profiles_dir",
            return_value=profiles_dir,
        ), patch(
            "lib.cookie_extract._is_wsl",
            return_value=False,
        ):
            result = extract_firefox_cookies(".x.com", ["auth_token", "ct0"])

        assert result is None


class TestExtractCookiesAuto:
    """Tests for extract_cookies with browser='auto'."""

    def test_auto_macos_tries_chrome_then_firefox(self, mock_firefox_env):
        """On macOS, auto tries the Chromium family first, falls back to Firefox."""
        profiles_dir = mock_firefox_env()

        # Mock every Chromium-family extractor to None so the test is hermetic
        # regardless of which browsers are actually installed/logged-in on the
        # machine running it (auto tries these before Firefox).
        with (
            patch("lib.cookie_extract.platform.system", return_value="Darwin"),
            patch("lib.cookie_extract.extract_chrome_cookies", return_value=None),
            patch("lib.cookie_extract.extract_brave_cookies", return_value=None),
            patch("lib.cookie_extract.extract_edge_cookies", return_value=None),
            patch("lib.cookie_extract.extract_vivaldi_cookies", return_value=None),
            patch("lib.cookie_extract.extract_opera_cookies", return_value=None),
            patch("lib.cookie_extract.extract_arc_cookies", return_value=None),
            patch("lib.cookie_extract.extract_chromium_cookies", return_value=None),
            patch("lib.cookie_extract.extract_safari_cookies", return_value=None),
            patch(
                "lib.cookie_extract._get_firefox_profiles_dir",
                return_value=profiles_dir,
            ),
        ):
            result = extract_cookies("auto", ".x.com", ["auth_token", "ct0"])

        # All Chromium browsers and Safari return None, Firefox succeeds
        assert result is not None
        assert result["auth_token"] == "tok_abc123"
        assert result["ct0"] == "ct0_xyz789"

    def test_auto_linux_tries_firefox_only(self, mock_firefox_env):
        """On Linux, auto only tries Firefox."""
        profiles_dir = mock_firefox_env()

        with (
            patch("lib.cookie_extract.platform.system", return_value="Linux"),
            patch(
                "lib.cookie_extract._get_firefox_profiles_dir",
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
            "lib.cookie_extract._get_firefox_profiles_dir",
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
            "lib.cookie_extract.extract_chrome_cookies",
            return_value={"auth_token": "chrome_tok"},
        ):
            result = extract_cookies("chrome", ".x.com", ["auth_token"])
        assert result == {"auth_token": "chrome_tok"}

    def test_safari_delegates_to_safari_module(self):
        """Safari extraction delegates to safari_cookies module."""
        with patch(
            "lib.cookie_extract.extract_safari_cookies",
            return_value={"auth_token": "safari_tok"},
        ):
            result = extract_cookies("safari", ".x.com", ["auth_token"])
        assert result == {"auth_token": "safari_tok"}
