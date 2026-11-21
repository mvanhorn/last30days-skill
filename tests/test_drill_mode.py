import copy
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import library, library_index, pipeline, planner, render, schema


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


def test_build_drill_plan_keeps_the_polymarket_hints():
    report = _report()
    hints = {"kind": "team", "aliases": [], "related": [{"name": "Yaxel Lendeborg", "relation": "player"}]}
    report.query_plan.polymarket_hints = hints

    plan = planner.build_drill_plan(report, "cluster 1")

    assert plan.polymarket_hints == hints
    assert plan.polymarket_hints is not hints


def _with_local_notes(report: schema.Report) -> schema.Candidate:
    note = _item("notes", "corpus", "Local packet tracing notes", "corpus://notes")
    private = _candidate(note)
    cluster = report.clusters[0]
    report.ranked_candidates.insert(0, private)
    report.items_by_source["corpus"] = [note]
    cluster.candidate_ids.insert(0, private.candidate_id)
    cluster.representative_ids = [private.candidate_id]
    cluster.title = note.title
    cluster.sources.append("corpus")
    return private


def test_mixed_drill_uses_a_public_title_when_the_representative_is_local():
    report = _report()
    private = _with_local_notes(report)

    plan = planner.build_drill_plan(report, "cluster 1")

    assert set(plan.source_weights) == {"reddit", "youtube", "corpus"}
    assert plan.subqueries[0].search_query == report.items_by_source["reddit"][0].title
    assert all("packet tracing" not in query.ranking_query.lower() for query in plan.subqueries)
    assert all(private.title not in query.search_query for query in plan.subqueries)


def test_mixed_drill_recovers_public_evidence_from_a_fused_candidate():
    report = _report()
    private = _with_local_notes(report)
    public = report.items_by_source["reddit"][0]
    private.source_items.append(public)
    private.sources.append(public.source)
    report.clusters[0].candidate_ids = [private.candidate_id]

    plan = planner.build_drill_plan(report, "cluster 1")

    assert set(plan.source_weights) == {"reddit", "corpus"}
    assert plan.subqueries[0].search_query == public.title
    assert all(private.title not in query.ranking_query for query in plan.subqueries)


def test_corpus_only_drill_researches_local_files_with_remote_options_available(tmp_path, monkeypatch):
    report = _report()
    private = _with_local_notes(report)
    report.clusters[0].candidate_ids = [private.candidate_id]
    report.clusters[0].sources = ["corpus"]
    plan = planner.build_drill_plan(report, "cluster 1")
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "Local packet tracing notes.md").write_text(
        "OpenClaw local packet tracing notes explain how to verify packet flows.",
        encoding="utf-8",
    )
    forbid_remote = mock.Mock(side_effect=AssertionError("remote configuration should be unused"))
    monkeypatch.setattr(pipeline.providers, "resolve_runtime", forbid_remote)
    monkeypatch.setattr(pipeline, "available_sources", forbid_remote)

    result = pipeline.run(
        topic=report.topic,
        config={"_drill_mode": True},
        depth="deep",
        requested_sources=list(plan.source_weights),
        external_plan=schema.to_dict(plan),
        corpus_dirs=[str(notes)],
        corpus_all_time=True,
        web_backend="keyless",
    )

    assert plan.subqueries[0].search_query == private.title
    assert set(result.items_by_source) == {"corpus"}
    assert [item.title for item in result.items_by_source["corpus"]] == [private.title]
    assert result.provider_runtime.reasoning_provider == "local"
    forbid_remote.assert_not_called()


