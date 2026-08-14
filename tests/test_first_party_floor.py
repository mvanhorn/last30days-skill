"""Retrieval-floor exemption for posts authored by a handle the run is searching.

The production failure this pins: a mixed batch where the mention lane clears
the relevance floor and the from lane does not. Because
`prune_low_relevance` ends with `return filtered or items`, the all-fail rescue
only fires when *everything* fails. A mixed batch is therefore the exact shape
that silently loses the subject's own posts, and no prior test exercised it --
existing supplement-lane tests use single-item batches that trip the rescue.
"""

from lib import schema, signals


def _x_item(item_id: str, author: str, relevance: float, engagement: dict | None = None):
    item = schema.SourceItem(
        item_id=item_id,
        source="x",
        title="",
        body="post body",
        url=f"https://x.com/{author}/status/{item_id}",
        author=author,
        engagement=engagement or {},
    )
    item.local_relevance = relevance
    return item


def _mixed_batch():
    """From-lane items score 0.0 (a post rarely names its own author);
    mention-lane items clear the floor because they contain the handle."""
    return [
        _x_item("1", "steipete", 0.0, {"likes": 7773, "reposts": 391}),
        _x_item("2", "steipete", 0.0, {"likes": 3466, "reposts": 128}),
        _x_item("3", "someone_else", 0.32, {"likes": 78, "reposts": 8}),
        _x_item("4", "another_acct", 0.23, {"likes": 8, "reposts": 1}),
    ]


def _annotate(items):
    """Populate engagement_score the way the real pipeline does."""
    scores = signals.normalize([signals.engagement_raw(i) for i in items])
    for item, score in zip(items, scores, strict=True):
        item.engagement_score = score
    return items


def test_mixed_batch_keeps_first_party_and_drops_off_topic():
    items = _annotate(_mixed_batch())
    kept = signals.prune_low_relevance(items, first_party_handles={"steipete"})
    authors = sorted(i.author for i in kept)
    assert "steipete" in authors, (
        "first-party posts were pruned from a mixed batch; this is the measured "
        "defect where 8 subject-authored posts never reached the report"
    )
    assert len([a for a in authors if a == "steipete"]) == 2


def test_mixed_batch_without_exemption_still_loses_first_party():
    """Characterizes the defect: without the exemption the batch drops them."""
    items = _annotate(_mixed_batch())
    kept = signals.prune_low_relevance(items)
    assert all(i.author != "steipete" for i in kept), (
        "expected the unexempted path to still drop zero-relevance first-party "
        "posts; if this now passes, the floor changed and the exemption's "
        "justification needs rechecking"
    )


def test_non_first_party_below_floor_is_still_pruned():
    """The exemption must be scoped, not a blanket floor removal."""
    items = _annotate(_mixed_batch() + [_x_item("5", "spam_acct", 0.02, {"likes": 0})])
    kept = signals.prune_low_relevance(items, first_party_handles={"steipete"})
    assert all(i.author != "spam_acct" for i in kept)


def test_batch_minimum_is_not_treated_as_zero_engagement():
    """`normalize` maps the batch minimum to exactly 0, so an item with real
    engagement was being given the stricter 1.5x social threshold purely for
    being the least-engaged item present."""
    items = _annotate([
        _x_item("1", "acct_a", 0.20, {"likes": 500, "reposts": 40}),
        _x_item("2", "acct_b", 0.20, {"likes": 5000, "reposts": 400}),
    ])
    least = next(i for i in items if i.author == "acct_a")
    assert least.engagement_score == 0, "precondition: min-max maps batch min to 0"
    kept = signals.prune_low_relevance(items)
    assert any(i.author == "acct_a" for i in kept), (
        "an item with 500 likes was pruned by the zero-engagement gate purely "
        "because it was the batch minimum"
    )


def test_genuinely_zero_engagement_still_gets_stricter_threshold():
    """The stricter gate must survive for real zero-engagement social noise."""
    items = _annotate([
        _x_item("1", "acct_a", 0.20, {"likes": 0, "reposts": 0}),
        _x_item("2", "acct_b", 0.90, {"likes": 5000, "reposts": 400}),
    ])
    kept = signals.prune_low_relevance(items)
    assert all(i.author != "acct_a" for i in kept), (
        "a genuinely zero-engagement item at 0.20 should fail the 0.225 gate"
    )


