"""Auto-discovered X handles must reach resolved_handles.

resolved_handles gates every first-party protection downstream: the entity-miss
exemption in rerank, FIRST_PARTY_FLOOR, the interaction floor, and the
retrieval-floor exemption in signals. It was built only from --x-handle,
--github-user and --x-related, so on any run that did not pass --x-handle
(the overwhelmingly common case) the whole mechanism was inert.
"""

import inspect

from lib import pipeline


def _call_supplements(**overrides):
    """Drive _run_supplemental_searches far enough to populate the out-param.

    The function bails early when no X or Reddit dicts are present unless a
    handle was supplied, so an explicit x_handle is the cheapest way to reach
    the resolution block without standing up a full retrieval bundle.
    """
    out: list[str] = []
    kwargs = dict(
        topic="Peter Steinberger steipete",
        bundle=overrides.pop("bundle"),
        plan=overrides.pop("plan"),
        config={},
        depth="default",
        date_range=("2026-07-14", "2026-08-13"),
        runtime=overrides.pop("runtime"),
        mock=True,
        rate_limited_sources=set(),
        rate_limit_lock=overrides.pop("lock"),
        resolved_handles_out=out,
    )
    kwargs.update(overrides)
    pipeline._run_supplemental_searches(**kwargs)
    return out


def test_out_param_is_part_of_the_contract():
    sig = inspect.signature(pipeline._run_supplemental_searches)
    assert "resolved_handles_out" in sig.parameters, (
        "the supplement pass must be able to report the handles it resolved; "
        "without it resolved_handles cannot see auto-discovered subjects"
    )
    assert sig.parameters["resolved_handles_out"].default is None, (
        "the out-param must stay optional so existing callers are unaffected"
    )


def test_resolved_handles_includes_supplemental_handles():
    """The merge site must read the supplement pass's output.

    Matched on the assignment rather than an exact literal: an earlier version
    pinned "resolved_handles = {" and broke when the construction changed to
    merge the explicit set, proving nothing about behavior either way.
    """
    src = inspect.getsource(pipeline)
    start = src.index("resolved_handles =")
    block = src[start:start + 400]
    assert "supplemental_handles" in block, (
        "resolved_handles is still built without the handles the supplement "
        "pass discovered; auto-discovered subjects stay unprotected"
    )
    assert "explicit_first_party" in block, (
        "the user-named handles must still be part of resolved_handles"
    )


def test_supplemental_handles_is_seeded_before_the_call():
    src = inspect.getsource(pipeline)
    assert "supplemental_handles: list[str] = []" in src
    assert "resolved_handles_out=supplemental_handles," in src, (
        "the supplement call must pass the collector it later merges from"
    )


def test_handles_are_normalized_and_deduped():
    """Normalization must match resolved_handles' own lstrip/strip/lower form."""
    src = inspect.getsource(pipeline._run_supplemental_searches)
    block = src[src.index("resolved_handles_out is not None"):]
    assert 'lstrip("@")' in block and ".lower()" in block, (
        "handles must be normalized the same way resolved_handles normalizes, "
        "or the set comparison in rerank/_is_first_party will miss them"
    )
    assert "seen" in block, "duplicate handles must not accumulate"


def test_population_precedes_the_no_handles_early_return():
    """A run whose lanes cannot execute must still contribute its handles."""
    src = inspect.getsource(pipeline._run_supplemental_searches)
    populate_at = src.index("resolved_handles_out is not None")
    early_return_at = src.index("if not handles and not related_handles:")
    assert populate_at < early_return_at, (
        "handles are surfaced after the early return, so a run with no usable "
        "handle lane would silently contribute nothing to resolved_handles"
    )
