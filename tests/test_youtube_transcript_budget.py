"""YouTube keeps time for transcripts and stops hammering yt-dlp after a block."""

import json
import threading

import pytest

from lib import subproc, youtube_yt


_START = 1_000.0
# Lane return point is the spend deadline minus the 10s hand-back margin.
_INITIAL_SECONDS_LEFT = 170.0


def _item(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "title": "Placeholder video",
        "date": "2026-10-01",
        "engagement": {"views": 10},
    }


def _install_clock(monkeypatch, now: float) -> dict:
    clock = {"now": now}
    monkeypatch.setattr(youtube_yt.time, "monotonic", lambda: clock["now"])
    return clock


def _run_expanded_searches(
    monkeypatch,
    *,
    depth: str,
    seconds_left_after_first: float,
    first_items: list,
):
    clock = _install_clock(monkeypatch, _START)
    searches = []

    def fake_search(query, *_args, **_kwargs):
        searches.append(query)
        if len(searches) == 1:
            used = _INITIAL_SECONDS_LEFT - seconds_left_after_first
            clock["now"] += used
            return {"items": list(first_items)}
        return {"items": [_item("later")]}

    monkeypatch.setattr(
        youtube_yt, "expand_youtube_queries", lambda *_a, **_k: ["one", "two", "three"],
    )
    def fake_transcripts(video_ids, **_kwargs):
        return {video_id: "placeholder transcript words" for video_id in video_ids}

    monkeypatch.setattr(youtube_yt, "search_youtube", fake_search)
    monkeypatch.setattr(youtube_yt, "fetch_transcripts_parallel", fake_transcripts)
    deadline = _START + youtube_yt._LANE_RETURN_MARGIN_SECONDS + _INITIAL_SECONDS_LEFT
    with youtube_yt.sc_spend_until(lambda: False, deadline=deadline):
        result = youtube_yt.search_and_transcribe(
            "placeholder topic", "2026-09-01", "2026-10-23", depth=depth,
        )
    return searches, result


def test_deep_search_does_not_start_when_time_left_is_under_the_reserve(monkeypatch):
    searches, result = _run_expanded_searches(
        monkeypatch, depth="deep", seconds_left_after_first=70.0, first_items=[_item("vidA")],
    )

    assert searches == ["one"]
    assert "out_of_time" not in result


def test_default_search_still_starts_when_its_smaller_reserve_is_left(monkeypatch):
    searches, result = _run_expanded_searches(
        monkeypatch, depth="default", seconds_left_after_first=70.0, first_items=[_item("vidA")],
    )

    assert searches == ["one", "two", "three"]
    assert "out_of_time" not in result


def test_default_search_does_not_start_when_time_left_is_under_its_reserve(monkeypatch):
    searches, _result = _run_expanded_searches(
        monkeypatch, depth="default", seconds_left_after_first=50.0, first_items=[_item("vidA")],
    )

    assert searches == ["one"]


def test_empty_first_search_still_runs_the_next_one_under_the_reserve(monkeypatch):
    searches, _result = _run_expanded_searches(
        monkeypatch, depth="deep", seconds_left_after_first=50.0, first_items=[],
    )

    # No videos yet, so the next search still runs. Once it finds one, the
    # reserve stops a further search.
    assert searches == ["one", "two"]


def test_without_a_lane_deadline_every_expanded_search_runs(monkeypatch):
    searches = []

    def fake_search(query, *_args, **_kwargs):
        searches.append(query)
        return {"items": [_item("vidA")]}

    monkeypatch.setattr(
        youtube_yt, "expand_youtube_queries", lambda *_a, **_k: ["one", "two", "three"],
    )
    monkeypatch.setattr(youtube_yt, "search_youtube", fake_search)
    monkeypatch.setattr(
        youtube_yt, "fetch_transcripts_parallel",
        lambda video_ids, **_kwargs: {video_id: "placeholder transcript words" for video_id in video_ids},
    )

    youtube_yt.search_and_transcribe(
        "placeholder topic", "2026-09-01", "2026-10-23", depth="deep",
    )

    assert searches == ["one", "two", "three"]


def _transcript_commands(monkeypatch, stderr: str):
    calls = []

    def fake_run(cmd, timeout=30, **_kwargs):
        video_id = next(
            (str(part).rsplit("v=", 1)[-1] for part in cmd if "watch?v=" in str(part)),
            "",
        )
        calls.append(video_id)
        return subproc.SubprocResult(returncode=1, stdout="", stderr=stderr)

    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(youtube_yt, "_transcript_backoff", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    return calls


@pytest.mark.parametrize(
    "stderr",
    [
        "ERROR: Sign in to confirm you're not a bot",
        "ERROR: HTTP Error 429: Too Many Requests",
    ],
)
def test_after_a_block_later_transcripts_skip_ytdlp_for_scrapecreators(monkeypatch, stderr):
    ytdlp_calls = _transcript_commands(monkeypatch, stderr)
    sc_calls = []

    def fake_sc(video_id, _token):
        sc_calls.append(video_id)
        return "placeholder transcript words about the topic"

    monkeypatch.setattr(youtube_yt, "_sc_fetch_transcript", fake_sc)
    monkeypatch.setattr(
        youtube_yt, "_fetch_transcript_direct",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("direct HTTP after a block")),
    )

    results = youtube_yt.fetch_transcripts_parallel(
        ["vidA", "vidB", "vidC"], max_workers=1, token="test-key",
    )

    assert ytdlp_calls == ["vidA"]
    assert sc_calls == ["vidA", "vidB", "vidC"]
    assert all(results[video_id] for video_id in ("vidA", "vidB", "vidC"))


