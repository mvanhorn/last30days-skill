"""Chromium-family cookie extraction for macOS.

Extracts cookies from Chromium-based browser SQLite databases using only
stdlib modules and in-process AES-128-CBC decryption (macOS CommonCrypto via
ctypes, with a pure-Python fallback). Zero pip dependencies and zero key
exposure on subprocess argv.

Chromium on macOS uses v10 encryption (AES-128-CBC with Keychain-stored key).
Every Chromium-based browser (Chrome, Brave, Edge, Vivaldi, Opera, Arc, Dia,
Chromium) shares the same algorithm; only the profile directory and Keychain
service name differ, so they all run through the same decryption core.
This is NOT affected by Windows App-Bound Encryption (v20).
"""

import ctypes
import hashlib
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .cookie_paths import path_exists, path_is_dir
from .sqlite_snapshot import copy_snapshot

logger = logging.getLogger(__name__)

_SECURITY_BIN = "/usr/bin/security"
_COMMONCRYPTO_LIB_PATH = "/usr/lib/system/libcommonCrypto.dylib"
_KCC_DECRYPT = 1
_KCC_ALG_AES128 = 0
_KCC_OPT_PKCS7_PADDING = 1
_KCC_SUCCESS = 0
_CHROME_EPOCH_OFFSET_SECONDS = 11644473600
_SQLITE_TEMP_SIDECARS = ("", "-wal", "-shm", "-journal")


def _lock_temp_cookie_copy(path: str) -> None:
    """Restrict copied cookie DB temp files to the current user on POSIX."""
    if os.name == "nt":
        return
    Path(path).chmod(0o600)


def _unlink_sqlite_temp_files(path: Optional[str]) -> None:
    """Remove a temporary SQLite copy and any WAL/SHM/journal sidecars."""
    if not path:
        return
    for suffix in _SQLITE_TEMP_SIDECARS:
        try:
            Path(path + suffix).unlink(missing_ok=True)
        except OSError:
            pass

# Cookie DB locations on macOS
_APP_SUPPORT = Path.home() / "Library" / "Application Support"
CHROME_BASE_DIR = _APP_SUPPORT / "Google" / "Chrome"
# Kept for backward compatibility; resolution now goes through the profile
# finder (which also handles the modern Network/Cookies layout).
CHROME_COOKIES_DB = CHROME_BASE_DIR / "Default" / "Cookies"
BRAVE_BASE_DIR = _APP_SUPPORT / "BraveSoftware" / "Brave-Browser"

# Other Chromium-based browsers, keyed by FROM_BROWSER name. Each maps to
# (profile base directory, macOS Keychain service name). Chrome and Brave keep
# their dedicated helpers below for backward compatibility; everything here is
# resolved generically by extract_chromium_browser_cookies_macos(). Keychain
# service names follow Chromium's "<Browser> Safe Storage" convention.
CHROMIUM_BROWSER_PROFILES: dict[str, tuple[Path, str]] = {
    "edge": (_APP_SUPPORT / "Microsoft Edge", "Microsoft Edge Safe Storage"),
    "vivaldi": (_APP_SUPPORT / "Vivaldi", "Vivaldi Safe Storage"),
    "opera": (_APP_SUPPORT / "com.operasoftware.Opera", "Opera Safe Storage"),
    "arc": (_APP_SUPPORT / "Arc" / "User Data", "Arc Safe Storage"),
    "dia": (_APP_SUPPORT / "Dia" / "User Data", "Dia Safe Storage"),
    "chromium": (_APP_SUPPORT / "Chromium", "Chromium Safe Storage"),
}

# Chromium v10 encryption constants (shared by Chrome and Brave)
CHROME_SALT = b"saltysalt"
CHROME_PBKDF2_ITERATIONS = 1003
CHROME_KEY_LENGTH = 16
# IV is 16 space characters (0x20)
CHROME_IV = b" " * 16
CHROME_IV_HEX = "20" * 16


def _gf_mul(a: int, b: int) -> int:
    res = 0
    while b:
        if b & 1:
            res ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return res


