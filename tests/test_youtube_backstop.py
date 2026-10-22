import threading
from unittest import mock

from lib import health, http, pipeline, schema, youtube_yt


def _youtube_stream(config, *, wrapped=False):
    retrieve = pipeline._retrieve_stream if wrapped else pipeline._retrieve_stream_impl
    return retrieve(
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


def test_one_free_video_triggers_backstop_and_preserves_its_transcript():
    free = {"video_id": "free", "transcript_snippet": "free transcript"}
    paid = {"video_id": "paid", "transcript_snippet": "paid transcript"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(
            youtube_yt, "search_youtube_sc",
            return_value={"items": [{"video_id": "free", "transcript_snippet": ""}, paid]},
        ) as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

    assert sc_search.call_count == 1
    assert [item["video_id"] for item in items] == ["free", "paid"]
    assert items[0]["transcript_snippet"] == "free transcript"
    assert "added 1" in artifact["_source_outcome_detail"]
    assert "_source_outcome" not in artifact


def test_two_free_videos_merge_duplicate_transcript_without_replacing_free_metadata():
    free = [
        {"video_id": "a", "title": "free title", "transcript_snippet": ""},
        {"video_id": "b", "title": "other", "transcript_snippet": "free transcript"},
    ]
    sc = [
        {"video_id": "a", "title": "paid title", "transcript_snippet": "rescued"},
        {"video_id": "c", "title": "new", "transcript_snippet": ""},
    ]
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": free}),
        mock.patch.object(youtube_yt, "search_youtube_sc", return_value={"items": sc}) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

    assert search.call_count == 1
    assert [item["video_id"] for item in items] == ["a", "b", "c"]
    assert items[0]["title"] == "free title"
    assert items[0]["transcript_snippet"] == "rescued"
    assert items[1]["transcript_snippet"] == "free transcript"
    assert free[0]["transcript_snippet"] == ""
    assert "added 1" in artifact["_source_outcome_detail"]


def test_backfill_spends_transcript_calls_only_on_missing_or_new_videos():
    free = [
        {"video_id": "ready", "title": "free ready", "transcript_snippet": "free transcript"},
        {"video_id": "missing", "title": "free missing", "transcript_snippet": "   "},
    ]
    video_ids = ["ready", "missing", *(f"new{i}" for i in range(5))]
    raw_search = [
        {
            "id": video_id, "title": f"SC {video_id}", "date": "2026-06-15",
            "views": 100 - index,
        }
        for index, video_id in enumerate(video_ids)
    ]
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": free}),
        mock.patch.object(youtube_yt, "_sc_youtube_search", return_value=raw_search) as search,
        mock.patch.object(
            youtube_yt, "_sc_fetch_transcript",
            # new0 has no captions; the headroom moves on to new1 and new2.
            side_effect=lambda video_id, token: (
                None if video_id == "new0" else f"paid transcript for {video_id}"
            ),
        ) as transcripts,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, _ = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

    search.assert_called_once()
    # Paid calls skip the free-transcribed video, and that free transcript
    # counts toward the default-depth target of 2, so one paid success meets
    # it instead of two more on top (#1315).
    assert [call.args[0] for call in transcripts.call_args_list] == ["missing"]
    assert [item["video_id"] for item in items] == video_ids
    assert items[0]["title"] == "free ready"
    assert items[0]["transcript_snippet"] == "free transcript"
    assert items[1]["title"] == "free missing"
    assert items[1]["transcript_snippet"] == "paid transcript for missing"
    assert not items[3].get("transcript_snippet")
    assert not items[-1].get("transcript_snippet")


def test_keyless_and_disabled_floor_keep_free_results_without_paid_search():
    free = {"video_id": "a", "transcript_snippet": ""}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc") as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        for config in (
            {},
            {"SCRAPECREATORS_API_KEY": "dummy-key", "LAST30DAYS_YT_SC_MIN_ITEMS": "0"},
            {"SCRAPECREATORS_API_KEY": "dummy-key", "LAST30DAYS_YT_SC_MIN_ITEMS": "bad"},
        ):
            items, artifact = _youtube_stream(config)
            assert items == [free]
            assert artifact == {}
    search.assert_not_called()


def test_at_floor_does_not_backfill():
    free = [{"video_id": str(i)} for i in range(3)]
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": free}),
        mock.patch.object(youtube_yt, "search_youtube_sc") as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})
    assert items == free
    assert artifact == {}
    search.assert_not_called()


