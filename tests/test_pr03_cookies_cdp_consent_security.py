"""Security regression tests for PR 3: browser cookies, CDP, and consent-gate hardening.

Covers:
  - HIGH-077: In-process macOS Chromium AES-128-CBC decryption (CommonCrypto + pure-Python
    fallback), zero openssl subprocess argv exposure, and pinned /usr/bin/security.
  - HIGH-079: Firefox (and Chromium) temp SQLite fd closure before unlink + WAL/SHM/journal
    cleanup (Windows %TEMP% leak regression) + Firefox cookie scoping hardening.
  - HIGH-081: Chromium exact host matching, PRAGMA schema inspection, expires_utc filtering,
    and single-scope grouping by (host_key, path, top_frame_site_key) without cross-scope mixing.
  - HIGH-082: Strict CDP URL validation (loopback-only http/ws, no userinfo/fragments/control
    chars, valid port 1..65535), no-proxy/no-redirect HTTP opener, cross-host/port
    webSocketDebuggerUrl rejection, WebSocket request-line injection rejection, and safe socket
    cleanup without raising.
  - HIGH-083: Per-user owner-only (0o700) Chrome login profile directory in box_chrome_login.py,
    lstat/symlink/UID/mode enforcement, non-zero abort on error, and `mkdir -m 700 -p`.
  - HIGH-084: Symmetric env/config refusal resolution in agentcookie.is_disabled() and
    respecting explicit FROM_BROWSER pins unless AGENTCOOKIE=on.
"""

from __future__ import annotations

import ctypes
import http.server
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

import box_chrome_login as bcl
from lib import agentcookie, chrome_cdp, chrome_cookies, cookie_extract

# Independent v10 vectors (PBKDF2-HMAC-SHA1, saltysalt, 1003 iterations, 16-byte key).
_V10_AES_KEY = bytes.fromhex("bae7b43668299f6b6839c58cff9e97db")
_V10_AUTH_TOKEN = bytes.fromhex(
    "76313040a19511ccca2ce72c5b842326adf2e97a36831c0c5943045f7d66f96cbf011e"
)
_V10_CT0 = bytes.fromhex("763130189b696f39999dba1b0c8f9c005b8a24")

# NIST FIPS-197 Appendix C.1 (AES-128) test vector.
_FIPS197_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
_FIPS197_PLAINTEXT = bytes.fromhex("00112233445566778899aabbccddeeff")
_FIPS197_CIPHERTEXT = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")


# ===========================================================================
# HIGH-077: In-process AES-128-CBC decryption & pinned /usr/bin/security
# ===========================================================================


