"""Follow-up to #1319: paid YouTube transcript spend stops at the target (#1314, #1315)."""

import time
from unittest import mock

import pytest
from lib import youtube_yt


def _ytdlp_rate_limited(video_id, temp_dir, status=None, fast_fail=False):
    if status is not None:
        status["ytdlp_error"] = "HTTP Error 429: Too Many Requests"
    return None


@pytest.fixture
def _all_ytdlp_fetches_fail():
    with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
         mock.patch.object(youtube_yt, "_ytdlp_ssh_host", return_value=None), \
         mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp", side_effect=_ytdlp_rate_limited):
        yield


@pytest.mark.usefixtures("_all_ytdlp_fetches_fail")
def test_parallel_sc_rescues_stop_at_the_cap():
    """24 candidates (3x a deep target of 8) all failing in yt-dlp pay for 8 rescues, not 24."""
    with mock.patch.object(
        youtube_yt, "_sc_fetch_transcript", side_effect=lambda vid, token: f"text {vid}",
    ) as sc_mock:
        results = youtube_yt.fetch_transcripts_parallel(
            [f"v{i}" for i in range(24)], token="sc-key", max_sc_rescues=8,
        )

    assert sc_mock.call_count == 8
    assert sum(1 for value in results.values() if value) == 8


@pytest.mark.usefixtures("_all_ytdlp_fetches_fail")
def test_parallel_without_a_cap_keeps_rescuing_every_failure():
    with mock.patch.object(
        youtube_yt, "_sc_fetch_transcript", side_effect=lambda vid, token: f"text {vid}",
    ) as sc_mock:
        youtube_yt.fetch_transcripts_parallel([f"v{i}" for i in range(5)], token="sc-key")

    assert sc_mock.call_count == 5


def test_search_and_transcribe_caps_rescues_at_the_depth_target():
    items = [
        {"video_id": f"v{i}", "title": f"t{i}", "date": "2026-06-15",
         "engagement": {"views": 100 - i}}
        for i in range(30)
    ]
    with mock.patch.object(youtube_yt, "expand_youtube_queries", return_value=["q"]), \
         mock.patch.object(youtube_yt, "search_youtube", return_value={"items": items}), \
         mock.patch.object(youtube_yt, "fetch_transcripts_parallel", return_value={}) as parallel:
        youtube_yt.search_and_transcribe(
            "topic", "2026-06-01", "2026-07-01", depth="deep", token="sc-key",
        )

    assert parallel.call_args.kwargs["max_sc_rescues"] == youtube_yt.TRANSCRIPT_LIMITS["deep"]


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("en,es,pt", "en-orig,es-orig,pt-orig,en,es,pt"),
        ("fr,en-orig,en", "fr-orig,en-orig,fr,en"),
        ("en", "en-orig,en"),
        ("en-orig", "en-orig"),
        ("en-orig,es", "en-orig,es-orig,es"),
    ],
)
def test_every_orig_track_is_requested_before_any_plain_code(
    configured, expected, monkeypatch: pytest.MonkeyPatch,
):
    """A Spanish video dubbed into English reaches `es-orig` before the translated `en` can 429."""
    monkeypatch.setenv("LAST30DAYS_YT_SUB_LANGS", configured)
    assert youtube_yt._ytdlp_request_langs() == expected


def _sc_search_items(count):
    return [
        {"id": f"v{i}", "title": f"t{i}", "date": "2026-06-15", "views": 100 - i}
        for i in range(count)
    ]


def test_free_transcripts_count_toward_the_backfill_target():
    """Default depth targets 2; with 2 free transcripts in hand the backfill pays for none."""
    with mock.patch.object(youtube_yt, "_sc_youtube_search", return_value=_sc_search_items(6)), \
         mock.patch.object(youtube_yt, "_sc_fetch_transcript") as transcripts:
        youtube_yt.search_youtube_sc(
            "topic", "2026-06-01", "2026-07-01", depth="default", token="sc-key",
            skip_transcript_ids={"free1", "free2"},
        )

    transcripts.assert_not_called()


def test_empty_paid_transcripts_do_not_count_toward_the_target():
    target = youtube_yt.TRANSCRIPT_LIMITS["deep"]
    calls = []

    def _every_third_empty(vid, token):
        calls.append(vid)
        return "" if len(calls) % 3 == 0 else f"text {vid}"

    with mock.patch.object(youtube_yt, "_sc_youtube_search", return_value=_sc_search_items(30)), \
         mock.patch.object(youtube_yt, "_sc_fetch_transcript", side_effect=_every_third_empty):
        result = youtube_yt.search_youtube_sc(
            "topic", "2026-06-01", "2026-07-01", depth="deep", token="sc-key",
        )

    assert sum(1 for item in result["items"] if item.get("transcript_snippet")) == target
    assert len(calls) <= target * 3


@pytest.mark.usefixtures("_all_ytdlp_fetches_fail")
def test_an_empty_rescue_gives_its_slot_back():
    """A paid miss does not use up the cap, so a later video still gets rescued."""
    with mock.patch.object(
        youtube_yt, "_sc_fetch_transcript",
        side_effect=lambda vid, token: "" if vid == "v0" else f"text {vid}",
    ):
        results = youtube_yt.fetch_transcripts_parallel(
            [f"v{i}" for i in range(4)], token="sc-key", max_sc_rescues=2, max_workers=1,
        )

    assert sum(1 for value in results.values() if value) == 2


def test_slots_held_by_slow_empty_rescues_go_to_waiting_videos():
    """Two slow paid misses hold both default-depth slots; later videos wait for them, then fill the target."""
    def _ytdlp(video_id, temp_dir, status=None, fast_fail=False):
        if video_id not in ("v0", "v1"):
            time.sleep(0.05)
        return _ytdlp_rate_limited(video_id, temp_dir, status=status)

    def _sc(vid, token):
        if vid in ("v0", "v1"):
            time.sleep(0.3)
            return ""
        return f"text {vid}"

    with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
         mock.patch.object(youtube_yt, "_ytdlp_ssh_host", return_value=None), \
         mock.patch.object(youtube_yt, "_fetch_transcript_ytdlp", side_effect=_ytdlp), \
         mock.patch.object(youtube_yt, "_sc_fetch_transcript", side_effect=_sc) as sc_mock:
        results = youtube_yt.fetch_transcripts_parallel(
            [f"v{i}" for i in range(6)], token="sc-key", max_sc_rescues=2, max_workers=5,
        )

    assert sum(1 for value in results.values() if value) == 2
    assert sc_mock.call_count == 4
