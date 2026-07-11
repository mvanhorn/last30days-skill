import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from lib import bird_x, health, http, jobs, pipeline, reddit, render, schema, youtube_yt


def _report(*, source_status=None, items_by_source=None, errors_by_source=None):
    return schema.Report(
        topic="test topic",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T18:22:03Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="test-planner",
            rerank_model="test-reranker",
        ),
        query_plan=schema.QueryPlan(
            intent="general",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic="test topic",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="test topic",
                    ranking_query="test topic",
                    sources=["x"],
                )
            ],
            source_weights={"x": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source=items_by_source or {},
        errors_by_source=errors_by_source or {},
        source_status=source_status or {},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (http.HTTPError("HTTP 429", status_code=429), schema.RATE_LIMITED),
        (http.HTTPError("HTTP 401", status_code=401), schema.AUTH_FAILED),
        (http.HTTPError("HTTP 402", status_code=402), schema.AUTH_FAILED),
        (http.HTTPError("HTTP 403", status_code=403), schema.AUTH_FAILED),
        (http.HTTPError("Invalid JSON response"), schema.SCHEMA_DRIFT),
        (http.HTTPError("Connection error: reset"), schema.UNREACHABLE),
        (http.HTTPError("Request timed out"), health.TIMEOUT),
    ],
)
def test_http_error_exposes_run_outcome_state(error, expected):
    assert error.outcome_state == expected


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_http_wrapper_classifies_dns_failure(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known")
    )

    with pytest.raises(http.HTTPError) as caught:
        http.get("https://unreachable.example", retries=1)

    assert caught.value.outcome_state == schema.UNREACHABLE


def test_source_specific_text_failures_are_mapped():
    assert bird_x.classify_run_failure("likely Twitter anti-bot interstitial") == schema.SCHEMA_DRIFT
    assert reddit.classify_run_failure("blocked by Reddit interstitial") == schema.RATE_LIMITED
    assert youtube_yt.classify_run_failure("Sign in to confirm you're not a bot") == schema.RATE_LIMITED


def test_bundle_distinguishes_clean_no_results_from_failure():
    clean = schema.RetrievalBundle()
    clean.mark_attempted("x")

    failed = schema.RetrievalBundle()
    failed.mark_attempted("x")
    failed.record_failure("x", schema.RATE_LIMITED, "HTTP 429")

    assert clean.source_status["x"].state == schema.NO_RESULTS
    assert failed.source_status["x"].state == schema.RATE_LIMITED
    assert failed.source_status["x"].fix_hint == "doctor"


@patch("lib.http.urllib.request.urlopen")
def test_stream_adapter_recovers_http_failure_laundered_as_empty(mock_urlopen):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.example.com",
        401,
        "Unauthorized",
        {},
        None,
    )

    def source_that_launders_failure(*_args, **_kwargs):
        try:
            http.get("https://api.example.com", retries=1)
        except http.HTTPError:
            return [], {}
        raise AssertionError("request should have failed")

    with patch("lib.pipeline._retrieve_stream_impl", side_effect=source_that_launders_failure):
        items, artifact = pipeline._retrieve_stream()

    assert items == []
    assert artifact["_source_outcome"]["state"] == schema.AUTH_FAILED


@patch("lib.http.time.sleep")
@patch("lib.http.urllib.request.urlopen")
def test_reddit_nested_worker_propagates_failure_capture(mock_urlopen, _mock_sleep):
    mock_urlopen.side_effect = urllib.error.HTTPError(
        "https://api.scrapecreators.com/v1/reddit/search",
        429,
        "Too Many Requests",
        {},
        None,
    )

    with http.capture_failures() as failures:
        result = reddit.search_reddit(
            "test topic",
            "2026-06-10",
            "2026-07-10",
            depth="quick",
            token="dummy-token",
        )

    assert result["items"] == []
    assert failures[-1].outcome_state == schema.RATE_LIMITED


