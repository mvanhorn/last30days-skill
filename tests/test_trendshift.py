"""Trendshift is a keyless, explicitly requested repository-momentum source."""

from unittest import mock

from lib import pipeline, planner, schema, signals, trendshift


LISTING = '''
<a href="/repositories/42">acme/rocket</a>
<a href="/repositories/7">other/widget</a>
<a href="/repositories/42">acme/rocket</a>
'''


def test_listing_parser_preserves_rank_and_deduplicates_repositories():
    items = trendshift.parse_listing(LISTING, as_of="2026-09-28")

    assert [item["title"] for item in items] == ["acme/rocket", "other/widget"]
    assert [item["engagement"]["rank"] for item in items] == [1, 2]
    assert [item["metadata"]["discovery_signal"] for item in items] == [1.0, 0.5]
    assert items[0]["url"] == "https://trendshift.io/repositories/42"
    assert items[0]["date"] == "2026-09-28"


def test_source_is_off_by_default_and_available_only_on_opt_in():
    assert "trendshift" not in pipeline.available_sources({}, None, x_pending=False)
    assert "trendshift" in pipeline.available_sources({}, ["trendshift"], x_pending=False)
    assert "trendshift" in pipeline.available_sources(
        {"INCLUDE_SOURCES": "trendshift"}, None, x_pending=False
    )


def test_search_uses_shared_html_fetch_and_filters_unrelated_repositories(monkeypatch):
    captured = {}

    def fake_get_text(url, **kwargs):
        captured["url"] = url
        captured["accept"] = kwargs["accept"]
        return LISTING

    monkeypatch.setattr(trendshift.http, "get_text", fake_get_text)
    monkeypatch.setattr(trendshift, "_current_snapshot_date", lambda: "2026-09-28")
    items = trendshift.search_trendshift("acme rocket", "2026-08-29", "2026-09-28")

    assert [item["title"] for item in items] == ["acme/rocket"]
    assert captured == {"url": "https://trendshift.io/", "accept": "text/html"}


def test_historical_snapshot_is_not_mislabeled_as_current_listing(monkeypatch):
    get_text = mock.Mock(return_value=LISTING)
    monkeypatch.setattr(trendshift.http, "get_text", get_text)
    monkeypatch.setattr(trendshift, "_current_snapshot_date", lambda: "2026-09-28")

    items, error = trendshift.fetch_trendshift(
        "acme", "2026-08-01", "2026-08-31", require_snapshot_date=True,
    )

    assert items == []
    assert "2026-08-31" in (error or "")
    get_text.assert_not_called()


def test_research_window_date_does_not_block_current_listing(monkeypatch):
    get_text = mock.Mock(return_value=LISTING)
    monkeypatch.setattr(trendshift.http, "get_text", get_text)
    monkeypatch.setattr(trendshift, "_current_snapshot_date", lambda: "2026-09-28")

    items, error = trendshift.fetch_trendshift(
        "acme", "2026-08-29", "2026-09-27",
    )

    assert error is None
    assert [item["title"] for item in items] == ["acme/rocket"]
    assert items[0]["date"] == "2026-09-28"
    get_text.assert_called_once()


def test_research_stream_surfaces_trendshift_fetch_failure(monkeypatch):
    monkeypatch.setattr(
        pipeline.trendshift,
        "fetch_trendshift",
        lambda *args, **kwargs: ([], "Trendshift listing fetch failed"),
    )
    subquery = schema.SubQuery(
        label="primary", search_query="AI agents", ranking_query="AI agents",
        sources=["trendshift"],
    )

    items, artifact = pipeline._retrieve_stream_impl(
        topic="AI agents",
        subquery=subquery,
        source="trendshift",
        config={},
        depth="quick",
        date_range=("2026-08-29", "2026-09-28"),
        runtime=schema.ProviderRuntime(
            reasoning_provider="none", planner_model="none", rerank_model="none",
        ),
        mock=False,
    )

    assert items == []
    assert artifact["_source_outcome"]["detail"] == "Trendshift listing fetch failed"


def test_discovery_fetch_preserves_trendshift_failure(monkeypatch):
    monkeypatch.setattr(
        pipeline.trendshift,
        "fetch_trendshift",
        lambda *args, **kwargs: ([], "Trendshift listing fetch failed"),
    )
    plan = schema.DiscoveryPlan(
        domain="AI agents", category=None, subreddits=[], sources=["trendshift"],
    )

    items, error = pipeline._fetch_discovery_source(
        "trendshift", plan,
        from_date="2026-09-01", to_date="2026-09-28", depth="quick",
        mock=False, config={},
    )

    assert items == []
    assert error == "Trendshift listing fetch failed"

    monkeypatch.setattr(pipeline, "available_sources", lambda *args, **kwargs: ["trendshift"])
    report = pipeline.run_discover(
        domain="AI agents",
        config={},
        requested_sources=["trendshift"],
        as_of_date="2026-09-28",
        enrich=False,
    )
    outcome = report.source_status["trendshift"]
    assert outcome.state != schema.NO_RESULTS
    assert outcome.detail == "Trendshift listing fetch failed"


def test_rank_is_a_discovery_signal_not_ordinary_engagement():
    ranked = trendshift.parse_listing(LISTING, as_of="2026-09-28")
    items = [
        schema.SourceItem(
            item_id=raw["id"], source="trendshift", title=raw["title"],
            body="", url=raw["url"], published_at=raw["date"], engagement=raw["engagement"],
            metadata=raw["metadata"],
        )
        for raw in ranked
    ]

    assert signals.engagement_raw(items[0]) is None
    assert signals.engagement_raw(items[1]) is None
    assert (
        pipeline.rerank.discovery_signal_total(items[0])
        > pipeline.rerank.discovery_signal_total(items[1])
    )


def test_how_to_planner_keeps_configured_trendshift_source():
    available = pipeline.available_sources(
        {"INCLUDE_SOURCES": "trendshift"}, None, x_pending=False,
    )
    plan = planner.plan_query(
        topic="how to build AI agents",
        available_sources=available,
        requested_sources=None,
        depth="default",
        provider=None,
        model=None,
    )

    assert "trendshift" in plan.subqueries[0].sources


def test_trendshift_is_a_discovery_source_when_explicitly_requested():
    with mock.patch.object(pipeline.topic_shape, "is_junk_shape", return_value=False):
        report = pipeline.run_discover(
            domain="agents",
            config={},
            depth="quick",
            requested_sources=["trendshift"],
            mock=True,
            subreddits=None,
            lookback_days=30,
            enrich=False,
        )

    assert "trendshift" in report.source_status
    assert report.topics