def test_workers_waiting_for_a_slot_skip_ytdlp_after_a_block(monkeypatch):
    # Five workers, two slots. The two that hold a slot must not finish until
    # the other three are queued in acquire, so every worker has already passed
    # the check that happens before the wait.
    videos = ["vidA", "vidB", "vidC", "vidD", "vidE"]
    state = {"inside": 0, "waiting": 0, "timed_acquires": 0}
    lock = threading.Lock()
    queued = threading.Event()
    ytdlp_calls = []

    def _note_progress():
        if state["inside"] >= 2 and state["waiting"] >= 3:
            queued.set()

    class _Gate:
        def __init__(self):
            self._sem = threading.Semaphore(2)

        def acquire(self, blocking=True, timeout=None):
            with lock:
                state["waiting"] += 1
                if timeout is not None:
                    state["timed_acquires"] += 1
                _note_progress()
            try:
                if timeout is None:
                    return self._sem.acquire(blocking=blocking)
                return self._sem.acquire(blocking=blocking, timeout=timeout)
            finally:
                with lock:
                    state["waiting"] -= 1

        def release(self):
            self._sem.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, *_args):
            self.release()
            return False

    def fake_run(cmd, timeout=30, **_kwargs):
        video_id = next(
            (str(part).rsplit("v=", 1)[-1] for part in cmd if "watch?v=" in str(part)),
            "",
        )
        with lock:
            state["inside"] += 1
            inside = state["inside"]
            _note_progress()
        if inside <= 2 and not queued.wait(5):
            raise AssertionError("transcript workers did not queue for a yt-dlp slot")
        ytdlp_calls.append(video_id)
        return subproc.SubprocResult(
            returncode=1,
            stdout="",
            stderr="ERROR: Sign in to confirm you're not a bot",
        )

    def fake_sc(video_id, _token):
        sc_calls.append(video_id)
        return "placeholder transcript words about the topic"

    sc_calls = []
    monkeypatch.setattr(youtube_yt, "_ytdlp_slots", _Gate())
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    monkeypatch.setattr(youtube_yt, "_sc_fetch_transcript", fake_sc)
    monkeypatch.setattr(
        youtube_yt, "_fetch_transcript_direct",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("direct HTTP after a block")),
    )

    deadline = youtube_yt.time.monotonic() + youtube_yt._LANE_RETURN_MARGIN_SECONDS + 100.0
    with youtube_yt.sc_spend_until(lambda: False, deadline=deadline):
        results = youtube_yt.fetch_transcripts_parallel(
            videos, max_workers=5, token="test-key",
        )

    assert state["timed_acquires"] == 5
    assert len(ytdlp_calls) == 2
    assert sorted(sc_calls) == videos
    assert all(results[video_id] for video_id in videos)


def test_timeout_does_not_send_later_transcripts_straight_to_scrapecreators(monkeypatch):
    ytdlp_calls = []

    def fake_run(cmd, timeout=30, **_kwargs):
        video_id = next(
            (str(part).rsplit("v=", 1)[-1] for part in cmd if "watch?v=" in str(part)),
            "",
        )
        ytdlp_calls.append(video_id)
        raise subproc.SubprocTimeout(f"Command yt-dlp timed out after {timeout}s")

    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    monkeypatch.setattr(
        youtube_yt, "_sc_fetch_transcript",
        lambda _video_id, _token: "placeholder transcript words about the topic",
    )

    youtube_yt.fetch_transcripts_parallel(
        ["vidA", "vidB"], max_workers=1, token="test-key",
    )

    assert ytdlp_calls == ["vidA", "vidB"]


def test_block_without_a_scrapecreators_key_still_tries_ytdlp(monkeypatch):
    ytdlp_calls = _transcript_commands(
        monkeypatch, "ERROR: Sign in to confirm you're not a bot",
    )
    sc_calls = []
    monkeypatch.setattr(
        youtube_yt, "_sc_fetch_transcript",
        lambda video_id, _token: sc_calls.append(video_id),
    )

    youtube_yt.fetch_transcripts_parallel(
        ["vidA", "vidB"], max_workers=1, token=None,
    )

    assert ytdlp_calls == ["vidA", "vidA", "vidA", "vidB", "vidB", "vidB"]
    assert sc_calls == []