def test_custom_floor_backfills_three_free_videos():
    free = [{"video_id": str(i)} for i in range(3)]
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": free}),
        mock.patch.object(
            youtube_yt, "search_youtube_sc", return_value={"items": [{"video_id": "new"}]},
        ) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({
            "SCRAPECREATORS_API_KEY": "dummy-key", "LAST30DAYS_YT_SC_MIN_ITEMS": "4",
        })
    assert search.call_count == 1
    assert [item["video_id"] for item in items] == ["0", "1", "2", "new"]
    assert "4-video backfill floor" in artifact["_source_outcome_detail"]


def test_paid_search_failure_keeps_free_video_and_reports_partial():
    free = {"video_id": "a", "transcript_snippet": "free transcript"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc", side_effect=RuntimeError("SC HTTP 429")) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})
    assert search.call_count == 1
    assert items == [free]
    assert artifact["_source_outcome"]["state"] == health.PARTIAL
    assert "SC HTTP 429" in artifact["_source_outcome"]["detail"]


def test_paid_http_429_with_no_free_items_stays_rate_limited():
    def failed_sc(*args, **kwargs):
        http._record_failure(http.HTTPError("SC HTTP 429", status_code=429))
        return {"items": []}

    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": []}),
        mock.patch.object(youtube_yt, "search_youtube_sc", side_effect=failed_sc),
    ):
        items, artifact = _youtube_stream(
            {"SCRAPECREATORS_API_KEY": "dummy-key"}, wrapped=True,
        )
    assert items == []
    assert artifact["_source_outcome"]["state"] == health.RATE_LIMITED


def test_paid_http_429_after_free_item_is_partial_not_source_rate_limit():
    def failed_sc(*args, **kwargs):
        http._record_failure(http.HTTPError("SC HTTP 429", status_code=429))
        return {"items": []}

    free = {"video_id": "a"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc", side_effect=failed_sc),
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream(
            {"SCRAPECREATORS_API_KEY": "dummy-key"}, wrapped=True,
        )
    assert items == [free]
    assert artifact["_source_outcome"]["state"] == health.PARTIAL
    assert "rate" in artifact["_source_outcome"]["detail"].lower()


def test_partial_ytdlp_search_error_stays_visible_after_backfill():
    free = {"video_id": "a"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(
            youtube_yt, "search_and_transcribe",
            return_value={"items": [free], "error": "yt-dlp bot gate"},
        ),
        mock.patch.object(youtube_yt, "search_youtube_sc", return_value={"items": []}),
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})
    assert items == [free]
    assert artifact["_source_outcome"]["state"] == health.PARTIAL
    assert "yt-dlp bot gate" in artifact["_source_outcome"]["detail"]


def test_full_pipeline_reports_clean_backfill_and_searches_paid_once_per_run():
    free = {
        "video_id": "a", "title": "Vuori review", "date": "2026-06-15",
        "url": "https://www.youtube.com/watch?v=a", "transcript_snippet": "free transcript",
    }
    paid = [
        {
            "video_id": str(i), "title": f"Vuori review {i}", "date": "2026-06-15",
            "url": f"https://www.youtube.com/watch?v={i}", "transcript_snippet": "",
        }
        for i in range(3)
    ]
    plan = {
        "intent": "general", "freshness_mode": "balanced_recent", "cluster_mode": "story",
        "subqueries": [
            {"label": "one", "search_query": "Vuori", "ranking_query": "Vuori", "sources": ["youtube"]},
            {"label": "two", "search_query": "Vuori reviews", "ranking_query": "Vuori", "sources": ["youtube"]},
        ],
        "source_weights": {"youtube": 1.0},
    }
    retrieve = pipeline._retrieve_stream

    def live_youtube(*args, **kwargs):
        return retrieve(*args, **{**kwargs, "mock": False})

    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(pipeline, "_retrieve_stream", side_effect=live_youtube),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc", return_value={"items": paid}) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        report = pipeline.run(
            topic="Vuori", config={"SCRAPECREATORS_API_KEY": "dummy-key"},
            depth="default", requested_sources=["youtube"], mock=True,
            external_plan=plan, as_of_date="2026-07-01",
        )

    # Both subqueries search the raw topic: the run fetch memo serves the
    # second stream (and the thin retry) from the first clean fetch.
    assert search.call_count == 1
    assert report.source_status["youtube"].state == health.OK
    assert "ScrapeCreators added 3" in report.source_status["youtube"].detail


