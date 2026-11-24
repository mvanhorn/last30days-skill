"""Security and correctness regression tests for PR 15.

Covers:
- HIGH-177: Randomized CSPRNG multipart boundary, collision resistance,
  sanitized filename header, and FFmpeg metadata stripping in transcribe.py.
- HIGH-183: Auth token header normalization, CR/LF/NUL rejection, and secret
  scrubbing in non-HTTPError exceptions across truthsocial, threads, pinterest,
  linkedin, telegram, and bluesky (plus unmocked putheader ValueError checks).
- HIGH-184: Xiaohongshu XIAOHONGSHU_API_BASE validation (blocking file://,
  ftp://, gopher://, cloud metadata IPs/hosts, cleartext remote HTTP, userinfo,
  queries/fragments, and off-origin redirects while preserving local/Docker/LAN
  self-hosted bridges and remote HTTPS bridges).
- HIGH-185: LinkedIn author_url / profile_url validation in parse_linkedin_response,
  _best_author_match, search_profile, enrich_articles, and read_profile.
- HIGH-186: Xiaohongshu null / non-dict "data" payload handling without crashes.
- HIGH-189: GitHub literal .git suffix removal (not rstrip(".git")) across
  extract_repo_refs, enrich_candidates_with_stars, apply_star_map, and refetch_datum.
- HIGH-190: GitHub negated search qualifiers (-is:public, -created:..., -org:...,
  -repo:..., etc.), missing GitHub qualifier keys, wrapper smuggling, and
  unbalanced/escaped quote sanitization.
"""

from __future__ import annotations

import http.client as http_client
import io
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from lib import (
    auth_safety,
    bluesky,
    env,
    github,
    http,
    linkedin,
    pinterest,
    public_host,
    telegram,
    threads,
    transcribe,
    truthsocial,
    xiaohongshu_api,
)


# ---------------------------------------------------------------------------
# HIGH-177: transcribe.py multipart boundary & filename sanitization
# ---------------------------------------------------------------------------


class _FakeWhisperResp:
    def __init__(self, text: str = "transcribed ok") -> None:
        self._payload = json.dumps({"text": text}).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


class TestHigh177TranscribeMultipartBoundary:
    def test_boundary_is_randomized_per_request_and_not_hardcoded(self, tmp_path: Path) -> None:
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"ID3\x03\x00audio-bytes")
        captured_requests: list[urllib.request.Request] = []

        def fake_open(req, timeout):
            captured_requests.append(req)
            return _FakeWhisperResp()

        with mock.patch.object(transcribe.http, "open_request", side_effect=fake_open):
            assert transcribe._post_audio("groq", str(audio), "dummy-groq-key", 10.0) == "transcribed ok"
            assert transcribe._post_audio("groq", str(audio), "dummy-groq-key", 10.0) == "transcribed ok"

        assert len(captured_requests) == 2
        boundaries = []
        for req in captured_requests:
            ct = req.get_header("Content-type") or ""
            m = re.match(r"^multipart/form-data; boundary=(----l30d-[0-9a-f]{32})$", ct)
            assert m is not None, f"unexpected Content-Type: {ct!r}"
            boundary = m.group(1)
            assert "l30dTranscribeBoundary" not in ct
            assert b"l30dTranscribeBoundary" not in req.data
            assert f"--{boundary}\r\n".encode("ascii") in req.data
            assert f"\r\n--{boundary}--\r\n".encode("ascii") in req.data
            boundaries.append(boundary)
        assert boundaries[0] != boundaries[1]

    def test_old_static_boundary_inside_audio_cannot_inject_form_fields(self, tmp_path: Path) -> None:
        injected_payload = (
            b"ID3\x03\x00"
            b"\r\n------l30dTranscribeBoundary\r\n"
            b'Content-Disposition: form-data; name="prompt"\r\n\r\n'
            b"INJECTED_PROMPT\r\n"
            b"------l30dTranscribeBoundary--\r\n"
        )
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(injected_payload)
        captured: list[urllib.request.Request] = []

        def fake_open(req, timeout):
            captured.append(req)
            return _FakeWhisperResp()

        with mock.patch.object(transcribe.http, "open_request", side_effect=fake_open):
            transcribe._post_audio("openai", str(audio), "dummy-openai-key", 10.0)

        req = captured[0]
        ct = req.get_header("Content-type") or ""
        boundary = ct.split("boundary=", 1)[1]
        parts = req.data.split(f"--{boundary}".encode("ascii"))
        # Expect: [b"", model_part, file_part, b"--\r\n"] -> exactly 2 form fields.
        assert len(parts) == 4
        assert b'name="model"' in parts[1]
        assert b'name="file"' in parts[2]
        assert injected_payload in parts[2]

    def test_boundary_regenerates_on_collision_with_audio_bytes(self, tmp_path: Path) -> None:
        colliding_hex = "a" * 32
        clean_hex = "b" * 32
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(f"prefix----l30d-{colliding_hex}suffix".encode("ascii"))
        captured: list[urllib.request.Request] = []

        def fake_open(req, timeout):
            captured.append(req)
            return _FakeWhisperResp()

        with mock.patch.object(transcribe.secrets, "token_hex", side_effect=[colliding_hex, clean_hex]), \
                mock.patch.object(transcribe.http, "open_request", side_effect=fake_open):
            transcribe._post_audio("groq", str(audio), "dummy-groq-key", 10.0)

        ct = captured[0].get_header("Content-type") or ""
        assert ct == f"multipart/form-data; boundary=----l30d-{clean_hex}"

    def test_boundary_collision_exhaustion_raises_cleanly(self, tmp_path: Path) -> None:
        stuck_hex = "c" * 32
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(f"----l30d-{stuck_hex}".encode("ascii"))

        with mock.patch.object(transcribe.secrets, "token_hex", return_value=stuck_hex):
            with pytest.raises(ValueError, match="collision-free multipart boundary"):
                transcribe._post_audio("groq", str(audio), "dummy-groq-key", 10.0)

    @pytest.mark.parametrize(
        ("filename", "expected_header_name"),
        [
            ("audio.mp3", "audio.mp3"),
            ("chunk_001.mp3", "chunk_001.mp3"),
            ('evil"; name="prompt.mp3', "audio.mp3"),
            ("evil\r\nInjected: 1.mp3", "audio.mp3"),
            ("evil\nInjected: 1.mp3", "audio.mp3"),
            ("weird name.wav", "audio.mp3"),
            ("..hidden.mp3", "audio.mp3"),
        ],
    )
    def test_filename_header_is_strictly_sanitized(
        self, filename: str, expected_header_name: str,
    ) -> None:
        fake_path = f"/virtual/work/{filename}"
        assert transcribe._safe_upload_filename(fake_path) == expected_header_name
        captured: list[urllib.request.Request] = []

        def fake_open(req, timeout):
            captured.append(req)
            return _FakeWhisperResp()

        # Patch open() instead of touching disk so Windows-illegal characters
        # like quotes, colons, and CR/LF stay portable across OSes.
        with mock.patch("builtins.open", return_value=io.BytesIO(b"audio-data")), \
                mock.patch.object(transcribe.http, "open_request", side_effect=fake_open):
            transcribe._post_audio("groq", fake_path, "dummy-groq-key", 10.0)

        expected = (
            f'Content-Disposition: form-data; name="file"; filename="{expected_header_name}"\r\n'
        ).encode("ascii")
        assert expected in captured[0].data

    def test_chunk_audio_and_acquire_audio_strip_ffmpeg_metadata(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(transcribe, "MAX_UPLOAD_BYTES", 4)
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"ID3\x03\x000123456789")
        workdir = tmp_path / "w"
        workdir.mkdir()
        cmds: list[list[str]] = []

        def fake_run(cmd, timeout=120.0, *, env=None):
            cmds.append(cmd)
            if "-segment_time" in cmd:
                (tmp_path / "chunk_000.mp3").write_bytes(b"0123")
            else:
                Path(cmd[-1]).write_bytes(b"mp3")
            return True

        with mock.patch.object(transcribe, "_run", side_effect=fake_run):
            acquired = transcribe._acquire_audio(str(audio), str(workdir), timeout=10.0)
            chunks = transcribe._chunk_audio(str(audio), str(tmp_path))

        assert acquired is not None
        assert chunks
        assert len(cmds) == 2
        acquire_cmd, chunk_cmd = cmds
        assert acquire_cmd[acquire_cmd.index("-map_metadata") + 1] == "-1"
        assert chunk_cmd[chunk_cmd.index("-map_metadata") + 1] == "-1"

    def test_transcribe_post_audio_rejects_malformed_key_and_scrubs_debug_log(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"audio")
        bad_key = "gsk_super_secret_987654\r\nX-Evil: 1"
        debug_lines: list[str] = []
        monkeypatch.setattr(transcribe.log, "debug", lambda msg: debug_lines.append(msg))

        with mock.patch.object(transcribe.http, "open_request") as open_mock:
            text, provider = transcribe._transcribe_chunk(str(audio), [("groq", bad_key)], timeout=5.0)

        assert text is None
        assert provider == ""
        open_mock.assert_not_called()
        combined = "\n".join(debug_lines)
        assert "gsk_super_secret_987654" not in combined
        assert "X-Evil" not in combined


