"""Tests for digg.py - Digg source via digg-pp-cli."""

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


def test_parse_post_drops_non_http_scheme_xurl():
    """Any non-http(s) scheme on xUrl yields no post.

    A malicious Digg API response (or compromised upstream) could set xUrl to
    javascript:, data:text/html;..., file:, vbscript:, etc. The HTML report
    renders the xUrl into an <a href> attribute, so any non-web scheme is a
    stored-XSS or local-file vector when the user clicks the attribution.
    """
    for bad_url in (
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "about:blank",
    ):
        out = digg._parse_post(_post(x_url=bad_url))
        assert out is None, f"expected non-http xUrl to be dropped, got: {out}"

    # http and https remain accepted.
    assert digg._parse_post(_post(x_url="https://x.com/a/status/1")) is not None
    assert digg._parse_post(_post(x_url="http://x.com/a/status/1")) is not None


def test_parse_post_logs_unsafe_xurl_rejection_even_in_non_tty(capsys):
    """Security-class drops must be observable in non-interactive runs.

    The default ``log.source_log`` path is TTY-gated; without forcing
    ``tty_only=False`` the rejection is invisible in Claude Code runs,
    which is exactly the attack surface. Guard against silent regression.
    """
    digg._parse_post(_post(x_url="javascript:alert(1)"))
    err = capsys.readouterr().err
    assert "[Digg] dropped post with unsafe xUrl scheme" in err
    assert "javascript:alert(1)" in err


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
    assert captured["cmd"][captured["cmd"].index("--topic") + 1] == "ai,tech"


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


_REAL_FALLBACK_STDERR = (
    "falling back to local FTS5: GET https://di.gg/api/search/stories: HTTP 404: "
    '<!DOCTYPE html><html data-dpl-id="dpl_x" lang="en" class="font-sans">'
    + "x" * 500
    + "\n"
)


def _real_fallback_result() -> subproc.SubprocResult:
    """digg-pp-cli 2026.9.1 output when the live API 404s: exit 0, no meta.reason."""
    return subproc.SubprocResult(
        returncode=0,
        stdout=json.dumps(
            {
                "meta": {
                    "count": 0,
                    "query": "openai",
                    "since": "30d",
                    "source": "local",
                },
                "results": [],
            }
        ),
        stderr=_REAL_FALLBACK_STDERR,
    )


def test_search_digg_real_fallback_reports_stderr_reason(monkeypatch):
    """A live search that fell back to an empty local store is not 'no results'."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout", lambda *a, **k: _real_fallback_result()
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert out["error"] == (
        "live-search-unavailable: GET https://di.gg/api/search/stories: HTTP 404"
    )


def test_real_fallback_reason_reaches_source_outcome(monkeypatch):
    from lib import pipeline

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout", lambda *a, **k: _real_fallback_result()
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    outcome = pipeline._result_outcome_artifact("digg", out)["_source_outcome"]
    assert outcome["state"] == "error"
    assert "HTTP 404" in outcome["detail"]
    assert "<!DOCTYPE" not in outcome["detail"]


def test_local_fallback_reason_bounds_non_http_errors():
    stderr = "noise\nfalling back to local FTS5: dial tcp: " + "y" * 1000 + "\n"
    reason = digg._local_fallback_reason(stderr)
    assert reason.startswith("dial tcp: ")
    assert len(reason) <= digg._FALLBACK_REASON_MAX


def test_search_digg_empty_local_fallback_prefers_meta_reason(monkeypatch):
    """meta.reason is not emitted by digg-pp-cli 2026.9.1; prefer it if a later release adds it."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "meta": {
                        "source": "local",
                        "reason": "GET https://di.gg/api/search/stories: HTTP 404",
                    },
                    "results": [],
                }
            ),
            stderr="falling back to local FTS5: stderr reason\n",
        ),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert out["error"] == (
        "live-search-unavailable: GET https://di.gg/api/search/stories: HTTP 404"
    )


def test_search_digg_empty_local_fallback_without_reason_still_errors(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _stdout_for({"meta": {"source": "local"}, "results": []}),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "live-search-unavailable" in out["error"]


def test_search_digg_local_fallback_with_rows_keeps_results(monkeypatch):
    """A user who ran `digg-pp-cli sync` still gets their local clusters."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _stdout_for(
            {"meta": {"source": "local"}, "results": [_cluster()]}
        ),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert len(out["results"]) == 1
    assert "error" not in out


def test_search_digg_live_empty_is_still_no_results(monkeypatch):
    """A live search that genuinely found nothing stays a clean empty."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _stdout_for(
            {"meta": {"source": "live", "count": 0}, "results": []}
        ),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" not in out

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


