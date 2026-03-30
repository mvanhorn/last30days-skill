"""Tests for Chrome cookie extraction on macOS."""

import hashlib
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from scripts.lib.chrome_cookies import (
    CHROME_COOKIES_DB,
    CHROME_IV_HEX,
    CHROME_KEY_LENGTH,
    CHROME_PBKDF2_ITERATIONS,
    CHROME_SALT,
    _derive_aes_key,
    _get_chrome_encryption_key,
    _get_db_version,
    _remove_pkcs7_padding,
    _decrypt_v10_value,
    extract_chrome_cookies_macos,
)


# ---------------------------------------------------------------------------
# Helpers — create real encrypted cookie values using known key + system openssl
# ---------------------------------------------------------------------------

KNOWN_PASSPHRASE = b"test_passphrase_for_unit_tests"
KNOWN_AES_KEY = _derive_aes_key(KNOWN_PASSPHRASE)


def _encrypt_value_v10(plaintext: str, aes_key: bytes) -> bytes:
    """Encrypt a value the same way Chrome v10 does, using system openssl.

    Returns b'v10' + AES-128-CBC ciphertext with PKCS7 padding.
    """
    hex_key = aes_key.hex()
    result = subprocess.run(
        [
            "openssl", "enc", "-aes-128-cbc", "-e",
            "-K", hex_key,
            "-iv", CHROME_IV_HEX,
        ],
        input=plaintext.encode("utf-8"),
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, f"openssl encrypt failed: {result.stderr}"
    return b"v10" + result.stdout


def _encrypt_value_v10_with_sha_prefix(plaintext: str, aes_key: bytes) -> bytes:
    """Encrypt with a 32-byte SHA-256 prefix (Chrome 130+ style)."""
    raw = b"\x00" * 32 + plaintext.encode("utf-8")
    hex_key = aes_key.hex()
    result = subprocess.run(
        [
            "openssl", "enc", "-aes-128-cbc", "-e",
            "-K", hex_key,
            "-iv", CHROME_IV_HEX,
        ],
        input=raw,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, f"openssl encrypt failed: {result.stderr}"
    return b"v10" + result.stdout


def _create_chrome_cookies_db(path: str, cookies: list[tuple], db_version: int = 20) -> None:
    """Create a minimal Chrome Cookies SQLite database.

    cookies: list of (host_key, name, value, encrypted_value) tuples
    """
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)", (str(db_version),))
    c.execute(
        "CREATE TABLE IF NOT EXISTS cookies ("
        "  host_key TEXT NOT NULL,"
        "  name TEXT NOT NULL,"
        "  value TEXT NOT NULL DEFAULT '',"
        "  encrypted_value BLOB NOT NULL DEFAULT x'',"
        "  path TEXT NOT NULL DEFAULT '/',"
        "  expires_utc INTEGER NOT NULL DEFAULT 0,"
        "  is_secure INTEGER NOT NULL DEFAULT 1,"
        "  is_httponly INTEGER NOT NULL DEFAULT 1,"
        "  creation_utc INTEGER NOT NULL DEFAULT 0,"
        "  last_access_utc INTEGER NOT NULL DEFAULT 0,"
        "  has_expires INTEGER NOT NULL DEFAULT 1,"
        "  is_persistent INTEGER NOT NULL DEFAULT 1,"
        "  priority INTEGER NOT NULL DEFAULT 1,"
        "  samesite INTEGER NOT NULL DEFAULT 0,"
        "  source_scheme INTEGER NOT NULL DEFAULT 2,"
        "  source_port INTEGER NOT NULL DEFAULT 443,"
        "  last_update_utc INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    for host_key, name, value, encrypted_value in cookies:
        c.execute(
            "INSERT INTO cookies (host_key, name, value, encrypted_value) VALUES (?, ?, ?, ?)",
            (host_key, name, value, encrypted_value),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# PKCS7 padding tests
# ---------------------------------------------------------------------------

class TestPkcs7Padding:
    def test_valid_padding_1(self):
        # 1 byte of padding
        data = b"hello world!!!!!" + b"\x01"
        assert _remove_pkcs7_padding(data) == b"hello world!!!!!"

    def test_valid_padding_5(self):
        data = b"hello world" + b"\x05\x05\x05\x05\x05"
        assert _remove_pkcs7_padding(data) == b"hello world"

    def test_valid_padding_16(self):
        # Full block of padding
        data = b"\x10" * 16
        assert _remove_pkcs7_padding(data) == b""

    def test_invalid_padding_zero(self):
        data = b"hello\x00"
        assert _remove_pkcs7_padding(data) is None

    def test_invalid_padding_mismatch(self):
        data = b"hello\x03\x03\x02"
        assert _remove_pkcs7_padding(data) is None

    def test_empty_data(self):
        assert _remove_pkcs7_padding(b"") is None


# ---------------------------------------------------------------------------
# Key derivation test
# ---------------------------------------------------------------------------

class TestKeyDerivation:
    def test_derive_aes_key_deterministic(self):
        key1 = _derive_aes_key(b"my_passphrase")
        key2 = _derive_aes_key(b"my_passphrase")
        assert key1 == key2
        assert len(key1) == 16

    def test_derive_aes_key_different_passphrases(self):
        key1 = _derive_aes_key(b"passphrase_a")
        key2 = _derive_aes_key(b"passphrase_b")
        assert key1 != key2


# ---------------------------------------------------------------------------
# Decryption test (real openssl, known key)
# ---------------------------------------------------------------------------

class TestDecryption:
    def test_decrypt_v10_roundtrip(self):
        """Encrypt then decrypt — verifies the full pipeline works."""
        original = "my_secret_cookie_value_12345"
        encrypted = _encrypt_value_v10(original, KNOWN_AES_KEY)
        assert encrypted[:3] == b"v10"

        decrypted = _decrypt_v10_value(encrypted, KNOWN_AES_KEY, db_version=20)
        assert decrypted == original

    def test_decrypt_v10_chrome130_with_sha_prefix(self):
        """Chrome 130+ (db_version >= 24) strips 32-byte SHA-256 prefix."""
        original = "session_token_abc"
        encrypted = _encrypt_value_v10_with_sha_prefix(original, KNOWN_AES_KEY)

        decrypted = _decrypt_v10_value(encrypted, KNOWN_AES_KEY, db_version=24)
        assert decrypted == original

    def test_decrypt_wrong_key_returns_none_or_garbage(self):
        """Wrong key should either fail decryption or produce garbage."""
        original = "secret"
        encrypted = _encrypt_value_v10(original, KNOWN_AES_KEY)
        wrong_key = _derive_aes_key(b"wrong_passphrase")

        result = _decrypt_v10_value(encrypted, wrong_key, db_version=20)
        # Either None (padding check fails) or garbage (not the original)
        assert result is None or result != original

    def test_decrypt_empty_ciphertext(self):
        """v10 prefix with no ciphertext should return None."""
        assert _decrypt_v10_value(b"v10", KNOWN_AES_KEY, db_version=20) is None


# ---------------------------------------------------------------------------
# Chrome not installed → returns None
# ---------------------------------------------------------------------------

class TestChromeNotInstalled:
    def test_db_not_found(self):
        with mock.patch(
            "scripts.lib.chrome_cookies.CHROME_COOKIES_DB",
            Path("/nonexistent/path/Cookies"),
        ):
            result = extract_chrome_cookies_macos(".x.com", ["auth_token"])
            assert result is None


# ---------------------------------------------------------------------------
# Keychain access denied → returns None
# ---------------------------------------------------------------------------

class TestKeychainDenied:
    def test_security_command_fails(self):
        with mock.patch("scripts.lib.chrome_cookies.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=44, stdout="", stderr="security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain."
            )
            result = _get_chrome_encryption_key()
            assert result is None

    def test_security_command_not_found(self):
        with mock.patch("scripts.lib.chrome_cookies.subprocess.run", side_effect=FileNotFoundError):
            result = _get_chrome_encryption_key()
            assert result is None


# ---------------------------------------------------------------------------
# openssl not found → returns None
# ---------------------------------------------------------------------------

class TestOpensslNotFound:
    def test_openssl_missing(self):
        encrypted = _encrypt_value_v10("test", KNOWN_AES_KEY)
        with mock.patch("scripts.lib.chrome_cookies.subprocess.run", side_effect=FileNotFoundError):
            result = _decrypt_v10_value(encrypted, KNOWN_AES_KEY, db_version=20)
            assert result is None


# ---------------------------------------------------------------------------
# Unencrypted cookie values → returned as-is
# ---------------------------------------------------------------------------

class TestUnencryptedCookies:
    def test_plain_value_returned(self, tmp_path):
        """Unencrypted cookies (value column populated) returned without decryption."""
        db_path = str(tmp_path / "Cookies")
        _create_chrome_cookies_db(db_path, [
            (".x.com", "auth_token", "plain_token_value", b""),
            (".x.com", "ct0", "plain_ct0_value", b""),
        ])

        with mock.patch("scripts.lib.chrome_cookies.CHROME_COOKIES_DB", Path(db_path)):
            # No keychain needed for unencrypted values
            with mock.patch("scripts.lib.chrome_cookies._get_chrome_encryption_key", return_value=None):
                result = extract_chrome_cookies_macos(".x.com", ["auth_token", "ct0"])

        assert result == {"auth_token": "plain_token_value", "ct0": "plain_ct0_value"}


# ---------------------------------------------------------------------------
# Full integration: mock DB with real v10 encryption, mock Keychain
# ---------------------------------------------------------------------------

class TestFullExtraction:
    def test_encrypted_cookies_extracted(self, tmp_path):
        """End-to-end: create DB with real v10-encrypted values, extract them."""
        auth_val = "my_auth_token_123"
        ct0_val = "my_ct0_csrf_456"

        encrypted_auth = _encrypt_value_v10(auth_val, KNOWN_AES_KEY)
        encrypted_ct0 = _encrypt_value_v10(ct0_val, KNOWN_AES_KEY)

        db_path = str(tmp_path / "Cookies")
        _create_chrome_cookies_db(db_path, [
            (".x.com", "auth_token", "", encrypted_auth),
            (".x.com", "ct0", "", encrypted_ct0),
            (".other.com", "other", "", b""),  # unrelated cookie
        ])

        with mock.patch("scripts.lib.chrome_cookies.CHROME_COOKIES_DB", Path(db_path)):
            with mock.patch(
                "scripts.lib.chrome_cookies._get_chrome_encryption_key",
                return_value=KNOWN_PASSPHRASE,
            ):
                result = extract_chrome_cookies_macos(".x.com", ["auth_token", "ct0"])

        assert result is not None
        assert result["auth_token"] == auth_val
        assert result["ct0"] == ct0_val

    def test_no_matching_cookies_returns_none(self, tmp_path):
        db_path = str(tmp_path / "Cookies")
        _create_chrome_cookies_db(db_path, [
            (".other.com", "session", "val", b""),
        ])

        with mock.patch("scripts.lib.chrome_cookies.CHROME_COOKIES_DB", Path(db_path)):
            with mock.patch("scripts.lib.chrome_cookies._get_chrome_encryption_key", return_value=None):
                result = extract_chrome_cookies_macos(".x.com", ["auth_token"])

        assert result is None

    def test_chrome130_db_version_24(self, tmp_path):
        """Chrome 130+ with db_version >= 24 strips SHA-256 prefix."""
        auth_val = "token_for_chrome130"
        encrypted_auth = _encrypt_value_v10_with_sha_prefix(auth_val, KNOWN_AES_KEY)

        db_path = str(tmp_path / "Cookies")
        _create_chrome_cookies_db(db_path, [
            (".x.com", "auth_token", "", encrypted_auth),
        ], db_version=24)

        with mock.patch("scripts.lib.chrome_cookies.CHROME_COOKIES_DB", Path(db_path)):
            with mock.patch(
                "scripts.lib.chrome_cookies._get_chrome_encryption_key",
                return_value=KNOWN_PASSPHRASE,
            ):
                result = extract_chrome_cookies_macos(".x.com", ["auth_token"])

        assert result is not None
        assert result["auth_token"] == auth_val


# ---------------------------------------------------------------------------
# DB version detection
# ---------------------------------------------------------------------------

class TestDbVersion:
    def test_reads_version_from_meta(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        c.execute("INSERT INTO meta VALUES ('version', '24')")
        conn.commit()
        assert _get_db_version(c) == 24
        conn.close()

    def test_no_meta_table_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE dummy (x TEXT)")
        conn.commit()
        assert _get_db_version(c) == 0
        conn.close()
