"""Story-diverse ordering of report.clusters (issue #1449 item 5)."""

import re

from lib import cluster, cluster_diversity, planner, render, schema


def _plan(labels=("primary",), intent="breaking_news", cluster_mode="story"):
    return schema.QueryPlan(
        intent=intent,
        freshness_mode="strict_recent",
        cluster_mode=cluster_mode,
        raw_topic="Microsoft",
        subqueries=[
            schema.SubQuery(
                label=label,
                search_query=f"microsoft {label}",
                ranking_query=f"What about Microsoft {label}?",
                sources=["reddit", "youtube", "hackernews"],
            )
            for label in labels
        ],
        source_weights={"reddit": 1.0, "youtube": 1.0, "hackernews": 1.0},
    )


def _candidate(cid, title, score, labels=("primary",), source="youtube", explanation=""):
    item = schema.SourceItem(
        item_id=cid,
        source=source,
        title=title,
        body=f"Body {cid}",
        url=f"https://example.com/{cid}",
        snippet=f"Snippet {cid}",
    )
    return schema.Candidate(
        candidate_id=cid,
        item_id=cid,
        source=source,
        title=title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=list(labels),
        native_ranks={f"primary:{source}": 1},
        local_relevance=0.8,
        freshness=80,
        engagement=10,
        source_quality=0.7,
        rrf_score=0.02,
        sources=[source],
        source_items=[item],
        explanation=explanation,
        final_score=score,
    )


def _singletons(candidates):
    return [
        schema.Cluster(
            cluster_id=f"cluster-{index}",
            title=c.title,
            candidate_ids=[c.candidate_id],
            representative_ids=[c.candidate_id],
            sources=[c.source],
            score=c.final_score,
        )
        for index, c in enumerate(candidates, start=1)
    ]


OUTAGE_TITLES = [
    "Microsoft 365 outage takes down Outlook and Teams",
    "Massive Microsoft 365 outage hits Outlook and Teams",
    "Microsoft 365 outage: Outlook and Teams down worldwide",
    "Outlook and Teams down in Microsoft 365 outage",
    "Microsoft 365 outage knocks Outlook and Teams offline",
    "Microsoft outage: Outlook and Teams down for millions",
]

DISTINCT_TITLES = [
    "Azure revenue grows 40 percent in quarterly earnings",
    "Copilot adds agent mode for Excel",
    "Xbox handheld leaks ahead of launch",
    "Windows 12 insider build ships new taskbar",
    "Microsoft buys gaming studio for 2 billion",
    "Teams pricing changes in Europe",
    "GitHub Universe keynote recap",
    "Surface Laptop 8 review roundup",
    "Bing search share climbs again",
    "Edge browser drops legacy extensions",
]


def _ids(clusters):
    return [c.cluster_id for c in clusters]


def test_near_identical_outage_clusters_take_at_most_three_of_top_eight():
    outage = [_candidate(f"o{i}", t, 90 - i) for i, t in enumerate(OUTAGE_TITLES)]
    other = [_candidate(f"d{i}", t, 40 - i) for i, t in enumerate(DISTINCT_TITLES[:6])]
    candidates = outage + other
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(clusters, candidates, _plan())

    head = ordered[:8]
    outage_ids = {f"cluster-{i}" for i in range(1, 7)}
    assert sum(c.cluster_id in outage_ids for c in head) == 3
    # The strongest outage cluster still leads; nothing is dropped.
    assert head[0].cluster_id == "cluster-1"
    assert sorted(_ids(ordered)) == sorted(_ids(clusters))


def test_floor_clearing_subquery_cluster_gets_a_head_slot():
    distinct = [_candidate(f"d{i}", t, 90 - i) for i, t in enumerate(DISTINCT_TITLES)]
    earnings = _candidate("e1", "Earnings beat estimates on cloud demand", 20, labels=("earnings",))
    candidates = distinct + [earnings]
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "earnings"))
    )

    head_ids = _ids(ordered[:8])
    assert "cluster-11" in head_ids
    # It takes the last head slot; the first seven keep score order.
    assert head_ids[:7] == [f"cluster-{i}" for i in range(1, 8)]
    assert head_ids[7] == "cluster-11"


