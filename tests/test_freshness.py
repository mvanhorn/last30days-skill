import json

import last30days as cli
from lib import freshness, github, health, hosted, http, planner, polymarket, render, schema, stocktwits


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
                "values": {"Yes": 0.47, "end_date": "2026-11-03"},
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
                "values": {"Bitcoin\x1f0": 0.86, "Bitcoin\x1f1": 0.75},
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
        lambda _payload, **_kwargs: [{"outcome_prices": [["Yes", 0.51]], "end_date": "2026-12-01"}],
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


def test_status_assertion_without_positive_rederivation_is_unsupported():
    report = _report(_item("reddit", title="Widget API is open"))

    verdict = freshness.verify_report(report)[0]

    assert verdict.verdict == "unsupported"
    assert "re-derived" in (verdict.detail or "")


def test_status_contradiction_requires_subject_bound_to_opposite_status():
    original = _item(
        "reddit",
        title="Widget API is open",
        published_at="2026-07-08T10:00:00Z",
    )
    report = _report(original)
    report.items_by_source["grounding"] = [
        _item(
            "grounding",
            item_id="web-2",
            title="Widget API remains open while legacy access is closed",
            published_at="2026-07-09T10:00:00Z",
        )
    ]

    verdict = freshness.verify_report(report)[0]

    assert verdict.verdict == "unsupported"


def test_verify_report_caches_one_point_refetch_per_source_key():
    first = _item(
        "stocktwits",
        item_id="stock-1",
        title="$ACME first post",
        metadata={"symbol": "ACME", "sentiment_aggregate": {"pct_bullish": 60}},
    )
    second = _item(
        "stocktwits",
        item_id="stock-2",
        title="$ACME second post",
        metadata={"symbol": "ACME", "sentiment_aggregate": {"pct_bullish": 60}},
    )
    calls = []

    verdicts = freshness.verify_report(
        _report(first, second),
        refetchers={
            "stocktwits": lambda item, key: calls.append((item, key)) or {
                "value": 60,
                "url": item.url,
            }
        },
    )

    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]
    assert len(calls) == 1


def test_stocktwits_refetch_cache_separates_depth_and_date_populations():
    default = _item(
        "stocktwits",
        item_id="stock-default",
        title="$ACME default population",
        metadata={
            "symbol": "ACME",
            "sentiment_aggregate": {"pct_bullish": 60},
            "freshness_window": {
                "depth": "default",
                "from_date": "2026-07-01",
                "to_date": "2026-07-10",
            },
        },
    )
    deep = _item(
        "stocktwits",
        item_id="stock-deep",
        title="$ACME deep population",
        metadata={
            "symbol": "ACME",
            "sentiment_aggregate": {"pct_bullish": 75},
            "freshness_window": {
                "depth": "deep",
                "from_date": "2026-06-10",
                "to_date": "2026-07-10",
            },
        },
    )
    calls = []

    verdicts = freshness.verify_report(
        _report(default, deep),
        refetchers={
            "stocktwits": lambda item, _key: calls.append(item.item_id) or {
                "value": item.metadata["sentiment_aggregate"]["pct_bullish"],
                "url": item.url,
            }
        },
    )

    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]
    assert calls == ["stock-default", "stock-deep"]


