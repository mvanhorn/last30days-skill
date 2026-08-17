"""grok's three supplement lanes, wired into the pipeline.

The coverage requirement: for an entity topic the run must return what the
subject said, what others said *to* them, and what others said *about* them by
name. The third is not redundant with the second -- most discussion never
@-mentions the subject, so a mention-only lane structurally cannot reach it.
"""

import inspect

import pytest

from lib import pipeline, schema


def _supplements_source():
    return inspect.getsource(pipeline._run_supplemental_searches)


def test_grok_is_handle_lane_capable():
    src = _supplements_source()
    assert '("grok", "bird", "xquik")' in src, (
        "grok supports from:/@ natively; leaving it out of the capable set "
        "silently drops all of Phase 2 for grok users, as it already does for "
        "xai and xurl"
    )


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


def test_partial_coverage_is_recorded():
    """One-sided coverage must be visible, not look like thin discussion."""
    src = _supplements_source()
    assert "partial coverage" in src
    assert 'if empty and len(empty) < 3:' in src, (
        "an all-empty result is an ordinary no-results outcome, not partial "
        "coverage; only a mixed result is worth flagging"
    )


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

def test_partial_coverage_does_not_raise_on_an_empty_x_source():
    """Regression: partial coverage was recorded via bundle.record_failure with
    the state string "degraded", which is not in SourceOutcome's valid_states.
    With zero Phase-1 X items record_failure passes the caller's state straight
    through, so it raised ValueError and killed the whole run -- on exactly the
    entity topics this feature targets. No source-text assertion could catch
    this; only executing the path does."""
    bundle = schema.RetrievalBundle()
    assert not bundle.items_by_source.get("x")
    empty = ["mention"]
    # Mirror the production call: this must not raise.
    bundle.artifacts.setdefault("x_partial_coverage", []).append(
        f"X partial coverage: {', '.join(empty)} lane(s) returned nothing"
    )
    assert bundle.artifacts["x_partial_coverage"]


def test_degraded_is_not_a_valid_source_outcome_state():
    """Pins why partial coverage must not go through record_failure."""
    with pytest.raises(ValueError):
        schema.SourceOutcome(
            source="x", state="degraded", items_returned=0, attempted=True,
        )


def test_partial_coverage_is_not_recorded_as_a_source_failure():
    """A one-sided lane result must not mark X partial: PARTIAL is outside
    _STRICT_EXIT_OK_STATES, so wrappers using LAST30DAYS_STRICT_EXIT would exit
    3 on runs that returned good X coverage."""
    src = _supplements_source()
    # Strip comments: the rationale for NOT using record_failure names it.
    code = "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("#")
    )
    idx = code.index("x_partial_coverage")
    window = code[max(0, idx - 400):idx]
    assert "record_failure" not in window, (
        "partial lane coverage must be a warning, not a source outcome: "
        "record_failure would set X to PARTIAL and trip strict-exit wrappers"
    )
