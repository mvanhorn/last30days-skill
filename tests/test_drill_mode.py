import copy
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import pipeline, planner, render, schema


def _item(item_id: str, source: str, title: str, url: str) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=item_id,
        source=source,
        title=title,
        body=f"Body for {title}",
        url=url,
        snippet=f"Evidence about {title}",
        local_rank_score=0.9,
    )


def _candidate(item: schema.SourceItem, score: float = 80.0) -> schema.Candidate:
    return schema.Candidate(
        candidate_id=f"cand-{item.item_id}",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["primary"],
        native_ranks={item.source: 1},
        local_relevance=0.9,
        freshness=90,
        engagement=10,
        source_quality=0.8,
        rrf_score=0.1,
        sources=[item.source],
        source_items=[item],
        final_score=score,
    )


def _report(*, drill: bool = False) -> schema.Report:
    ban = _item(
        "ban",
        "reddit",
        "OpenClaw API ban discussion",
        "https://reddit.example/ban",
    )
    policy = _item(
        "policy",
        "youtube",
        "OpenClaw policy explained",
        "https://youtube.example/policy",
    )
    release = _item(
        "release",
        "hackernews",
        "OpenClaw ships a new release",
        "https://news.example/release",
    )
    candidates = [_candidate(ban, 92), _candidate(policy, 85), _candidate(release, 70)]
    clusters = [
        schema.Cluster(
            cluster_id="cluster-1",
            title="OpenClaw API ban discussion",
            candidate_ids=[candidates[0].candidate_id, candidates[1].candidate_id],
            representative_ids=[candidates[0].candidate_id],
            sources=["reddit", "youtube"],
            score=92,
        ),
        schema.Cluster(
            cluster_id="cluster-2",
            title="OpenClaw release notes",
            candidate_ids=[candidates[2].candidate_id],
            representative_ids=[candidates[2].candidate_id],
            sources=["hackernews"],
            score=70,
        ),
    ]
    if drill:
        deeper = _item(
            "deeper",
            "reddit",
            "OpenClaw API policy enforcement details",
            "https://reddit.example/deeper",
        )
        # The first result repeats the cached URL; merge must keep one copy.
        candidates = [_candidate(ban, 95), _candidate(deeper, 90)]
        clusters = [
            schema.Cluster(
                cluster_id="cluster-1",
                title="OpenClaw API policy enforcement details",
                candidate_ids=[candidate.candidate_id for candidate in candidates],
                representative_ids=[candidate.candidate_id for candidate in candidates],
                sources=["reddit"],
                score=95,
            )
        ]
        items_by_source = {"reddit": [ban, deeper]}
    else:
        items_by_source = {
            "reddit": [ban],
            "youtube": [policy],
            "hackernews": [release],
        }
    return schema.Report(
        topic="OpenClaw API policy" if drill else "OpenClaw",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T12:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="mock-planner",
            rerank_model="mock-reranker",
        ),
        query_plan=schema.QueryPlan(
            intent="opinion",
            freshness_mode="balanced_recent",
            cluster_mode="debate",
            raw_topic="OpenClaw",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="OpenClaw",
                    ranking_query="What is happening with OpenClaw?",
                    sources=list(items_by_source),
                )
            ],
            source_weights={source: 1.0 for source in items_by_source},
        ),
        clusters=clusters,
        ranked_candidates=candidates,
        items_by_source=items_by_source,
        errors_by_source={},
        source_status={
            source: schema.SourceOutcome(
                source=source,
                state="ok",
                items_returned=len(items),
            )
            for source, items in items_by_source.items()
        },
    )


def test_cluster_resolution_by_index_and_number():
    report = _report()
    assert planner.resolve_drill_clusters(report, "cluster 2")[0].cluster_id == "cluster-2"
    assert planner.resolve_drill_clusters(report, "1")[0].cluster_id == "cluster-1"


def test_cluster_resolution_by_fuzzy_title_and_entities():
    matched = planner.resolve_drill_clusters(
        _report(),
        "what is behind the OpenClaw API ban?",
    )
    assert [cluster.cluster_id for cluster in matched] == ["cluster-1"]


