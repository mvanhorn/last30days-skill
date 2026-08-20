"""U4 - confidence floor: the fix for discovery's ranked-junk failure mode.

The named 2026-07-12 regression: on quiet windows ("sports", "AI") the sweep
ranked noise against noise and emitted five 1-like tweets as a trend list.
These tests pin the new contract: sub-floor evidence never ranks, and the
honest outcome is "nothing-solid" with the strongest weak signal named.
"""

from unittest import mock

from lib import discovery_handoff, pipeline, render, rerank, schema


def _x_item(item_id: str, text: str, likes: int, *, date: str = "2026-07-09") -> dict:
    return {
        "id": item_id,
        "text": text,
        "url": f"https://x.com/example/status/{item_id}",
        "author_handle": "example",
        "date": date,
        "engagement": {"likes": likes, "reposts": 0, "replies": 0, "quotes": 0},
        "relevance": 0.9,
    }


def _hn_item(item_id: str, title: str, points: int, comments: int, *, date: str = "2026-07-09") -> dict:
    return {
        "id": item_id,
        "title": title,
        "url": f"https://example.com/{item_id}",
        "hn_url": f"https://news.ycombinator.com/item?id={item_id}",
        "author": "example",
        "date": date,
        "engagement": {"points": points, "comments": comments},
        "relevance": 0.9,
    }


def _reddit_item(item_id: str, title: str, score: int, comments: int, *, date: str = "2026-07-09") -> dict:
    return {
        "id": item_id,
        "title": title,
        "url": f"https://reddit.com/r/example/comments/{item_id}",
        "subreddit": "example",
        "date": date,
        "engagement": {"score": score, "num_comments": comments},
        "selftext": title,
        "relevance": 0.9,
    }


def _run_discover_with(items_by_source: dict[str, list[dict]], **kwargs) -> schema.DiscoveryReport:
    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        return items_by_source.get(source, []), None

    with mock.patch.object(
        pipeline, "available_sources", return_value=list(items_by_source),
    ), mock.patch.object(
        pipeline, "_fetch_discovery_source", side_effect=fake_fetch,
    ):
        return pipeline.run_discover(
            domain=kwargs.pop("domain", "sports"),
            config={},
            as_of_date="2026-07-10",
            **kwargs,
        )


def test_junk_corpus_returns_nothing_solid_not_ranked_noise():
    """THE regression: five single-source 1-like tweets (the 'sports' corpus)
    must produce an honest empty result, never a ranked junk list."""
    report = _run_discover_with({
        "x": [
            _x_item("junk1", "Wii Sports nostalgia thread about sports", 1),
            _x_item("junk2", "kids travel sports burnout post", 1),
            _x_item("junk3", "motorsports vs stick and ball sports", 1),
            _x_item("junk4", "midjourney skateboarder sports prompt", 1),
            _x_item("junk5", "manga review mentioning sports matches", 1),
        ],
    })

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None
    assert any("confidence floor" in warning for warning in report.warnings)


def test_strong_single_source_spike_clears_floor():
    """A 1,084-point / 577-comment single-source HN thread (the '60% of US
    consumers' case) is a real story and must rank."""
    report = _run_discover_with(
        {"hackernews": [_hn_item("big1", "Sixty percent of consumers say AI in sports ads is a turnoff", 1084, 577)]},
        domain="sports",
    )

    assert report.outcome == "ok"
    assert len(report.topics) == 1
    assert "turnoff" in report.topics[0].name.lower() or report.topics[0].velocity_score > 0


def test_weak_single_source_item_stays_buried():
    """A 30-upvote single-source post is not a trend."""
    report = _run_discover_with(
        {"reddit": [_reddit_item("meh1", "Mildly interesting sports take", 25, 4)]},
    )

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None


def test_mixed_corpus_emits_only_floor_clearing_topics():
    """Strong multi-source story ranks; 1-like junk is silently dropped."""
    report = _run_discover_with({
        "hackernews": [_hn_item("story1", "NBA finals collapse shocks sports world", 450, 200)],
        "reddit": [_reddit_item("story1r", "NBA finals collapse shocks sports world", 900, 400)],
        "x": [_x_item("junkA", "random sports meme", 1)],
    })

    assert report.outcome == "ok"
    assert len(report.topics) >= 1
    names = " ".join(topic.name.lower() for topic in report.topics)
    assert "nba" in names or "finals" in names
    assert all(topic.velocity_score > 0 for topic in report.topics)
    # The 1-like junk never appears.
    assert all("meme" not in topic.name.lower() for topic in report.topics)