# ---------------------------------------------------------------------------
# HIGH-183: Token stripping, control-char rejection, & secret scrubbing
# ---------------------------------------------------------------------------


class TestHigh183TokenValidationAndSecretScrubbing:
    def test_auth_safety_normalize_and_scrub_helpers(self) -> None:
        assert auth_safety.normalize_header_token("  tok_valid_123\r\n") == ("tok_valid_123", None)
        assert auth_safety.normalize_header_token("   \n\t ") == (None, None)
        assert auth_safety.normalize_header_token("") == (None, None)
        assert auth_safety.normalize_header_token(None) == (None, None)

        for bad in (
            "tok_a\r\nX-Injected: 1",
            "tok_a\ntok_b",
            "tok_a\x00tail",
            "tok_a\x0btail",
            "tok_a\x7f",
            "tok_non_ascii_\u2603",
            12345,
        ):
            cleaned, err = auth_safety.normalize_header_token(bad)
            assert cleaned is None
            assert err == "Invalid token format"

        # putheader ValueError embeds repr(b'Bearer <secret>\n') — verify full redaction.
        secret = "tok_secret_value_abc123"
        putheader_msg = f"Invalid header value b'Bearer {secret}\\r\\nX-Evil: 1'"
        scrubbed = auth_safety.scrub_secrets(putheader_msg, (f"{secret}\r\nX-Evil: 1",))
        assert secret not in scrubbed
        assert "X-Evil" not in scrubbed
        assert "<redacted>" in scrubbed

        # Also redact short 2-char secrets inside putheader ValueError and Bearer strings.
        short_err = auth_safety.scrub_secrets("Invalid header value b'Bearer xy\\n'", ("xy\n",))
        assert "xy" not in short_err
        assert "<redacted>" in short_err

        # Preserve benign non-secret diagnostics.
        assert auth_safety.scrub_secrets("connection refused", ("pw",)) == "connection refused"

    @pytest.mark.parametrize(
        "malformed_token",
        [
            "SECRET_TOK_12345\r\nX-Injected: 1",
            "SECRET_TOK_12345\nsecond-line",
            "SECRET_TOK_12345\x00nullbyte",
        ],
    )
    def test_all_six_modules_reject_malformed_tokens_without_network_or_leak(
        self, malformed_token: str, capsys,
    ) -> None:
        bluesky._reset_session_cache()
        try:
            with mock.patch.object(http, "request") as req_mock, \
                    mock.patch.object(http, "get") as get_mock:
                # 1. Truth Social
                ts_res = truthsocial.search_truthsocial(
                    "tariffs", "2026-06-01", "2026-06-30",
                    config={"TRUTHSOCIAL_TOKEN": malformed_token},
                )
                assert ts_res["statuses"] == []
                assert ts_res["error"] == "Invalid token format"

                # 2. Threads
                th_res = threads.search_threads(
                    "ai agents", "2026-06-01", "2026-06-30", token=malformed_token,
                )
                assert th_res["items"] == []
                assert th_res["error"] == "Invalid token format"

                # 3. Pinterest
                pi_res = pinterest.search_pinterest(
                    "ai agents", "2026-06-01", "2026-06-30", token=malformed_token,
                )
                assert pi_res["items"] == []
                assert pi_res["error"] == "Invalid token format"

                # 4. LinkedIn (search_linkedin, search_profile, read_profile, enrich_articles)
                li_res = linkedin.search_linkedin(
                    "ai agents", "2026-06-01", "2026-06-30", token=malformed_token,
                )
                assert li_res["posts"] == []
                assert li_res["error"] == "Invalid token format"
                assert linkedin.search_profile("https://www.linkedin.com/in/janedoe", malformed_token) == {}
                rp_res = linkedin.read_profile("https://www.linkedin.com/in/janedoe", malformed_token)
                assert rp_res["status"] == "failed"
                assert rp_res["detail"] == "Invalid token format"

                # 5. Telegram (search_telegram and direct _fetch_channel_posts)
                tg_res = telegram.search_telegram(
                    "ai agents", "2026-06-01", "2026-06-30",
                    token=malformed_token, config={"TELEGRAM_SOURCES": "aipost"},
                )
                assert tg_res["items"] == []
                assert tg_res["error"] == "Invalid token format"
                assert telegram._fetch_channel_posts(
                    "aipost", malformed_token, from_date="2026-06-01", topic="ai", max_pages=1,
                ) == []

                # 6. Bluesky (search with malformed session JWT + refresh JWT)
                assert bluesky._refresh_session(malformed_token) is None
                assert "SECRET_TOK_12345" not in (bluesky._session_error or "")
                with mock.patch.object(bluesky, "_create_session", return_value=malformed_token):
                    bs_res = bluesky.search_bluesky(
                        "ai agents", "2026-06-01", "2026-06-30",
                        config={"BSKY_HANDLE": "u.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"},
                    )
                assert bs_res["posts"] == []
                assert "Invalid token format" in bs_res["error"]

            req_mock.assert_not_called()
            get_mock.assert_not_called()

            stderr = capsys.readouterr().err
            for output in (
                stderr,
                ts_res["error"],
                th_res["error"],
                pi_res["error"],
                li_res["error"],
                rp_res["detail"],
                tg_res["error"],
                bs_res["error"],
            ):
                assert "SECRET_TOK_12345" not in output
                assert "X-Injected" not in output
        finally:
            bluesky._reset_session_cache()

    def test_trailing_newline_from_devtools_is_stripped_cleanly(self) -> None:
        token_with_nl = "  valid_devtools_secret_999\r\n"

        with mock.patch.object(truthsocial.http, "request", return_value={"statuses": []}) as ts_req:
            truthsocial.search_truthsocial("ai", "2026-06-01", "2026-06-30", config={"TRUTHSOCIAL_TOKEN": token_with_nl})
        assert ts_req.call_args.kwargs["headers"]["Authorization"] == "Bearer valid_devtools_secret_999"

        with mock.patch.object(threads.http, "get", return_value={"items": []}) as th_get:
            threads.search_threads("ai", "2026-06-01", "2026-06-30", token=token_with_nl)
        assert th_get.call_args.kwargs["headers"]["x-api-key"] == "valid_devtools_secret_999"

        with mock.patch.object(pinterest.http, "get", return_value={"pins": []}) as pi_get:
            pinterest.search_pinterest("ai", "2026-06-01", "2026-06-30", token=token_with_nl)
        assert pi_get.call_args.kwargs["headers"]["x-api-key"] == "valid_devtools_secret_999"

        with mock.patch.object(linkedin.http, "get", return_value={"posts": []}) as li_get:
            linkedin.search_linkedin("ai", "2026-06-01", "2026-06-30", token=token_with_nl)
        assert li_get.call_args.kwargs["headers"]["x-api-key"] == "valid_devtools_secret_999"

        with mock.patch.object(telegram.http, "get", return_value={"success": True, "posts": []}) as tg_get:
            telegram.search_telegram(
                "ai", "2026-06-01", "2026-06-30",
                token=token_with_nl, config={"TELEGRAM_SOURCES": "aipost"},
            )
        assert tg_get.call_args.kwargs["headers"]["x-api-key"] == "valid_devtools_secret_999"

        bluesky._reset_session_cache()
        try:
            with mock.patch.object(
                bluesky.http,
                "request",
                side_effect=[{"accessJwt": token_with_nl, "refreshJwt": "ref_ok"}, {"posts": []}],
            ) as bs_req:
                bluesky.search_bluesky(
                    "ai", "2026-06-01", "2026-06-30",
                    config={"BSKY_HANDLE": "u.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"},
                )
            assert bs_req.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer valid_devtools_secret_999"
        finally:
            bluesky._reset_session_cache()

    def test_unredacted_putheader_valueerror_from_transport_is_scrubbed_everywhere(self, capsys) -> None:
        """Even if a lower layer raises a ValueError containing the secret, none of the
        six modules may leak it to stderr or the returned error dict."""
        secret = "LIVE_SECRET_TOKEN_XYZ789"
        leaking_exc = ValueError(f"Invalid header value b'Bearer {secret}\\n'")

        bluesky._reset_session_cache()
        try:
            with mock.patch.object(truthsocial.http, "request", side_effect=leaking_exc):
                ts = truthsocial.search_truthsocial("ai", "2026-06-01", "2026-06-30", config={"TRUTHSOCIAL_TOKEN": secret})
            with mock.patch.object(threads.http, "get", side_effect=leaking_exc):
                th = threads.search_threads("ai", "2026-06-01", "2026-06-30", token=secret)
            with mock.patch.object(pinterest.http, "get", side_effect=leaking_exc):
                pi = pinterest.search_pinterest("ai", "2026-06-01", "2026-06-30", token=secret)
            with mock.patch.object(linkedin.http, "get", side_effect=leaking_exc):
                li = linkedin.search_linkedin("ai", "2026-06-01", "2026-06-30", token=secret)
                lip = linkedin.search_profile("https://www.linkedin.com/in/janedoe", secret)
            with mock.patch.object(telegram.http, "get", side_effect=leaking_exc):
                # Must catch non-HTTPError ValueError cleanly instead of crashing out of _fetch_channel_posts.
                tg = telegram.search_telegram(
                    "ai", "2026-06-01", "2026-06-30",
                    token=secret, config={"TELEGRAM_SOURCES": "aipost"},
                )
            with mock.patch.object(bluesky, "_create_session", return_value=secret), \
                    mock.patch.object(bluesky.http, "request", side_effect=leaking_exc):
                bs = bluesky.search_bluesky(
                    "ai", "2026-06-01", "2026-06-30",
                    config={"BSKY_HANDLE": "u.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"},
                )

            assert lip == {}
            assert tg["items"] == []
            assert "error" in tg
            stderr = capsys.readouterr().err
            for surface in (stderr, ts["error"], th["error"], pi["error"], li["error"], tg["error"], bs["error"]):
                assert secret not in surface
                assert "<redacted>" in surface
        finally:
            bluesky._reset_session_cache()

    @pytest.mark.parametrize("status_code", [401, 500])
    def test_http_request_scrubs_echoed_api_key_in_error_reason_and_body_before_recording(
        self, status_code: int, capsys,
    ) -> None:
        secret = "SC_LIVE_ECHO_SECRET_987654"
        url = "https://api.scrapecreators.com/v1/telegram/channel/posts?handle=aipost"

        def fake_urlopen(req, timeout=None):
            err_body = io.BytesIO(f'{{"error": "rejected x-api-key: {secret}"}}'.encode("utf-8"))
            raise urllib.error.HTTPError(
                url,
                status_code,
                f"Rejected key {secret}",
                hdrs={},
                fp=err_body,
            )

        with http.capture_failures() as failures, \
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                mock.patch.object(http.time, "sleep"):
            tg = telegram.search_telegram(
                "ai agents",
                "2026-06-01",
                "2026-06-30",
                token=secret,
                config={"TELEGRAM_SOURCES": "aipost"},
            )

        assert failures, "expected http.capture_failures to record the terminal HTTP error"
        recorded = failures[0]
        assert recorded.status_code == status_code
        assert str(status_code) in str(recorded)
        assert secret not in str(recorded)
        assert "<redacted>" in str(recorded)
        assert recorded.body is not None
        assert secret not in recorded.body
        assert "<redacted>" in recorded.body

        assert tg["items"] == []
        assert "error" in tg
        assert str(status_code) in tg["error"]
        assert secret not in tg["error"]
        assert "<redacted>" in tg["error"]
        assert secret not in capsys.readouterr().err

    def test_telegram_non_http_error_sets_error_and_keeps_fetching_other_channels(self) -> None:
        # All channels fail with a non-HTTPError (e.g. invalid UTF-8 decode).
        decode_err = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        with mock.patch.object(telegram.http, "get", side_effect=decode_err):
            all_failed = telegram.search_telegram(
                "ai agents",
                "2026-06-01",
                "2026-06-30",
                token="valid-sc-key",
                config={"TELEGRAM_SOURCES": "broken_chan"},
            )
        assert all_failed["items"] == []
        assert "error" in all_failed
        assert "UnicodeDecodeError" in all_failed["error"]
        assert "@broken_chan" in all_failed["error"]

        # First channel hits UnicodeDecodeError, second channel succeeds -> partial result.
        def mixed_get(url, **kwargs):
            handle = (kwargs.get("params") or {}).get("handle")
            if handle == "broken_chan":
                raise decode_err
            return {
                "success": True,
                "channel": {"handle": "good_chan", "name": "Good", "subscriber_count": 100},
                "posts": [{
                    "id": "1",
                    "text": "AI agents shipped",
                    "published_at": "2026-06-15T10:00:00Z",
                    "view_count": 50,
                    "reaction_count": 5,
                }],
                "has_more": False,
            }

        with mock.patch.object(telegram.http, "get", side_effect=mixed_get):
            partial = telegram.search_telegram(
                "ai agents",
                "2026-06-01",
                "2026-06-30",
                token="valid-sc-key",
                config={"TELEGRAM_SOURCES": "broken_chan,good_chan"},
            )
        assert len(partial["items"]) == 1
        assert partial["items"][0]["handle"] == "good_chan"
        assert partial.get("partial") is True
        assert "UnicodeDecodeError" in partial.get("error", "")

    def test_real_http_client_putheader_error_shape_is_scrubbed(self) -> None:
        """Exercise Python's real http.client.HTTPConnection.putheader ValueError."""
        conn = http_client.HTTPConnection("example.invalid")
        conn.putrequest("GET", "/")
        secret = "REAL_PUTHEADER_SECRET_999"
        with pytest.raises(ValueError) as exc_info:
            conn.putheader("Authorization", f"Bearer {secret}\n")
        assert secret in str(exc_info.value)  # Confirm stdlib leaks it raw.
        scrubbed = auth_safety.scrub_secrets(str(exc_info.value), (f"{secret}\n",))
        assert secret not in scrubbed
        assert "<redacted>" in scrubbed