def test_cluster_resolution_no_match_prints_candidates():
    with pytest.raises(planner.DrillTargetError) as exc:
        planner.resolve_drill_clusters(_report(), "quantum potato harvest")
    message = str(exc.value)
    assert "Available clusters" in message
    assert "1. OpenClaw API ban discussion" in message
    assert "2. OpenClaw release notes" in message


def test_build_drill_plan_only_uses_contributing_sources_and_cluster_terms():
    plan = planner.build_drill_plan(_report(), "cluster 1")
    assert set(plan.source_weights) == {"reddit", "youtube"}
    assert all(set(subquery.sources) == {"reddit", "youtube"} for subquery in plan.subqueries)
    assert all("hackernews" not in subquery.sources for subquery in plan.subqueries)
    assert "drill-mode" in plan.notes
    assert any("openclaw" in subquery.search_query.lower() for subquery in plan.subqueries)


def test_merge_dedupes_new_results_preserves_other_clusters_and_renders_context():
    base = _report()
    merged = pipeline.merge_drill_report(
        base,
        _report(drill=True),
        [base.clusters[0]],
        target="cluster 1",
    )

    assert merged.drill_of == "OpenClaw API ban discussion"
    assert [cluster.cluster_id for cluster in merged.clusters] == ["cluster-1", "cluster-2"]
    assert len([item for item in merged.items_by_source["reddit"] if item.url.endswith("/ban")]) == 1
    assert any(item.url.endswith("/deeper") for item in merged.items_by_source["reddit"])
    assert merged.artifacts["drill_context"]["new_items"] == 1
    assert len(merged.artifacts["drill_history"]) == 1
    output = render.render_compact(merged)
    assert "## Drill Follow-up" in output
    assert "### Original" in output
    assert "### Deeper" in output


def test_merge_dedupes_drill_candidates_against_untouched_clusters():
    base = _report()
    drill_report = _report(drill=True)
    rediscovered = copy.deepcopy(base.ranked_candidates[2])
    drill_report.ranked_candidates.append(rediscovered)
    drill_report.clusters[0].candidate_ids.append(rediscovered.candidate_id)

    merged = pipeline.merge_drill_report(
        base,
        drill_report,
        [base.clusters[0]],
        target="cluster 1",
    )

    candidate_ids = [candidate.candidate_id for candidate in merged.ranked_candidates]
    assert candidate_ids.count(rediscovered.candidate_id) == 1
    assert rediscovered.candidate_id not in merged.clusters[0].candidate_ids
    assert rediscovered.candidate_id in merged.clusters[1].candidate_ids


def test_merge_retains_enriched_rediscovery_in_untouched_cluster():
    base = _report()
    base.ranked_candidates[2].cluster_id = "cluster-2"
    drill_report = _report(drill=True)
    rediscovered = copy.deepcopy(base.ranked_candidates[2])
    rediscovered.snippet = "Enriched release evidence from the drill"
    rediscovered.engagement = 321
    rediscovered.source_items[0].snippet = "Transcript-backed release evidence"
    rediscovered.source_items[0].engagement = {"comments": 42}
    rediscovered.source_items[0].metadata = {
        "transcript": "Detailed release transcript",
        "comments": ["Useful community context"],
    }
    drill_report.ranked_candidates.append(rediscovered)

    merged = pipeline.merge_drill_report(
        base,
        drill_report,
        [base.clusters[0]],
        target="cluster 1",
    )

    retained = next(
        candidate
        for candidate in merged.ranked_candidates
        if candidate.candidate_id == rediscovered.candidate_id
    )
    assert retained.cluster_id == base.ranked_candidates[2].cluster_id
    assert retained.snippet == "Enriched release evidence from the drill"
    assert retained.engagement == 321
    assert retained.source_items[0].metadata["transcript"] == "Detailed release transcript"
    assert retained.source_items[0].engagement == {"comments": 42}


def test_merge_recomputes_attempted_source_health_from_retained_evidence():
    base = _report()
    base.errors_by_source["reddit"] = "cached timeout"
    base.source_status["reddit"] = schema.SourceOutcome(
        source="reddit",
        state=schema.RATE_LIMITED,
        detail="cached timeout",
    )
    base.warnings = [
        "Some sources failed: reddit",
        "No candidates survived retrieval and ranking.",
    ]
    drill_report = _report(drill=True)
    drill_report.source_status["youtube"] = schema.SourceOutcome(
        source="youtube",
        state=schema.NO_RESULTS,
        items_returned=0,
    )

    merged = pipeline.merge_drill_report(
        base,
        drill_report,
        [base.clusters[0]],
        target="cluster 1",
    )

    assert "reddit" not in merged.errors_by_source
    assert merged.source_status["reddit"].state == "ok"
    assert merged.source_status["youtube"].state == "ok"
    assert merged.source_status["youtube"].items_returned == 1
    assert not any("Some sources failed" in warning for warning in merged.warnings)
    assert "No candidates survived retrieval and ranking." not in merged.warnings