def test_enriched_evidence_is_judged_not_seed_evidence():
    """With enrich=True, a topic whose seed was thin but whose full-pipeline
    corpus is rich clears the floor on the enriched evidence."""
    seed = {"x": [_x_item("seed1", "quiet sports story gathering steam", 40)]}

    def fake_run(*, topic, **_kwargs):
        items = {
            "reddit": [
                schema.SourceItem(
                    item_id="e1", source="reddit", title=topic, body=topic,
                    url="https://reddit.com/r/x/1", published_at="2026-07-09",
                    engagement={"score": 800, "num_comments": 300}, snippet=topic,
                ),
            ],
            "hackernews": [
                schema.SourceItem(
                    item_id="e2", source="hackernews", title=topic, body=topic,
                    url="https://example.com/e2", published_at="2026-07-09",
                    engagement={"points": 400, "comments": 150}, snippet=topic,
                ),
            ],
        }
        return schema.Report(
            topic=topic,
            range_from="2026-06-10", range_to="2026-07-10",
            generated_at="2026-07-10T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="none",
                planner_model="deterministic",
                rerank_model="deterministic",
            ),
            query_plan=schema.QueryPlan(
                intent="factual", freshness_mode="balanced_recent",
                cluster_mode="none", raw_topic=topic, subqueries=[],
                source_weights={},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source=items, errors_by_source={},
        )

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        report = _run_discover_with(seed, enrich=True)

    assert report.outcome == "ok"
    assert len(report.topics) == 1
    topic = report.topics[0]
    # Judged on the enriched corpus: multi-source, enriched engagement.
    assert set(topic.sources) == {"hackernews", "reddit"}
    assert "evidence item" in topic.why_spiking


def test_passes_discovery_floor_policy():
    floor = rerank.passes_discovery_floor
    # Absolute junk gate.
    assert not floor(source_count=1, engagement_total=1, item_count=1)
    assert not floor(source_count=3, engagement_total=10, item_count=5)
    assert not floor(source_count=2, engagement_total=500, item_count=0)
    # Multi-source with modest engagement clears.
    assert floor(source_count=2, engagement_total=30, item_count=2)
    # Single-source needs a genuinely strong spike.
    assert not floor(source_count=1, engagement_total=100, item_count=3)
    assert floor(source_count=1, engagement_total=1600, item_count=1)


# --- U3 junk-shape gate ------------------------------------------------------
# Extends the frozen corpus above (existing cases stay byte-identical). A
# junk-shaped nomination (help-me/beginner/musing, per the host judge or
# topic_shape heuristics) loses the single-source engagement bypass, and its
# corroboration is counted against SEED listing sources - never the enriched
# corpus, which is multi-source for almost any topic that enriches cleanly.


def test_junk_shape_blocks_single_source_engagement_bypass():
    """A 226-comment single-source 'help me' thread is a busy support thread,
    not a story: junk shape disables the engagement bypass."""
    report = _run_discover_with({
        "reddit": [_reddit_item(
            "junkhelp1", "Help me understand the new Karvella sports doping ruling", 30, 226,
        )],
    })

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None


def test_same_engagement_without_junk_shape_surfaces():
    """The identical engagement in a statement shape is a genuine
    single-source spike and must still rank (non-junk behavior unchanged)."""
    report = _run_discover_with({
        "reddit": [_reddit_item(
            "story2", "Karvella sports doping ruling rocks the league", 30, 226,
        )],
    })

    assert report.outcome == "ok"
    assert len(report.topics) == 1


def test_junk_shape_with_two_seed_sources_surfaces():
    """A junk-shaped story corroborated across two SEED listing sources
    (reddit + hackernews) clears the floor."""
    title = "Help me understand the Marseille sports betting collapse"
    report = _run_discover_with({
        "reddit": [_reddit_item("junk2a", title, 40, 30)],
        "hackernews": [_hn_item("junk2b", title, 35, 20)],
    })

    assert report.outcome == "ok"
    assert len(report.topics) == 1
    assert set(report.topics[0].sources) == {"hackernews", "reddit"}


def test_junk_corroboration_counts_seed_sources_not_enriched_corpus():
    """THE key case: a junk-shaped topic with ONE seed listing source must
    fail the floor even when enrichment succeeded and returned a rich
    multi-source corpus - a successful enrichment pass pulls a multi-source
    corpus for almost any topic, so an enriched-count check would never bind."""
    seed = {"reddit": [_reddit_item(
        "junkseed1", "Help me understand the new Karvella sports doping ruling", 40, 226,
    )]}

    def fake_run(*, topic, **_kwargs):
        items = {
            "reddit": [
                schema.SourceItem(
                    item_id="e1", source="reddit", title=topic, body=topic,
                    url="https://reddit.com/r/x/1", published_at="2026-07-09",
                    engagement={"score": 800, "num_comments": 300}, snippet=topic,
                ),
            ],
            "hackernews": [
                schema.SourceItem(
                    item_id="e2", source="hackernews", title=topic, body=topic,
                    url="https://example.com/e2", published_at="2026-07-09",
                    engagement={"points": 400, "comments": 150}, snippet=topic,
                ),
            ],
        }
        return schema.Report(
            topic=topic,
            range_from="2026-06-10", range_to="2026-07-10",
            generated_at="2026-07-10T00:00:00+00:00",
            provider_runtime=schema.ProviderRuntime(
                reasoning_provider="none",
                planner_model="deterministic",
                rerank_model="deterministic",
            ),
            query_plan=schema.QueryPlan(
                intent="factual", freshness_mode="balanced_recent",
                cluster_mode="none", raw_topic=topic, subqueries=[],
                source_weights={},
            ),
            clusters=[], ranked_candidates=[],
            items_by_source=items, errors_by_source={},
        )

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        report = _run_discover_with(seed, enrich=True)

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None