class TestHigh077ChromeCryptoAndSecurityPin:
    def test_keychain_lookup_pins_usr_bin_security(self):
        proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="dummy-pass\n", stderr="")
        with mock.patch("lib.chrome_cookies.subprocess.run", return_value=proc) as run:
            got = chrome_cookies._get_chromium_encryption_key("Chrome Safe Storage")
        assert got == b"dummy-pass"
        argv = run.call_args.args[0]
        assert argv[0] == "/usr/bin/security"
        assert argv[1:] == ["find-generic-password", "-w", "-s", "Chrome Safe Storage"]

    def test_decrypt_v10_spawns_zero_subprocesses_and_never_uses_openssl(self):
        with (
            mock.patch(
                "lib.chrome_cookies.subprocess.run",
                side_effect=AssertionError("must never spawn openssl or any subprocess"),
            ),
            mock.patch(
                "subprocess.Popen",
                side_effect=AssertionError("must never spawn subprocesses"),
            ),
        ):
            assert chrome_cookies._decrypt_v10_value(_V10_AUTH_TOKEN, _V10_AES_KEY, 20) == (
                "my_auth_token_123"
            )
            assert chrome_cookies._decrypt_v10_value(_V10_CT0, _V10_AES_KEY, 20) == (
                "my_ct0_csrf_456"
            )

    def test_pure_aes128_block_matches_fips197_appendix_c1(self):
        round_keys = chrome_cookies._aes128_expand_key(_FIPS197_KEY)
        decrypted = chrome_cookies._aes128_decrypt_block(_FIPS197_CIPHERTEXT, round_keys)
        assert decrypted == _FIPS197_PLAINTEXT

    def test_decrypt_v10_rejects_bad_prefix_length_and_non_bytes(self):
        assert chrome_cookies._decrypt_v10_value(b"v11" + b"\x00" * 16, _V10_AES_KEY, 20) is None
        assert chrome_cookies._decrypt_v10_value(b"v10short", _V10_AES_KEY, 20) is None
        assert chrome_cookies._decrypt_v10_value(b"v10" + b"\x00" * 16, b"short-key", 20) is None
        assert chrome_cookies._decrypt_v10_value(42, _V10_AES_KEY, 20) is None  # type: ignore[arg-type]

    def test_commoncrypto_path_uses_libcommoncrypto_cccrypt_on_darwin(self, monkeypatch):
        seen_calls = []

        def fake_cccrypt(
            op, alg, options, key, key_len, iv, data_in, data_in_len, data_out, data_out_avail, out_moved
        ):
            seen_calls.append((op, alg, options, key, key_len, iv, data_in, data_in_len, data_out_avail))
            plaintext = b"cc-decrypted-cookie"
            ctypes.memmove(data_out, plaintext, len(plaintext))
            out_moved._obj.value = len(plaintext)
            return 0  # kCCSuccess

        fake_lib = mock.MagicMock()
        fake_lib.CCCrypt = fake_cccrypt

        monkeypatch.setattr(chrome_cookies, "_COMMONCRYPTO_LOADED", False)
        monkeypatch.setattr(chrome_cookies, "_COMMONCRYPTO_FUNC", None)
        monkeypatch.setattr(chrome_cookies.sys, "platform", "darwin")
        with mock.patch("lib.chrome_cookies.ctypes.CDLL", return_value=fake_lib) as cdll:
            got = chrome_cookies._decrypt_v10_value(_V10_AUTH_TOKEN, _V10_AES_KEY, db_version=20)

        cdll.assert_called_once_with("/usr/lib/system/libcommonCrypto.dylib")
        assert got == "cc-decrypted-cookie"
        assert len(seen_calls) == 1
        op, alg, options, key, key_len, iv, data_in, data_in_len, data_out_avail = seen_calls[0]
        assert (op, alg, options) == (1, 0, 1)  # kCCDecrypt, kCCAlgorithmAES128, kCCOptionPKCS7Padding
        assert key == _V10_AES_KEY and key_len == 16
        assert iv == b" " * 16
        assert data_in == _V10_AUTH_TOKEN[3:] and data_in_len == len(_V10_AUTH_TOKEN[3:])
        assert data_out_avail >= data_in_len + 16

    def test_commoncrypto_decode_error_returns_none_without_fallback(self, monkeypatch):
        def failing_cccrypt(*_args):
            return -4304  # kCCDecodeError

        monkeypatch.setattr(chrome_cookies, "_COMMONCRYPTO_LOADED", True)
        monkeypatch.setattr(chrome_cookies, "_COMMONCRYPTO_FUNC", failing_cccrypt)
        with mock.patch(
            "lib.chrome_cookies._decrypt_aes128_cbc_pure",
            side_effect=AssertionError("must not fall back when CommonCrypto reports decode error"),
        ):
            assert chrome_cookies._decrypt_v10_value(_V10_AUTH_TOKEN, _V10_AES_KEY, 20) is None

    def test_chrome130_empty_cookie_does_not_leak_32_byte_digest(self, tmp_path):
        fake_digest_only = b"\x41" * 32
        with mock.patch("lib.chrome_cookies._decrypt_aes128_cbc", return_value=fake_digest_only):
            assert chrome_cookies._decrypt_v10_value(b"v10" + b"\x00" * 48, _V10_AES_KEY, 24) == ""

        db = tmp_path / "Cookies"
        _make_chromium_db(
            db,
            [(".x.com", "auth_token", "", b"v10" + b"\x00" * 48, "/", _chrome_utc(3600), 1, 1, "")],
            db_version=24,
        )
        with (
            mock.patch("lib.chrome_cookies._get_chromium_encryption_key", return_value=b"pass"),
            mock.patch("lib.chrome_cookies._decrypt_aes128_cbc", return_value=fake_digest_only),
        ):
            assert (
                chrome_cookies._extract_chromium_cookies_macos(
                    db, "Chrome Safe Storage", ".x.com", ["auth_token"]
                )
                is None
            )


# ===========================================================================
# HIGH-079: Firefox temp fd close before unlink, sidecars, and scoping
# ===========================================================================


