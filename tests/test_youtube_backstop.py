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
            side_effect=lambda video_id, token: f"paid transcript for {video_id}",
        ) as transcripts,
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=False),
    ):
        items, _ = _youtube_stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

    search.assert_called_once()
    assert [call.args[0] for call in transcripts.call_args_list] == video_ids[1:]
    assert len(transcripts.call_args_list) == 6
    assert [item["video_id"] for item in items] == video_ids
    assert items[0]["title"] == "free ready"
    assert items[0]["transcript_snippet"] == "free transcript"
    assert items[1]["title"] == "free missing"
    assert items[1]["transcript_snippet"] == "paid transcript for missing"
    assert items[-1]["title"] == "SC new4"
    assert items[-1]["transcript_snippet"] == "paid transcript for new4"


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
