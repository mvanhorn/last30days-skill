"""Fallback (no reasoning provider) ranking behavior.

With no LLM reranker every candidate is scored by rerank._fallback_tuple +
rerank._final_score. These tests pin the five fixes from the 2026-10-11 live
traces (Ghostty, SQLite): engagement on an absolute per-source log scale with
real weight, freshness counted once, the low-rerank cliff limited to LLM
scores, the All Items dump printing a real score, and first-party floors
limited to on-topic posts or the topic's own handle.
"""

from lib import fusion, planner, render, rerank, schema, signals

TO = "2026-10-11"
FROM = "2026-09-11"
SOURCES = ["reddit", "x", "hackernews"]


def _plan(topic: str) -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="product",
        freshness_mode="balanced_recent",
        cluster_mode="none",
        raw_topic=topic,
        subqueries=[
            schema.SubQuery(
                label="primary", search_query=topic, ranking_query=topic,
                sources=SOURCES, weight=1.0,
            )
        ],
        source_weights=planner._normalize_weights({s: 1.0 for s in SOURCES}),
    )


def _item(item_id, source, title, date, engagement, author=None, mentions=()):
    return schema.SourceItem(
        item_id=item_id, source=source, title=title, body=title,
        url=f"https://example.test/{source}/{item_id}", author=author,
        published_at=date, date_confidence="high", engagement=engagement,
        metadata={"mentioned_handles": list(mentions)} if mentions else {},
    )


def _rank(topic, streams, handles=frozenset()):
    """Run the real annotate -> fuse -> fallback-rerank path; return ids in order."""
    annotated = {
        ("primary", source): signals.annotate_stream(
            items, topic, "balanced_recent", reference_date=TO, max_days=30
        )
        for source, items in streams.items()
    }
    plan = _plan(topic)
    candidates = fusion.weighted_rrf(
        annotated, plan, pool_limit=40, range_from=FROM, range_to=TO,
        first_party_handles=set(handles),
    )
    ranked = rerank.rerank_candidates(
        topic=topic, plan=plan, candidates=candidates, provider=None, model=None,
        shortlist_size=40, resolved_handles=set(handles),
    )
    return ranked


# --- Fix 1: absolute per-source engagement scale with real weight -----------

def test_engagement_score_is_absolute_not_stream_relative():
    """A thread's engagement score must not depend on what else is in its stream."""
    big = _item("big", "reddit", "Ghostty smooth cursor animation shader", "2026-09-14",
                {"score": 254, "num_comments": 24})
    alone = signals.annotate_stream([big], "ghostty", "balanced_recent", reference_date=TO)[0]
    alone_score = alone.engagement_score
    big2 = _item("big", "reddit", "Ghostty smooth cursor animation shader", "2026-09-14",
                 {"score": 254, "num_comments": 24})
    huge = _item("huge", "reddit", "Ghostty 2.0 released", "2026-09-14",
                 {"score": 9000, "num_comments": 900})
    mixed = signals.annotate_stream([big2, huge], "ghostty", "balanced_recent", reference_date=TO)
    mixed_score = next(i for i in mixed if i.item_id == "big").engagement_score
    assert alone_score == mixed_score
    # A 3-like post must not be lifted to the stream midpoint just because it is alone.
    tiny = _item("tiny", "x", "ghostty", TO, {"likes": 3})
    tiny = signals.annotate_stream([tiny], "ghostty", "balanced_recent", reference_date=TO)[0]
    assert tiny.engagement_score < 15


def test_250_upvote_reddit_thread_outranks_3_like_x_post_at_equal_relevance():
    title = "Ghostty smooth cursor animation shader"
    ranked = _rank("Ghostty", {
        "reddit": [_item("r", "reddit", title, "2026-10-09", {"score": 250, "num_comments": 24})],
        "x": [_item("x", "x", title, "2026-10-09", {"likes": 3}, author="someone")],
    })
    assert [c.item_id for c in ranked] == ["r", "x"]