def test_stocktwits_refetch_reuses_retrieval_depth_and_date_window(monkeypatch):
    item = _item(
        "stocktwits",
        title="$ACME",
        metadata={
            "symbol": "ACME",
            "freshness_window": {
                "depth": "deep",
                "from_date": "2026-07-01",
                "to_date": "2026-07-10",
            },
        },
    )
    calls = []

    def fake_request(_method, _url, **kwargs):
        calls.append(kwargs.get("params"))
        if len(calls) == 1:
            return {
                "messages": [
                    {"id": 3, "created_at": "2026-07-10T12:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}},
                    {"id": 2, "created_at": "2026-06-30T12:00:00Z", "entities": {"sentiment": {"basic": "Bearish"}}},
                ],
                "cursor": {"more": True, "max": 2},
            }
        return {
            "messages": [
                {"id": 1, "created_at": "2026-07-09T12:00:00Z", "entities": {"sentiment": {"basic": "Bullish"}}},
            ],
            "cursor": {"more": False},
        }

    monkeypatch.setattr(http, "request", fake_request)

    refreshed = stocktwits.refetch_datum(item, "pct_bullish")

    assert refreshed["value"] == 100
    assert calls == [None, {"max": 2}]


def test_unverified_drill_clears_inherited_freshness_verdicts(tmp_path, monkeypatch):
    cached = _report(_item("reddit", title="Widget API is open"))
    freshness.verify_report(cached)
    merged = _report(_item("reddit", title="Widget API is open"))
    merged.freshness_verdicts = list(cached.freshness_verdicts)
    drill_report = _report(_item("reddit", title="Widget API is closed"))
    monkeypatch.setattr(cli, "_load_last_report_cache", lambda *_args, **_kwargs: (cached, None, tmp_path / "last-report.json"))
    monkeypatch.setattr(planner, "resolve_drill_clusters", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(planner, "build_drill_plan", lambda *_args, **_kwargs: cached.query_plan)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli.pipeline, "run", lambda **_kwargs: drill_report)
    monkeypatch.setattr(cli.pipeline, "merge_drill_report", lambda *_args, **_kwargs: merged)
    monkeypatch.setattr(cli, "_show_runtime_ui", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "_write_last_run", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *_args, **_kwargs: 0)
    args = cli.build_parser().parse_args(["--drill", "cluster 1", "--mock"])

    assert cli._run_drill(args, {}) == 0
    assert merged.freshness_verdicts == []


def test_polymarket_refetch_uses_complete_outcomes_and_one_event_snapshot(monkeypatch):
    item = _item(
        "polymarket",
        title="Who will win?",
        url="https://polymarket.com/event/winner",
        metadata={
            "event_id": "42",
            "outcome_prices": [["Delta", 0.05]],
            "end_date": "2026-12-01",
        },
    )
    event = {
        "id": "42",
        "slug": "winner",
        "title": "Who will win?",
        "updatedAt": "2026-07-10T12:00:00Z",
        "markets": [{
            "active": True,
            "closed": False,
            "question": "Who will win?",
            "liquidity": "100",
            "outcomes": '["Alpha", "Beta", "Gamma", "Delta"]',
            "outcomePrices": '["0.4", "0.3", "0.25", "0.05"]',
            "endDate": "2026-12-01T00:00:00Z",
        }],
    }
    calls = []
    monkeypatch.setattr(
        polymarket.http,
        "request",
        lambda *_args, **_kwargs: calls.append(1) or event,
    )

    verdicts = freshness.verify_report(_report(item))

    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]
    assert len(calls) == 1


def test_polymarket_refetch_marks_resolved_market_movement_stale(monkeypatch):
    item = _item(
        "polymarket",
        title="Will the bill pass?",
        url="https://polymarket.com/event/bill",
        metadata={
            "event_id": "42",
            "outcome_prices": [["Yes", 0.42]],
        },
    )
    monkeypatch.setattr(
        polymarket.http,
        "request",
        lambda *_args, **_kwargs: {
            "id": "42",
            "slug": "bill",
            "title": "Will the bill pass?",
            "active": False,
            "closed": True,
            "markets": [{
                "active": False,
                "closed": True,
                "question": "Will the bill pass?",
                "liquidity": "0",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]',
            }],
        },
    )

    verdict = freshness.verify_report(_report(item))[0]

    assert verdict.verdict == "stale"
    assert verdict.current_value == 1.0


def test_github_refetch_reuses_resolved_gh_cli_token(monkeypatch):
    item = _item(
        "github",
        title="owner/private",
        url="https://github.com/owner/private",
        container="owner/private",
    )
    seen_headers = []
    monkeypatch.setattr(github, "_resolve_token", lambda: "gh-cli-token")
    monkeypatch.setattr(
        github.http,
        "request",
        lambda *_args, **kwargs: seen_headers.append(kwargs["headers"]) or {
            "stargazers_count": 7,
            "html_url": item.url,
        },
    )

    github.refetch_datum(item, "stars")

    assert seen_headers == [{
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer gh-cli-token",
    }]


