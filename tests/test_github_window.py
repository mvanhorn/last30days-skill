"""Historical-window regressions through GitHub search and normalization."""

from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from lib import github, pipeline, schema


@pytest.mark.parametrize("depth, count", [("quick", 15), ("default", 30), ("deep", 60)])
@pytest.mark.parametrize("token", [None, "test-token"])
def test_search_preserves_both_window_bounds_in_every_query(depth, count, token):
    with patch.object(github, "_resolve_token", return_value=token), patch.object(
        github, "_fetch_json", return_value={"items": []}, autospec=True
    ) as fetch:
        github.search_github(
            "react created:>2025-01-01", "2024-02-01", "2024-02-29", depth=depth
        )

    expected_qualifiers = ["is:issue", "is:pull-request"] if token else [None]
    assert fetch.call_count == len(expected_qualifiers)
    for call, qualifier in zip(fetch.call_args_list, expected_qualifiers):
        params = parse_qs(urlparse(call.args[0]).query)
        expected_query = "is:public react created:2024-02-01..2024-02-29 in:title"
        if qualifier:
            expected_query += f" {qualifier}"
        assert params["q"] == [expected_query]
        expected_count = count if token else 10
        assert params["per_page"] == [str(expected_count)]
        assert params["sort"] == ["reactions"]
        assert params["order"] == ["desc"]
        assert call.kwargs["token"] == token


@pytest.mark.parametrize("token", [None, "test-token"])
def test_search_and_parse_include_full_boundary_days_and_exclude_adjacent_days(token):
    timestamps = [
        "2024-01-31T23:59:59Z",
        "2024-02-01T00:00:00Z",
        "2024-02-29T23:59:59Z",
        "2024-03-01T00:00:00Z",
    ]
    raw_items = [
        {
            "id": index,
            "html_url": f"https://github.com/example/react/issues/{index}",
            "title": f"React issue {index}",
            "created_at": timestamp,
            "reactions": {"total_count": index},
        }
        for index, timestamp in enumerate(timestamps, start=1)
    ]
    with patch.object(github, "_resolve_token", return_value=token), patch.object(
        github, "_fetch_json", return_value={"items": raw_items}, autospec=True
    ):
        response = github.search_github("react", "2024-02-01", "2024-02-29")

    items = github.parse_github_response(response)

    assert {item["url"] for item in items} == {
        "https://github.com/example/react/issues/2",
        "https://github.com/example/react/issues/3",
    }
    assert {item["date"] for item in items} == {"2024-02-01", "2024-02-29"}


def test_search_github_title_only_false_omits_in_title():
    with patch.object(github, "_resolve_token", return_value="test-token"), patch.object(
        github, "_fetch_json", return_value={"items": []}, autospec=True
    ) as fetch:
        github.search_github(
            "Claude Code subagents worktrees release",
            "2026-03-01",
            "2026-03-31",
            title_only=False,
        )

    assert fetch.call_count == 2
    for call in fetch.call_args_list:
        params = parse_qs(urlparse(call.args[0]).query)
        assert "in:title" not in params["q"][0]


def test_pipeline_github_retries_without_in_title_when_expanded_query_misses():
    calls = []

    def _fake_search(query, from_date, to_date, depth="default", token=None, *, title_only=True):
        calls.append((query, title_only))
        if title_only:
            return {
                "items": [],
                "context": {"core": query, "from_date": from_date, "to_date": to_date, "count": 30},
            }
        return {
            "items": [
                {
                    "id": 99,
                    "html_url": "https://github.com/anthropics/claude-code/issues/99",
                    "title": "Claude Code hooks stop firing",
                    "body": "Affects subagents worktrees release flow.",
                    "created_at": "2026-03-15T00:00:00Z",
                    "reactions": {"total_count": 25},
                }
            ],
            "context": {"core": query, "from_date": from_date, "to_date": to_date, "count": 30},
        }

    sq = schema.SubQuery(
        label="primary",
        search_query="Claude Code subagents worktrees release",
        ranking_query="Claude Code",
        sources=["github"],
    )
    with patch.object(github, "resolve_token", return_value=None), patch.object(
        github, "search_github", side_effect=_fake_search
    ):
        items, _artifact = pipeline._retrieve_stream_impl(
            topic="Claude Code",
            subquery=sq,
            source="github",
            config={},
            depth="default",
            date_range=("2026-03-01", "2026-03-31"),
            runtime=schema.ProviderRuntime(
                reasoning_provider="local",
                planner_model="local",
                rerank_model="local",
            ),
            mock=False,
            raw_topic="Claude Code",
        )

    assert calls == [
        ("Claude Code subagents worktrees release", True),
        ("Claude Code subagents worktrees release", False),
    ]
    assert len(items) == 1
    assert items[0]["title"] == "Claude Code hooks stop firing"


def test_pipeline_github_does_not_retry_without_in_title_on_error():
    calls = []

    def _fake_search(query, from_date, to_date, depth="default", token=None, *, title_only=True):
        calls.append(title_only)
        return {
            "items": [],
            "error": "rate limit exceeded",
            "context": {"core": query, "from_date": from_date, "to_date": to_date, "count": 30},
        }

    sq = schema.SubQuery(
        label="primary",
        search_query="Claude Code subagents",
        ranking_query="Claude Code",
        sources=["github"],
    )
    with patch.object(github, "resolve_token", return_value=None), patch.object(
        github, "search_github", side_effect=_fake_search
    ):
        items, _artifact = pipeline._retrieve_stream_impl(
            topic="Claude Code",
            subquery=sq,
            source="github",
            config={},
            depth="default",
            date_range=("2026-03-01", "2026-03-31"),
            runtime=schema.ProviderRuntime(
                reasoning_provider="local",
                planner_model="local",
                rerank_model="local",
            ),
            mock=False,
            raw_topic="Claude Code",
        )

    assert calls == [True]
    assert items == []