@patch("lib.http.urllib.request.urlopen")
def test_jobs_expected_probe_misses_do_not_degrade_final_result(mock_urlopen):
    miss = urllib.error.HTTPError(
        "https://boards-api.greenhouse.io/v1/boards/example/jobs",
        404,
        "Not Found",
        {},
        None,
    )
    success = MagicMock()
    success.status = 200
    success.read.return_value = (
        b'{"jobs":[{"id":"1","title":"Engineer",'
        b'"jobUrl":"https://jobs.ashbyhq.com/example/1"}]}'
    )
    success.__enter__.return_value = success
    success.__exit__.return_value = False
    mock_urlopen.side_effect = [miss, success]

    with patch("lib.jobs._candidate_slugs", return_value=["example"]):
        with http.capture_failures() as failures:
            provider, slug, _ = jobs._probe_ats("Example")

    assert provider == jobs.ATS_PROVIDER_ASHBY
    assert slug == "example"
    assert failures == []


@pytest.mark.parametrize(
    ("source", "artifact", "expected"),
    [
        ("perplexity", {"error": "timeout"}, health.TIMEOUT),
        (
            "grounding",
            {"reason": "keyless-search-unavailable"},
            schema.UNREACHABLE,
        ),
    ],
)
def test_stream_adapter_converts_legacy_error_artifacts(source, artifact, expected):
    with patch("lib.pipeline._retrieve_stream_impl", return_value=([], artifact)):
        _, converted = pipeline._retrieve_stream(source=source)

    assert converted["_source_outcome"]["state"] == expected


@pytest.mark.parametrize(
    ("source", "detail", "expected"),
    [
        ("truthsocial", "Truth Social token expired", schema.AUTH_FAILED),
        (
            "bluesky",
            "Cloudflare blocked the request (403 Forbidden). This is a network-level block, not an auth issue.",
            schema.UNREACHABLE,
        ),
    ],
)
def test_legacy_result_uses_source_specific_outcome(source, detail, expected):
    artifact = pipeline._result_outcome_artifact(source, {"error": detail})

    assert artifact["_source_outcome"]["state"] == expected


def test_captured_http_failure_overrides_generic_artifact_error():
    failure = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    outcome = pipeline._resolve_stream_outcome(
        "tiktok",
        pipeline._outcome_artifact(health.ERROR, "request failed"),
        [failure],
    )

    assert outcome["state"] == schema.RATE_LIMITED


def test_bundle_records_items_then_429_as_partial():
    item = schema.SourceItem(
        item_id="x1",
        source="x",
        title="A post",
        body="body",
        url="https://x.com/example/status/1",
    )
    bundle = schema.RetrievalBundle()
    bundle.mark_attempted("x")
    bundle.add_items("primary", "x", [item])
    bundle.record_failure("x", schema.RATE_LIMITED, "429 after first page")

    outcome = bundle.source_status["x"]
    assert outcome.state == schema.PARTIAL
    assert outcome.items_returned == 1
    assert outcome.detail == "429 after first page"


def test_pipeline_records_clean_empty_source_as_no_results():
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "test topic",
                "ranking_query": "test topic",
                "sources": ["x"],
            }
        ],
        "source_weights": {"x": 1.0},
    }
    with patch("lib.pipeline._retrieve_stream", return_value=([], {})):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["x"],
            mock=True,
            external_plan=plan,
        )

    assert report.source_status["x"].state == schema.NO_RESULTS
    assert "x" not in report.errors_by_source


def test_pipeline_preserves_typed_http_failure():
    plan = {
        "intent": "general",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "story",
        "subqueries": [
            {
                "label": "primary",
                "search_query": "test topic",
                "ranking_query": "test topic",
                "sources": ["x"],
            }
        ],
        "source_weights": {"x": 1.0},
    }
    failure = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    with patch("lib.pipeline._retrieve_stream", side_effect=failure):
        report = pipeline.run(
            topic="test topic",
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["x"],
            mock=True,
            external_plan=plan,
        )

    assert report.source_status["x"].state == schema.RATE_LIMITED
    assert report.source_status["x"].items_returned == 0
    assert "x" in report.errors_by_source


