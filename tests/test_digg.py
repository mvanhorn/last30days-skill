"""Tests for digg.py - Digg AI 1000 source via digg-pp-cli."""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from lib import digg
from lib import subproc

# === Helpers ===


def _cluster(
    cluster_url_id: str = "abc123xy",
    title: str = "Sample cluster",
    tldr: str = "A short summary of what is happening.",
    rank: int = 1,
    post_count: int = 5,
    unique_authors: int = 3,
    first_post_age: str = "5d",
):
    return {
        "clusterUrlId": cluster_url_id,
        "clusterId": f"uuid-{cluster_url_id}",
        "title": title,
        "tldr": tldr,
        "rank": rank,
        "postCount": post_count,
        "uniqueAuthors": unique_authors,
        "firstPostAge": first_post_age,
    }


def _post(
    username: str = "someone",
    body: str = "Some body text about the topic.",
    rank: int = 100,
    category: str = "Engineer",
    post_type: str = "tweet",
    x_url: str | None = None,
):
    return {
        "author": {
            "username": username,
            "display_name": username.title(),
            "category": category,
            "rank": rank,
        },
        "body": body,
        "post_type": post_type,
        "xUrl": x_url or f"https://x.com/{username}/status/1234567890",
        "posted_at": "2026-05-01T12:00:00+00:00",
    }


def _stdout_for(payload: dict) -> subproc.SubprocResult:
    return subproc.SubprocResult(returncode=0, stdout=json.dumps(payload), stderr="")

# === _parse_first_post_age ===


def test_parse_first_post_age_days():
    today = datetime(2026, 5, 9, tzinfo=timezone.utc)
    assert digg._parse_first_post_age("5d", today=today) == "2026-05-04"


def test_parse_first_post_age_hours_returns_today():
    today = datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc)
    assert digg._parse_first_post_age("5h", today=today) == "2026-05-09"


def test_parse_first_post_age_weeks():
    today = datetime(2026, 5, 9, tzinfo=timezone.utc)
    assert digg._parse_first_post_age("2w", today=today) == "2026-04-25"


def test_parse_first_post_age_months_inside_window():
    today = datetime(2026, 5, 9, tzinfo=timezone.utc)
    # 1 month = 30 days exactly, still inside the 30-day window.
    assert digg._parse_first_post_age("1m", today=today) == (today - timedelta(days=30)).date().isoformat()


def test_parse_first_post_age_outside_30d_returns_none():
    today = datetime(2026, 5, 9, tzinfo=timezone.utc)
    assert digg._parse_first_post_age("2m", today=today) is None
    assert digg._parse_first_post_age("31d", today=today) is None


def test_parse_first_post_age_invalid():
    assert digg._parse_first_post_age(None) is None
    assert digg._parse_first_post_age("") is None
    assert digg._parse_first_post_age("garbage") is None
    assert digg._parse_first_post_age("5x") is None
    assert digg._parse_first_post_age("d") is None
    assert digg._parse_first_post_age("-3d") is None

# === parse_digg_response ===


def test_parse_response_happy_path():
    response = {
        "results": [
            _cluster(cluster_url_id="aaa", title="First", rank=1),
            _cluster(cluster_url_id="bbb", title="Second", rank=4),
            _cluster(cluster_url_id="ccc", title="Third", rank=12),
        ]
    }
    items = digg.parse_digg_response(response)
    assert len(items) == 3
    ids = [i["id"] for i in items]
    assert ids == ["aaa", "bbb", "ccc"]
    for item in items:
        assert item["url"].startswith("https://di.gg/ai/")
        assert item["engagement"]["postCount"] == 5
        assert item["engagement"]["uniqueAuthors"] == 3
        assert item["engagement"]["rank"] in (1, 4, 12)
        assert item["posts"] == []
        assert item["date"] is not None


def test_parse_response_empty():
    assert digg.parse_digg_response({"results": []}) == []
    assert digg.parse_digg_response({}) == []
    assert digg.parse_digg_response({"results": "not-a-list"}) == []


def test_parse_response_drops_missing_id():
    response = {
        "results": [
            {"title": "no id", "tldr": "x", "postCount": 1, "uniqueAuthors": 1, "firstPostAge": "1d"},
            _cluster(cluster_url_id="ok", title="ok"),
        ]
    }
    items = digg.parse_digg_response(response)
    assert [i["id"] for i in items] == ["ok"]


def test_parse_response_drops_clusters_outside_30d():
    response = {
        "results": [
            _cluster(cluster_url_id="recent", first_post_age="2d"),
            _cluster(cluster_url_id="ancient", first_post_age="2m"),
        ]
    }
    items = digg.parse_digg_response(response)
    assert [i["id"] for i in items] == ["recent"]