@pytest.mark.parametrize("keep_local_candidate", [True, False])
def test_legacy_corpus_drill_is_private_after_a_cache_round_trip(monkeypatch, capsys, keep_local_candidate):
    report = _report()
    private = _with_local_notes(report)
    merged = pipeline.merge_drill_report(report, _report(drill=True), [report.clusters[0]], target="cluster 1")
    merged.query_plan.subqueries.append(schema.SubQuery(
        label="legacy", search_query=private.title, ranking_query=private.snippet, sources=["reddit", "corpus"],
    ))
    if not keep_local_candidate:
        merged.ranked_candidates = [
            candidate for candidate in merged.ranked_candidates
            if candidate.candidate_id != private.candidate_id
        ]
        merged.clusters[0].candidate_ids.remove(private.candidate_id)
        merged.clusters[0].representative_ids.remove(private.candidate_id)
    stored = schema.report_from_dict(schema.to_dict(merged))
    stored.source_status.pop("corpus", None)
    assert stored.artifacts["drill_context"]["original_summary"] == private.snippet

    public = schema.without_sources(stored, {"corpus"})
    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--emit=html", "--publish-html"])
    publish = mock.Mock(return_value={"url": "https://example.test/brief"})
    monkeypatch.setattr(cli, "publish_rendered_html", publish)
    assert cli._render_save_and_print(args, stored, None, None, {}) == 0
    local = capsys.readouterr().out
    published = publish.call_args.args[0]

    assert private.snippet in local
    compact = render.render_compact(stored)
    assert f"{render.PRIVATE_CORPUS_START}\n## Drill Follow-up" in compact
    for markdown in (compact, render.render_full(stored)):
        assert f"{render.PUBLIC_CLUSTERS_MARKER}\n## Ranked Evidence Clusters" in markdown
    assert private.title not in published
    assert private.snippet not in published
    assert public.drill_of is None
    assert "drill_context" not in public.artifacts
    assert "drill_history" not in public.artifacts
    assert not public.query_plan.subqueries
    assert public.items_by_source["reddit"]
    assert public.clusters[0].title in {candidate.title for candidate in public.ranked_candidates}
    assert stored.drill_of == private.title


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
    assert "### Original" in library.public_markdown(output)
    public = schema.without_sources(merged, {"corpus"})
    assert public.artifacts["drill_context"] == merged.artifacts["drill_context"]


def test_public_drill_verdict_cannot_export_private_claims_or_corroboration(tmp_path, monkeypatch, capsys):
    base = _report()
    private = _with_local_notes(base)
    private_item = private.source_items[0]
    private_item.url = "https://example.test/saved-local-snapshot"
    report = pipeline.merge_drill_report(base, _report(drill=True), [base.clusters[0]], target="cluster 1")
    public = report.ranked_candidates[0]
    verdict = schema.FreshnessVerdict(
        claim_id="public", candidate_id=public.candidate_id, claim="Publicly corroborated status",
        source=public.source, source_item_id=public.item_id, verdict="contradicted",
        checked_at="2026-07-10", evidence_url="https://example.test/public-evidence",
    )
    report.freshness_verdicts = [
        verdict,
        replace(
            verdict, claim_id="local-copy", evidence_url=private_item.url,
            detail="Local snapshot corroboration", current_value="Local-only status",
        ),
        replace(
            verdict, claim_id="legacy", evidence_url="corpus://older-removed-note",
            evidence_timestamp="2026-07-09", detail="Archived local corroboration",
        ),
        replace(
            verdict, claim_id="private-unsupported", candidate_id=private.candidate_id,
            claim="Private unavailableclaim", source="corpus", source_item_id=private.item_id,
            source_url=private_item.url, evidence_url="", verdict="unsupported",
        ),
        replace(
            verdict, claim_id="private-contradicted", candidate_id=private.candidate_id,
            claim="Private disputedclaim", source="corpus", source_item_id=private.item_id,
            source_url=private_item.url,
        ),
    ]
    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--emit=html", "--publish-html"])
    publish = mock.Mock(return_value={"url": "https://example.test/brief"})
    monkeypatch.setattr(cli, "publish_rendered_html", publish)
    assert cli._render_save_and_print(args, report, None, None, {}) == 0
    local = capsys.readouterr().out
    published = publish.call_args.args[0]

    exported = schema.to_agent_export(report)
    assert [row["claim_id"] for row in exported["freshness_verdicts"]] == ["public"]
    assert "Local snapshot corroboration" in local
    assert "Archived local corroboration" in local
    assert "Local snapshot corroboration" not in published
    assert "Archived local corroboration" not in published
    assert "Publicly corroborated status" in published
    assert "unavailableclaim" in local and "disputedclaim" in local
    assert "unavailableclaim" not in published and "disputedclaim" not in published

    memory = tmp_path / "memory"
    memory.mkdir()
    raw = memory / "openclaw-raw.md"
    raw.write_text(render.render_full(report), encoding="utf-8")
    entry = library._parse_markdown(raw)
    publish_many = mock.Mock(return_value={})
    monkeypatch.setattr("lib.html_publish.publish_html_documents", publish_many)
    monkeypatch.setattr("lib.html_publish.publish_html", mock.Mock(return_value={"url": "https://example.test/index"}))
    feed_args = cli.build_parser().parse_args(["library", "feed", "--save-dir", str(memory), "--publish"])
    assert cli._run_library_feed(feed_args, {}) == 0
    public_brief = publish_many.call_args.args[0][entry.entry_id]
    local_brief = (memory / "briefs" / entry.output_name).read_text(encoding="utf-8")
    assert "Publicly corroborated status" in public_brief
    assert "unavailableclaim" in local_brief and "disputedclaim" in local_brief
    assert "unavailableclaim" not in public_brief and "disputedclaim" not in public_brief

    db_path = tmp_path / "library.db"
    no_store = tmp_path / "no-store.db"
    library_index.sync_library(memory, tmp_path / "no-briefings", db_path=db_path)
    assert library_index.search("corroborated status", db_path=db_path, store_db_path=no_store)
    assert not library_index.search("unavailableclaim", db_path=db_path, store_db_path=no_store)
    assert not library_index.search("disputedclaim", db_path=db_path, store_db_path=no_store)


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


