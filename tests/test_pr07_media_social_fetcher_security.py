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
import socket
import urllib.error
import urllib.request
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
        cmds: list[list[str]] = []

        def fake_run(cmd, timeout=120.0):
            cmds.append(cmd)
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

        ytdlp_cmd, ffmpeg_cmd = cmds
        assert ytdlp_cmd[0] == "yt-dlp"
        dash_idx = ytdlp_cmd.index("--")
        assert ytdlp_cmd[dash_idx + 1] == url
        flags = ytdlp_cmd[:dash_idx]
        assert "--ignore-config" in flags
        assert "--no-cookies-from-browser" in flags
        assert "--no-playlist" in flags
        assert "--no-live-from-start" in flags
        assert flags[flags.index("--match-filter") + 1] == "!is_live"
        assert flags[flags.index("--max-filesize") + 1] == transcribe.MAX_DOWNLOAD_SIZE_ARG

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

        def fake_run(cmd, timeout=120.0):
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
