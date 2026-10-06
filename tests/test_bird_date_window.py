import json
import threading
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import last30days
from lib import bird_x, dates, pipeline, schema, subproc


def _response(items):
    return subproc.SubprocResult(
        returncode=0, stdout=json.dumps({"items": items}), stderr=""
    )


def _tweet(post_id, created_at, author="analyst"):
    return {
        "id": post_id,
        "url": f"https://x.com/{author}/status/{post_id}",
        "author": {"username": author},
        "text": "multi-agent autonomous systems",
        "createdAt": created_at,
    }


def test_every_topic_retry_keeps_the_inclusive_end_date():
    with mock.patch.object(
        bird_x.subproc, "run_with_timeout", autospec=True, return_value=_response([])
    ) as run:
        result = bird_x.search_x(
            "multi-agent autonomous systems", "2024-02-01", "2024-02-29"
        )

    queries = [call.args[0][2] for call in run.call_args_list]
    assert result == {"items": []}
    assert queries == [
        "multi-agent autonomous systems since:2024-02-01 until:2024-03-01",
        '("multi-agent") since:2024-02-01 until:2024-03-01',
        "multi-agent autonomous since:2024-02-01 until:2024-03-01",
        "multi-agent since:2024-02-01 until:2024-03-01",
    ]


def test_historical_search_retries_when_recent_posts_are_outside_the_window():
    historical = _tweet("1", "2024-02-29T23:59:59Z")
    recent = _tweet("2", "2026-10-04T12:00:00Z")

    def search(cmd, **kwargs):
        query = cmd[2]
        if "until:2024-03-01" not in query:
            return _response([recent])
        if query.startswith("("):
            return _response([historical])
        return _response([])

    with mock.patch.object(
        bird_x.subproc, "run_with_timeout", autospec=True, side_effect=search
    ) as run:
        result = bird_x.search_x(
            "multi-agent autonomous systems", "2024-02-01", "2024-02-29"
        )

    assert result["items"] == [historical]
    assert run.call_count == 2


def test_current_day_search_retains_posts_from_the_whole_end_date():
    today = datetime.now(timezone.utc).date()
    last_second = _tweet("3", f"{today.isoformat()}T23:59:59Z")
    with mock.patch.object(
        bird_x.subproc,
        "run_with_timeout",
        autospec=True,
        return_value=_response([last_second]),
    ) as run:
        result = bird_x.search_x(
            "multi-agent", (today - timedelta(days=30)).isoformat(), today.isoformat()
        )

    assert result["items"] == [last_second]
    assert run.call_count == 1
    assert f"until:{(today + timedelta(days=1)).isoformat()}" in run.call_args.args[0][2]


@pytest.mark.parametrize("lane", ["topic", "from", "mentions"])
@pytest.mark.parametrize(
    ("end_date", "expected_filters"),
    [
        ("9999-12-31", "since:9999-12-01"),
        ("9999-12-30", "since:9999-11-30 until:9999-12-31"),
    ],
)
def test_cli_maximum_end_dates_reach_every_bird_lane(lane, end_date, expected_filters):
    args = last30days.build_parser().parse_args(["multi-agent", "--as-of", end_date])
    from_date, to_date = dates.get_date_range(as_of_date=args.as_of_date)
    topic = " ".join(args.topic)
    tweet = _tweet("5", f"{end_date}T23:59:59Z")
    with mock.patch.object(
        bird_x.subproc,
        "run_with_timeout",
        autospec=True,
        return_value=_response([tweet]),
    ) as run:
        if lane == "topic":
            result = bird_x.search_x(topic, from_date, to_date)
            items = bird_x.parse_bird_response(result, query=topic)
            expected = topic
        elif lane == "from":
            items = bird_x.search_handles(["subject"], topic, from_date, to_date=to_date)
            expected = "from:subject"
        else:
            items = bird_x.search_mentions(["subject"], from_date, to_date=to_date)
            expected = "@subject"

    assert [item["url"] for item in items] == [tweet["url"]]
    assert [call.args[0][2] for call in run.call_args_list] == [
        f"{expected} {expected_filters}"
    ]


@pytest.mark.parametrize("lane", ["from", "mentions"])
def test_handle_lanes_bound_the_inclusive_end_date(lane):
    tweet = _tweet("4", "2024-12-31T23:59:59Z")
    with mock.patch.object(
        bird_x.subproc,
        "run_with_timeout",
        autospec=True,
        return_value=_response([tweet]),
    ) as run:
        if lane == "from":
            items = bird_x.search_handles(
                ["subject"], "multi-agent", "2024-12-01", to_date="2024-12-31"
            )
            expected = "from:subject"
        else:
            items = bird_x.search_mentions(
                ["subject"], "2024-12-01", to_date="2024-12-31"
            )
            expected = "@subject"

    assert [item["url"] for item in items] == [tweet["url"]]
    assert run.call_args.args[0][2] == (
        f"{expected} since:2024-12-01 until:2025-01-01"
    )


def test_pipeline_passes_the_end_date_to_both_bird_handle_lanes():
    bundle = schema.RetrievalBundle()
    plan = schema.QueryPlan(
        intent="news",
        freshness_mode="balanced_recent",
        cluster_mode="timeline",
        raw_topic="subject",
        source_weights={"x": 1.0},
        subqueries=[schema.SubQuery(
            label="primary", search_query="subject", ranking_query="subject", sources=["x"]
        )],
    )
    with mock.patch.object(
        bird_x.subproc, "run_with_timeout", autospec=True, return_value=_response([])
    ) as run:
        pipeline._run_supplemental_searches(
            topic="subject",
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2024-12-01", "2024-12-31"),
            runtime=schema.ProviderRuntime(
                reasoning_provider="local", planner_model="", rerank_model="",
                x_search_backend="bird",
            ),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            x_handle="subject",
        )

    assert sorted(call.args[0][2] for call in run.call_args_list) == [
        "@subject since:2024-12-01 until:2025-01-01",
        "from:subject since:2024-12-01 until:2025-01-01",
    ]