@pytest.mark.parametrize(
    ("envelope_digest", "bound"),
    [(None, False), ("a" * 64, False), ("a" * 64, True)],
)
def test_cli_drill_runs_deep_updates_cache_and_can_chain(tmp_path: Path, envelope_digest, bound):
    config_dir = tmp_path / "config"
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        assert cli._write_last_run("OpenClaw", _report(), x_envelope_sha256=envelope_digest)

    parser = cli.build_parser()
    options = ["--drill", "cluster 1", "--mock"]
    cache_path = config_dir / "last-report.json"
    original_run_id = json.loads(cache_path.read_bytes())["run_id"]
    if bound:
        options.extend(["--followup-topic=openclaw", f"--followup-run={original_run_id}"])
    args = parser.parse_args(options)
    drill_result = _report(drill=True)
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=drill_result) as run_mock, \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
        assert cli._main(parser, args, []) == 0

    call = run_mock.call_args.kwargs
    assert call["depth"] == "deep"
    assert set(call["requested_sources"]) == {"reddit", "youtube"}
    assert set(call["external_plan"]["subqueries"][0]["sources"]) == {"reddit", "youtube"}

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        cached = cli._load_last_report_cache(None, x_envelope_sha256=envelope_digest)
    assert cached is not None
    assert len(cached[0].artifacts["drill_history"]) == 1
    drill_run_id = json.loads(cache_path.read_bytes())["run_id"]
    assert drill_run_id != original_run_id
    assert f"[last30days] Follow-up cache ID: {drill_run_id}\n" in stderr.getvalue()

    if bound:
        args = parser.parse_args([*options[:-1], f"--followup-run={drill_run_id}"])
    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", return_value=drill_result), \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        assert cli._run_drill(args, {}) == 0

    with mock.patch.object(cli.env, "CONFIG_DIR", config_dir):
        chained = cli._load_last_report_cache(None, x_envelope_sha256=envelope_digest)
    assert chained is not None
    assert len(chained[0].artifacts["drill_history"]) == 2


