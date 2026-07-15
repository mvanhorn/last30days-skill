"""U4 - confidence floor: the fix for discovery's ranked-junk failure mode.

The named 2026-07-12 regression: on quiet windows ("sports", "AI") the sweep
ranked noise against noise and emitted five 1-like tweets as a trend list.
These tests pin the new contract: sub-floor evidence never ranks, and the
honest outcome is "nothing-solid" with the strongest weak signal named.
"""

from unittest import mock

from lib import pipeline, rerank, schema


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
