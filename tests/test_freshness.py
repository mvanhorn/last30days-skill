import json

import last30days as cli
from lib import freshness, github, health, http, polymarket, render, schema, stocktwits


def _report(*items: schema.SourceItem) -> schema.Report:
    candidates = []
    for index, item in enumerate(items, start=1):
        candidates.append(
            schema.Candidate(
                candidate_id=f"candidate-{index}",
                item_id=item.item_id,
                source=item.source,
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                subquery_labels=["primary"],
                native_ranks={f"primary:{item.source}": index},
                local_relevance=0.9,
                freshness=90,
                engagement=10,
                source_quality=0.8,
                rrf_score=0.02,
                final_score=90,
                source_items=[item],
            )
        )
    return schema.Report(
        topic="freshness fixture",
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="fixture",
            rerank_model="fixture",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="strict_recent",
            cluster_mode="story",
            raw_topic="freshness fixture",
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query="freshness fixture",
                    ranking_query="freshness fixture",
                    sources=[item.source for item in items] or ["reddit"],
                )
            ],
            source_weights={item.source: 1.0 for item in items},
        ),
        clusters=[],
        ranked_candidates=candidates,
        items_by_source={
            source: [item for item in items if item.source == source]
            for source in {item.source for item in items}
        },
        errors_by_source={},
        source_status={
            source: schema.SourceOutcome(source=source, state=health.OK, items_returned=1)
            for source in {item.source for item in items}
        },
    )


def _item(source: str, *, title: str, snippet: str = "", **kwargs) -> schema.SourceItem:
    return schema.SourceItem(
        item_id=kwargs.pop("item_id", f"{source}-1"),
        source=source,
        title=title,
        body=kwargs.pop("body", snippet),
        url=kwargs.pop("url", f"https://example.com/{source}/1"),
        published_at=kwargs.pop("published_at", "2026-07-09T12:00:00Z"),
        snippet=snippet,
        **kwargs,
    )


def test_extract_claims_is_conservative_and_structurally_grounded():
    prose = _item(
        "reddit",
        title="10 ways teams discussed a launch",
        snippet="The post has 2,000 votes and mentions 2026-08-01 in passing.",
    )
    market = _item(
        "polymarket",
        title="Will the bill pass?",
        metadata={
            "event_id": "123",
            "outcome_prices": [["Yes", 0.42], ["No", 0.58]],
            "end_date": "2026-11-03",
        },
    )

    claims = freshness.extract_claims(_report(prose, market))

    assert [claim.datum_kind for claim in claims] == [
        "polymarket_probability",
        "polymarket_probability",
        "polymarket_end_date",
    ]
    assert all(claim.source_item_id == market.item_id for claim in claims)


def test_verify_report_assigns_current_and_stale_for_each_point_source():
    market = _item(
        "polymarket",
        title="Will the bill pass?",
        metadata={"outcome_prices": [["Yes", 0.42]], "end_date": "2026-11-03"},
    )
    github = _item(
        "github",
        title="owner/repo (1K stars)",
        url="https://github.com/owner/repo",
        container="owner/repo",
        engagement={"stars": 1000},
    )
    stocktwits = _item(
        "stocktwits",
        title="$ACME traders debate earnings",
        container="ACME",
        metadata={
            "symbol": "ACME",
            "sentiment_aggregate": {"pct_bullish": 60},
        },
    )
    report = _report(market, github, stocktwits)

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-10T13:00:00Z",
        refetchers={
            "polymarket": lambda _item, key: {
                "value": 0.47 if key == "Yes" else "2026-11-03",
                "url": "https://polymarket.com/event/bill",
                "timestamp": "2026-07-10T12:59:00Z",
            },
            "github": lambda _item, _key: {"value": 1000, "url": _item.url},
            "stocktwits": lambda _item, _key: {"value": 55, "url": _item.url},
        },
    )

    by_kind = {claim.datum_kind: claim for claim in freshness.extract_claims(report)}
    by_id = {verdict.claim_id: verdict for verdict in verdicts}
    assert by_id[by_kind["polymarket_probability"].claim_id].verdict == "stale"
    assert by_id[by_kind["polymarket_end_date"].claim_id].verdict == "current"
    assert by_id[by_kind["github_stars"].claim_id].verdict == "current"
    stock_verdict = by_id[by_kind["stocktwits_bullish_pct"].claim_id]
    assert stock_verdict.verdict == "stale"
    assert stock_verdict.original_value == 60
    assert stock_verdict.current_value == 55
    assert report.freshness_verdicts == verdicts