@pytest.mark.parametrize("concurrent_write", ["research", "freshness"])
def test_bound_drill_preserves_a_concurrent_cache_write(tmp_path, monkeypatch, capsys, concurrent_write):
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_show_runtime_ui", lambda *_args, **_kwargs: None)
    report = _report()
    cache_path = tmp_path / "last-report.json"
    summary_path = tmp_path / "last-run.json"
    digest = "a" * 64
    assert cli._write_last_run(report.topic, report, x_envelope_sha256=digest)
    original_cache = cache_path.read_bytes()
    run_id = json.loads(original_cache)["run_id"]
    capsys.readouterr()
    published = []

    def publish_concurrent_result(*_args, **_kwargs):
        if concurrent_write == "research":
            replacement = _report()
            replacement.range_from = "2026-05-01"
            replacement.range_to = "2026-05-08"
            with redirect_stderr(io.StringIO()):
                assert cli._write_last_run(report.topic, replacement, x_envelope_sha256="b" * 64)
        else:
            cached = cli._load_last_report_cache(report.topic, x_envelope_sha256=digest)
            assert cached is not None
            cached.report.freshness_verdicts = [
                schema.FreshnessVerdict(
                    claim_id="claim-1",
                    candidate_id="cand-ban",
                    claim="OpenClaw API ban",
                    source="reddit",
                    source_item_id="ban",
                    verdict="current",
                    checked_at="2026-05-08T12:00:00Z",
                )
            ]
            assert cli._update_cached_freshness(cached)
        published.append((cache_path.read_bytes(), summary_path.read_bytes()))
        return _report(drill=True)

    parser = cli.build_parser()
    args = parser.parse_args([
        "--drill=cluster 1", f"--followup-topic={report.topic}", f"--followup-run={run_id}", "--mock",
    ])
    with mock.patch.object(cli.pipeline, "run", side_effect=publish_concurrent_result) as run:
        assert cli._main(parser, args, []) == 0

    run.assert_called_once()
    [(published_cache, published_summary)] = published
    assert published_cache != original_cache
    replacement_id = json.loads(published_cache)["run_id"]
    if concurrent_write == "research":
        assert replacement_id != run_id
    else:
        assert replacement_id == run_id
    assert cache_path.read_bytes() == published_cache
    assert summary_path.read_bytes() == published_summary
    captured = capsys.readouterr()
    assert "Original" in captured.out and "Deeper" in captured.out
    assert "Follow-up cache ID:" not in captured.err


@pytest.mark.parametrize("option", [["--drill", "cluster 1"], ["--verify-freshness"]])
def test_bound_cached_followup_rejects_another_topic(tmp_path, monkeypatch, capsys, option):
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    report = _report()
    assert cli._write_last_run(report.topic, report)
    cache = tmp_path / "last-report.json"
    before = cache.read_bytes()

    parser = cli.build_parser()
    args = parser.parse_args([*option, "--followup-topic=Another topic", "--mock"])
    with mock.patch.object(cli.pipeline, "run") as run, \
         mock.patch.object(cli, "_verify_report_set") as verify:
        assert cli._main(parser, args, []) == 2

    run.assert_not_called()
    verify.assert_not_called()
    assert cache.read_bytes() == before
    assert "No fresh cached report for the requested topic" in capsys.readouterr().err


@pytest.mark.parametrize("option", [["--drill", "cluster 1"], ["--verify-freshness"]])
def test_bound_cached_followup_rejects_a_replacement_for_the_same_topic(tmp_path, monkeypatch, capsys, option):
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    report = _report()
    cache = tmp_path / "last-report.json"
    assert cli._write_last_run(report.topic, report)
    run_id = json.loads(cache.read_bytes())["run_id"]
    assert f"[last30days] Follow-up cache ID: {run_id}\n" in capsys.readouterr().err

    replacement = copy.deepcopy(report)
    replacement.range_from = "2026-05-01"
    replacement.range_to = "2026-05-08"
    assert cli._write_last_run(replacement.topic, replacement)
    before = cache.read_bytes()
    assert json.loads(before)["run_id"] != run_id

    parser = cli.build_parser()
    args = parser.parse_args([*option, f"--followup-topic={report.topic}", f"--followup-run={run_id}", "--mock"])
    with mock.patch.object(cli.pipeline, "run") as run, \
         mock.patch.object(cli, "_verify_report_set") as verify:
        assert cli._main(parser, args, []) == 2

    run.assert_not_called()
    verify.assert_not_called()
    assert cache.read_bytes() == before
    assert "No fresh cached report for the requested run" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--drill=cluster 1", "--verify-freshness"])
@pytest.mark.parametrize(
    "new_input", ["--x-posts=x.json", "--reddit-threads=-", "--competitors-plan=plan.json", "--plan=-"],
)
def test_cached_followup_rejects_new_evidence_inputs(option, new_input, capsys):
    parser = cli.build_parser()
    args = parser.parse_args([option, new_input])
    with mock.patch.object(cli.env, "get_config") as config, pytest.raises(SystemExit) as error:
        cli._main(parser, args, [])
    assert error.value.code == 2
    config.assert_not_called()
    assert "do not accept new plans or envelope files" in capsys.readouterr().err


