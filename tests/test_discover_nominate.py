"""U1 - nominate stage: river/listing candidate discovery.

Covers the two behaviors the nominate stage adds over the old inline sweep:
a ``keyword_gate`` toggle (domain scoping vs global trending) and
fault-tolerant per-source failure recording that never raises.
"""

from unittest import mock

from lib import pipeline, reddit_listing, schema


def _plan(domain: str, sources: list[str], subreddits: list[str] | None = None) -> schema.DiscoveryPlan:
    return schema.DiscoveryPlan(
        domain=domain,
        category=None,
        subreddits=subreddits or ["all"],
        sources=sources,
    )


def test_fetch_discovery_source_reddit_gate_filters_off_domain():
    """With the keyword gate on, off-domain listing items are dropped; with it
    off (global trending), the feed's own hot ranking is kept verbatim."""
    plan = _plan("AI agents", ["reddit"])
    payload = {
        "items": [
            {"title": "New AI agents framework launched", "selftext": ""},
            {"title": "Gardening tips for spring", "selftext": ""},
        ],
        "errors": [],
    }
    with mock.patch.object(reddit_listing, "fetch_discovery_listings", return_value=payload):
        gated, _ = pipeline._fetch_discovery_source(
            "reddit", plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, keyword_gate=True,
        )
        ungated, _ = pipeline._fetch_discovery_source(
            "reddit", plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, keyword_gate=False,
        )

    assert [item["title"] for item in gated] == ["New AI agents framework launched"]
    assert len(ungated) == 2


def test_nominate_candidates_threads_keyword_gate():
    """nominate_candidates forwards its keyword_gate to each source fetch, so a
    global (no-domain) run really does disable the gate."""
    plan = _plan("", ["reddit"])
    seen: dict[str, bool] = {}

    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        seen["keyword_gate"] = keyword_gate
        return [], None

    with mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        pipeline.nominate_candidates(
            plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, lookback_days=30,
            keyword_gate=False,
        )

    assert seen["keyword_gate"] is False


def test_nominate_candidates_records_source_failure_without_raising():
    """One dead feed is recorded on the bundle as a failure; the surviving feed
    still yields candidates, and the call never raises."""
    plan = _plan("AI agents", ["reddit", "hackernews"])

    def fake_fetch(source, plan, *, from_date, to_date, depth, mock, config, keyword_gate=True):
        if source == "hackernews":
            raise TimeoutError("hn listing timed out")
        return pipeline._mock_discovery_items(source, plan.domain, to_date), None

    with mock.patch.object(pipeline, "_fetch_discovery_source", side_effect=fake_fetch):
        bundle = pipeline.nominate_candidates(
            plan,
            from_date="2026-06-10", to_date="2026-07-10",
            depth="default", mock=False, config={}, lookback_days=30,
        )

    # Surviving source produced candidates.
    assert bundle.items_by_source.get("reddit")
    # Dead source recorded as a failure, not silently dropped or raised.
    hackernews = bundle.source_status["hackernews"]
    assert hackernews.state not in (schema.NO_RESULTS,)
    assert "ok" != hackernews.state