def test_weak_signal_prefers_non_junk_failure():
    """A nothing-solid brief names the strongest NON-junk weak signal even
    when a junk-shaped failure has higher velocity."""
    report = _run_discover_with({
        "reddit": [_reddit_item("junkfast1", "Help me pick my first sports bike", 30, 100)],
        "hackernews": [_hn_item("slow1", "Zion Bay sports arena funding vote stalls", 20, 10)],
    })

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None
    assert "zion" in report.weak_signal.lower()


def test_weak_signal_named_when_all_failures_junk():
    """When every sub-floor failure is junk-shaped, the brief still names one
    (never empty when failures exist)."""
    report = _run_discover_with({
        "reddit": [
            _reddit_item("alljunk1", "Help me pick my first sports bike", 40, 20),
            _reddit_item("alljunk2", "Any advice on sports nutrition apps", 25, 15),
        ],
    })

    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal is not None


def test_one_shot_live_run_emits_heuristics_note_once(capsys):
    """Every non-mock one-shot run must say LOUDLY (exactly once) that names
    are deterministic heuristics with no angles, pointing at the host-judged
    SKILL.md protocol - never at provider API keys (the engine-side judge is
    gone; no key would change this path)."""
    report = _run_discover_with(
        {"hackernews": [_hn_item("big1", "Sixty percent of consumers say AI in sports ads is a turnoff", 1084, 577)]},
    )

    assert report.outcome == "ok"
    err = capsys.readouterr().err
    assert err.count("deterministic heuristics") == 1
    assert "host-judged" in err
    assert "SKILL.md" in err
    for key_advice in ("API key", "GEMINI_API_KEY", "XAI_API_KEY",
                       "OPENROUTER_API_KEY", "OpenAI auth"):
        assert key_advice not in err
    # The one-shot path generates no angles at all, so no angle lines render.
    assert all(
        topic.podcast_angle is None and topic.x_article_angle is None
        for topic in report.topics
    )
    rendered = render.render_discovery(report)
    assert "**Podcast angle:**" not in rendered
    assert "**X article angle:**" not in rendered


# --- Same-story fold + velocity rank order -----------------------------------
# Real-run regression (2026-07): "China open-weights AI strategy is winning"
# and "Chinese models" surfaced as two ranked topics quoting the IDENTICAL
# 1,635-vote comment. Survivors that share enriched evidence are the same
# story: fold them, keep the higher velocity, and rank by displayed velocity.

KESTREL_TITLE = "Kestrel Avionics Merger Approved"
SOURDOUGH_TITLE = "Sourdough Robot Bakery Funding"

_SHARED_COMMENT = {
    "text": "The merger filings quietly admit the avionics unit was insolvent",
    "score": 1635,
    "author": "modelwatcher",
}


def _evidence_item(
    item_id: str,
    source: str,
    title: str,
    url: str,
    *,
    score: int = 500,
    comments: int = 200,
    top_comments: list[dict] | None = None,
) -> schema.SourceItem:
    engagement = (
        {"score": score, "num_comments": comments}
        if source == "reddit"
        else {"points": score, "comments": comments}
    )
    return schema.SourceItem(
        item_id=item_id, source=source, title=title, body=title,
        url=url, published_at="2026-07-09",
        engagement=engagement, snippet=title,
        metadata={"top_comments": top_comments} if top_comments else {},
    )


