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


def test_quick_run_without_an_explicit_handle_still_protects_the_subject():
    """The gap in the first fix: covering only user-typed handles does nothing
    for a quick run, where nobody typed one and Phase 2's automatic resolution
    never executes."""
    from lib import pipeline
    candidates = pipeline._topic_first_party_candidates("Peter Steinberger steipete")
    assert "steipete" in candidates

    items = _annotate(_mixed_batch())
    kept = signals.prune_low_relevance(items, first_party_handles=candidates)
    assert any(i.author == "steipete" for i in kept), (
        "a quick search naming the subject must not discard what they wrote"
    )


def test_topic_candidates_include_explicit_mentions():
    from lib import pipeline
    assert "getenergy_" in pipeline._topic_first_party_candidates("@GetEnergy_ launch")


def test_topic_candidates_exclude_stopwords():
    from lib import pipeline
    got = pipeline._topic_first_party_candidates("the best of the year")
    assert "the" not in got and "of" not in got


def test_topic_candidates_do_not_exempt_unrelated_authors():
    """Candidates only matter when a post's author matches one, so ordinary
    words cost nothing -- no account is named 'lunch'."""
    from lib import pipeline
    candidates = pipeline._topic_first_party_candidates("bentgo lunch boxes")
    items = _annotate(_mixed_batch() + [_x_item("9", "spam_acct", 0.01, {"likes": 0})])
    kept = signals.prune_low_relevance(items, first_party_handles=candidates)
    assert all(i.author != "spam_acct" for i in kept)


def test_topic_candidates_are_unioned_into_the_explicit_set():
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline.run)
    assert "_topic_first_party_candidates(topic)" in src
    build = src.index("explicit_first_party")
    use = src.index("first_party_handles=explicit_first_party,")
    assert build < use


def test_name_only_topic_resolves_the_subject_from_mentions():
    """The hard case: search "Peter Steinberger" with no handle anywhere. His
    handle is @steipete, which matches no topic token, and Phase 2's resolution
    has not run. Posts *about* him mention him, which is the signal the engine
    already uses -- just later than the prune."""
    from lib import pipeline
    raw = [
        {"text": "Great thread from @steipete on agent loops"},
        {"text": "@steipete nailed this one"},
        {"text": "watching @steipete build in public is wild"},
        {"text": "unrelated chatter with no mention"},
    ]
    assert "steipete" in pipeline._batch_subject_handles(raw)


def test_batch_subject_keys_on_mentions_not_authors():
    """A prolific commentator inflates author counts; being mentioned by other
    accounts is what identifies the subject."""
    from lib import pipeline
    raw = [
        {"author_handle": "spam_acct", "text": "buy now"},
        {"author_handle": "spam_acct", "text": "buy now again"},
        {"author_handle": "spam_acct", "text": "and again"},
        {"author_handle": "someone", "text": "actually useful thread by @realsubject"},
    ]
    got = pipeline._batch_subject_handles(raw)
    assert "spam_acct" not in got
    assert "realsubject" in got


def test_batch_subject_is_capped():
    from lib import pipeline
    raw = [{"text": f"@acct{i} said something"} for i in range(10)]
    assert len(pipeline._batch_subject_handles(raw)) <= 2


def test_batch_subject_is_empty_without_mentions():
    from lib import pipeline
    assert pipeline._batch_subject_handles([{"text": "no mentions here"}]) == set()
    assert pipeline._batch_subject_handles([]) == set()


def test_batch_inference_is_unioned_not_gated():
    """Regression: gating this on "no handles supplied" made it dead code.

    The caller's set is derived partly from topic tokens, so it is non-empty
    for essentially every real topic -- a fallback would never fire and the
    name-only case would stay broken while looking fixed.
    """
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline._normalize_score_dedupe)
    assert "floor_handles |= _batch_subject_handles(raw_items)" in src
    assert "not floor_handles" not in src, (
        "batch inference must union, never gate on an empty supplied set"
    )


def test_name_only_topic_keeps_subject_posts_end_to_end():
    """The full path: topic names a person, handle appears nowhere in it, and
    the subject's own zero-relevance posts still survive the floor."""
    from lib import pipeline
    supplied = pipeline._topic_first_party_candidates("Peter Steinberger")
    assert "steipete" not in supplied, "precondition: the handle is not in the topic"
    raw = [
        {"text": "Great thread from @steipete on agent loops"},
        {"text": "@steipete nailed this"},
        {"text": "more praise for @steipete"},
    ]
    inferred = pipeline._batch_subject_handles(raw)
    items = _annotate(_mixed_batch())
    kept = signals.prune_low_relevance(
        items, first_party_handles=supplied | inferred
    )
    assert any(i.author == "steipete" for i in kept)


# --- ordering fix: prune X after resolution, not before ---------------------

