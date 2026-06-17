"""Tests for backfill_transcripts - the post-selection transcript pass (#542).

search_and_transcribe() fetches transcripts for each search's top-by-views
candidates, but the pipeline's final selection ranks by relevance. When the
two sets are disjoint, every surviving video ships transcript-less. These
tests pin the second-pass backfill that runs from _finalize_items_by_source().
"""

import unittest
from unittest import mock

from lib import schema, youtube_yt


def _item(item_id, **metadata):
    return schema.SourceItem(
        item_id=item_id,
        source="youtube",
        title=f"Video {item_id}",
        body=f"Video {item_id}",
        url=f"https://www.youtube.com/watch?v={item_id}",
        metadata=dict(metadata),
    )


TRANSCRIPT = (
    "Claude Code skills are directories containing a SKILL md file. "
    "This video covers the fastest growing skills on GitHub this month. "
    "Superpowers enforces test driven development across every phase."
)


class TestBackfillTranscripts(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_backfills_survivors_without_transcripts(self):
        """The #542 shape: finalized videos disjoint from the fetched set."""
        items = [_item("vidA"), _item("vidB"), _item("vidC"), _item("vidD")]
        with mock.patch.object(
            youtube_yt, "fetch_transcripts_parallel",
            return_value={"vidA": TRANSCRIPT, "vidB": TRANSCRIPT},
        ) as fetch:
            youtube_yt.backfill_transcripts(items, topic="claude code skills", depth="default")

        # default budget is 2, attempts capped at need*3
        fetched_ids = fetch.call_args.args[0]
        self.assertEqual(fetched_ids, ["vidA", "vidB", "vidC", "vidD"])
        self.assertEqual(items[0].metadata["transcript_snippet"], TRANSCRIPT)
        self.assertEqual(items[1].metadata["transcript_snippet"], TRANSCRIPT)
        self.assertNotIn("transcript_snippet", items[2].metadata)
        self.assertTrue(items[0].metadata.get("transcript_highlights"))
        self.assertTrue(items[0].snippet)

    def test_noop_when_budget_already_met(self):
        """Survivors that already carry >= limit transcripts trigger no fetch."""
        items = [
            _item("vidA", transcript_snippet=TRANSCRIPT),
            _item("vidB", transcript_highlights=["quote"]),
            _item("vidC"),
        ]
        with mock.patch.object(youtube_yt, "fetch_transcripts_parallel") as fetch:
            youtube_yt.backfill_transcripts(items, depth="default")
        fetch.assert_not_called()

    def test_partial_budget_fetches_only_the_gap(self):
        """One transcript present at default depth (limit 2) -> need is 1."""
        items = [
            _item("vidA", transcript_snippet=TRANSCRIPT),
            _item("vidB"),
            _item("vidC"),
            _item("vidD"),
            _item("vidE"),
        ]
        with mock.patch.object(
            youtube_yt, "fetch_transcripts_parallel", return_value={"vidB": TRANSCRIPT},
        ) as fetch:
            youtube_yt.backfill_transcripts(items, depth="default")
        # need=1, attempts capped at need*3=3
        self.assertEqual(fetch.call_args.args[0], ["vidB", "vidC", "vidD"])

    def test_noop_at_quick_depth(self):
        """quick depth has a transcript budget of 0 - never fetch."""
        items = [_item("vidA")]
        with mock.patch.object(youtube_yt, "fetch_transcripts_parallel") as fetch:
            youtube_yt.backfill_transcripts(items, depth="quick")
        fetch.assert_not_called()

    def test_noop_when_ytdlp_missing(self):
        items = [_item("vidA")]
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=False):
            with mock.patch.object(youtube_yt, "fetch_transcripts_parallel") as fetch:
                youtube_yt.backfill_transcripts(items, depth="default")
        fetch.assert_not_called()

    def test_skips_captions_disabled_items(self):
        """Uploader-disabled captions are not retried and stay marked."""
        items = [_item("vidA", captions_disabled=True), _item("vidB")]
        with mock.patch.object(
            youtube_yt, "fetch_transcripts_parallel", return_value={"vidB": TRANSCRIPT},
        ) as fetch:
            youtube_yt.backfill_transcripts(items, depth="default")
        self.assertEqual(fetch.call_args.args[0], ["vidB"])

    def test_marks_newly_discovered_captions_disabled(self):
        """A backfill attempt that reveals no caption tracks feeds quality_nudge."""
        def fake_fetch(video_ids, out_captions_disabled=None):
            if out_captions_disabled is not None:
                out_captions_disabled.add("vidA")
            return {"vidA": None, "vidB": TRANSCRIPT}

        items = [_item("vidA"), _item("vidB")]
        with mock.patch.object(youtube_yt, "fetch_transcripts_parallel", side_effect=fake_fetch):
            youtube_yt.backfill_transcripts(items, depth="default")
        self.assertTrue(items[0].metadata.get("captions_disabled"))
        self.assertNotIn("transcript_snippet", items[0].metadata)
        self.assertEqual(items[1].metadata["transcript_snippet"], TRANSCRIPT)

    def test_does_not_overwrite_existing_snippet(self):
        items = [_item("vidA")]
        items[0].snippet = "ranker-chosen snippet"
        with mock.patch.object(
            youtube_yt, "fetch_transcripts_parallel", return_value={"vidA": TRANSCRIPT},
        ):
            youtube_yt.backfill_transcripts(items, depth="default")
        self.assertEqual(items[0].snippet, "ranker-chosen snippet")
        self.assertEqual(items[0].metadata["transcript_snippet"], TRANSCRIPT)


class TestFinalizeWiring(unittest.TestCase):
    def test_finalize_calls_backfill_for_youtube_survivors(self):
        from lib import pipeline

        items = [_item("vidA")]
        with mock.patch.object(youtube_yt, "backfill_transcripts") as backfill:
            pipeline._finalize_items_by_source(
                {"youtube": items}, topic="claude code skills", depth="deep",
            )
        backfill.assert_called_once()
        self.assertEqual(backfill.call_args.kwargs.get("depth"), "deep")
        self.assertEqual(backfill.call_args.kwargs.get("topic"), "claude code skills")

    def test_finalize_skips_backfill_in_mock_mode(self):
        from lib import pipeline

        items = [_item("vidA")]
        with mock.patch.object(youtube_yt, "backfill_transcripts") as backfill:
            pipeline._finalize_items_by_source(
                {"youtube": items}, topic="claude code skills", depth="default", mock=True,
            )
        backfill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