def test_parse_response_keeps_cluster_when_age_missing():
    # When firstPostAge is absent or empty, we don't have evidence to drop;
    # keep the cluster with date=None and let date-confidence downgrade it.
    response = {
        "results": [
            {**_cluster(cluster_url_id="noage"), "firstPostAge": None},
        ]
    }
    items = digg.parse_digg_response(response)
    assert len(items) == 1
    assert items[0]["date"] is None


def test_parse_response_relevance_with_query():
    response = {
        "results": [
            _cluster(cluster_url_id="match", title="OpenClaw launch", tldr="OpenClaw shipped today"),
            _cluster(cluster_url_id="nomatch", title="Cricket scores", tldr="Mumbai vs Delhi"),
        ]
    }
    items = digg.parse_digg_response(response, query="OpenClaw")
    by_id = {i["id"]: i for i in items}
    assert by_id["match"]["relevance"] > by_id["nomatch"]["relevance"]


def test_parse_response_engagement_rank_score():
    response = {
        "results": [
            _cluster(cluster_url_id="top", rank=1),
            _cluster(cluster_url_id="off-leaderboard", rank=999),
        ]
    }
    items = digg.parse_digg_response(response)
    by_id = {i["id"]: i for i in items}
    assert by_id["top"]["engagement"]["rank_score"] == 50.0
    assert by_id["off-leaderboard"]["engagement"]["rank_score"] == 0.0

# === _parse_post ===


def test_parse_post_happy():
    out = digg._parse_post(_post(username="adam", body="Hello world"))
    assert out is not None
    assert out["username"] == "adam"
    assert out["body"] == "Hello world"
    assert out["x_url"].startswith("https://x.com/")


def test_parse_post_drops_missing_body_or_handle_or_url():
    assert digg._parse_post({"author": {"username": "x"}, "body": "", "xUrl": "u"}) is None
    assert digg._parse_post({"author": {}, "body": "txt", "xUrl": "u"}) is None
    assert digg._parse_post({"author": {"username": "x"}, "body": "txt", "xUrl": ""}) is None
    assert digg._parse_post(None) is None  # type: ignore[arg-type]

# === _run_cli / search_digg with stubbed subprocess ===