def _run_thin_youtube_report(sc_responses):
    free = {
        "video_id": "free", "title": "Vuori review", "date": "2026-06-15",
        "url": "https://www.youtube.com/watch?v=free", "transcript_snippet": "free transcript",
    }
    plan = {
        "intent": "general", "freshness_mode": "balanced_recent", "cluster_mode": "story",
        "subqueries": [{
            "label": "primary", "search_query": "Vuori", "ranking_query": "Vuori",
            "sources": ["youtube"],
        }],
        "source_weights": {"youtube": 1.0},
    }
    retrieve = pipeline._retrieve_stream

    def live_youtube(*args, **kwargs):
        return retrieve(*args, **{**kwargs, "mock": False})

    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(pipeline, "_retrieve_stream", side_effect=live_youtube),
        mock.patch.object(youtube_yt, "search_and_transcribe", return_value={"items": [free]}),
        mock.patch.object(youtube_yt, "search_youtube_sc", side_effect=sc_responses) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        report = pipeline.run(
            topic="Vuori", config={"SCRAPECREATORS_API_KEY": "dummy-key"},
            depth="default", requested_sources=["youtube"], mock=True,
            external_plan=plan, as_of_date="2026-07-01",
        )
    return report, search.call_count


def test_thin_retry_reuses_a_clean_empty_backfill_instead_of_refetching():
    # A clean (error-free) backfill is the answer for this query in this run:
    # the identical thin-source retry is served from the run fetch memo.
    unused = [
        {
            "video_id": str(i), "title": f"Vuori review {i}", "date": "2026-06-15",
            "url": f"https://www.youtube.com/watch?v={i}",
        }
        for i in range(2)
    ]
    report, calls = _run_thin_youtube_report([
        {"items": []}, {"items": unused},
    ])
    assert calls == 1
    assert report.source_status["youtube"].state == health.OK
    assert [item.item_id for item in report.items_by_source["youtube"]] == ["free"]


def test_clean_sparse_topic_stays_ok_after_empty_backfill_and_retry():
    report, calls = _run_thin_youtube_report([{"items": []}, {"items": []}])
    assert calls == 1
    assert report.source_status["youtube"].state == health.OK
    assert [item.item_id for item in report.items_by_source["youtube"]] == ["free"]
    assert "added 0" in report.source_status["youtube"].detail


def test_paid_429_after_free_item_stops_paid_thin_retry():
    report, calls = _run_thin_youtube_report([
        {"items": [], "error": "SC HTTP 429"},
    ])
    assert calls == 1
    assert report.source_status["youtube"].state == health.PARTIAL
    assert "429" in report.source_status["youtube"].detail


def test_transient_paid_failure_stays_partial_if_retry_finds_no_new_video():
    report, calls = _run_thin_youtube_report([
        {"items": [], "error": "SC connection reset"},
        {"items": [], "error": "SC connection reset"},
    ])
    assert calls == 2
    assert report.source_status["youtube"].state == health.PARTIAL
    assert "connection reset" in report.source_status["youtube"].detail


