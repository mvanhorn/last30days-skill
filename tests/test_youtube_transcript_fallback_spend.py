from pathlib import Path
from unittest import mock

import pytest
from lib import env, youtube_yt


def test_youtube_tuning_loaded_from_env_file_reaches_lazy_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / ".env"
    config_file.write_text(
        "LAST30DAYS_YT_SUB_LANGS=en\nLAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT=25\n",
        encoding="utf-8",
    )
    config_file.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", config_file)
    monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("LAST30DAYS_YT_SUB_LANGS", raising=False)
    monkeypatch.delenv("LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT", raising=False)
    monkeypatch.chdir(tmp_path)

    with (
        mock.patch.object(env, "_load_keychain", return_value={}),
        mock.patch.object(env, "_load_pass", return_value={}),
    ):
        config = env.get_config()

    assert config["LAST30DAYS_YT_SUB_LANGS"] == "en"
    assert config["LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT"] == "25"
    assert youtube_yt._ytdlp_sub_langs() == "en"
    assert youtube_yt._transcript_fast_timeout() == 25.0


@pytest.mark.parametrize(
    "value",
    ["", "not-a-number", "0", "-1", "nan", "inf"],
)
def test_invalid_or_empty_fast_timeout_uses_default(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT", value)

    assert youtube_yt._transcript_fast_timeout() == 12.0


def test_timeout_reuses_completed_vtt_without_paid_fallback(
    tmp_path: Path,
) -> None:
    (tmp_path / "video123.en.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nusable transcript\n",
        encoding="utf-8",
    )
    status: dict[str, str] = {}

    with mock.patch.object(
        youtube_yt.subproc,
        "run_with_timeout",
        side_effect=youtube_yt.subproc.SubprocTimeout,
    ) as run_mock:
        transcript = youtube_yt._fetch_transcript_ytdlp(
            "video123",
            str(tmp_path),
            status=status,
            fast_fail=True,
        )

    assert transcript is not None
    assert "usable transcript" in transcript
    assert "ytdlp_error" not in status
    assert run_mock.call_count == 1


def test_fast_timeout_override_controls_keyed_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT", "25")
    status: dict[str, str] = {}

    with mock.patch.object(
        youtube_yt.subproc,
        "run_with_timeout",
        side_effect=youtube_yt.subproc.SubprocTimeout,
    ) as run_mock:
        transcript = youtube_yt._fetch_transcript_ytdlp(
            "video123",
            str(tmp_path),
            status=status,
            fast_fail=True,
        )

    assert transcript is None
    assert run_mock.call_args.kwargs["timeout"] == 25.0
    assert status["ytdlp_error"] == "timed out after 25.0s"