def test_380_point_hn_story_from_26_days_ago_outranks_3_point_story_from_today():
    title = "Show HN: Capsule - single-file web apps that save their data into SQLite"
    ranked = _rank("SQLite", {
        "hackernews": [
            _item("old", "hackernews", title, "2026-09-15", {"points": 380, "comments": 166}),
            _item("new", "hackernews", title, TO, {"points": 3}),
        ],
    })
    assert [c.item_id for c in ranked] == ["old", "new"]


# --- Fix 2: freshness enters the fallback score once -------------------------

def _candidate(*, freshness=50, rel=0.5, sq=0.6, engagement=None, explanation=None,
               author=None, text="SQLite note", mentions=(), source="x"):
    item = schema.SourceItem(
        item_id="i", source=source, title=text, body=text,
        url="https://example.test/i", author=author, snippet=text,
        metadata={"mentioned_handles": list(mentions)} if mentions else {},
    )
    c = schema.Candidate(
        candidate_id=f"c-{author}-{freshness}-{text}", item_id="i", source=source,
        title=text, url=item.url, snippet=text, subquery_labels=["primary"],
        native_ranks={"primary:x": 1}, local_relevance=rel, freshness=freshness,
        engagement=engagement, source_quality=sq, rrf_score=0.0, source_items=[item],
    )
    c.explanation = explanation
    return c


def test_fallback_rerank_score_does_not_include_freshness():
    old = _candidate(freshness=10)
    new = _candidate(freshness=90)
    assert rerank._fallback_tuple(old)[0] == rerank._fallback_tuple(new)[0]


def test_fallback_final_score_counts_freshness_once():
    old = _candidate(freshness=10)
    new = _candidate(freshness=90)
    rerank._apply_fallback_scores([old, new])
    # _final_score weights freshness at 0.10; 80 points of freshness is worth 8.
    assert abs((new.final_score - old.final_score) - 8.0) < 1e-6


# --- Fix 3: the <20 rerank cliff applies to LLM scores only -------------------

def test_fallback_scored_candidate_below_20_is_not_cliffed():
    fallback = _candidate(explanation="fallback-local-score")
    fallback.rerank_score = 18.0
    llm = _candidate(explanation="weak match")
    llm.rerank_score = 18.0
    assert abs(rerank._final_score(llm) - 0.3 * rerank._final_score(fallback)) < 1e-6


def test_llm_scored_candidate_below_20_is_still_cliffed():
    above = _candidate(explanation="ok")
    above.rerank_score = 20.0
    below = _candidate(explanation="weak")
    below.rerank_score = 19.0
    assert rerank._final_score(below) < 0.35 * rerank._final_score(above)


# --- Fix 4: All Items by Source prints a real score --------------------------

def test_all_items_by_source_prints_local_rank_score_on_a_100_scale():
    item = _item("i1", "reddit", "SQLite 3.54.0 released", "2026-10-10",
                 {"score": 47, "num_comments": 6})
    item.local_rank_score = 0.73
    report = schema.Report(
        topic="SQLite", range_from=FROM, range_to=TO,
        generated_at="2026-10-11T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
        ),
        query_plan=_plan("SQLite"),
        clusters=[], ranked_candidates=[],
        items_by_source={"reddit": [item]}, errors_by_source={},
    )
    dump = render.render_full(report).split("## All Items by Source", 1)[1]
    assert "**i1** (score:73)" in dump


# --- Fix 5: first-party floors only for on-topic posts or the topic's handle --

def _rerank_one_with_peers(topic, target, handles):
    peers = [
        _candidate(author=f"peer{i}", text=f"{topic} thoughts {i}", engagement=e, freshness=80)
        for i, e in enumerate((1, 2))
    ]
    pool = [target, *peers]
    for c in pool:
        c.candidate_id = f"{c.candidate_id}-{id(c)}"
    rerank.rerank_candidates(
        topic=topic, plan=_plan(topic), candidates=pool, provider=None, model=None,
        shortlist_size=10, resolved_handles=set(handles),
    )
    return target