def test_retry_error_stays_partial_even_when_retry_adds_a_video():
    new = {
        "video_id": "new", "title": "Vuori review new", "date": "2026-06-15",
        "url": "https://www.youtube.com/watch?v=new",
    }
    report, calls = _run_thin_youtube_report([
        {"items": [], "error": "SC connection reset"},
        {"items": [new], "error": "SC connection reset"},
    ])
    assert calls == 2
    assert "new" in {item.item_id for item in report.items_by_source["youtube"]}
    assert report.source_status["youtube"].state == health.PARTIAL
    assert "connection reset" in report.source_status["youtube"].detail


def test_ytdlp_bot_gate_and_paid_exception_do_not_retry_paid_search():
    free = {"video_id": "free"}
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(
            youtube_yt, "search_and_transcribe",
            return_value={"items": [free], "error": "Sign in to confirm you're not a bot"},
        ),
        mock.patch.object(
            youtube_yt, "search_youtube_sc", side_effect=RuntimeError("SC disconnected"),
        ) as search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, artifact = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})
    assert items == [free]
    assert search.call_count == 1
    assert artifact["_source_outcome"]["state"] == health.PARTIAL
    assert "not a bot" in artifact["_source_outcome"]["detail"]
    assert "SC disconnected" in artifact["_source_outcome"]["detail"]


def test_freshness_mode_reaches_both_youtube_searches():
    """plan.freshness_mode is threaded into the yt-dlp and ScrapeCreators searches."""
    free = {
        "video_id": "free", "title": "Vuori review", "date": "2026-06-15",
        "url": "https://www.youtube.com/watch?v=free", "transcript_snippet": "free transcript",
    }
    plan = {
        "intent": "how_to", "freshness_mode": "evergreen_ok", "cluster_mode": "story",
        "subqueries": [{
            "label": "primary", "search_query": "Vuori", "ranking_query": "Vuori",
            "sources": ["youtube"],
        }],
        "source_weights": {"youtube": 1.0},
    }
    retrieve = pipeline._retrieve_stream

    def live_youtube(*args, **kwargs):
        return retrieve(*args, **{**kwargs, "mock": False})

    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(pipeline, "_retrieve_stream", side_effect=live_youtube),
        mock.patch.object(
            youtube_yt, "search_and_transcribe", return_value={"items": [free]},
        ) as free_search,
        mock.patch.object(youtube_yt, "search_youtube_sc", return_value={"items": []}) as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        pipeline.run(
            topic="Vuori", config={"SCRAPECREATORS_API_KEY": "dummy-key"},
            depth="default", requested_sources=["youtube"], mock=True,
            external_plan=plan, as_of_date="2026-07-01",
        )

    assert free_search.call_count >= 1
    assert sc_search.call_count >= 1
    for call in free_search.call_args_list + sc_search.call_args_list:
        assert call.kwargs.get("freshness_mode") == "evergreen_ok"


def _run_ends_during(free_result):
    """A live config whose run is cancelled while yt-dlp is still working.

    The stream starts inside the budget (``_retrieve_stream_impl`` refuses a
    stopped run up front), then the main thread gives up on it, as when the
    lane is abandoned at the stream deadline.
    """
    cancel = threading.Event()
    config = {"SCRAPECREATORS_API_KEY": "dummy-key", "_enrich_cancel": cancel}

    def _free_search(*args, **kwargs):
        cancel.set()
        return free_result(**kwargs) if callable(free_result) else free_result

    return config, _free_search


def test_abandoned_lane_skips_paid_search_after_the_run_ends():
    """#1315: once the run is over, an empty yt-dlp result buys no SC search."""
    config, free_search = _run_ends_during({"items": []})
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", side_effect=free_search) as free,
        mock.patch.object(youtube_yt, "search_youtube_sc") as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, _ = _youtube_stream(config)

    free.assert_called_once()
    assert items == []
    sc_search.assert_not_called()


def test_interpreter_shutdown_is_not_an_empty_ytdlp_result():
    """#1315: the straggler's RuntimeError must not trigger a paid search."""
    shutdown = RuntimeError("cannot schedule new futures after interpreter shutdown")
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", side_effect=shutdown),
        mock.patch.object(youtube_yt, "search_youtube_sc") as sc_search,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

    sc_search.assert_not_called()


