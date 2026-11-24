"""Browser cookie extraction for last30days.

Extracts cookies from local browser databases (Firefox, Chrome, Brave, Safari)
to enable zero-config authentication for services like X/Twitter.
Note: Chrome/Brave extraction is macOS-only; Windows Chrome/Edge use
DPAPI-encrypted stores that are not yet supported.

Only uses Python stdlib — no external dependencies.
"""

import configparser
import functools
import logging
import os
import platform
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cookie_paths import path_is_dir, path_is_file
from .sqlite_snapshot import copy_snapshot

logger = logging.getLogger(__name__)
_SQLITE_TEMP_SIDECARS = ("", "-wal", "-shm", "-journal")


class CookieDatabaseReadError(RuntimeError):
    """A browser database could not be read to check for a login."""


def has_complete_pair(result: Optional[Dict[str, str]], cookie_names) -> bool:
    """Return True when ``result`` holds every requested cookie with a value.

    Shared by the Firefox profile scan, ``env.extract_browser_credentials``,
    and the setup wizard so a partial pair (e.g. a lone ``ct0`` from a
    logged-out session) never shadows a complete one. Empty values do not
    count: ``{"auth_token": "", "ct0": ""}`` is incomplete. ``cookie_names``
    is the spec's cookie list (a spec dict with a ``"cookies"`` key also
    works).
    """
    if not result:
        return False
    if isinstance(cookie_names, dict):
        cookie_names = cookie_names.get("cookies", [])
    return all(result.get(name) for name in cookie_names)


def _lock_temp_cookie_copy(path: str) -> None:
    """Restrict copied cookie DB temp files to the current user on POSIX."""
    if os.name == "nt":
        return
    Path(path).chmod(0o600)


@functools.lru_cache(maxsize=1)
def _is_wsl() -> bool:
    """Detect if running under Windows Subsystem for Linux.

    Cached after the first call since /proc/version doesn't change at runtime.
    """
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def _get_wsl_firefox_profiles_dir() -> Optional[Path]:
    """Find Firefox profiles directory on the Windows host from WSL.

    Scans /mnt/c/Users/*/AppData/Roaming/Mozilla/Firefox for real user
    directories (skips Public, Default, etc.).
    """
    mnt_users = Path("/mnt/c/Users")
    if not path_is_dir(mnt_users):
        return None
    skip = {"Public", "Default", "Default User", "All Users"}
    denied = None
    try:
        for user_dir in sorted(mnt_users.iterdir()):
            if user_dir.name in skip:
                continue
            try:
                if not path_is_dir(user_dir):
                    continue
                ff_dir = user_dir / "AppData" / "Roaming" / "Mozilla" / "Firefox"
                if path_is_dir(ff_dir):
                    return ff_dir
            except PermissionError as exc:
                if denied is None:
                    denied = exc
    except PermissionError as exc:
        if denied is None:
            denied = exc
    except OSError:
        pass
    if denied is not None:
        raise denied
    return None


def _get_firefox_profiles_dir() -> Optional[Path]:
    """Return the Firefox profiles directory for the current platform, or None."""
    system = platform.system()
    if system == "Darwin":
        path = Path.home() / "Library" / "Application Support" / "Firefox"
    elif system == "Linux":
        # Default location for most distros
        path = Path.home() / ".mozilla" / "firefox"
        denied = None
        try:
            if path_is_dir(path):
                return path
        except PermissionError as exc:
            denied = exc
        # Some distros (e.g. Fedora) honour $XDG_CONFIG_HOME
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config and os.path.isabs(xdg_config):
            path = Path(xdg_config) / "mozilla" / "firefox"
        else:
            path = Path.home() / ".config" / "mozilla" / "firefox"
    else:
        # Windows: %APPDATA%\Mozilla\Firefox — best-effort
        appdata = Path.home() / "AppData" / "Roaming" / "Mozilla" / "Firefox"
        path = appdata
    if path_is_dir(path):
        return path
    if system == "Linux" and denied is not None:
        raise denied
    return None


