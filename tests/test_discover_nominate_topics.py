"""U2 - nomination ranking: cluster nominated items into named, seed-ranked
candidate topics.

nominate_topics() is the contract between the nominate stage and the
enrichment fan-out: names ordered by seed velocity, deduped casefold, never
padded past what the evidence supports.
"""

from lib import pipeline, schema


def _item(
    item_id: str,
    source: str,
    title: str,
    *,
    published_at: str = "2026-07-09",
    engagement: dict[str, int | float] | None = None,
) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=title,
        url=f"https://{source}.example/{item_id}",
        published_at=published_at,
        engagement=engagement or {},
        snippet=f"Evidence about {title}",
    )


def _bundle(items: list[schema.SourceItem]) -> schema.RetrievalBundle:
    bundle = schema.RetrievalBundle()
    by_source: dict[str, list[schema.SourceItem]] = {}
    for item in items:
        by_source.setdefault(item.source, []).append(item)
    for source, source_items in by_source.items():
        bundle.add_items("discovery-listings", source, source_items)
    return bundle


def _query_plan(domain: str, sources: list[str]) -> schema.QueryPlan:
    return schema.QueryPlan(
        intent="breaking_news",
        freshness_mode="breaking",
        cluster_mode="story",
        raw_topic=domain,
        subqueries=[schema.SubQuery(
            label="discovery-listings",
            search_query=domain,
            ranking_query=f"What is accelerating in {domain}?",
            sources=sources,
        )],
        source_weights={source: 1.0 for source in sources},
        notes=["discover-mode", "listing-sweep"],
    )


def _plan(domain: str, sources: list[str]) -> schema.DiscoveryPlan:
    return schema.DiscoveryPlan(
        domain=domain, category=None, subreddits=["all"], sources=sources,
    )


def test_nominations_ranked_by_seed_velocity():
    """A high-engagement recent story outranks a low-engagement one."""
    items = [
        _item("hot1", "hackernews", "GPT-6 rumors flood the valley",
              engagement={"points": 900, "num_comments": 400}),
        _item("cold1", "hackernews", "Minor framework patch notes released",
              engagement={"points": 3, "num_comments": 1}),
    ]
    nominations = pipeline.nominate_topics(
        _bundle(items), _query_plan("AI", ["hackernews"]), _plan("AI", ["hackernews"]),
        to_date="2026-07-10", limit=10,
    )
    assert nominations, "expected at least one nomination"
    assert "GPT-6" in nominations[0].name
    assert nominations[0].seed_score >= (nominations[-1].seed_score)


def test_nominations_dedupe_names_casefold():
    """Two clusters resolving to the same casefolded name yield one nomination."""
    items = [
        _item("a1", "hackernews", "OpenAI Agent SDK",
              engagement={"points": 500, "num_comments": 100}),
        _item("a2", "reddit", "openai agent sdk",
              engagement={"score": 300, "num_comments": 80}),
    ]
    nominations = pipeline.nominate_topics(
        _bundle(items), _query_plan("AI agents", ["hackernews", "reddit"]),
        _plan("AI agents", ["hackernews", "reddit"]),
        to_date="2026-07-10", limit=10,
    )
    names = [nomination.name.casefold() for nomination in nominations]
    assert len(names) == len(set(names))


def test_fewer_clusters_than_limit_returns_all_without_padding():
    items = [
        _item("only1", "hackernews", "Quantum breakthrough announced",
              engagement={"points": 250, "num_comments": 60}),
    ]
    nominations = pipeline.nominate_topics(
        _bundle(items), _query_plan("quantum", ["hackernews"]),
        _plan("quantum", ["hackernews"]),
        to_date="2026-07-10", limit=8,
    )
    assert 1 <= len(nominations) < 8


def test_zero_velocity_clusters_are_dropped():
    """Items with no engagement produce no nomination at all."""
    items = [
        _item("dead1", "hackernews", "Silent post nobody engaged with",
              engagement={"points": 0, "num_comments": 0}),
    ]
    nominations = pipeline.nominate_topics(
        _bundle(items), _query_plan("AI", ["hackernews"]), _plan("AI", ["hackernews"]),
        to_date="2026-07-10", limit=8,
    )
    assert nominations == []


def test_nomination_carries_leader_summary_and_items():
    items = [
        _item("s1", "hackernews", "Rust rewrite of the Linux scheduler",
              engagement={"points": 700, "num_comments": 250}),
    ]
    nominations = pipeline.nominate_topics(
        _bundle(items), _query_plan("Linux", ["hackernews"]), _plan("Linux", ["hackernews"]),
        to_date="2026-07-10", limit=8,
    )
    assert nominations
    top = nominations[0]
    assert top.items and top.items[0].item_id == "s1"
    assert top.summary
