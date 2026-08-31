"""Reddit transport failures must not be reported as a clean no-results.

Regression coverage for issue #899: on a datacenter egress Reddit answers
429/403 on the keyless lanes, the adapters swallow that into an empty list, and
the run used to export ``"reddit": "no-results"`` with ``doctor --postmortem``
printing "No failures on the last run." These tests drive the real pipeline and
the real postmortem renderer over a mocked socket, so the whole chain --
capture sink -> source outcome -> postmortem bucket -- stays honest.
"""

import urllib.error
from unittest import mock

from lib import doctor, http, pipeline, schema


def _plan(source):
    return {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "none",
        "source_weights": {source: 1.0},
        "subqueries": [{
            "label": "primary",
            "search_query": "claude code user feedback",
            "ranking_query": "claude code user feedback",
            "sources": [source],
        }],
    }


def _run_reddit_against(error):
    runtime = schema.ProviderRuntime("local", "test-planner", "test-reranker")
    with mock.patch.object(
        pipeline.providers, "resolve_runtime", return_value=(runtime, mock.Mock())
    ), mock.patch.object(
        pipeline, "available_sources", return_value=["reddit"]
    ), mock.patch("lib.http.time.sleep"), mock.patch(
        "lib.http.urllib.request.urlopen", side_effect=error
    ):
        return pipeline.run(
            topic="claude code user feedback",
            config={"EXCLUDE_SOURCES": ""},
            depth="quick",
            requested_sources=["reddit"],
            mock=False,
            as_of_date="2026-07-10",
            external_plan=_plan("reddit"),
        )


def _postmortem_from(report):
    return {
        "engine_version": "test",
        "mode": "postmortem",
        "present": True,
        "topic": report.topic,
        "at": report.generated_at,
        "outcomes": {
            source: schema.to_dict(outcome)
            for source, outcome in report.source_status.items()
        },
    }


def test_reddit_rate_limit_is_not_reported_as_clean_no_results():
    report = _run_reddit_against(
        urllib.error.HTTPError(
            "https://www.reddit.com/search.rss", 429, "Too Many Requests", {}, None
        )
    )

    outcome = report.source_status["reddit"]
    assert outcome.state == schema.RATE_LIMITED
    assert "429" in (outcome.detail or "")


def test_reddit_block_is_not_reported_as_clean_no_results():
    report = _run_reddit_against(
        urllib.error.HTTPError(
            "https://www.reddit.com/search.rss", 403, "Blocked", {}, None
        )
    )

    # 403 lands on auth-failed via http.classify_failure. The point of the test
    # is that a blocked host is a failure state at all, not which noun it gets.
    assert report.source_status["reddit"].state != schema.NO_RESULTS


def test_postmortem_does_not_claim_success_after_a_reddit_block():
    report = _run_reddit_against(
        urllib.error.HTTPError(
            "https://www.reddit.com/search.rss", 429, "Too Many Requests", {}, None
        )
    )

    text = doctor.render_postmortem_text(_postmortem_from(report))

    assert "No failures on the last run." not in text
    assert "Succeeded: reddit" not in text
    assert "Failed:" in text
    assert schema.RATE_LIMITED in text


def test_items_delivered_with_swallowed_lane_403_are_not_branded():
    """Regression (datacenter egress): a source that returns items must not be
    branded auth-failed/partial by a swallowed lane-level 403.

    On hosts where Reddit blocks the shreddit partials (HTTP 403), the capture
    sink records those failures even though the keyless lanes delivered items.
    Before the fix, ``_retrieve_stream`` attached the captured failure as the
    source outcome and the run reported ``auth-failed`` / ``partial after N
    items: HTTP 403: Blocked`` for a source that actually succeeded.
    """
    lane_error = http.HTTPError(
        "https://www.reddit.com/svc/shreddit/community-more-posts/top/?name=tea",
        status_code=403,
        body=b"Blocked",
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="matcha tea trends",
        ranking_query="matcha tea trends",
        sources=["reddit"],
    )
    with mock.patch.object(
        pipeline,
        "_retrieve_stream_impl",
        return_value=([{"url": "https://www.reddit.com/r/tea/comments/abc/"}], {}),
    ), mock.patch.object(http, "capture_failures") as cf, mock.patch.object(
        http, "fixture_module_capture"
    ):
        cf.return_value.__enter__.return_value = [lane_error]
        cf.return_value.__exit__.return_value = False
        items, artifact = pipeline._retrieve_stream(
            source="reddit",
            topic="matcha tea trends",
            subquery=subquery,
            config={},
            depth="quick",
            date_range=("2026-07-08", "2026-08-08"),
            runtime=schema.ProviderRuntime("local", "test-planner", "test-reranker"),
            mock=False,
            web_backend="auto",
        )

    assert items, "the run delivered items"
    assert "_source_outcome" not in artifact, (
        "swallowed lane failures must not brand a source that returned items"
    )


