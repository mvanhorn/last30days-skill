"""Tests for scripts/lib/transcribe.py — caption-free transcription fallback (U6)."""

from unittest import mock

from lib import env, health, transcribe


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
        def post(provider, path, key, timeout, config=None):
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


class TestLocalProvider:
    """Self-hosted OpenAI-compatible transcription (LAST30DAYS_WHISPER_URL).

    Covers the ordering guarantee, config-driven endpoint resolution, and the
    unauthenticated-POST path that self-hosted servers need.
    """

    LOCAL_URL = "http://127.0.0.1:8910/v1/audio/transcriptions"

    def test_local_is_preferred_over_hosted_providers(self):
        config = {
            "LAST30DAYS_WHISPER_URL": self.LOCAL_URL,
            "GROQ_API_KEY": "g",
            "OPENAI_API_KEY": "o",
        }
        assert [name for name, _ in env.transcription_providers(config)] == [
            "local",
            "groq",
            "openai",
        ]

    def test_local_absent_without_url(self):
        assert env.transcription_providers({"GROQ_API_KEY": "g"}) == [("groq", "g")]

    def test_local_carries_empty_key_by_default(self):
        assert env.transcription_providers({"LAST30DAYS_WHISPER_URL": self.LOCAL_URL}) == [
            ("local", "")
        ]

    def test_local_key_used_when_gateway_needs_one(self):
        config = {"LAST30DAYS_WHISPER_URL": self.LOCAL_URL, "LAST30DAYS_WHISPER_KEY": "tok"}
        assert env.transcription_providers(config) == [("local", "tok")]

    def test_endpoint_and_model_resolution(self):
        config = {"LAST30DAYS_WHISPER_URL": self.LOCAL_URL}
        assert transcribe._endpoint_for("local", config) == self.LOCAL_URL
        assert transcribe._model_for("local", config) == transcribe._LOCAL_DEFAULT_MODEL
        pinned = dict(config, LAST30DAYS_WHISPER_MODEL="ggml-large-v3")
        assert transcribe._model_for("local", pinned) == "ggml-large-v3"

    def test_hosted_endpoints_unchanged(self):
        assert transcribe._endpoint_for("groq", {}) == transcribe._PROVIDER_ENDPOINTS["groq"]
        assert transcribe._model_for("openai", {}) == transcribe._PROVIDER_MODELS["openai"]

    def test_is_available_with_local_only(self):
        with mock.patch.object(transcribe.shutil, "which", return_value="/usr/bin/ffmpeg"):
            assert transcribe.is_available({"LAST30DAYS_WHISPER_URL": self.LOCAL_URL}) is True

    def _capture_post(self, tmp_path, api_key, config):
        """POST one tiny file through _post_audio; return the captured Request."""
        audio = tmp_path / "clip.mp3"
        audio.write_bytes(b"\x00\x01")
        captured = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"text": "hello"}'

        def _urlopen(req, timeout=None):
            captured["req"] = req
            return _Resp()

        with mock.patch("urllib.request.urlopen", side_effect=_urlopen):
            text = transcribe._post_audio("local", str(audio), api_key, 5.0, config=config)
        return text, captured["req"]

    def test_post_omits_authorization_when_unauthenticated(self, tmp_path):
        text, req = self._capture_post(
            tmp_path, "", {"LAST30DAYS_WHISPER_URL": self.LOCAL_URL}
        )
        assert text == "hello"
        assert req.full_url == self.LOCAL_URL
        # An empty bearer makes some self-hosted servers 401 outright.
        assert not any(h.lower() == "authorization" for h in req.headers)

    def test_post_sends_authorization_when_key_present(self, tmp_path):
        _, req = self._capture_post(
            tmp_path, "tok", {"LAST30DAYS_WHISPER_URL": self.LOCAL_URL}
        )
        assert req.headers["Authorization"] == "Bearer tok"

    def test_post_returns_none_without_configured_url(self, tmp_path):
        audio = tmp_path / "clip.mp3"
        audio.write_bytes(b"\x00\x01")
        assert transcribe._post_audio("local", str(audio), "", 5.0, config={}) is None
