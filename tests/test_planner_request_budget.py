"""The engine planner must leave time for the source streams to run."""

import threading
import time

import pytest

from lib import http, pipeline, providers, schema


@pytest.mark.parametrize("inherited", [False, True])
def test_planner_deadline_and_retries_allow_fallback_retrieval(monkeypatch, inherited):
    provider = providers.OpenRouterClient("dummy")
    runtime = schema.ProviderRuntime(
        reasoning_provider="openrouter",
        planner_model="fixture-planner",
        rerank_model="",
        x_search_backend="none",
    )
    calls = []
    sources = []
    cancel = threading.Event()
    start = time.monotonic()
    inherited_deadline = start + 15
    config = {"_enrich_cancel": cancel}
    if inherited:
        config["_research_deadline"] = inherited_deadline

    def post(_url, _payload, **kwargs):
        calls.append(kwargs)
        raise http.DeadlineExceeded()

    def retrieve(**kwargs):
        sources.append(kwargs["source"])
        return [], {}

    monkeypatch.setattr(providers.http, "post", post)
    monkeypatch.setattr(providers, "resolve_runtime", lambda *_: (runtime, provider))
    monkeypatch.setattr(pipeline, "available_sources", lambda *_, **__: ["hackernews"])
    monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)
    bundle = pipeline.run(
        topic="SQLite incremental vacuum",
        config=config,
        depth="quick",
        requested_sources=["hackernews"],
        web_backend="none",
    )

    assert "hackernews" in sources
    assert "fallback-plan" in " ".join(bundle.query_plan.notes)
    assert len(calls) == 1
    request = calls[0]
    assert request["cancel"] is cancel
    assert request["retries"] == 2
    assert request["retry_dns"] is False
    if inherited:
        assert request["deadline_monotonic"] == inherited_deadline
    else:
        assert start < request["deadline_monotonic"] <= time.monotonic() + 30