def test_search_bot_check_sends_transcripts_straight_to_scrapecreators(monkeypatch):
    # yt-dlp prints a video, then exits on a bot-check. search_youtube keeps
    # the video and does not return an error, so the block has to come from stderr.
    ytdlp_calls = []
    direct_calls = []
    sc_calls = []
    video = {
        "id": "vidA",
        "title": "Placeholder video",
        "view_count": 10,
        "like_count": 1,
        "comment_count": 0,
        "upload_date": "20261001",
        "description": "placeholder",
        "channel": "Placeholder",
    }

    def fake_run(cmd, timeout=30, **_kwargs):
        blob = " ".join(str(part) for part in cmd)
        if "ytsearch" in blob:
            return subproc.SubprocResult(
                returncode=1,
                stdout=json.dumps(video) + "\n",
                stderr="ERROR: Sign in to confirm you're not a bot",
            )
        video_id = next(
            (str(part).rsplit("v=", 1)[-1] for part in cmd if "watch?v=" in str(part)),
            "",
        )
        ytdlp_calls.append(video_id)
        return subproc.SubprocResult(
            returncode=1,
            stdout="",
            stderr="ERROR: Sign in to confirm you're not a bot",
        )

    def fake_sc(video_id, _token):
        sc_calls.append(video_id)
        return "placeholder transcript words about the topic"

    monkeypatch.setattr(youtube_yt, "expand_youtube_queries", lambda *_a, **_k: ["one"])
    monkeypatch.setattr(youtube_yt, "is_ytdlp_installed", lambda: True)
    monkeypatch.setattr(youtube_yt, "_ytdlp_ssh_host", lambda: None)
    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    monkeypatch.setattr(
        youtube_yt, "_fetch_transcript_direct",
        lambda video_id, *_a, **_k: direct_calls.append(video_id),
    )
    monkeypatch.setattr(youtube_yt, "_sc_fetch_transcript", fake_sc)
    youtube_yt.reset_search_cache()
    try:
        result = youtube_yt.search_and_transcribe(
            "placeholder topic", "2026-09-01", "2026-10-23", depth="default", token="test-key",
        )
    finally:
        youtube_yt.reset_search_cache()

    assert [item["video_id"] for item in result["items"]] == ["vidA"]
    assert "error" not in result
    assert ytdlp_calls == []
    assert direct_calls == []
    assert sc_calls == ["vidA"]
    assert result["items"][0]["transcript_snippet"]


def test_expired_lane_names_the_deadline_instead_of_the_fast_timeout(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(youtube_yt.time, "monotonic", lambda: _START)
    status = {}
    with youtube_yt.sc_spend_until(lambda: False, deadline=_START):
        transcript = youtube_yt._fetch_transcript_ytdlp(
            "vidA", str(tmp_path), status=status, fast_fail=True,
        )

    assert transcript is None
    assert "lane deadline" in status["ytdlp_error"].lower()
    assert "12" not in status["ytdlp_error"]
    logged = capsys.readouterr().err
    assert "lane deadline" in logged.lower()
    assert "timed out after 12" not in logged


def test_short_lane_budget_is_not_logged_as_the_fast_timeout(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(youtube_yt.time, "monotonic", lambda: _START)
    seen = {}

    def fake_run(_cmd, timeout=30, **_kwargs):
        seen["timeout"] = timeout
        raise subproc.SubprocTimeout(f"Command yt-dlp timed out after {timeout}s")

    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    status = {}
    # 3 seconds of lane time, under the 12s fast attempt.
    with youtube_yt.sc_spend_until(
        lambda: False,
        deadline=_START + youtube_yt._LANE_RETURN_MARGIN_SECONDS + 3.0,
    ):
        transcript = youtube_yt._fetch_transcript_ytdlp(
            "vidA", str(tmp_path), status=status, fast_fail=True,
        )

    assert transcript is None
    assert seen["timeout"] == pytest.approx(3.0)
    assert "lane deadline" in status["ytdlp_error"].lower()
    assert "timed out after 12" not in capsys.readouterr().err


def test_full_attempt_timeout_keeps_its_own_message(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube_yt.time, "monotonic", lambda: _START)

    def fake_run(_cmd, timeout=30, **_kwargs):
        raise subproc.SubprocTimeout(f"Command yt-dlp timed out after {timeout}s")

    monkeypatch.setattr(youtube_yt.subproc, "run_with_timeout", fake_run)
    status = {}
    with youtube_yt.sc_spend_until(
        lambda: False,
        deadline=_START + youtube_yt._LANE_RETURN_MARGIN_SECONDS + 100.0,
    ):
        youtube_yt._fetch_transcript_ytdlp(
            "vidA", str(tmp_path), status=status, fast_fail=True,
        )

    assert status["ytdlp_error"] == "timed out after 12.0s"
