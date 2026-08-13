"""Reddit transport failures must fail the strict selected-source gate.

Regression coverage for issue #899: on datacenter egress Reddit may answer
429/403 on keyless lanes. A selected Reddit source must not become clean
``no-results`` or produce a reduced report; the actual adapter failure is
classified and surfaced through the secret-free doctor gate.
"""

import io
import urllib.error
from unittest import mock

import pytest

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


def test_reddit_rate_limit_fails_before_a_report(capsys):
    with pytest.raises(doctor.SourceGateError) as caught:
        _run_reddit_against(
            urllib.error.HTTPError(
                "https://www.reddit.com/search.rss", 429, "Too Many Requests", {}, None
            )
        )

    assert caught.value.failures["reddit"].state == schema.RATE_LIMITED
    stderr = capsys.readouterr().err
    assert "SOURCE_GATE_FAILED source=reddit" in stderr
    assert "reason=\"rate-limited\"" in stderr


def test_reddit_block_fails_before_a_report(capsys):
    with pytest.raises(doctor.SourceGateError) as caught:
        _run_reddit_against(
            urllib.error.HTTPError(
                "https://www.reddit.com/search.rss", 403, "Blocked", {}, None
            )
        )

    assert caught.value.failures["reddit"].state != schema.NO_RESULTS
    stderr = capsys.readouterr().err
    assert "SOURCE_GATE_FAILED source=reddit" in stderr


def test_reddit_gate_does_not_expose_transport_exception_text(capsys):
    private_message = "private-provider-body-must-not-appear"
    with pytest.raises(doctor.SourceGateError):
        _run_reddit_against(
            urllib.error.HTTPError(
                "https://www.reddit.com/search.rss", 429, private_message, {}, None
            )
        )

    assert private_message not in capsys.readouterr().err
