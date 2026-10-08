"""grok's three supplement lanes, wired into the pipeline.

The coverage requirement: for an entity topic the run must return what the
subject said, what others said *to* them, and what others said *about* them by
name. The third is not redundant with the second -- most discussion never
@-mentions the subject, so a mention-only lane structurally cannot reach it.
"""

import inspect

import pytest

import last30days as cli
from lib import pipeline, schema


def _supplements_source():
    return inspect.getsource(pipeline._run_supplemental_searches)


def test_grok_is_handle_lane_capable():
    src = _supplements_source()
    assert '("grok", "bird", "xapi", "xquik")' in src, (
        "grok supports from:/@ natively; leaving it out of the capable set "
        "silently drops all of Phase 2 for grok users, as it already does for "
        "xai and xurl. xapi (X API v2 bearer) runs the same lanes, after "
        "bird and before xquik (R7)."
    )


def test_xapi_lane_branch_sits_between_bird_and_xquik():
    src = _supplements_source()
    grok = src.index('if primary == "grok":')
    bird = src.index('elif primary == "bird":')
    xapi = src.index('elif primary == "xapi":')
    xquik = src.index('elif primary == "xquik":')
    assert grok < bird < xapi < xquik
    xapi_block = src[xapi:xquik]
    assert "x_api.search_handles" in xapi_block
    assert "x_api.search_mentions" in xapi_block


def test_all_three_lanes_are_defined_for_grok():
    src = _supplements_source()
    grok_block = src[src.index('if primary == "grok":'):src.index('elif primary == "bird":')]
    assert "_from_lane" in grok_block
    assert "_about_lane" in grok_block
    assert "_name_lane" in grok_block


def test_name_lane_is_gated_and_defaults_off():
    """Backends without phrase/negation support must not get a broken lane."""
    src = _supplements_source()
    assert "_name_lane = None" in src
    assert "if _name_lane is not None:" in src


def test_name_lane_items_reach_the_batch():
    src = _supplements_source()
    assert "from_items + about_items + name_items" in src, (
        "name-lane results must join the batch, not be computed and dropped"
    )


def test_name_lane_excludes_the_subject_handles():
    src = _supplements_source()
    grok_block = src[src.index('if primary == "grok":'):src.index('elif primary == "bird":')]
    assert "exclude_handles=hs" in grok_block, (
        "the name lane must exclude the subject's own posts; those belong to "
        "the by-lane and would otherwise double-count"
    )


def test_name_lane_failure_does_not_abort_the_run():
    src = _supplements_source()
    block = src[src.index("if _name_lane is not None:"):]
    assert "except Exception" in block
    assert "NAME-lane" in block


def test_by_lane_does_not_and_the_topic_by_default():
    """A prior defect emptied the from-lane by ANDing the topic into it.

    The `and_topic` parameter now exists for extracted handles that need to
    demonstrate on-topic content, but the default is False (no topic AND).
    """
    from lib import grok_x
    sig = inspect.signature(grok_x.search_handles)
    assert "topic" in sig.parameters
    # and_topic parameter should default to False
    assert "and_topic" in sig.parameters
    assert sig.parameters["and_topic"].default is False
    body = inspect.getsource(grok_x.search_handles)
    assert "from:{clean}" in body
    # The default path (and_topic=False) should not include {topic} in the query
    assert 'query = f"from:{clean} since:{from_date}' in body



# --- behavioral: the source-text assertions above cannot catch a crash -------

@pytest.mark.parametrize("has_posts", [True, False])
def test_partial_coverage_does_not_raise_on_an_empty_x_source(monkeypatch, has_posts):
    """Regression: partial coverage was recorded via bundle.record_failure with
    the state string "degraded", which is not in SourceOutcome's valid_states.
    With zero Phase-1 X items record_failure passes the caller's state straight
    through, so it raised ValueError and killed the whole run -- on exactly the
    entity topics this feature targets. No source-text assertion could catch
    this; only executing the path does."""
    runtime = schema.ProviderRuntime(
        reasoning_provider="local", planner_model="", rerank_model="",
        x_search_backend="grok",
    )
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", lambda *_args: (runtime, None))
    monkeypatch.setattr(pipeline, "available_sources", lambda *_args, **_kwargs: ["x"])
    monkeypatch.setattr(pipeline.env, "x_backend_chain", lambda _config: ["grok"])
    monkeypatch.setattr(pipeline, "_retrieve_stream_impl", lambda **_kwargs: ([], {}))
    lanes = []

    def from_lane(handles, topic, from_date, to_date, **kwargs):
        lanes.append("by")
        assert handles == ["steipete"]
        assert kwargs["and_topic"] is False
        if not has_posts:
            return [], False
        return [{
            "id": "2087568620465607078",
            "url": "https://x.com/steipete/status/2087568620465607078",
            "author_handle": "steipete",
            "text": "Peter Steinberger released a new agent toolkit.",
            "date": "2026-08-12",
            "relevance": 0.9,
            "engagement": {"likes": 1462, "reposts": 48, "replies": 95},
        }], False

    def empty_lane(name):
        def search(*_args, **_kwargs):
            lanes.append(name)
            return [], False
        return search

    monkeypatch.setattr(pipeline.grok_x, "search_handles", from_lane)
    monkeypatch.setattr(pipeline.grok_x, "search_mentions", empty_lane("mention"))
    monkeypatch.setattr(pipeline.grok_x, "search_name", empty_lane("name"))
    report = pipeline.run(
        topic="Peter Steinberger", config={}, depth="default",
        requested_sources=["x"], x_handle="steipete", web_backend="none",
        as_of_date="2026-08-13",
        external_plan={
            "intent": "general", "freshness_mode": "balanced_recent",
            "cluster_mode": "story", "raw_topic": "Peter Steinberger",
            "source_weights": {"x": 1.0},
            "subqueries": [{"label": "primary", "search_query": "Peter Steinberger",
                            "ranking_query": "Peter Steinberger", "sources": ["x"]}],
        },
    )

    warning = (
        "X partial coverage: mention, name lane(s) returned nothing; "
        "the report may show only one side of this entity."
    )
    assert lanes == ["by", "mention", "name"]
    expected_urls = [
        "https://x.com/steipete/status/2087568620465607078",
    ] if has_posts else []
    assert [item.url for item in report.items_by_source["x"]] == expected_urls
    expected_warnings = [warning] if has_posts else []
    assert report.artifacts.get("x_partial_coverage", []) == expected_warnings
    assert [note for note in report.warnings if note.startswith("X partial coverage:")] == expected_warnings
    assert report.errors_by_source == {}
    assert report.source_status["x"].state == ("ok" if has_posts else "no-results")
    assert report.source_status["x"].items_returned == int(has_posts)
    assert cli._strict_exit_code(report, None, {"LAST30DAYS_STRICT_EXIT": "1"}) == 0


def test_degraded_is_not_a_valid_source_outcome_state():
    """Pins why partial coverage must not go through record_failure."""
    with pytest.raises(ValueError):
        schema.SourceOutcome(
            source="x", state="degraded", items_returned=0, attempted=True,
        )
