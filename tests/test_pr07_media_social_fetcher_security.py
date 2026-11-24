"""Security regression tests for PR07: media & social fetcher hardening.

Covers:
- HIGH-175: YouTube direct-transcript baseUrl SSRF / file:// / redirect / unbounded read
- HIGH-176: transcribe.py remote URL validation, yt-dlp isolation/caps, ffmpeg whitelist
- HIGH-178: YouTube video_id validation, VTT path-traversal/glob/collision/symlink isolation
- HIGH-181: Bluesky BSKY_SEARCH_HOST validation, redirect guard, and JWT exfiltration protection
"""

from __future__ import annotations

import ast
import io
import json
import os
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from lib import bluesky, bounded_get, http, public_host, subproc, transcribe, youtube_yt


# ---------------------------------------------------------------------------
# HIGH-175: youtube_yt.py direct transcript baseUrl & read bounds
# ---------------------------------------------------------------------------


def _watch_html_with_base_url(base_url: str) -> str:
    player = {
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {"languageCode": "en", "baseUrl": base_url},
                ]
            }
        }
    }
    return f"<html><script>var ytInitialPlayerResponse = {json.dumps(player)};</script></html>"


class TestHigh175YouTubeDirectTranscriptSSRF:
    @pytest.mark.parametrize(
        "bad_url",
        [
            "file:///etc/passwd",
            "file://localhost/etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8080/captions",
            "http://www.youtube.com/api/timedtext?v=abc123",
            "https://evil.example/api/timedtext?v=abc123",
            "https://evil-youtube.com/api/timedtext?v=abc123",
            "https://youtube.com.evil.com/api/timedtext?v=abc123",
            "https://googlevideo.com/api/timedtext?v=abc123",
            "https://ytimg.com/captions.vtt",
            "https://user:pass@www.youtube.com/api/timedtext?v=abc123",
            "https://@www.youtube.com/api/timedtext?v=abc123",
            "https://www.youtube.com:8443/api/timedtext?v=abc123",
            "https://169.254.169.254/api/timedtext",
            "https://[::1]/api/timedtext",
            "https://www.youtube.com/api/timedtext\r\nX-Injected: 1",
            "https://evil.com\\@www.youtube.com/api/timedtext",
            "/api/timedtext?v=abc123",
            "ftp://www.youtube.com/captions",
            "data:text/vtt,WEBVTT",
            " https://www.youtube.com/api/timedtext ",
            "",
        ],
    )
    def test_disallowed_caption_base_urls_rejected_before_fetch(self, bad_url: str) -> None:
        assert youtube_yt._is_allowed_youtube_transcript_url(bad_url) is False

        seen_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_urls.append(req.full_url)
            if "/watch?" in req.full_url:
                return io.BytesIO(_watch_html_with_base_url(bad_url).encode("utf-8"))
            raise AssertionError(f"Disallowed caption URL was fetched: {req.full_url}")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = youtube_yt._fetch_transcript_direct("abc12345678")

        assert result is None
        assert len(seen_urls) == 1
        assert seen_urls[0] == "https://www.youtube.com/watch?v=abc12345678"

    @pytest.mark.parametrize(
        "good_url",
        [
            "https://www.youtube.com/api/timedtext?v=abc123&lang=en",
            "https://youtube.com/api/timedtext?v=abc123",
            "https://m.youtube.com/api/timedtext?v=abc123",
            "https://music.youtube.com/api/timedtext?v=abc123",
            "https://video.google.com/timedtext?v=abc123",
            "https://rr1---sn-ab5l6nzy.googlevideo.com/timedtext?v=abc123",
            "https://i.ytimg.com/vi/abc123/captions.vtt",
            "https://www.youtube.com:443/api/timedtext?v=abc123",
        ],
    )
    def test_allowed_youtube_caption_urls_accepted(self, good_url: str) -> None:
        assert youtube_yt._is_allowed_youtube_transcript_url(good_url) is True

    def test_read_transcript_url_uses_http_open_request_with_guards(self) -> None:
        captured_reqs: list[urllib.request.Request] = []

        def fake_open_request(req, timeout):
            captured_reqs.append(req)
            return io.BytesIO(b"WEBVTT\n\nok\n")

        req = urllib.request.Request("https://www.youtube.com/api/timedtext?v=abc123&fmt=vtt")
        with mock.patch.object(http, "open_request", side_effect=fake_open_request):
            body = youtube_yt._read_transcript_url(req, timeout=10)

        assert "WEBVTT" in body
        assert len(captured_reqs) == 1
        sent = captured_reqs[0]
        assert getattr(sent, http._PUBLIC_ONLY_ATTR, False) is True
        guard = getattr(sent, http._REDIRECT_GUARD_ATTR, None)
        assert guard is youtube_yt._is_allowed_youtube_transcript_url

    def test_youtube_yt_has_no_direct_urlopen_calls(self) -> None:
        source = Path(youtube_yt.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        direct_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "urlopen"
        ]
        assert direct_calls == []

    @pytest.mark.parametrize(
        "redirect_target",
        [
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "https://127.0.0.1/secret",
            "https://evil.example/steal",
            "http://www.youtube.com/api/timedtext?v=abc",
            "https://www.youtube.com:8443/api/timedtext?v=abc",
            "https://user@www.youtube.com/api/timedtext?v=abc",
        ],
    )
    def test_redirect_handler_blocks_disallowed_targets(self, redirect_target: str) -> None:
        handler = http._StripAuthOnCrossOriginRedirect()
        req = urllib.request.Request("https://www.youtube.com/api/timedtext?v=abc123")
        setattr(req, http._REDIRECT_GUARD_ATTR, youtube_yt._is_allowed_youtube_transcript_url)
        setattr(req, http._PUBLIC_ONLY_ATTR, True)
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, redirect_target)
        assert exc_info.value.code == 403

    def test_public_only_blocks_private_dns_resolution(self, monkeypatch) -> None:
        def fake_getaddrinfo(host, port, *_args, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", port))]

        monkeypatch.setattr(public_host.socket, "getaddrinfo", fake_getaddrinfo)
        req = urllib.request.Request("https://www.youtube.com/api/timedtext?v=abc123")
        with pytest.raises(urllib.error.URLError) as exc_info:
            youtube_yt._read_transcript_url(req, timeout=5)
        assert isinstance(exc_info.value.reason, public_host.NonPublicAddressError)

    def test_response_exceeding_max_bytes_is_rejected_no_deadline(self, monkeypatch) -> None:
        monkeypatch.setattr(youtube_yt, "_MAX_TRANSCRIPT_RESPONSE_BYTES", 64)
        oversized = b"A" * 65

        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(oversized)):
            result = youtube_yt._fetch_transcript_direct("abc12345678")

        assert result is None

    def test_response_exceeding_max_bytes_is_rejected_with_deadline(self, monkeypatch) -> None:
        monkeypatch.setattr(youtube_yt, "_MAX_TRANSCRIPT_RESPONSE_BYTES", 64)
        oversized = b"A" * 65

        with mock.patch.object(bounded_get, "get", return_value=(200, oversized, None)), \
             youtube_yt.sc_spend_until(lambda: False, deadline=youtube_yt.time.monotonic() + 60):
            result = youtube_yt._fetch_transcript_direct("abc12345678")

        assert result is None

    def test_fragment_in_base_url_stripped_before_fmt_param(self) -> None:
        seen_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            seen_urls.append(req.full_url)
            if "/watch?" in req.full_url:
                html = _watch_html_with_base_url("https://www.youtube.com/api/timedtext?v=abc123#frag")
                return io.BytesIO(html.encode("utf-8"))
            return io.BytesIO(b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n")

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = youtube_yt._fetch_transcript_direct("abc12345678")

        assert result is not None and "hello" in result
        assert seen_urls[1] == "https://www.youtube.com/api/timedtext?v=abc123&fmt=vtt"

    def test_read_transcript_url_propagates_guards_and_max_bytes_under_deadline(self) -> None:
        captured: list[tuple[urllib.request.Request, dict]] = []

        def fake_bounded(req, **kwargs):
            captured.append((req, kwargs))
            return 200, b"WEBVTT\n\nok\n", None

        req = urllib.request.Request("https://www.youtube.com/api/timedtext?v=abc123&fmt=vtt")
        with mock.patch.object(bounded_get, "get", side_effect=fake_bounded), \
             youtube_yt.sc_spend_until(lambda: False, deadline=time.monotonic() + 60):
            body = youtube_yt._read_transcript_url(req, timeout=10)

        assert "WEBVTT" in body
        assert len(captured) == 1
        sent_req, kwargs = captured[0]
        assert getattr(sent_req, http._PUBLIC_ONLY_ATTR, False) is True
        assert getattr(sent_req, bounded_get._REDIRECT_POLICY_ATTR, None) == youtube_yt._YT_TRANSCRIPT_REDIRECT_POLICY
        assert kwargs["max_bytes"] == youtube_yt._MAX_TRANSCRIPT_RESPONSE_BYTES

    def test_bounded_get_worker_enforces_public_only_on_loopback(self) -> None:
        hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"secret")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/secret"
            req = urllib.request.Request(url)
            setattr(req, http._PUBLIC_ONLY_ATTR, True)
            with pytest.raises(urllib.error.URLError) as exc_info:
                bounded_get.get(req, timeout=5, deadline_monotonic=time.monotonic() + 5)
            assert isinstance(exc_info.value.reason, public_host.NonPublicAddressError)

            req_kw = urllib.request.Request(url)
            with pytest.raises(urllib.error.URLError) as exc_info_kw:
                bounded_get.get(
                    req_kw, timeout=5, deadline_monotonic=time.monotonic() + 5, public_only=True,
                )
            assert isinstance(exc_info_kw.value.reason, public_host.NonPublicAddressError)
            assert hits == []
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    @pytest.mark.parametrize(
        ("target_kind", "expected_code"),
        [
            ("metadata", 403),
            ("secret_loopback", 403),
            ("evil_host", 403),
            ("ftp_scheme", 403),
            ("file_scheme", 403),
        ],
    )
    def test_bounded_get_worker_blocks_disallowed_redirects_unmocked(
        self, target_kind: str, expected_code: int,
    ) -> None:
        secret_hits: list[str] = []

        class SecretHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                secret_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"TOP_SECRET")

        secret_srv = ThreadingHTTPServer(("127.0.0.1", 0), SecretHandler)
        secret_srv.daemon_threads = True
        secret_thread = threading.Thread(target=secret_srv.serve_forever, daemon=True)
        secret_thread.start()

        redirect_targets = {
            "metadata": "http://169.254.169.254/latest/meta-data/",
            "secret_loopback": f"http://127.0.0.1:{secret_srv.server_port}/secret",
            "evil_host": "https://evil.example/steal",
            "ftp_scheme": "ftp://evil.example/secret",
            "file_scheme": "file:///etc/passwd",
        }
        location = redirect_targets[target_kind]

        class RedirectHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

        front_srv = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        front_srv.daemon_threads = True
        front_thread = threading.Thread(target=front_srv.serve_forever, daemon=True)
        front_thread.start()
        try:
            policy = {
                "https_only": False,
                "require_public_syntax": False,
                "exact_hosts": ["127.0.0.1"],
                "allowed_ports": [front_srv.server_port],
            }
            req = urllib.request.Request(f"http://127.0.0.1:{front_srv.server_port}/start")
            setattr(req, bounded_get._REDIRECT_POLICY_ATTR, policy)
            status, body, error = bounded_get.get(
                req, timeout=5, deadline_monotonic=time.monotonic() + 5, read_error_body=False,
            )
            assert status == expected_code
            assert body is None
            assert isinstance(error, urllib.error.HTTPError)
            assert error.code == expected_code
            assert secret_hits == []
        finally:
            front_srv.shutdown()
            front_srv.server_close()
            front_thread.join(2)
            secret_srv.shutdown()
            secret_srv.server_close()
            secret_thread.join(2)

    def test_bounded_get_worker_enforces_max_bytes_mid_stream_under_sc_spend_until(
        self, monkeypatch,
    ) -> None:
        bytes_written = [0]
        total_stream_bytes = 4 * 1024 * 1024

        class StreamHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(total_stream_bytes))
                self.end_headers()
                chunk = b"X" * 16384
                remaining = total_stream_bytes
                try:
                    while remaining > 0:
                        piece = chunk[:remaining]
                        self.wfile.write(piece)
                        self.wfile.flush()
                        bytes_written[0] += len(piece)
                        remaining -= len(piece)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StreamHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        original_bounded = bounded_get.get
        monkeypatch.setattr(youtube_yt, "_MAX_TRANSCRIPT_RESPONSE_BYTES", 4096)

        def local_bounded(req, **kwargs):
            local_req = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/watch",
                headers=dict(req.header_items()),
            )
            return original_bounded(local_req, **kwargs)

        monkeypatch.setattr(bounded_get, "get", local_bounded)
        try:
            with youtube_yt.sc_spend_until(lambda: False, deadline=time.monotonic() + 20):
                result = youtube_yt._fetch_transcript_direct("abc12345678", timeout=5)
            assert result is None
            assert bytes_written[0] < total_stream_bytes
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