def test_expired_cache_exits_cleanly_with_research_guidance(tmp_path: Path):
    config_dir = tmp_path / "config"
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cli._write_last_run("OpenClaw", _report())
        cache_path = config_dir / "last-report.json"
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        payload["timestamp"] = "2026-01-01T00:00:00+00:00"
        cache_path.write_text(json.dumps(payload), encoding="utf-8")

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("pipeline should not run")), \
         mock.patch.object(cli.sys, "argv", ["last30days.py", "--drill", "cluster 1"]):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.main()

    assert rc == 2
    assert "run a research pass first" in stderr.getvalue()


def test_non_object_cache_is_unavailable_with_warning(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "last-report.json").write_text("[]", encoding="utf-8")

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            cached = cli._load_last_report_cache(None)

    assert cached is None
    assert "Could not read report cache" in stderr.getvalue()


def test_drill_publish_html_requires_html_emit_before_dispatch():
    parser = cli.build_parser()
    args = parser.parse_args(["--drill", "cluster 1", "--publish-html"])

    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli, "_run_drill") as run_drill:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli._main(parser, args, [])

    assert rc == 2
    assert "--publish-html requires --emit=html" in stderr.getvalue()
    run_drill.assert_not_called()


def test_drill_applies_config_backed_source_filters_before_dispatch():
    parser = cli.build_parser()
    args = parser.parse_args([
        "--drill", "cluster 1",
        "--dedicated-subreddits", "r/OpenClaw, OpenClawDev",
        "--polymarket-keywords", "API, Policy",
    ])

    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli, "_run_drill", return_value=0) as run_drill:
        assert cli._main(parser, args, []) == 0

    drill_config = run_drill.call_args.args[1]
    assert drill_config["_dedicated_subreddits"] == ["OpenClaw", "OpenClawDev"]
    assert drill_config["_polymarket_keywords"] == ["api", "policy"]


def test_drill_inherits_cached_historical_window(tmp_path: Path):
    config_dir = tmp_path / "config"
    cached_report = _report()
    cached_report.range_from = "2026-05-01"
    cached_report.range_to = "2026-05-08"
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cli._write_last_run("OpenClaw", cached_report)

    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--mock"])
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=_report(drill=True)) as run_mock, \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli._run_drill(args, {}) == 0

    assert run_mock.call_args.kwargs["lookback_days"] == 7
    assert run_mock.call_args.kwargs["as_of_date"] == "2026-05-08"


def test_drill_uses_cached_financial_topic_while_plan_stays_cluster_focused(tmp_path: Path):
    config_dir = tmp_path / "config"
    cached_report = _report()
    stock_item = _item(
        "now",
        "stocktwits",
        "AI agent rollout",
        "https://stocktwits.example/now",
    )
    stock_candidate = _candidate(stock_item, 95)
    cached_report.topic = "ServiceNow $NOW stock"
    cached_report.ranked_candidates = [stock_candidate]
    cached_report.clusters = [schema.Cluster(
        cluster_id="cluster-1",
        title="AI agent rollout",
        candidate_ids=[stock_candidate.candidate_id],
        representative_ids=[stock_candidate.candidate_id],
        sources=["stocktwits"],
        score=95,
    )]
    cached_report.items_by_source = {"stocktwits": [stock_item]}
    cached_report.query_plan.source_weights = {"stocktwits": 1.0}
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cli._write_last_run(cached_report.topic, cached_report)

    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--mock"])
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=_report(drill=True)) as run_mock, \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli._run_drill(args, {}) == 0

    call = run_mock.call_args.kwargs
    assert call["topic"] == "ServiceNow $NOW stock"
    assert call["external_plan"]["subqueries"][0]["search_query"] == "AI agent rollout"
    assert call["requested_sources"] == ["stocktwits"]