def test_duplicate_polymarket_labels_keep_distinct_refetch_identity():
    market = _item(
        "polymarket",
        title="What price will Bitcoin hit?",
        metadata={"outcome_prices": [["Bitcoin", 0.86], ["Bitcoin", 0.75]]},
    )
    report = _report(market)

    claims = freshness.extract_claims(report)
    verdicts = freshness.verify_report(
        report,
        refetchers={
            "polymarket": lambda _item, key: {
                "value": [0.86, 0.75][int(key.rsplit("\x1f", 1)[1])],
                "url": _item.url,
            }
        },
    )

    assert len({claim.datum_key for claim in claims}) == 2
    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]


def test_source_refetch_helpers_use_shared_http_wrapper(monkeypatch):
    github_item = _item(
        "github",
        title="owner/repo",
        url="https://github.com/owner/repo",
        container="owner/repo",
    )
    monkeypatch.setattr(github.env, "read_secret_env", lambda _key: None)
    monkeypatch.setattr(
        github.http,
        "request",
        lambda *_args, **_kwargs: {
            "stargazers_count": 123,
            "html_url": github_item.url,
            "updated_at": "2026-07-10T12:00:00Z",
        },
    )
    assert github.refetch_datum(github_item, "stars")["value"] == 123

    market_item = _item(
        "polymarket",
        title="Market",
        url="https://polymarket.com/event/market-slug",
        metadata={"event_id": "42"},
    )
    monkeypatch.setattr(
        polymarket.http,
        "request",
        lambda *_args, **_kwargs: {"id": "42", "updatedAt": "2026-07-10T12:00:00Z"},
    )
    monkeypatch.setattr(
        polymarket,
        "parse_polymarket_response",
        lambda _payload: [{"outcome_prices": [["Yes", 0.51]], "end_date": "2026-12-01"}],
    )
    assert polymarket.refetch_datum(market_item, "Yes")["value"] == 0.51

    stock_item = _item(
        "stocktwits",
        title="$ACME",
        metadata={"symbol": "ACME"},
    )
    monkeypatch.setattr(
        http,
        "request",
        lambda *_args, **_kwargs: {
            "messages": [
                {
                    "created_at": "2026-07-10T12:00:00Z",
                    "entities": {"sentiment": {"basic": "Bullish"}},
                }
            ]
        },
    )
    assert stocktwits.refetch_datum(stock_item, "pct_bullish")["value"] == 100


def test_source_item_lookup_is_scoped_by_source_when_ids_collide():
    github_item = _item(
        "github",
        item_id="42",
        title="owner/repo",
        url="https://github.com/owner/repo",
        container="owner/repo",
        engagement={"stars": 10},
    )
    market_item = _item(
        "polymarket",
        item_id="42",
        title="Will it pass?",
        metadata={"outcome_prices": [["Yes", 0.5]]},
    )
    report = _report(github_item, market_item)

    verdicts = freshness.verify_report(
        report,
        refetchers={
            "github": lambda item, _key: {"value": item.engagement["stars"], "url": item.url},
            "polymarket": lambda item, _key: {
                "value": item.metadata["outcome_prices"][0][1],
                "url": item.url,
            },
        },
    )

    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]