# === digg-pp-cli 2026.10.1+ envelopes (local feed copy with meta.stale) ===


def _feed_envelope(*, source: str, stale: bool, results: list) -> subproc.SubprocResult:
    return _stdout_for(
        {
            "meta": {
                "count": len(results),
                "query": "openai",
                "since": "30d",
                "source": source,
                "stale": stale,
                "topics": ["ai", "tech"],
            },
            "results": results,
        }
    )


def test_search_digg_fresh_local_empty_is_no_matches(monkeypatch):
    """A synced store with nothing matching is a real empty answer, not a fallback."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _feed_envelope(source="local", stale=False, results=[]),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert "error" not in out


def test_search_digg_stale_result_keeps_items_and_notes_upgrade(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _feed_envelope(
            source="local+live", stale=True, results=[_cluster()]
        ),
    )
    out = digg.search_digg("openai", "2026-04-09", "2026-05-09")
    assert len(out["results"]) == 1
    assert digg.upgrade_note(out) == digg.UPGRADE_NOTE
    assert "sync --topics" not in digg.UPGRADE_NOTE


def test_old_cli_search_marks_digg_partial_in_pipeline(monkeypatch):
    from lib import health, pipeline, schema

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: _feed_envelope(
            source="local+live", stale=True, results=[_cluster()]
        ),
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="openai",
        ranking_query="openai",
        sources=["digg"],
    )
    items, artifact = pipeline._retrieve_stream_impl(
        topic="openai",
        subquery=subquery,
        source="digg",
        config={},
        depth="default",
        date_range=("2026-04-09", "2026-05-09"),
        runtime=None,
        mock=False,
    )
    assert len(items) == 1
    outcome = artifact["_source_outcome"]
    assert outcome["state"] == health.PARTIAL
    assert "upgrade" in outcome["detail"]
    assert "AI and tech" in outcome["detail"]


def test_old_cli_partial_with_no_items_finalizes_as_no_results():
    """An old CLI search that matched nothing keeps its upgrade note and is not an error."""
    from lib import health, pipeline, schema

    outcomes = {
        "digg": schema.SourceOutcome(
            source="digg",
            state=health.PARTIAL,
            items_returned=0,
            attempted=True,
            detail=digg.UPGRADE_NOTE,
        ),
        "reddit": schema.SourceOutcome(
            source="reddit",
            state=health.PARTIAL,
            items_returned=0,
            attempted=True,
            detail="HTTP 503 from upstream",
        ),
    }
    final = pipeline._finalize_source_status(outcomes, {})
    assert final["digg"].state == schema.NO_RESULTS
    assert final["digg"].detail == digg.UPGRADE_NOTE
    assert final["reddit"].state != schema.NO_RESULTS


# === digg-pp-cli 2026.10.1 search answer, captured from the real binary ===

_CLI_2026_10_1_ANSWER = {
    "meta": {
        "count": 3,
        "query": "openai",
        "since": "30d",
        "source": "live",
        "stale": True,
        "topics": ["ai", "tech"],
    },
    "results": [
        {
            "clusterId": "6043f14d-1717-4b75-8fb2-3a596fe44df5",
            "clusterUrlId": "ljvbajl5",
            "firstPostAge": "16d",
            "postCount": 127,
            "rank": 7,
            "title": "OpenAI announces claimed AI proofs in math, computer science and physics",
            "tldr": "OpenAI says its claimed refutation of the Exponential Time Hypothesis was formalized in Lean and made public.",
            "uniqueAuthors": 96,
        },
        {
            "clusterId": "4e6ae3f7-6a4a-47c3-803c-0bbd1f6c992b",
            "clusterUrlId": "g7fcfxf4",
            "firstPostAge": "29d",
            "postCount": 147,
            "rank": 8,
            "title": "Hidden recording device found in OpenAI office; employee fired and charged",
            "tldr": "OpenAI says its models helped identify the employee who planted the device; the FBI made an arrest.",
            "uniqueAuthors": 101,
        },
        {
            "clusterId": "2c90283c-21fa-47be-abaa-d93cf0609de9",
            "clusterUrlId": "x7hzsqzf",
            "firstPostAge": "19d",
            "postCount": 29,
            "rank": 70,
            "title": "OpenAI red-team agents reportedly exchanged synthetic secrets through processor-cache timing",
            "tldr": "OpenAI reported hardware side channels; AndrewCurran_ quoted its account of agents passing synthetic secrets between virtual machines.",
            "uniqueAuthors": 23,
        },
    ],
}

_CAPTURE_DAY = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def test_cli_2026_10_1_answer_parses_to_the_same_items(monkeypatch):
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    items = digg.parse_digg_response(_CLI_2026_10_1_ANSWER, query="openai")
    assert items == [
        {
            "id": "ljvbajl5",
            "title": "OpenAI announces claimed AI proofs in math, computer science and physics",
            "url": "https://di.gg/ai/ljvbajl5",
            "tldr": "OpenAI says its claimed refutation of the Exponential Time Hypothesis was formalized in Lean and made public.",
            "author": "",
            "date": "2026-10-26",
            "engagement": {"postCount": 127, "uniqueAuthors": 96, "rank": 7, "rank_score": 44.0},
            "first_post_age": "16d",
            "posts": [],
            "relevance": 1.0,
            "why_relevant": "Digg cluster (rank 7, 127 posts, 96 authors)",
        },
        {
            "id": "g7fcfxf4",
            "title": "Hidden recording device found in OpenAI office; employee fired and charged",
            "url": "https://di.gg/ai/g7fcfxf4",
            "tldr": "OpenAI says its models helped identify the employee who planted the device; the FBI made an arrest.",
            "author": "",
            "date": "2026-10-13",
            "engagement": {"postCount": 147, "uniqueAuthors": 101, "rank": 8, "rank_score": 43.0},
            "first_post_age": "29d",
            "posts": [],
            "relevance": 1.0,
            "why_relevant": "Digg cluster (rank 8, 147 posts, 101 authors)",
        },
        {
            "id": "x7hzsqzf",
            "title": "OpenAI red-team agents reportedly exchanged synthetic secrets through processor-cache timing",
            "url": "https://di.gg/ai/x7hzsqzf",
            "tldr": "OpenAI reported hardware side channels; AndrewCurran_ quoted its account of agents passing synthetic secrets between virtual machines.",
            "author": "",
            "date": "2026-10-23",
            "engagement": {"postCount": 29, "uniqueAuthors": 23, "rank": 70, "rank_score": 0.0},
            "first_post_age": "19d",
            "posts": [],
            "relevance": 0.88,
            "why_relevant": "Digg cluster (rank 70, 29 posts, 23 authors)",
        },
    ]


def test_cli_2026_10_1_answer_normalizes_to_the_same_source_item(monkeypatch):
    from lib import normalize

    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    item = digg.parse_digg_response(_CLI_2026_10_1_ANSWER, query="openai")[0]
    source_item = normalize._normalize_digg("digg", item, 0, "2026-10-12", "2026-11-11")
    assert source_item.item_id == "ljvbajl5"
    assert source_item.url == "https://di.gg/ai/ljvbajl5"
    assert source_item.published_at == "2026-10-26"
    assert source_item.engagement == {
        "postCount": 127,
        "uniqueAuthors": 96,
        "rank": 7,
        "rank_score": 44.0,
    }
    assert source_item.metadata == {
        "clusterUrlId": "ljvbajl5",
        "tldr": "OpenAI says its claimed refutation of the Exponential Time Hypothesis was formalized in Lean and made public.",
        "rank": 7,
        "uniqueAuthors": 96,
        "postCount": 127,
        "firstPostAge": "16d",
        "posts": [],
    }


def _search_calls(calls: list) -> list:
    return [cmd for cmd in calls if "--help" not in cmd]


def test_old_cli_gets_the_ai_and_tech_call_unchanged(monkeypatch):
    calls: list = []

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        calls.append(list(cmd))
        return _stdout_for(_CLI_2026_10_1_ANSWER)

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    digg.search_digg("openai", "2026-10-12", "2026-11-11")
    assert _search_calls(calls) == [
        [
            "digg-pp-cli",
            "search",
            "openai",
            "--topic",
            "ai,tech",
            "--since",
            "30d",
            "--agent",
            "--limit",
            "20",
        ]
    ]


# === All-topic search (digg-pp-cli releases whose search lists --sort) ===

_NEW_SEARCH_HELP = (
    "Search Digg stories.\n\nFlags:\n"
    "      --limit int       Max number of results\n"
    "      --since string    Window start\n"
    "      --sort string     relevance, score or newest (default relevance)\n"
    "      --topic strings   Topics to search (default every topic)\n"
    "      --until string    Window end\n"
)
_OLD_SEARCH_HELP = (
    "Search Digg stories by title and summary.\n\nFlags:\n"
    "      --limit int       Max number of results (default 20)\n"
    "      --since string    Filter to stories that started within this window.\n"
    "      --topic strings   Comma-separated Digg sections to search (default [ai])\n"
)


@pytest.fixture(autouse=True)
def _forget_cli_capability(monkeypatch):
    """Each test starts as a fresh run that has not yet read the CLI's help."""
    monkeypatch.setattr(digg, "_all_topic_search", None)


