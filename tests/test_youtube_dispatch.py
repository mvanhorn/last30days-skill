"""Pipeline YouTube dispatch: thinness-floor ScrapeCreators search backstop (#977).

The SC YouTube search backstop must fire when yt-dlp returns fewer than
``pipeline._YT_SC_MIN_ITEMS`` items (not only zero), and its results merge with
the yt-dlp items - never discarding them (R2).
"""

from unittest import mock

from lib import health, pipeline, schema


def _subquery():
    return schema.SubQuery(
        label="t", search_query="youtube topic", ranking_query="youtube topic",
        sources=["youtube"],
    )


def _runtime():
    return schema.ProviderRuntime(
        reasoning_provider="mock", planner_model="mock", rerank_model="mock",
    )


def _item(vid, title="video", transcript=None):
    item = {
        "video_id": vid,
        "title": title,
        "url": f"https://www.youtube.com/watch?v={vid}",
    }
    if transcript is not None:
        item["transcript_snippet"] = transcript
    return item


def _run(config, free_items, sc_items=None, sc_raises=False, sc_error=None,
         skip_sc_backstop=False, comments_available=False):
    """Drive the real pipeline youtube branch with the SC backstop mocked.

    ``env.is_youtube_comments_available`` is pinned explicitly so the suite is
    deterministic on hosts with or without yt-dlp installed (the env probe uses
    the real PATH, not the patched ``lib.pipeline.which``).
    """
    sc_mock = mock.Mock()
    if sc_raises:
        sc_mock.side_effect = Exception("sc down")
    else:
        payload = {"items": sc_items or []}
        if sc_error:
            payload["error"] = sc_error
        sc_mock.return_value = payload
    with mock.patch("lib.pipeline.which", return_value="/usr/local/bin/yt-dlp"), \
         mock.patch(
             "lib.pipeline.youtube_yt.search_and_transcribe",
             return_value={"items": free_items},
         ), \
         mock.patch("lib.pipeline.youtube_yt.search_youtube_sc", sc_mock), \
         mock.patch("lib.pipeline.env.is_youtube_comments_available",
                    return_value=comments_available), \
         mock.patch("lib.pipeline.youtube_yt.enrich_with_comments") as enrich:
        items, artifact = pipeline._retrieve_stream(
            topic="youtube topic", subquery=_subquery(), source="youtube",
            config=config, depth="quick",
            date_range=("2026-07-17", "2026-08-16"),
            runtime=_runtime(), mock=False,
            skip_sc_backstop=skip_sc_backstop,
        )
    return items, artifact, sc_mock, enrich


