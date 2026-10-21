"""Historical-window regressions through GitHub search and normalization."""

from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import pytest

from lib import github


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
        expected_query = "react created:2024-02-01..2024-02-29"
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