# ---------------------------------------------------------------------------
# HIGH-178: youtube_yt.py video_id traversal, glob, collision, URL injection
# ---------------------------------------------------------------------------


class TestHigh178YouTubeVideoIdValidation:
    @pytest.mark.parametrize(
        "bad_id",
        [
            "",
            "../outside",
            "../../etc/passwd",
            "..\\outside",
            "sub/vid",
            "sub\\vid",
            "*",
            "abc*",
            "abc?",
            "[a-z]bc",
            "abc&list=PLevil",
            "abc#frag",
            "abc?x=1",
            "abc%26x=1",
            "abc def",
            "abc\ndef",
            "abc.en",
            "a" * 65,
            None,
            12345,
        ],
    )
    def test_invalid_video_ids_rejected(self, bad_id) -> None:
        assert youtube_yt._is_valid_video_id(bad_id) is False

    @pytest.mark.parametrize(
        "good_id",
        [
            "dQw4w9WgXcQ",
            "-_abc123XYZ",
            "abc123",
            "YT1",
            "YT10",
            "video-00",
        ],
    )
    def test_valid_video_ids_accepted(self, good_id: str) -> None:
        assert youtube_yt._is_valid_video_id(good_id) is True

    def test_pick_ytdlp_vtt_blocks_parent_directory_traversal(self, tmp_path: Path) -> None:
        (tmp_path / "secret.en.vtt").write_text("WEBVTT\n\nSECRET\n", encoding="utf-8")
        work = tmp_path / "work"
        work.mkdir()

        assert youtube_yt._pick_ytdlp_vtt("../secret", str(work), ["en"]) is None
        assert youtube_yt._pick_ytdlp_vtt("../*", str(work), ["en"]) is None
        assert youtube_yt._pick_ytdlp_vtt("*", str(work), ["en"]) is None

    def test_pick_ytdlp_vtt_blocks_prefix_collision_between_yt1_and_yt10(self, tmp_path: Path) -> None:
        (tmp_path / "YT10.en.vtt").write_text("WEBVTT\n\nVideo ten\n", encoding="utf-8")

        assert youtube_yt._pick_ytdlp_vtt("YT1", str(tmp_path), ["en"]) is None

        (tmp_path / "YT1.en.vtt").write_text("WEBVTT\n\nVideo one\n", encoding="utf-8")
        picked = youtube_yt._pick_ytdlp_vtt("YT1", str(tmp_path), ["en"])
        assert picked is not None
        assert picked.name == "YT1.en.vtt"

    def test_pick_ytdlp_vtt_blocks_symlink_escape(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.vtt"
        outside.write_text("WEBVTT\n\nLEAKED\n", encoding="utf-8")
        work = tmp_path / "work"
        work.mkdir()
        link = work / "abc12345678.en.vtt"
        link.symlink_to(outside)

        assert youtube_yt._pick_ytdlp_vtt("abc12345678", str(work), ["en"]) is None

    def test_parallel_transcripts_isolate_worker_temp_dirs(self) -> None:
        seen_dirs: dict[str, str] = {}

        def fake_fetch(vid, temp_dir, status=None, token=None, rescue_budget=None):
            seen_dirs[vid] = temp_dir
            return f"ok-{vid}"

        with mock.patch.object(youtube_yt, "fetch_transcript", side_effect=fake_fetch):
            out = youtube_yt.fetch_transcripts_parallel(["YT1", "YT10"], max_workers=2)

        assert out == {"YT1": "ok-YT1", "YT10": "ok-YT10"}
        assert seen_dirs["YT1"] != seen_dirs["YT10"]
        assert Path(seen_dirs["YT1"]).parent == Path(seen_dirs["YT10"]).parent

    @pytest.mark.parametrize("bad_id", ["../secret", "abc&list=1", "abc#frag", "*"])
    def test_all_entrypoints_reject_invalid_video_id_without_side_effects(
        self, tmp_path: Path, bad_id: str,
    ) -> None:
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout") as run_mock, \
             mock.patch.object(youtube_yt.http, "get") as http_get, \
             mock.patch.object(http, "open_request") as open_req:
            assert youtube_yt.fetch_transcript(bad_id, str(tmp_path), token="k") is None
            assert youtube_yt._fetch_transcript_direct(bad_id) is None
            assert youtube_yt._fetch_transcript_ytdlp(bad_id, str(tmp_path)) is None
            assert youtube_yt._fetch_transcript_ytdlp_via_ssh(bad_id, "macmini") is None
            assert youtube_yt._ytdlp_comments_result(bad_id) == ([], False)
            assert youtube_yt._fetch_video_comments(bad_id, token="k") == []
            assert youtube_yt._sc_fetch_transcript(bad_id, token="k") is None

        run_mock.assert_not_called()
        http_get.assert_not_called()
        open_req.assert_not_called()

    def test_search_parsers_drop_poisoned_video_ids(self) -> None:
        stdout = "\n".join([
            json.dumps({"id": "../evil", "title": "Bad", "upload_date": "20260601"}),
            json.dumps({"id": "abc&foo=bar", "title": "Bad2", "upload_date": "20260601"}),
            json.dumps({"id": "dQw4w9WgXcQ", "title": "Good", "upload_date": "20260601"}),
        ])
        items = youtube_yt._parse_search_stdout(stdout, "topic")
        assert [i["video_id"] for i in items] == ["dQw4w9WgXcQ"]

        sc_raw = [
            {"id": "../evil", "title": "Bad", "views": 100, "upload_date": "2026-06-15"},
            {"id": "dQw4w9WgXcQ", "title": "Good", "views": 50, "upload_date": "2026-06-15"},
        ]
        with mock.patch.object(youtube_yt, "_sc_youtube_search", return_value=sc_raw), \
             mock.patch.object(youtube_yt, "_sc_fetch_transcript", return_value="ok"):
            out = youtube_yt.search_youtube_sc(
                "topic", "2026-06-01", "2026-06-30", depth="default", token="dummy",
            )
        assert [i["video_id"] for i in out["items"]] == ["dQw4w9WgXcQ"]

    def test_leading_dash_video_id_and_proxy_stay_before_option_terminator(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("YOUTUBE_PROXY", "socks5://127.0.0.1:1080")
        monkeypatch.delenv("LAST30DAYS_YOUTUBE_SSH_HOST", raising=False)
        fake_res = subproc.SubprocResult(returncode=0, stdout='{"comments": []}', stderr="")

        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=fake_res) as run_mock:
            youtube_yt._fetch_transcript_ytdlp("-_abc123XYZ", str(tmp_path))
            youtube_yt._ytdlp_comments_result("-_abc123XYZ")

        for call in run_mock.call_args_list:
            cmd = call.args[0]
            dash_idx = cmd.index("--")
            assert cmd[dash_idx + 1] == "https://www.youtube.com/watch?v=-_abc123XYZ"
            assert cmd.index("--proxy") < dash_idx
            assert cmd.index("--extractor-args") < dash_idx
            assert "--ignore-config" in cmd[:dash_idx]
            assert "--no-cookies-from-browser" in cmd[:dash_idx]

    def test_leading_dash_video_id_on_ssh_transcript_uses_option_terminator(self) -> None:
        fake_vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nssh transcript\n"
        fake_res = subproc.SubprocResult(returncode=0, stdout=fake_vtt, stderr="")
        with mock.patch.object(youtube_yt, "_run_ytdlp", return_value=fake_res) as run_mock:
            out = youtube_yt._fetch_transcript_ytdlp_via_ssh("-_abc123XYZ", "macmini")

        assert out == fake_vtt
        remote_script = run_mock.call_args.args[0][-1]
        assert "-- 'https://www.youtube.com/watch?v=-_abc123XYZ'" in remote_script


# ---------------------------------------------------------------------------
# HIGH-176: transcribe.py URL SSRF, yt-dlp isolation/caps, ffmpeg whitelist
# ---------------------------------------------------------------------------


class TestHigh176TranscribeHardening:
    @pytest.mark.parametrize(
        "bad_source",
        [
            "http://127.0.0.1:8080/audio.mp3",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/internal.mp3",
            "http://[::1]/audio.mp3",
            "http://2130706433/audio.mp3",
            "http://0177.0.0.1/audio.mp3",
            "https://localhost/audio.mp3",
            "https://user:pass@example.com/audio.mp3",
            "https://127.0.0.1\\@media.example.com/audio.mp3",
            "https://evil.com\\@media.example.com/audio.mp3",
            "file:///etc/passwd",
            "ftp://example.com/audio.mp3",
            "gopher://127.0.0.1:6379/",
            "concat:/etc/passwd|/etc/shadow",
            "pipe:0",
            "subfile,,start,0,,:/etc/passwd",
            "--exec=id",
            "https://example.com/a\nb",
            "  https://127.0.0.1/a",
            "",
        ],
    )
    def test_unsafe_sources_rejected_without_spawning_subprocesses(
        self, tmp_path: Path, bad_source: str,
    ) -> None:
        with mock.patch.object(transcribe, "_run") as run_mock:
            result = transcribe._acquire_audio(bad_source, str(tmp_path), timeout=30.0)
        assert result is None
        run_mock.assert_not_called()

    def test_private_dns_resolution_rejected_for_remote_url(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        def fake_getaddrinfo(host, port, *_args, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("169.254.169.254", port))]

        monkeypatch.setattr(public_host.socket, "getaddrinfo", fake_getaddrinfo)
        with mock.patch.object(transcribe, "_run") as run_mock:
            result = transcribe._acquire_audio(
                "https://rebind.example/media.mp4", str(tmp_path), timeout=30.0,
            )
        assert result is None
        run_mock.assert_not_called()

    @pytest.mark.parametrize(
        "url",
        [
            "https://media.example.com/clip.mp4",
            "HTTP://media.example.com/clip.mp4",
            "HTTPS://media.example.com/clip.mp4",
        ],
    )
    def test_public_remote_url_uses_hardened_ytdlp_and_ffmpeg_flags(
        self, tmp_path: Path, monkeypatch, url: str,
    ) -> None:
        def fake_getaddrinfo(host, port, *_args, **_kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]

        monkeypatch.setattr(public_host.socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setenv("NO_PROXY", "169.254.169.254,127.0.0.1,localhost")
        monkeypatch.setenv("ALL_PROXY", "socks5://attacker.example:1080")
        cmds: list[tuple[list[str], dict | None]] = []

        def fake_run(cmd, timeout=120.0, *, env=None):
            cmds.append((cmd, env))
            if cmd[0] == "yt-dlp":
                out_path = cmd[cmd.index("-o") + 1]
                Path(out_path).write_bytes(b"fake-m4a")
            elif cmd[0] == "ffmpeg":
                Path(cmd[-1]).write_bytes(b"fake-mp3")
            return True

        with mock.patch.object(transcribe, "_run", side_effect=fake_run):
            out = transcribe._acquire_audio(url, str(tmp_path), timeout=30.0)

        assert out is not None and out.endswith("audio.mp3")
        assert len(cmds) == 2

        (ytdlp_cmd, ytdlp_env), (ffmpeg_cmd, _ffmpeg_env) = cmds
        assert ytdlp_cmd[0] == "yt-dlp"
        dash_idx = ytdlp_cmd.index("--")
        assert ytdlp_cmd[dash_idx + 1] == url
        flags = ytdlp_cmd[:dash_idx]
        assert "--ignore-config" in flags
        assert "--no-plugin-dirs" in flags
        assert "--no-cookies-from-browser" in flags
        assert "--no-playlist" in flags
        assert "--no-live-from-start" in flags
        assert flags[flags.index("--match-filter") + 1] == "!is_live"
        assert flags[flags.index("--max-filesize") + 1] == transcribe.MAX_DOWNLOAD_SIZE_ARG
        assert flags[flags.index("--downloader") + 1] == "dash,m3u8:native"
        proxy_url = flags[flags.index("--proxy") + 1]
        assert proxy_url.startswith("http://127.0.0.1:")
        assert ytdlp_env is not None
        assert ytdlp_env["HTTP_PROXY"] == proxy_url
        assert ytdlp_env["HTTPS_PROXY"] == proxy_url
        assert ytdlp_env["NO_PROXY"] == ""
        assert ytdlp_env["no_proxy"] == ""
        assert "ALL_PROXY" not in ytdlp_env

        assert ffmpeg_cmd[0] == "ffmpeg"
        assert "-nostdin" in ffmpeg_cmd
        wl_idx = ffmpeg_cmd.index("-protocol_whitelist")
        i_idx = ffmpeg_cmd.index("-i")
        assert ffmpeg_cmd[wl_idx + 1] == "file"
        assert wl_idx < i_idx
        assert "-vn" in ffmpeg_cmd
        assert ffmpeg_cmd[ffmpeg_cmd.index("-map_metadata") + 1] == "-1"
        assert os.path.isabs(ffmpeg_cmd[i_idx + 1])

    def test_oversized_ytdlp_download_rejected_before_ffmpeg(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(transcribe, "MAX_DOWNLOAD_BYTES", 10)
        monkeypatch.setattr(
            public_host.socket, "getaddrinfo",
            lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))],
        )
        cmds: list[list[str]] = []

        def fake_run(cmd, timeout=120.0, *, env=None):
            cmds.append(cmd)
            if cmd[0] == "yt-dlp":
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"X" * 11)
            return True

        with mock.patch.object(transcribe, "_run", side_effect=fake_run):
            out = transcribe._acquire_audio(
                "https://media.example.com/huge.mp4", str(tmp_path), timeout=30.0,
            )

        assert out is None
        assert [c[0] for c in cmds] == ["yt-dlp"]

    def test_media_proxy_blocks_http_and_connect_ssrf_and_redirects(self) -> None:
        secret_hits: list[str] = []

        class SecretHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                secret_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"PRIVATE_SECRET")

        secret_srv = ThreadingHTTPServer(("127.0.0.1", 0), SecretHandler)
        secret_srv.daemon_threads = True
        secret_thread = threading.Thread(target=secret_srv.serve_forever, daemon=True)
        secret_thread.start()

        class PublicHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/redirect-secret":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{secret_srv.server_port}/secret")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/playlist.m3u8":
                    body = f"#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:{secret_srv.server_port}/seg.ts\n".encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")

        pub_srv = ThreadingHTTPServer(("127.0.0.1", 0), PublicHandler)
        pub_srv.daemon_threads = True
        pub_thread = threading.Thread(target=pub_srv.serve_forever, daemon=True)
        pub_thread.start()

        def hybrid_resolver(host: str, port: int):
            if host == "public.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", pub_srv.server_port))]
            return public_host.resolve_public_addresses(host, port)

        try:
            with transcribe._PublicMediaProxy(max_bytes=1024 * 1024, resolver=hybrid_resolver) as proxy:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy.url, "https": proxy.url})
                )
                # Direct public.test succeeds.
                with opener.open("http://public.test/ok", timeout=5) as resp:
                    assert resp.read() == b"ok"

                # 302 redirect from public.test to 127.0.0.1 secret is blocked with 403.
                with pytest.raises(urllib.error.HTTPError) as exc_redir:
                    opener.open("http://public.test/redirect-secret", timeout=5)
                assert exc_redir.value.code == 403

                # HLS playlist segment pointing at loopback is blocked with 403.
                with opener.open("http://public.test/playlist.m3u8", timeout=5) as resp:
                    playlist = resp.read().decode("utf-8")
                seg_url = [line for line in playlist.splitlines() if line.startswith("http")][0]
                with pytest.raises(urllib.error.HTTPError) as exc_seg:
                    opener.open(seg_url, timeout=5)
                assert exc_seg.value.code == 403

                # Direct metadata & loopback HTTP and HTTPS CONNECT targets are refused with 403.
                for target in (
                    f"127.0.0.1:{secret_srv.server_port}",
                    "169.254.169.254:443",
                    "[::1]:443",
                    "2130706433:80",
                ):
                    with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                        sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode("ascii"))
                        reply = sock.recv(1024).decode("latin-1")
                    assert reply.startswith("HTTP/1.1 403"), (target, reply)

            assert secret_hits == []
        finally:
            pub_srv.shutdown()
            pub_srv.server_close()
            pub_thread.join(2)
            secret_srv.shutdown()
            secret_srv.server_close()
            secret_thread.join(2)

    def test_media_proxy_aborts_chunked_stream_mid_transfer_at_max_bytes(self) -> None:
        bytes_written = [0]
        total_stream_bytes = 2 * 1024 * 1024

        class ChunkedHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                payload = b"A" * 8192
                frame = f"{len(payload):x}\r\n".encode("ascii") + payload + b"\r\n"
                remaining = total_stream_bytes
                try:
                    while remaining > 0:
                        self.wfile.write(frame)
                        self.wfile.flush()
                        bytes_written[0] += len(payload)
                        remaining -= len(payload)
                    self.wfile.write(b"0\r\n\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), ChunkedHandler)
        srv.daemon_threads = True
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()

        def hybrid_resolver(host: str, port: int):
            if host == "stream.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", srv.server_port))]
            return public_host.resolve_public_addresses(host, port)

        try:
            with transcribe._PublicMediaProxy(max_bytes=4096, resolver=hybrid_resolver) as proxy:
                with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                    sock.sendall(b"GET http://stream.test/live HTTP/1.1\r\nHost: stream.test\r\n\r\n")
                    received = bytearray()
                    try:
                        while True:
                            chunk = sock.recv(4096)
                            if not chunk:
                                break
                            received.extend(chunk)
                    except OSError:
                        pass
                assert proxy.limit_exceeded is True
                assert len(received) <= 4096
                assert bytes_written[0] < total_stream_bytes

                # Subsequent requests after limit_exceeded return 413 immediately.
                with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                    sock.sendall(b"GET http://stream.test/next HTTP/1.1\r\nHost: stream.test\r\n\r\n")
                    reply = sock.recv(1024).decode("latin-1")
                assert reply.startswith("HTTP/1.1 413")
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(2)

    def test_media_proxy_aborts_connect_tunnel_mid_transfer_at_max_bytes(self) -> None:
        upstream_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        upstream_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        upstream_srv.bind(("127.0.0.1", 0))
        upstream_srv.listen(4)
        upstream_port = upstream_srv.getsockname()[1]
        bytes_written = [0]
        total_bytes = 2 * 1024 * 1024
        stop_event = threading.Event()

        def serve_stream() -> None:
            upstream_srv.settimeout(2.0)
            try:
                conn, _ = upstream_srv.accept()
            except OSError:
                return
            with conn:
                chunk = b"B" * 8192
                remaining = total_bytes
                try:
                    while remaining > 0 and not stop_event.is_set():
                        conn.sendall(chunk)
                        bytes_written[0] += len(chunk)
                        remaining -= len(chunk)
                except OSError:
                    pass

        thread = threading.Thread(target=serve_stream, daemon=True)
        thread.start()

        def hybrid_resolver(host: str, port: int):
            if host == "tunnel.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", upstream_port))]
            return public_host.resolve_public_addresses(host, port)

        try:
            with transcribe._PublicMediaProxy(max_bytes=4096, resolver=hybrid_resolver) as proxy:
                with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as sock:
                    sock.sendall(b"CONNECT tunnel.test:443 HTTP/1.1\r\nHost: tunnel.test:443\r\n\r\n")
                    buf = bytearray()
                    while b"\r\n\r\n" not in buf:
                        part = sock.recv(1024)
                        assert part
                        buf.extend(part)
                    header, _, tail = bytes(buf).partition(b"\r\n\r\n")
                    assert header.startswith(b"HTTP/1.1 200")
                    received = bytearray(tail)
                    try:
                        while True:
                            part = sock.recv(4096)
                            if not part:
                                break
                            received.extend(part)
                    except OSError:
                        pass
                assert proxy.limit_exceeded is True
                assert len(received) <= 4096
                assert bytes_written[0] < total_bytes
        finally:
            stop_event.set()
            upstream_srv.close()
            thread.join(2)

    @pytest.mark.skipif(shutil.which("yt-dlp") is None, reason="yt-dlp binary not installed")
    def test_real_ytdlp_cannot_ssrf_via_redirect_or_hls_through_media_proxy(
        self, tmp_path: Path,
    ) -> None:
        secret_hits: list[str] = []

        class SecretHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                secret_hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"SECRET_AUDIO")

        secret_srv = ThreadingHTTPServer(("127.0.0.1", 0), SecretHandler)
        secret_srv.daemon_threads = True
        secret_thread = threading.Thread(target=secret_srv.serve_forever, daemon=True)
        secret_thread.start()

        class FrontHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{secret_srv.server_port}/secret.mp3")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/evil.m3u8":
                    body = (
                        "#EXTM3U\n"
                        "#EXT-X-VERSION:3\n"
                        "#EXT-X-TARGETDURATION:2\n"
                        "#EXTINF:2.0,\n"
                        f"http://127.0.0.1:{secret_srv.server_port}/seg0.ts\n"
                        "#EXT-X-ENDLIST\n"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

        front_srv = ThreadingHTTPServer(("127.0.0.1", 0), FrontHandler)
        front_srv.daemon_threads = True
        front_thread = threading.Thread(target=front_srv.serve_forever, daemon=True)
        front_thread.start()

        def hybrid_resolver(host: str, port: int):
            if host == "public.test":
                return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", front_srv.server_port))]
            return public_host.resolve_public_addresses(host, port)

        try:
            with transcribe._PublicMediaProxy(max_bytes=1024 * 1024, resolver=hybrid_resolver) as proxy:
                env = transcribe._sanitized_ytdlp_env(proxy.url)
                for path in ("/redirect", "/evil.m3u8"):
                    out_file = str(tmp_path / f"out_{path.strip('/').replace('.', '_')}.m4a")
                    ok = transcribe._run([
                        "yt-dlp",
                        "--ignore-config",
                        "--no-plugin-dirs",
                        "--no-cookies-from-browser",
                        "--no-playlist",
                        "--no-live-from-start",
                        "--match-filter", "!is_live",
                        "--max-filesize", transcribe.MAX_DOWNLOAD_SIZE_ARG,
                        "--downloader", "dash,m3u8:native",
                        "--proxy", proxy.url,
                        "-f", "bestaudio[protocol*=http]/bestaudio[protocol^=m3u8]",
                        "-o", out_file,
                        "--",
                        f"http://public.test{path}",
                    ], timeout=15.0, env=env)
                    assert ok is False
            assert secret_hits == []
        finally:
            front_srv.shutdown()
            front_srv.server_close()
            front_thread.join(2)
            secret_srv.shutdown()
            secret_srv.server_close()
            secret_thread.join(2)

    @pytest.mark.parametrize("filename", ["http_clip.mp3", "-leading_dash.mp3"])
    def test_local_files_with_http_prefix_or_leading_dash_handled_safely(
        self, tmp_path: Path, monkeypatch, filename: str,
    ) -> None:
        clip = tmp_path / filename
        clip.write_bytes(b"local-audio")
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(tmp_path)
        cmds: list[list[str]] = []

        def fake_run(cmd, timeout=120.0):
            cmds.append(cmd)
            Path(cmd[-1]).write_bytes(b"mp3")
            return True

        with mock.patch.object(transcribe, "_run", side_effect=fake_run):
            out = transcribe._acquire_audio(filename, str(workdir), timeout=30.0)

        assert out is not None
        assert len(cmds) == 1
        assert cmds[0][0] == "ffmpeg"
        i_arg = cmds[0][cmds[0].index("-i") + 1]
        assert os.path.isabs(i_arg)
        assert not i_arg.startswith("-")
        assert Path(i_arg) == clip.resolve()

    def test_chunk_audio_ffmpeg_uses_nostdin_and_file_protocol_whitelist(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        monkeypatch.setattr(transcribe, "MAX_UPLOAD_BYTES", 5)
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"0123456789")
        cmds: list[list[str]] = []

        def fake_run(cmd, timeout=120.0):
            cmds.append(cmd)
            (tmp_path / "chunk_000.mp3").write_bytes(b"01234")
            return True

        with mock.patch.object(transcribe, "_run", side_effect=fake_run):
            chunks = transcribe._chunk_audio(str(audio), str(tmp_path))

        assert len(chunks) == 1
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "-nostdin" in cmd
        wl_idx = cmd.index("-protocol_whitelist")
        i_idx = cmd.index("-i")
        assert cmd[wl_idx + 1] == "file"
        assert wl_idx < i_idx


# ---------------------------------------------------------------------------
# HIGH-181: bluesky.py BSKY_SEARCH_HOST validation & JWT protection
# ---------------------------------------------------------------------------


class TestHigh181BlueskySearchHostHardening:
    @pytest.fixture(autouse=True)
    def _clean_bluesky_state(self, monkeypatch):
        bluesky._reset_session_cache()
        monkeypatch.delenv("BSKY_SEARCH_HOST", raising=False)
        yield
        bluesky._reset_session_cache()

    @pytest.mark.parametrize(
        "bad_host",
        [
            "api.bsky.app@attacker.example",
            "user:pass@api.bsky.app",
            "attacker.example?x=1",
            "attacker.example#frag",
            "attacker.example\\evil",
            "api.bsky.app\t.evil.com",
            "api.bsky.app\r\nX-Injected: 1",
            "169.254.169.254",
            "127.0.0.1",
            "127.0.0.1:8080",
            "10.0.0.1",
            "8.8.8.8",
            "[::1]",
            "::1",
            "2130706433",
            "0177.0.0.1",
            "0x7f.0.0.1",
            "localhost",
            "sub.localhost",
            "printer.local",
            "metadata.google.internal",
            "host.localdomain",
            "router.home.arpa",
            "http://api.bsky.app",
            "file:///etc/passwd",
            "https://https://api.bsky.app",
            "api.bsky.app:8443",
            "api.bsky.app:443",
            "api.bsky.app/xrpc/evil",
            "https://api.bsky.app//",
            "аpi.bsky.app",
            "-bad.bsky.app",
            "bad-.bsky.app",
            "singlelabel",
            "   ",
        ],
    )
    def test_malicious_bsky_search_host_falls_back_to_default(
        self, monkeypatch, bad_host: str,
    ) -> None:
        monkeypatch.setenv("BSKY_SEARCH_HOST", bad_host)
        url = bluesky._resolve_search_url()
        assert url == "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    def test_poisoned_host_never_receives_bearer_jwt_in_search(self, monkeypatch) -> None:
        monkeypatch.setenv("BSKY_SEARCH_HOST", "api.bsky.app@attacker.example")
        config = {"BSKY_HANDLE": "user.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}

        with mock.patch.object(
            bluesky.http,
            "request",
            side_effect=[{"accessJwt": "live-jwt", "refreshJwt": "live-ref"}, {"posts": []}],
        ) as req_mock:
            result = bluesky.search_bluesky("ai", "2026-06-01", "2026-06-30", config=config)

        assert result == {"posts": []}
        search_url = req_mock.call_args_list[1].args[1]
        assert search_url.startswith("https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?")
        assert "attacker.example" not in search_url

    def test_untrusted_third_party_fqdn_never_receives_bsky_social_jwt(self) -> None:
        config = {
            "BSKY_HANDLE": "user.bsky.social",
            "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
            "BSKY_SEARCH_HOST": "attacker.example",
        }
        with mock.patch.object(bluesky.http, "request", return_value={"posts": []}) as req_mock:
            result = bluesky.search_bluesky("ai", "2026-06-01", "2026-06-30", config=config)

        assert result == {"posts": []}
        # Must NOT call createSession and must NOT send Authorization header.
        assert req_mock.call_count == 1
        call = req_mock.call_args_list[0]
        assert call.args[0] == "GET"
        assert call.args[1].startswith("https://attacker.example/xrpc/app.bsky.feed.searchPosts?")
        assert "headers" not in call.kwargs or "Authorization" not in call.kwargs["headers"]
        assert call.kwargs.get("public_only") is True
        guard = call.kwargs.get("redirect_guard")
        assert callable(guard)
        assert guard("https://attacker.example/xrpc/app.bsky.feed.searchPosts") is True
        assert guard("https://other.example/steal") is False
        assert guard("http://attacker.example/downgrade") is False
        assert guard("https://169.254.169.254/latest") is False

    def test_untrusted_host_401_does_not_trigger_bsky_session_refresh(self) -> None:
        config = {
            "BSKY_HANDLE": "user.bsky.social",
            "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
            "BSKY_SEARCH_HOST": "pds.example.com",
        }
        with mock.patch.object(
            bluesky.http,
            "request",
            side_effect=http.HTTPError("HTTP 401: Unauthorized", 401, ""),
        ) as req_mock:
            result = bluesky.search_bluesky("ai", "2026-06-01", "2026-06-30", config=config)

        assert req_mock.call_count == 1
        assert result["posts"] == []
        assert "pds.example.com" in result["error"]

    def test_trusted_bsky_subdomain_receives_bearer_with_redirect_guard(self) -> None:
        config = {
            "BSKY_HANDLE": "user.bsky.social",
            "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
            "BSKY_SEARCH_HOST": "staging.bsky.app",
        }
        with mock.patch.object(
            bluesky.http,
            "request",
            side_effect=[{"accessJwt": "tok-staging", "refreshJwt": "ref"}, {"posts": []}],
        ) as req_mock:
            result = bluesky.search_bluesky("ai", "2026-06-01", "2026-06-30", config=config)

        assert result == {"posts": []}
        assert req_mock.call_count == 2
        search_call = req_mock.call_args_list[1]
        assert search_call.args[1].startswith("https://staging.bsky.app/xrpc/app.bsky.feed.searchPosts?")
        assert search_call.kwargs["headers"] == {"Authorization": "Bearer tok-staging"}
        guard = search_call.kwargs["redirect_guard"]
        assert guard("https://staging.bsky.app/next") is True
        assert guard("https://evil.example/steal") is False
        assert guard("http://staging.bsky.app/downgrade") is False