def test_search_digg_binary_missing_returns_empty(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: None)
    out = digg.search_digg("anything", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" in out


def test_search_digg_passes_since_30d(monkeypatch):
    captured: dict = {}

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        captured["cmd"] = list(cmd)
        return _stdout_for({"results": []})

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert "--since" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--since") + 1] == "30d"
    assert "--agent" in captured["cmd"]
    assert captured["cmd"][:3] == [digg.CLI_BIN, "search", "openclaw"]


def test_search_digg_subproc_timeout_returns_empty(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")

    def fake_run(*_a, **_kw):
        raise subproc.SubprocTimeout("boom")

    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    out = digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" in out


def test_search_digg_nonzero_exit_returns_empty(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(returncode=2, stdout="", stderr="cluster not found"),
    )
    out = digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" in out


def test_search_digg_invalid_json_returns_empty(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(returncode=0, stdout="not json", stderr=""),
    )
    out = digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" in out


def test_search_digg_empty_query_short_circuits(monkeypatch):
    called = MagicMock()
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", called)
    out = digg.search_digg("", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    called.assert_not_called()

# === enrich_with_top_posts ===


def test_enrich_with_top_posts_attaches_posts(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        # cmd = ['digg-pp-cli', 'posts', '<urlId>', '--agent', '--by', 'rank', '--limit', '3']
        cluster_url_id = cmd[2]
        return _stdout_for(
            {
                "results": [
                    _post(username=f"u_{cluster_url_id}", body=f"body for {cluster_url_id}"),
                    _post(username=f"v_{cluster_url_id}", body=f"second for {cluster_url_id}"),
                ]
            }
        )

    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)

    items = [
        {"id": "aaa", "engagement": {"postCount": 5}, "posts": []},
        {"id": "bbb", "engagement": {"postCount": 3}, "posts": []},
        {"id": "ccc", "engagement": {"postCount": 9}, "posts": []},
        {"id": "ddd", "engagement": {"postCount": 1}, "posts": []},
    ]
    digg.enrich_with_top_posts(items, top_k=2, posts_per=3)
    assert len(items[0]["posts"]) == 2
    assert items[0]["posts"][0]["username"] == "u_aaa"
    assert len(items[1]["posts"]) == 2
    assert items[2]["posts"] == []  # not enriched (top_k=2)


def test_enrich_skips_zero_postcount(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    fake = MagicMock(return_value=_stdout_for({"results": [_post()]}))
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake)

    items = [
        {"id": "no-posts", "engagement": {"postCount": 0}, "posts": []},
        {"id": "ok", "engagement": {"postCount": 7}, "posts": []},
    ]
    digg.enrich_with_top_posts(items, top_k=2, posts_per=3)
    assert items[0]["posts"] == []
    assert len(items[1]["posts"]) == 1


def test_enrich_partial_timeout_does_not_break_others(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    call_count = {"n": 0}

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise subproc.SubprocTimeout("boom")
        return _stdout_for({"results": [_post(username=f"u{call_count['n']}")]})

    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)

    items = [
        {"id": "a", "engagement": {"postCount": 3}, "posts": []},
        {"id": "b", "engagement": {"postCount": 3}, "posts": []},
        {"id": "c", "engagement": {"postCount": 3}, "posts": []},
    ]
    digg.enrich_with_top_posts(items, top_k=3, posts_per=3)
    assert len(items[0]["posts"]) == 1
    assert items[1]["posts"] == []  # timed out
    assert len(items[2]["posts"]) == 1


def test_enrich_top_k_zero_skips_all(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake)
    items = [{"id": "a", "engagement": {"postCount": 5}, "posts": []}]
    digg.enrich_with_top_posts(items, top_k=0)
    fake.assert_not_called()

# === enrich_source_items (post-dedupe path) ===


class _FakeSourceItem:
    def __init__(self, source, item_id, engagement, metadata):
        self.source = source
        self.item_id = item_id
        self.engagement = engagement
        self.metadata = metadata


def test_enrich_source_items_attaches_to_survivors(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda cmd, *, timeout, env=None, on_pid=None: _stdout_for(
            {"results": [_post(username=f"u_{cmd[2]}")]}
        ),
    )
    items = [
        _FakeSourceItem("digg", "ID1", {"postCount": 4}, {"clusterUrlId": "ID1", "posts": []}),
        _FakeSourceItem("digg", "ID2", {"postCount": 6}, {"clusterUrlId": "ID2", "posts": []}),
        _FakeSourceItem("digg", "ID3", {"postCount": 8}, {"clusterUrlId": "ID3", "posts": []}),
    ]
    digg.enrich_source_items(items, top_k=2)
    assert items[0].metadata["posts"][0]["username"] == "u_ID1"
    assert items[1].metadata["posts"][0]["username"] == "u_ID2"
    assert items[2].metadata["posts"] == []


def test_enrich_source_items_skips_non_digg(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake)
    items = [_FakeSourceItem("hackernews", "HN1", {"points": 100}, {"posts": []})]
    digg.enrich_source_items(items, top_k=3)
    fake.assert_not_called()


def test_enrich_source_items_falls_back_to_item_id(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    captured = {}

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        captured["cluster_id"] = cmd[2]
        return _stdout_for({"results": [_post()]})

    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    items = [_FakeSourceItem("digg", "fallbackid", {"postCount": 3}, {"posts": []})]
    digg.enrich_source_items(items, top_k=1)
    assert captured["cluster_id"] == "fallbackid"

# === Live tests (opt-in) ===

LIVE = os.environ.get("LAST30DAYS_DIGG_LIVE", "").lower() in ("1", "true", "yes")
HAVE_BIN = shutil.which(digg.CLI_BIN) is not None

@pytest.mark.skipif(not (LIVE and HAVE_BIN), reason="LAST30DAYS_DIGG_LIVE not set or digg-pp-cli missing")


class TestLiveDigg:
    def test_search_returns_clusters(self):
        out = digg.search_digg("claude code", "2026-04-09", "2026-05-09", depth="quick")
        assert "results" in out
        assert isinstance(out["results"], list)
        # Topic should produce at least 1 cluster in the last 30d.
        assert len(out["results"]) >= 1
        sample = out["results"][0]
        for key in ("clusterUrlId", "title", "firstPostAge", "postCount"):
            assert key in sample

    def test_parse_then_enrich_roundtrip(self):
        raw = digg.search_digg("claude code", "2026-04-09", "2026-05-09", depth="quick")
        items = digg.parse_digg_response(raw, query="claude code")
        assert items, "expected at least one parsed cluster"
        digg.enrich_with_top_posts(items, top_k=1, posts_per=2)
        # Either the top cluster was successfully enriched, or it was a 0-post
        # cluster and posts stayed empty. Both are valid; we just want no crash.
        assert isinstance(items[0]["posts"], list)

    def test_off_topic_returns_list(self):
        # Digg's live search uses fuzzy/popularity fallback so an impossible
        # token may return clusters Digg considers loosely related rather
        # than an empty list. The contract we depend on is shape: results
        # must always be a list. Token-overlap relevance later in the
        # pipeline filters off-topic noise.
        out = digg.search_digg("ksdjflksjdflkjsdf-impossible-token", "2026-04-09", "2026-05-09", depth="quick")
        assert isinstance(out.get("results"), list)

    def test_missing_cluster_id_graceful(self):
        posts = digg.fetch_top_posts("notarealclusterid", posts_per=2)
        assert posts == []

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