def test_all_fail_rescue_is_unchanged():
    items = _annotate([_x_item("1", "acct_a", 0.01), _x_item("2", "acct_b", 0.02)])
    kept = signals.prune_low_relevance(items)
    assert len(kept) == 2, "the all-fail rescue must still return the batch"


def test_batch_with_no_first_party_behaves_as_before():
    items = _annotate(_mixed_batch())
    assert signals.prune_low_relevance(items, first_party_handles=frozenset()) == \
        signals.prune_low_relevance(items)


def test_handles_are_matched_case_insensitively():
    items = _annotate(_mixed_batch())
    kept = signals.prune_low_relevance(items, first_party_handles={"SteiPete"})
    assert any(i.author == "steipete" for i in kept)


# --- KTD8: one owner for the entity-miss predicate -------------------------

def _candidate(explanation: str, final_score: float, author: str = "someone"):
    url = f"https://x.com/{author}/status/1"
    cand = schema.Candidate(
        candidate_id="c1",
        item_id="i1",
        source="x",
        title="t",
        url=url,
        snippet="s",
        subquery_labels=["primary"],
        native_ranks={"primary:x": 1},
        local_relevance=0.0,
        freshness=80,
        engagement=50,
        source_quality=0.68,
        rrf_score=0.02,
    )
    cand.source_items = [
        schema.SourceItem(
            item_id="i1", source="x", title="t", body="b", url=url, author=author,
        )
    ]
    cand.explanation = explanation
    cand.final_score = final_score
    return cand


def test_render_delegates_to_shared_predicate():
    """render must not carry its own copy of the entity-miss test."""
    from lib import render, rerank
    cand = _candidate("fallback-local-score (entity-miss demotion)", 40.0)
    assert render._best_take_relevance_ok(cand) is rerank.candidate_relevance_ok(cand)
    ok = _candidate("llm-scored", 40.0)
    assert render._best_take_relevance_ok(ok) is rerank.candidate_relevance_ok(ok)


def test_shared_predicate_rejects_entity_miss_and_zero_score():
    from lib import rerank
    assert not rerank.candidate_relevance_ok(
        _candidate("fallback-local-score (entity-miss demotion)", 40.0)
    )
    assert not rerank.candidate_relevance_ok(_candidate("llm-scored", 0.0))
    assert rerank.candidate_relevance_ok(_candidate("llm-scored", 40.0))


def test_first_party_carveout_reaches_render_side_gate():
    """The measured KTD8 failure: a first-party post demoted on the LLM path
    was floored by rerank but still discarded at render because the render-side
    copy re-tested the explanation string."""
    from lib import render, rerank
    cand = _candidate("fallback-local-score (entity-miss demotion)", 0.0, author="steipete")
    assert not render._best_take_relevance_ok(cand), "precondition: demoted before the floor runs"
    rerank._apply_first_party_floor([cand], resolved_handles={"steipete"})
    assert cand.final_score >= rerank.FIRST_PARTY_FLOOR
    assert render._best_take_relevance_ok(cand), (
        "first-party carve-out applied in rerank did not propagate to the "
        "render-side relevance gate"
    )


def test_non_first_party_demotion_survives_the_floor_pass():
    from lib import render, rerank
    cand = _candidate("fallback-local-score (entity-miss demotion)", 0.0, author="rando")
    rerank._apply_first_party_floor([cand], resolved_handles={"steipete"})
    assert not render._best_take_relevance_ok(cand), (
        "an off-topic collision post must stay buried"
    )


# --- Phase 1 / quick-depth wiring (Greptile) --------------------------------

def test_phase_one_normalize_receives_the_explicit_handles():
    """Quick runs skip Phase 2 entirely, so an exemption reaching only the
    supplement path leaves quick-depth reports discarding the subject's posts."""
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline.run)
    assert "explicit_first_party = {" in src, (
        "the user-named handles must be resolved before retrieval, not after"
    )
    assert "first_party_handles=explicit_first_party," in src, (
        "the Phase 1 per-source normalize must receive the exemption"
    )


def test_explicit_handles_are_available_before_any_retrieval():
    """The entity-extracted set does not exist until Phase 2; the explicit one
    must be built from run()'s own arguments so Phase 1 can use it."""
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline.run)
    build_at = src.index("explicit_first_party = {")
    first_use = src.index("first_party_handles=explicit_first_party,")
    assert build_at < first_use


def test_related_handles_lane_gets_the_exemption():
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline._run_supplemental_searches)
    assert "first_party_handles=related_handles," in src