def test_differently_phrased_headlines_on_one_subquery_angle_are_capped():
    # Pairwise title overlap is low, but every headline keeps the event word
    # the outage subquery searched for.
    titles = [
        "What caused the Microsoft outage affecting millions?",
        "Microsoft outage impacts millions, expert explains ripple effects",
        "Outage live: global disruption will cost Australia billions",
        "Massive M365 outage stretches past 15 hours",
        "The great outage of 2026",
        "Regulators ask questions after the outage",
    ]
    outage = [
        _candidate(f"o{i}", t, 90 - i, labels=("primary", "outage", "earnings"))
        for i, t in enumerate(titles)
    ]
    other = [_candidate(f"d{i}", t, 40 - i) for i, t in enumerate(DISTINCT_TITLES[1:7])]
    earnings = _candidate("e1", "Earnings beat estimates on cloud demand", 20, labels=("earnings",))
    candidates = outage + other + [earnings]
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "outage", "earnings"))
    )

    head = _ids(ordered[:8])
    assert sum(cid in {f"cluster-{i}" for i in range(1, 7)} for cid in head) == 3
    # Retrieved by the earnings subquery is not enough: the outage clusters
    # carry the label too, so only the cluster on the earnings angle counts.
    assert head[-1] == "cluster-13"


def test_below_floor_subquery_cluster_is_never_promoted():
    distinct = [_candidate(f"d{i}", t, 90 - i) for i, t in enumerate(DISTINCT_TITLES)]
    off_topic = _candidate(
        "e1", "Earnings beat estimates on cloud demand", 20,
        labels=("earnings",), explanation="entity-miss demotion",
    )
    candidates = distinct + [off_topic]
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "earnings"))
    )

    assert _ids(ordered) == _ids(clusters)


def test_distinct_clusters_with_subqueries_represented_keep_order():
    labels = ["primary"] * 10
    labels[2] = "earnings"
    labels[6] = "ai"
    candidates = [
        _candidate(f"d{i}", t, 90 - i, labels=(labels[i],))
        for i, t in enumerate(DISTINCT_TITLES)
    ]
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "earnings", "ai"))
    )

    assert ordered == clusters


def test_unsorted_input_without_duplicates_keeps_order():
    # cluster_mode=none keeps candidate order, which is not always score order.
    scores = [50, 80, 30, 70, 60]
    candidates = [
        _candidate(f"d{i}", DISTINCT_TITLES[i], s) for i, s in enumerate(scores)
    ]
    clusters = _singletons(candidates)

    assert cluster_diversity.diversify_clusters(clusters, candidates, _plan()) == clusters


def test_concept_plan_singleton_duplicates_still_diversify():
    plan = _plan(intent="concept", cluster_mode="none")
    candidates = [_candidate(f"o{i}", t, 90 - i) for i, t in enumerate(OUTAGE_TITLES)]
    candidates += [_candidate(f"d{i}", t, 40 - i) for i, t in enumerate(DISTINCT_TITLES[:5])]
    clusters = cluster.cluster_candidates(candidates, plan)
    assert len(clusters) == len(candidates)  # no clustering for concept plans

    ordered = cluster_diversity.diversify_clusters(clusters, candidates, plan)

    outage_ids = {f"cluster-{i}" for i in range(1, 7)}
    assert sum(c.cluster_id in outage_ids for c in ordered[:8]) <= 3


def _report(candidates, clusters, plan):
    sources = sorted({c.source for c in candidates})
    return schema.Report(
        topic="Microsoft",
        range_from="2026-10-15",
        range_to="2026-11-14",
        generated_at="2026-11-14T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-reranker",
        ),
        query_plan=plan,
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source={
            s: [i for c in candidates if c.source == s for i in c.source_items] for s in sources
        },
        errors_by_source={},
        source_status={
            s: schema.SourceOutcome(source=s, state="ok", items_returned=1) for s in sources
        },
    )


def test_below_floor_cluster_in_head_sinks_and_drill_matches_compact_numbering():
    candidates = [_candidate(f"d{i}", t, 90 - i) for i, t in enumerate(DISTINCT_TITLES)]
    candidates[2].explanation = "entity-miss demotion"
    candidates[4].final_score = 0.0
    plan = _plan()
    clusters = cluster_diversity.diversify_clusters(_singletons(candidates), candidates, plan)

    assert _ids(clusters)[-2:] == ["cluster-3", "cluster-5"]
    report = _report(candidates, clusters, plan)
    compact = render.render_compact(report)
    shown = dict(re.findall(r"^### (\d+)\. (.+?) \(score", compact, re.MULTILINE))
    assert len(shown) == 8
    for number, title in shown.items():
        assert planner.resolve_drill_clusters(report, number)[0].title == title


