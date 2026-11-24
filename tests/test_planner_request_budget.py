"""The engine planner must leave time for the source streams to run."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lib import dates, http, pipeline, providers, schema


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



def test_stalled_http_planner_yields_to_retrieval(monkeypatch):
    accepted = threading.Event()
    release = threading.Event()
    response_started = threading.Event()
    sources = []

    class StalledPlanner(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            accepted.set()
            release.wait(5)
            response_started.set()
            payload = b'{"choices":[{"message":{"content":"{}"}}]}'
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                # A client that has timed out will already have closed the socket.
                pass

        def log_message(self, *_):
            pass

    def retrieve(**kwargs):
        assert accepted.is_set()
        assert not response_started.is_set()
        sources.append(kwargs["source"])
        release.set()
        return [], {}

    with ThreadingHTTPServer(("127.0.0.1", 0), StalledPlanner) as server:
        monkeypatch.setenv("OPENROUTER_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setattr(pipeline, "PLANNER_TIMEOUT_SECONDS", 1.0)
        runtime = schema.ProviderRuntime("openrouter", "fixture-planner", "")
        provider = providers.OpenRouterClient("dummy")
        monkeypatch.setattr(providers, "resolve_runtime", lambda *_: (runtime, provider))
        monkeypatch.setattr(pipeline, "available_sources", lambda *_, **__: ["hackernews"])
        monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()
        try:
            report = pipeline.run(
                topic="SQLite incremental vacuum",
                config={},
                depth="quick",
                requested_sources=["hackernews"],
                web_backend="none",
            )
        finally:
            release.set()
            server.shutdown()
            server_thread.join()

    assert sources == ["hackernews"]
    assert "DeadlineExceeded" in " ".join(report.query_plan.notes)


def test_reranker_does_not_inherit_the_planner_request_budget(monkeypatch):
    runtime = schema.ProviderRuntime("openrouter", "fixture-planner", "fixture-reranker")
    provider = providers.OpenRouterClient("dummy")
    calls = {}

    def post(_url, payload, **kwargs):
        calls.setdefault(payload["model"], []).append(kwargs)
        if payload["model"] == "fixture-planner":
            raise http.DeadlineExceeded()
        return {"choices": [{"message": {"content": "{}"}}]}

    def retrieve(**_kwargs):
        return [{
            "id": "999",
            "title": "SQLite incremental vacuum makes unused pages reusable",
            "url": "https://example.com/sqlite-vacuum",
            "hn_url": "https://news.ycombinator.com/item?id=999",
            "author": "tester",
            "date": dates.get_date_range(0)[1],
            "engagement": {"points": 64, "comments": 12},
            "relevance": 1.0,
        }], {}

    monkeypatch.setattr(providers.http, "post", post)
    monkeypatch.setattr(providers, "resolve_runtime", lambda *_: (runtime, provider))
    monkeypatch.setattr(pipeline, "available_sources", lambda *_, **__: ["hackernews"])
    monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)
    report = pipeline.run(
        topic="SQLite incremental vacuum",
        config={},
        depth="quick",
        requested_sources=["hackernews"],
        web_backend="none",
    )

    assert report.ranked_candidates
    planned = calls["fixture-planner"][0]
    reranked = calls["fixture-reranker"][0]
    assert planned["retries"] == 2
    assert reranked["deadline_monotonic"] > planned["deadline_monotonic"]
    assert "retries" not in reranked
    assert "retry_dns" not in reranked