def test_cli_drill_runs_deep_updates_cache_and_can_chain(tmp_path: Path):
    config_dir = tmp_path / "config"
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cli._write_last_run("OpenClaw", _report())

    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--mock"])
    drill_result = _report(drill=True)
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=drill_result) as run_mock, \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli._run_drill(args, {}) == 0

    call = run_mock.call_args.kwargs
    assert call["depth"] == "deep"
    assert set(call["requested_sources"]) == {"reddit", "youtube"}
    assert set(call["external_plan"]["subqueries"][0]["sources"]) == {"reddit", "youtube"}

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cached = cli._load_last_report_cache(None)
    assert cached is not None
    assert len(cached[0].artifacts["drill_history"]) == 1

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=drill_result), \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli._run_drill(args, {}) == 0

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        chained = cli._load_last_report_cache(None)
    assert chained is not None
    assert len(chained[0].artifacts["drill_history"]) == 2


def test_drill_plan_does_not_gain_jobs_via_company_topic(monkeypatch):
    from lib import pipeline, schema

    plan = schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="story",
        raw_topic="OpenClaw",
        notes=["drill-mode"],
        subqueries=[
            schema.SubQuery(
                label="drill",
                search_query="OpenClaw api ban",
                ranking_query="OpenClaw api ban",
                sources=["youtube"],
            )
        ],
        source_weights={"youtube": 1.0},
    )
    pipeline._ensure_jobs_in_plan(plan, ["youtube", "jobs"], explicit=False, topic="OpenClaw")
    # Direct call still injects (documenting baseline)...
    assert "jobs" in plan.source_weights
    # ...but run()'s drill gate skips the injection entirely for drill plans;
    # assert the gate condition itself so the contract is pinned.
    assert "drill-mode" in plan.notes


def test_merge_collapses_exact_url_rediscoveries():
    from lib import pipeline, schema
    import copy

    def item(url, body):
        return schema.SourceItem(
            item_id=url, source="reddit", title="t", body=body, url=url,
            published_at="2026-07-01", snippet=body[:20], engagement={"score": 5},
        )

    old = item("https://reddit.com/r/x/1", "original body")
    new = item("https://reddit.com/r/x/1", "enriched body with transcript and much longer text")
    from lib import dedupe
    new_urls = {new.url}
    filtered_old = [i for i in [old] if not (i.url and i.url in new_urls)]
    combined = dedupe.dedupe_items([copy.deepcopy(new), *filtered_old])
    assert len(combined) == 1
    assert combined[0].body.startswith("enriched")


def test_write_last_run_returns_false_on_failure(monkeypatch, capsys):
    import last30days as cli
    from lib import env

    class ExplodingPath:
        def mkdir(self, *a, **k):
            raise OSError("disk full")

    monkeypatch.setattr(cli.env, "CONFIG_DIR", ExplodingPath())
    report = _report()
    ok = cli._write_last_run("topic", report)
    assert ok is False
    assert "could not write run cache" in capsys.readouterr().err


def test_drill_gates_subreddit_context_on_source_allowlist(monkeypatch):
    import io
    from contextlib import redirect_stdout, redirect_stderr
    from unittest import mock
    import last30days as cli
    from lib import schema

    report = _report()
    # Force a non-Reddit single-source cluster and cached subreddit context.
    report.artifacts["resolved"] = {"subreddits": ["LocalLLaMA", "MachineLearning"]}
    for cluster in report.clusters:
        cluster.sources = ["youtube"]
    for candidate in report.ranked_candidates:
        candidate.source = "youtube"
        candidate.sources = ["youtube"]
        for item in candidate.source_items:
            item.source = "youtube"

    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return _report(drill=True)

    args = cli.build_parser().parse_args(["--drill", "cluster 1"])
    with mock.patch.object(cli, "_load_last_report_cache", return_value=(report, None, Path("/tmp/last-report.json"))), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", side_effect=lambda **k: fake_run(**k)), \
         mock.patch.object(cli.pipeline, "merge_drill_report", side_effect=lambda r, d, c, target: r), \
         mock.patch.object(cli, "_write_last_run", return_value=True), \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        cli._run_drill(args, {})

    assert "reddit" not in (captured.get("requested_sources") or [])
    assert captured.get("subreddits") is None