class TestThinnessFloorBackstop:
    KEY = {"SCRAPECREATORS_API_KEY": "k"}

    def test_below_floor_fires_backstop_and_merges(self, capsys):
        # 1 free item (< floor) -> backstop fires; SC item that duplicates the
        # free video_id is not double-listed; free item stays first.
        free = [_item("a")]
        sc = [_item("b"), _item("a", title="dupe"), _item("c")]
        items, _, sc_mock, _ = _run(self.KEY, free, sc)
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["a", "b", "c"]
        err = capsys.readouterr().err
        assert "[YouTube]" in err
        assert "below the 3-item floor" in err

    def test_below_floor_backfill_marks_lane_partial(self):
        # The rescued lane must not read as clean in durable surfaces (R3).
        _, artifact, _, _ = _run(self.KEY, [_item("a")], [_item("b"), _item("c")])
        assert artifact["_source_outcome"]["state"] == schema.PARTIAL
        assert "below the 3-item floor" in artifact["_source_outcome"]["detail"]

    def test_at_floor_skips_backstop(self):
        items, artifact, sc_mock, _ = _run(
            self.KEY, [_item("a"), _item("b"), _item("c")])
        sc_mock.assert_not_called()
        assert len(items) == 3
        assert "_source_outcome" not in artifact

    def test_zero_items_fires_backstop_silently(self, capsys):
        items, artifact, sc_mock, _ = _run(self.KEY, [], [_item("z")])
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["z"]
        assert "[YouTube]" not in capsys.readouterr().err
        assert "_source_outcome" not in artifact

    def test_empty_backstop_preserves_free_items(self):
        free = [_item("a")]
        items, artifact, sc_mock, _ = _run(self.KEY, free, [])
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["a"]
        assert artifact["_source_outcome"]["state"] == schema.PARTIAL

    def test_backstop_throw_preserves_free_items_and_failure(self):
        free = [_item("a")]
        items, artifact, sc_mock, _ = _run(self.KEY, free, sc_raises=True)
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["a"]
        assert artifact["_source_outcome"]["state"] == health.ERROR
        assert "sc down" in artifact["_source_outcome"]["detail"]

    def test_sc_error_response_merges_items_and_records_failure(self):
        free = [_item("a")]
        items, artifact, sc_mock, _ = _run(
            self.KEY, free, [_item("b"), _item("c")], sc_error="credit exhausted")
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["a", "b", "c"]
        assert "credit exhausted" in artifact["_source_outcome"]["detail"]
        assert artifact["_source_outcome"]["state"] != health.OK

    def test_backstop_failure_does_not_mask_bot_gate(self):
        # The composed failure keeps the actionable yt-dlp bot-gate marker.
        with mock.patch("lib.pipeline.which", return_value="/usr/local/bin/yt-dlp"), \
             mock.patch(
                 "lib.pipeline.youtube_yt.search_and_transcribe",
                 return_value={
                     "items": [_item("a")],
                     "error": "Sign in to confirm you're not a bot",
                 },
             ), \
             mock.patch("lib.pipeline.youtube_yt.search_youtube_sc",
                        side_effect=Exception("sc down")), \
             mock.patch("lib.pipeline.env.is_youtube_comments_available",
                        return_value=False), \
             mock.patch("lib.pipeline.youtube_yt.enrich_with_comments"):
            items, artifact = pipeline._retrieve_stream(
                topic="youtube topic", subquery=_subquery(), source="youtube",
                config=self.KEY, depth="quick",
                date_range=("2026-07-17", "2026-08-16"),
                runtime=_runtime(), mock=False,
            )
        assert [i["video_id"] for i in items] == ["a"]
        assert artifact["_source_outcome"]["state"] == schema.RATE_LIMITED
        assert "not a bot" in artifact["_source_outcome"]["detail"]

    def test_keyless_never_calls_sc(self):
        items, _, sc_mock, _ = _run({}, [_item("a")])
        sc_mock.assert_not_called()
        assert [i["video_id"] for i in items] == ["a"]

    def test_skip_sc_backstop_suppresses_backfill(self):
        # Phase-2b thin-source retries must not spend a second SC call per run.
        items, _, sc_mock, _ = _run(self.KEY, [_item("a")], skip_sc_backstop=True)
        sc_mock.assert_not_called()
        assert [i["video_id"] for i in items] == ["a"]

    def test_keyless_sc_item_not_dropped_by_merge(self):
        # An SC item with no id fields (URL fallback) must survive the merge.
        free = [_item("a")]
        sc = [{"video_id": "", "title": "no-id",
               "url": "https://www.youtube.com/watch?v=xyz"}]
        items, _, sc_mock, _ = _run(self.KEY, free, sc)
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["a", ""]
        assert items[1]["url"].endswith("xyz")

    def test_merge_grafts_paid_transcript_onto_free_copy(self):
        # Free-first merge keeps the yt-dlp copy, but a paid SC transcript on
        # the colliding copy must not be thrown away.
        free = [_item("a")]  # transcript_snippet absent (bot-gated)
        sc = [_item("a", transcript="paid transcript"), _item("b")]
        items, _, _, _ = _run(self.KEY, free, sc)
        assert [i["video_id"] for i in items] == ["a", "b"]
        assert items[0]["transcript_snippet"] == "paid transcript"

    def test_no_ytdlp_still_falls_back_to_sc(self):
        # result None (yt-dlp branch not run) is the pre-existing trigger.
        with mock.patch("lib.pipeline.which", return_value=None), \
             mock.patch("lib.pipeline.youtube_yt.search_youtube_sc",
                        return_value={"items": [_item("z")]}) as sc_mock, \
             mock.patch("lib.pipeline.env.is_youtube_comments_available",
                        return_value=False), \
             mock.patch("lib.pipeline.youtube_yt.enrich_with_comments"):
            items, _ = pipeline._retrieve_stream(
                topic="youtube topic", subquery=_subquery(), source="youtube",
                config=self.KEY, depth="quick",
                date_range=("2026-07-17", "2026-08-16"),
                runtime=_runtime(), mock=False,
            )
        sc_mock.assert_called_once()
        assert [i["video_id"] for i in items] == ["z"]

    def test_bot_gate_failure_preserved_after_rescue(self):
        free = [_item("a")]
        with mock.patch("lib.pipeline.which", return_value="/usr/local/bin/yt-dlp"), \
             mock.patch(
                 "lib.pipeline.youtube_yt.search_and_transcribe",
                 return_value={
                     "items": free,
                     "error": "Sign in to confirm you're not a bot",
                 },
             ), \
             mock.patch("lib.pipeline.youtube_yt.search_youtube_sc",
                        return_value={"items": [_item("b"), _item("c"), _item("d")]}), \
             mock.patch("lib.pipeline.env.is_youtube_comments_available",
                        return_value=False), \
             mock.patch("lib.pipeline.youtube_yt.enrich_with_comments"):
            items, artifact = pipeline._retrieve_stream(
                topic="youtube topic", subquery=_subquery(), source="youtube",
                config=self.KEY, depth="quick",
                date_range=("2026-07-17", "2026-08-16"),
                runtime=_runtime(), mock=False,
            )
        assert len(items) == 4
        assert artifact["_source_outcome"]["state"] == schema.RATE_LIMITED

    def test_comments_enrichment_runs_on_rescued_items(self):
        free = [_item("a")]
        items, _, _, enrich = _run(
            self.KEY, free, [_item("b"), _item("c")], comments_available=True)
        enrich.assert_called_once_with(
            items, token=self.KEY["SCRAPECREATORS_API_KEY"],
        )
