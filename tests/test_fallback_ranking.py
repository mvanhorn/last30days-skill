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
    fallback = _candidate(text="SQLite fallback")
    rerank._apply_fallback_scores([fallback])
    fallback.rerank_score = 18.0
    llm = _candidate(text="SQLite judged")
    rerank._apply_llm_scores(
        [llm], {"scores": [{"candidate_id": llm.candidate_id, "relevance": 18, "reason": "weak match"}]}
    )
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

def _rerank_one_with_peers(topic, target, handles, primary_handle=None):
    peers = [
        _candidate(author=f"peer{i}", text=f"{topic} thoughts {i}", engagement=e, freshness=80)
        for i, e in enumerate((1, 2))
    ]
    pool = [target, *peers]
    for c in pool:
        c.candidate_id = f"{c.candidate_id}-{id(c)}"
    rerank.rerank_candidates(
        topic=topic, plan=_plan(topic), candidates=pool, provider=None, model=None,
        shortlist_size=10, resolved_handles=set(handles), primary_handle=primary_handle,
    )
    return target


def test_off_topic_first_party_reply_is_not_floored():
    nanit = _candidate(author="mitchellh", text="@johnsoncory_ Nanit", rel=0.0,
                       mentions=["johnsoncory_"], engagement=20, freshness=82)
    # --x-handle mitchellh: the primary handle, but not the topic's own account.
    _rerank_one_with_peers("Ghostty terminal", nanit, {"mitchellh"}, primary_handle="mitchellh")
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
    """214a8fa0's case: the subject's own reply never names them (--x-handle steipete)."""
    c = _candidate(author="steipete", text="@someone thanks, shipping tonight", rel=0.0,
                   mentions=["someone"])
    c.final_score = 5.0
    rerank._apply_interaction_signal(
        [c], resolved_handles={"steipete"}, primary_entity="Peter Steinberger",
        topic="Peter Steinberger", primary_handle="steipete",
    )
    assert c.final_score >= rerank.INTERACTION_FLOOR
    d = _candidate(author="steipete", text="shipping tonight", rel=0.0)
    d.final_score = 2.0
    rerank._apply_first_party_floor(
        [d], resolved_handles={"steipete"}, primary_entity="Peter Steinberger",
        topic="Peter Steinberger", primary_handle="steipete",
    )
    assert d.final_score >= rerank.FIRST_PARTY_FLOOR


def test_person_topic_end_to_end_keeps_subject_reply_floored():
    reply = _candidate(author="steipete", text="@someone thanks, shipping tonight", rel=0.0,
                       mentions=["someone"], engagement=1, freshness=80)
    _rerank_one_with_peers("Peter Steinberger", reply, {"steipete"}, primary_handle="steipete")
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


# --- Review fixes: provenance, subject role, short topics, engagement units --

def test_llm_reason_starting_with_fallback_is_still_cliffed():
    """Provenance comes from the scoring path, not the explanation wording."""
    tricky = _candidate(text="SQLite tricky")
    plain = _candidate(text="SQLite plain")
    payload = {"scores": [
        {"candidate_id": tricky.candidate_id, "relevance": 10,
         "reason": "fallback option only, barely related"},
        {"candidate_id": plain.candidate_id, "relevance": 10, "reason": "barely related"},
    ]}
    rerank._apply_llm_scores([tricky, plain], payload)
    assert tricky.explanation == "fallback option only, barely related"
    assert abs(tricky.final_score - plain.final_score) < 1e-9


def test_unscored_candidate_in_llm_batch_is_fallback_scored_and_not_cliffed():
    missing = _candidate(rel=0.1, text="SQLite missing")
    rerank._apply_llm_scores([missing], {"scores": []})
    assert missing.rerank_score < 20.0
    assert missing.explanation == "fallback-local-score"
    assert rerank._is_fallback_scored(missing)


def test_related_handle_inside_a_topic_word_is_not_a_subject():
    """'art' sits inside 'smart', but a related handle is never the subject."""
    reply = _candidate(author="art", text="@friend lunch was great", rel=0.0, mentions=["friend"])
    reply.final_score = 5.0
    rerank._apply_interaction_signal(
        [reply], resolved_handles={"art"}, primary_entity="Smart home", topic="Smart home",
    )
    assert reply.final_score == 5.0
    post = _candidate(author="art", text="lunch was great", rel=0.0)
    post.final_score = 2.0
    rerank._apply_first_party_floor(
        [post], resolved_handles={"art"}, primary_entity="Smart home", topic="Smart home",
    )
    assert post.final_score == 2.0


def test_related_handle_off_topic_post_end_to_end_gets_no_lift():
    reply = _candidate(author="art", text="@friend lunch was great", rel=0.0,
                       mentions=["friend"], engagement=30, freshness=82)
    _rerank_one_with_peers("Smart home", reply, {"art", "homeowner"}, primary_handle="homeowner")
    assert reply.final_score == rerank._final_score(reply)
    assert reply.final_score < rerank.FIRST_PARTY_FLOOR