def test_footer_and_synthesis_note_surface_failed_source():
    report = _report(
        source_status={
            "x": schema.SourceOutcome(
                source="x",
                state=schema.RATE_LIMITED,
                detail="HTTP 429 after retry budget",
                fix_hint="doctor",
            )
        },
        errors_by_source={"x": "HTTP 429 after retry budget"},
    )

    text = render.render_compact(report)

    assert "## Partial Coverage" in text
    assert "Do not interpret a failed source as no discussion" in text
    assert "🔵 X: rate-limited: HTTP 429 after retry budget (run doctor for fixes)" in text


def test_report_source_status_round_trips_through_schema_serialization():
    report = _report(
        source_status={
            "x": schema.SourceOutcome(
                source="x",
                state=schema.PARTIAL,
                items_returned=12,
                detail="429 after 12 items",
                at="2026-07-10T18:22:03Z",
                fix_hint="doctor",
            )
        }
    )

    payload = schema.to_dict(report)
    restored = schema.report_from_dict(payload)

    assert payload["source_status"]["x"]["state"] == schema.PARTIAL
    assert restored.source_status["x"] == report.source_status["x"]


# --- strict exit (LAST30DAYS_STRICT_EXIT, issue #384) ---

import last30days as cli


def _outcome(source, state, **kwargs):
    return schema.SourceOutcome(source=source, state=state, **kwargs)


def test_strict_exit_disabled_by_default_even_when_degraded():
    report = _report(
        source_status={"x": _outcome("x", schema.RATE_LIMITED, detail="429")}
    )
    assert cli._strict_exit_code(report, None, {}) == 0


def test_strict_exit_returns_3_for_degraded_run(capsys):
    report = _report(
        source_status={"x": _outcome("x", schema.AUTH_FAILED, detail="401")}
    )
    rc = cli._strict_exit_code(report, None, {"LAST30DAYS_STRICT_EXIT": "1"})
    assert rc == 3
    assert "strict-exit: degraded sources: x" in capsys.readouterr().err


def test_strict_exit_clean_states_return_0():
    report = _report(
        source_status={
            "reddit": _outcome("reddit", health.OK, items_returned=12),
            "hn": _outcome("hn", schema.NO_RESULTS),
            "tiktok": _outcome("tiktok", schema.SKIPPED_UNCONFIGURED, attempted=False),
        }
    )
    assert cli._strict_exit_code(report, None, {"LAST30DAYS_STRICT_EXIT": "true"}) == 0


def test_strict_exit_checks_entity_reports_in_comparison_runs():
    lead = _report(source_status={"reddit": _outcome("reddit", health.OK)})
    entity = _report(
        source_status={"x": _outcome("x", schema.UNREACHABLE, detail="dns")}
    )
    rc = cli._strict_exit_code(lead, [("other", entity)], {"LAST30DAYS_STRICT_EXIT": "on"})
    assert rc == 3


def test_strict_exit_env_key_is_registered():
    # Unregistered keys are silently dropped by env config loading (#707 class).
    from lib import env as env_module
    import inspect

    assert "LAST30DAYS_STRICT_EXIT" in inspect.getsource(env_module)


def test_captured_failure_selection_prefers_most_specific():
    auth = http.HTTPError("HTTP 401: Unauthorized", status_code=401)
    rate = http.HTTPError("HTTP 429: Too Many Requests", status_code=429)
    # Order must not matter: auth-failed wins over rate-limited either way.
    for failures in ([auth, rate], [rate, auth]):
        outcome = pipeline._resolve_stream_outcome("x", None, failures)
        assert outcome["state"] == schema.AUTH_FAILED