# ---------------------------------------------------------------------------
# HIGH-184 & HIGH-186: xiaohongshu_api.py SSRF / base URL & null-data crashes
# ---------------------------------------------------------------------------


class TestHigh184XiaohongshuBaseUrlValidation:
    @pytest.mark.parametrize(
        "bad_base",
        [
            "",
            "   ",
            None,
            "file:///etc/passwd",
            "file://localhost/etc/passwd",
            "ftp://localhost:21/xhs",
            "gopher://127.0.0.1:6379/_INFO",
            "data:application/json,{}",
            "javascript:alert(1)",
            "http://169.254.169.254",
            "https://169.254.169.254",
            "http://169.254.170.2",
            "http://2852039166",
            "http://0xa9fea9fe",
            "http://0251.0376.0251.0376",
            "http://[::ffff:169.254.169.254]:80",
            "http://[64:ff9b::169.254.169.254]",
            "http://[fe80::1]:18060",
            "http://[fd00:ec2::254]",
            "http://100.100.100.200",
            "http://0.0.0.0:18060",
            "http://[::]:18060",
            "http://224.0.0.1:18060",
            "http://metadata.google.internal",
            "https://metadata.google.internal",
            "http://metadata",
            "http://instance-data",
            "http://evil.example:18060",
            "http://8.8.8.8:18060",
            "http://localhost:18060?foo=bar",
            "http://localhost:18060#frag",
            "http://user:pass@localhost:18060",
            "http://localhost@evil.example:18060",
            "http://localhost:18060/api/../etc",
            "http://localhost:18060/%2e%2e/etc",
            "http://localhost\\@evil.example:18060",
            "http://localhost:18060\r\nX-Injected: 1",
            "http://localhost:99999",
            "/relative/path",
        ],
    )
    def test_disallowed_base_urls_rejected_before_network(self, bad_base: Any) -> None:
        with mock.patch.object(xiaohongshu_api.http, "get") as get_mock, \
                mock.patch.object(xiaohongshu_api.http, "post") as post_mock:
            with pytest.raises(ValueError):
                xiaohongshu_api.search_feeds("matcha", "2026-06-01", "2026-07-01", bad_base)
        get_mock.assert_not_called()
        post_mock.assert_not_called()

    @pytest.mark.parametrize(
        ("good_base", "expected_local", "expected_normalized"),
        [
            ("http://localhost:18060/", True, "http://localhost:18060"),
            ("http://127.0.0.1:18060", True, "http://127.0.0.1:18060"),
            ("http://[::1]:18060/", True, "http://[::1]:18060"),
            ("http://host.docker.internal:18060", True, "http://host.docker.internal:18060"),
            ("http://custom.local:18060/", True, "http://custom.local:18060"),
            ("http://192.168.1.50:18060", True, "http://192.168.1.50:18060"),
            ("http://10.0.0.12:18060", True, "http://10.0.0.12:18060"),
            ("http://xhs-mcp:18060", True, "http://xhs-mcp:18060"),
            ("https://xhs.example.com/bridge/", False, "https://xhs.example.com/bridge"),
        ],
    )
    def test_legitimate_local_and_https_bridges_allowed_with_correct_guards(
        self, good_base: str, expected_local: bool, expected_normalized: str,
    ) -> None:
        normalized, is_local = xiaohongshu_api._validate_base_url(good_base)
        assert normalized == expected_normalized
        assert is_local is expected_local

        with mock.patch.object(xiaohongshu_api.http, "get", return_value={"data": {"is_logged_in": True}}) as get_mock, \
                mock.patch.object(xiaohongshu_api.http, "post", return_value={"data": {"feeds": []}}) as post_mock:
            items = xiaohongshu_api.search_feeds("matcha", "2026-06-01", "2026-07-01", good_base)

        assert items == []
        for m in (get_mock, post_mock):
            guard = m.call_args.kwargs.get("redirect_guard")
            assert callable(guard)
            assert guard(f"{expected_normalized}/api/v1/login/status") is True
            assert guard("http://169.254.169.254/latest/meta-data/") is False
            assert guard("file:///etc/passwd") is False
            assert guard("https://evil.example/steal") is False
            if expected_local:
                assert m.call_args.kwargs.get("local_bridge_only") is True
                assert "public_only" not in m.call_args.kwargs
            else:
                assert m.call_args.kwargs.get("public_only") is True
                assert "local_bridge_only" not in m.call_args.kwargs

    def test_redirect_to_other_origin_is_blocked_unmocked(self) -> None:
        secret_hits: list[str] = []

        class SecretHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                secret_hits.append(self.path)
                body = b'{"data":{"is_logged_in":true}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        secret_srv = ThreadingHTTPServer(("127.0.0.1", 0), SecretHandler)
        secret_thread = threading.Thread(target=secret_srv.serve_forever, daemon=True)
        secret_thread.start()

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{secret_srv.server_port}/api/v1/login/status")
                self.send_header("Content-Length", "0")
                self.end_headers()

        front_srv = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        front_thread = threading.Thread(target=front_srv.serve_forever, daemon=True)
        front_thread.start()
        try:
            with pytest.raises(http.HTTPError) as exc_info:
                xiaohongshu_api.search_feeds(
                    "matcha", "2026-06-01", "2026-07-01",
                    f"http://127.0.0.1:{front_srv.server_port}",
                )
            assert exc_info.value.status_code == 403
            assert secret_hits == []
        finally:
            front_srv.shutdown()
            front_srv.server_close()
            front_thread.join(2)
            secret_srv.shutdown()
            secret_srv.server_close()
            secret_thread.join(2)

    def test_remote_https_bridge_blocks_private_dns_rebinding(self, monkeypatch) -> None:
        def private_dns(host, port, *_args, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", port))]

        monkeypatch.setattr(public_host.socket, "getaddrinfo", private_dns)
        with pytest.raises(http.HTTPError):
            xiaohongshu_api.search_feeds(
                "matcha", "2026-06-01", "2026-07-01", "https://rebind.example.com",
            )

    @pytest.mark.parametrize(
        "rebound_ips",
        [
            ["169.254.169.254"],
            ["100.100.100.200"],
            ["fd00:ec2::254"],
            ["0.0.0.0"],
            ["93.184.216.34"],
            ["192.168.1.50", "169.254.169.254"],
            ["127.0.0.1", "93.184.216.34"],
        ],
    )
    @pytest.mark.parametrize("bridge_base", ["http://custom.local:18060", "http://xhs-mcp:18060"])
    def test_local_bridge_blocks_connect_time_dns_rebinding_to_disallowed_ips(
        self, monkeypatch, bridge_base: str, rebound_ips: list[str],
    ) -> None:
        def fake_getaddrinfo(host, port, *_args, **_kwargs):
            out = []
            for ip in rebound_ips:
                af = socket.AF_INET6 if ":" in ip else socket.AF_INET
                sockaddr = (ip, port, 0, 0) if af == socket.AF_INET6 else (ip, port)
                out.append((af, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
            return out

        monkeypatch.setattr(http.socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(http.HTTPError):
            xiaohongshu_api.search_feeds("matcha", "2026-06-01", "2026-07-01", bridge_base)
        # env.is_xiaohongshu_available must also fail closed on the exact same DNS rebinding.
        cfg = {"XIAOHONGSHU_API_BASE": bridge_base}
        assert env.is_xiaohongshu_available(cfg) is False
        assert env.XIAOHONGSHU_RESOLVED_API_BASE_KEY not in cfg

    @pytest.mark.parametrize(
        "allowed_ip",
        ["127.0.0.1", "::1", "10.0.0.5", "172.16.4.2", "192.168.1.50", "fd12:3456:789a::1"],
    )
    def test_local_bridge_resolver_accepts_loopback_rfc1918_and_ula(
        self, monkeypatch, allowed_ip: str,
    ) -> None:
        af = socket.AF_INET6 if ":" in allowed_ip else socket.AF_INET
        sockaddr = (allowed_ip, 18060, 0, 0) if af == socket.AF_INET6 else (allowed_ip, 18060)
        monkeypatch.setattr(
            http.socket,
            "getaddrinfo",
            lambda host, port, *a, **kw: [(af, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)],
        )
        verified = http._resolve_local_bridge_addresses("custom.local", 18060)
        assert len(verified) == 1
        assert verified[0][4][0] == allowed_ip

    def test_local_bridge_resolver_preserves_trailing_dot_for_dns_lookup(self, monkeypatch) -> None:
        seen_hosts: list[str] = []

        def fake_getaddrinfo(host, port, *a, **kw):
            seen_hosts.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.50", port))]

        monkeypatch.setattr(http.socket, "getaddrinfo", fake_getaddrinfo)
        verified = http._resolve_local_bridge_addresses("bridge.local.", 18060)
        assert seen_hosts == ["bridge.local."]
        assert verified[0][4] == ("192.168.1.50", 18060)

        with pytest.raises(public_host.NonPublicAddressError):
            http._resolve_local_bridge_addresses("bridge.local..", 18060)

    def test_http_request_rejects_mutually_exclusive_pinning_flags(self) -> None:
        with pytest.raises(ValueError, match="mutually exclusive"):
            http.request("GET", "https://example.com", public_only=True, local_bridge_only=True)

    @pytest.mark.parametrize(
        "bad_base",
        [
            "file:///etc/passwd",
            "ftp://localhost:21/xhs",
            "http://169.254.169.254",
            "http://metadata.google.internal",
            "http://evil.example:18060",
            "http://localhost:18060?x=1",
            "http://localhost:18060#frag",
            "http://user:pass@localhost:18060",
            "http://localhost..:18060",
        ],
    )
    def test_env_is_xiaohongshu_available_blocks_unsafe_base_urls_before_network(
        self, bad_base: str,
    ) -> None:
        with mock.patch.object(http, "get") as get_mock:
            cfg = {"XIAOHONGSHU_API_BASE": bad_base}
            assert env.is_xiaohongshu_available(cfg) is False
            assert env._xiaohongshu_base_logged_in(bad_base, http) is False
        get_mock.assert_not_called()
        assert env.XIAOHONGSHU_RESOLVED_API_BASE_KEY not in cfg

    def test_env_is_xiaohongshu_available_passes_pinning_and_caches_normalized_base(self) -> None:
        seen_kwargs: list[dict[str, Any]] = []

        def fake_get(url, **kwargs):
            seen_kwargs.append(kwargs)
            if url.endswith("/health"):
                return {"success": True}
            if url.endswith("/api/v1/login/status"):
                return {"data": {"is_logged_in": True}}
            raise AssertionError(f"unexpected URL: {url}")

        with mock.patch.object(http, "get", side_effect=fake_get):
            cfg_local = {"XIAOHONGSHU_API_BASE": "  HTTP://custom.local:18060/  "}
            assert env.is_xiaohongshu_available(cfg_local) is True
            assert cfg_local[env.XIAOHONGSHU_RESOLVED_API_BASE_KEY] == "http://custom.local:18060"

            cfg_remote = {"XIAOHONGSHU_API_BASE": "https://xhs.example.com/bridge/"}
            assert env.is_xiaohongshu_available(cfg_remote) is True
            assert cfg_remote[env.XIAOHONGSHU_RESOLVED_API_BASE_KEY] == "https://xhs.example.com/bridge"

        assert len(seen_kwargs) == 4
        for kw in seen_kwargs[:2]:
            assert kw.get("local_bridge_only") is True
            assert callable(kw.get("redirect_guard"))
            assert "public_only" not in kw
        for kw in seen_kwargs[2:]:
            assert kw.get("public_only") is True
            assert callable(kw.get("redirect_guard"))
            assert "local_bridge_only" not in kw

    def test_env_is_xiaohongshu_available_blocks_cross_origin_redirect_unmocked(self) -> None:
        secret_hits: list[str] = []

        class SecretHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                secret_hits.append(self.path)
                body = b'{"success":true,"data":{"is_logged_in":true}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        secret_srv = ThreadingHTTPServer(("127.0.0.1", 0), SecretHandler)
        secret_thread = threading.Thread(target=secret_srv.serve_forever, daemon=True)
        secret_thread.start()

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{secret_srv.server_port}{self.path}")
                self.send_header("Content-Length", "0")
                self.end_headers()

        front_srv = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        front_thread = threading.Thread(target=front_srv.serve_forever, daemon=True)
        front_thread.start()
        try:
            cfg = {"XIAOHONGSHU_API_BASE": f"http://127.0.0.1:{front_srv.server_port}"}
            assert env.is_xiaohongshu_available(cfg) is False
            assert secret_hits == []
        finally:
            front_srv.shutdown()
            front_srv.server_close()
            front_thread.join(2)
            secret_srv.shutdown()
            secret_srv.server_close()
            secret_thread.join(2)

    @pytest.mark.parametrize(
        "login_payload",
        [
            {"code": -1, "data": None},
            {"code": -1, "data": []},
            {"code": -1, "data": "err"},
            None,
        ],
    )
    def test_env_xiaohongshu_probe_handles_null_or_non_dict_data_without_crashing(
        self, login_payload: Any,
    ) -> None:
        def fake_get(url, **kwargs):
            if url.endswith("/health"):
                return {"success": True}
            return login_payload

        with mock.patch.object(http, "get", side_effect=fake_get):
            assert env._xiaohongshu_base_logged_in("http://localhost:18060", http) is False
            assert env.is_xiaohongshu_available({"XIAOHONGSHU_API_BASE": "http://localhost:18060"}) is False


class TestHigh186XiaohongshuNullDataPayloads:
    @pytest.mark.parametrize(
        "login_payload",
        [
            {"code": -1, "data": None},
            {"code": -1, "data": []},
            {"code": -1, "data": "not-a-dict"},
            {"code": -1, "data": 123},
            None,
            [],
        ],
    )
    def test_login_null_or_non_dict_data_raises_http_error_not_attribute_error(
        self, login_payload: Any,
    ) -> None:
        with mock.patch.object(xiaohongshu_api.http, "get", return_value=login_payload):
            with pytest.raises(http.HTTPError, match="not logged in"):
                xiaohongshu_api.search_feeds(
                    "matcha", "2026-06-01", "2026-07-01", "http://localhost:18060",
                )

    @pytest.mark.parametrize(
        "search_payload",
        [
            {"code": -1, "data": None},
            {"code": -1, "data": []},
            {"code": -1, "data": "err"},
            {"data": {"feeds": None}},
            {"data": {"feeds": "not-a-list"}},
            None,
            [],
        ],
    )
    def test_search_null_or_non_dict_data_returns_empty_list_without_crashing(
        self, search_payload: Any,
    ) -> None:
        with mock.patch.object(xiaohongshu_api.http, "get", return_value={"data": {"is_logged_in": True}}), \
                mock.patch.object(xiaohongshu_api.http, "post", return_value=search_payload):
            items = xiaohongshu_api.search_feeds(
                "matcha", "2026-06-01", "2026-07-01", "http://localhost:18060",
            )
        assert items == []


# ---------------------------------------------------------------------------
# HIGH-185: linkedin.py author_url / profile_url SSRF & paid-API abuse defense
# ---------------------------------------------------------------------------


class TestHigh185LinkedInAuthorUrlValidation:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "",
            "   ",
            None,
            "http://www.linkedin.com/in/janedoe",
            "http://169.254.169.254/latest/meta-data/",
            "https://169.254.169.254/in/janedoe",
            "file:///in/etc/passwd",
            "https://evil.example/in/janedoe",
            "https://evil-linkedin.com/in/janedoe",
            "https://linkedin.com.evil.com/in/janedoe",
            "https://www.linkedin.com/company/new-york-times",
            "https://www.linkedin.com/posts/janedoe_123",
            "https://www.linkedin.com/in/",
            "https://www.linkedin.com/in///",
            "https://www.linkedin.com/in/.",
            "https://www.linkedin.com/in/./",
            "https://www.linkedin.com/in/%2e",
            "https://www.linkedin.com/in/janedoe/details/",
            "https://www.linkedin.com/in/janedoe/recent-activity/all/",
            "https://www.linkedin.com/in/janedoe//",
            "https://www.linkedin.com/in/janedoe%2fdetails",
            "https://www.linkedin.com/in/janedoe%5cdetails",
            "https://www.linkedin.com/in/../company/acme",
            "https://www.linkedin.com/in/%2e%2e/company/acme",
            "https://www.linkedin.com../in/janedoe",
            "https://user:pass@www.linkedin.com/in/janedoe",
            "https://evil.com\\@www.linkedin.com/in/janedoe",
            "https://www.linkedin.com:8443/in/janedoe",
            "https://.linkedin.com/in/janedoe",
            "https://a..linkedin.com/in/janedoe",
            "https://www.linkedin.com/in/janedoe\r\nX-Evil: 1",
        ],
    )
    def test_disallowed_profile_urls_rejected_across_all_boundaries(self, bad_url: Any) -> None:
        assert linkedin._is_valid_linkedin_profile_url(bad_url) is False

        with mock.patch.object(linkedin.http, "get") as get_mock, \
                mock.patch.object(linkedin.http, "request") as req_mock:
            # 1. parse_linkedin_response clears untrusted author_url
            parsed = linkedin.parse_linkedin_response({
                "posts": [{
                    "description": "hello world",
                    "author": {"name": "New York Times", "url": bad_url},
                }],
            })
            assert parsed[0]["author_url"] == ""

            # 2. _best_author_match skips untrusted author_url even if passed directly
            assert linkedin._best_author_match(
                [{"author": "New York Times", "author_url": bad_url}], "New York",
            ) == ""

            # 3. search_profile refuses without network call
            assert linkedin.search_profile(bad_url, "valid-token") == {}

            # 4. read_profile refuses without network call
            rp = linkedin.read_profile(bad_url, "valid-token")
            assert rp["status"] == "failed"
            assert rp["data"] is None

        get_mock.assert_not_called()
        req_mock.assert_not_called()

    @pytest.mark.parametrize(
        "good_url",
        [
            "https://www.linkedin.com/in/janedoe",
            "https://linkedin.com/in/janedoe/",
            "https://uk.linkedin.com/in/jane-doe-123",
            "https://www.linkedin.com:443/in/janedoe",
            "https://www.linkedin.com/in/janedoe?trk=public_post",
        ],
    )
    def test_valid_linkedin_profile_urls_accepted(self, good_url: str) -> None:
        assert linkedin._is_valid_linkedin_profile_url(good_url) is True

    def test_company_author_skipped_in_favor_of_person_profile(self) -> None:
        items = [
            {
                "author": "New York Times",
                "author_url": "https://www.linkedin.com/company/the-new-york-times",
            },
            {
                "author": "Jane Doe",
                "author_url": "https://evil.example/in/janedoe",
            },
            {
                "author": "Jane Doe",
                "author_url": "https://www.linkedin.com/in/janedoe",
            },
        ]
        assert linkedin._best_author_match(items, "New York") == ""
        assert linkedin._best_author_match(items, "Jane Doe") == "https://www.linkedin.com/in/janedoe"


# ---------------------------------------------------------------------------
# HIGH-189: github.py .removesuffix(".git") / _normalize_repo_slug
# ---------------------------------------------------------------------------


@dataclass
class _StubCandidate:
    url: str = ""
    title: str = ""
    evidence: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TestHigh189GitHubRepoSlugNormalization:
    @pytest.mark.parametrize(
        ("url", "expected_slug"),
        [
            ("https://github.com/facebook/react", "facebook/react"),
            ("https://github.com/rust-lang/rust", "rust-lang/rust"),
            ("https://github.com/tiangolo/fastapi", "tiangolo/fastapi"),
            ("https://github.com/vercel/ai", "vercel/ai"),
            ("https://github.com/microsoft/TypeScript", "microsoft/TypeScript"),
            ("https://github.com/git/git", "git/git"),
            ("https://github.com/foo/bar.git", "foo/bar"),
            ("https://github.com/facebook/react.git", "facebook/react"),
            ("https://github.com/foo/bar.GIT", "foo/bar"),
            ("https://github.com/facebook/react.", "facebook/react"),
            ("https://github.com/foo/bar.git.", "foo/bar"),
            ("https://github.com/vercel/next.js", "vercel/next.js"),
        ],
    )
    def test_extract_and_apply_star_map_preserve_trailing_g_i_t_and_strip_git_suffix(
        self, url: str, expected_slug: str,
    ) -> None:
        cand = _StubCandidate(url=url, title="check repo", evidence="repo ref")
        refs = github.extract_repo_refs([cand])
        assert refs == [expected_slug]

        fetched: list[str] = []

        def fake_info(repo: str, token: str):
            fetched.append(repo)
            return {"stars": 4200, "forks": 10, "description": "", "language": "", "open_issues": 1}

        with mock.patch.object(github, "_resolve_token", return_value="tok"), \
                mock.patch.object(github, "_fetch_repo_info", side_effect=fake_info):
            annotated = github.enrich_candidates_with_stars([cand])

        assert fetched == [expected_slug]
        assert annotated == 1
        assert cand.metadata["github_stars"] == {expected_slug: 4200}
        assert "(live: 4.2K stars)" in cand.evidence

    def test_distinct_repos_differing_by_t_do_not_collide_and_dot_git_dedupes(self) -> None:
        cands = [
            _StubCandidate(url="https://github.com/foo/react"),
            _StubCandidate(url="https://github.com/foo/reac"),
            _StubCandidate(url="https://github.com/foo/react.git"),
            _StubCandidate(url="https://github.com/foo/.git"),
        ]
        assert github.extract_repo_refs(cands) == ["foo/react", "foo/reac"]

    def test_refetch_datum_strips_case_insensitive_git_suffix(self) -> None:
        item = github.schema.SourceItem(
            item_id="GH1",
            source="github",
            title="t",
            body="",
            url="https://github.com/facebook/react.GIT",
        )
        seen_urls: list[str] = []

        def fake_req(method, url, **kwargs):
            seen_urls.append(url)
            return {"visibility": "public", "private": False, "stargazers_count": 200000}

        with mock.patch.object(github, "_resolve_token", return_value="tok"), \
                mock.patch.object(github.http, "request", side_effect=fake_req):
            out = github.refetch_datum(item, "stars")

        assert seen_urls == ["https://api.github.com/repos/facebook/react"]
        assert out["value"] == 200000


# ---------------------------------------------------------------------------
# HIGH-190: GitHub negated qualifiers, missing keys, & unbalanced quotes
# ---------------------------------------------------------------------------


class TestHigh190GitHubQualifierSanitization:
    @pytest.mark.parametrize(
        ("raw_topic", "expected"),
        [
            ("auth -is:public", "auth"),
            ("react -created:>2020-01-01", "react"),
            ("foo -is:issue", "foo"),
            ("foo -is:pull-request", "foo"),
            ("(-is:public) auth", "auth"),
            ('"-is:public" auth', "auth"),
            ("'-is:public' auth", "auth"),
            ("[-is:public] auth", "auth"),
            ("auth,-is:public", "auth,"),
            ("auth --is:public", "auth"),
            ("auth -org:secret-org -repo:secret/repo", "auth"),
            ('auth -org:"secret org" -label:"bug fix"', "auth"),
            ("auth visibility:private -visibility:public", "auth"),
            ("auth draft:true status:pending reason:completed", "auth"),
            ("auth parent-issue:12 sub-issue:34 category:general answered:true", "auth"),
            ("auth sha:deadbeef owner:acme", "auth"),
            # Wrapper-boundary bypasses: closing paren/bracket before qualifier.
            ("(auth)-is:public", "(auth)"),
            ("(auth)is:private", "(auth)"),
            ("[auth]-is:public", "[auth]"),
            ("[auth]org:secret", "[auth]"),
            # Parenthesized qualifier values.
            ("auth -is:(public)", "auth"),
            ("auth is:(private)", "auth"),
            ("auth label:(bug OR feat)", "auth"),
            ("auth -org:(acme OR corp)", "auth"),
            ("auth -is:(public", "auth"),
            ("(auth)-is:(public)", "(auth)"),
            # Negated wrapped qualifiers leave no stray '-'.
            ('auth -"is:public"', "auth"),
            ("auth -(is:public)", "auth"),
            ("auth -[-is:public]", "auth"),
            # Wrapper-smuggling attempts where empty wrappers split qualifier tokens.
            ("auth is:()private", "auth"),
            ("auth -is:()public", "auth"),
            ("auth -()is:public", "auth"),
            ("auth is:[]private", "auth"),
            ('auth is:""private', "auth"),
            # Adjacent quoted phrases remain distinct (never merged).
            ('"foo" "bar" -is:public', '"foo" "bar"'),
            # Qualifier-only negated inputs collapse to empty string.
            ("-is:public", ""),
            ("(-is:public)", ""),
            ("-(is:public)", ""),
            ('"-is:public"', ""),
            # Unbalanced and backslash-escaped quotes cannot swallow engine qualifiers.
            ('say "hello world', "say hello world"),
            ('say "hello\\" world', 'say "hello" world'),
            ('say "hello\\"', 'say "hello"'),
            ('"exact phrase" and "unclosed', '"exact phrase" and unclosed'),
            ('auth "-is:public', "auth"),
            ('"', ""),
            ('"""', ""),
        ],
    )
    def test_strip_search_qualifiers_blocks_negation_smuggling_and_unbalanced_quotes(
        self, raw_topic: str, expected: str,
    ) -> None:
        assert github.strip_search_qualifiers(raw_topic) == expected

    def test_strip_search_qualifiers_linear_time_on_deeply_nested_wrappers(self) -> None:
        payload = "(" * 5000 + "is:private" + ")" * 5000
        start = time.monotonic()
        result = github.strip_search_qualifiers(payload)
        elapsed = time.monotonic() - start
        assert result == ""
        assert elapsed < 0.2, f"deeply nested wrappers took {elapsed:.3f}s (expected linear O(N) < 0.2s)"

        mixed = "[(" * 2500 + "-is:public" + ")]" * 2500
        start2 = time.monotonic()
        assert github.strip_search_qualifiers(mixed) == ""
        assert time.monotonic() - start2 < 0.2

    def test_strip_search_qualifiers_linear_time_on_long_backslash_run(self) -> None:
        slashes = "\\" * 50000
        start = time.monotonic()
        result = github.strip_search_qualifiers(slashes)
        elapsed = time.monotonic() - start
        assert result == slashes
        assert elapsed < 0.1, f"50k backslashes took {elapsed:.3f}s (expected linear O(N) < 0.1s)"

        start2 = time.monotonic()
        res2 = github.strip_search_qualifiers(f"auth {slashes} -is:public")
        elapsed2 = time.monotonic() - start2
        assert res2 == f"auth {slashes}"
        assert elapsed2 < 0.1

    @pytest.mark.parametrize(
        "adversarial_topic",
        [
            "auth -is:public",
            "(auth)-is:public",
            "(auth)is:private",
            "auth -is:(public)",
            "auth label:(bug OR feat)",
            'auth -"is:public"',
            "react -created:>2020-01-01",
            "foo -is:issue -is:pull-request",
            "(-is:public) auth -org:private-org -repo:acme/secret",
            'say "hello world',
            'say "hello\\"',
            "auth -is:()public visibility:private",
        ],
    )
    @pytest.mark.parametrize("token", [None, "test-token"])
    def test_search_github_preserves_public_and_date_boundaries_against_breakout(
        self, adversarial_topic: str, token: str | None,
    ) -> None:
        captured_queries: list[str] = []

        def fake_fetch(url, *args, **kwargs):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["q"][0]
            captured_queries.append(q)
            return {"items": []}

        with mock.patch.object(github, "_resolve_token", return_value=token), \
                mock.patch.object(github, "_fetch_json", side_effect=fake_fetch):
            github.search_github(adversarial_topic, "2026-07-01", "2026-07-31")

        assert captured_queries
        for q in captured_queries:
            assert q.startswith("is:public ")
            assert "-is:" not in q
            assert "-created:" not in q
            assert "-org:" not in q
            assert "-repo:" not in q
            assert "visibility:" not in q
            assert q.count("created:") == 1
            assert "created:2026-07-01..2026-07-31" in q
            # Balanced quotes guarantee engine-added created:/is:issue are outside any quote.
            assert q.count('"') % 2 == 0
            assert '\\"' not in q

    @pytest.mark.parametrize("qualifier_only", ["-is:public", "(-is:public)", "-(is:public)", '"'])
    def test_negated_qualifier_only_topic_skips_network(self, qualifier_only: str) -> None:
        with mock.patch.object(github, "_resolve_token", return_value="test-token"), \
                mock.patch.object(github, "_fetch_json") as fetch_mock:
            res = github.search_github(qualifier_only, "2026-07-01", "2026-07-31")
        fetch_mock.assert_not_called()
        assert res["items"] == []
        assert "error" not in res
