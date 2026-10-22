"""LAST30DAYS_YOUTUBE_BACKEND=yt-dlp keeps YouTube keyless with a key set (#1316)."""

from pathlib import Path
from unittest import mock

import pytest
from lib import doctor, env, pipeline, schema, youtube_yt

KEYED = {"SCRAPECREATORS_API_KEY": "dummy-key"}
PINNED = {**KEYED, "LAST30DAYS_YOUTUBE_BACKEND": "yt-dlp"}


def _youtube_stream(config):
    return pipeline._retrieve_stream_impl(
        topic="Vuori",
        subquery=schema.SubQuery(
            label="q", search_query="Vuori", ranking_query="Vuori", sources=["youtube"],
        ),
        source="youtube",
        config=config,
        depth="default",
        date_range=("2026-06-01", "2026-07-01"),
        runtime=schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
        ),
        mock=False,
    )


@pytest.mark.parametrize("value", ["yt-dlp", "YT-DLP", " ytdlp "])
def test_pin_turns_off_youtube_sc_with_key_set(value):
    assert env.is_youtube_sc_available({**KEYED, "LAST30DAYS_YOUTUBE_BACKEND": value}) is False


@pytest.mark.parametrize("value", [None, "", "auto", "scrapecreators", "bogus"])
def test_unset_or_unknown_pin_keeps_auto_behavior(value):
    config = {**KEYED, "LAST30DAYS_YOUTUBE_BACKEND": value}
    assert env.youtube_backend_pin(config) == ""
    assert env.is_youtube_sc_available(config) is True


def test_pinned_without_ytdlp_disables_sc_comments_and_the_source():
    config = {**PINNED, "INCLUDE_SOURCES": "youtube_comments"}
    with mock.patch.object(env, "is_ytdlp_available", return_value=False), \
         mock.patch.object(pipeline, "which", return_value=None):
        assert env.is_youtube_comments_available(config) is False
        assert "youtube" not in pipeline.available_sources(config)


def test_pinned_stream_makes_no_scrapecreators_calls():
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(
            youtube_yt, "search_and_transcribe", return_value={"items": []},
        ) as free_search,
        mock.patch.object(youtube_yt, "search_youtube_sc") as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, _artifact = _youtube_stream(dict(PINNED))

    assert items == []
    sc_search.assert_not_called()
    assert free_search.call_args.kwargs["token"] is None


def test_pinned_stream_enriches_comments_without_the_key():
    free = {"video_id": "a", "title": "Vuori review", "transcript_snippet": "t"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc") as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=True),
        mock.patch.object(youtube_yt, "enrich_with_comments") as comments,
    ):
        _youtube_stream(dict(PINNED))

    sc_search.assert_not_called()
    assert comments.call_args.kwargs["token"] == ""


def test_unpinned_stream_still_passes_the_key_to_comments():
    free = {"video_id": "a", "title": "Vuori review", "transcript_snippet": "t"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(
            youtube_yt, "search_and_transcribe", return_value={"items": [free] * 3},
        ),
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=True),
        mock.patch.object(youtube_yt, "enrich_with_comments") as comments,
    ):
        _youtube_stream(dict(KEYED))

    assert comments.call_args.kwargs["token"] == "dummy-key"


def test_fast_attempts_override_retries_keyed_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LAST30DAYS_YT_TRANSCRIPT_FAST_ATTEMPTS", "3")
    monkeypatch.setenv("LAST30DAYS_YT_TRANSCRIPT_FAST_TIMEOUT", "")
    status: dict = {}
    with mock.patch.object(
        youtube_yt.subproc, "run_with_timeout", side_effect=youtube_yt.subproc.SubprocTimeout,
    ) as run_mock, mock.patch.object(youtube_yt.time, "sleep"):
        youtube_yt._fetch_transcript_ytdlp("vid", str(tmp_path), status=status, fast_fail=True)

    assert run_mock.call_count == 3
    assert {c.kwargs["timeout"] for c in run_mock.call_args_list} == {12.0}


@pytest.mark.parametrize("value", ["", "0", "-1", "abc", "2.5"])
def test_invalid_fast_attempts_keep_one_attempt(value, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LAST30DAYS_YT_TRANSCRIPT_FAST_ATTEMPTS", value)
    assert youtube_yt._transcript_fast_attempts() == 1


def test_pin_changes_doctor_cache_fingerprint():
    assert doctor._config_fingerprint(dict(KEYED)) != doctor._config_fingerprint(dict(PINNED))
