"""Tests for scripts/lib/transcribe.py — caption-free transcription fallback (U6)."""

from unittest import mock

from lib import health, http, transcribe


class TestPrerequisites:
    def test_missing_ffmpeg_degrades(self):
        with mock.patch.object(transcribe.shutil, "which", return_value=None):
            result = transcribe.transcribe_media("https://x/v", {"GROQ_API_KEY": "k"})
        assert result.ok is False
        assert "ffmpeg" in result.reason
        assert result.health.state == health.MISSING

    def test_no_provider_key_degrades(self):
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"):
            result = transcribe.transcribe_media("https://x/v", {})
        assert result.ok is False
        assert "provider" in result.reason
        assert result.health.state == health.MISSING

    def test_is_available(self):
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"):
            assert transcribe.is_available({"GROQ_API_KEY": "k"}) is True
            assert transcribe.is_available({}) is False


class TestTranscribeFlow:
    def _patches(self, chunks, post_side_effect):
        return [
            mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"),
            mock.patch.object(transcribe, "_acquire_audio", return_value="/tmp/audio.mp3"),
            mock.patch.object(transcribe, "_chunk_audio", return_value=chunks),
            mock.patch.object(transcribe, "_post_audio", side_effect=post_side_effect),
            mock.patch.object(transcribe.shutil, "rmtree"),
            mock.patch.object(transcribe.tempfile, "mkdtemp", return_value="/tmp/wd"),
        ]

    def test_under_limit_single_chunk(self):
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(transcribe, "_acquire_audio", return_value="/tmp/audio.mp3"), \
             mock.patch.object(transcribe, "_chunk_audio", return_value=["/tmp/audio.mp3"]), \
             mock.patch.object(transcribe, "_post_audio", return_value="hello world"), \
             mock.patch.object(transcribe.shutil, "rmtree"), \
             mock.patch.object(transcribe.tempfile, "mkdtemp", return_value="/tmp/wd"):
            result = transcribe.transcribe_media("https://x/v", {"GROQ_API_KEY": "k"})
        assert result.ok is True
        assert result.text == "hello world"
        assert result.chunks == 1
        assert result.provider == "groq"

    def test_over_limit_chunks_joined_in_order(self):
        chunks = ["/tmp/wd/chunk_000.mp3", "/tmp/wd/chunk_001.mp3"]
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(transcribe, "_acquire_audio", return_value="/tmp/audio.mp3"), \
             mock.patch.object(transcribe, "_chunk_audio", return_value=chunks), \
             mock.patch.object(transcribe, "_post_audio", side_effect=["part one", "part two"]), \
             mock.patch.object(transcribe.shutil, "rmtree"), \
             mock.patch.object(transcribe.tempfile, "mkdtemp", return_value="/tmp/wd"):
            result = transcribe.transcribe_media("https://x/v", {"GROQ_API_KEY": "k"})
        assert result.ok is True
        assert result.text == "part one\npart two"
        assert result.chunks == 2

    def test_provider_fallback_on_chunk(self):
        # groq raises, openai succeeds -> fallback used.
        def post(provider, path, key, timeout):
            if provider == "groq":
                raise RuntimeError("groq 500")
            return "via openai"
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(transcribe, "_acquire_audio", return_value="/tmp/audio.mp3"), \
             mock.patch.object(transcribe, "_chunk_audio", return_value=["/tmp/audio.mp3"]), \
             mock.patch.object(transcribe, "_post_audio", side_effect=post), \
             mock.patch.object(transcribe.shutil, "rmtree"), \
             mock.patch.object(transcribe.tempfile, "mkdtemp", return_value="/tmp/wd"):
            result = transcribe.transcribe_media(
                "https://x/v", {"GROQ_API_KEY": "k", "OPENAI_API_KEY": "o"})
        assert result.ok is True
        assert result.text == "via openai"
        assert result.provider == "openai"

    def test_all_providers_fail_degrades(self):
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"), \
             mock.patch.object(transcribe, "_acquire_audio", return_value="/tmp/audio.mp3"), \
             mock.patch.object(transcribe, "_chunk_audio", return_value=["/tmp/audio.mp3"]), \
             mock.patch.object(transcribe, "_post_audio", side_effect=RuntimeError("boom")), \
             mock.patch.object(transcribe.shutil, "rmtree"), \
             mock.patch.object(transcribe.tempfile, "mkdtemp", return_value="/tmp/wd"):
            result = transcribe.transcribe_media("https://x/v", {"GROQ_API_KEY": "k"})
        assert result.ok is False
        assert "all providers failed" in result.reason


class TestPostAudioUserAgent:
    """_post_audio must send a real User-Agent, not Python-urllib/3.x.

    Groq's Cloudflare edge 403s the default urllib fingerprint with
    "error code: 1010" (#983); a plain-urllib UA is a liability on every
    provider this helper serves.
    """

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"text": "hello"}'

    def _capture_headers(self, tmp_path):
        captured = {}

        def _capture(req, timeout=None):
            # urllib stores header keys capitalized ("User-agent").
            captured.update({k.lower(): v for k, v in req.headers.items()})
            return self._Resp()

        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake-audio")
        with mock.patch("urllib.request.urlopen", side_effect=_capture):
            transcribe._post_audio("groq", str(audio), "key", timeout=5.0)
        return captured

    def test_user_agent_is_canonical_skill_ua(self, tmp_path):
        captured = self._capture_headers(tmp_path)
        assert "user-agent" in captured
        assert captured["user-agent"] == http.USER_AGENT

    def test_user_agent_not_urllib_default(self, tmp_path):
        captured = self._capture_headers(tmp_path)
        assert not captured["user-agent"].startswith("Python-urllib")

    def test_user_agent_sent_for_openai_too(self, tmp_path):
        captured = {}

        def _capture(req, timeout=None):
            captured.update({k.lower(): v for k, v in req.headers.items()})
            return self._Resp()

        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake-audio")
        with mock.patch("urllib.request.urlopen", side_effect=_capture):
            transcribe._post_audio("openai", str(audio), "key", timeout=5.0)
        assert captured.get("user-agent") == http.USER_AGENT