def test_primary_handle_must_still_name_the_topic():
    assert rerank._subject_handles("steipete", "Peter Steinberger") == {"steipete"}
    assert rerank._subject_handles("@SteiPete", "Peter Steinberger") == {"steipete"}
    assert rerank._subject_handles("mitchellh", "Ghostty terminal") == set()
    assert rerank._subject_handles(None, "Smart home") == set()


def test_short_topic_related_handle_off_topic_post_is_not_floored():
    """'Go' cannot be grounded safely, so the floors need real relevance."""
    reply = _candidate(author="rob", text="@friend lunch was great", rel=0.0,
                       mentions=["friend"], engagement=30, freshness=82)
    _rerank_one_with_peers("Go", reply, {"rob"})
    assert reply.final_score == rerank._final_score(reply)
    assert reply.final_score < rerank.FIRST_PARTY_FLOOR


def test_short_topic_on_topic_first_party_post_is_still_floored():
    c = _candidate(author="rob", text="@someone go 1.26 generics are great", rel=0.5,
                   mentions=["someone"])
    c.final_score = 5.0
    rerank._apply_interaction_signal(
        [c], resolved_handles={"rob"}, primary_entity="Go", topic="Go",
    )
    assert c.final_score >= rerank.INTERACTION_FLOOR


def test_polymarket_engagement_does_not_saturate_on_dollar_amounts():
    pm = _item("pm", "polymarket", "Will SQLite 4 ship?", TO,
               {"volume": 5678, "liquidity": 100000})
    reddit = _item("r", "reddit", "SQLite thread", TO, {"score": 250, "num_comments": 24})
    assert signals.engagement_score(reddit) == 51
    # 0.6*log1p(5678) + 0.4*log1p(100000) over polymarket.py's own anchors
    # (0.6*16 + 0.4*14 = 15.2): 9.79 / 15.2.
    assert signals.engagement_score(pm) == 64


def test_engagement_reference_units_per_source():
    def score(source, engagement):
        return signals.engagement_score(_item("i", source, "t", TO, engagement))

    # Lifetime totals on a company or product: ~300 maps near 50, not 100.
    assert score("trustpilot", {"reviews": 300, "trustScore": 4.2}) == 50
    assert score("amazon", {"ratings": 300}) == 50
    # A busy issue sits mid-scale; a repo snapshot's lifetime stars are not engagement.
    assert 40 <= score("github", {"reactions": 50, "comments": 80}) <= 55
    assert score("github", {"stars": 2000, "comments": 50}) is None
    # A rank-1 Digg cluster with 15 posts is strong but not saturated.
    assert 60 <= score("digg", {"postCount": 15, "uniqueAuthors": 10, "rank_score": 50}) <= 75
    # No audience signal: no engagement score at all.
    assert score("jobs", {"open_roles": 1}) is None
    assert score("meta_ads", {"variants": 7}) is None
    assert score("perplexity", {"citations": 12}) is None
    assert score("grounding", {"citations": 3}) is None
    assert score("stocktwits", {"likes": 0, "reshares": 0, "followers": 50000}) is None
    assert score("telegram", {"views": 100, "reactions": 0, "subscribers": 1000000}) is None


def test_every_source_declares_its_engagement_units():
    from lib import pipeline

    sources = set(pipeline.MOCK_AVAILABLE_SOURCES) | {"stocktwits"}
    undeclared = {
        s for s in sources
        if s not in signals._ENGAGEMENT_LOG_REFERENCE
        and s not in signals._NO_ENGAGEMENT_SCORE_SOURCES
    }
    assert undeclared == set()


# --- Repo snapshot cards carry lifetime totals, not month-window engagement ---
# 2026-10-11 e2e: --github-repo project cards ranked #1 in 3 of 4 runs
# (Ghostty 62K stars, Bun 96K, SQLite 11K) on saturated engagement from
# lifetime stars, and the SQLite card also took first-party authorship credit
# because its owner ("sqlite") matched a topic-token handle.

def _repo_snapshot(item_id, repo, stars, open_issues, author, labels=("project-mode",)):
    return schema.SourceItem(
        item_id=item_id, source="github",
        title=f"{repo} ({stars // 1000}K stars) - {open_issues} open issues",
        body=f"Project: {repo}", url=f"https://github.com/{repo}", author=author,
        container=repo, published_at=TO, date_confidence="high",
        engagement={"stars": stars, "comments": open_issues},
        metadata={"labels": list(labels), "state": "open", "is_pr": False},
    )


