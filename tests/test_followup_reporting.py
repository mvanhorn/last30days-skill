"""Follow-up failures must preserve evidence and report its actual source state."""

import socket

import pytest

from lib import health, pipeline, render, schema
from tests.test_adaptive_pipeline import FACETS, RANGE, TOPIC, answers_for


@pytest.mark.parametrize("policy", ["fixed", "adaptive"])
@pytest.mark.parametrize("initial_count,successful_followups", [(1, 0), (1, 1), (0, 0)])
def test_followup_timeout_reports_partial_only_when_evidence_survives(
    monkeypatch, tmp_path, policy, initial_count, successful_followups
):
    search_queries = []

    def transport(url, payload, **kwargs):
        if "systemone" in url:
            return {"model": "jev-1.13.0", "answers": answers_for(payload["questions"]), "usage": {}}
        assert url == "https://api.perplexity.ai/search"
        search_queries.append(payload["query"])
        if payload["query"] == TOPIC:
            if not initial_count:
                return {"results": []}
            result = {
                "title": "Atlas coding tool release",
                "url": "https://example.com/atlas-release",
                "snippet": "Atlas coding tool released retry support. No user trial is reported.",
            }
        elif len(search_queries) <= 1 + successful_followups:
            result = {
                "title": "Atlas coding tool users report editor crashes",
                "url": "https://example.com/atlas-editor-crashes",
                "snippet": "Atlas coding tool users report crashes when opening large files.",
            }
        else:
            raise TimeoutError("offline follow-up timeout")
        return {"results": [{**result, "date": "2026-09-24"}]}

    def reject_network(*args, **kwargs):
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(pipeline.env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "available_sources", lambda *args, **kwargs: ["perplexity"])
    monkeypatch.setattr(
        pipeline.providers,
        "resolve_runtime",
        lambda *args, **kwargs: (
            schema.ProviderRuntime("local", "deterministic", "local-score"), None
        ),
    )
    monkeypatch.setattr(pipeline.http, "post", transport)
    plan = {
        "intent": "product", "freshness_mode": "balanced_recent", "cluster_mode": "theme",
        "subqueries": [{"label": "primary", "search_query": TOPIC,
                        "ranking_query": TOPIC, "sources": ["perplexity"], "weight": 1.0}],
    }
    report = pipeline.run(
        topic=TOPIC, depth="quick", mock=False, web_backend="none",
        requested_sources=["perplexity"], external_plan=plan, as_of_date=RANGE[1],
        config={
            "PERPLEXITY_API_KEY": "dummy-pplx", "TYPESAFE_API_KEY": "dummy-jev",
            "LAST30DAYS_JEV_PROVIDER": "typesafe", "LAST30DAYS_PERPLEXITY_MODE": "search",
            "LAST30DAYS_PERPLEXITY_SEARCH_TYPE": "fast", "_research_policy": policy,
            "_research_facets": FACETS,
        },
    )

    retained = initial_count + successful_followups
    assert len(search_queries) == 2 + successful_followups
    assert len(report.items_by_source["perplexity"]) == retained
    assert len(report.ranked_candidates) == retained
    outcome = report.source_status["perplexity"]
    assert outcome.state == (health.PARTIAL if retained else health.TIMEOUT)
    assert outcome.items_returned == retained
    assert outcome.detail == "Adaptive search failed"
    receipt = report.artifacts[f"{policy}_research"]
    assert receipt["status"] == "partial"
    assert receipt["reason"] == "search_failed"
    assert receipt["searches"][-1]["failure_state"] == health.TIMEOUT

    full = render.render_full(report)
    compact = render.render_compact(report)
    if retained:
        assert "perplexity" not in report.errors_by_source
        assert "Some sources returned partial results (degraded): perplexity" in full
        assert "Some sources failed: perplexity" not in full
        assert "Perplexity partial" in compact
    else:
        assert "perplexity" in report.errors_by_source
        assert "Some sources failed: perplexity" in full
        assert "Some sources returned partial results (degraded): perplexity" not in full