def _fake_report(topic: str, items: list[schema.SourceItem]) -> schema.Report:
    by_source: dict[str, list[schema.SourceItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    return schema.Report(
        topic=topic,
        range_from="2026-06-10", range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="none",
            planner_model="deterministic",
            rerank_model="deterministic",
        ),
        query_plan=schema.QueryPlan(
            intent="factual", freshness_mode="balanced_recent",
            cluster_mode="none", raw_topic=topic, subqueries=[],
            source_weights={},
        ),
        clusters=[], ranked_candidates=[],
        items_by_source=by_source, errors_by_source={},
    )


def _run_discover_enriched(reports_by_key: dict[str, list[schema.SourceItem]]) -> schema.DiscoveryReport:
    """Two strong seed stories (Kestrel first / higher seed velocity), each
    enriched via a fake pipeline.run keyed on the topic name."""
    seed = {"hackernews": [
        _hn_item("k1", KESTREL_TITLE, 900, 400),
        _hn_item("s1", SOURDOUGH_TITLE, 700, 300),
    ]}

    def fake_run(*, topic, **_kwargs):
        for key, items in reports_by_key.items():
            if key in topic.lower():
                return _fake_report(topic, items)
        raise AssertionError(f"unexpected enrichment topic: {topic!r}")

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        return _run_discover_with(seed, enrich=True)


def test_same_story_survivors_fold_to_higher_velocity_one(capsys):
    """Two distinct-named survivors quoting the IDENTICAL top comment (and
    sharing 2 evidence URLs) are one story: only the higher-velocity one
    ships, a fold line reaches stderr, ranks are contiguous from 1, and the
    surviving topic ships without engine-written angles."""
    report = _run_discover_enriched({
        "kestrel": [
            _evidence_item("ka", "reddit", KESTREL_TITLE,
                           "https://reddit.com/r/aero/comments/shared1",
                           score=900, comments=300, top_comments=[_SHARED_COMMENT]),
            _evidence_item("kb", "hackernews", KESTREL_TITLE,
                           "https://news.example.com/shared2",
                           score=500, comments=200),
        ],
        "sourdough": [
            _evidence_item("sa", "reddit", SOURDOUGH_TITLE,
                           "https://reddit.com/r/aero/comments/shared1",
                           score=300, comments=100, top_comments=[_SHARED_COMMENT]),
            _evidence_item("sb", "hackernews", SOURDOUGH_TITLE,
                           "https://news.example.com/shared2",
                           score=200, comments=80),
        ],
    })

    assert report.outcome == "ok"
    assert len(report.topics) == 1
    survivor = report.topics[0]
    assert survivor.rank == 1
    assert "kestrel" in survivor.name.lower()
    err = capsys.readouterr().err
    assert "folded duplicate story" in err
    assert survivor.podcast_angle is None
    assert survivor.x_article_angle is None


def test_distinct_stories_do_not_fold():
    """No shared URLs, different comments: both genuinely distinct stories
    survive with contiguous ranks."""
    report = _run_discover_enriched({
        "kestrel": [
            _evidence_item("ka", "reddit", KESTREL_TITLE,
                           "https://reddit.com/r/aero/comments/k1",
                           score=900, comments=300,
                           top_comments=[{"text": "Regulators folded like a cheap suit here", "score": 40, "author": "a"}]),
            _evidence_item("kb", "hackernews", KESTREL_TITLE,
                           "https://news.example.com/k2", score=500, comments=200),
        ],
        "sourdough": [
            _evidence_item("sa", "reddit", SOURDOUGH_TITLE,
                           "https://reddit.com/r/bread/comments/s1",
                           score=300, comments=100,
                           top_comments=[{"text": "The starter culture is doing the heavy lifting", "score": 30, "author": "b"}]),
            _evidence_item("sb", "hackernews", SOURDOUGH_TITLE,
                           "https://news.example.com/s2", score=200, comments=80),
        ],
    })

    assert len(report.topics) == 2
    assert [topic.rank for topic in report.topics] == [1, 2]


def test_rank_order_follows_displayed_velocity():
    """Seed order inverts enriched velocity: rank 1 must be the topic with the
    higher DISPLAYED velocity, and rank values equal list positions."""
    report = _run_discover_enriched({
        # Kestrel is the stronger SEED story but enriches thin.
        "kestrel": [
            _evidence_item("ka", "reddit", KESTREL_TITLE,
                           "https://reddit.com/r/aero/comments/k1",
                           score=100, comments=50),
            _evidence_item("kb", "hackernews", KESTREL_TITLE,
                           "https://news.example.com/k2", score=60, comments=20),
        ],
        "sourdough": [
            _evidence_item("sa", "reddit", SOURDOUGH_TITLE,
                           "https://reddit.com/r/bread/comments/s1",
                           score=900, comments=300),
            _evidence_item("sb", "hackernews", SOURDOUGH_TITLE,
                           "https://news.example.com/s2", score=500, comments=200),
        ],
    })

    assert len(report.topics) == 2
    assert [topic.rank for topic in report.topics] == [1, 2]
    assert "sourdough" in report.topics[0].name.lower()
    assert "kestrel" in report.topics[1].name.lower()
    assert report.topics[0].velocity_score > report.topics[1].velocity_score


def test_url_only_overlap_folds_without_shared_comment(capsys):
    """No top comments at all, but 3 shared evidence URLs: still one story."""
    shared_urls = [
        "https://reddit.com/r/aero/comments/shared1",
        "https://reddit.com/r/aero/comments/shared2",
        "https://news.example.com/shared3",
    ]
    report = _run_discover_enriched({
        "kestrel": [
            _evidence_item("ka", "reddit", KESTREL_TITLE, shared_urls[0], score=900, comments=300),
            _evidence_item("kb", "reddit", KESTREL_TITLE, shared_urls[1], score=400, comments=100),
            _evidence_item("kc", "hackernews", KESTREL_TITLE, shared_urls[2], score=500, comments=200),
        ],
        "sourdough": [
            _evidence_item("sa", "reddit", SOURDOUGH_TITLE, shared_urls[0], score=300, comments=100),
            _evidence_item("sb", "reddit", SOURDOUGH_TITLE, shared_urls[1], score=100, comments=40),
            _evidence_item("sc", "hackernews", SOURDOUGH_TITLE, shared_urls[2], score=200, comments=80),
        ],
    })

    assert len(report.topics) == 1
    assert report.topics[0].rank == 1
    assert "kestrel" in report.topics[0].name.lower()
    assert "folded duplicate story" in capsys.readouterr().err


def test_single_shared_url_with_different_comments_does_not_fold():
    """Exactly 1 shared URL and different top comments is corroboration
    overlap, not the same story."""
    report = _run_discover_enriched({
        "kestrel": [
            _evidence_item("ka", "reddit", KESTREL_TITLE,
                           "https://reddit.com/r/aero/comments/k1",
                           score=900, comments=300,
                           top_comments=[{"text": "Regulators folded like a cheap suit here", "score": 40, "author": "a"}]),
            _evidence_item("kb", "hackernews", KESTREL_TITLE,
                           "https://news.example.com/shared", score=500, comments=200),
        ],
        "sourdough": [
            _evidence_item("sa", "reddit", SOURDOUGH_TITLE,
                           "https://reddit.com/r/bread/comments/s1",
                           score=300, comments=100,
                           top_comments=[{"text": "The starter culture is doing the heavy lifting", "score": 30, "author": "b"}]),
            _evidence_item("sb", "hackernews", SOURDOUGH_TITLE,
                           "https://news.example.com/shared", score=200, comments=80),
        ],
    })

    assert len(report.topics) == 2
    assert [topic.rank for topic in report.topics] == [1, 2]


def _fold_record(
    name: str,
    velocity: float,
    urls: list[str],
    comment: str | None = None,
) -> dict:
    """Minimal floor-survivor record: only the fields the fold reads."""
    return {
        "name": name,
        "velocity_score": velocity,
        "top_comment": comment,
        "evidence_urls": urls,
    }


def test_fold_three_way_chain_collapses_to_one_survivor(capsys):
    """F18: after a replacement fold, the survivor re-scans to a fixpoint. A
    kept, B kept, then C (highest velocity) shares the comment with A and two
    URLs with B: one survivor, and BOTH folds are logged by name."""
    a = _fold_record("Story A", 10.0, ["https://a/1", "https://a/2"],
                     comment="the shared 1,635-vote take")
    b = _fold_record("Story B", 5.0, ["https://b/1", "https://b/2"])
    c = _fold_record("Story C", 20.0, ["https://b/1", "https://b/2", "https://c/1"],
                     comment="the shared 1,635-vote take")

    folded = pipeline._fold_same_story_records([a, b, c])

    assert [record["name"] for record in folded] == ["Story C"]
    err = capsys.readouterr().err
    assert "folded duplicate story 'Story A' into 'Story C'" in err
    assert "folded duplicate story 'Story B' into 'Story C'" in err


def test_fold_velocity_inversion_replaces_kept_twin_and_logs_names(capsys):
    """F13: the first-processed LOWER-velocity twin is replaced by the
    second-processed higher-velocity twin, and the log line names the right
    direction (low folded INTO high)."""
    low = _fold_record("Low velocity twin", 5.0, ["https://s/1", "https://s/2"])
    high = _fold_record("High velocity twin", 9.0, ["https://s/1", "https://s/2"])

    folded = pipeline._fold_same_story_records([low, high])

    assert [record["name"] for record in folded] == ["High velocity twin"]
    err = capsys.readouterr().err
    assert (
        "folded duplicate story 'Low velocity twin' into 'High velocity twin'"
        in err
    )


def test_passes_discovery_floor_junk_params():
    floor = rerank.passes_discovery_floor
    # Junk + single seed source: no engagement bypass, however huge.
    assert not floor(source_count=1, engagement_total=999, item_count=3,
                     junk_shape=True, seed_source_count=1)
    # Junk corroboration binds on SEED sources - a rich enriched corpus
    # (source_count high) cannot rescue a single-seed-source junk topic.
    assert not floor(source_count=5, engagement_total=999, item_count=10,
                     junk_shape=True, seed_source_count=1)
    # Junk + seed corroboration >= FLOOR_MIN_SOURCES clears.
    assert floor(source_count=1, engagement_total=30, item_count=2,
                 junk_shape=True, seed_source_count=2)
    # Junk still needs the absolute engagement minimum.
    assert not floor(source_count=2, engagement_total=10, item_count=2,
                     junk_shape=True, seed_source_count=2)
    # Junk without a seed count falls back to the evidence source count -
    # corroboration still required, bypass still off.
    assert floor(source_count=2, engagement_total=30, item_count=2, junk_shape=True)
    assert not floor(source_count=1, engagement_total=999, item_count=1, junk_shape=True)
    # Non-junk behavior is unchanged, seed count present or not.
    assert floor(source_count=1, engagement_total=1600, item_count=1,
                 junk_shape=False, seed_source_count=1)
    assert floor(source_count=2, engagement_total=30, item_count=2,
                 junk_shape=False, seed_source_count=1)


# --- U4 leg 2 resume: host judgments, slots, floor, fold ----------------------
# The resume leg replays the SAME floor/fold/rank code path over judged rows:
# host-junk rows never contend for enrichment slots, heuristic-junk fallback
# rows keep the seed-corroboration rule, and every velocity/momentum figure is
# scored against the bundle's momentum window - never the resume-time clock.


def _seed_item(
    item_id: str,
    source: str,
    title: str,
    *,
    points: int = 300,
    comments: int = 40,
    published_at: str = "2026-07-09",
) -> schema.SourceItem:
    engagement = (
        {"score": points, "num_comments": comments}
        if source == "reddit"
        else {"points": points, "comments": comments}
    )
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=f"https://{source}.example/{item_id}",
        published_at=published_at,
        engagement=engagement,
        snippet=f"Evidence about {title}",
    )


