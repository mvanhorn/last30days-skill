"""The person's own LinkedIn posts in a person run.

Posts inside the window become ranked ``linkedin_profile`` items; older posts
(up to a year back) go to the Person Profile artifact only. These tests drive
``pipeline.run`` with the Deepline posts read faked at ``lib.http.request``
and every other source faked at ``_retrieve_stream``, and read the report.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from lib import dates, http, person, pipeline, render

NAME = "Dana Okafor"
PROFILE_URL = "https://www.linkedin.com/in/dana-okafor"
POSTS_TOOL = "harvestapi_get_profile_posts"
PLAN = {
    "intent": "research", "freshness_mode": "balanced_recent", "cluster_mode": "topic",
    "subqueries": [{"label": "primary", "search_query": NAME,
                    "ranking_query": f"What has {NAME} done lately?", "sources": ["reddit"]}],
}


def _days_ago(days: int) -> str:
    _, to_date = dates.get_date_range(30)
    return (date.fromisoformat(to_date) - timedelta(days=days)).isoformat()


def _context(**overrides) -> person.PersonContext:
    ctx = person.PersonContext(
        full_name=NAME, employer_name="Northwind Freight", employer_domain="northwind.example",
        linkedin_url=PROFILE_URL, anchors=["Northwind Freight", "northwind.example", PROFILE_URL],
        linkedin_status="checked", profile={"source": "deepline", "current_roles": []},
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _element(index: int, days: int, text: str, *, reshare: bool = False) -> dict:
    element = {
        "id": f"p{index}",
        "linkedinUrl": f"https://www.linkedin.com/posts/dana-okafor-{index}",
        "content": text,
        "postedAt": {"date": f"{_days_ago(days)}T09:00:00Z"},
        "author": {"name": NAME, "linkedinUrl": PROFILE_URL},
        "engagement": {"likes": 40 + index, "comments": 3, "shares": 1},
    }
    if reshare:
        element["repost"] = {"content": text}
    return element


def _posts_response(elements: list[dict]) -> dict:
    return {"status": "completed", "result": {"data": {"elements": elements, "pagination": {}}}}


def _fake_http(elements=None, *, error: http.HTTPError | None = None):
    calls: list[dict] = []

    def request(method, url, **kwargs):
        calls.append({"url": url, "payload": (kwargs.get("json_data") or {}).get("payload")})
        assert url.endswith(f"/{POSTS_TOOL}/execute"), url
        if error is not None:
            raise error
        return _posts_response(elements or [])

    return request, calls


def _run(config: dict, *, request=None, sources=("reddit",)):
    patches = [
        patch.object(pipeline, "_retrieve_stream", return_value=([], {})),
        patch.object(pipeline, "_retry_thin_sources"),
    ]
    if request is not None:
        patches.append(patch.object(http, "request", side_effect=request))
    for p in patches:
        p.start()
    try:
        return pipeline.run(
            topic=NAME, config=config, depth="quick", requested_sources=list(sources),
            web_backend="none", external_plan=PLAN,
        )
    finally:
        for p in reversed(patches):
            p.stop()


def test_in_window_posts_rank_and_older_posts_go_to_the_artifact_only():
    ctx = _context()
    request, calls = _fake_http([
        _element(1, 5, "Our cold-chain lanes cut dwell time by a third this quarter."),
        _element(2, 20, "Hiring two planners for the Rotterdam hub this month."),
        _element(3, 120, "Notes from the regional logistics meetup in the spring."),
    ])
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    assert len(calls) == 1
    assert calls[0]["payload"]["scrapePostedLimit"] == "year"
    ranked_urls = {c.url for c in report.ranked_candidates}
    assert ranked_urls == {
        "https://www.linkedin.com/posts/dana-okafor-1",
        "https://www.linkedin.com/posts/dana-okafor-2",
    }
    items = report.items_by_source["linkedin_profile"]
    assert len(items) == 2
    assert all(item.metadata["identity_anchor"] is True for item in items)
    assert all(item.author == NAME for item in items)
    assert all(item.engagement.get("likes") for item in items)

    profile = report.artifacts["person_profile"]
    assert [post["url"] for post in profile["older_posts"]] == [
        "https://www.linkedin.com/posts/dana-okafor-3",
    ]
    assert profile["older_posts"][0]["date"] == _days_ago(120)
    assert profile["in_window_posts"] == 2
    assert profile["first_party_in_window"] == 2
    assert report.source_status["linkedin_profile"].items_returned == 2


def test_older_posts_are_newest_first_truncated_and_capped():
    ctx = _context()
    long_text = "Freight planning notes " * 40
    request, _ = _fake_http(
        [_element(i, 40 + 30 * i, long_text) for i in range(1, 9)]
        + [_element(20, 500, "Too old to show.")]
    )
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    older = report.artifacts["person_profile"]["older_posts"]
    assert len(older) == 6
    assert [post["date"] for post in older] == sorted((p["date"] for p in older), reverse=True)
    assert all(len(post["text"]) <= 301 for post in older)
    assert "Too old to show." not in {post["text"] for post in older}
    assert not report.items_by_source.get("linkedin_profile")


def test_reshares_carry_the_flag_and_render_as_reshares():
    ctx = _context()
    request, _ = _fake_http([
        _element(1, 3, "Northwind Freight opens a new cross-dock in Rotterdam.", reshare=True),
        _element(2, 60, "An older reshare about warehouse robotics.", reshare=True),
    ])
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    [item] = report.items_by_source["linkedin_profile"]
    assert item.metadata["is_reshare"] is True
    assert report.artifacts["person_profile"]["older_posts"][0]["is_reshare"] is True
    rendered = render.render_compact(report)
    assert "Reshared: Northwind Freight opens a new cross-dock" in rendered


def test_a_post_another_member_wrote_counts_as_a_reshare():
    ctx = _context()
    other = _element(1, 3, "Episode 12 with Dana Okafor is out now.")
    other["author"] = {"name": "Lee Marsh", "linkedinUrl": "https://www.linkedin.com/in/lee-marsh",
                       "publicIdentifier": "lee-marsh"}
    own = _element(2, 5, "Our Rotterdam cross-dock is live.")
    own["author"]["publicIdentifier"] = "dana-okafor"
    request, _ = _fake_http([other, own])
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    flags = {item.url: item.metadata["is_reshare"] for item in report.items_by_source["linkedin_profile"]}
    assert flags == {
        "https://www.linkedin.com/posts/dana-okafor-1": True,
        "https://www.linkedin.com/posts/dana-okafor-2": False,
    }


def test_posts_read_failure_is_recorded_and_the_run_completes():
    ctx = _context()
    request, _ = _fake_http(error=http.HTTPError("server error", status_code=500))
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    status = report.source_status["linkedin_profile"]
    assert status.state != "ok"
    assert status.items_returned == 0
    assert "linkedin_profile" in report.errors_by_source
    assert report.artifacts["person_profile"]["older_posts"] == []


def test_no_posts_in_window_records_no_results_without_a_keyword_backfill():
    ctx = _context()
    request, _ = _fake_http([_element(1, 90, "A quarter-old post about routing.")])
    report = _run({"DEEPLINE_API_KEY": "test-key", "_person": ctx}, request=request)

    assert report.source_status["linkedin_profile"].state == "no-results"
    assert not report.items_by_source.get("linkedin_profile")
    assert len(report.artifacts["person_profile"]["older_posts"]) == 1


def test_scrapecreators_profile_posts_feed_the_lane_without_a_paid_read():
    ctx = _context(profile={"source": "scrapecreators", "posts": [
        {"url": "https://www.linkedin.com/posts/dana-okafor-9", "date": _days_ago(4),
         "text": "Kicking off the winter peak plan with the dispatch team.", "is_reshare": False},
        {"url": "https://www.linkedin.com/posts/dana-okafor-8", "date": _days_ago(75),
         "text": "Summer recap from the freight desk.", "is_reshare": False},
    ]})

    def no_network(*_args, **_kwargs):
        raise AssertionError("no paid call expected")

    report = _run({"SCRAPECREATORS_API_KEY": "sc-key", "_person": ctx}, request=no_network)
    assert [i.url for i in report.items_by_source["linkedin_profile"]] == [
        "https://www.linkedin.com/posts/dana-okafor-9",
    ]
    assert len(report.artifacts["person_profile"]["older_posts"]) == 1


@pytest.mark.parametrize("config", [
    {"DEEPLINE_API_KEY": "test-key"},
    {"DEEPLINE_API_KEY": "test-key", "_person": _context(linkedin_status="no key")},
    {"DEEPLINE_API_KEY": "test-key", "_person": _context(linkedin_url="")},
    {"_person": _context()},
])
def test_source_unavailable_without_a_person_run_an_anchored_profile_and_a_key(config):
    assert "linkedin_profile" not in pipeline.available_sources(config)


def test_source_available_for_an_anchored_person_run_with_a_key():
    assert "linkedin_profile" in pipeline.available_sources(
        {"DEEPLINE_API_KEY": "test-key", "_person": _context()}
    )


def test_non_person_run_reads_no_posts_and_carries_no_person_profile():
    def no_network(*_args, **_kwargs):
        raise AssertionError("no posts read expected")

    report = _run({"DEEPLINE_API_KEY": "test-key"}, request=no_network)
    assert "linkedin_profile" not in report.source_status
    assert "person_profile" not in report.artifacts


def test_person_run_without_a_linkedin_check_still_carries_the_person_profile():
    ctx = _context(linkedin_status="no key", linkedin_url="", profile={})
    report = _run({"_person": ctx})
    profile = report.artifacts["person_profile"]
    assert profile["full_name"] == NAME
    assert profile["linkedin_status"] == "no key"
    assert profile["in_window_posts"] == 0
    assert "linkedin_profile" not in report.source_status
