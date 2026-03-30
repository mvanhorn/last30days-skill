"""Browser cookie extraction for last30days.

Extracts cookies from local browser databases (Firefox, Chrome, Safari)
to enable zero-config authentication for services like X/Twitter.

Only uses Python stdlib — no external dependencies.
"""

import configparser
import logging
import platform
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_firefox_profiles_dir() -> Optional[Path]:
    """Return the Firefox profiles directory for the current platform, or None."""
    system = platform.system()
    if system == "Darwin":
        path = Path.home() / "Library" / "Application Support" / "Firefox"
    elif system == "Linux":
        path = Path.home() / ".mozilla" / "firefox"
    else:
        # Windows: %APPDATA%\Mozilla\Firefox — best-effort
        appdata = Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"
        path = appdata
    return path if path.is_dir() else None


def _find_default_profile(profiles_dir: Path) -> Optional[Path]:
    """Parse profiles.ini to find the default profile directory.

    Looks for a section with Default=1. Falls back to the first profile
    directory found on disk if profiles.ini is missing or malformed.
    """
    ini_path = profiles_dir / "profiles.ini"

    if ini_path.is_file():
        try:
            config = configparser.ConfigParser()
            config.read(str(ini_path), encoding="utf-8")

            # First pass: look for Default=1
            for section in config.sections():
                if config.has_option(section, "Default") and config.get(section, "Default") == "1":
                    return _resolve_profile_path(profiles_dir, config, section)

            # Second pass: first Install* section with Default key (Firefox >= 67 format)
            for section in config.sections():
                if section.startswith("Install") and config.has_option(section, "Default"):
                    raw = config.get(section, "Default")
                    candidate = profiles_dir / raw
                    if candidate.is_dir():
                        return candidate

            # Third pass: first Profile section that exists on disk
            for section in config.sections():
                if section.startswith("Profile"):
                    resolved = _resolve_profile_path(profiles_dir, config, section)
                    if resolved and resolved.is_dir():
                        return resolved
        except (configparser.Error, OSError) as exc:
            logger.debug("Failed to parse profiles.ini: %s", exc)

    # Fallback: scan directory for anything that looks like a profile
    return _fallback_find_profile(profiles_dir)


def _resolve_profile_path(
    profiles_dir: Path, config: configparser.ConfigParser, section: str
) -> Optional[Path]:
    """Resolve a profile path from a ConfigParser section."""
    if not config.has_option(section, "Path"):
        return None
    raw_path = config.get(section, "Path")
    is_relative = config.has_option(section, "IsRelative") and config.get(section, "IsRelative") == "1"
    if is_relative:
        candidate = profiles_dir / raw_path
    else:
        candidate = Path(raw_path)
    return candidate if candidate.is_dir() else None


def _fallback_find_profile(profiles_dir: Path) -> Optional[Path]:
    """Find the first directory that contains cookies.sqlite."""
    try:
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and (child / "cookies.sqlite").is_file():
                return child
    except OSError:
        pass
    return None