def test_repo_snapshot_gets_no_engagement_score():
    # Project mode (owner as author) and every person-mode repo card shape.
    assert signals.engagement_score(_repo_snapshot("p", "oven-sh/bun", 96195, 9551, "oven-sh")) is None
    for labels in (["person-profile", "own-repo"], ["person-profile", "external-repo"],
                   ["person-profile", "recent-push"]):
        card = _repo_snapshot("o", "steipete/peekaboo", 3000, 12, "steipete", labels=labels)
        assert signals.engagement_score(card) is None


def test_github_issues_and_prs_keep_their_engagement():
    issue = _item("i", "github", "SQLite WAL corruption", TO, {"reactions": 50, "comments": 80})
    issue.metadata = {"labels": [], "state": "open", "is_pr": False}
    pr = _item("p", "github", "Fix WAL checkpoint", TO, {"reactions": 12, "comments": 30})
    pr.metadata = {"labels": [], "state": "closed", "is_pr": True}
    assert 40 <= signals.engagement_score(issue) <= 55
    assert signals.engagement_score(pr) > 0


def test_repo_snapshot_gets_no_first_party_credit_or_floor():
    # "sqlite" is a topic-token handle (pipeline._topic_first_party_candidates)
    # and the project card's author is the repo owner, so it matched.
    card = _repo_snapshot("g", "sqlite/sqlite", 96000, 24, "sqlite")
    with_handle = _rank("SQLite", {"github": [card]}, handles={"sqlite"})[0]
    card2 = _repo_snapshot("g", "sqlite/sqlite", 96000, 24, "sqlite")
    without = _rank("SQLite", {"github": [card2]})[0]
    assert "first-party" not in (with_handle.explanation or "")
    assert with_handle.final_score == without.final_score


def test_person_mode_repo_card_is_not_floored_but_not_pruned():
    card = _repo_snapshot("o", "steipete/peekaboo", 3000, 12, "steipete",
                          labels=["person-profile", "own-repo"])
    c = _candidate(author="steipete", text=card.title, rel=0.0, source="github")
    c.source_items = [card]
    _rerank_one_with_peers("Peter Steinberger", c, {"steipete"}, primary_handle="steipete")
    assert c.final_score < rerank.FIRST_PARTY_FLOOR
    assert "entity-miss" not in (c.explanation or "")
    assert rerank.prune_fallback_entity_misses([c], topic="Peter Steinberger") == [c]


def test_96k_star_repo_card_does_not_outrank_equally_relevant_380_point_hn_story():
    title = "sqlite/sqlite SQLite source repository"
    card = _repo_snapshot("g", "sqlite/sqlite", 96000, 24, "sqlite")
    card.title = card.body = title
    hn = _item("hn", "hackernews", title, "2026-10-09", {"points": 380, "comments": 166})
    ranked = _rank("SQLite", {"github": [card], "hackernews": [hn]}, handles={"sqlite"})
    assert [c.item_id for c in ranked] == ["hn", "g"]


# --- View-weighted platforms are calibrated on a view scale -------------------
# 2026-10-11 e2e: a 117-view YouTube video ranked #2 for SQLite, and 655-view
# TikToks with 17 likes scored near a 9,676-upvote Reddit thread, because
# log1p(views) dominates engagement_raw for YouTube/TikTok/Instagram while the
# reference was calibrated like votes.

def _score(source, engagement):
    return signals.engagement_score(_item("i", source, "t", TO, engagement))


def test_view_platform_scores_land_in_intended_bands():
    # A barely-watched upload is near the bottom, like a 3-like X post (~8).
    assert _score("youtube", {"views": 117, "likes": 1}) <= 10
    assert _score("tiktok", {"views": 1000}) <= 15
    # Hundreds of views and a few dozen likes: low, below a 50-upvote Reddit post.
    assert 10 <= _score("tiktok", {"views": 655, "likes": 17}) <= 25
    assert _score("tiktok", {"views": 655, "likes": 17}) < _score(
        "reddit", {"score": 50, "num_comments": 20, "upvote_ratio": 0.9})
    # A real hit is strong; ~1M+ views nears the top.
    assert 60 <= _score("tiktok", {"views": 133690, "likes": 8441, "comments": 192}) <= 80
    assert 75 <= _score("youtube", {"views": 726269, "likes": 13031, "comments": 1300}) <= 92
    assert _score("youtube", {"views": 5_000_000, "likes": 150_000, "comments": 5000}) >= 95
    assert _score("instagram", {"views": 1_000_000, "likes": 50_000, "comments": 1000}) >= 80


def test_655_view_tiktok_scores_well_below_9676_upvote_reddit_thread():
    tiktok = _score("tiktok", {"views": 655, "likes": 17})
    reddit = _score("reddit", {"score": 9676, "num_comments": 455, "upvote_ratio": 0.95})
    assert reddit - tiktok >= 60