def test_early_return_paths_do_not_ignore_freshness_verification(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test")
    monkeypatch.setattr(cli.env, "get_config", lambda **_kwargs: {})
    parser = cli.build_parser()
    hosted_args = parser.parse_args(["topic", "--verify-freshness"])

    assert cli._main(parser, hosted_args, []) == 2
    assert "not supported by the hosted backend" in capsys.readouterr().err

    monkeypatch.delenv("LAST30DAYS_API_KEY")
    monkeypatch.delenv("LAST30DAYS_API_BASE")
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("# Synthesis", encoding="utf-8")
    report = _report(_item("reddit", title="Widget API is open"))
    calls = []
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_load_last_report_cache", lambda *_args, **_kwargs: (report, None, tmp_path / "last-report.json"))
    monkeypatch.setattr(cli, "_verify_report_set", lambda *_args, **_kwargs: calls.append("verified"))
    monkeypatch.setattr(cli, "_render_save_and_print", lambda *_args, **_kwargs: calls.append("rendered") or 0)
    cached_args = parser.parse_args([
        "topic", "--emit=html", f"--synthesis-file={synthesis}", "--verify-freshness",
    ])

    assert cli._main(parser, cached_args, []) == 0
    assert calls == ["verified", "rendered"]


def test_hosted_configured_freshness_skips_unless_cli_explicitly_enables(monkeypatch, capsys):
    monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test")
    monkeypatch.setattr(
        cli.env,
        "get_config",
        lambda **_kwargs: {"LAST30DAYS_VERIFY_FRESHNESS": "on"},
    )
    calls = []
    monkeypatch.setattr(
        hosted,
        "run_hosted",
        lambda topic, depth, **_kwargs: calls.append((topic, depth)) or 0,
    )
    parser = cli.build_parser()

    assert cli._main(parser, parser.parse_args(["topic"]), []) == 0
    assert calls == [("topic", "default")]
    assert capsys.readouterr().err == (
        "hosted backend does not support freshness verification; skipping\n"
    )
    no_verify_args = parser.parse_args(["topic", "--no-verify-freshness"])
    assert cli._freshness_enabled(
        no_verify_args,
        {"LAST30DAYS_VERIFY_FRESHNESS": "on"},
    ) is False


def test_agent_export_includes_typed_claim_metadata():
    item = _item("reddit", title="Widget API is open")
    report = _report(item)
    freshness.verify_report(report, checked_at="2026-07-10T13:00:00Z")

    exported = schema.to_agent_export(report)

    assert exported["schema_version"] == "1.2"
    assert exported["freshness_verdicts"][0]["verdict"] == "unsupported"
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
    assert "(moved: 1,000 -> 1,010)" in rendered
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
    assert cached_verdicts[0]["verdict"] == "unsupported"
    assert payload["timestamp"] == before["timestamp"]
    assert "Updated freshness verdicts" in capsys.readouterr().err


def test_opt_in_gating_accepts_flag_or_truthy_config():
    parser = cli.build_parser()
    default_args = parser.parse_args(["topic"])
    flag_args = parser.parse_args(["topic", "--verify-freshness"])

    assert cli._freshness_enabled(default_args, {}) is False
    assert cli._freshness_enabled(default_args, {"LAST30DAYS_VERIFY_FRESHNESS": "on"}) is True
    assert cli._freshness_enabled(flag_args, {}) is True


def test_mixed_event_prefers_active_markets(monkeypatch):
    from types import SimpleNamespace
    from lib import polymarket

    event = {
        "id": "ev1",
        "title": "Mixed event",
        "active": True,
        "markets": [
            {"id": "m-closed", "active": False, "closed": True,
             "question": "Old market", "volume": "900000",
             "outcomePrices": '["0.99","0.01"]', "outcomes": '["Yes","No"]'},
            {"id": "m-active", "active": True, "closed": False,
             "question": "Live market", "volume": "1000",
             "outcomePrices": '["0.60","0.40"]', "outcomes": '["Yes","No"]',
             "liquidity": "5000"},
        ],
    }
    captured = {}
    real_parse = polymarket.parse_polymarket_response

    def spy(payload, **kwargs):
        captured.update(kwargs)
        return real_parse(payload, **kwargs)

    monkeypatch.setattr(polymarket, "parse_polymarket_response", spy)
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [event])
    item = SimpleNamespace(metadata={"event_id": "ev1"}, url="")
    try:
        polymarket.refetch_datum(item, "probability")
    except Exception:
        pass
    assert captured.get("include_closed") is False

    # Fully resolved event falls back to closed markets.
    captured.clear()
    resolved = dict(event)
    resolved["markets"] = [dict(event["markets"][0])]
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [resolved])
    try:
        polymarket.refetch_datum(item, "probability")
    except Exception:
        pass
    assert captured.get("include_closed") is True