@pytest.mark.parametrize(
    "options",
    [
        ["topic", "--verify-freshness"],
        ["--welcome"],
        ["--drill=cluster 1", "--preflight"],
        ["--verify-freshness", "--resolve-browser-consent"],
    ],
)
@pytest.mark.parametrize(("flag", "value"), [("--followup-topic", "topic"), ("--followup-run", "a" * 32)])
def test_followup_selectors_are_only_for_a_cached_action(options, flag, value, capsys):
    parser = cli.build_parser()
    args = parser.parse_args([*options, f"{flag}={value}"])
    with mock.patch.object(cli.env, "get_config") as config, pytest.raises(SystemExit) as error:
        cli._main(parser, args, [])
    assert error.value.code == 2
    config.assert_not_called()
    assert f"{flag} requires" in capsys.readouterr().err


@pytest.mark.parametrize("drill", [True, False])
def test_drill_plan_does_not_gain_jobs_via_company_topic(monkeypatch, drill):
    plan = schema.QueryPlan(
        intent="general",
        freshness_mode="balanced_recent",
        cluster_mode="story",
        raw_topic="OpenClaw",
        notes=["drill-mode"] if drill else [],
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
    monkeypatch.setattr(pipeline, "MOCK_AVAILABLE_SOURCES", ["youtube", "jobs"])
    report = pipeline.run(
        topic="OpenClaw", config={}, depth="quick", mock=True,
        external_plan=schema.to_dict(plan), web_backend="none",
        as_of_date="2026-07-10",
    )

    expected = {"youtube"}
    assert report.query_plan.subqueries
    assert all(set(subquery.sources) == expected for subquery in report.query_plan.subqueries)


def test_merge_collapses_exact_url_rediscoveries():
    base = _report()
    drill_report = _report(drill=True)
    old = base.items_by_source["reddit"][0]
    new = drill_report.items_by_source["reddit"][0]
    old.body = "Initial rumor about the account restriction."
    new.body = "Full interview transcript explains revised enforcement and appeal procedures."
    new.snippet = "Appeals now receive human review."
    new.metadata = {"top_comments": [{"text": "My appeal was approved."}]}
    new.engagement = {"score": 321}

    merged = pipeline.merge_drill_report(
        base, drill_report, [base.clusters[0]], target="cluster 1",
    )

    retained = [item for item in merged.items_by_source["reddit"] if item.url == old.url]
    assert len(retained) == 1
    assert retained[0].body == "Full interview transcript explains revised enforcement and appeal procedures."
    assert retained[0].snippet == "Appeals now receive human review."
    assert retained[0].metadata == {"top_comments": [{"text": "My appeal was approved."}]}
    assert retained[0].engagement == {"score": 321}


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
    snapshot = cli._CachedReport(report, None, Path("/tmp/last-report.json"), b"", None)
    with mock.patch.object(cli, "_load_last_report_cache", return_value=snapshot), \
         mock.patch.object(cli.pipeline, "diagnose", return_value={}), \
         mock.patch.object(cli.pipeline, "run", side_effect=lambda **k: fake_run(**k)), \
         mock.patch.object(cli.pipeline, "merge_drill_report", side_effect=lambda r, d, c, target: r), \
         mock.patch.object(cli, "_write_last_run", return_value=True), \
         mock.patch.object(cli, "_show_runtime_ui"), \
         redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        cli._run_drill(args, {})

    assert "reddit" not in (captured.get("requested_sources") or [])
    assert captured.get("subreddits") is None


def test_drill_preserves_person_identity_anchors_without_aliasing():
    report = _report()
    report.query_plan.person_identity_anchors = ['Northstar Labs', 'Cedar Ventures']
    drill_plan = planner.build_drill_plan(report, 'cluster 1')
    assert drill_plan.person_identity_anchors == report.query_plan.person_identity_anchors
    drill_plan.person_identity_anchors.append('Another Organization')
    assert report.query_plan.person_identity_anchors == ['Northstar Labs', 'Cedar Ventures']


def test_drill_keeps_a_person_run_gate():
    report = _report()
    report.query_plan.person_run = True
    assert planner.build_drill_plan(report, 'cluster 1').person_run is True