def test_compact_numbering_and_drill_agree_after_story_reorder():
    outage = [_candidate(f"o{i}", t, 90 - i) for i, t in enumerate(OUTAGE_TITLES)]
    other = [_candidate(f"d{i}", t, 40 - i) for i, t in enumerate(DISTINCT_TITLES[:6])]
    candidates = outage + other
    plan = _plan()
    clusters = cluster_diversity.diversify_clusters(_singletons(candidates), candidates, plan)
    report = _report(candidates, clusters, plan)

    compact = render.render_compact(report)
    shown = dict(re.findall(r"^### (\d+)\. (.+?) \(score", compact, re.MULTILINE))
    assert sum(title in OUTAGE_TITLES for title in shown.values()) == 3
    for number, title in shown.items():
        assert planner.resolve_drill_clusters(report, number)[0].title == title


def test_title_chain_and_angle_share_one_story_cap():
    # The first near-duplicate carries only the primary label; the next three
    # carry the outage angle. They are still one story: at most three in the head.
    outage = [_candidate("o0", OUTAGE_TITLES[0], 95)] + [
        _candidate(f"o{i}", OUTAGE_TITLES[i], 95 - i, labels=("outage",))
        for i in range(1, 4)
    ]
    other = [_candidate(f"d{i}", t, 40 - i) for i, t in enumerate(DISTINCT_TITLES[:6])]
    candidates = outage + other
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "outage"))
    )

    outage_ids = {f"cluster-{i}" for i in range(1, 5)}
    assert sum(c.cluster_id in outage_ids for c in ordered[:8]) == 3


def test_out_of_window_subquery_cluster_is_not_reserved_a_head_slot():
    distinct = [_candidate(f"d{i}", t, 90 - i) for i, t in enumerate(DISTINCT_TITLES)]
    earnings = _candidate("e1", "Earnings beat estimates on cloud demand", 20, labels=("earnings",))
    earnings.source_items[0].published_at = "2025-01-10"
    earnings.metadata["range_from"] = "2026-10-16"
    earnings.metadata["range_to"] = "2026-11-15"
    assert schema.candidate_out_of_window(earnings)
    candidates = distinct + [earnings]
    clusters = _singletons(candidates)

    ordered = cluster_diversity.diversify_clusters(
        clusters, candidates, _plan(("primary", "earnings"))
    )

    assert _ids(ordered[:8]) == [f"cluster-{i}" for i in range(1, 9)]


def test_out_of_window_cluster_is_not_promoted_past_current_ones():
    # A near-duplicate's penalty makes the next non-duplicate the default; an
    # old cluster must not take that place ahead of current stories.
    outage = [_candidate(f"o{i}", OUTAGE_TITLES[i], 95 - i) for i in range(2)]
    old = _candidate("old", "Earnings beat estimates on cloud demand", 93)
    old.source_items[0].published_at = "2025-01-10"
    old.metadata["range_from"] = "2026-10-16"
    old.metadata["range_to"] = "2026-11-15"
    assert schema.candidate_out_of_window(old)
    current = [_candidate(f"d{i}", t, 60 - i) for i, t in enumerate(DISTINCT_TITLES[:6])]
    candidates = outage + [old] + current
    clusters = _singletons(candidates)

    ordered = _ids(cluster_diversity.diversify_clusters(clusters, candidates, _plan(("primary",))))

    # The old cluster keeps its incoming place, after the current near-duplicate.
    assert ordered[:3] == ["cluster-1", "cluster-2", "cluster-3"]


def test_out_of_window_cluster_does_not_take_a_capped_storys_slot():
    # Four outage near-duplicates: the fourth is story-capped. The old cluster
    # behind it must not take its slot ahead of the current cluster after it.
    outage = [_candidate(f"o{i}", OUTAGE_TITLES[i], 95 - i) for i in range(4)]
    # Current clusters score low enough that the penalized near-duplicates
    # still win, so the outage story reaches its cap inside the head.
    old = _candidate("old", "Earnings beat estimates on cloud demand", 90)
    old.source_items[0].published_at = "2025-01-10"
    old.metadata["range_from"] = "2026-10-16"
    old.metadata["range_to"] = "2026-11-15"
    current = [_candidate(f"d{i}", t, 20 - i) for i, t in enumerate(DISTINCT_TITLES[:6])]
    candidates = outage + [old] + current
    clusters = _singletons(candidates)

    ordered = _ids(cluster_diversity.diversify_clusters(clusters, candidates, _plan(("primary",))))

    assert ordered[:3] == ["cluster-1", "cluster-2", "cluster-3"]
    assert ordered[3] == "cluster-6"
    assert ordered.index("cluster-5") > ordered.index("cluster-4")