def test_off_topic_first_party_reply_is_not_floored():
    nanit = _candidate(author="mitchellh", text="@johnsoncory_ Nanit", rel=0.0,
                       mentions=["johnsoncory_"], engagement=20, freshness=82)
    _rerank_one_with_peers("Ghostty terminal", nanit, {"mitchellh"})
    # Its own score stands: no interaction / first-party / rescue floor lifted it.
    assert nanit.final_score == rerank._final_score(nanit)
    assert nanit.final_score < rerank.FIRST_PARTY_FLOOR
    # Still protected from the entity-miss marker / visibility prune.
    assert "entity-miss" not in (nanit.explanation or "")
    assert rerank.prune_fallback_entity_misses([nanit], topic="Ghostty terminal") == [nanit]


def test_off_topic_related_handle_post_is_not_rescued_by_engagement():
    """Top of the X pool by engagement, but off-topic: the rescue must not lift it."""
    shower = _candidate(author="simonw", text="Current iPhones are waterproof enough for the shower",
                        rel=0.0, engagement=24, freshness=87, mentions=["romanhotsiy"])
    _rerank_one_with_peers("SQLite", shower, {"simonw"})
    assert shower.final_score == rerank._final_score(shower)
    assert shower.final_score < rerank.FIRST_PARTY_FLOOR


def test_on_topic_first_party_reply_is_still_floored():
    c = _candidate(author="mitchellh", text="@someone Ghostty 1.3 fixes that", rel=0.5,
                   mentions=["someone"])
    c.final_score = 5.0
    rerank._apply_interaction_signal(
        [c], resolved_handles={"mitchellh"}, primary_entity="Ghostty terminal",
        topic="Ghostty terminal",
    )
    assert c.final_score >= rerank.INTERACTION_FLOOR


def test_person_topic_subject_reply_is_still_floored():
    """214a8fa0's case: the subject's own reply never names them."""
    c = _candidate(author="steipete", text="@someone thanks, shipping tonight", rel=0.0,
                   mentions=["someone"])
    c.final_score = 5.0
    rerank._apply_interaction_signal(
        [c], resolved_handles={"steipete"}, primary_entity="Peter Steinberger",
        topic="Peter Steinberger",
    )
    assert c.final_score >= rerank.INTERACTION_FLOOR
    d = _candidate(author="steipete", text="shipping tonight", rel=0.0)
    d.final_score = 2.0
    rerank._apply_first_party_floor(
        [d], resolved_handles={"steipete"}, primary_entity="Peter Steinberger",
        topic="Peter Steinberger",
    )
    assert d.final_score >= rerank.FIRST_PARTY_FLOOR


def test_person_topic_end_to_end_keeps_subject_reply_floored():
    reply = _candidate(author="steipete", text="@someone thanks, shipping tonight", rel=0.0,
                       mentions=["someone"], engagement=1, freshness=80)
    _rerank_one_with_peers("Peter Steinberger", reply, {"steipete"})
    assert reply.final_score >= rerank.INTERACTION_FLOOR


def test_handle_is_topic_matching():
    assert rerank._handle_is_topic("steipete", "Peter Steinberger")
    assert rerank._handle_is_topic("sama", "Sam Altman")
    assert rerank._handle_is_topic("mitchellh", "Mitchell Hashimoto")
    assert rerank._handle_is_topic("karpathy", "Andrej Karpathy")
    assert rerank._handle_is_topic("ghostty", "Ghostty terminal")
    assert rerank._handle_is_topic("steipete", "Peter Steinberger steipete")
    assert not rerank._handle_is_topic("mitchellh", "Ghostty terminal")
    assert not rerank._handle_is_topic("simonw", "SQLite")
    assert not rerank._handle_is_topic("", "SQLite")


def test_off_topic_first_party_gets_no_authorship_credit():
    off = _candidate(author="mitchellh", text="@johnsoncory_ Nanit", rel=0.0)
    stranger = _candidate(author="stranger", text="@johnsoncory_ Nanit", rel=0.0)
    off_score, off_reason = rerank._fallback_tuple(
        off, primary_entity="Ghostty terminal", resolved_handles={"mitchellh"},
        subject_handles=set(),
    )
    base, _ = rerank._fallback_tuple(stranger)
    assert off_score == base
    assert "entity-miss" not in off_reason
