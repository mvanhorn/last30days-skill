import pytest

from lib import env, pipeline, reddit, reddit_public, render


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("selection", ["automatic", "requested", "hiring", "host-plan"])
def test_jobs_dispatch_respects_exclusion(monkeypatch, excluded, selection):
    dispatched = []

    def retrieve(**kwargs):
        dispatched.append(kwargs["source"])
        return [], {}

    monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)
    monkeypatch.setattr(pipeline, "which", lambda _name: None)
    config = {"EXCLUDE_SOURCES": "jobs"} if excluded else {}
    requested = ["reddit", "jobs"] if selection == "requested" else None
    if selection == "hiring":
        requested = ["reddit"]
    external_plan = None
    if selection == "host-plan":
        external_plan = {
            "intent": "research",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "topic",
            "subqueries": [{
                "label": "primary",
                "search_query": "OpenAI",
                "ranking_query": "OpenAI",
                "sources": ["reddit", "jobs"],
            }],
        }

    report = pipeline.run(
        topic="OpenAI",
        config=config,
        depth="quick",
        requested_sources=requested,
        hiring_signals_mode=selection == "hiring",
        external_plan=external_plan,
        web_backend="none",
    )

    assert "reddit" in dispatched
    assert ("jobs" in dispatched) is not excluded
    assert any("jobs" in sq.sources for sq in report.query_plan.subqueries) is not excluded
    assert any(status.source == "jobs" and status.attempted for status in report.source_status.values()) is not excluded


def test_jobs_only_hiring_run_rejects_excluded_source_before_dispatch(monkeypatch):
    dispatched = []

    def retrieve(**kwargs):
        dispatched.append(kwargs["source"])
        return [], {}

    monkeypatch.setattr(pipeline, "_retrieve_stream", retrieve)
    with pytest.raises(RuntimeError, match="No sources are available"):
        pipeline.run(
            topic="OpenAI",
            config={"EXCLUDE_SOURCES": "jobs"},
            depth="quick",
            hiring_signals_mode=True,
            web_backend="none",
        )
    assert dispatched == []


@pytest.mark.parametrize("include_match", [False, True])
@pytest.mark.parametrize("filter_kind", ["topic", "keywords"])
def test_polymarket_rejections_are_absent_from_fusion_and_output(
    monkeypatch, filter_kind, include_match,
):
    topic = "Hermes vs OpenClaw" if filter_kind == "topic" else "Warriors"
    rejected_title = "Glasgow Warriors rugby championship"
    accepted_title = "Hermes launch by December?" if filter_kind == "topic" else "Warriors win NBA finals?"
    rejected = {
        "id": "rugby",
        "title": rejected_title,
        "url": "https://polymarket.com/event/glasgow-rugby",
        "date": "2026-10-01",
        "volume1mo": 100000,
        "relevance": 1.0,
    }
    accepted = {
        "id": "accepted",
        "title": accepted_title,
        "url": "https://polymarket.com/event/relevant-market",
        "date": "2026-10-01",
        "volume1mo": 100000,
        "relevance": 1.0,
    }
    markets = [rejected, accepted] if include_match else [rejected]
    monkeypatch.setattr(pipeline, "_retrieve_stream", lambda **_kwargs: (markets, {}))
    fused_titles = []
    weighted_rrf = pipeline.weighted_rrf

    def observe_fusion(streams, *args, **kwargs):
        fused_titles.extend(item.title for items in streams.values() for item in items)
        return weighted_rrf(streams, *args, **kwargs)

    monkeypatch.setattr(pipeline, "weighted_rrf", observe_fusion)
    config = {"_polymarket_keywords": ["nba", "gsw"]} if filter_kind == "keywords" else {}
    report = pipeline.run(
        topic=topic,
        config=config,
        depth="default",
        requested_sources=["polymarket"],
        web_backend="none",
        as_of_date="2026-10-22",
        external_plan={
            "intent": "research",
            "freshness_mode": "balanced_recent",
            "cluster_mode": "topic",
            "subqueries": [{
                "label": label,
                "search_query": "Warriors Hermes",
                "ranking_query": "Warriors Hermes",
                "sources": ["polymarket"],
            } for label in ("primary", "secondary")],
        },
    )

    expected = {accepted_title} if include_match else set()
    assert {item.title for item in report.items_by_source["polymarket"]} == expected
    assert set(fused_titles) == expected
    assert {candidate.title for candidate in report.ranked_candidates} == expected
    assert report.source_status["polymarket"].items_returned == len(expected)
    assert rejected_title not in render.render_compact(report)


@pytest.mark.parametrize("freshness_mode", ["strict_recent", "balanced_recent", "evergreen_ok"])
@pytest.mark.parametrize("sc_date", ["2026-08-01", "2026-09-22", "2026-10-22", None])
def test_reddit_sc_date_filter_precedes_enrichment_and_public_fallback(
    monkeypatch, freshness_mode, sc_date,
):
    comment_requests = []
    public_requests = []
    stale_url = "https://www.reddit.com/r/music/comments/stale/old_contract/"
    recent_url = "https://www.reddit.com/r/music/comments/current/tour_schedule/"
    public_url = "https://www.reddit.com/r/music/comments/public/album_release/"
    sc_posts = [{
        "id": "stale",
        "title": "Kanye old contract dispute",
        "url": stale_url,
        "created_at": "2026-08-01",
        "subreddit": "music",
        "score": 100,
        "num_comments": 20,
    }, {
        "id": "current",
        "title": "Kanye new tour schedule released",
        "url": recent_url,
        "created_at": sc_date,
        "subreddit": "music",
        "score": 80,
        "num_comments": 10,
    }]

    def sc_response(url, **kwargs):
        assert url.startswith(reddit.SCRAPECREATORS_BASE)
        if url.endswith("/post/comments"):
            comment_requests.append(kwargs["params"]["url"])
            return {"comments": [{
                "body": "Kanye tour dates match the newly announced schedule.",
                "author": "dummy-fan",
                "score": 12,
                "created_utc": 1791504000,
            }]}
        assert url.endswith("/search")
        return {"posts": sc_posts}

    def public_response(*args, **kwargs):
        public_requests.append((args, kwargs))
        return [{
            "id": "public",
            "title": "Kanye album release announced",
            "url": public_url,
            "date": "2026-10-01",
            "subreddit": "music",
            "engagement": {"score": 100, "num_comments": 20},
            "relevance": 1.0,
        }]

    monkeypatch.setattr(reddit.http, "get", sc_response)
    monkeypatch.setattr(reddit_public, "search_reddit_public", public_response)
    report = pipeline.run(
        topic="Kanye",
        config={
            "SCRAPECREATORS_API_KEY": "dummy-scrapecreators-key",
            env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
        },
        depth="quick",
        requested_sources=["reddit"],
        web_backend="none",
        as_of_date="2026-10-22",
        external_plan={
            "intent": "research",
            "freshness_mode": freshness_mode,
            "cluster_mode": "topic",
            "subqueries": [{
                "label": "primary",
                "search_query": "Kanye",
                "ranking_query": "Kanye",
                "sources": ["reddit"],
            }],
        },
    )

    all_stale = sc_date == "2026-08-01"
    expected_url = public_url if all_stale else recent_url
    assert {item.url for item in report.items_by_source["reddit"]} == {expected_url}
    assert bool(public_requests) is all_stale
    assert comment_requests == ([] if all_stale else [recent_url])
    if not all_stale:
        assert report.items_by_source["reddit"][0].metadata["top_comments"][0]["score"] == 12