def _make_firefox_db(
    path: Path,
    rows: list[tuple[str, str, str, str, int, int, int, str]],
    *,
    wal_mode: bool = False,
    user_version: int = 0,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        if wal_mode:
            conn.execute("PRAGMA journal_mode=WAL")
        if user_version:
            conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.execute(
            "CREATE TABLE moz_cookies ("
            "  id INTEGER PRIMARY KEY,"
            "  host TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  value TEXT NOT NULL,"
            "  path TEXT NOT NULL DEFAULT '/',"
            "  expiry INTEGER NOT NULL DEFAULT 0,"
            "  isSecure INTEGER NOT NULL DEFAULT 1,"
            "  isHttpOnly INTEGER NOT NULL DEFAULT 1,"
            "  originAttributes TEXT NOT NULL DEFAULT ''"
            ")"
        )
        conn.executemany(
            "INSERT INTO moz_cookies "
            "(host, name, value, path, expiry, isSecure, isHttpOnly, originAttributes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


class TestHigh079FirefoxTempCleanupAndScoping:
    def test_windows_style_unlink_sees_zero_open_fds_and_cleans_sidecars(self, tmp_path, monkeypatch):
        db = tmp_path / "cookies.sqlite"
        _make_firefox_db(
            db,
            [(".x.com", "auth_token", "dummy-auth", "/", 0, 1, 1, "")],
            wal_mode=True,
        )
        scratch = tmp_path / "temp_root"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))

        open_fds: dict[str, set[int]] = {}
        real_open = os.open
        real_close = os.close
        real_unlink = Path.unlink
        real_copy = cookie_extract.copy_snapshot

        def tracked_open(path, flags, mode=0o777, *args, **kwargs):
            fd = real_open(path, flags, mode, *args, **kwargs)
            p = str(path)
            if p.startswith(str(scratch)):
                open_fds.setdefault(p, set()).add(fd)
            return fd

        def tracked_close(fd):
            for fds in open_fds.values():
                fds.discard(fd)
            return real_close(fd)

        def windows_strict_unlink(self, *args, **kwargs):
            p = str(self)
            if open_fds.get(p):
                raise PermissionError(13, "WinError 32: file in use by open fd", p)
            return real_unlink(self, *args, **kwargs)

        def copy_with_sidecars(src, dst, **kwargs):
            real_copy(src, dst, **kwargs)
            # Simulate WAL/SHM/journal sidecar creation beside temp copy.
            for suffix in ("-wal", "-shm", "-journal"):
                Path(str(dst) + suffix).write_bytes(b"sidecar")

        monkeypatch.setattr(os, "open", tracked_open)
        monkeypatch.setattr(os, "close", tracked_close)
        monkeypatch.setattr(Path, "unlink", windows_strict_unlink)
        monkeypatch.setattr(cookie_extract, "copy_snapshot", copy_with_sidecars)

        got = cookie_extract._query_cookies_db(db, ".x.com", ["auth_token"])
        assert got == {"auth_token": "dummy-auth"}
        assert list(scratch.iterdir()) == []
        assert all(not fds for fds in open_fds.values())

    def test_firefox_temp_dir_cleaned_on_query_error(self, tmp_path, monkeypatch):
        db = tmp_path / "broken.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE other (x TEXT)")
        conn.commit()
        conn.close()

        scratch = tmp_path / "temp_err"
        scratch.mkdir()
        monkeypatch.setattr(tempfile, "tempdir", str(scratch))

        assert cookie_extract._query_cookies_db(db, ".x.com", ["auth_token"]) is None
        assert list(scratch.iterdir()) == []

    def test_firefox_scoping_rejects_suffix_subdomains_expired_and_cross_scope_mix(self, tmp_path):
        db = tmp_path / "cookies.sqlite"
        future = int(time.time()) + 3600
        past = int(time.time()) - 3600
        _make_firefox_db(
            db,
            [
                # Suffix/subdomain lookalikes must never match.
                ("evilx.com", "auth_token", "evil-auth", "/", future, 1, 1, ""),
                ("sub.x.com", "auth_token", "sub-auth", "/", future, 1, 1, ""),
                # Expired root cookie must be filtered out.
                (".x.com", "auth_token", "expired-auth", "/", past, 1, 1, ""),
                # Containerized/partitioned and subpath cookies must lose to unpartitioned root.
                (".x.com", "auth_token", "part-auth", "/", future, 1, 1, "^userContextId=2"),
                (".x.com", "ct0", "part-ct0", "/", future, 1, 1, "^userContextId=2"),
                (".x.com", "auth_token", "subpath-auth", "/i", future, 1, 1, ""),
                (".x.com", "ct0", "subpath-ct0", "/i", future, 1, 1, ""),
                # Insecure cookie on same scope loses to secure cookie.
                (".x.com", "auth_token", "insecure-auth", "/", future, 0, 1, ""),
                # Valid unpartitioned root pair.
                (".X.COM", "auth_token", "good-auth", "/", future, 1, 1, ""),
                (".x.com", "ct0", "good-ct0", "/", 0, 1, 1, ""),
            ],
        )
        got = cookie_extract._query_cookies_db(db, "x.com", ["auth_token", "ct0"])
        assert got == {"auth_token": "good-auth", "ct0": "good-ct0"}

    def test_firefox_never_mixes_pairs_across_path_partition_or_subdomain(self, tmp_path):
        db = tmp_path / "cookies.sqlite"
        future = int(time.time()) + 3600
        _make_firefox_db(
            db,
            [
                (".x.com", "auth_token", "root-auth", "/", future, 1, 1, ""),
                (".x.com", "ct0", "subpath-ct0", "/i", future, 1, 1, ""),
                (".x.com", "ct0", "part-ct0", "/", future, 1, 1, "^userContextId=2"),
                ("sub.x.com", "ct0", "subdomain-ct0", "/", future, 1, 1, ""),
            ],
        )
        got = cookie_extract._query_cookies_db(db, ".x.com", ["auth_token", "ct0"])
        assert got is not None
        assert set(got) != {"auth_token", "ct0"}

    def test_firefox_pairs_host_only_and_domain_cookies_in_same_scope(self, tmp_path):
        db = tmp_path / "cookies.sqlite"
        future = int(time.time()) + 3600
        _make_firefox_db(
            db,
            [
                ("x.com", "auth_token", "host-auth", "/", future, 1, 1, ""),
                (".x.com", "ct0", "domain-ct0", "/", future, 1, 1, ""),
            ],
        )
        assert cookie_extract._query_cookies_db(db, ".x.com", ["auth_token", "ct0"]) == {
            "auth_token": "host-auth",
            "ct0": "domain-ct0",
        }

        # When both x.com and .x.com define the same cookie, preferred_host wins.
        db_both = tmp_path / "both.sqlite"
        _make_firefox_db(
            db_both,
            [
                ("x.com", "auth_token", "host-auth", "/", future, 1, 1, ""),
                (".x.com", "auth_token", "domain-auth", "/", future, 1, 1, ""),
            ],
        )
        assert cookie_extract._query_cookies_db(db_both, ".x.com", ["auth_token"]) == {
            "auth_token": "domain-auth"
        }
        assert cookie_extract._query_cookies_db(db_both, "x.com", ["auth_token"]) == {
            "auth_token": "host-auth"
        }

    def test_firefox_142_millisecond_expiry_drops_expired_and_keeps_future(self, tmp_path):
        db = tmp_path / "cookies_v16.sqlite"
        now_ms = int(time.time()) * 1000
        past_ms = now_ms - 60_000
        future_ms = now_ms + 3_600_000
        _make_firefox_db(
            db,
            [
                (".x.com", "auth_token", "expired-ms-auth", "/", past_ms, 1, 1, ""),
                (".x.com", "ct0", "tiny-expired-ms-ct0", "/", 500, 1, 1, ""),
                (".x.com", "auth_token", "valid-ms-auth", "/", future_ms, 1, 1, ""),
                (".x.com", "ct0", "valid-ms-ct0", "/", future_ms, 1, 1, ""),
            ],
            user_version=16,
        )
        assert cookie_extract._query_cookies_db(db, ".x.com", ["auth_token", "ct0"]) == {
            "auth_token": "valid-ms-auth",
            "ct0": "valid-ms-ct0",
        }

        # Only expired millisecond rows under user_version=16 -> None.
        db_exp = tmp_path / "cookies_v16_expired.sqlite"
        _make_firefox_db(
            db_exp,
            [
                (".x.com", "auth_token", "expired-ms-auth", "/", past_ms, 1, 1, ""),
                (".x.com", "ct0", "tiny-expired-ms-ct0", "/", 500, 1, 1, ""),
            ],
            user_version=16,
        )
        assert cookie_extract._query_cookies_db(db_exp, ".x.com", ["auth_token", "ct0"]) is None