def test_slug_refetch_requires_identity_match(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from lib import polymarket

    wrong = {"id": "222", "slug": "different-market", "title": "Other",
             "markets": [{"id": "m1", "active": True, "closed": False,
                          "question": "q", "volume": "10",
                          "outcomePrices": '["0.5","0.5"]', "outcomes": '["Yes","No"]',
                          "liquidity": "10"}]}
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [wrong])
    item = SimpleNamespace(
        metadata={}, item_id="777",
        url="https://polymarket.com/event/fed-rate-cut-2026",
    )
    with pytest.raises(KeyError):
        polymarket.refetch_datum(item, "probability")

    right = dict(wrong)
    right["slug"] = "fed-rate-cut-2026"
    right["id"] = "777"
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [wrong, right])
    values = polymarket.refetch_datum(item, "Yes")
    assert values is not None


def _stale_fixture_report() -> schema.Report:
    market = _item(
        "polymarket",
        title="Will the bill pass?",
        metadata={"outcome_prices": [["Yes", 0.42]]},
    )
    gh = _item(
        "github",
        title="owner/repo (1K stars)",
        url="https://github.com/owner/repo",
        container="owner/repo",
        engagement={"stars": 1000},
    )
    stock = _item(
        "stocktwits",
        title="$ACME traders debate earnings",
        container="ACME",
        metadata={"symbol": "ACME", "sentiment_aggregate": {"pct_bullish": 60}},
    )
    return _report(market, gh, stock)


def test_stale_detail_carries_original_and_current_values():
    report = _stale_fixture_report()
    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-10T13:00:00Z",
        refetchers={
            "polymarket": lambda _item, _key: {"value": 0.47, "url": _item.url},
            "github": lambda _item, _key: {"value": 1200, "url": _item.url},
            "stocktwits": lambda _item, _key: {"value": 55, "url": _item.url},
        },
    )

    details = {verdict.source: verdict.detail for verdict in verdicts}
    assert details["polymarket"] == "moved: 42% -> 47%"
    assert details["github"] == "moved: 1,000 -> 1,200"
    assert details["stocktwits"] == "moved: 60% -> 55%"


def test_current_verdicts_keep_detail_none():
    report = _stale_fixture_report()
    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-10T13:00:00Z",
        refetchers={
            "polymarket": lambda _item, _key: {"value": 0.42, "url": _item.url},
            "github": lambda _item, _key: {"value": 1000, "url": _item.url},
            "stocktwits": lambda _item, _key: {"value": 60, "url": _item.url},
        },
    )

    assert all(verdict.verdict == "current" for verdict in verdicts)
    assert all(verdict.detail is None for verdict in verdicts)


def test_agent_export_carries_enriched_stale_detail():
    report = _stale_fixture_report()
    freshness.verify_report(
        report,
        checked_at="2026-07-10T13:00:00Z",
        refetchers={
            "polymarket": lambda _item, _key: {"value": 0.47, "url": _item.url},
            "github": lambda _item, _key: {"value": 1200, "url": _item.url},
            "stocktwits": lambda _item, _key: {"value": 55, "url": _item.url},
        },
    )

    exported = schema.to_agent_export(report)
    stale = [v for v in exported["freshness_verdicts"] if v["verdict"] == "stale"]
    assert stale and all(v["detail"].startswith("moved: ") for v in stale)


def test_zero_claim_verification_notes_outcome_on_stderr(capsys):
    prose_only = _report(_item("reddit", title="10 ways teams discussed a launch"))

    cli._verify_report_set(prose_only, None, allow_network=False)

    err = capsys.readouterr().err
    assert "no re-checkable claims" in err
    assert prose_only.freshness_verdicts == []