def test_x_defers_its_relevance_floor_past_resolution():
    """The root ordering bug: X was pruned before the run knew who the subject
    was, so the floor could not exempt an author nobody had identified yet. No
    amount of guessing at prune time substitutes for knowing."""
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline.run)
    assert 'defer_relevance_prune=(source == "x")' in src


def test_deferred_prune_runs_after_resolution_and_before_fusion():
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline.run)
    resolve_at = src.index("resolved_handles = explicit_first_party")
    prune_at = src.index("Deferred X relevance floor")
    fuse_at = src.index("candidates = weighted_rrf(")
    assert resolve_at < prune_at < fuse_at, (
        "the deferred floor must see resolved handles and still run before fusion"
    )


def test_non_x_sources_still_prune_in_place():
    """Only X defers; everything else keeps its existing behavior."""
    import inspect
    from lib import pipeline
    src = inspect.getsource(pipeline._normalize_score_dedupe)
    assert 'if source != "jobs" and not defer_relevance_prune:' in src


def test_deferred_prune_still_drops_off_topic_posts():
    """Deferring must not mean skipping."""
    items = _annotate(_mixed_batch() + [_x_item("9", "spam_acct", 0.01, {"likes": 0})])
    kept = signals.prune_low_relevance(items, first_party_handles={"steipete"})
    assert any(i.author == "steipete" for i in kept)
    assert all(i.author != "spam_acct" for i in kept)


# --- unresolved subject policy: skip X floor when no real handle identified ---

def test_topic_handle_mentions_extracts_only_at_mentions():
    """@mentions in the topic are real handles; regular words are not."""
    from lib import pipeline
    assert pipeline._topic_handle_mentions("Peter Steinberger @steipete") == {"steipete"}
    assert pipeline._topic_handle_mentions("Peter Steinberger") == set()
    assert pipeline._topic_handle_mentions("@GetEnergy_ launch") == {"getenergy_"}


def test_topic_handle_mentions_is_case_insensitive():
    from lib import pipeline
    assert pipeline._topic_handle_mentions("@SteiPete") == {"steipete"}


def test_entity_topic_no_handle_no_discovery_skips_x_floor():
    """Policy: when the subject cannot be identified, skip the X floor entirely.

    Entity-shaped topic, no --x-handle, auto-resolve/Phase 2 return nothing.
    Retrieved X items include a zero-relevance post whose author is not a
    topic token. That post survives prune because the floor is not applied.
    A companion on-topic post is in the batch so the all-fail rescue cannot
    hide an incorrectly applied floor.
    """
    from unittest.mock import patch
    from lib import pipeline

    topic = "Peter Steinberger"
    assert pipeline._topic_handle_mentions(topic) == set()
    topic_tokens = pipeline._topic_first_party_candidates(topic)
    assert "peter" in topic_tokens and "steinberger" in topic_tokens
    assert "rando_acct" not in topic_tokens

    raw_items = [
        {
            "id": "1",
            "text": "shipping a new agent loop tonight",
            "url": "https://x.com/rando_acct/status/1",
            "author_handle": "rando_acct",
            "date": "2026-08-01",
            "engagement": {"likes": 0, "reposts": 0, "replies": 0},
        },
        {
            "id": "2",
            "text": "Peter Steinberger just shipped another agent demo",
            "url": "https://x.com/third_acct/status/2",
            "author_handle": "third_acct",
            "date": "2026-08-01",
            "engagement": {"likes": 50, "reposts": 5, "replies": 2},
        },
    ]
    plan = {
        "intent": "person",
        "freshness_mode": "balanced_recent",
        "cluster_mode": "topic",
        "subqueries": [{
            "label": "primary",
            "search_query": topic,
            "ranking_query": topic,
            "sources": ["x"],
        }],
        "source_weights": {"x": 1.0},
    }

    def fake_retrieve(**kwargs):
        if kwargs.get("source") == "x":
            return raw_items, {}
        return [], {}

    with patch("lib.pipeline._retrieve_stream", side_effect=lambda **kw: fake_retrieve(**kw)):
        report = pipeline.run(
            topic=topic,
            config={"LAST30DAYS_REASONING_PROVIDER": "gemini"},
            depth="quick",
            requested_sources=["x"],
            mock=True,
            external_plan=plan,
            as_of_date="2026-08-14",
        )

    x_items = report.items_by_source.get("x") or []
    authors = {item.author for item in x_items}
    assert "rando_acct" in authors, (
        "unresolved subject policy: skip X floor when no real handle identified; "
        "the zero-relevance post whose author is not a topic token must survive"
    )


