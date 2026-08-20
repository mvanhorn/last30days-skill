"""U3 - enrichment stage: full pipeline pass per nominated topic.

The fault-tolerance contract is the point of these tests: one topic failing or
running past the batch budget must never sink the others, and the batch never
raises. The U4 resume section pins the leg-2 tier parameterization: the
one-shot path keeps quick/240/3 while a deep-tier resume upgrades to
default/450/4 - both ways, so neither tier can leak into the other.
"""

import inspect
import time
from unittest import mock

from lib import discovery_handoff, pipeline, schema


def _nomination(name: str, score: float = 50.0) -> pipeline.Nomination:
    return pipeline.Nomination(name=name, seed_score=score, items=[], summary=name)


def _report(topic: str) -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="none",
            planner_model="deterministic",
            rerank_model="deterministic",
        ),
        query_plan=schema.QueryPlan(
            intent="factual",
            freshness_mode="balanced_recent",
            cluster_mode="none",
            raw_topic=topic,
            subqueries=[],
            source_weights={},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={},
        errors_by_source={},
    )


def test_enrich_all_success_preserves_order():
    nominations = [_nomination("Topic A"), _nomination("Topic B"), _nomination("Topic C")]

    def fake_run(*, topic, **_kwargs):
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(nominations, config={})

    assert [entry.nomination.name for entry in enriched] == ["Topic A", "Topic B", "Topic C"]
    assert all(entry.report is not None for entry in enriched)
    assert all(entry.error is None for entry in enriched)


def test_enrich_one_failure_does_not_sink_the_batch():
    nominations = [_nomination("Good"), _nomination("Bad"), _nomination("Also good")]

    def fake_run(*, topic, **_kwargs):
        if topic == "Bad":
            raise RuntimeError("upstream exploded")
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(nominations, config={})

    by_name = {entry.nomination.name: entry for entry in enriched}
    assert by_name["Good"].report is not None
    assert by_name["Also good"].report is not None
    assert by_name["Bad"].report is None
    assert "upstream exploded" in (by_name["Bad"].error or "")


def test_enrich_budget_expiry_drops_slow_topic_to_nomination_only():
    nominations = [_nomination("Fast"), _nomination("Slow")]

    def fake_run(*, topic, **_kwargs):
        if topic == "Slow":
            time.sleep(5)
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(
            nominations, config={}, budget_seconds=1.0, max_workers=2,
        )

    by_name = {entry.nomination.name: entry for entry in enriched}
    assert by_name["Fast"].report is not None
    assert by_name["Slow"].report is None
    assert "budget" in (by_name["Slow"].error or "")


def test_enrich_runs_as_internal_subrun():
    """Sub-runs must use the internal_subrun lane (no library context, capped
    inner workers) exactly like comparison-mode entity passes."""
    seen: dict[str, object] = {}

    def fake_run(*, topic, **kwargs):
        seen.update(kwargs)
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        pipeline.enrich_nominations([_nomination("One")], config={})

    assert seen.get("internal_subrun") is True


def test_enrich_empty_nominations_returns_empty():
    assert pipeline.enrich_nominations([], config={}) == []


def test_enrich_workers_are_daemon_threads():
    """Stragglers must not block interpreter exit: every enrichment worker runs
    as a daemon thread (the P1 from PR #816 review - a hung sub-run kept the
    process alive past the wall-clock budget with non-daemon executor threads)."""
    import threading

    daemon_flags: list[bool] = []

    def fake_run(*, topic, **_kwargs):
        daemon_flags.append(threading.current_thread().daemon)
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        pipeline.enrich_nominations([_nomination("One"), _nomination("Two")], config={})

    assert daemon_flags and all(daemon_flags)