def test_transcript_rescue_inside_an_over_run_spends_nothing():
    """#1315: per-video SC rescue in pool workers sees the stop gate."""
    rescued = {}

    def _transcribe(**kwargs):
        rescued.update(youtube_yt.fetch_transcripts_parallel(["v1", "v2"], token=kwargs["token"]))
        return {"items": []}

    config, free_search = _run_ends_during(_transcribe)
    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "search_and_transcribe", side_effect=free_search),
        mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False),
        mock.patch.object(youtube_yt, "_fetch_transcript_direct", return_value=None),
        mock.patch.object(youtube_yt.http, "get") as paid_get,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        _youtube_stream(config)

    assert rescued == {"v1": None, "v2": None}
    paid_get.assert_not_called()


def test_sc_paid_endpoints_refuse_inside_a_stopped_context():
    with mock.patch.object(youtube_yt.http, "get") as paid_get, \
         mock.patch.object(youtube_yt, "_ytdlp_comments_result", return_value=([], False)), \
         youtube_yt.sc_spend_until(lambda: True):
        assert youtube_yt._sc_youtube_search("vuori", "dummy-key") == []
        assert youtube_yt._sc_fetch_transcript("v1", "dummy-key") is None
        assert youtube_yt._fetch_video_comments("v1", "dummy-key") == []
    paid_get.assert_not_called()


def test_sc_paid_endpoints_still_spend_while_the_run_is_live():
    with mock.patch.object(
        youtube_yt.http, "get", return_value={"transcript": "live transcript"},
    ) as paid_get, youtube_yt.sc_spend_until(lambda: False):
        assert youtube_yt._sc_fetch_transcript("v1", "dummy-key") == "live transcript"
    paid_get.assert_called_once()


def test_sc_transcript_buys_no_next_language_after_the_run_ends():
    """#1315: the run ends while `en` is in flight; `es` and `pt` are not bought."""
    stopped = threading.Event()

    def _empty_then_stop(*args, **kwargs):
        stopped.set()
        return {"transcript": None}

    with mock.patch.object(youtube_yt.http, "get", side_effect=_empty_then_stop) as paid_get, \
         mock.patch.dict("os.environ", {"LAST30DAYS_YT_SUB_LANGS": "en,es,pt"}), \
         youtube_yt.sc_spend_until(stopped.is_set):
        assert youtube_yt._sc_fetch_transcript("v1", "dummy-key") is None
    assert [c.kwargs["params"]["language"] for c in paid_get.call_args_list] == ["en"]


def test_sc_requests_carry_the_run_cancel_and_deadline():
    """#1315: retries of a paid request stop at the run's deadline or cancel."""
    cancel = threading.Event()
    run_deadline = 1.0  # monotonic; earlier than any per-call deadline below
    with mock.patch.object(youtube_yt.http, "get", return_value={}) as paid_get, \
         mock.patch.object(youtube_yt, "_ytdlp_comments_result", return_value=([], False)), \
         mock.patch.object(youtube_yt.time, "monotonic", return_value=0.0), \
         youtube_yt.sc_spend_until(lambda: False, cancel=cancel, deadline=run_deadline):
        youtube_yt._sc_youtube_search("vuori", "dummy-key")
        youtube_yt._fetch_video_comments("v1", "dummy-key")
        youtube_yt._sc_fetch_transcript("v1", "dummy-key")
    assert paid_get.call_count >= 3
    for call in paid_get.call_args_list:
        assert call.kwargs["cancel"] is cancel
        assert call.kwargs["deadline_monotonic"] == run_deadline


def test_sc_transcript_keeps_its_own_deadline_outside_a_run():
    with mock.patch.object(youtube_yt.http, "get", return_value={"transcript": "t"}) as paid_get:
        assert youtube_yt._sc_fetch_transcript("v1", "dummy-key") == "t"
    kwargs = paid_get.call_args.kwargs
    assert "cancel" not in kwargs
    assert kwargs["deadline_monotonic"] > 0
