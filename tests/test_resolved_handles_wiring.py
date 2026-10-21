"""Supplemental handle discovery reaches the first-party ranking boundaries."""

import inspect
import threading
from unittest.mock import patch

from lib import pipeline, providers, schema


def _plan():
    return schema.QueryPlan(
        intent="exploration", freshness_mode="balanced_recent", cluster_mode="topic",
        raw_topic="Example research", source_weights={"x": 1.0},
        subqueries=[schema.SubQuery(label="primary", search_query="Example research",
                                   ranking_query="Example research", sources=["x"])],
    )


def test_out_param_is_part_of_the_contract():
    parameter = inspect.signature(pipeline._run_supplemental_searches).parameters["resolved_handles_out"]
    assert parameter.default is None


def test_collector_normalizes_deduplicates_and_requires_corroboration():
    output = ["existing"]
    with patch.object(pipeline.entity_extract, "extract_entities", return_value={
        "x_handles": ["@ExampleDev", "EXAMPLEDEV", "commentator", "@Existing"],
        "x_hashtags": [], "reddit_subreddits": [],
    }), patch.object(pipeline.env, "x_backend_chain", return_value=[]):
        pipeline._run_supplemental_searches(
            topic="Example research", bundle=schema.RetrievalBundle(), plan=_plan(),
            config={}, depth="default", date_range=("2026-03-01", "2026-03-31"),
            runtime=providers.mock_runtime({}, "default"), mock=False,
            rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
            x_handle="@Explicit", x_related=["@Related", "related"],
            resolved_handles_out=output,
        )
    assert output == ["existing", "explicit", "exampledev", "related"]


def _resolve(topic, extracted):
    """Run the Phase 2 collector with a fixed extracted-handle list; return the
    handles it resolved beyond the explicit anchor handle (the collector returns
    early with no X items and no explicit handle, so one anchor keeps it running)."""
    output = []
    with patch.object(pipeline.entity_extract, "extract_entities", return_value={
        "x_handles": extracted, "x_hashtags": [], "reddit_subreddits": [],
    }), patch.object(pipeline.env, "x_backend_chain", return_value=[]):
        pipeline._run_supplemental_searches(
            topic=topic, bundle=schema.RetrievalBundle(), plan=_plan(),
            config={}, depth="default", date_range=("2026-03-01", "2026-03-31"),
            runtime=providers.mock_runtime({}, "default"), mock=False,
            rate_limited_sources=set(), rate_limit_lock=threading.Lock(),
            x_handle="anchor_handle", resolved_handles_out=output,
        )
    return [h for h in output if h != "anchor_handle"]


def test_short_handles_are_not_corroborated_by_substring_overlap():
    """An extracted handle that is merely a short fragment of a topic word ("go"
    in "Google", "ai" in "Chair") does not name the topic and must not earn
    first-party status."""
    assert _resolve("Google Gemini", ["go", "google_ai_dev"]) == ["google_ai_dev"]
    assert _resolve("Chair review", ["ai"]) == []


def test_handle_containing_a_full_three_letter_topic_word_is_corroborated():
    """A short subject name ("Vue") inside a longer handle ("vuejs") is still
    the subject, while a short fragment of a longer topic word ("go" in
    "Google") is not."""
    assert _resolve("Vue", ["vuejs", "go"]) == ["vuejs"]
    assert _resolve("Google Gemini", ["go"]) == []
    # Scaffolding words never name the subject, even when contained in a handle.
    assert _resolve("Cursor review", ["reviewbot"]) == []


def test_handle_spelled_from_the_topic_name_is_still_corroborated():
    assert _resolve("Peter Steinberger", ["steipete", "commentator"]) == ["steipete"]


def test_discovered_handles_reach_real_fusion_and_reranking():
    raw = [
        {"id": "subject", "text": "A new release shipped today",
         "author_handle": "ExampleDev", "url": "https://x.com/ExampleDev/status/1",
         "date": "2026-03-15", "engagement": {"likes": 100}},
        {"id": "mention", "text": "Example research by ExampleDev is detailed",
         "author_handle": "observer", "url": "https://x.com/observer/status/2",
         "date": "2026-03-16", "engagement": {"likes": 20}},
    ]
    runtime = providers.mock_runtime({}, "default")
    with patch.object(pipeline.providers, "resolve_runtime", return_value=(runtime, None)), \
         patch.object(pipeline, "available_sources", return_value=["x"]), \
         patch.object(pipeline.env, "x_backend_chain", return_value=[]), \
         patch.object(pipeline, "_retrieve_stream", return_value=(raw, {})), \
         patch.object(pipeline, "_run_supplemental_searches", wraps=pipeline._run_supplemental_searches) as supplements, \
         patch.object(pipeline, "weighted_rrf", wraps=pipeline.weighted_rrf) as fuse, \
         patch.object(pipeline.rerank, "rerank_candidates", wraps=pipeline.rerank.rerank_candidates) as rank:
        report = pipeline.run(
            topic="Example research", config={}, depth="default", mock=False,
            requested_sources=["x"], web_backend="none", as_of_date="2026-03-31",
            external_plan=schema.to_dict(_plan()),
        )
    assert supplements.call_count == 1
    assert supplements.call_args.kwargs["resolved_handles_out"] == ["exampledev"]
    assert "exampledev" in fuse.call_args.kwargs["first_party_handles"]
    assert "observer" not in fuse.call_args.kwargs["first_party_handles"]
    assert rank.call_count == 2
    assert all("exampledev" in call.kwargs["resolved_handles"] for call in rank.call_args_list)
    assert any(item.author == "ExampleDev" for candidate in report.ranked_candidates
               for item in candidate.source_items)