def _story(
    short_id: str = "k3v9xq2m",
    topic: str = "science",
    title: str = "Webb telescope spots water on a rocky exoplanet",
    published_at: str = "2026-11-08T15:02:00Z",
    **overrides,
):
    story = {
        "clusterId": f"11111111-2222-3333-4444-{short_id:0>12}",
        "clusterUrlId": short_id,
        "url": f"https://digg.com/{topic}/{short_id}",
        "topic": topic,
        "title": title,
        "description": "Longer description of the story.",
        "tldr": "Astronomers report water vapor in the planet's atmosphere.",
        "diggScore": 412,
        "sourceCount": 37,
        "postCount": 37,
        "uniqueAuthors": 29,
        "publishedAt": published_at,
        "firstSourceAt": "2026-11-08T13:48:00Z",
        "firstPostAge": "3d",
    }
    story.update(overrides)
    return story


def _live_envelope(*stories: dict) -> dict:
    return {
        "meta": {
            "count": len(stories),
            "query": "exoplanet",
            "source": "live",
            "stale": False,
            "totalMatches": len(stories),
            "window": {"since": "2026-10-12", "until": "2026-11-11"},
            "sort": "relevance",
        },
        "results": list(stories),
    }


def _fake_cli(help_text: str, answer: dict, calls: list):
    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        calls.append(list(cmd))
        if "--help" in cmd:
            return subproc.SubprocResult(returncode=0, stdout=help_text, stderr="")
        return _stdout_for(answer)

    return fake_run


