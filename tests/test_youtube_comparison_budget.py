"""Comparison entities split one single-topic yt-dlp budget instead of each taking a full one (#1335)."""

from unittest import mock

import pytest

from lib import pipeline, schema, youtube_yt
from tests.competitor_cli_audit_helpers import run_competitor_cli


def _count_ytdlp_work(config):
    """Run one entity's YouTube lane and count the yt-dlp calls it queues."""
    calls = {"searches": 0, "transcripts": 0, "comments": 0}

    def search(q, *_args, **_kwargs):
        calls["searches"] += 1
        return {"items": [
            {"video_id": f"{q}-{i}", "title": q, "date": "2026-06-20",
             "engagement": {"views": 100 - i, "likes": 1, "comments": 1}}
            for i in range(8)
        ]}

    def transcript(*_args, **_kwargs):
        calls["transcripts"] += 1
        return "transcript text"

    def comments(*_args, **_kwargs):
        calls["comments"] += 1
        return [{"author": "a", "text": "c", "likes": 1, "date": ""}]

    with (
        mock.patch.object(pipeline, "which", return_value="/usr/bin/yt-dlp"),
        mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True),
        mock.patch.object(youtube_yt, "search_youtube", side_effect=search),
        mock.patch.object(youtube_yt, "fetch_transcript", side_effect=transcript),
        mock.patch.object(youtube_yt, "_fetch_video_comments", side_effect=comments),
        mock.patch.object(pipeline.env, "is_youtube_sc_available", return_value=False),
        mock.patch.object(pipeline.env, "is_youtube_comments_available", return_value=True),
    ):
        items, _artifact = pipeline._retrieve_stream_impl(
            topic="Widget launch news",
            subquery=schema.SubQuery(
                label="q", search_query="Widget", ranking_query="Widget", sources=["youtube"],
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
    assert items
    return calls


def test_single_topic_keeps_the_full_youtube_budget():
    assert _count_ytdlp_work({}) == {"searches": 2, "transcripts": 6, "comments": 3}


def test_three_entity_comparison_splits_one_single_topic_budget():
    """Three entities x full budget queued 3x the work on the shared 2-slot gate and timed out."""
    assert _count_ytdlp_work({"_comparison_entity_count": 3}) == {
        "searches": 1, "transcripts": 3, "comments": 1,
    }


@pytest.mark.parametrize(
    ("total", "entities", "share"),
    [(2, 1, 2), (2, 2, 1), (2, 3, 1), (3, 2, 2), (8, 3, 3), (0, 3, 0), (3, 6, 1)],
)
def test_every_entity_keeps_at_least_one_of_each_budgeted_call(total, entities, share):
    assert youtube_yt.comparison_share(total, entities) == share


def test_backfill_targets_the_entity_share():
    items = [
        schema.SourceItem(
            item_id=f"v{i}", source="youtube", title="t", body="", url="u",
        )
        for i in range(6)
    ]
    with (
        mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True),
        mock.patch.object(youtube_yt, "fetch_transcripts_parallel", return_value={}) as fetch,
    ):
        youtube_yt.backfill_transcripts(items, depth="default", entity_count=3)

    assert fetch.call_args.kwargs["max_sc_rescues"] == 1
    assert len(fetch.call_args.args[0]) == 3


def test_every_comparison_subrun_learns_the_entity_count(monkeypatch, tmp_path):
    observed = run_competitor_cli(monkeypatch, tmp_path)

    for entity in ("MainBrand", "PeerOne", "PeerTwo"):
        assert observed.calls[entity]["config"]["_comparison_entity_count"] == 3