def test_swallowed_lane_403_still_brands_when_no_items_returned():
    """The no-items case keeps the transport-failure outcome (issue #899): the
    captured failure must still surface so doctor can prescribe a fix."""
    lane_error = http.HTTPError(
        "https://www.reddit.com/svc/shreddit/community-more-posts/top/?name=tea",
        status_code=403,
        body=b"Blocked",
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="matcha tea trends",
        ranking_query="matcha tea trends",
        sources=["reddit"],
    )
    with mock.patch.object(
        pipeline, "_retrieve_stream_impl", return_value=([], {})
    ), mock.patch.object(http, "capture_failures") as cf, mock.patch.object(
        http, "fixture_module_capture"
    ):
        cf.return_value.__enter__.return_value = [lane_error]
        cf.return_value.__exit__.return_value = False
        items, artifact = pipeline._retrieve_stream(
            source="reddit",
            topic="matcha tea trends",
            subquery=subquery,
            config={},
            depth="quick",
            date_range=("2026-07-08", "2026-08-08"),
            runtime=schema.ProviderRuntime("local", "test-planner", "test-reranker"),
            mock=False,
            web_backend="auto",
        )

    assert not items
    assert artifact.get("_source_outcome", {}).get("state") == schema.AUTH_FAILED


def test_items_delivered_with_swallowed_lane_failure_carry_detail():
    """A source that delivered items keeps ``ok`` but records what was lost, so
    ``doctor --postmortem`` can still show the swallowed sub-request failures."""
    lane_error = http.HTTPError(
        "https://www.reddit.com/svc/shreddit/community-more-posts/top/?name=tea",
        status_code=429,
        body=b"Too Many Requests",
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="matcha tea trends",
        ranking_query="matcha tea trends",
        sources=["reddit"],
    )
    with mock.patch.object(
        pipeline,
        "_retrieve_stream_impl",
        return_value=([{"url": "https://www.reddit.com/r/tea/comments/abc/"}], {}),
    ), mock.patch.object(http, "capture_failures") as cf, mock.patch.object(
        http, "fixture_module_capture"
    ):
        cf.return_value.__enter__.return_value = [lane_error, lane_error]
        cf.return_value.__exit__.return_value = False
        items, artifact = pipeline._retrieve_stream(
            source="reddit",
            topic="matcha tea trends",
            subquery=subquery,
            config={},
            depth="quick",
            date_range=("2026-07-08", "2026-08-08"),
            runtime=schema.ProviderRuntime("local", "test-planner", "test-reranker"),
            mock=False,
            web_backend="auto",
        )

    assert items
    assert "_source_outcome" not in artifact
    detail = artifact.get("_source_outcome_detail") or ""
    assert "2 sub-requests" in detail
    assert "429" in detail


def test_postmortem_shows_lane_detail_on_succeeded_source():
    outcome = schema.SourceOutcome(
        source="reddit",
        state="ok",
        items_returned=36,
        attempted=True,
        detail="3 sub-requests rate-limited (HTTP 429)",
    )
    pm = {
        "engine_version": "test",
        "mode": "postmortem",
        "present": True,
        "topic": "kanye west",
        "at": outcome.at,
        "outcomes": {"reddit": schema.to_dict(outcome)},
    }

    text = doctor.render_postmortem_text(pm)

    assert "Failed:" not in text
    assert "Partial:" not in text
    assert "Succeeded: reddit (36 items; 3 sub-requests rate-limited (HTTP 429))" in text


def test_swallowed_429s_flag_the_source_as_rate_limited_for_thin_retry():
    lane_error = http.HTTPError(
        "https://www.reddit.com/search.rss", status_code=429, body=b"Too Many Requests"
    )
    subquery = schema.SubQuery(
        label="primary", search_query="matcha", ranking_query="matcha", sources=["reddit"],
    )
    with mock.patch.object(
        pipeline, "_retrieve_stream_impl",
        return_value=([{"url": "https://www.reddit.com/r/tea/comments/abc/"}], {}),
    ), mock.patch.object(http, "capture_failures") as cf, mock.patch.object(http, "fixture_module_capture"):
        cf.return_value.__enter__.return_value = [lane_error]
        cf.return_value.__exit__.return_value = False
        _items, artifact = pipeline._retrieve_stream(
            source="reddit", topic="matcha", subquery=subquery, config={}, depth="quick",
            date_range=("2026-07-08", "2026-08-08"),
            runtime=schema.ProviderRuntime("local", "test-planner", "test-reranker"),
            mock=False, web_backend="auto",
        )
    assert artifact["_source_outcome_detail_state"] == schema.RATE_LIMITED