def _load_profiles_ini(ini_path: Path) -> configparser.ConfigParser:
    """Parse Firefox profiles.ini, retrying UTF-16 LE used on Windows (#1067)."""
    config = configparser.ConfigParser()
    try:
        config.read(str(ini_path), encoding="utf-8")
    except UnicodeDecodeError:
        config.read(str(ini_path), encoding="utf-16")
    return config


def _find_default_profile(
    profiles_dir: Path, denials: Optional[list[PermissionError]] = None
) -> Optional[Path]:
    """Parse profiles.ini to find the default profile directory.

    Looks for a section with Default=1. Falls back to the first profile
    directory found on disk if profiles.ini is missing or malformed.
    """
    ini_path = profiles_dir / "profiles.ini"
    denied: list[PermissionError] = []

    try:
        has_ini = path_is_file(ini_path)
    except PermissionError as exc:
        denied.append(exc)
        has_ini = False
    if has_ini:
        try:
            config = _load_profiles_ini(ini_path)

            # First pass: Install* section (Firefox >= 67 format, takes priority)
            for section in config.sections():
                if section.startswith("Install") and config.has_option(section, "Default"):
                    raw = config.get(section, "Default")
                    candidate = profiles_dir / raw
                    try:
                        found = path_is_dir(candidate)
                    except PermissionError as exc:
                        denied.append(exc)
                        continue
                    if found:
                        if denials is not None:
                            denials.extend(denied)
                        return candidate

            # Second pass: Profile section with Default=1
            for section in config.sections():
                if section.startswith("Profile") and config.has_option(section, "Default") and config.get(section, "Default") == "1":
                    try:
                        resolved = _resolve_profile_path(profiles_dir, config, section)
                    except PermissionError as exc:
                        denied.append(exc)
                        continue
                    if resolved is not None:
                        if denials is not None:
                            denials.extend(denied)
                        return resolved

            # Third pass: first Profile section that exists on disk
            for section in config.sections():
                if section.startswith("Profile"):
                    try:
                        resolved = _resolve_profile_path(profiles_dir, config, section)
                    except PermissionError as exc:
                        denied.append(exc)
                        continue
                    if resolved:
                        if denials is not None:
                            denials.extend(denied)
                        return resolved
        except PermissionError as exc:
            denied.append(exc)
        except (configparser.Error, OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to parse profiles.ini: %s", exc)

    # Fallback: scan directory for anything that looks like a profile
    profile = _fallback_find_profile(profiles_dir, denied)
    if denials is not None:
        denials.extend(denied)
    elif denied and profile is None:
        raise denied[0]
    return profile


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
    return candidate if path_is_dir(candidate) else None


def _fallback_find_profile(
    profiles_dir: Path, denials: Optional[list[PermissionError]] = None
) -> Optional[Path]:
    """Find the first directory that contains cookies.sqlite."""
    denied: list[PermissionError] = []
    try:
        for child in sorted(profiles_dir.iterdir()):
            try:
                if path_is_dir(child) and path_is_file(child / "cookies.sqlite"):
                    if denials is not None:
                        denials.extend(denied)
                    return child
            except PermissionError as exc:
                denied.append(exc)
    except PermissionError as exc:
        denied.append(exc)
    except OSError:
        pass
    if denials is not None:
        denials.extend(denied)
    elif denied:
        raise denied[0]
    return None


def _unlink_sqlite_temp_files(path: Optional[str]) -> None:
    """Remove a temporary SQLite copy and any WAL/SHM/journal sidecars."""
    if not path:
        return
    for suffix in _SQLITE_TEMP_SIDECARS:
        try:
            Path(path + suffix).unlink(missing_ok=True)
        except OSError:
            pass


def _select_scoped_firefox_cookies(
    rows: List[tuple[Any, ...]],
    wanted: str,
    preferred_host: str,
    cookie_names: List[str],
    now_unix: int,
) -> Optional[Dict[str, str]]:
    """Select cookie values from a single non-expired Firefox scope without mixing."""
    allowed_hosts = (wanted, f".{wanted}")
    scopes: Dict[tuple[str, str, str], Dict[str, List[tuple[int, int, int, str]]]] = {}
    for name, value, host, path, expiry, is_secure, is_httponly, origin_attrs in rows:
        if not isinstance(value, str) or not value:
            continue
        try:
            expiry_val = int(expiry) if expiry is not None else 0
        except (TypeError, ValueError):
            continue
        if expiry_val != 0 and expiry_val <= now_unix:
            continue
        host_norm = str(host or "").strip().lower()
        if host_norm not in allowed_hosts:
            continue
        path_norm = path if isinstance(path, str) and path else "/"
        partition_norm = str(origin_attrs or "").strip()
        secure_flag = 1 if is_secure else 0
        httponly_flag = 1 if is_httponly else 0
        scope_key = (host_norm, path_norm, partition_norm)
        scopes.setdefault(scope_key, {}).setdefault(str(name), []).append(
            (secure_flag, httponly_flag, expiry_val, value)
        )

    if not scopes:
        return None

    requested = list(dict.fromkeys(cookie_names))
    resolved_scopes: List[tuple[tuple[Any, ...], Dict[str, str]]] = []
    for scope_key, by_name in scopes.items():
        host_norm, path_norm, partition_norm = scope_key
        jar: Dict[str, str] = {}
        all_secure = True
        all_httponly = True
        for name, candidates in by_name.items():
            candidates.sort(
                key=lambda item: (
                    item[0] == 0,
                    item[1] == 0,
                    0 if item[2] == 0 else -item[2],
                )
            )
            best = candidates[0]
            jar[name] = best[3]
            if best[0] == 0:
                all_secure = False
            if best[1] == 0:
                all_httponly = False
        if not jar:
            continue
        complete = all(name in jar for name in requested)
        rank = (
            not complete,
            partition_norm != "",
            path_norm != "/",
            not all_secure,
            not all_httponly,
            host_norm != preferred_host,
            path_norm,
            host_norm,
            partition_norm,
        )
        resolved_scopes.append((rank, jar))

    if not resolved_scopes:
        return None
    resolved_scopes.sort(key=lambda item: item[0])
    return resolved_scopes[0][1]


def _query_cookies_db(
    db_path: Path, domain: str, cookie_names: List[str], *, windows_wsl: bool = False,
) -> Optional[Dict[str, str]]:
    """Snapshot the cookies database to a private temp dir and query it.

    The snapshot includes committed WAL updates while Firefox is running.
    Returns {name: value} dict from a single non-expired scope, or None if no
    matching cookies are found.
    """
    if not path_is_file(db_path):
        return None
    wanted = domain.strip().lstrip(".").lower()
    if not wanted or not cookie_names:
        return None
    preferred_host = domain.strip().lower()
    if preferred_host not in (wanted, f".{wanted}"):
        preferred_host = f".{wanted}"

    try:
        with tempfile.TemporaryDirectory(prefix="last30days-cookies-") as tmp_dir:
            if os.name != "nt":
                os.chmod(tmp_dir, 0o700)
            tmp_path = str(Path(tmp_dir) / "cookies.sqlite")
            # Pre-create with 0600 on POSIX and close the fd immediately so
            # Windows never holds an open descriptor when SQLite writes or
            # cleanup unlinks the temp database and its WAL/SHM sidecars.
            tmp_fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(tmp_fd)
            conn: Optional[sqlite3.Connection] = None
            try:
                if windows_wsl:
                    copy_snapshot(db_path, tmp_path, wsl_wal_fallback=True)
                else:
                    copy_snapshot(db_path, tmp_path)
                _lock_temp_cookie_copy(tmp_path)

                conn = sqlite3.connect(tmp_path)
                col_rows = conn.execute("PRAGMA table_info(moz_cookies)").fetchall()
                col_map = {str(row[1]).lower(): str(row[1]) for row in col_rows}
                if not {"name", "value", "host"}.issubset(col_map):
                    return None

                path_expr = col_map["path"] if "path" in col_map else "'/'"
                expiry_expr = col_map["expiry"] if "expiry" in col_map else "0"
                secure_expr = col_map["issecure"] if "issecure" in col_map else "1"
                httponly_expr = col_map["ishttponly"] if "ishttponly" in col_map else "1"
                partition_expr = (
                    col_map["originattributes"] if "originattributes" in col_map else "''"
                )

                placeholders = ",".join("?" for _ in cookie_names)
                query = (
                    f"SELECT name, value, host, {path_expr}, {expiry_expr}, "
                    f"{secure_expr}, {httponly_expr}, {partition_expr} "
                    f"FROM moz_cookies "
                    f"WHERE LOWER(host) IN (?, ?) AND name IN ({placeholders})"
                )
                params = [wanted, f".{wanted}"] + list(cookie_names)
                rows = conn.execute(query, params).fetchall()
            finally:
                if conn is not None:
                    conn.close()
                _unlink_sqlite_temp_files(tmp_path)

        if not rows:
            return None
        return _select_scoped_firefox_cookies(
            rows, wanted, preferred_host, cookie_names, int(time.time())
        )

    except (sqlite3.Error, OSError) as exc:
        if isinstance(exc, PermissionError):
            if exc.filename == str(db_path):
                raise
            if windows_wsl:
                source = db_path.resolve()
                if exc.filename in (str(source), f"{source}-wal"):
                    raise
        logger.debug("Failed to query cookies database %s: %s", db_path, exc)
        if windows_wsl:
            raise CookieDatabaseReadError("Could not read Windows Firefox cookies") from exc
        return None


def _try_firefox_dir(
    profiles_dir: Path, domain: str, cookie_names: List[str], *, windows_wsl: bool = False,
) -> Optional[Dict[str, str]]:
    """Try to extract cookies from a Firefox profiles directory.

    Tries the default profile first, then falls back to scanning all
    profiles for matching cookies.  This handles multi-profile setups
    where the user is logged into x.com on a non-default profile.
    A complete match (all ``cookie_names``) always wins: a partial
    default-profile result is kept only as a fallback.
    """
    denials: list[PermissionError] = []
    read_errors: list[CookieDatabaseReadError] = []

    def query(db: Path) -> Optional[Dict[str, str]]:
        try:
            return _query_cookies_db(db, domain, cookie_names, windows_wsl=windows_wsl)
        except PermissionError as exc:
            denials.append(exc)
        except CookieDatabaseReadError as exc:
            read_errors.append(exc)
        return None

    default_profile = _find_default_profile(profiles_dir, denials)
    profiles_tried = 0
    fallback: Optional[Dict[str, str]] = None
    if default_profile is not None:
        result = query(default_profile / "cookies.sqlite")
        if result is not None:
            if has_complete_pair(result, cookie_names):
                return result
            fallback = result
        profiles_tried = 1
    # Fallback: scan every profile directory for matching cookies
    try:
        for child in sorted(profiles_dir.iterdir()):
            try:
                if not path_is_dir(child):
                    continue
                if default_profile is not None and child == default_profile:
                    continue
                db = child / "cookies.sqlite"
                if path_is_file(db):
                    result = query(db)
                    if result is not None:
                        if has_complete_pair(result, cookie_names):
                            return result
                        if fallback is None:
                            fallback = result
                    profiles_tried += 1
            except PermissionError as exc:
                denials.append(exc)
    except PermissionError as exc:
        denials.append(exc)
    except OSError:
        pass
    if denials:
        raise denials[0]
    if fallback is not None:
        return fallback
    if read_errors:
        raise read_errors[0]
    logger.debug("No matching cookies found in %d Firefox profile(s)", profiles_tried)
    return None


def extract_firefox_cookies(
    domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from Firefox for the given domain and cookie names.

    Finds the default Firefox profile, copies cookies.sqlite to a temp file
    (to avoid lock conflicts), and queries for the requested cookies.

    On WSL2, falls back to Windows Firefox if native Linux Firefox has no
    matching cookies. If that database cannot be read, raises
    CookieDatabaseReadError so callers can distinguish failure from no match.

    Args:
        domain: The cookie domain to match (e.g. ".x.com"). Matched with LIKE %domain.
        cookie_names: List of cookie names to extract (e.g. ["auth_token", "ct0"]).

    Returns:
        Dict of {cookie_name: cookie_value} or None if no match.
    """
    result = _extract_firefox_with_source(domain, cookie_names)
    return result[0] if result is not None else None


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
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("Chrome cookie extraction failed: %s", exc)
        return None


def extract_brave_cookies(
    domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from Brave for the given domain and cookie names.

    macOS only — Brave uses the same v10 AES-128-CBC encryption as Chrome,
    with a different DB path and Keychain service name ("Brave Safe Storage").
    Tries the Default profile first, then scans numbered Profile directories.

    Returns:
        Dict of {cookie_name: cookie_value} or None if extraction fails.
    """
    if platform.system() != "Darwin":
        logger.debug("Brave cookie extraction only supported on macOS")
        return None
    try:
        from .chrome_cookies import extract_brave_cookies_macos
        return extract_brave_cookies_macos(domain, cookie_names)
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("Brave cookie extraction failed: %s", exc)
        return None


def _extract_chromium_family_cookies(
    browser: str, domain: str, cookie_names: List[str]
) -> Optional[Dict[str, str]]:
    """Extract cookies from a non-Chrome/Brave Chromium browser on macOS.

    macOS only — Edge, Vivaldi, Opera, Arc, Dia, and Chromium all reuse Chrome's
    v10 AES-128-CBC encryption, with their own profile path and Keychain
    service name (see chrome_cookies.CHROMIUM_BROWSER_PROFILES).
    """
    if platform.system() != "Darwin":
        logger.debug("%s cookie extraction only supported on macOS", browser)
        return None
    try:
        from .chrome_cookies import extract_chromium_browser_cookies_macos
        return extract_chromium_browser_cookies_macos(browser, domain, cookie_names)
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("%s cookie extraction failed: %s", browser, exc)
        return None


def extract_edge_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from Microsoft Edge for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("edge", domain, cookie_names)


def extract_vivaldi_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from Vivaldi for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("vivaldi", domain, cookie_names)


def extract_opera_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from Opera for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("opera", domain, cookie_names)


def extract_arc_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from Arc for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("arc", domain, cookie_names)


def extract_dia_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from Dia for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("dia", domain, cookie_names)


def extract_chromium_cookies(domain: str, cookie_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract cookies from open-source Chromium for the given domain (macOS only)."""
    return _extract_chromium_family_cookies("chromium", domain, cookie_names)


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
    except PermissionError:
        raise
    except Exception as exc:
        logger.debug("Safari cookie extraction failed: %s", exc)
        return None


def extract_cookies(
    browser: str, domain: str, cookie_names: list[str]
) -> Optional[dict[str, str]]:
    """Extract cookies from the specified browser.

    See extract_cookies_with_source() for selection and permission-error behavior.

    Args:
        browser: One of 'firefox', 'chrome', 'brave', 'edge', 'vivaldi',
            'opera', 'arc', 'dia', 'chromium', 'safari', or 'auto'.
            'auto' tries browsers in platform-appropriate order:
            - macOS: Chrome -> Brave -> Edge -> Vivaldi -> Opera -> Arc -> Dia -> Chromium -> Firefox -> Safari
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


def _extract_firefox_with_source(
    domain: str, cookie_names: List[str]
) -> Optional[tuple[Dict[str, str], str]]:
    """Extract Firefox cookies and report whether they came from native or WSL.

    Returns (cookies, "firefox") for native Linux/macOS Firefox, or
    (cookies, "firefox-wsl") for Windows Firefox accessed via WSL2.
    """
    denied = None
    try:
        profiles_dir = _get_firefox_profiles_dir()
    except PermissionError as exc:
        denied = exc
        profiles_dir = None
    if profiles_dir is not None:
        try:
            result = _try_firefox_dir(profiles_dir, domain, cookie_names)
        except PermissionError as exc:
            denied = exc
            result = None
        if result is not None:
            return (result, "firefox")

    if platform.system() == "Linux" and _is_wsl():
        try:
            wsl_dir = _get_wsl_firefox_profiles_dir()
        except PermissionError as exc:
            if denied is None:
                denied = exc
            wsl_dir = None
        if wsl_dir is not None:
            logger.debug("Trying Windows Firefox via WSL: %s", wsl_dir)
            try:
                result = _try_firefox_dir(wsl_dir, domain, cookie_names, windows_wsl=True)
            except PermissionError as exc:
                if denied is None:
                    denied = exc
                result = None
            except CookieDatabaseReadError:
                if denied is not None:
                    raise denied from None
                raise
            if result is not None:
                return (result, "firefox-wsl")

    if denied is not None:
        raise denied
    if profiles_dir is None:
        logger.debug("Firefox profiles directory not found")
    return None


def extract_cookies_with_source(
    browser: str, domain: str, cookie_names: list[str]
) -> Optional[tuple[dict[str, str], str]]:
    """Extract cookies and report which browser they came from.

    Same as extract_cookies() but returns a (cookies, browser_name) tuple
    so callers can track the source.

    Auto mode tries browsers in platform order until one supplies every requested
    cookie with a nonempty value. If none does, it returns the first partial
    result without combining cookies from different browsers. A browser's
    access or database read error does not stop later candidates from being tried.

    Args:
        browser: One of 'firefox', 'chrome', 'brave', 'edge', 'vivaldi',
            'opera', 'arc', 'dia', 'chromium', 'safari', or 'auto'.
        domain: Bare or dot-prefixed cookie domain (e.g. "x.com" or ".x.com").
        cookie_names: List of cookie names to extract.

    Returns:
        Tuple of ({cookie_name: cookie_value}, browser_name) or None.
        browser_name is "firefox-wsl" when cookies came from Windows Firefox via WSL2.

    Raises:
        PermissionError: The explicitly selected browser denies access, or auto
            mode encounters a denial and no browser returns even a partial result.
        CookieDatabaseReadError: Windows Firefox cannot be read and no other
            candidate supplies cookies or reports a permission denial.
    """
    extractors = {
        "firefox": extract_firefox_cookies,
        "chrome": extract_chrome_cookies,
        "brave": extract_brave_cookies,
        "edge": extract_edge_cookies,
        "vivaldi": extract_vivaldi_cookies,
        "opera": extract_opera_cookies,
        "arc": extract_arc_cookies,
        "dia": extract_dia_cookies,
        "chromium": extract_chromium_cookies,
        "safari": extract_safari_cookies,
    }

    if browser != "auto":
        if browser == "firefox":
            return _extract_firefox_with_source(domain, cookie_names)
        extractor = extractors.get(browser)
        if extractor is None:
            logger.warning("Unknown browser: %s", browser)
            return None
        result = extractor(domain, cookie_names)
        return (result, browser) if result is not None else None

    # Auto mode: try browsers in platform-appropriate order.
    # Note: the skill's own entry point (env.extract_browser_credentials) builds
    # its own list that tries the SILENT browsers (Firefox, Safari) first to
    # avoid macOS Keychain prompts. This standalone "auto" is Chromium-first; the
    # two orderings are intentional for their respective callers.
    system = platform.system()
    if system == "Darwin":
        order = ["chrome", "brave", "edge", "vivaldi", "opera", "arc", "dia", "chromium", "firefox", "safari"]
    elif system == "Linux":
        order = ["firefox"]
    else:
        order = ["firefox"]

    fallback = None
    first_denial = None
    first_read_error = None
    for name in order:
        try:
            if name == "firefox":
                result = _extract_firefox_with_source(domain, cookie_names)
            else:
                cookies = extractors[name](domain, cookie_names)
                result = (cookies, name) if cookies is not None else None
        except PermissionError as exc:
            if first_denial is None:
                first_denial = exc
            continue
        except CookieDatabaseReadError as exc:
            if first_read_error is None:
                first_read_error = exc
            continue

        if result is not None:
            if has_complete_pair(result[0], cookie_names):
                return result
            if fallback is None:
                fallback = result

    if fallback is not None:
        return fallback
    if first_denial is not None:
        raise first_denial
    if first_read_error is not None:
        raise first_read_error

    return None
