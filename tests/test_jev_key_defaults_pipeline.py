"""Key-driven ranking and follow-up deadlines have distinct lifetimes."""
from unittest.mock import patch

import pytest

from lib import pipeline, schema
from tests.test_adaptive_pipeline import answers_for


@pytest.mark.parametrize("provider,key", [
    ("typesafe", "TYPESAFE_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
])
@pytest.mark.parametrize("with_facets", [False, True])
def test_slow_retrieval_keeps_ordinary_ranking_but_bounds_followup(provider, key, with_facets, monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.env, "CONFIG_DIR", tmp_path)
    clock = [100.0]
    topic = "Rust release"
    plan = {
        "intent": "product", "freshness_mode": "balanced_recent", "cluster_mode": "theme",
        "subqueries": [{"label": "primary", "search_query": topic,
                        "ranking_query": topic, "sources": ["perplexity"], "weight": 1.0}],
    }
    raw = [{"id": "release", "title": topic, "url": "https://example.com/rust-release",
            "snippet": "Rust release adds compiler support.", "date": "2026-09-20"}]
    config = {key: "dummy-jev-key", "PERPLEXITY_API_KEY": "dummy-search-key"}
    if with_facets:
        config["_research_facets"] = [{"id": "release", "question": "What changed?",
                                      "query": "Rust compiler changes"}]
    else:
        config["_deep_research"] = True

    def retrieve(*args, **kwargs):
        clock[0] = 401.0
        return raw, {}

    def post(url, payload, **kwargs):
        assert url.endswith("/systemone")
        assert kwargs["timeout"] == 10.0
        assert kwargs["deadline_monotonic"] == 411.0
        return {"model": "jev-1.13.0" if provider == "typesafe" else "typesafe/jev-1.13",
                "usage": {"input_tokens": 100, "output_tokens": 10},
                "answers": answers_for(payload["questions"])}

    runtime = schema.ProviderRuntime("local", "deterministic", "local-score")
    with (
        patch.object(pipeline.time, "monotonic", side_effect=lambda: clock[0]),
        patch.object(pipeline.providers, "resolve_runtime", return_value=(runtime, None)),
        patch.object(pipeline, "available_sources", return_value=["perplexity"]),
        patch.object(pipeline, "_retrieve_stream", side_effect=retrieve) as search,
        patch.object(pipeline.http, "post", side_effect=post) as judge,
    ):
        report = pipeline.run(topic=topic, depth="quick", requested_sources=["perplexity"],
                              mock=False, web_backend="none", as_of_date="2026-09-28",
                              external_plan=plan, config=config)

    assert search.call_count == 1
    receipt = report.artifacts["jev_rerank"]["initial"]
    if with_facets:
        judge.assert_not_called()
        assert receipt["judge_route"] == "deterministic"
        assert report.artifacts["adaptive_research"]["status"] == "skipped"
    else:
        judge.assert_called_once()
        assert receipt["judge_route"] == f"jev:{provider}"
        assert all(candidate.rerank_score == 100 for candidate in report.ranked_candidates)
        assert "adaptive_research" not in report.artifacts