def _bundle_row(
    nomination_id: str,
    name: str,
    items: list[schema.SourceItem],
    *,
    heuristic_junk: bool = False,
) -> discovery_handoff.BundleNomination:
    return discovery_handoff.BundleNomination(
        nomination_id=nomination_id,
        nomination=pipeline.Nomination(
            name=name,
            seed_score=50.0,
            items=items,
            summary=f"Summary of {name}",
            junk_shape=heuristic_junk,
            worthiness=None,
        ),
        cluster_id=f"c-{nomination_id}",
        heuristic_name=name,
        heuristic_junk=heuristic_junk,
        sources=sorted({item.source for item in items}),
        engagement_by_source={},
    )


def _resume_bundle(
    rows: list[discovery_handoff.BundleNomination],
    *,
    tier: str = "deep",
    to_date: str = "2026-07-10",
) -> discovery_handoff.NominationsBundle:
    return discovery_handoff.NominationsBundle(
        schema_version=schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION,
        bundle_id="cafef00dcafef00d",
        generated_at=f"{to_date}T00:00:00Z",
        from_date="2026-06-10",
        to_date=to_date,
        domain="AI agents",
        tier=tier,
        enrichment_source_boundary=None,
        requested_sources=None,
        lookback_days=30,
        nominations=rows,
    )


