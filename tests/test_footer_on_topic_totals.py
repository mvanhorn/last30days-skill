"""Footer engagement totals and Top voices count on-topic items only.

Waymo run, 2026-11-10: the footer said "YouTube: 4 videos | 12,745,080 views"
and 12,519,652 of those views were one video about the Tesla Roadster that
never names Waymo. In person runs, posts from related handles (--x-related)
about other things counted toward the X totals and Top voices.

The items below are the YouTube section of that run's saved file. The ranking
already marks such an item (entity-miss demotion); the source dump keeps it.
"""

from lib import render, rerank, schema

ENTITY_MISS = rerank._FALLBACK_ENTITY_MISS_EXPLANATION
FIRST_PARTY = rerank._FIRST_PARTY_FALLBACK_EXPLANATION


def _item(source, item_id, title, author, engagement, body="", container=None, metadata=None):
    return schema.SourceItem(
        item_id=item_id, source=source, title=title, body=body,
        url=f"https://example.test/{source}/{item_id}", author=author, container=container,
        published_at="2026-10-20", engagement=engagement, metadata=metadata or {},
    )


def _candidate(item, explanation="fallback-local-score", final_score=50.0, local_relevance=0.6):
    return schema.Candidate(
        candidate_id=f"c-{item.item_id}", item_id=item.item_id, source=item.source,
        title=item.title, url=item.url, snippet=item.body, subquery_labels=["primary"],
        native_ranks={"primary": 1}, local_relevance=local_relevance, freshness=80,
        engagement=1, source_quality=0.7, rrf_score=0.02, sources=[item.source],
        source_items=[item], final_score=final_score, explanation=explanation,
    )