def test_degraded_source_is_unsupported_not_stale():
    github = _item(
        "github",
        title="owner/repo (1K stars)",
        url="https://github.com/owner/repo",
        container="owner/repo",
        engagement={"stars": 1000},
    )
    report = _report(github)
    report.source_status["github"] = schema.SourceOutcome(
        source="github", state=schema.UNREACHABLE,
    )
    called = False

    def refetcher(_item, _key):
        nonlocal called
        called = True
        return 999

    verdict = freshness.verify_report(report, refetchers={"github": refetcher})[0]

    assert verdict.verdict == "unsupported"
    assert "unreachable" in (verdict.detail or "")
    assert called is False


def test_newer_in_report_status_disagreement_is_contradicted():
    original = _item(
        "reddit",
        title="Widget API is open",
        published_at="2026-07-08T10:00:00Z",
    )
    report = _report(original)
    contradiction = _item(
        "grounding",
        item_id="web-2",
        title="Widget API is closed",
        published_at="2026-07-09T10:00:00Z",
        url="https://status.example.com/widget",
    )
    report.items_by_source["grounding"] = [contradiction]

    verdict = freshness.verify_report(report)[0]

    assert verdict.verdict == "contradicted"
    assert verdict.current_value == "closed"
    assert verdict.evidence_url == contradiction.url
    assert verdict.evidence_timestamp == contradiction.published_at


def test_agent_export_includes_typed_claim_metadata():
    item = _item("reddit", title="Widget API is open")
    report = _report(item)
    freshness.verify_report(report, checked_at="2026-07-10T13:00:00Z")

    exported = schema.to_agent_export(report)

    assert exported["schema_version"] == "1.1"
    assert exported["freshness_verdicts"][0]["verdict"] == "current"
    assert exported["freshness_verdicts"][0]["source_item_id"] == item.item_id


def test_render_surfaces_inline_flag_and_freshness_footer_table():
    item = _item(
        "github",
        title="owner/repo (1K stars)",
        url="https://github.com/owner/repo",
        container="owner/repo",
        engagement={"stars": 1000},
    )
    report = _report(item)
    report.clusters = [
        schema.Cluster(
            cluster_id="cluster-1",
            title="Repository traction",
            candidate_ids=["candidate-1"],
            representative_ids=["candidate-1"],
            sources=["github"],
            score=90,
        )
    ]
    report.ranked_candidates[0].cluster_id = "cluster-1"
    freshness.verify_report(
        report,
        checked_at="2026-07-10T13:00:00Z",
        refetchers={"github": lambda _item, _key: {"value": 1010, "url": _item.url}},
    )

    rendered = render.render_compact(report)

    assert "[freshness:stale]" in rendered
    assert "## Freshness Verification" in rendered
    assert "(was 1000, now 1010)" in rendered
    assert "## Freshness Verification" in render.render_for_html(report)
    assert "## Freshness Verification" in render.render_brief(report)


def test_post_hoc_path_loads_updates_and_rewrites_cache(tmp_path, monkeypatch, capsys):
    item = _item("reddit", title="Widget API is open")
    report = _report(item)
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    assert cli._write_last_run(report.topic, report)
    before = json.loads((tmp_path / "last-report.json").read_text(encoding="utf-8"))
    args = cli.build_parser().parse_args(["--verify-freshness", "--mock", "--emit=json"])

    rc = cli._run_cached_freshness(args, {})

    assert rc == 0
    payload = json.loads((tmp_path / "last-report.json").read_text(encoding="utf-8"))
    cached_verdicts = payload["reports"][0]["report"]["freshness_verdicts"]
    assert cached_verdicts[0]["verdict"] == "current"
    assert payload["timestamp"] == before["timestamp"]
    assert "Updated freshness verdicts" in capsys.readouterr().err


def test_opt_in_gating_accepts_flag_or_truthy_config():
    parser = cli.build_parser()
    default_args = parser.parse_args(["topic"])
    flag_args = parser.parse_args(["topic", "--verify-freshness"])

    assert cli._freshness_enabled(default_args, {}) is False
    assert cli._freshness_enabled(default_args, {"LAST30DAYS_VERIFY_FRESHNESS": "on"}) is True
    assert cli._freshness_enabled(flag_args, {}) is True