def _judgment(name=None, junk=None, worthiness=None) -> discovery_handoff.HostJudgment:
    return discovery_handoff.HostJudgment(name=name, junk=junk, worthiness=worthiness)


def _enrich_spy(seen: dict):
    def spy(nominations, **kwargs):
        seen["nominations"] = list(nominations)
        seen.update(kwargs)
        return [pipeline.EnrichedTopic(nomination=n) for n in nominations]
    return spy


def test_resume_host_junk_never_takes_a_slot_next_candidate_does():
    """AE4: a host-junk nomination is excluded from slot contention outright,
    so the next blended candidate inherits its slot; a heuristic-junk fallback
    row with a single seed source is skipped pre-enrichment (it structurally
    cannot pass the floor's seed-corroboration rule)."""
    rows = [
        _bundle_row(f"n{index}", f"Story {chr(64 + index)}",
                    [_seed_item(f"s{index}", "hackernews", f"Story {chr(64 + index)}",
                                points=900 - 50 * index)])
        for index in range(1, 8)  # n1..n7: one more than ENRICH_LIMIT
    ]
    rows.append(_bundle_row(
        "n8", "Help me pick a framework",
        [_seed_item("s8", "reddit", "Help me pick a framework", points=500)],
        heuristic_junk=True,
    ))
    assert pipeline.ENRICH_LIMIT == 6
    seen: dict = {}
    judgments = {"n1": _judgment(junk=True)}
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    enriched_names = [n.name for n in seen["nominations"]]
    assert len(enriched_names) == pipeline.ENRICH_LIMIT
    assert "Story A" not in enriched_names       # host-junk: no slot
    assert "Story G" in enriched_names           # n7 takes the freed slot
    assert "Help me pick a framework" not in enriched_names  # sub-floor junk fallback


def test_resume_quiet_but_worthy_survives_the_cut():
    """Relocated from the retired engine-judge suite, retargeted to the
    judgments-file path: host worthiness blends into slot ranking BEFORE the
    ENRICH_LIMIT cut, so a low-velocity worthiness-90 row survives while the
    weakest of six high-velocity worthiness-10 rows is the one cut."""
    rows = [
        _bundle_row(f"n{index}", f"Viral story {chr(64 + index)}",
                    [_seed_item(f"v{index}", "hackernews",
                                f"Viral story {chr(64 + index)}",
                                points=100 - index, comments=20)])
        for index in range(1, 7)  # n1..n6 fill every slot on velocity alone
    ]
    rows.append(_bundle_row(
        "n7", "Quiet maintainer burnout wave",
        [_seed_item("q1", "hackernews", "Quiet maintainer burnout wave",
                    points=45, comments=15)],
    ))
    judgments = {
        f"n{index}": _judgment(worthiness=10) for index in range(1, 7)
    }
    judgments["n7"] = _judgment(worthiness=90)
    seen: dict = {}
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    enriched_names = [n.name for n in seen["nominations"]]
    assert len(enriched_names) == pipeline.ENRICH_LIMIT
    # The quiet-but-worthy row outranks every viral-but-junky one (blend
    # multipliers span 0.5x-1.5x) and takes the top slot.
    assert enriched_names[0] == "Quiet maintainer burnout wave"
    # The weakest viral row is the one cut, not the quiet rescue.
    assert "Viral story F" not in enriched_names