def test_enrich_concurrency_capped_by_semaphore():
    """Never more than max_workers sub-runs in flight."""
    import threading

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_run(*, topic, **_kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return _report(topic)

    nominations = [_nomination(f"T{i}") for i in range(6)]
    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(nominations, config={}, max_workers=2)

    assert state["peak"] <= 2
    assert all(entry.report is not None for entry in enriched)


def test_enrichment_reaches_all_sources_by_default():
    """No user source filter -> sub-runs get requested_sources=None, which is
    what lets Techmeme, arXiv, YouTube, and Polymarket reach discovery despite
    having no river feed of their own."""
    seen: dict[str, object] = {}

    def fake_run(*, topic, **kwargs):
        seen.update(kwargs)
        return _report(topic)

    raw = {
        "id": "seed1",
        "title": "AI agents breakthrough sweeps the industry",
        "url": "https://example.com/seed1",
        "hn_url": "https://news.ycombinator.com/item?id=1",
        "author": "example",
        "date": "2026-07-09",
        "engagement": {"points": 900, "comments": 400},
        "relevance": 0.9,
    }
    with mock.patch.object(pipeline, "available_sources", return_value=["hackernews"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", return_value=([raw], None)), \
         mock.patch.object(pipeline, "run", side_effect=fake_run):
        pipeline.run_discover(
            domain="AI agents", config={}, as_of_date="2026-07-10", enrich=True,
        )

    assert seen.get("internal_subrun") is True
    assert seen.get("requested_sources") is None


def test_user_source_boundary_holds_through_enrichment():
    """--search reddit must bound the sub-runs too, not just the sweep."""
    seen: dict[str, object] = {}

    def fake_run(*, topic, **kwargs):
        seen.update(kwargs)
        return _report(topic)

    raw = {
        "id": "seed1",
        "title": "AI agents breakthrough sweeps the industry",
        "url": "https://reddit.com/r/x/seed1",
        "subreddit": "example",
        "date": "2026-07-09",
        "engagement": {"score": 900, "num_comments": 400},
        "selftext": "AI agents breakthrough",
        "relevance": 0.9,
    }
    with mock.patch.object(pipeline, "available_sources", return_value=["reddit"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", return_value=([raw], None)), \
         mock.patch.object(pipeline, "run", side_effect=fake_run):
        pipeline.run_discover(
            domain="AI agents", config={}, as_of_date="2026-07-10",
            requested_sources=["reddit"], enrich=True,
            enrich_requested_sources=["reddit"],
        )

    assert seen.get("requested_sources") == ["reddit"]


# --- U4 leg 2: resume enrichment tiers -----------------------------------------
# The resume leg parameterizes enrich_nominations rather than editing the
# one-shot constants: deep-tier bundles get default/450(config)/4, shallow-tier
# bundles and the one-shot --discover path keep quick/240/3. Pinned BOTH ways
# so neither tier can leak into the other.


def _seed_item(
    item_id: str,
    source: str,
    title: str,
    *,
    points: int = 300,
    published_at: str = "2026-07-09",
) -> schema.SourceItem:
    engagement = (
        {"score": points, "num_comments": 40}
        if source == "reddit"
        else {"points": points, "comments": 40}
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
    boundary: list[str] | None = None,
    lookback_days: int = 30,
) -> discovery_handoff.NominationsBundle:
    return discovery_handoff.NominationsBundle(
        schema_version=schema.DISCOVERY_NOMINATIONS_SCHEMA_VERSION,
        bundle_id="cafef00dcafef00d",
        generated_at="2026-07-10T00:00:00Z",
        from_date="2026-06-10",
        to_date="2026-07-10",
        domain="AI agents",
        tier=tier,
        enrichment_source_boundary=boundary,
        requested_sources=None,
        lookback_days=lookback_days,
        nominations=rows,
    )


def _enrich_spy(seen: dict):
    def spy(nominations, **kwargs):
        seen["nominations"] = list(nominations)
        seen.update(kwargs)
        return [pipeline.EnrichedTopic(nomination=n) for n in nominations]
    return spy


def test_one_shot_discover_enrichment_stays_quick_tier():
    """Tier-leak pin, direction 1: the one-shot --discover path must keep the
    quick/240/3 enrichment constants untouched by the resume-leg tiers."""
    seen: dict = {}
    raw = {
        "id": "seed1",
        "title": "AI agents breakthrough sweeps the industry",
        "url": "https://example.com/seed1",
        "hn_url": "https://news.ycombinator.com/item?id=1",
        "author": "example",
        "date": "2026-07-09",
        "engagement": {"points": 900, "comments": 400},
        "relevance": 0.9,
    }
    with mock.patch.object(pipeline, "available_sources", return_value=["hackernews"]), \
         mock.patch.object(pipeline, "_fetch_discovery_source", return_value=([raw], None)), \
         mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover(
            domain="AI agents", config={}, as_of_date="2026-07-10", enrich=True,
        )

    assert seen.get("depth", pipeline.ENRICH_DEPTH) == "quick"
    assert seen.get("max_workers", pipeline.ENRICH_MAX_WORKERS) == 3
    assert seen.get("budget_seconds", pipeline.ENRICH_BUDGET_SECONDS) == 240.0
    assert (pipeline.ENRICH_DEPTH, pipeline.ENRICH_MAX_WORKERS,
            pipeline.ENRICH_BUDGET_SECONDS) == ("quick", 3, 240.0)


def test_resume_deep_tier_uses_default_depth_budget_and_workers():
    """Tier-leak pin, direction 2: a deep-tier bundle upgrades sub-runs to
    default/450(default)/4 and scores against the bundle's window/boundary."""
    seen: dict = {}
    bundle = _resume_bundle(
        [_bundle_row("n1", "Topic A", [_seed_item("a1", "hackernews", "Topic A")])],
        tier="deep", boundary=["reddit"], lookback_days=7,
    )
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(bundle, {}, config={})

    assert seen["depth"] == "default"
    assert seen["budget_seconds"] == 450.0
    assert seen["max_workers"] == 4
    assert seen["as_of_date"] == "2026-07-10"
    assert seen["lookback_days"] == 7
    assert seen["requested_sources"] == ["reddit"]


def test_resume_shallow_tier_keeps_quick_constants():
    """A shallow-tier bundle enriches with today's one-shot quick constants."""
    seen: dict = {}
    bundle = _resume_bundle(
        [_bundle_row("n1", "Topic A", [_seed_item("a1", "hackernews", "Topic A")])],
        tier="shallow",
    )
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(bundle, {}, config={})

    assert seen["depth"] == pipeline.ENRICH_DEPTH == "quick"
    assert seen["budget_seconds"] == pipeline.ENRICH_BUDGET_SECONDS == 240.0
    assert seen["max_workers"] == pipeline.ENRICH_MAX_WORKERS == 3


def test_resume_budget_knob_reads_config_only_never_os_environ(monkeypatch):
    """LAST30DAYS_ENRICH_BUDGET_SECONDS comes from the RESOLVED config dict;
    a bare os.environ value that never went through env.get_config is
    invisible to the pipeline (no bare os.environ reads in lib/)."""
    monkeypatch.setenv("LAST30DAYS_ENRICH_BUDGET_SECONDS", "77")
    seen: dict = {}
    bundle = _resume_bundle(
        [_bundle_row("n1", "Topic A", [_seed_item("a1", "hackernews", "Topic A")])],
    )
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(bundle, {}, config={})
    assert seen["budget_seconds"] == 450.0

    seen.clear()
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(
            bundle, {}, config={"LAST30DAYS_ENRICH_BUDGET_SECONDS": "333"},
        )
    assert seen["budget_seconds"] == 333.0


def test_resume_budget_env_file_seam(tmp_path, monkeypatch):
    """The knob rides the same .env-file seam as every other config value
    (mirrors the queue-toggle seam tests in test_discover_mode.py)."""
    from lib import env

    monkeypatch.delenv("LAST30DAYS_ENRICH_BUDGET_SECONDS", raising=False)
    env_file = tmp_path / "config.env"
    env_file.write_text("LAST30DAYS_ENRICH_BUDGET_SECONDS=333\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", env_file)
    monkeypatch.chdir(tmp_path)
    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "_load_pass", return_value={}):
        config = env.get_config()
    assert config["LAST30DAYS_ENRICH_BUDGET_SECONDS"] == "333"

    seen: dict = {}
    bundle = _resume_bundle(
        [_bundle_row("n1", "Topic A", [_seed_item("a1", "hackernews", "Topic A")])],
    )
    with mock.patch.object(pipeline, "enrich_nominations", side_effect=_enrich_spy(seen)):
        pipeline.run_discover_resume(bundle, {}, config=config)
    assert seen["budget_seconds"] == 333.0


def test_resume_budget_parser_rejects_garbage_and_nonpositive():
    default = pipeline.RESUME_DEEP_ENRICH_BUDGET_SECONDS
    assert default == 450.0
    assert pipeline._resume_enrich_budget_seconds({}) == default
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": ""}) == default
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": "not-a-number"}) == default
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": "0"}) == default
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": "-5"}) == default
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": "600"}) == 600.0
    assert pipeline._resume_enrich_budget_seconds(
        {"LAST30DAYS_ENRICH_BUDGET_SECONDS": 300}) == 300.0


def test_enrich_budget_expiry_downgrades_at_deep_tier():
    """The wall-clock downgrade contract holds unchanged under the deep-tier
    parameters (default depth, 4 workers): stragglers become nomination-only."""
    nominations = [_nomination("Fast"), _nomination("Slow")]
    depths: list[str] = []

    def fake_run(*, topic, depth, **_kwargs):
        depths.append(depth)
        if topic == "Slow":
            time.sleep(5)
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(
            nominations, config={}, depth="default",
            budget_seconds=1.0, max_workers=4,
        )

    by_name = {entry.nomination.name: entry for entry in enriched}
    assert by_name["Fast"].report is not None
    assert by_name["Slow"].report is None
    assert "budget" in (by_name["Slow"].error or "")
    assert depths and all(depth == "default" for depth in depths)


def test_enrich_workers_are_daemon_threads_at_deep_tier():
    """Daemon-thread containment holds at the deep tier too: a hung default-
    depth sub-run must never block interpreter exit."""
    import threading

    daemon_flags: list[bool] = []

    def fake_run(*, topic, **_kwargs):
        daemon_flags.append(threading.current_thread().daemon)
        return _report(topic)

    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        pipeline.enrich_nominations(
            [_nomination("One"), _nomination("Two")], config={},
            depth="default", max_workers=4,
        )

    assert daemon_flags and all(daemon_flags)


def test_enrich_concurrency_capped_at_deep_tier():
    """Never more than the deep tier's 4 sub-runs in flight."""
    import threading

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_run(*, topic, **_kwargs):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return _report(topic)

    nominations = [_nomination(f"T{i}") for i in range(9)]
    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        enriched = pipeline.enrich_nominations(
            nominations, config={}, depth="default", max_workers=4,
        )

    assert state["peak"] <= 4
    assert all(entry.report is not None for entry in enriched)


def test_enrichment_path_never_uses_thread_pool_executor():
    """Executor threads are non-daemon and joined at shutdown, defeating the
    wall-clock budget (docs/solutions/logic-errors/non-daemon-executor-threads-
    defeat-wall-clock-budget.md). The enrichment batch must stay on the
    daemon-thread + Semaphore + queue pattern."""
    source = inspect.getsource(pipeline.enrich_nominations)
    # The comment naming the anti-pattern is fine; constructing one is not.
    assert "ThreadPoolExecutor(" not in source
    assert "daemon=True" in source
    assert "Semaphore" in source


def test_host_judged_name_becomes_enrichment_sub_run_topic():
    """Relocated from the retired engine-judge suite, retargeted to the
    judgments-file path: the host's applied name IS the enrichment sub-run
    topic (the nomination name is what run() researches)."""
    seen: dict = {}

    def fake_run(*, topic, **kwargs):
        seen["topic"] = topic
        seen.update(kwargs)
        return _report(topic)

    bundle = _resume_bundle([
        _bundle_row(
            "n1",
            "Google is updating Gemma 4 chat templates",
            [_seed_item("hn1", "hackernews",
                        "Google is updating Gemma 4 chat templates",
                        points=900)],
        ),
    ])
    judgments = {
        "n1": discovery_handoff.HostJudgment(
            name="Gemma 4 Flash Attention", junk=False, worthiness=88,
        ),
    }
    with mock.patch.object(pipeline, "run", side_effect=fake_run):
        result = pipeline.run_discover_resume(bundle, judgments, config={})

    assert seen["topic"] == "Gemma 4 Flash Attention"
    assert seen.get("internal_subrun") is True
    assert [topic.name for topic in result.report.topics] == ["Gemma 4 Flash Attention"]