def test_zero_claim_note_prints_once_across_entity_reports(capsys):
    first = _report(_item("reddit", title="Thread one", item_id="reddit-a"))
    second = _report(_item("reddit", title="Thread two", item_id="reddit-b"))

    cli._verify_report_set(first, [("one", first), ("two", second)], allow_network=False)

    err = capsys.readouterr().err
    assert err.count("no re-checkable claims") == 1


def test_zero_claim_note_absent_when_any_report_has_verdicts(capsys):
    market = _report(
        _item("polymarket", title="Will it?", metadata={"outcome_prices": [["Yes", 0.42]]})
    )

    cli._verify_report_set(market, None, allow_network=False)

    err = capsys.readouterr().err
    assert "no re-checkable claims" not in err
    assert market.freshness_verdicts  # unsupported verdicts still count as outcomes


def _candidate_star_report(*, repos: dict[str, int], item_source: str = "reddit") -> schema.Report:
    """A report whose only star facts live in candidate enrichment metadata."""
    primary = _item(item_source, title="Agent tooling thread")
    report = _report(primary)
    report.ranked_candidates[0].metadata["github_stars"] = dict(repos)
    return report


def test_candidate_star_claims_dispatch_despite_non_github_primary_item():
    """Regression: star facts attached during post-rerank enrichment carried
    no claims because extraction read only item-level engagement, so a
    GitHub-flavored run produced zero verdicts."""
    report = _candidate_star_report(repos={"a/b": 100})

    calls: list[tuple[object, str]] = []

    def fake_refetch(item, key):
        calls.append((item, key))
        return {"value": 100, "url": f"https://github.com/{key}"}

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": fake_refetch},
    )

    assert [verdict.verdict for verdict in verdicts] == ["current"]
    assert calls == [(None, "a/b")]
    assert verdicts[0].source == "github"
    assert verdicts[0].source_url == "https://github.com/a/b"


def test_candidate_star_claim_moved_value_is_stale_with_detail():
    report = _candidate_star_report(repos={"a/b": 100})

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": lambda _item, key: {"value": 250, "url": f"https://github.com/{key}"}},
    )

    assert [verdict.verdict for verdict in verdicts] == ["stale"]
    assert verdicts[0].detail == "moved: 100 -> 250"
    assert verdicts[0].original_value == 100
    assert verdicts[0].current_value == 250


def test_repo_claimed_at_item_level_is_not_claimed_again_from_metadata():
    gh_item = _item(
        "github",
        title="a/b (100 stars)",
        url="https://github.com/a/b",
        container="a/b",
        engagement={"stars": 100},
    )
    report = _report(gh_item)
    report.ranked_candidates[0].metadata["github_stars"] = {"a/b": 100}

    claims = [
        claim for claim in freshness.extract_claims(report)
        if claim.datum_kind == "github_stars"
    ]

    assert len(claims) == 1
    assert claims[0].datum_key == "stars"


def test_multiple_candidates_citing_same_repo_share_one_refetch():
    first = _item("reddit", title="Thread one", item_id="reddit-a")
    second = _item("reddit", title="Thread two", item_id="reddit-b")
    report = _report(first, second)
    for candidate in report.ranked_candidates:
        candidate.metadata["github_stars"] = {"a/b": 100}

    calls: list[str] = []

    def fake_refetch(_item, key):
        calls.append(key)
        return {"value": 100, "url": f"https://github.com/{key}"}

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": fake_refetch},
    )

    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]
    assert calls == ["a/b"]


def test_candidate_star_claims_bypass_github_source_outcome_gate():
    report = _candidate_star_report(repos={"a/b": 100})
    report.source_status["github"] = schema.SourceOutcome(
        source="github", state="timeout", items_returned=0,
    )

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": lambda _item, key: {"value": 100, "url": f"https://github.com/{key}"}},
    )

    assert [verdict.verdict for verdict in verdicts] == ["current"]


def test_candidates_without_star_metadata_emit_no_claims():
    report = _report(_item("reddit", title="Agent tooling thread"))

    assert freshness.extract_claims(report) == []


def test_refetch_datum_accepts_repo_slug_datum_key(monkeypatch):
    seen_urls: list[str] = []
    monkeypatch.setattr(github, "_resolve_token", lambda: "")
    monkeypatch.setattr(
        github.http,
        "request",
        lambda _method, url, **_kwargs: seen_urls.append(url) or {
            "stargazers_count": 42,
            "html_url": "https://github.com/a/b",
        },
    )

    refreshed = github.refetch_datum(None, "a/b")

    assert seen_urls == ["https://api.github.com/repos/a/b"]
    assert refreshed["value"] == 42


