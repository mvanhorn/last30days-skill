"""GitHub repo/profile counters keep their own units instead of posing as comments."""

import io
import urllib.parse
from unittest.mock import patch

import pytest

from lib import github, normalize, render, schema

START, END = "2026-10-01", "2026-10-25"


def _fake_json(url, **kwargs):
    parsed = urllib.parse.urlsplit(url)
    if "/events/public" in parsed.path:
        return [{
            "id": "event-1", "type": "PushEvent", "actor": {"login": "alice"},
            "repo": {"name": "alice/own"}, "created_at": "2026-10-20T12:00:00Z",
        }]
    if "/users/alice/repos" in parsed.path:
        return [{
            "full_name": "alice/own", "stargazers_count": 123,
            "open_issues_count": 17, "description": "Own repo", "fork": False,
        }]
    if parsed.path == "/search/issues":
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
        if "author:alice" in query:
            merged = "is:merged" in query
            return {
                "total_count": 3 if merged else 8,
                "items": [{"html_url": "https://github.com/other/ext/pull/1"}] if merged else [],
            }
        return {"total_count": 1, "items": [{
            "title": "Real issue", "comments": 9, "reactions": {"total_count": 5},
            "html_url": "https://github.com/alice/own/issues/1",
        }]}
    if parsed.path.endswith("/releases"):
        return []
    if parsed.path.startswith("/repos/"):
        return {
            "stargazers_count": 123, "open_issues_count": 17,
            "description": "Repo description", "language": "Python", "forks_count": 4,
        }
    raise AssertionError(f"Unexpected HTTP endpoint: {url}")


@pytest.fixture(autouse=True)
def _offline_github():
    with patch.object(github, "_fetch_json", side_effect=_fake_json), patch.object(
        github.http, "open_request", side_effect=lambda *a, **k: io.BytesIO(b"README")
    ):
        yield


def _person_item(label):
    items = github.search_github_person("alice", START, END, token="dummy-token")
    return next(x for x in items if label in x["metadata"]["labels"])


@pytest.mark.parametrize(
    "collect, key, count",
    [
        (lambda: github._person_recent_pushes(
            "alice", START, END, github.PERSON_DEPTH_LIMITS["default"], "dummy-token")[0],
         "open_issues", 17),
        (lambda: _person_item("own-repo"), "open_issues", 17),
        (lambda: _person_item("external-repo"), "contributed_prs", 1),
        (lambda: _person_item("velocity"), "total_prs", 8),
        (lambda: github.search_github_project(["alice/own"], START, END, token="dummy-token")[0],
         "open_issues", 17),
    ],
    ids=["recent-push", "own-repo", "external-repo", "velocity", "project"],
)
def test_collector_counters_are_not_comments(collect, key, count):
    item = collect()
    assert item["engagement"][key] == count
    assert "comments" not in item["engagement"]
    assert item["metadata"].get("comment_count", 0) == 0
    assert normalize._normalize_github("github", item, 0, START, END).engagement[key] == count


def _item(item_id, engagement, url="https://github.com/alice/own"):
    return schema.SourceItem(
        item_id=item_id, source="github", title="t", body="", url=url, engagement=engagement,
    )


def test_footer_labels_each_counter_and_keeps_real_comments():
    report = schema.Report(
        topic="t", range_from=START, range_to=END, generated_at=f"{END}T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini", planner_model="m", rerank_model="m",
        ),
        query_plan=schema.QueryPlan(
            intent="general", freshness_mode="balanced_recent", cluster_mode="story",
            raw_topic="t", subqueries=[], source_weights={"github": 1.0},
        ),
        clusters=[], ranked_candidates=[], errors_by_source={},
        items_by_source={"github": [
            _item("repo", {"stars": 1200, "open_issues": 17}),
            _item("profile", {"merged_prs": 3, "total_prs": 8}, "https://github.com/alice"),
            _item("ext", {"stars": 5, "contributed_prs": 2}, "https://github.com/other/ext"),
            _item("issue", {"comments": 9, "reactions": 4}, "https://github.com/alice/own/issues/1"),
        ]},
    )
    assert render._build_source_footer_lines(report) == [
        "🐙 GitHub: 4 items │ 1,205 stars │ 3 merged │ 4 reactions │ 17 open issues/PRs"
        " │ 8 total PRs │ 2 contributed PRs │ 9 comments"
    ]


def test_aggregate_stats_tell_total_and_contributed_prs_apart():
    items = [
        _item("profile", {"merged_prs": 3, "total_prs": 8}, "https://github.com/alice"),
        _item("ext", {"stars": 5, "contributed_prs": 2}, "https://github.com/other/ext"),
    ]
    assert render._aggregate_engagement("github", items) == "5stars, 3merged, 8PRs, 2contrib"
