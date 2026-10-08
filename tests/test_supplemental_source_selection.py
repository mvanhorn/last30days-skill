"""Supplemental X lanes must stay inside the effective research plan."""

from unittest.mock import patch
from uuid import uuid4

import pytest

from lib import dates, pipeline, x_envelope


@pytest.mark.parametrize("depth", ["default", "deep"])
@pytest.mark.parametrize("lane", ["backend", "envelope"])
@pytest.mark.parametrize(
    "requested,excluded,planned,allowed",
    [
        pytest.param(["reddit"], "x", ["reddit"], False, id="reddit-and-exclusion"),
        pytest.param(["reddit"], "", ["reddit", "x"], False, id="requested-reddit"),
        pytest.param(None, "x", ["reddit", "x"], False, id="excluded-x"),
        pytest.param(["reddit", "x"], "x", ["reddit", "x"], False, id="exclusion-wins"),
        pytest.param(["reddit", "x"], "", ["reddit"], False, id="plan-omits-x"),
        pytest.param(None, "", ["reddit", "x"], True, id="available-x"),
        pytest.param(["reddit", "x"], "", ["reddit", "x"], True, id="requested-x"),
        pytest.param(["xquik"], "", ["reddit", "x"], True, id="requested-alias"),
    ],
)
def test_supplemental_lanes_obey_effective_sources(depth, lane, requested, excluded, planned, allowed):
    topic = "widget development"
    marker = uuid4().hex
    window = dates.get_date_range(30)
    posts = [
        {
            "id": str(index),
            "url": f"https://x.com/example/status/{index}",
            "author_handle": "example",
            "text": f"{topic} {marker}: {label}",
            "date": window[1],
            "engagement": {"likes": 120, "retweets": 10, "replies": 5},
            "relevance": 1.0,
        }
        for index, label in enumerate([
            "shipping a compiler release with faster incremental builds and reduced memory consumption",
            "community feedback suggests clearer tutorials explaining deployment authentication errors for beginners",
        ], start=1)
    ]
    envelope = None
    if lane == "envelope":
        envelope = x_envelope.Envelope(
            path="fixture.json", sha256="fixture", status="ok", error_category="",
            provider="x-connector", window=window, topic_items=[],
            lane_calls=[
                x_envelope.EnvelopeCall(0, "from", ["example"], [posts[0]]),
                x_envelope.EnvelopeCall(1, "mention", ["example"], [posts[1]]),
            ],
            counters={}, accepted=2, total=2,
            lane_counts={"from": 1, "mention": 1}, call_lanes=["from", "mention"],
        )
    config = {
        "X_BEARER_TOKEN": "dummy-bearer",
        "LAST30DAYS_X_BACKEND": "xapi",
        "EXCLUDE_SOURCES": excluded,
    }
    plan = {
        "intent": "opinion", "freshness_mode": "balanced_recent", "cluster_mode": "debate",
        "subqueries": [{
            "label": "primary", "search_query": topic,
            "ranking_query": topic, "sources": planned,
        }],
    }
    with (
        patch.object(pipeline.reddit_public, "search_reddit_public", autospec=True, return_value=[]),
        patch.object(pipeline.x_api, "search_x", autospec=True, return_value={"items": []}),
        patch.object(pipeline.x_api, "search_handles", autospec=True, return_value=[posts[0]]) as from_lane,
        patch.object(pipeline.x_api, "search_mentions", autospec=True, return_value=[posts[1]]) as about_lane,
    ):
        report = pipeline.run(
            topic=topic, config=config, depth=depth, requested_sources=requested,
            external_plan=plan, x_handle="example", x_posts=envelope,
            web_backend="none", save_dir="",
        )

    evidence = report.items_by_source.get("x", [])
    if allowed:
        assert {item.url for item in evidence} == {post["url"] for post in posts}
        assert all(marker in item.body for item in evidence)
    else:
        assert not evidence
    if allowed and lane == "backend":
        from_lane.assert_called_once()
        about_lane.assert_called_once()
    else:
        from_lane.assert_not_called()
        about_lane.assert_not_called()
    if envelope is not None:
        remaining = envelope.take_lanes()
        if allowed:
            assert remaining is None
        else:
            assert remaining == envelope.lane_calls
