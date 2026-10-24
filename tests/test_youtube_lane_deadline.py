"""A YouTube lane that runs into the stream deadline keeps the videos it found."""

import json
import threading
import time

from lib import fetch_memo, health, pipeline, youtube_yt


def _plan(topic: str) -> dict:
    return {
        "intent": "comparison",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [{
            "label": "primary",
            "search_query": topic,
            "ranking_query": topic,
            "sources": ["youtube"],
            "weight": 1.0,
        }],
        "source_weights": {"youtube": 1.0},
    }


def _search_payload(cmd, videos: int) -> str:
    query = next(str(part) for part in cmd if "ytsearch" in str(part))
    slug = query.split(":", 1)[-1]
    return "\n".join(
        json.dumps({
            "id": f"video-{n:02d}",
            "title": f"Alpha video {n}",
            "description": slug,
            "channel": "Desk",
            "view_count": 1000 + n,
            "upload_date": "20261015",
        })
        for n in range(videos)
    ) + "\n"


def _run_lane(
    monkeypatch, *, transcript_seconds: float, later_search_seconds: float = 0.0,
    videos: int = 30, calls: dict | None = None, first_search_seconds: float = 0.0,
    sc_key: bool = False, failing_first_searches: int = 0,
):
    calls = {"search": 0, "transcript": 0} if calls is None else calls

    def fake_run(cmd, timeout=120, **kwargs):
        if any("ytsearch" in str(part) for part in cmd):
            calls["search"] += 1
            if calls["search"] <= failing_first_searches:
                raise youtube_yt.subproc.SubprocTimeout("search timed out")
            slow = first_search_seconds if calls["search"] == 1 else later_search_seconds
            if slow:
                time.sleep(min(slow, timeout))
                if timeout < slow:
                    raise youtube_yt.subproc.SubprocTimeout("search timed out")
            return youtube_yt.subproc.SubprocResult(
                returncode=0, stdout=_search_payload(cmd, videos), stderr="",
            )
        return youtube_yt.subproc.SubprocResult(returncode=1, stdout="", stderr="")

    released = threading.Event()

    def slow_transcript(video_id, *args, **kwargs):
        calls["transcript"] += 1
        released.wait(transcript_seconds)
        return f"{video_id} explains the comparison with specific numbers."

    def paid_search(*args, **kwargs):
        calls["sc_search"] = calls.get("sc_search", 0) + 1
        return {"items": []}

    monkeypatch.setattr(pipeline, "STREAM_FUTURE_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(pipeline, "THIN_RETRY_FUTURE_TIMEOUT_SECONDS", 3.0)
    monkeypatch.setattr(youtube_yt, "_LANE_RETURN_MARGIN_SECONDS", 1.0, raising=False)
    monkeypatch.setattr(pipeline, "which", lambda cmd: "/usr/bin/yt-dlp" if cmd == "yt-dlp" else None)
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    monkeypatch.setattr(youtube_yt, "fetch_transcript", slow_transcript)
    monkeypatch.setattr(youtube_yt, "enrich_with_comments", lambda items, token, **k: items)
    monkeypatch.setattr(youtube_yt, "search_youtube_sc", paid_search)
    config = {"EXCLUDE_SOURCES": "youtube_comments"}
    if sc_key:
        config["SCRAPECREATORS_API_KEY"] = "test-key"
    youtube_yt.reset_search_cache()
    fetch_memo.reset()
    try:
        report = pipeline.run(
            topic="Alpha",
            config=config,
            depth="deep",
            requested_sources=["youtube"],
            mock=False,
            external_plan=_plan("Alpha"),
            web_backend="none",
            lookback_days=30,
            as_of_date="2026-10-23",
            internal_subrun=True,
        )
    finally:
        released.set()
        youtube_yt.reset_search_cache()
        fetch_memo.reset()
    return report.source_status["youtube"]


def test_slow_transcripts_do_not_discard_found_videos(monkeypatch):
    outcome = _run_lane(monkeypatch, transcript_seconds=5.0)

    assert outcome.items_returned > 0, (outcome.state, outcome.detail)
    assert outcome.state == health.PARTIAL, (outcome.state, outcome.detail)
    assert "transcript" in (outcome.detail or "")


def test_slow_search_is_cut_off_at_the_lane_deadline(monkeypatch):
    outcome = _run_lane(monkeypatch, transcript_seconds=0.0, later_search_seconds=30.0)

    assert outcome.items_returned > 0, (outcome.state, outcome.detail)
    assert outcome.state != health.TIMEOUT, (outcome.state, outcome.detail)


def test_out_of_time_lane_does_no_more_youtube_work(monkeypatch):
    calls = {"search": 0, "transcript": 0}
    outcome = _run_lane(monkeypatch, transcript_seconds=4.0, videos=1, calls=calls)

    assert outcome.state == health.PARTIAL, (outcome.state, outcome.detail)
    assert calls["transcript"] == 1, calls


def test_lane_with_no_videos_at_the_deadline_is_not_retried(monkeypatch):
    calls = {"search": 0, "transcript": 0}
    outcome = _run_lane(monkeypatch, transcript_seconds=0.0, first_search_seconds=30.0, calls=calls)

    assert outcome.state == health.TIMEOUT, (outcome.state, outcome.detail)
    assert calls["search"] == 1, calls


def test_out_of_time_lane_buys_no_scrapecreators_backfill(monkeypatch):
    calls = {"search": 0, "transcript": 0}
    outcome = _run_lane(monkeypatch, transcript_seconds=4.0, videos=1, calls=calls, sc_key=True)

    assert outcome.state == health.PARTIAL, (outcome.state, outcome.detail)
    assert calls.get("sc_search", 0) == 0, calls
    assert calls["transcript"] == 1, calls


def test_transcript_past_the_lane_deadline_buys_no_scrapecreators_rescue(monkeypatch, tmp_path):
    paid = []
    monkeypatch.setattr(youtube_yt, "_LANE_RETURN_MARGIN_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(youtube_yt, "_sc_fetch_transcript", lambda *a, **k: paid.append(a) or "bought")
    with youtube_yt.sc_spend_until(lambda: False, deadline=time.monotonic() + 5):
        transcript = youtube_yt.fetch_transcript("vid", str(tmp_path), {}, "test-key")

    assert transcript is None
    assert paid == []


def test_lane_whose_transcripts_all_fail_at_the_deadline_is_out_of_time(monkeypatch):
    def failed_at_deadline(video_ids, **kwargs):
        while not youtube_yt._lane_out_of_time():
            time.sleep(0.01)
        return {vid: None for vid in video_ids}

    monkeypatch.setattr(youtube_yt, "_LANE_RETURN_MARGIN_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(youtube_yt, "search_youtube", lambda *a, **k: {
        "items": [{"video_id": "video-00", "engagement": {"views": 1000}}],
    })
    monkeypatch.setattr(youtube_yt, "fetch_transcripts_parallel", failed_at_deadline)
    with youtube_yt.sc_spend_until(lambda: False, deadline=time.monotonic() + 0.5):
        result = youtube_yt.search_and_transcribe("Alpha", "2026-09-23", "2026-10-23")

    assert result["items"], result
    assert result.get("out_of_time"), result


def test_retry_that_runs_out_of_time_does_no_more_youtube_work(monkeypatch):
    calls = {"search": 0, "transcript": 0}
    outcome = _run_lane(
        monkeypatch, transcript_seconds=4.0, videos=1, calls=calls, failing_first_searches=3,
    )

    assert calls["search"] > 3, calls
    assert outcome.state == health.PARTIAL, (outcome.state, outcome.detail)
    assert calls["transcript"] == 1, calls


def test_lane_that_finishes_in_time_is_not_partial(monkeypatch):
    outcome = _run_lane(monkeypatch, transcript_seconds=0.0)

    assert outcome.items_returned > 0, (outcome.state, outcome.detail)
    assert outcome.state != health.PARTIAL, (outcome.state, outcome.detail)
    assert outcome.state != health.TIMEOUT, (outcome.state, outcome.detail)