def test_unsupported_verdicts_carry_no_fabricated_evidence():
    report = _report(_item("reddit", title="Widget API is open"))

    verdicts = freshness.verify_report(report, checked_at="2026-07-11T13:00:00Z")

    assert [verdict.verdict for verdict in verdicts] == ["unsupported"]
    assert verdicts[0].evidence_url == ""
    assert verdicts[0].evidence_timestamp is None
    assert verdicts[0].source_url  # provenance stays on the source fields


def test_rendered_table_shows_unsupported_reason_from_detail():
    report = _report(_item("reddit", title="Widget API is open"))
    freshness.verify_report(report, checked_at="2026-07-11T13:00:00Z")

    rendered = render.render_compact(report)

    assert "(Status could not be positively re-derived from a current source)" in rendered


def test_agent_export_results_expose_candidate_id_join_key():
    report = _candidate_star_report(repos={"a/b": 100})
    freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": lambda _item, key: {"value": 100, "url": f"https://github.com/{key}"}},
    )

    exported = schema.to_agent_export(report)

    result_ids = {result["candidate_id"] for result in exported["results"]}
    verdict_ids = {verdict["candidate_id"] for verdict in exported["freshness_verdicts"]}
    assert verdict_ids and verdict_ids <= result_ids


def test_other_candidates_item_level_claim_does_not_suppress_enriched_claim():
    gh_item = _item(
        "github",
        title="a/b (100 stars)",
        url="https://github.com/a/b",
        container="a/b",
        engagement={"stars": 100},
    )
    reddit_item = _item("reddit", title="Thread about a/b", item_id="reddit-a")
    report = _report(gh_item, reddit_item)
    report.ranked_candidates[1].metadata["github_stars"] = {"a/b": 100}

    calls: list[str] = []

    def fake_refetch(_item, key):
        calls.append(key)
        return {"value": 100, "url": "https://github.com/a/b"}

    verdicts = freshness.verify_report(
        report,
        checked_at="2026-07-11T13:00:00Z",
        refetchers={"github": fake_refetch},
    )

    # Both candidates get their own verdict, sharing one repo snapshot.
    assert [verdict.verdict for verdict in verdicts] == ["current", "current"]
    assert {verdict.candidate_id for verdict in verdicts} == {"candidate-1", "candidate-2"}
    assert calls == ["stars"]


def test_slug_refetch_rejects_same_slug_different_event_id(monkeypatch):
    from types import SimpleNamespace
    import pytest
    from lib import polymarket

    recreated = {"id": "999", "slug": "fed-rate-cut-2026", "title": "Recreated",
                 "markets": [{"id": "m1", "active": True, "closed": False,
                              "question": "q", "volume": "10",
                              "outcomePrices": '["0.5","0.5"]', "outcomes": '["Yes","No"]',
                              "liquidity": "10"}]}
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [recreated])
    cached = SimpleNamespace(
        metadata={}, item_id="123",
        url="https://polymarket.com/event/fed-rate-cut-2026",
    )
    with pytest.raises(KeyError):
        polymarket.refetch_datum(cached, "Yes")

    original = dict(recreated)
    original["id"] = "123"
    monkeypatch.setattr(polymarket.http, "request", lambda *a, **k: [recreated, original])
    assert polymarket.refetch_datum(cached, "Yes") is not None


def test_slug_refetch_fails_closed_without_any_event_identity(monkeypatch):
    """A synthetic PM<N> parse id carries no identity, so slug equality alone
    must not verify a claim; the refetch fails closed to unsupported."""
    from types import SimpleNamespace
    import pytest
    from lib import polymarket

    calls: list[object] = []
    monkeypatch.setattr(
        polymarket.http, "request", lambda *a, **k: calls.append(a) or []
    )
    cached = SimpleNamespace(
        metadata={}, item_id="PM3",
        url="https://polymarket.com/event/fed-rate-cut-2026",
    )
    with pytest.raises(ValueError):
        polymarket.refetch_datum(cached, "Yes")
    assert calls == []  # fails closed before any network request