def test_resume_judgments_omitting_row_falls_back_to_heuristics():
    """AE2: a judgments file that omits a nomination is legal - the omitted
    row keeps the bundle's heuristic name and junk flag, and the run
    completes with both topics ranked."""
    rows = [
        _bundle_row("n1", "Kestrel avionics merger",
                    [_seed_item("k1", "hackernews", "Kestrel avionics merger",
                                points=900, comments=400)]),
        _bundle_row("n2", "Sourdough robot bakery",
                    [_seed_item("s1", "reddit", "Sourdough robot bakery",
                                points=700, comments=300)]),
    ]
    judgments = {"n1": _judgment(name="Kestrel Merger Fallout", worthiness=80)}
    topics_run: list[str] = []

    def fake_run(*, topic, **_kwargs):
        topics_run.append(topic)
        raise RuntimeError("enrichment down")  # nomination-only is fine here

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        result = pipeline.run_discover_resume(_resume_bundle(rows), {}, config={})
        report_heuristic_only = result.report
        topics_run.clear()
        result = pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    report = result.report
    assert report.outcome == "ok"
    assert report_heuristic_only.outcome == "ok"
    assert sorted(topics_run) == ["Kestrel Merger Fallout", "Sourdough robot bakery"]
    names = [topic.name for topic in report.topics]
    assert "Kestrel Merger Fallout" in names   # host name applied
    assert "Sourdough robot bakery" in names   # omitted row: heuristic name
    assert set(result.angle_inputs) == {"n1", "n2"}


def test_resume_host_not_junk_clears_heuristic_junk_shape_at_floor():
    """A host verdict junk=false overrides a junk heuristic shape: the row
    reaches the floor with junk_shape=False, so the single-source engagement
    bypass applies again."""
    rows = [_bundle_row(
        "n1", "Help me understand the Karvella doping ruling",
        [_seed_item("s1", "reddit",
                    "Help me understand the Karvella doping ruling",
                    points=30, comments=400)],
        heuristic_junk=True,
    )]
    judgments = {"n1": _judgment(name="Karvella doping ruling", junk=False)}
    with mock.patch.object(
        pipeline, "run", side_effect=RuntimeError("enrichment down"),
    ):
        result = pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    report = result.report
    assert report.outcome == "ok"
    assert [topic.name for topic in report.topics] == ["Karvella doping ruling"]


def test_resume_heuristic_junk_fallback_keeps_seed_corroboration_rule():
    """A judgment-omitted junk-shaped row keeps its heuristic flag: with two
    seed listing sources it earns a slot and clears the junk floor; the
    single-seed-source twin never even enriches."""
    title = "Help me understand the Marseille betting collapse"
    corroborated = _bundle_row(
        "n1", title,
        [
            _seed_item("s1", "reddit", title, points=40, comments=30),
            _seed_item("s2", "hackernews", title, points=35, comments=20),
        ],
        heuristic_junk=True,
    )
    seen: dict = {}
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        result = pipeline.run_discover_resume(
            _resume_bundle([corroborated]), {}, config={},
        )

    assert [n.name for n in seen["nominations"]] == [title]
    assert seen["nominations"][0].junk_shape is True  # heuristic flag survives
    report = result.report
    assert report.outcome == "ok"
    assert [topic.name for topic in report.topics] == [title]


def test_resume_zero_survivors_prefers_non_junk_weak_signal():
    """Nothing-solid on the resume path: the strongest NON-junk floor failure
    is named ahead of a higher-velocity host-junk exclusion, and no enrichment
    slot is ever spent."""
    rows = [
        _bundle_row("n1", "Viral junk story",
                    [_seed_item("s1", "hackernews", "Viral junk story", points=900)]),
        _bundle_row("n2", "Quiet real story",
                    [_seed_item("s2", "reddit", "Quiet real story",
                                points=20, comments=6)]),
    ]
    judgments = {"n1": _judgment(junk=True)}
    with mock.patch.object(
        pipeline, "run", side_effect=RuntimeError("enrichment down"),
    ):
        result = pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    report = result.report
    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal == "Quiet real story"
    assert result.angle_inputs == {}
    assert any("confidence floor" in warning for warning in report.warnings)