def test_entity_topic_with_at_mention_applies_x_floor():
    """When the topic DOES include @mentions, the X floor applies normally."""
    from lib import pipeline

    topic = "Peter Steinberger @steipete"
    explicit_x_handles = pipeline._topic_handle_mentions(topic)
    supplemental_handles = []

    assert "steipete" in explicit_x_handles, (
        "precondition: @mention is a real handle"
    )

    real_x_handles = explicit_x_handles | {
        h.lstrip("@").strip().lower() for h in supplemental_handles if h and h.strip()
    }
    assert real_x_handles, "real handles were resolved"

    topic_candidates = pipeline._topic_first_party_candidates(topic)
    items = _annotate([
        _x_item("1", "steipete", 0.0, {"likes": 100, "reposts": 10}),
        _x_item("2", "rando_acct", 0.0, {"likes": 0, "reposts": 0}),
        _x_item("3", "third_acct", 0.18, {"likes": 20, "reposts": 2}),
    ])

    kept = signals.prune_low_relevance(items, first_party_handles=topic_candidates | real_x_handles)

    assert "steipete" in {i.author for i in kept}, (
        "first-party posts survive the floor"
    )
    assert "rando_acct" not in {i.author for i in kept}, (
        "when real handles exist, off-topic zero-relevance posts are pruned"
    )


def test_explicit_x_handle_applies_x_floor():
    """--x-handle triggers the floor even with entity-only topic."""
    from lib import pipeline

    topic = "Peter Steinberger"
    x_handle = "steipete"
    explicit_x_handles = {x_handle.lstrip("@").strip().lower()}

    assert pipeline._topic_handle_mentions(topic) == set(), (
        "precondition: topic has no @mentions"
    )
    assert explicit_x_handles == {"steipete"}, (
        "but we have an explicit --x-handle"
    )

    topic_candidates = pipeline._topic_first_party_candidates(topic)
    resolved_handles = topic_candidates | explicit_x_handles

    items = _annotate([
        _x_item("1", "steipete", 0.0, {"likes": 100, "reposts": 10}),
        _x_item("2", "rando_acct", 0.0, {"likes": 0, "reposts": 0}),
    ])

    kept = signals.prune_low_relevance(items, first_party_handles=resolved_handles)

    assert "steipete" in {i.author for i in kept}
    assert "rando_acct" not in {i.author for i in kept}, (
        "--x-handle triggers the floor, pruning off-topic posts"
    )


# --- thin-source retry must not prune X before handle resolution ------------

def test_thin_retry_keeps_zero_relevance_subject_authored_x_post():
    """Phase 1 defers the X floor; the simplified-query retry must too.

    A default/deep run with fewer than three X items retries with a simpler
    query. If that retry returns a subject-authored post that does not repeat
    the subject's name, applying the relevance floor here (with no resolved
    handles) discards it before it enters the bundle. The later
    resolved-handle floor cannot recover a post that never arrived.
    """
    import threading
    from unittest.mock import patch
    from lib import pipeline

    topic = "Peter Steinberger"
    raw_items = [
        {
            "id": "1",
            "text": "shipping a new agent loop tonight",
            "url": "https://x.com/steipete/status/1",
            "author_handle": "steipete",
            "date": "2026-08-01",
            "engagement": {"likes": 3466, "reposts": 128, "replies": 40},
        },
        {
            "id": "2",
            "text": "Peter Steinberger just shipped another agent demo",
            "url": "https://x.com/third_acct/status/2",
            "author_handle": "third_acct",
            "date": "2026-08-01",
            "engagement": {"likes": 50, "reposts": 5, "replies": 2},
        },
    ]
    plan = schema.QueryPlan(
        intent="person",
        freshness_mode="balanced_recent",
        cluster_mode="topic",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary",
                search_query=topic,
                ranking_query=topic,
                sources=["x"],
            )
        ],
        source_weights={"x": 1.0},
    )
    bundle = schema.RetrievalBundle()

    with patch("lib.pipeline._retrieve_stream", return_value=(raw_items, {})):
        pipeline._retry_thin_sources(
            topic=topic,
            bundle=bundle,
            plan=plan,
            config={},
            depth="default",
            date_range=("2026-07-15", "2026-08-14"),
            runtime=schema.ProviderRuntime(
                reasoning_provider="mock",
                planner_model="mock",
                rerank_model="mock",
            ),
            mock=False,
            rate_limited_sources=set(),
            rate_limit_lock=threading.Lock(),
            settings=pipeline.DEPTH_SETTINGS["default"],
        )

    x_items = bundle.items_by_source.get("x") or []
    authors = {item.author for item in x_items}
    assert "steipete" in authors, (
        "thin-retry X path must defer the relevance floor the way Phase 1 does; "
        "a zero-relevance subject-authored post must survive into the bundle"
    )
    assert "third_acct" in authors, (
        "precondition: the companion on-topic post cleared the floor, so the "
        "all-fail rescue cannot hide an incorrectly applied prune"
    )
