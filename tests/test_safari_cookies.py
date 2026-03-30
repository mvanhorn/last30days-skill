"""Tests for Safari binary cookie extraction."""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the internal parser directly for testability (avoids platform check)
from scripts.lib.safari_cookies import (
    _parse_binary_cookies,
    extract_safari_cookies_macos,
)


def _build_cookie_record(url: str, name: str, value: str, path: str = "/") -> bytes:
    """Build a single binary cookie record."""
    # Fixed header: size(4) + flags(4) + padding(8) + url_off(4) + name_off(4) + path_off(4) + value_off(4) + comment(8) + expiry(8) + creation(8)
    # Total fixed = 4 + 4 + 8 + 4 + 4 + 4 + 4 + 8 + 8 + 8 = 56 bytes
    # String data starts at offset 56

    url_b = url.encode("utf-8") + b"\x00"
    name_b = name.encode("utf-8") + b"\x00"
    path_b = path.encode("utf-8") + b"\x00"
    value_b = value.encode("utf-8") + b"\x00"

    str_offset_base = 56
    url_offset = str_offset_base
    name_offset = url_offset + len(url_b)
    path_offset = name_offset + len(name_b)
    value_offset = path_offset + len(path_b)

    total_size = value_offset + len(value_b)

    record = struct.pack("<I", total_size)  # size
    record += struct.pack("<I", 0)  # flags
    record += b"\x00" * 8  # padding/unknown
    record += struct.pack("<I", url_offset)  # url offset
    record += struct.pack("<I", name_offset)  # name offset
    record += struct.pack("<I", path_offset)  # path offset
    record += struct.pack("<I", value_offset)  # value offset
    record += b"\x00" * 8  # comment (unused)
    record += struct.pack("<d", 700000000.0)  # expiry (Mac epoch)
    record += struct.pack("<d", 690000000.0)  # creation (Mac epoch)
    record += url_b + name_b + path_b + value_b

    return record


def _build_page(cookie_records: list[bytes]) -> bytes:
    """Build a binary cookies page from a list of cookie records."""
    num_cookies = len(cookie_records)

    # Page header: 4-byte marker + 4-byte cookie count + offset array
    header_size = 4 + 4 + num_cookies * 4
    # Also add 4 bytes for end-of-page marker
    offsets_start = header_size

    # Calculate offsets for each cookie record
    offsets = []
    current_offset = offsets_start
    for rec in cookie_records:
        offsets.append(current_offset)
        current_offset += len(rec)

    page = b"\x00\x00\x01\x00"  # page header marker
    page += struct.pack("<I", num_cookies)
    for off in offsets:
        page += struct.pack("<I", off)
    for rec in cookie_records:
        page += rec

    return page


def _build_binary_cookies_file(pages: list[bytes]) -> bytes:
    """Build a complete Cookies.binarycookies file from pages."""
    num_pages = len(pages)

    data = b"cook"  # magic
    data += struct.pack(">I", num_pages)  # page count (big-endian)

    # Page sizes (big-endian)
    for page in pages:
        data += struct.pack(">I", len(page))

    # Page data
    for page in pages:
        data += page

    return data


@pytest.fixture
def x_cookies_file() -> bytes:
    """Build a minimal valid binary cookies file with .x.com cookies."""
    rec1 = _build_cookie_record(".x.com", "auth_token", "test_auth_abc123")
    rec2 = _build_cookie_record(".x.com", "ct0", "test_ct0_xyz789")
    rec3 = _build_cookie_record(".google.com", "NID", "google_nid_value")
    page = _build_page([rec1, rec2, rec3])
    return _build_binary_cookies_file([page])