# ===========================================================================
# HIGH-081: Chromium exact host, expiry, schema, and single-scope pairing
# ===========================================================================


def _make_chromium_db(
    path: Path,
    rows: list[tuple[str, str, str, bytes, str, int, int, int, str]],
    *,
    include_chips: bool = True,
    db_version: int = 24,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO meta VALUES ('version', ?)", (str(db_version),))
        chips_col = ", top_frame_site_key TEXT NOT NULL DEFAULT ''" if include_chips else ""
        conn.execute(
            "CREATE TABLE cookies ("
            "  host_key TEXT NOT NULL,"
            "  name TEXT NOT NULL,"
            "  value TEXT NOT NULL DEFAULT '',"
            "  encrypted_value BLOB NOT NULL DEFAULT x'',"
            "  path TEXT NOT NULL DEFAULT '/',"
            "  expires_utc INTEGER NOT NULL DEFAULT 0,"
            "  is_secure INTEGER NOT NULL DEFAULT 1,"
            "  is_httponly INTEGER NOT NULL DEFAULT 1"
            f"{chips_col}"
            ")"
        )
        for host_key, name, value, enc, cpath, exp, sec, httponly, chips in rows:
            if include_chips:
                conn.execute(
                    "INSERT INTO cookies "
                    "(host_key, name, value, encrypted_value, path, expires_utc, "
                    "is_secure, is_httponly, top_frame_site_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (host_key, name, value, enc, cpath, exp, sec, httponly, chips),
                )
            else:
                conn.execute(
                    "INSERT INTO cookies "
                    "(host_key, name, value, encrypted_value, path, expires_utc, "
                    "is_secure, is_httponly) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (host_key, name, value, enc, cpath, exp, sec, httponly),
                )
        conn.commit()
    finally:
        conn.close()


def _chrome_utc(offset_seconds: int) -> int:
    return (int(time.time()) + offset_seconds + 11644473600) * 1_000_000


class TestHigh081ChromiumCookieScoping:
    def test_rejects_suffix_confusion_subdomains_and_like_wildcards(self, tmp_path):
        db = tmp_path / "Cookies"
        future = _chrome_utc(3600)
        _make_chromium_db(
            db,
            [
                ("evilx.com", "auth_token", "evil-auth", b"", "/", future, 1, 1, ""),
                ("notx.com", "ct0", "notx-ct0", b"", "/", future, 1, 1, ""),
                ("sub.x.com", "auth_token", "sub-auth", b"", "/", future, 1, 1, ""),
                ("x_com", "auth_token", "wildcard-auth", b"", "/", future, 1, 1, ""),
                ("x.com.evil.com", "auth_token", "suffix-auth", b"", "/", future, 1, 1, ""),
            ],
        )
        with mock.patch("lib.chrome_cookies._get_chromium_encryption_key") as get_key:
            assert (
                chrome_cookies._extract_chromium_cookies_macos(
                    db, "Chrome Safe Storage", "x.com", ["auth_token", "ct0"]
                )
                is None
            )
        get_key.assert_not_called()

    def test_filters_expired_cookies_before_keychain_lookup(self, tmp_path):
        db = tmp_path / "Cookies"
        past = _chrome_utc(-60)
        _make_chromium_db(
            db,
            [
                (".x.com", "auth_token", "", _V10_AUTH_TOKEN, "/", past, 1, 1, ""),
                (".x.com", "ct0", "expired-plain", b"", "/", past, 1, 1, ""),
            ],
        )
        with mock.patch("lib.chrome_cookies._get_chromium_encryption_key") as get_key:
            assert (
                chrome_cookies._extract_chromium_cookies_macos(
                    db, "Chrome Safe Storage", ".x.com", ["auth_token", "ct0"]
                )
                is None
            )
        # Expired encrypted rows must never trigger a macOS Keychain prompt.
        get_key.assert_not_called()

    def test_prefers_unpartitioned_root_secure_scope_over_chips_subpath_and_insecure(self, tmp_path):
        db = tmp_path / "Cookies"
        future = _chrome_utc(3600)
        _make_chromium_db(
            db,
            [
                # Partitioned (CHIPS) complete pair appears first in DB.
                (".x.com", "auth_token", "chips-auth", b"", "/", future, 1, 1, "https://embed.example"),
                (".x.com", "ct0", "chips-ct0", b"", "/", future, 1, 1, "https://embed.example"),
                # Subpath complete pair.
                (".x.com", "auth_token", "subpath-auth", b"", "/i/flow", future, 1, 1, ""),
                (".x.com", "ct0", "subpath-ct0", b"", "/i/flow", future, 1, 1, ""),
                # Insecure cookie in the root scope must lose to secure cookie.
                (".x.com", "auth_token", "insecure-auth", b"", "/", future, 0, 1, ""),
                # Legitimate unpartitioned root secure pair (case-insensitive host_key).
                (".X.COM", "auth_token", "good-auth", b"", "/", future, 1, 1, ""),
                (".x.com", "ct0", "good-ct0", b"", "/", 0, 1, 1, ""),
            ],
            include_chips=True,
        )
        got = chrome_cookies._extract_chromium_cookies_macos(
            db, "Chrome Safe Storage", "x.com", ["auth_token", "ct0"]
        )
        assert got == {"auth_token": "good-auth", "ct0": "good-ct0"}

    def test_never_mixes_cookies_across_path_chips_or_subdomain_scopes(self, tmp_path):
        db = tmp_path / "Cookies"
        future = _chrome_utc(3600)
        _make_chromium_db(
            db,
            [
                (".x.com", "auth_token", "root-auth", b"", "/", future, 1, 1, ""),
                (".x.com", "ct0", "subpath-ct0", b"", "/i", future, 1, 1, ""),
                (".x.com", "ct0", "chips-ct0", b"", "/", future, 1, 1, "https://other.example"),
                ("sub.x.com", "ct0", "subdomain-ct0", b"", "/", future, 1, 1, ""),
            ],
            include_chips=True,
        )
        got = chrome_cookies._extract_chromium_cookies_macos(
            db, "Chrome Safe Storage", ".x.com", ["auth_token", "ct0"]
        )
        assert got is not None
        assert set(got) != {"auth_token", "ct0"}

    def test_pairs_host_only_and_domain_cookies_in_same_scope(self, tmp_path):
        db = tmp_path / "Cookies"
        future = _chrome_utc(3600)
        _make_chromium_db(
            db,
            [
                ("x.com", "auth_token", "host-auth", b"", "/", future, 1, 1, ""),
                (".x.com", "ct0", "domain-ct0", b"", "/", future, 1, 1, ""),
            ],
            include_chips=True,
        )
        assert chrome_cookies._extract_chromium_cookies_macos(
            db, "Chrome Safe Storage", ".x.com", ["auth_token", "ct0"]
        ) == {"auth_token": "host-auth", "ct0": "domain-ct0"}

        # When both x.com and .x.com define the same cookie, preferred_host wins.
        db_both = tmp_path / "Cookies_both"
        _make_chromium_db(
            db_both,
            [
                ("x.com", "auth_token", "host-auth", b"", "/", future, 1, 1, ""),
                (".x.com", "auth_token", "domain-auth", b"", "/", future, 1, 1, ""),
            ],
            include_chips=True,
        )
        assert chrome_cookies._extract_chromium_cookies_macos(
            db_both, "Chrome Safe Storage", ".x.com", ["auth_token"]
        ) == {"auth_token": "domain-auth"}
        assert chrome_cookies._extract_chromium_cookies_macos(
            db_both, "Chrome Safe Storage", "x.com", ["auth_token"]
        ) == {"auth_token": "host-auth"}

    def test_works_on_legacy_schema_without_top_frame_site_key(self, tmp_path):
        db = tmp_path / "Cookies"
        future = _chrome_utc(3600)
        _make_chromium_db(
            db,
            [
                ("x.com", "auth_token", "host-auth", b"", "/", future, 1, 1, ""),
                ("x.com", "ct0", "host-ct0", b"", "/", future, 1, 1, ""),
            ],
            include_chips=False,
        )
        got = chrome_cookies._extract_chromium_cookies_macos(
            db, "Chrome Safe Storage", ".x.com", ["auth_token", "ct0"]
        )
        assert got == {"auth_token": "host-auth", "ct0": "host-ct0"}


# ===========================================================================
# HIGH-082: Strict CDP URL validation, no proxy/redirect, WS hardening
# ===========================================================================


class TestHigh082ChromeCdpHardening:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "http://169.254.169.254:80",
            "http://evil.example:9222",
            "http://10.0.0.1:18800",
            "http://0.0.0.0:18800",
            "http://localhost.evil.com:18800",
            "ws://evil.example:18800/devtools/page/1",
            "https://127.0.0.1:18800",
            "wss://127.0.0.1:18800/devtools/page/1",
            "file:///etc/passwd",
            "ftp://127.0.0.1:21",
            "data:application/json,{}",
            "http://user:pass@127.0.0.1:18800",
            "http://127.0.0.1:18800#frag",
            "http://127.0.0.1:0",
            "http://127.0.0.1:65536",
            "http://127.0.0.1:99999",
            "http://127.0.0.1",
            "http://127.0.0.1:18800/extra/path",
            "ws://127.0.0.1:18800/devtools/page/1 HTTP/1.1\r\nX-Test: 1",
            "ws://127.0.0.1:18800/devtools/page/1\n",
            "ws://127.0.0.1:18800/devtools/page/1\r",
            "ws://127.0.0.1:18800/devtools/page/\u00e9",
            "ws://127.0.0.1:18800",
        ],
    )
    def test_rejects_unsafe_browser_cdp_urls_without_network_io(self, bad_url):
        with (
            mock.patch("socket.create_connection", side_effect=AssertionError("no socket")),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("no urlopen")),
        ):
            assert chrome_cdp.candidate_endpoints({"BROWSER_CDP_URL": bad_url}) == []
            assert (
                chrome_cdp.read_x_cookies(
                    {"BROWSER_CDP_URL": bad_url, "BROWSER_CONSENT": "true"}
                )
                is None
            )

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("127.0.0.1:18800", "http://127.0.0.1:18800"),
            ("http://localhost:9222/", "http://localhost:9222"),
            ("http://[::1]:18800", "http://[::1]:18800"),
            ("[::1]:18800", "http://[::1]:18800"),
            ("ws://127.0.0.1:18800/devtools/page/ABC", "ws://127.0.0.1:18800/devtools/page/ABC"),
            ("ws://[::1]:18800/devtools/page/ABC", "ws://[::1]:18800/devtools/page/ABC"),
        ],
    )
    def test_accepts_valid_loopback_http_and_ws_urls(self, raw, expected):
        assert chrome_cdp._normalize_base(raw) == expected

    def test_http_get_json_refuses_302_redirects(self):
        target_hits = []

        class TargetHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                target_hits.append(self.path)
                payload = json.dumps({"Browser": "Chrome/120"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        target_srv = http.server.HTTPServer(("127.0.0.1", 0), TargetHandler)
        target_port = target_srv.server_address[1]

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target_port}/json/version")
                self.end_headers()

            def log_message(self, *_args):
                pass

        redir_srv = http.server.HTTPServer(("127.0.0.1", 0), RedirectHandler)
        redir_port = redir_srv.server_address[1]

        t1 = threading.Thread(target=target_srv.serve_forever, daemon=True)
        t2 = threading.Thread(target=redir_srv.serve_forever, daemon=True)
        t1.start()
        t2.start()
        try:
            assert chrome_cdp._http_get_json(f"http://127.0.0.1:{redir_port}/json/version") is None
            assert target_hits == []
        finally:
            redir_srv.shutdown()
            target_srv.shutdown()
            redir_srv.server_close()
            target_srv.server_close()

    def test_http_get_json_bypasses_http_proxy_env(self, monkeypatch):
        proxy_hits = []

        class TrapProxy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                proxy_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"Browser": "FakeProxyChrome"}')

            def log_message(self, *_args):
                pass

        class DirectServer(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = json.dumps({"Browser": "Chrome/Direct"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                pass

        proxy_srv = http.server.HTTPServer(("127.0.0.1", 0), TrapProxy)
        direct_srv = http.server.HTTPServer(("127.0.0.1", 0), DirectServer)
        proxy_port = proxy_srv.server_address[1]
        direct_port = direct_srv.server_address[1]

        monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy_port}")
        monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{proxy_port}")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        t1 = threading.Thread(target=proxy_srv.serve_forever, daemon=True)
        t2 = threading.Thread(target=direct_srv.serve_forever, daemon=True)
        t1.start()
        t2.start()
        try:
            got = chrome_cdp._http_get_json(f"http://127.0.0.1:{direct_port}/json/version")
            assert got == {"Browser": "Chrome/Direct"}
            assert proxy_hits == []
        finally:
            proxy_srv.shutdown()
            direct_srv.shutdown()
            proxy_srv.server_close()
            direct_srv.server_close()

    def test_cdp_opener_omits_file_ftp_data_and_https_handlers(self):
        opener = chrome_cdp._CDP_OPENER
        handler_types = tuple(type(h) for h in opener.handlers)
        for forbidden in (
            urllib.request.FileHandler,
            urllib.request.FTPHandler,
            urllib.request.DataHandler,
            urllib.request.HTTPSHandler,
        ):
            assert forbidden not in handler_types

    @pytest.mark.parametrize(
        "spoofed_ws",
        [
            "ws://169.254.169.254:80/devtools/page/1",
            "ws://127.0.0.1:6379/devtools/page/1",
            "ws://localhost:18800/devtools/page/1",  # host mismatch against 127.0.0.1
            "ws://127.0.0.1:18800/devtools/page/1 HTTP/1.1\r\nHost: evil",
            "ws://127.0.0.1:18800/devtools/page/1\n",
            "wss://127.0.0.1:18800/devtools/page/1",
        ],
    )
    def test_page_ws_url_rejects_cross_endpoint_or_injected_targets(self, spoofed_ws):
        targets = [{"type": "page", "webSocketDebuggerUrl": spoofed_ws}]
        with mock.patch("lib.chrome_cdp._http_get_json", return_value=targets):
            assert chrome_cdp._page_ws_url("http://127.0.0.1:18800") is None

    @pytest.mark.parametrize(
        "malicious_ws",
        [
            "ws://127.0.0.1:18800/a HTTP/1.1\r\nX-Smuggled: 1",
            "ws://127.0.0.1:18800/a\rb",
            "ws://127.0.0.1:18800/a\n",
            "ws://127.0.0.1:99999/devtools/page/1",
            "ws://127.0.0.1:18800/devtools/page/\u00e9",
            "ws://evil.example:18800/devtools/page/1",
            "ws://user@127.0.0.1:18800/devtools/page/1",
            "ws://127.0.0.1:18800/devtools/page/1#frag",
        ],
    )
    def test_wsconn_connect_rejects_injection_overflow_and_non_ascii_without_raising(self, malicious_ws):
        with mock.patch("socket.create_connection", side_effect=AssertionError("must not connect")):
            assert chrome_cdp._WSConn.connect(malicious_ws, 1.0) is None

    def test_wsconn_connect_closes_socket_on_send_error(self):
        fake_sock = mock.MagicMock(spec=socket.socket)
        fake_sock.sendall.side_effect = UnicodeEncodeError("ascii", "x", 0, 1, "boom")
        with mock.patch("socket.create_connection", return_value=fake_sock):
            assert (
                chrome_cdp._WSConn.connect("ws://127.0.0.1:18800/devtools/page/ABC", 1.0) is None
            )
        fake_sock.close.assert_called_once()

    @pytest.mark.parametrize(
        "env_refusal",
        [
            {"FROM_BROWSER": "off"},
            {"BROWSER_CONSENT": "false"},
            {"BROWSER_CONSENT": "unknown"},
        ],
    )
    def test_cookie_access_allowed_honors_env_refusal(self, monkeypatch, env_refusal):
        for k, v in env_refusal.items():
            monkeypatch.setenv(k, v)
        assert chrome_cdp.cookie_access_allowed({"BROWSER_CONSENT": "true"}) is False
        assert chrome_cdp.cookie_access_allowed({"FROM_BROWSER": "chrome"}) is False


# ===========================================================================
# HIGH-083: Owner-only Chrome login profile directory in box_chrome_login.py
# ===========================================================================


class TestHigh083BoxChromeLoginProfileDir:
    def test_default_profile_dir_is_never_fixed_tmp_path(self, tmp_path, monkeypatch):
        assert bcl.DEFAULT_PROFILE_DIR != "/tmp/last30days-x-chrome"
        assert Path(bcl.DEFAULT_PROFILE_DIR).parent != Path("/tmp")

        runtime = tmp_path / "run"
        runtime.mkdir(mode=0o700)
        os.chmod(runtime, 0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        assert bcl._default_profile_dir() == str(runtime / "last30days-x-chrome")

        # World-writable XDG_RUNTIME_DIR (e.g. /tmp) is rejected -> uses safe 0700 XDG_CACHE_HOME.
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/tmp")
        cache = tmp_path / "cache"
        cache.mkdir(mode=0o700)
        os.chmod(cache, 0o700)
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
        assert bcl._default_profile_dir() == str(cache / "last30days" / "x-chrome")

        # Unsafe XDG_CACHE_HOME (e.g. /tmp or loose mode) falls back to ~/.cache.
        monkeypatch.setenv("XDG_CACHE_HOME", "/tmp")
        assert bcl._default_profile_dir() == str(Path.home() / ".cache" / "last30days" / "x-chrome")

    def test_render_recipe_emits_mkdir_m_700(self, tmp_path):
        profile = tmp_path / "x-chrome"
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
        ):
            recipe = bcl.build_recipe({}, profile_dir=str(profile))
        rendered = bcl.render_recipe(recipe)
        assert f"mkdir -m 700 -p {profile}" in rendered
        assert "/tmp/last30days-x-chrome" not in rendered

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode/uid checks")
    def test_exec_creates_0700_dir_and_tightens_loose_owner_dir(self, tmp_path):
        profile = tmp_path / "new-profile"
        popen = mock.MagicMock()
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
            mock.patch.object(bcl.env, "get_config", return_value={}),
            mock.patch.object(bcl, "_default_profile_dir", return_value=str(profile)),
            mock.patch("subprocess.Popen", popen),
        ):
            assert bcl.main(["--exec"]) == 0
        assert stat.S_IMODE(os.lstat(profile).st_mode) == 0o700

        # Existing owner-owned directory with 0755 gets tightened to 0700.
        os.chmod(profile, 0o755)
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
            mock.patch.object(bcl.env, "get_config", return_value={}),
            mock.patch.object(bcl, "_default_profile_dir", return_value=str(profile)),
            mock.patch("subprocess.Popen", popen),
        ):
            assert bcl.main(["--exec"]) == 0
        assert stat.S_IMODE(os.lstat(profile).st_mode) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink/uid checks")
    def test_exec_aborts_nonzero_on_symlink_profile_dir(self, tmp_path):
        real_dir = tmp_path / "attacker"
        real_dir.mkdir(mode=0o777)
        link = tmp_path / "symlink-profile"
        link.symlink_to(real_dir)
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
            mock.patch.object(bcl.env, "get_config", return_value={}),
            mock.patch.object(bcl, "_default_profile_dir", return_value=str(link)),
            mock.patch("subprocess.Popen", side_effect=AssertionError("must not spawn on symlink")),
        ):
            assert bcl.main(["--exec"]) == 1

    @pytest.mark.skipif(os.name == "nt", reason="POSIX uid checks")
    def test_exec_aborts_nonzero_on_foreign_owner_profile_dir(self, tmp_path):
        profile = tmp_path / "foreign-profile"
        profile.mkdir(mode=0o700)
        fake_stat = mock.MagicMock()
        fake_stat.st_mode = stat.S_IFDIR | 0o700
        fake_stat.st_uid = os.getuid() + 1
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
            mock.patch.object(bcl.env, "get_config", return_value={}),
            mock.patch.object(bcl, "_default_profile_dir", return_value=str(profile)),
            mock.patch("os.lstat", return_value=fake_stat),
            mock.patch("subprocess.Popen", side_effect=AssertionError("must not spawn on foreign uid")),
        ):
            assert bcl.main(["--exec"]) == 1

    def test_exec_aborts_nonzero_when_makedirs_fails(self, tmp_path):
        profile = tmp_path / "uncreatable"
        with (
            mock.patch("lib.env.x_extras_enabled", return_value=True),
            mock.patch("shutil.which", return_value="/usr/local/bin/box-chrome"),
            mock.patch.object(bcl.env, "get_config", return_value={}),
            mock.patch.object(bcl, "_default_profile_dir", return_value=str(profile)),
            mock.patch("os.makedirs", side_effect=PermissionError("read-only")),
            mock.patch("subprocess.Popen", side_effect=AssertionError("must not spawn on OSError")),
        ):
            assert bcl.main(["--exec"]) == 1