def test_resume_all_host_junk_names_junk_weak_signal_and_skips_enrichment():
    """Every row host-junked: the brief still names the strongest signal
    (junk-tracked, never empty when failures exist) and enrichment never runs."""
    rows = [
        _bundle_row("n1", "Junk story one",
                    [_seed_item("s1", "hackernews", "Junk story one", points=900)]),
        _bundle_row("n2", "Junk story two",
                    [_seed_item("s2", "reddit", "Junk story two", points=100)]),
    ]
    judgments = {"n1": _judgment(junk=True), "n2": _judgment(junk=True)}
    with mock.patch.object(pipeline, "enrich_nominations") as enrich:
        result = pipeline.run_discover_resume(_resume_bundle(rows), judgments, config={})

    enrich.assert_not_called()
    report = result.report
    assert report.topics == []
    assert report.outcome == "nothing-solid"
    assert report.weak_signal == "Junk story one"


def test_resume_velocity_and_momentum_pinned_to_bundle_window():
    """Scenario 7: with a bundle whose to_date is NOT today, velocity and
    momentum must be computed against the bundle window - identical to an
    in-memory computation at that as_of date, and different from today's."""
    items = [_seed_item("s1", "hackernews", "Window pinned story",
                        points=900, comments=400, published_at="2026-07-09")]
    rows = [_bundle_row("n1", "Window pinned story", items)]
    with mock.patch.object(
        pipeline, "run", side_effect=RuntimeError("enrichment down"),
    ):
        result = pipeline.run_discover_resume(
            _resume_bundle(rows, to_date="2026-07-10"), {}, config={},
        )

    report = result.report
    assert len(report.topics) == 1
    topic = report.topics[0]
    expected = round(rerank.discovery_velocity_score(items, as_of_date="2026-07-10"), 2)
    assert topic.velocity_score == expected
    from datetime import date as _date
    today = _date.today().isoformat()
    at_today = round(rerank.discovery_velocity_score(items, as_of_date=today), 2)
    assert topic.velocity_score != at_today
    # Published 1 day before the bundle window's end: new-this-week by the
    # bundle clock even though it is weeks old by the resume-time clock.
    assert topic.momentum == "new-this-week"


def test_resume_reuses_same_story_fold_and_velocity_ranks(capsys):
    """The committed fold/rank path runs on leg 2 too: two judged survivors
    sharing enriched evidence fold to the higher-velocity one, and the angle
    inputs are keyed by the SURVIVING nomination id only."""
    shared_comment = {
        "text": "The merger filings quietly admit the unit was insolvent",
        "score": 1635,
        "author": "modelwatcher",
    }
    rows = [
        _bundle_row("n1", KESTREL_TITLE,
                    [_seed_item("k1", "hackernews", KESTREL_TITLE, points=900)]),
        _bundle_row("n2", SOURDOUGH_TITLE,
                    [_seed_item("s1", "hackernews", SOURDOUGH_TITLE, points=700)]),
    ]

    def fake_run(*, topic, **_kwargs):
        strong = "Kestrel" in topic
        return _fake_report(topic, [
            _evidence_item(
                f"{topic[:4]}-a", "reddit", topic,
                "https://reddit.com/r/aero/comments/shared1",
                score=900 if strong else 300,
                comments=300 if strong else 100,
                top_comments=[shared_comment],
            ),
            _evidence_item(
                f"{topic[:4]}-b", "hackernews", topic,
                "https://news.example.com/shared2",
                score=500 if strong else 200,
                comments=200 if strong else 80,
            ),
        ])

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        result = pipeline.run_discover_resume(_resume_bundle(rows), {}, config={})

    report = result.report
    assert report.outcome == "ok"
    assert len(report.topics) == 1
    assert report.topics[0].rank == 1
    assert "Kestrel" in report.topics[0].name
    assert list(result.angle_inputs) == ["n1"]
    entry = result.angle_inputs["n1"]
    assert set(entry) == {"name", "titles", "top_comment", "engagement"}
    assert entry["name"] == report.topics[0].name
    assert "folded duplicate story" in capsys.readouterr().err


def test_resume_report_carries_restored_leg1_source_status_and_warning():
    """F1b: the resume report's source_status is the bundle's restored leg-1
    sweep status - a degraded feed from the sweep reaches the leg-2 report
    and its degraded-sources warning, exactly as the one-shot reports it."""
    import dataclasses

    status = {
        "hackernews": schema.SourceOutcome(
            source="hackernews", state="ok", items_returned=1,
        ),
        "reddit": schema.SourceOutcome(
            source="reddit", state=schema.UNREACHABLE, detail="dns failure",
        ),
    }
    rows = [_bundle_row(
        "n1", "Window pinned story",
        [_seed_item("s1", "hackernews", "Window pinned story",
                    points=900, comments=400)],
    )]
    bundle = dataclasses.replace(_resume_bundle(rows), source_status=status)
    with mock.patch.object(
        pipeline, "run", side_effect=RuntimeError("enrichment down"),
    ):
        result = pipeline.run_discover_resume(bundle, {}, config={})

    report = result.report
    assert report.source_status == status
    assert any(
        "Some discovery sources degraded: reddit" in warning
        for warning in report.warnings
    )