def _report(topic, items, candidates):
    by_source: dict[str, list[schema.SourceItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    return schema.Report(
        topic=topic, range_from="2026-10-12", range_to="2026-11-11",
        generated_at="2026-11-11T01:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local", planner_model="mock", rerank_model="mock",
        ),
        query_plan=schema.QueryPlan(
            intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
            raw_topic=topic, subqueries=[], source_weights={},
        ),
        clusters=[], ranked_candidates=candidates, items_by_source=by_source,
        errors_by_source={},
    )


TRANSCRIPT = {"transcript_snippet": "a transcript"}
ROBOTAXIS = _item("youtube", "TukUnoOi6VA", "3,200 Chinese Robotaxis Just Hit the US. Tesla Is Shocked",
                  "Auto Aspect", {"views": 132385, "likes": 1280},
                  body="Waymo has imported more than 3,200 electric vans", metadata=TRANSCRIPT)
CYBERCAB = _item("youtube", "A0-2H6i2teU", "Tesla Cybercab Robotaxi Mass Operation Amazing UPDATE",
                 "TESLA CAR WORLD", {"views": 59678, "likes": 786},
                 body="Waymo's sixth-generation autonomous driving system", metadata=TRANSCRIPT)
ROADSTER = _item("youtube", "scwlR0QHGOk", "The NEW Tesla Roadster: My Honest Reaction",
                 "Marques Brownlee", {"views": 12519652, "likes": 192799},
                 body="Tesla had one of the most insane product demos of all time", metadata=TRANSCRIPT)
FOURTH = _item("youtube", "waymo4", "Riding a Waymo on the freeway", "Ride Review",
               {"views": 33365, "likes": 760}, body="Waymo freeway rides", metadata=TRANSCRIPT)
WAYMO_VIDEOS = [ROBOTAXIS, CYBERCAB, ROADSTER, FOURTH]


def _youtube_line(report):
    return next(line for line in render._build_source_footer_lines(report) if "YouTube" in line)


def test_saved_waymo_run_counts_views_of_on_topic_videos_only():
    candidates = [
        _candidate(ROBOTAXIS), _candidate(CYBERCAB), _candidate(FOURTH),
        _candidate(ROADSTER, explanation=ENTITY_MISS, final_score=12.0),
    ]

    line = _youtube_line(_report("Waymo", WAYMO_VIDEOS, candidates))

    # 132,385 + 59,678 + 33,365; the item count and transcripts stay as they were.
    assert line == "🔴 YouTube: 4 videos │ 225,428 views │ 4/4 with transcripts"


def test_item_pruned_as_off_topic_is_not_counted():
    # prune_fallback_entity_misses drops the candidate; the item stays in the
    # dump and the pipeline records that it failed the relevance check.
    candidates = [_candidate(ROBOTAXIS), _candidate(CYBERCAB), _candidate(FOURTH)]
    report = _report("Waymo", WAYMO_VIDEOS, candidates)
    report.artifacts[rerank.REJECTED_ITEMS_ARTIFACT] = rerank.rejected_item_keys(
        [*candidates, _candidate(ROADSTER, explanation=ENTITY_MISS, final_score=12.0)], candidates,
    )

    assert "225,428 views" in _youtube_line(report)


def test_item_with_no_ranking_verdict_still_counts():
    # Fusion drops candidates for reasons that are not relevance (the
    # per-author cap, the pool size), so a missing verdict is not "off topic":
    # four on-topic posts by one author leave three candidates.
    posts = [
        _item("x", f"p{i}", f"Elon Musk on Starship flight {i}", "SawyerMerritt",
              {"likes": 100, "reposts": 10})
        for i in range(4)
    ]
    report = _report("Elon Musk", posts, [_candidate(post) for post in posts[:3]])

    lines = render._build_source_footer_lines(report)

    assert lines == ["🔵 X: 4 posts │ 400 likes │ 40 reposts"]
    assert render._top_voices_footer_line(report) == "🗣️ Top voices: @SawyerMerritt"


def test_fusion_author_cap_really_leaves_on_topic_items_without_a_candidate():
    # The premise of the test above, from the code: weighted_rrf keeps fewer
    # candidates than one author's on-topic posts.
    from lib import fusion

    posts = [
        _item("x", f"p{i}", f"Elon Musk on Starship flight {i}", "SawyerMerritt",
              {"likes": 100, "reposts": 10})
        for i in range(8)
    ]
    for post in posts:
        post.local_relevance, post.freshness, post.source_quality = 0.8, 80, 0.7
    plan = schema.QueryPlan(
        intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
        raw_topic="Elon Musk", source_weights={},
        subqueries=[schema.SubQuery(label="primary", search_query="Elon Musk",
                                    ranking_query="Elon Musk", sources=["x"])],
    )

    candidates = fusion.weighted_rrf({("primary", "x"): posts}, plan, pool_limit=50)

    assert 0 < len(candidates) < len(posts)


def test_pipeline_records_what_the_relevance_prune_rejected():
    kept, dropped = _candidate(ROBOTAXIS), _candidate(ROADSTER, explanation=ENTITY_MISS)

    assert rerank.rejected_item_keys([kept, dropped], [kept]) == [["youtube", ROADSTER.url]]


def test_report_with_no_ranking_counts_every_item():
    line = _youtube_line(_report("Waymo", WAYMO_VIDEOS, []))

    assert "12,745,080 views" in line


def test_source_with_nothing_on_topic_is_counted_whole():
    candidates = [_candidate(ROADSTER, explanation=ENTITY_MISS, final_score=12.0)]

    line = _youtube_line(_report("Waymo", [ROADSTER], candidates))

    assert "12,519,652 views" in line


OWN_POST = _item("x", "x1", "Starship flight 15 went great", "elonmusk", {"likes": 9000, "reposts": 900})
ABOUT_POST = _item("x", "x2", "Elon Musk says Tesla will ship Optimus in 2027", "SawyerMerritt",
                   {"likes": 400, "reposts": 40})
RELATED_ON_TOPIC = _item("x", "x3", "Elon Musk on stage at the Tesla shareholder meeting", "Tesla",
                         {"likes": 300, "reposts": 30})
RELATED_OFF_TOPIC = _item("x", "x4", "Model Y is now available in two new colors", "Tesla",
                          {"likes": 50000, "reposts": 7000})
RELATED_OFF_TOPIC_2 = _item("x", "x5", "Supercharger network passes 80,000 stalls", "Tesla",
                            {"likes": 30000, "reposts": 4000})


def _person_report():
    items = [OWN_POST, ABOUT_POST, RELATED_ON_TOPIC, RELATED_OFF_TOPIC, RELATED_OFF_TOPIC_2]
    candidates = [
        _candidate(OWN_POST, explanation=FIRST_PARTY, local_relevance=0.05),
        _candidate(ABOUT_POST),
        _candidate(RELATED_ON_TOPIC, explanation=FIRST_PARTY, local_relevance=0.5),
        _candidate(RELATED_OFF_TOPIC, explanation=FIRST_PARTY, local_relevance=0.05),
        _candidate(RELATED_OFF_TOPIC_2, explanation=rerank._FIRST_PARTY_EXPLANATION, local_relevance=0.05),
    ]
    return _report("Elon Musk", items, candidates)


def test_related_handle_posts_about_other_things_stay_out_of_x_totals():
    line = next(line for line in render._build_source_footer_lines(_person_report()) if " X:" in line)

    # The person's own posts count (nobody names themselves); a related
    # handle's posts count only when they are about the person.
    assert line == "🔵 X: 5 posts │ 9,700 likes │ 970 reposts"


def test_top_voices_are_drawn_from_on_topic_posts():
    line = render._top_voices_footer_line(_person_report())

    # Three posts by @Tesla, one about the topic: no longer the top voice.
    assert line == "🗣️ Top voices: @elonmusk, @SawyerMerritt, @Tesla"


def test_candidate_gate_keeps_the_topics_own_account_and_grounded_related_posts():
    verdicts = rerank.item_topic_verdicts(_person_report().ranked_candidates, "Elon Musk")

    assert verdicts == {
        ("x", OWN_POST.url): True,
        ("x", ABOUT_POST.url): True,
        ("x", RELATED_ON_TOPIC.url): True,
        ("x", RELATED_OFF_TOPIC.url): False,
        ("x", RELATED_OFF_TOPIC_2.url): False,
    }