# ===========================================================================
# HIGH-084: Symmetric consent resolution & FROM_BROWSER pin in agentcookie
# ===========================================================================


class TestHigh084AgentcookieConsentGate:
    @pytest.mark.parametrize(
        "env_vars,cfg",
        [
            ({"FROM_BROWSER": "off", "AGENTCOOKIE": "on"}, {}),
            ({"BROWSER_CONSENT": "false", "AGENTCOOKIE": "on"}, {}),
            ({"BROWSER_CONSENT": "unknown", "AGENTCOOKIE": "on"}, {}),
            ({"FROM_BROWSER": "off"}, {"AGENTCOOKIE": "on"}),
            ({"BROWSER_CONSENT": "false"}, {"AGENTCOOKIE": "on"}),
            ({"BROWSER_CONSENT": "false"}, {"BROWSER_CONSENT": "true"}),
            ({"AGENTCOOKIE": "off"}, {"AGENTCOOKIE": "on", "BROWSER_CONSENT": "true"}),
            ({"AGENTCOOKIE": "on"}, {"FROM_BROWSER": "off"}),
            ({"AGENTCOOKIE": "on"}, {"BROWSER_CONSENT": "false"}),
        ],
    )
    def test_refusal_in_either_env_or_config_always_vetoes(self, monkeypatch, env_vars, cfg):
        for k, v in env_vars.items():
            monkeypatch.setenv(k, v)
        with (
            mock.patch("shutil.which", return_value="/mock/agentcookie"),
            mock.patch("subprocess.run", side_effect=AssertionError("refused sidecar must not run")),
        ):
            assert agentcookie.is_disabled(cfg) is True
            assert agentcookie.is_available(cfg) is False
            assert agentcookie.read_x_cookies(cfg) is None

    @pytest.mark.parametrize(
        "cfg",
        [
            {"FROM_BROWSER": "firefox", "BROWSER_CONSENT": "true"},
            {"FROM_BROWSER": "chrome", "BROWSER_CONSENT": "true"},
            {"FROM_BROWSER": "firefox,safari", "BROWSER_CONSENT": "true"},
        ],
    )
    @pytest.mark.parametrize("via_env", [False, True])
    def test_explicit_browser_pin_does_not_auto_enable_sidecar(self, monkeypatch, cfg, via_env):
        if via_env:
            for k, v in cfg.items():
                monkeypatch.setenv(k, v)
            passed = None
        else:
            passed = cfg
        with (
            mock.patch("shutil.which", return_value="/mock/agentcookie"),
            mock.patch("subprocess.run", side_effect=AssertionError("pinned browser must not invoke sidecar")),
        ):
            assert agentcookie.is_disabled(passed) is True
            assert agentcookie.is_available(passed) is False
            assert agentcookie.read_x_cookies(passed) is None

    def test_env_browser_pin_blocks_sidecar_even_with_partial_config(self, monkeypatch):
        monkeypatch.setenv("FROM_BROWSER", "firefox")
        assert agentcookie.is_disabled({"BROWSER_CONSENT": "true"}) is True

    def test_explicit_agentcookie_on_overrides_browser_pin_without_refusal(self):
        cfg = {"FROM_BROWSER": "firefox", "BROWSER_CONSENT": "true", "AGENTCOOKIE": "on"}
        assert agentcookie.is_disabled(cfg) is False

    def test_auto_or_unset_from_browser_with_consent_still_allows_sidecar(self):
        assert agentcookie.is_disabled({"BROWSER_CONSENT": "true"}) is False
        assert agentcookie.is_disabled({"FROM_BROWSER": "auto", "BROWSER_CONSENT": "true"}) is False