def _gf_inv(a: int) -> int:
    if a == 0:
        return 0
    res = 1
    base = a
    exp = 254
    while exp:
        if exp & 1:
            res = _gf_mul(res, base)
        base = _gf_mul(base, base)
        exp >>= 1
    return res


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_aes_tables() -> tuple[tuple[int, ...], tuple[int, ...]]:
    sbox = [0] * 256
    inv_sbox = [0] * 256
    for x in range(256):
        inv = _gf_inv(x)
        sub = (
            inv
            ^ _rotl8(inv, 1)
            ^ _rotl8(inv, 2)
            ^ _rotl8(inv, 3)
            ^ _rotl8(inv, 4)
            ^ 0x63
        )
        sbox[x] = sub
        inv_sbox[sub] = x
    return tuple(sbox), tuple(inv_sbox)


_AES_SBOX, _AES_INV_SBOX = _build_aes_tables()
_AES_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)
_AES_MUL9 = tuple(_gf_mul(x, 0x09) for x in range(256))
_AES_MUL11 = tuple(_gf_mul(x, 0x0B) for x in range(256))
_AES_MUL13 = tuple(_gf_mul(x, 0x0D) for x in range(256))
_AES_MUL14 = tuple(_gf_mul(x, 0x0E) for x in range(256))