def test_story_keeps_its_own_url_score_counts_and_publish_date():
    items = digg.parse_digg_response(
        _live_envelope(_story()), query="exoplanet", from_date="2026-10-12"
    )
    assert len(items) == 1
    item = items[0]
    assert item["id"] == "k3v9xq2m"
    assert item["url"] == "https://digg.com/science/k3v9xq2m"
    assert item["date"] == "2026-11-08"
    assert item["engagement"] == {"postCount": 37, "uniqueAuthors": 29, "digg_score": 412}


def test_story_post_count_is_the_source_count():
    items = digg.parse_digg_response(
        _live_envelope(_story(sourceCount=52, postCount=37)), from_date="2026-10-12"
    )
    assert items[0]["engagement"]["postCount"] == 52


def test_sports_story_links_to_the_sports_section():
    items = digg.parse_digg_response(
        _live_envelope(_story(short_id="w8n2jd4c", topic="sports", title="Cup final goes to penalties")),
        from_date="2026-10-12",
    )
    assert items[0]["url"] == "https://digg.com/sports/w8n2jd4c"
    assert "/ai/" not in items[0]["url"]


def test_story_without_a_score_keeps_counts_and_has_no_score():
    story = _story()
    del story["diggScore"]
    del story["firstSourceAt"]
    items = digg.parse_digg_response(_live_envelope(story), from_date="2026-10-12")
    assert items[0]["engagement"] == {"postCount": 37, "uniqueAuthors": 29}


def test_story_published_40_days_ago_is_dropped_from_a_30_day_run(monkeypatch):
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    envelope = _live_envelope(
        _story(short_id="recent01", published_at="2026-11-08T15:02:00Z"),
        _story(short_id="old00001", published_at="2026-10-02T09:00:00Z", firstPostAge="5w"),
    )
    with_window = digg.parse_digg_response(envelope, from_date="2026-10-12")
    assert [i["id"] for i in with_window] == ["recent01"]
    default_window = digg.parse_digg_response(envelope)
    assert [i["id"] for i in default_window] == ["recent01"]


def test_story_with_a_non_web_url_is_dropped():
    envelope = _live_envelope(
        _story(short_id="bad00001", url="javascript:alert(1)"),
        _story(short_id="good0001"),
    )
    items = digg.parse_digg_response(envelope, from_date="2026-10-12")
    assert [i["id"] for i in items] == ["good0001"]


