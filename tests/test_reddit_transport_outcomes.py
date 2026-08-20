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

from lib import doctor, pipeline, schema


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