class TestParseValidCookies:
    def test_extracts_matching_cookies(self, x_cookies_file: bytes):
        result = _parse_binary_cookies(x_cookies_file, "x.com", ["auth_token", "ct0"])
        assert result is not None
        assert result["auth_token"] == "test_auth_abc123"
        assert result["ct0"] == "test_ct0_xyz789"

    def test_ignores_other_domains(self, x_cookies_file: bytes):
        result = _parse_binary_cookies(x_cookies_file, "google.com", ["auth_token"])
        assert result is None

    def test_partial_match_returns_found_only(self, x_cookies_file: bytes):
        result = _parse_binary_cookies(
            x_cookies_file, "x.com", ["auth_token", "nonexistent"]
        )
        assert result is not None
        assert result["auth_token"] == "test_auth_abc123"
        assert "nonexistent" not in result

    def test_no_matching_cookie_names(self, x_cookies_file: bytes):
        result = _parse_binary_cookies(x_cookies_file, "x.com", ["bogus"])
        assert result is None

    def test_domain_substring_match_with_leading_dot(self, x_cookies_file: bytes):
        """Domain '.x.com' in cookie should match search for 'x.com'."""
        result = _parse_binary_cookies(x_cookies_file, "x.com", ["auth_token"])
        assert result is not None
        assert result["auth_token"] == "test_auth_abc123"


class TestMultiplePages:
    def test_cookies_across_pages(self):
        rec1 = _build_cookie_record(".x.com", "auth_token", "page1_auth")
        rec2 = _build_cookie_record(".x.com", "ct0", "page2_ct0")
        page1 = _build_page([rec1])
        page2 = _build_page([rec2])
        data = _build_binary_cookies_file([page1, page2])

        result = _parse_binary_cookies(data, "x.com", ["auth_token", "ct0"])
        assert result is not None
        assert result["auth_token"] == "page1_auth"
        assert result["ct0"] == "page2_ct0"


class TestErrorPaths:
    def test_file_not_found(self, tmp_path: Path):
        with patch(
            "scripts.lib.safari_cookies.Path.home", return_value=tmp_path
        ), patch("scripts.lib.safari_cookies.sys") as mock_sys:
            mock_sys.platform = "darwin"
            mock_sys.stderr = sys.stderr
            result = extract_safari_cookies_macos("x.com", ["auth_token"])
        assert result is None

    def test_permission_denied(self, tmp_path: Path, capsys):
        cookie_dir = tmp_path / "Library" / "Cookies"
        cookie_dir.mkdir(parents=True)
        cookie_file = cookie_dir / "Cookies.binarycookies"
        cookie_file.write_bytes(b"cook")
        cookie_file.chmod(0o000)

        try:
            with patch(
                "scripts.lib.safari_cookies.Path.home", return_value=tmp_path
            ), patch("scripts.lib.safari_cookies.sys") as mock_sys:
                mock_sys.platform = "darwin"
                mock_sys.stderr = sys.stderr
                result = extract_safari_cookies_macos("x.com", ["auth_token"])
            assert result is None
            captured = capsys.readouterr()
            assert "Full Disk Access" in captured.err
        finally:
            cookie_file.chmod(0o644)

    def test_truncated_magic_only(self):
        result = _parse_binary_cookies(b"cook", "x.com", ["auth_token"])
        assert result is None

    def test_empty_file(self):
        result = _parse_binary_cookies(b"", "x.com", ["auth_token"])
        assert result is None

    def test_wrong_magic(self):
        result = _parse_binary_cookies(b"notcook!", "x.com", ["auth_token"])
        assert result is None

    def test_truncated_page_sizes(self):
        # Header says 5 pages but data is too short
        data = b"cook" + struct.pack(">I", 5) + b"\x00" * 4
        result = _parse_binary_cookies(data, "x.com", ["auth_token"])
        assert result is None

    def test_truncated_page_data(self):
        # Valid header with 1 page of size 1000, but no actual page data
        data = b"cook" + struct.pack(">I", 1) + struct.pack(">I", 1000)
        result = _parse_binary_cookies(data, "x.com", ["auth_token"])
        assert result is None

    def test_non_darwin_returns_none(self):
        with patch("scripts.lib.safari_cookies.sys") as mock_sys:
            mock_sys.platform = "linux"
            result = extract_safari_cookies_macos("x.com", ["auth_token"])
        assert result is None

    def test_garbage_data_no_crash(self):
        """Random bytes after valid magic should not crash."""
        import os

        data = b"cook" + os.urandom(200)
        # Should not raise — may return None or a dict
        result = _parse_binary_cookies(data, "x.com", ["auth_token"])
        # Just verify no exception; result is either None or dict
        assert result is None or isinstance(result, dict)