def _aes128_expand_key(key: bytes) -> list[bytes]:
    words = [list(key[i : i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        temp = list(words[i - 1])
        if i % 4 == 0:
            temp = [_AES_SBOX[b] for b in (temp[1], temp[2], temp[3], temp[0])]
            temp[0] ^= _AES_RCON[i // 4 - 1]
        prev = words[i - 4]
        words.append([prev[j] ^ temp[j] for j in range(4)])
    return [
        bytes(words[4 * r] + words[4 * r + 1] + words[4 * r + 2] + words[4 * r + 3])
        for r in range(11)
    ]


def _aes128_decrypt_block(block: bytes, round_keys: list[bytes]) -> bytes:
    rk = round_keys[10]
    s = [block[i] ^ rk[i] for i in range(16)]
    for rnd in range(9, 0, -1):
        s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
        s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
        s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
        rk = round_keys[rnd]
        s = [_AES_INV_SBOX[s[i]] ^ rk[i] for i in range(16)]
        for c in (0, 4, 8, 12):
            a0, a1, a2, a3 = s[c], s[c + 1], s[c + 2], s[c + 3]
            s[c] = _AES_MUL14[a0] ^ _AES_MUL11[a1] ^ _AES_MUL13[a2] ^ _AES_MUL9[a3]
            s[c + 1] = _AES_MUL9[a0] ^ _AES_MUL14[a1] ^ _AES_MUL11[a2] ^ _AES_MUL13[a3]
            s[c + 2] = _AES_MUL13[a0] ^ _AES_MUL9[a1] ^ _AES_MUL14[a2] ^ _AES_MUL11[a3]
            s[c + 3] = _AES_MUL11[a0] ^ _AES_MUL13[a1] ^ _AES_MUL9[a2] ^ _AES_MUL14[a3]
    s[1], s[5], s[9], s[13] = s[13], s[1], s[5], s[9]
    s[2], s[6], s[10], s[14] = s[10], s[14], s[2], s[6]
    s[3], s[7], s[11], s[15] = s[7], s[11], s[15], s[3]
    rk0 = round_keys[0]
    return bytes(_AES_INV_SBOX[s[i]] ^ rk0[i] for i in range(16))


def _decrypt_aes128_cbc_pure(ciphertext: bytes, aes_key: bytes, iv: bytes) -> Optional[bytes]:
    """Decrypt AES-128-CBC with PKCS7 padding in pure Python (no subprocesses)."""
    round_keys = _aes128_expand_key(aes_key)
    out = bytearray()
    prev = iv
    for offset in range(0, len(ciphertext), 16):
        block = ciphertext[offset : offset + 16]
        plain = _aes128_decrypt_block(block, round_keys)
        out.extend(plain[i] ^ prev[i] for i in range(16))
        prev = block
    return _remove_pkcs7_padding(bytes(out))


_COMMONCRYPTO_FUNC: Any = None
_COMMONCRYPTO_LOADED = False


def _load_commoncrypto() -> Any:
    """Load macOS CommonCrypto's CCCrypt symbol in-process, or return None."""
    global _COMMONCRYPTO_FUNC, _COMMONCRYPTO_LOADED
    if _COMMONCRYPTO_LOADED:
        return _COMMONCRYPTO_FUNC
    _COMMONCRYPTO_LOADED = True
    if sys.platform != "darwin":
        return None
    try:
        lib = ctypes.CDLL(_COMMONCRYPTO_LIB_PATH)
        cc_crypt = lib.CCCrypt
        cc_crypt.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        cc_crypt.restype = ctypes.c_int32
        _COMMONCRYPTO_FUNC = cc_crypt
    except (OSError, AttributeError):
        _COMMONCRYPTO_FUNC = None
    return _COMMONCRYPTO_FUNC


def _decrypt_aes128_cbc_commoncrypto(
    ciphertext: bytes, aes_key: bytes, iv: bytes, cc_crypt: Any
) -> Optional[bytes]:
    """Decrypt AES-128-CBC with PKCS7 padding in-process via macOS CommonCrypto."""
    out_buf = ctypes.create_string_buffer(len(ciphertext) + 16)
    out_len = ctypes.c_size_t(0)
    status = cc_crypt(
        _KCC_DECRYPT,
        _KCC_ALG_AES128,
        _KCC_OPT_PKCS7_PADDING,
        aes_key,
        len(aes_key),
        iv,
        ciphertext,
        len(ciphertext),
        out_buf,
        len(out_buf),
        ctypes.byref(out_len),
    )
    if status != _KCC_SUCCESS:
        return None
    return out_buf.raw[: out_len.value]


def _decrypt_aes128_cbc(
    ciphertext: bytes, aes_key: bytes, iv: bytes = CHROME_IV
) -> Optional[bytes]:
    """Decrypt AES-128-CBC ciphertext in-process without exposing key material."""
    if (
        len(aes_key) != CHROME_KEY_LENGTH
        or len(iv) != 16
        or not ciphertext
        or len(ciphertext) % 16 != 0
    ):
        return None
    cc_crypt = _load_commoncrypto()
    if cc_crypt is not None:
        return _decrypt_aes128_cbc_commoncrypto(ciphertext, aes_key, iv, cc_crypt)
    return _decrypt_aes128_cbc_pure(ciphertext, aes_key, iv)


def _get_chromium_encryption_key(service_name: str) -> Optional[bytes]:
    """Retrieve the encryption passphrase for a Chromium-based browser from macOS Keychain.

    Calls `/usr/bin/security find-generic-password` which may trigger a system dialog
    on first access.

    Returns the raw passphrase bytes, or None on failure.
    """
    try:
        result = subprocess.run(
            [_SECURITY_BIN, "find-generic-password", "-w", "-s", service_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.info("%s Keychain access denied or browser not installed: %s", service_name, result.stderr.strip())
            return None
        passphrase = result.stdout.strip()
        if not passphrase:
            logger.info("%s Keychain returned empty passphrase", service_name)
            return None
        return passphrase.encode("utf-8")
    except FileNotFoundError:
        logger.info("'%s' command not found — not on macOS?", _SECURITY_BIN)
        return None
    except subprocess.TimeoutExpired:
        logger.info("%s Keychain access timed out", service_name)
        return None
    except Exception as e:
        logger.info("Failed to get %s encryption key: %s", service_name, e)
        return None


def _get_chrome_encryption_key() -> Optional[bytes]:
    return _get_chromium_encryption_key("Chrome Safe Storage")


def _derive_aes_key(passphrase: bytes) -> bytes:
    """Derive 16-byte AES key from Chrome's Keychain passphrase via PBKDF2."""
    return hashlib.pbkdf2_hmac(
        "sha1",
        passphrase,
        CHROME_SALT,
        CHROME_PBKDF2_ITERATIONS,
        dklen=CHROME_KEY_LENGTH,
    )


def _decrypt_v10_value(encrypted_value: bytes, aes_key: bytes, db_version: int) -> Optional[str]:
    """Decrypt a Chrome v10-encrypted cookie value in-process (zero subprocesses).

    Uses macOS CommonCrypto via ctypes on Darwin (with a pure-Python fallback)
    so ``aes_key`` never leaves process memory.
    For Chrome 130+ (db_version >= 24), strips 32-byte SHA-256 prefix after decryption.

    Returns decrypted string or None on failure.
    """
    if not isinstance(encrypted_value, (bytes, bytearray, memoryview)):
        return None
    raw_bytes = bytes(encrypted_value)
    if not raw_bytes.startswith(b"v10"):
        return None
    ciphertext = raw_bytes[3:]
    if not ciphertext:
        return None

    try:
        decrypted = _decrypt_aes128_cbc(ciphertext, aes_key, CHROME_IV)
        if decrypted is None:
            return None

        # Chrome 130+ (db version >= 24): strip 32-byte SHA-256 prefix
        if db_version >= 24 and len(decrypted) > 32:
            decrypted = decrypted[32:]

        return decrypted.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug("Chrome cookie decryption error: %s", e)
        return None


def _remove_pkcs7_padding(data: bytes) -> Optional[bytes]:
    """Remove PKCS7 padding from decrypted data.

    The last byte indicates the number of padding bytes added.
    All padding bytes must have the same value.

    Returns unpadded data or None if padding is invalid.
    """
    if not data:
        return None
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 16:
        return None
    # Verify all padding bytes match
    if data[-pad_len:] != bytes([pad_len]) * pad_len:
        return None
    return data[:-pad_len]


def _get_db_version(cursor: sqlite3.Cursor) -> int:
    """Get Chrome cookie database version from the meta table.

    Returns 0 if meta table doesn't exist or version can't be read.
    """
    try:
        cursor.execute("SELECT value FROM meta WHERE key = 'version'")
        row = cursor.fetchone()
        if row:
            return int(row[0])
    except Exception:
        pass
    return 0


def _extract_chromium_cookies_macos(
    db_path: Path,
    keychain_service: str,
    domain: str,
    cookie_names: list[str],
    key_cache: Optional[dict[str, Optional[bytes]]] = None,
) -> Optional[dict[str, str]]:
    """Extract cookies from any Chromium-based browser on macOS.

    Snapshots committed Cookies database state to a temp file, reads specified cookies
    from a single non-expired scope, and decrypts v10-encrypted values in-process using
    the Keychain-stored key.

    Args:
        db_path: Path to the browser's Cookies SQLite file.
        keychain_service: macOS Keychain service name (e.g. "Chrome Safe Storage").
        domain: Cookie domain to match (e.g., ".twitter.com", ".x.com").
        cookie_names: List of cookie names to extract.

    Returns:
        Dict mapping cookie name to decrypted value from one scope, or None on failure.
        Only includes cookies that were successfully found and decrypted.
    """
    try:
        db_path.stat()
    except PermissionError:
        raise
    except OSError:
        logger.info("%s cookies database not found at %s", keychain_service, db_path)
        return None

    wanted = domain.strip().lstrip(".").lower()
    if not wanted or not cookie_names:
        return None
    preferred_host = domain.strip().lower()
    if preferred_host not in (wanted, f".{wanted}"):
        preferred_host = f".{wanted}"

    # The backup includes committed WAL updates while the browser is running.
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(tmp_fd)
        # Snapshot content only; preserve mkstemp's 0600 permissions.
        copy_snapshot(db_path, tmp_path)
        _lock_temp_cookie_copy(tmp_path)
    except Exception as e:
        browser_read_denied = isinstance(e, PermissionError) and e.filename == str(db_path)
        if browser_read_denied:
            logger.info("Permission denied reading %s cookies database", keychain_service)
        else:
            logger.info("Failed to copy %s cookies database: %s", keychain_service, e)
        _unlink_sqlite_temp_files(tmp_path)
        if browser_read_denied:
            raise
        return None

    conn = None
    try:
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        db_version = _get_db_version(cursor)
        logger.debug("%s cookie DB version: %d", keychain_service, db_version)

        cursor.execute("PRAGMA table_info(cookies)")
        col_map = {str(row[1]).lower(): str(row[1]) for row in cursor.fetchall()}
        if not {"name", "value", "encrypted_value", "host_key"}.issubset(col_map):
            logger.info("%s cookies table missing required columns", keychain_service)
            return None

        path_expr = col_map["path"] if "path" in col_map else "'/'"
        expires_expr = col_map["expires_utc"] if "expires_utc" in col_map else "0"
        secure_expr = col_map["is_secure"] if "is_secure" in col_map else "1"
        httponly_expr = col_map["is_httponly"] if "is_httponly" in col_map else "1"
        partition_expr = (
            col_map["top_frame_site_key"] if "top_frame_site_key" in col_map else "''"
        )

        placeholders = ",".join("?" for _ in cookie_names)
        query = (
            f"SELECT name, value, encrypted_value, host_key, "
            f"{path_expr}, {expires_expr}, {secure_expr}, {httponly_expr}, {partition_expr} "
            f"FROM cookies "
            f"WHERE LOWER(host_key) IN (?, ?) AND name IN ({placeholders})"
        )
        params = [wanted, f".{wanted}"] + list(cookie_names)
        cursor.execute(query, params)

        now_chrome_utc = (int(time.time()) + _CHROME_EPOCH_OFFSET_SECONDS) * 1_000_000
        allowed_hosts = (wanted, f".{wanted}")
        scopes: dict[tuple[str, str, str], dict[str, list[tuple[int, int, int, Any, Any]]]] = {}
        for (
            name,
            value,
            encrypted_value,
            host_key,
            path,
            expires_utc,
            is_secure,
            is_httponly,
            top_frame_site_key,
        ) in cursor.fetchall():
            try:
                expires_val = int(expires_utc) if expires_utc is not None else 0
            except (TypeError, ValueError):
                continue
            if expires_val != 0 and expires_val <= now_chrome_utc:
                continue
            host_norm = str(host_key or "").strip().lower()
            if host_norm not in allowed_hosts:
                continue
            path_norm = path if isinstance(path, str) and path else "/"
            partition_norm = str(top_frame_site_key or "").strip()
            secure_flag = 1 if is_secure else 0
            httponly_flag = 1 if is_httponly else 0
            scope_key = (host_norm, path_norm, partition_norm)
            scopes.setdefault(scope_key, {}).setdefault(str(name), []).append(
                (secure_flag, httponly_flag, expires_val, value, encrypted_value)
            )

        if not scopes:
            logger.info("No matching cookies found in %s for domain %s", keychain_service, domain)
            return None

        requested = list(dict.fromkeys(cookie_names))
        for by_name in scopes.values():
            for candidates in by_name.values():
                candidates.sort(
                    key=lambda item: (
                        item[0] == 0,
                        item[1] == 0,
                        0 if item[2] == 0 else -item[2],
                    )
                )

        def _scope_sort_key(
            item: tuple[tuple[str, str, str], dict[str, list[tuple[int, int, int, Any, Any]]]]
        ) -> tuple[Any, ...]:
            (host_norm, path_norm, partition_norm), by_name = item
            has_all_names = all(name in by_name for name in requested)
            all_secure = all(cands[0][0] == 1 for cands in by_name.values())
            all_httponly = all(cands[0][1] == 1 for cands in by_name.values())
            return (
                not has_all_names,
                partition_norm != "",
                path_norm != "/",
                not all_secure,
                not all_httponly,
                host_norm != preferred_host,
                path_norm,
                host_norm,
                partition_norm,
            )

        aes_key = None
        key_fetched = False
        best_partial: Optional[dict[str, str]] = None

        for _scope_key, by_name in sorted(scopes.items(), key=_scope_sort_key):
            scope_results: dict[str, str] = {}
            for name, candidates in by_name.items():
                for _sec, _http, _exp, value, encrypted_value in candidates:
                    if isinstance(value, str) and value:
                        scope_results[name] = value
                        break

                    if isinstance(encrypted_value, (bytes, bytearray, memoryview)):
                        enc_bytes = bytes(encrypted_value)
                        if enc_bytes[:3] == b"v10":
                            if not key_fetched:
                                # Fetch the Keychain key lazily — only once we actually have
                                # a non-expired encrypted cookie to decrypt.
                                if key_cache is not None and keychain_service in key_cache:
                                    aes_key = key_cache[keychain_service]
                                else:
                                    passphrase = _get_chromium_encryption_key(keychain_service)
                                    aes_key = _derive_aes_key(passphrase) if passphrase else None
                                    if key_cache is not None:
                                        key_cache[keychain_service] = aes_key
                                key_fetched = True
                            if aes_key is None:
                                logger.debug("Skipping encrypted cookie %s — no Keychain access", name)
                                continue
                            decrypted = _decrypt_v10_value(enc_bytes, aes_key, db_version)
                            if decrypted:
                                scope_results[name] = decrypted
                                break
                            logger.debug("Failed to decrypt cookie %s", name)
                        elif enc_bytes:
                            logger.debug(
                                "Unknown encryption for cookie %s (prefix: %r)",
                                name,
                                enc_bytes[:3],
                            )
                    elif encrypted_value:
                        logger.debug("Malformed encrypted cookie %s", name)

            if scope_results:
                if all(name in scope_results for name in requested):
                    return scope_results
                if best_partial is None:
                    best_partial = scope_results

        if not best_partial:
            logger.info("No matching cookies found in %s for domain %s", keychain_service, domain)
            return None

        return best_partial

    except sqlite3.Error as e:
        logger.info("Failed to read %s cookies database: %s", keychain_service, e)
        return None
    except Exception as e:
        logger.info("Unexpected error reading %s cookies: %s", keychain_service, e)
        return None
    finally:
        if conn is not None:
            conn.close()
        _unlink_sqlite_temp_files(tmp_path)


def extract_chrome_cookies_macos(domain: str, cookie_names: list[str]) -> Optional[dict[str, str]]:
    """Extract cookies from Chrome on macOS.

    Resolves the cookie DB through the shared profile finder so Chrome gets the
    same modern ``Default/Network/Cookies`` (Chromium >= 96) and legacy
    ``Default/Cookies`` probing as the rest of the Chromium family.
    """
    return _extract_chromium_cookies_any_profile(
        CHROME_BASE_DIR, "Chrome Safe Storage", domain, cookie_names
    )


def _profile_cookie_db(
    profile_dir: Path, denials: Optional[list[PermissionError]] = None
) -> Optional[Path]:
    """Return the Cookies DB inside a profile dir, or None.

    Prefers the modern ``Network/Cookies`` location (Chromium >= 96 moved the
    cookie store into a per-profile ``Network/`` subdirectory) and falls back
    to the legacy flat ``Cookies`` file. Different browsers and versions use
    different layouts, so both are probed.
    """
    denied: list[PermissionError] = []
    for rel in ("Network/Cookies", "Cookies"):
        candidate = profile_dir / rel
        try:
            if path_exists(candidate):
                if denials is not None:
                    denials.extend(denied)
                return candidate
        except PermissionError as exc:
            denied.append(exc)
    if denials is not None:
        denials.extend(denied)
    elif denied:
        raise denied[0]
    return None


def _find_chromium_cookies_db(base_dir: Path) -> Optional[Path]:
    """Find a Chromium-based browser's Cookies database under base_dir.

    Checks the Default profile first, then the base dir itself (Opera's flat
    layout), then numbered "Profile N" directories by most-recently-modified.
    Each location is probed for both the modern ``Network/Cookies`` and legacy
    ``Cookies`` paths (see _profile_cookie_db). Chromium browsers create extra
    profiles as "Profile 1", "Profile 2", etc. alongside Default; the most
    recently used one is the likeliest to hold current cookies. Lexicographic
    sort would visit "Profile 10" before "Profile 2", which can return the
    wrong profile, so we sort by mtime.

    Kept for backward compatibility; new code should use
    _find_all_chromium_cookies_dbs() to search across all profiles.
    """
    dbs = _find_all_chromium_cookies_dbs(base_dir)
    return dbs[0] if dbs else None


def _find_all_chromium_cookies_dbs(
    base_dir: Path, denials: Optional[list[PermissionError]] = None
) -> list[Path]:
    """Return ALL candidate Cookies DBs under base_dir, best-guess order first.

    Order: Default, the base dir itself (Opera's flat layout), then numbered
    "Profile N" dirs by most-recently-modified. Unlike _find_chromium_cookies_db
    (which returns the first DB that merely EXISTS), this returns every profile
    so the caller can pick the one that actually holds the target domain's
    cookies. Needed because a logged-in session often lives in a non-Default
    profile while Default still has a (guest-only) cookie DB.
    """
    paths: list[Path] = []
    seen: set[Path] = set()
    denied: list[PermissionError] = []

    def add(p: Optional[Path]) -> None:
        if p is not None and p not in seen:
            seen.add(p)
            paths.append(p)

    add(_profile_cookie_db(base_dir / "Default", denied))
    add(_profile_cookie_db(base_dir, denied))
    try:
        candidates = []
        for child in base_dir.iterdir():
            if not child.name.startswith("Profile "):
                continue
            try:
                if path_is_dir(child):
                    candidates.append((child.stat().st_mtime, child))
            except PermissionError as exc:
                denied.append(exc)
        for _, child in sorted(candidates, reverse=True):
            add(_profile_cookie_db(child, denied))
    except PermissionError as exc:
        denied.append(exc)
    except OSError:
        pass
    if denials is not None:
        denials.extend(denied)
    elif denied and not paths:
        raise denied[0]
    return paths


def _extract_chromium_cookies_any_profile(
    base_dir: Path, keychain_service: str, domain: str, cookie_names: list[str]
) -> Optional[dict[str, str]]:
    """Try every profile under base_dir and return the best cookie match.

    Returns the first profile that yields ALL requested cookie_names. If no
    profile has the complete set, returns the first partial match found, or
    None if no profile yielded any. This fixes the single-profile limitation
    where a guest-only Default profile shadowed a logged-in "Profile N".
    """
    denials: list[PermissionError] = []
    db_paths = _find_all_chromium_cookies_dbs(base_dir, denials)
    if not db_paths:
        if denials:
            raise denials[0]
        logger.info("%s cookies database not found under %s", keychain_service, base_dir)
        return None
    best: Optional[dict[str, str]] = None
    key_cache: dict[str, Optional[bytes]] = {}
    for db_path in db_paths:
        try:
            got = _extract_chromium_cookies_macos(
                db_path, keychain_service, domain, cookie_names, key_cache=key_cache
            )
        except PermissionError as exc:
            denials.append(exc)
            continue
        if got:
            if all(name in got for name in cookie_names):
                logger.debug("Found complete cookie set for %s in %s", domain, db_path)
                return got
            if best is None:
                best = got
    if denials:
        raise denials[0]
    return best


def _find_brave_cookies_db() -> Optional[Path]:
    """Find Brave's Cookies database on macOS (Default, then Profile N)."""
    return _find_chromium_cookies_db(BRAVE_BASE_DIR)


def extract_brave_cookies_macos(domain: str, cookie_names: list[str]) -> Optional[dict[str, str]]:
    """Extract cookies from Brave on macOS.

    Brave uses the same v10 AES-128-CBC encryption as Chrome; only the DB
    path and Keychain service name differ.
    """
    return _extract_chromium_cookies_any_profile(
        BRAVE_BASE_DIR, "Brave Safe Storage", domain, cookie_names
    )


def extract_chromium_browser_cookies_macos(
    browser: str, domain: str, cookie_names: list[str]
) -> Optional[dict[str, str]]:
    """Extract cookies from a registry-defined Chromium browser on macOS.

    Covers every browser in CHROMIUM_BROWSER_PROFILES (Edge, Vivaldi, Opera,
    Arc, Dia, Chromium). They all reuse Chrome's v10 AES-128-CBC encryption; only
    the profile directory and Keychain service name differ.
    """
    spec = CHROMIUM_BROWSER_PROFILES.get(browser)
    if spec is None:
        logger.debug("Unknown Chromium browser: %s", browser)
        return None
    base_dir, keychain_service = spec
    return _extract_chromium_cookies_any_profile(
        base_dir, keychain_service, domain, cookie_names
    )