def test_story_metadata_carries_uuid_topic_and_canonical_url():
    from lib import normalize

    item = digg.parse_digg_response(
        _live_envelope(_story()), query="exoplanet", from_date="2026-10-12"
    )[0]
    source_item = normalize._normalize_digg("digg", item, 0, "2026-10-12", "2026-11-11")
    assert source_item.url == "https://digg.com/science/k3v9xq2m"
    assert source_item.published_at == "2026-11-08"
    assert source_item.engagement == {"postCount": 37, "uniqueAuthors": 29, "digg_score": 412}
    assert source_item.metadata["clusterId"] == "11111111-2222-3333-4444-0000k3v9xq2m"
    assert source_item.metadata["clusterUrlId"] == "k3v9xq2m"
    assert source_item.metadata["topic"] == "science"
    assert source_item.metadata["url"] == "https://digg.com/science/k3v9xq2m"


def test_new_cli_search_names_no_topic_and_no_sort(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(_NEW_SEARCH_HELP, _live_envelope(_story()), calls),
    )
    out = digg.search_digg("exoplanet", "2026-10-12", "2026-11-11")
    assert _search_calls(calls) == [
        ["digg-pp-cli", "search", "exoplanet", "--since", "30d", "--agent", "--limit", "20"]
    ]
    assert len(out["results"]) == 1
    assert digg.upgrade_note(out) is None


def test_deep_search_asks_for_50_stories(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(_NEW_SEARCH_HELP, _live_envelope(), calls),
    )
    digg.search_digg("exoplanet", "2026-10-12", "2026-11-11", depth="deep")
    search = _search_calls(calls)[0]
    assert search[search.index("--limit") + 1] == "50"


def test_search_help_is_read_once_per_run(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(_OLD_SEARCH_HELP, _CLI_2026_10_1_ANSWER, calls),
    )
    digg.search_digg("openai", "2026-10-12", "2026-11-11")
    digg.search_digg("anthropic", "2026-10-12", "2026-11-11")
    help_calls = [cmd for cmd in calls if "--help" in cmd]
    assert help_calls == [["digg-pp-cli", "search", "--help"]]
    assert [cmd[2] for cmd in _search_calls(calls)] == ["openai", "anthropic"]
    assert all("--topic" in cmd for cmd in _search_calls(calls))


def test_old_cli_answer_yields_the_same_items_plus_the_upgrade_note(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(_OLD_SEARCH_HELP, _CLI_2026_10_1_ANSWER, calls),
    )
    out = digg.search_digg("openai", "2026-10-12", "2026-11-11")
    items = digg.parse_digg_response(out, query="openai", from_date="2026-10-12")
    assert items == digg.parse_digg_response(_CLI_2026_10_1_ANSWER, query="openai")
    assert [i["url"] for i in items] == [
        "https://di.gg/ai/ljvbajl5",
        "https://di.gg/ai/g7fcfxf4",
        "https://di.gg/ai/x7hzsqzf",
    ]
    note = digg.upgrade_note(out)
    assert note is not None
    assert "upgrade" in note
    assert len(note) < 140


def test_upgrade_note_absent_for_failed_and_unmarked_answers(monkeypatch):
    assert digg.upgrade_note({"results": []}) is None
    assert digg.upgrade_note(_CLI_2026_10_1_ANSWER) is None
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(returncode=2, stdout="", stderr="boom"),
    )
    out = digg.search_digg("openai", "2026-10-12", "2026-11-11")
    assert out["error"] == "boom"
    assert digg.upgrade_note(out) is None


def test_new_cli_stories_reach_the_pipeline_without_a_partial_note(monkeypatch):
    from lib import pipeline, schema

    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(
            _NEW_SEARCH_HELP,
            _live_envelope(
                _story(short_id="recent01"),
                _story(short_id="old00001", published_at="2026-10-02T09:00:00Z"),
            ),
            [],
        ),
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="exoplanet",
        ranking_query="exoplanet",
        sources=["digg"],
    )
    items, artifact = pipeline._retrieve_stream_impl(
        topic="exoplanet",
        subquery=subquery,
        source="digg",
        config={},
        depth="default",
        date_range=("2026-10-12", "2026-11-11"),
        runtime=None,
        mock=False,
    )
    assert [item["id"] for item in items] == ["recent01"]
    assert items[0]["url"] == "https://digg.com/science/recent01"
    assert "_source_outcome" not in artifact