def _query_cookies_db(
    db_path: Path, domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Copy the cookies database to a temp file and query it.

    Firefox locks cookies.sqlite while running, so we copy first.
    Returns {name: value} dict or None if no matching cookies found.
    """
    if not db_path.is_file():
        return None

    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
        shutil.copy2(str(db_path), tmp_path)

        conn = sqlite3.connect(tmp_path)
        try:
            # Build parameterized query — SQLite doesn't support array params,
            # so we build the IN clause with individual placeholders.
            placeholders = ",".join("?" for _ in cookie_names)
            query = (
                f"SELECT name, value FROM moz_cookies "
                f"WHERE host LIKE ? AND name IN ({placeholders})"
            )
            # domain pattern: match .x.com, x.com, etc.
            domain_pattern = f"%{domain}"
            params = [domain_pattern] + list(cookie_names)

            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            return None
        return {name: value for name, value in rows}

    except (sqlite3.Error, OSError) as exc:
        logger.debug("Failed to query cookies database %s: %s", db_path, exc)
        return None
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass
        if tmp_fd is not None:
            try:
                import os
                os.close(tmp_fd)
            except OSError:
                pass


def extract_firefox_cookies(
    domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from Firefox for the given domain and cookie names.

    Finds the default Firefox profile, copies cookies.sqlite to a temp file
    (to avoid lock conflicts), and queries for the requested cookies.

    Args:
        domain: The cookie domain to match (e.g. ".x.com"). Matched with LIKE %domain.
        cookie_names: List of cookie names to extract (e.g. ["auth_token", "ct0"]).

    Returns:
        Dict of {cookie_name: cookie_value} or None if extraction fails.
    """
    profiles_dir = _get_firefox_profiles_dir()
    if profiles_dir is None:
        logger.debug("Firefox profiles directory not found")
        return None

    profile_path = _find_default_profile(profiles_dir)
    if profile_path is None:
        logger.debug("No Firefox profile found in %s", profiles_dir)
        return None

    db_path = profile_path / "cookies.sqlite"
    return _query_cookies_db(db_path, domain, cookie_names)


def extract_chrome_cookies(
    domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from Chrome for the given domain and cookie names.

    macOS only — uses Keychain + system openssl for AES-128-CBC decryption.
    Linux/Windows not supported (Chrome uses platform-specific encryption).

    Returns:
        Dict of {cookie_name: cookie_value} or None if extraction fails.
    """
    if platform.system() != "Darwin":
        logger.debug("Chrome cookie extraction only supported on macOS")
        return None
    try:
        from .chrome_cookies import extract_chrome_cookies_macos
        return extract_chrome_cookies_macos(domain, cookie_names)
    except Exception as exc:
        logger.debug("Chrome cookie extraction failed: %s", exc)
        return None


def extract_safari_cookies(
    domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from Safari for the given domain and cookie names.

    macOS only — parses the unencrypted binary cookie file.

    Returns:
        Dict of {cookie_name: cookie_value} or None if extraction fails.
    """
    if platform.system() != "Darwin":
        logger.debug("Safari cookie extraction only supported on macOS")
        return None
    try:
        from .safari_cookies import extract_safari_cookies_macos
        return extract_safari_cookies_macos(domain, cookie_names)
    except Exception as exc:
        logger.debug("Safari cookie extraction failed: %s", exc)
        return None


def extract_cookies(
    browser: str, domain: str, cookie_names: list[str]
) -> Optional[dict[str, str]]:
    """Extract cookies from the specified browser.

    Args:
        browser: One of 'firefox', 'chrome', 'safari', or 'auto'.
            'auto' tries browsers in platform-appropriate order:
            - macOS: Chrome -> Firefox -> Safari
            - Linux: Firefox only
        domain: The cookie domain to match (e.g. ".x.com").
        cookie_names: List of cookie names to extract.

    Returns:
        Dict of {cookie_name: cookie_value} or None if extraction fails.
    """
    result = extract_cookies_with_source(browser, domain, cookie_names)
    if result is None:
        return None
    cookies, _browser_name = result
    return cookies


def extract_cookies_with_source(
    browser: str, domain: str, cookie_names: list[str]
) -> Optional[tuple[dict[str, str], str]]:
    """Extract cookies and report which browser they came from.

    Same as extract_cookies() but returns a (cookies, browser_name) tuple
    so callers can track the source.

    Args:
        browser: One of 'firefox', 'chrome', 'safari', or 'auto'.
        domain: The cookie domain to match (e.g. ".x.com").
        cookie_names: List of cookie names to extract.

    Returns:
        Tuple of ({cookie_name: cookie_value}, browser_name) or None.
    """
    extractors = {
        "firefox": extract_firefox_cookies,
        "chrome": extract_chrome_cookies,
        "safari": extract_safari_cookies,
    }

    if browser != "auto":
        extractor = extractors.get(browser)
        if extractor is None:
            logger.warning("Unknown browser: %s", browser)
            return None
        result = extractor(domain, cookie_names)
        return (result, browser) if result is not None else None

    # Auto mode: try browsers in platform-appropriate order
    system = platform.system()
    if system == "Darwin":
        order = ["chrome", "firefox", "safari"]
    elif system == "Linux":
        order = ["firefox"]
    else:
        order = ["firefox"]

    for name in order:
        result = extractors[name](domain, cookie_names)
        if result is not None:
            return (result, name)

    return None
