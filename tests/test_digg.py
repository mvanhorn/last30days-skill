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


def test_search_digg_unknown_flag_names_an_outdated_helper_and_its_upgrade(monkeypatch):
    from lib import health, pipeline

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(
            returncode=1, stdout="", stderr="Error: unknown flag: --topic\nUsage: ..."
        ),
    )
    out = digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert out["results"] == []
    assert out["error"] == (
        f"digg-pp-cli is outdated and rejected the search; upgrade: {health.helper_upgrade_cmd('digg')}"
    )
    assert len(out["error"]) <= 140
    outcome = pipeline._result_outcome_artifact("digg", out)
    assert outcome["_source_outcome"]["detail"] == out["error"]
    assert outcome["_source_outcome"]["state"] != health.OK


def test_search_digg_other_nonzero_exit_keeps_its_own_error(monkeypatch):
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        lambda *a, **k: subproc.SubprocResult(returncode=2, stdout="", stderr="cluster not found"),
    )
    out = digg.search_digg("openclaw", "2026-04-09", "2026-05-09")
    assert out["error"] == "cluster not found"
    assert digg.outdated_cli_error(out["error"]) is None


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
    def fake_run(cmd, *, timeout, **_bounds):
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


def test_story_reason_names_its_score_and_counts():
    items = digg.parse_digg_response(_live_envelope(_story()), from_date="2026-10-12")
    assert items[0]["why_relevant"] == "Digg cluster (Digg Score 412, 37 posts, 29 authors)"


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


# === Story sources and confirmation of matching items ===


def _source_row(
    kind: str = "x",
    url: str = "https://x.com/astro_ana/status/1986000000000000001",
    username: str = "astro_ana",
    body: str = "Water vapor on a rocky world. Big if it holds up.",
):
    author = {"display_name": username.title() if username else "Example Newsroom"}
    if kind == "x":
        author["username"] = username
    return {
        "kind": kind,
        "body": body,
        "xUrl": url,
        "title": "" if kind == "x" else "Webb finds water on a rocky exoplanet",
        "posted_at": "2026-11-08T14:00:00Z",
        "post_x_id": url.rsplit("/", 1)[-1] if kind == "x" else "",
        "author": author,
    }


def _sources_answer(*rows: dict) -> dict:
    return {"meta": {"topic": "science"}, "results": list(rows)}


def _story_item(short_id: str = "k3v9xq2m", digg_score: float = 412, engagement_score=80, **story):
    from lib import normalize

    [parsed] = digg.parse_digg_response(
        _live_envelope(_story(short_id=short_id, diggScore=digg_score, **story)),
        query="exoplanet",
        from_date="2026-10-12",
    )
    [item] = normalize.normalize_source_items("digg", [parsed], "2026-10-12", "2026-11-11")
    item.engagement_score = engagement_score
    return item


def _other_item(source: str, url: str, **metadata):
    from lib import schema

    return schema.SourceItem(
        item_id=f"{source}-1", source=source, title="A post", body="A post", url=url,
        published_at="2026-11-09", metadata=dict(metadata),
    )


def _candidate_for(*items):
    from lib import schema

    first = items[0]
    return schema.Candidate(
        candidate_id=first.url, item_id=first.item_id, source=first.source,
        title=first.title, url=first.url, snippet="", subquery_labels=["primary"],
        native_ranks={f"primary:{first.source}": 1}, local_relevance=0.6, freshness=70,
        engagement=30, source_quality=0.7, rrf_score=0.02, source_items=list(items),
    )


def _sources_cli(answers: dict, calls: list):
    """A CLI whose ``posts <uuid>`` answers come from ``answers`` by UUID."""

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        calls.append(list(cmd))
        return _stdout_for(answers.get(cmd[2], _sources_answer()))

    return fake_run


def _confirm(story, rows, *others, depth: str = "default", monkeypatch):
    """Fetch ``story``'s sources from a fake CLI and tag ``others``."""
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _sources_cli({story.metadata["clusterId"]: _sources_answer(*rows)}, []),
    )
    fetched = digg.fetch_story_sources([story], depth)
    stories = digg.apply_story_sources([story], fetched)
    candidates = [_candidate_for(item) for item in others]
    digg.tag_confirmed_candidates(candidates, stories)
    return candidates


@pytest.mark.parametrize("item_url", [
    "https://twitter.com/astro_ana/status/1986000000000000001",
    "https://x.com/Astro_Ana/status/1986000000000000001",
    "https://x.com/astro_ana/status/1986000000000000001?s=20&t=abcDEF",
    "https://x.com/i/status/1986000000000000001",
])
def test_x_item_with_a_story_sources_status_id_is_tagged(monkeypatch, item_url):
    story = _story_item()
    [candidate] = _confirm(
        story, [_source_row()], _other_item("x", item_url), monkeypatch=monkeypatch,
    )
    assert candidate.metadata["digg_story"] == {
        "title": "Webb telescope spots water on a rocky exoplanet",
        "url": "https://digg.com/science/k3v9xq2m",
        "digg_score": 412,
        "source_count": 37,
        "engagement": 80,
    }


def test_x_item_with_another_status_id_is_not_tagged(monkeypatch):
    [candidate] = _confirm(
        _story_item(), [_source_row()],
        _other_item("x", "https://x.com/astro_ana/status/1986000000000000002"),
        monkeypatch=monkeypatch,
    )
    assert "digg_story" not in candidate.metadata


def test_hacker_news_item_linking_a_story_source_article_is_tagged(monkeypatch):
    article = "https://www.example-observatory.org/news/webb-water/?utm_source=digg"
    [candidate] = _confirm(
        _story_item(),
        [_source_row(kind="webpage", url=article, username="")],
        _other_item(
            "hackernews", "https://example-observatory.org/news/webb-water",
            hn_url="https://news.ycombinator.com/item?id=45900001",
        ),
        monkeypatch=monkeypatch,
    )
    assert candidate.metadata["digg_story"]["url"] == "https://digg.com/science/k3v9xq2m"


def test_reddit_text_post_about_the_same_event_is_not_tagged(monkeypatch):
    [candidate] = _confirm(
        _story_item(),
        [_source_row(kind="webpage", url="https://example-observatory.org/news/webb-water", username="")],
        _other_item("reddit", "https://www.reddit.com/r/space/comments/1abcde/webb_finds_water/"),
        monkeypatch=monkeypatch,
    )
    assert "digg_story" not in candidate.metadata


def test_item_sharing_only_a_domain_with_a_source_is_not_tagged(monkeypatch):
    [candidate] = _confirm(
        _story_item(),
        [_source_row(kind="webpage", url="https://example-observatory.org/news/webb-water", username="")],
        _other_item("hackernews", "https://example-observatory.org/news/another-story"),
        monkeypatch=monkeypatch,
    )
    assert "digg_story" not in candidate.metadata


def test_the_digg_story_itself_is_not_tagged(monkeypatch):
    story = _story_item()
    [candidate] = _confirm(
        story, [_source_row(kind="webpage", url=story.url, username="")], story,
        monkeypatch=monkeypatch,
    )
    assert "digg_story" not in candidate.metadata


def test_story_with_40_sources_tags_the_30th_and_keeps_5_quotes(monkeypatch):
    rows = [
        _source_row(
            url=f"https://x.com/voice{n}/status/19860000000000001{n:02d}",
            username=f"voice{n}", body=f"Take number {n}.",
        )
        for n in range(1, 41)
    ]
    story = _story_item()
    [candidate] = _confirm(
        story, rows, _other_item("x", "https://x.com/voice30/status/1986000000000000130"),
        monkeypatch=monkeypatch,
    )
    assert "digg_story" in candidate.metadata
    assert [post["username"] for post in story.metadata["posts"]] == [
        "voice1", "voice2", "voice3", "voice4", "voice5",
    ]


def test_item_in_two_stories_is_tagged_with_the_more_engaged_one(monkeypatch):
    shared = _source_row()
    small = _story_item(short_id="small001", digg_score=40, engagement_score=35, title="Small story")
    big = _story_item(short_id="big00001", digg_score=900, engagement_score=95, title="Big story")
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({
            small.metadata["clusterId"]: _sources_answer(shared),
            big.metadata["clusterId"]: _sources_answer(shared),
        }, []),
    )
    stories = digg.apply_story_sources(
        [small, big], digg.fetch_story_sources([small, big], "default"),
    )
    candidate = _candidate_for(_other_item("x", shared["xUrl"]))
    digg.tag_confirmed_candidates([candidate], stories)
    assert candidate.metadata["digg_story"]["title"] == "Big story"


def test_story_rejected_after_retrieval_no_longer_confirms_other_items(monkeypatch):
    shared = _source_row()
    kept = _story_item(short_id="kept0001", digg_score=40, engagement_score=35, title="Kept story")
    rejected = _story_item(
        short_id="reject01", digg_score=900, engagement_score=95, title="Rejected story",
    )
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({
            kept.metadata["clusterId"]: _sources_answer(_source_row(
                url="https://x.com/other/status/1986000000000000777",
            )),
            rejected.metadata["clusterId"]: _sources_answer(shared),
        }, []),
    )
    stories = digg.apply_story_sources(
        [kept, rejected], digg.fetch_story_sources([kept, rejected], "default"),
    )
    surviving = digg.surviving_stories(stories, [kept, _other_item("x", shared["xUrl"])])

    assert [[story["title"] for story in citing] for citing in surviving.values()] == [
        ["Kept story"],
    ]
    candidate = _candidate_for(_other_item("x", shared["xUrl"]))
    assert digg.tag_confirmed_candidates([candidate], surviving) == 0
    assert "digg_story" not in candidate.metadata


def test_shared_source_falls_back_to_the_surviving_story_when_the_top_one_is_rejected(
    monkeypatch,
):
    shared = _source_row()
    small = _story_item(short_id="small001", digg_score=40, engagement_score=35, title="Small story")
    big = _story_item(short_id="big00001", digg_score=900, engagement_score=95, title="Big story")
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({
            small.metadata["clusterId"]: _sources_answer(shared),
            big.metadata["clusterId"]: _sources_answer(shared),
        }, []),
    )
    stories = digg.apply_story_sources(
        [small, big], digg.fetch_story_sources([small, big], "default"),
    )
    candidate = _candidate_for(_other_item("x", shared["xUrl"]))

    digg.tag_confirmed_candidates([candidate], digg.surviving_stories(stories, [small]))

    assert candidate.metadata["digg_story"]["title"] == "Small story"


def test_merged_passes_keep_every_citing_story_highest_digg_score_first():
    first = {"k": [{"url": "u1", "digg_score": 226, "engagement": 100}]}
    digg.merge_stories(first, {
        "k": [
            {"url": "u2", "digg_score": 1286, "engagement": 100},
            {"url": "u1", "digg_score": 226, "engagement": 100},
            {"url": "u0", "digg_score": 226, "engagement": 100},
        ],
        "other": [{"url": "u3", "engagement": 10}],
    })
    assert [story["url"] for story in first["k"]] == ["u2", "u0", "u1"]
    assert [story["url"] for story in first["other"]] == ["u3"]


def test_shared_source_names_the_story_with_the_higher_digg_score(monkeypatch):
    # Both stories read 100 on the 0 to 100 engagement scale.
    shared = _source_row()
    lower = _story_item(short_id="lower001", digg_score=226, engagement_score=100, title="226 story")
    higher = _story_item(short_id="higher01", digg_score=1286, engagement_score=100, title="1286 story")
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({
            lower.metadata["clusterId"]: _sources_answer(shared),
            higher.metadata["clusterId"]: _sources_answer(shared),
        }, []),
    )
    stories = digg.apply_story_sources(
        [lower, higher], digg.fetch_story_sources([lower, higher], "default"),
    )
    candidate = _candidate_for(_other_item("x", shared["xUrl"]))
    digg.tag_confirmed_candidates([candidate], stories)
    assert candidate.metadata["digg_story"]["title"] == "1286 story"


def test_item_citing_two_sources_names_the_story_with_the_higher_digg_score():
    lower = {"title": "226 story", "url": "u1", "digg_score": 226, "engagement": 100}
    higher = {"title": "1286 story", "url": "u2", "digg_score": 1286, "engagement": 100}
    first = _other_item("x", "https://x.com/a/status/1986000000000000001")
    second = _other_item("hackernews", "https://example.org/news/water")
    candidate = _candidate_for(first, second)
    digg.tag_confirmed_candidates([candidate], {
        digg.match_key(first.url): [lower],
        digg.match_key(second.url): [higher],
    })
    assert candidate.metadata["digg_story"]["title"] == "1286 story"


@pytest.mark.parametrize("topic, sent", [
    ("AI", "AI AI"),
    ("Go", "Go Go"),
    ("X", "X X"),
    ("  AI  ", "AI AI"),
    ("C++", "C++"),
    ("a" * 120, "a" * 120),
    ("a" * 121, "a" * 120),
    ("word " * 30, ("word " * 24).strip()),
    ("x" * 118 + " yz", "x" * 118),
    ("AI " + "u" * 130, "AI AI"),
    ("A " + "u" * 130, "A A"),
])
def test_all_topic_query_is_fitted_to_the_length_the_cli_accepts(topic, sent):
    args = digg._build_search_args(topic, 10, all_topics=True)
    assert args[2] == sent
    assert digg.QUERY_MIN_CHARS <= len(args[2]) <= digg.QUERY_MAX_CHARS


def test_older_cli_query_is_sent_as_given():
    assert digg._build_search_args("AI", 10, all_topics=False)[2] == "AI"
    assert digg._build_search_args("a" * 200, 10, all_topics=False)[2] == "a" * 200


def _five_stories():
    scores = {"s1": 150, "s2": 1286, "s3": 239, "s4": 473, "s5": 180}
    return [_story_item(short_id=short_id, digg_score=score) for short_id, score in scores.items()]


def _stories(count: int) -> list:
    """``count`` stories s01, s02, ... with distinct scores in no special order."""
    return [
        _story_item(short_id=f"s{n:02d}", digg_score=100 + (n * 37) % 101)
        for n in range(1, count + 1)
    ]


def _by_score(stories: list) -> list:
    """Story UUIDs, highest Digg Score first, then by UUID."""
    return [
        item.metadata["clusterId"]
        for item in sorted(
            stories,
            key=lambda item: (-item.engagement["digg_score"], item.metadata["clusterId"]),
        )
    ]


def test_default_depth_fetches_the_8_top_scored_stories_four_at_a_time(monkeypatch):
    import threading

    calls: list = []
    lock = threading.Lock()
    running = {"now": 0, "most": 0}
    four_running = threading.Event()
    inner = _sources_cli({}, calls)

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        with lock:
            running["now"] += 1
            running["most"] = max(running["most"], running["now"])
            fourth = running["now"] == 4 and not four_running.is_set()
        try:
            assert timeout <= 15
            if fourth:
                # Leave room for a fifth call to show up before any ends.
                threading.Event().wait(0.05)
                four_running.set()
            four_running.wait(0.5)
            return inner(cmd, timeout=timeout)
        finally:
            with lock:
                running["now"] -= 1

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    stories = _stories(10)
    digg.fetch_story_sources(stories, "default")
    assert sorted(calls) == sorted(
        ["digg-pp-cli", "posts", uuid, "--agent", "--limit", "40"]
        for uuid in _by_score(stories)[:8]
    )
    assert running["most"] == 4


def test_fetched_sources_come_back_by_score_then_id_whatever_finishes_first(monkeypatch):
    import threading

    stories = [
        _story_item(short_id="tie-b", digg_score=300),
        _story_item(short_id="low", digg_score=120),
        _story_item(short_id="top", digg_score=900),
        _story_item(short_id="tie-a", digg_score=300),
    ]
    order = _by_score(stories)
    lock = threading.Lock()
    finished: list = []
    others_done = threading.Event()

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        # The top story's call ends last, the lowest one's first.
        if cmd[2] == order[0]:
            others_done.wait(1)
        with lock:
            finished.append(cmd[2])
            if len(finished) == len(order) - 1:
                others_done.set()
        return _stdout_for(_sources_answer(_source_row()))

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    fetched = digg.fetch_story_sources(stories, "default")
    assert finished[-1] == order[0]
    assert list(fetched) == order
    assert [uuid[-5:] for uuid in order] == ["00top", "tie-a", "tie-b", "00low"]


def test_deep_depth_fetches_12_stories_and_quick_fetches_none(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _sources_cli({}, calls))
    assert digg.fetch_story_sources(_stories(15), "quick") == {}
    assert calls == []
    digg.fetch_story_sources(_stories(15), "deep")
    assert len(calls) == 12


def test_story_found_by_two_searches_is_fetched_once_and_both_copies_get_quotes(monkeypatch):
    calls: list = []
    first, second = _story_item(), _story_item()
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({first.metadata["clusterId"]: _sources_answer(_source_row())}, calls),
    )
    fetched = digg.fetch_story_sources([first, second], "default")
    digg.apply_story_sources([first, second], fetched)
    assert len(calls) == 1
    assert first.metadata["posts"][0]["username"] == "astro_ana"
    assert second.metadata["posts"][0]["username"] == "astro_ana"


def test_sources_fetch_stops_when_the_run_is_out_of_time(monkeypatch):
    import threading

    calls: list = []
    out_of_time = threading.Event()
    # The first four calls meet, and the run is out of time before any ends.
    four_running = threading.Barrier(4, action=out_of_time.set, timeout=1)
    inner = _sources_cli({}, calls)

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        four_running.wait()
        return inner(cmd, timeout=timeout)

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    digg.fetch_story_sources(_stories(10), "default", stopped=out_of_time.is_set)
    assert len(calls) == 4


def test_sources_fetch_timing_out_tags_nothing_and_leaves_the_story_intact(monkeypatch):
    def fake_run(*_a, **_kw):
        raise subproc.SubprocTimeout("posts timed out")

    story = _story_item()
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    fetched = digg.fetch_story_sources([story], "default")
    stories = digg.apply_story_sources([story], fetched)
    candidate = _candidate_for(_other_item("x", _source_row()["xUrl"]))
    digg.tag_confirmed_candidates([candidate], stories)
    assert fetched == {} and stories == {}
    assert "digg_story" not in candidate.metadata
    assert story.metadata["posts"] == []
    assert story.url == "https://digg.com/science/k3v9xq2m"


def test_source_with_a_non_web_url_confirms_nothing(monkeypatch):
    [candidate] = _confirm(
        _story_item(),
        [_source_row(kind="webpage", url="javascript:alert(1)", username="")],
        _other_item("hackernews", "javascript:alert(1)"),
        monkeypatch=monkeypatch,
    )
    assert "digg_story" not in candidate.metadata


def test_old_cli_clusters_get_no_sources_fetch(monkeypatch):
    from lib import normalize

    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _sources_cli({}, calls))
    parsed = digg.parse_digg_response({"results": [_cluster()]}, query="sample")
    items = normalize.normalize_source_items("digg", parsed, "2026-01-01", "2026-12-31")
    assert digg.fetch_story_sources(items, "default") == {}
    assert calls == []


def test_ranked_post_enrichment_leaves_all_topic_stories_alone(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _sources_cli({}, calls))
    digg.enrich_source_items([_story_item()], top_k=3)
    assert calls == []


def test_tag_and_quotes_survive_fusion_of_two_streams(monkeypatch):
    from lib import fusion, schema

    source = _source_row()
    stories_a, stories_b = _story_item(), _story_item()
    post_a = _other_item("x", "https://x.com/astro_ana/status/1986000000000000001")
    post_b = _other_item("x", "https://x.com/astro_ana/status/1986000000000000001")
    for item in (stories_a, stories_b, post_a, post_b):
        item.local_relevance, item.freshness, item.source_quality = 0.7, 80, 0.7
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout",
        _sources_cli({stories_a.metadata["clusterId"]: _sources_answer(source)}, []),
    )
    digg_items = [stories_a, stories_b]
    confirmed = digg.apply_story_sources(digg_items, digg.fetch_story_sources(digg_items, "default"))
    plan = schema.QueryPlan(
        intent="breaking_news", freshness_mode="balanced_recent", cluster_mode="story",
        raw_topic="exoplanet",
        subqueries=[
            schema.SubQuery(label=label, search_query="exoplanet", ranking_query="exoplanet", sources=["digg", "x"])
            for label in ("primary", "second")
        ],
        source_weights={"digg": 1.0, "x": 1.0},
    )
    candidates = fusion.weighted_rrf(
        {
            ("primary", "digg"): [stories_a], ("primary", "x"): [post_a],
            ("second", "digg"): [stories_b], ("second", "x"): [post_b],
        },
        plan, pool_limit=10,
    )
    digg.tag_confirmed_candidates(candidates, confirmed)
    by_source = {candidate.source: candidate for candidate in candidates}
    assert len(candidates) == 2
    assert by_source["x"].subquery_labels == ["primary", "second"]
    assert by_source["x"].metadata["digg_story"]["url"] == stories_a.url
    assert "digg_story" not in by_source["digg"].metadata
    assert schema.candidate_primary_item(by_source["digg"]).metadata["posts"][0]["username"] == "astro_ana"


def test_confirmed_item_names_its_digg_story_in_the_report(monkeypatch):
    from lib import render

    [candidate] = _confirm(
        _story_item(), [_source_row()],
        _other_item("x", "https://x.com/astro_ana/status/1986000000000000001"),
        monkeypatch=monkeypatch,
    )
    lines = render._render_candidate(candidate, "1.")
    assert (
        "   - Also in Digg story: "
        "[Webb telescope spots water on a rocky exoplanet](https://digg.com/science/k3v9xq2m)"
        " (Digg Score 412, 37 sources)"
    ) in lines


def test_unconfirmed_item_names_no_digg_story():
    from lib import render

    candidate = _candidate_for(_other_item("x", "https://x.com/astro_ana/status/1986000000000000001"))
    assert not [line for line in render._render_candidate(candidate, "1.") if "Digg story" in line]


# === Top stories for global trending ===

# What digg-pp-cli 2026.10.1 printed for `top --agent --limit 20` against an
# empty local store, captured 2026-11-11: plain text, exit 0.
_CLI_2026_10_1_TOP_STDOUT = "no clusters in the local store. Run `digg-pp-cli sync` first.\n"


def _top_row(index: int = 1, topic: str = "science", **overrides) -> dict:
    short_id = f"top{index:05d}"
    row = {
        "rank": index,
        "clusterId": f"11111111-2222-3333-4444-{short_id:0>12}",
        "clusterUrlId": short_id,
        "url": f"https://digg.com/{topic}/{short_id}",
        "topic": topic,
        "title": f"Top story number {index}",
        "description": "Longer description of the story.",
        "diggScore": 900 - index,
        "postCount": 40 + index,
        "uniqueAuthors": 20 + index,
        "publishedAt": "2026-11-11T08:30:00Z",
    }
    row.update(overrides)
    return row


def _top_envelope(*rows: dict) -> dict:
    return {
        "meta": {"count": len(rows), "source": "live", "view": "top", "range": "24h"},
        "results": list(rows),
    }


def _top_calls(calls: list) -> list:
    return [cmd for cmd in calls if "--help" not in cmd]


def test_top_stories_reads_every_topic_from_the_new_cli(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _fake_cli(_NEW_SEARCH_HELP, _top_envelope(_top_row(1), _top_row(2)), calls),
    )
    out = digg.top_stories()
    assert _top_calls(calls) == [["digg-pp-cli", "top", "--agent", "--limit", "20"]]
    assert len(out["results"]) == 2
    assert "error" not in out


def test_deep_top_stories_asks_for_50(monkeypatch):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout", _fake_cli(_NEW_SEARCH_HELP, _top_envelope(), calls)
    )
    digg.top_stories(depth="deep")
    assert _top_calls(calls) == [["digg-pp-cli", "top", "--agent", "--limit", "50"]]


def test_top_stories_from_cli_2026_10_1_are_empty_without_an_error(monkeypatch):
    calls: list = []

    def fake_run(cmd, *, timeout, env=None, on_pid=None):
        calls.append(list(cmd))
        if "--help" in cmd:
            return subproc.SubprocResult(returncode=0, stdout=_OLD_SEARCH_HELP, stderr="")
        return subproc.SubprocResult(returncode=0, stdout=_CLI_2026_10_1_TOP_STDOUT, stderr="")

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    out = digg.top_stories()
    assert out == {"results": []}
    assert digg.parse_top_response(out, from_date="2026-10-12") == []
    assert _top_calls(calls) == []


def test_top_rows_parse_like_search_stories():
    rows = [_top_row(i, topic="sports" if i % 2 else "science") for i in range(1, 21)]
    items = digg.parse_top_response(_top_envelope(*rows), from_date="2026-10-12")
    assert len(items) == 20
    first = items[0]
    assert first["id"] == "top00001"
    assert first["cluster_id"] == "11111111-2222-3333-4444-0000top00001"
    assert first["topic"] == "sports"
    assert first["url"] == "https://digg.com/sports/top00001"
    assert first["title"] == "Top story number 1"
    assert first["date"] == "2026-11-11"
    assert first["engagement"] == {"postCount": 41, "uniqueAuthors": 21, "digg_score": 899}
    for item in items:
        assert set(item["engagement"]) == {"postCount", "uniqueAuthors", "digg_score"}
        assert item["date"] == "2026-11-11"


def test_top_rows_without_a_title_yield_no_items():
    untitled = _top_row(1)
    del untitled["title"]
    blank = _top_row(2, title="  ")
    assert digg.parse_top_response(_top_envelope(untitled, blank, {}), from_date="2026-10-12") == []


# === A failed help probe is not an older CLI ===


def _cli_with_failing_help(help_failure, answer: dict, calls: list):
    """A CLI whose ``search --help`` fails while every other call answers."""

    def fake_run(cmd, *, timeout, **_kwargs):
        calls.append(list(cmd))
        if "--help" in cmd:
            return help_failure()
        return _stdout_for(answer)

    return fake_run


def _help_times_out():
    raise subproc.SubprocTimeout("search --help timed out")


def _help_exits_non_zero():
    return subproc.SubprocResult(returncode=1, stdout="", stderr="config unreadable")


@pytest.mark.parametrize("help_failure", [_help_times_out, _help_exits_non_zero])
def test_failed_help_probe_keeps_the_items_without_the_upgrade_note(monkeypatch, help_failure):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _cli_with_failing_help(help_failure, _live_envelope(_story()), calls),
    )
    out = digg.search_digg("exoplanet", "2026-10-12", "2026-11-11")
    items = digg.parse_digg_response(out, query="exoplanet", from_date="2026-10-12")
    assert [item["url"] for item in items] == ["https://digg.com/science/k3v9xq2m"]
    assert "error" not in out
    assert digg.upgrade_note(out) is None
    # The call both CLIs accept.
    assert _search_calls(calls) == [
        ["digg-pp-cli", "search", "exoplanet", "--topic", "ai,tech",
         "--since", "30d", "--agent", "--limit", "20"]
    ]

    digg.search_digg("exoplanet", "2026-10-12", "2026-11-11")
    assert len([cmd for cmd in calls if "--help" in cmd]) == 2


@pytest.mark.parametrize("help_failure", [_help_times_out, _help_exits_non_zero])
def test_top_stories_after_a_failed_help_probe_are_empty_without_an_error(monkeypatch, help_failure):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _cli_with_failing_help(help_failure, _top_envelope(_top_row(1)), calls),
    )
    assert digg.top_stories() == {"results": []}
    assert _top_calls(calls) == []


# === Missing binary ===


def test_search_and_top_stories_name_the_missing_binary(monkeypatch):
    def never_run(*_a, **_kw):
        raise AssertionError("ran a CLI that is not on PATH")

    monkeypatch.setattr(digg.shutil, "which", lambda _: None)
    monkeypatch.setattr(digg.subproc, "run_with_timeout", never_run)
    search = digg.search_digg("exoplanet", "2026-10-12", "2026-11-11")
    top = digg.top_stories()
    assert search == {"results": [], "error": "digg-pp-cli not on PATH"}
    assert top == {"results": [], "error": "digg-pp-cli not on PATH"}


# === Stories without a usable publish date ===


@pytest.mark.parametrize("published_at", [None, "", "last Tuesday"])
def test_story_without_a_readable_publish_date_is_kept_undated(published_at):
    story = _story(published_at=published_at)
    if published_at is None:
        del story["publishedAt"]
    [item] = digg.parse_digg_response(_live_envelope(story), from_date="2026-10-12")
    assert item["url"] == "https://digg.com/science/k3v9xq2m"
    assert item["date"] is None


# === Story sources: run deadline, cancel signal and comparison share ===


class _Clock:
    """A monotonic clock that only moves when a fake CLI call spends time."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _one_call_at_a_time(monkeypatch):
    """The fake clock only adds up when calls do not overlap."""
    monkeypatch.setattr(digg, "SOURCES_CONCURRENCY", 1)


def _slow_sources_cli(clock: _Clock, seen: list, *, times_out: bool = True):
    """A ``posts`` call that takes its whole timeout, then times out or
    answers with no sources."""

    def fake_run(cmd, *, timeout, **kwargs):
        seen.append({"timeout": timeout, **kwargs})
        clock.now += timeout
        if times_out:
            raise subproc.SubprocTimeout("posts timed out")
        return subproc.SubprocResult(returncode=0, stdout='{"results": []}', stderr="")

    return fake_run


def test_sources_fetch_caps_a_call_at_the_time_left_and_stops_at_the_deadline(monkeypatch):
    clock, seen = _Clock(), []
    _one_call_at_a_time(monkeypatch)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg, "time", clock)
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _slow_sources_cli(clock, seen))
    deadline = clock.now + 2
    assert digg.fetch_story_sources(_five_stories(), "deep", deadline=deadline) == {}
    assert len(seen) == 1
    assert 0 < seen[0]["timeout"] <= 2
    assert seen[0]["deadline_monotonic"] == deadline


def test_sources_fetch_makes_no_call_once_the_run_is_cancelled(monkeypatch):
    import threading

    calls: list = []
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _sources_cli({}, calls))
    assert digg.fetch_story_sources(_five_stories(), "deep", cancel=cancel) == {}
    assert calls == []


def test_cancel_during_the_fetch_stops_the_calls_not_yet_started(monkeypatch):
    import threading

    calls: list = []
    cancel = threading.Event()
    # The first four calls meet, and the run is cancelled before any ends.
    four_running = threading.Barrier(4, action=cancel.set, timeout=1)
    inner = _sources_cli({}, calls)

    def fake_run(cmd, *, timeout, **kwargs):
        four_running.wait()
        return inner(cmd, timeout=timeout)

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    stories = _stories(10)
    digg.fetch_story_sources(stories, "default", cancel=cancel)
    assert len(calls) == 4
    assert {call[2] for call in calls} == set(_by_score(stories)[:4])


def test_deadline_two_seconds_away_caps_every_call_and_leaves_no_worker_running(monkeypatch):
    import threading
    import time

    lock = threading.Lock()
    seen: list = []
    running = {"now": 0}

    def fake_run(cmd, *, timeout, **kwargs):
        with lock:
            running["now"] += 1
            seen.append({"timeout": timeout, **kwargs})
        try:
            return _stdout_for(_sources_answer())
        finally:
            with lock:
                running["now"] -= 1

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    threads_before = set(threading.enumerate())
    deadline = time.monotonic() + 2
    digg.fetch_story_sources(_stories(10), "default", deadline=deadline)
    assert len(seen) == 8
    for call in seen:
        assert 0 < call["timeout"] <= 2
        assert call["deadline_monotonic"] == deadline
    assert running["now"] == 0
    assert [t for t in threading.enumerate() if t not in threads_before and t.is_alive()] == []


def test_sources_fetch_hands_the_cancel_signal_to_each_call(monkeypatch):
    import threading

    seen: list = []
    cancel = threading.Event()

    def fake_run(cmd, *, timeout, **kwargs):
        seen.append(kwargs.get("cancel"))
        return _stdout_for(_sources_answer())

    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    digg.fetch_story_sources(_five_stories(), "default", cancel=cancel)
    assert seen == [cancel] * 5


def test_sources_fetch_without_a_run_deadline_spends_at_most_40_seconds(monkeypatch):
    clock, seen = _Clock(), []
    _one_call_at_a_time(monkeypatch)
    started = clock.now
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg, "time", clock)
    monkeypatch.setattr(
        digg.subproc, "run_with_timeout", _slow_sources_cli(clock, seen, times_out=False),
    )
    digg.fetch_story_sources(_five_stories(), "deep")
    assert [call["timeout"] for call in seen] == [10, 10, 10, 10]
    assert clock.now - started <= 40


def test_sources_fetch_stops_starting_calls_once_one_fails(monkeypatch):
    # Digg stalls hit every call alike, so the rest are not left to wait
    # out their own timeouts.
    calls: list = []

    def timing_out(cmd, *, timeout, **_bounds):
        calls.append(cmd)
        raise subproc.SubprocTimeout("posts timed out")

    _one_call_at_a_time(monkeypatch)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", timing_out)
    assert digg.fetch_story_sources(_five_stories(), "deep") == {}
    assert len(calls) == 1


@pytest.mark.parametrize("depth, entity_count, expected_calls", [
    ("default", 1, 8),
    ("deep", 1, 12),
    ("default", 3, 3),
    ("deep", 3, 4),
    ("default", 9, 1),
    ("quick", 3, 0),
])
def test_comparison_run_fetches_its_share_of_the_story_budget(
    monkeypatch, depth, entity_count, expected_calls,
):
    calls: list = []
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _sources_cli({}, calls))
    digg.fetch_story_sources(_stories(15), depth, entity_count=entity_count)
    assert len(calls) == expected_calls


# === Research search: most relevant stories plus the highest-scored ones ===

_RELEVANCE_ARGS = ["digg-pp-cli", "search", "exoplanet", "--since", "30d", "--agent", "--limit"]


def _research_cli(relevance, score, calls: list, seen: list | None = None, help_text=_NEW_SEARCH_HELP):
    """A CLI answering the relevance call and the ``--sort score`` call apart.

    An answer is an envelope, or a callable that raises or returns a result.
    """

    def fake_run(cmd, *, timeout, **kwargs):
        calls.append(list(cmd))
        if "--help" in cmd:
            return subproc.SubprocResult(returncode=0, stdout=help_text, stderr="")
        if seen is not None:
            seen.append(kwargs)
        answer = score if "--sort" in cmd else relevance
        return answer() if callable(answer) else _stdout_for(answer)

    return fake_run


def _research_search(monkeypatch, fake_run, depth="default", from_date="2026-10-12", **kwargs):
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", fake_run)
    return digg.search_digg(
        "exoplanet", from_date, "2026-11-11", depth=depth, include_top_scored=True, **kwargs
    )


def _score_call_times_out():
    raise subproc.SubprocTimeout("search timed out after 30s")


@pytest.mark.parametrize("depth,size", [("default", "50"), ("deep", "100")])
def test_research_search_asks_for_the_most_relevant_and_the_highest_scored(monkeypatch, depth, size):
    calls: list = []
    answer = _live_envelope(_story())
    out = _research_search(monkeypatch, _research_cli(answer, answer, calls), depth=depth)
    assert _search_calls(calls) == [
        [*_RELEVANCE_ARGS, size],
        [*_RELEVANCE_ARGS, size, "--sort", "score"],
    ]
    assert len(out["results"]) == 1
    assert "error" not in out
    assert digg.score_pass_note(out) is None


def test_quick_research_search_is_one_call_of_8_with_no_sort(monkeypatch):
    calls: list = []
    answer = _live_envelope(_story())
    _research_search(monkeypatch, _research_cli(answer, answer, calls), depth="quick", from_date="2026-11-04")
    assert _search_calls(calls) == [[*_RELEVANCE_ARGS, "8"]]


def test_research_search_on_the_older_cli_is_the_one_ai_and_tech_call(monkeypatch):
    calls: list = []
    out = _research_search(
        monkeypatch,
        _research_cli(_CLI_2026_10_1_ANSWER, _CLI_2026_10_1_ANSWER, calls, help_text=_OLD_SEARCH_HELP),
        from_date="2026-11-04",
    )
    assert _search_calls(calls) == [
        ["digg-pp-cli", "search", "exoplanet", "--topic", "ai,tech",
         "--since", "30d", "--agent", "--limit", "20"]
    ]
    assert digg.upgrade_note(out) == digg.UPGRADE_NOTE


@pytest.mark.parametrize("help_failure", [_help_times_out, _help_exits_non_zero])
def test_research_search_after_a_failed_help_probe_is_the_one_ai_and_tech_call(monkeypatch, help_failure):
    calls: list = []
    _research_search(
        monkeypatch, _cli_with_failing_help(help_failure, _live_envelope(_story()), calls)
    )
    assert _search_calls(calls) == [
        ["digg-pp-cli", "search", "exoplanet", "--topic", "ai,tech",
         "--since", "30d", "--agent", "--limit", "20"]
    ]


def test_story_in_both_calls_appears_once_and_score_only_stories_follow_by_score(monkeypatch):
    relevance = _live_envelope(
        _story(short_id="rel00001", diggScore=12),
        _story(short_id="both0001", diggScore=500),
    )
    score = _live_envelope(
        _story(short_id="both0001", diggScore=500),
        _story(short_id="big90bbb", diggScore=90),
        _story(short_id="big300aa", diggScore=300),
        _story(short_id="big90aaa", diggScore=90),
    )
    out = _research_search(monkeypatch, _research_cli(relevance, score, []))
    assert [story["clusterUrlId"] for story in out["results"]] == [
        "rel00001", "both0001", "big300aa", "big90aaa", "big90bbb",
    ]


def test_stories_without_a_uuid_are_matched_by_their_short_id(monkeypatch):
    def bare(short_id: str, score: int) -> dict:
        story = _story(short_id=short_id, diggScore=score)
        del story["clusterId"]
        return story

    out = _research_search(
        monkeypatch,
        _research_cli(
            _live_envelope(bare("same0001", 40)),
            _live_envelope(bare("same0001", 40), bare("other001", 70)),
            [],
        ),
    )
    assert [story["clusterUrlId"] for story in out["results"]] == ["same0001", "other001"]


def test_failed_score_call_keeps_the_relevance_stories_and_says_so(monkeypatch):
    out = _research_search(
        monkeypatch,
        _research_cli(_live_envelope(_story(short_id="rel00001")), _score_call_times_out, []),
    )
    assert [story["clusterUrlId"] for story in out["results"]] == ["rel00001"]
    assert "error" not in out
    note = digg.score_pass_note(out)
    assert "highest-scored" in note
    assert "timed out" in note
    assert len(note) <= 140


def test_score_pass_note_is_absent_for_a_plain_answer():
    assert digg.score_pass_note({"results": []}) is None
    assert digg.score_pass_note(None) is None


def test_failed_score_call_is_a_detail_on_an_ok_digg_outcome(monkeypatch):
    from lib import health, pipeline, schema

    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(
        digg.subproc,
        "run_with_timeout",
        _research_cli(_live_envelope(_story(short_id="rel00001")), _score_call_times_out, []),
    )
    subquery = schema.SubQuery(
        label="primary",
        search_query="exoplanet score pass",
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
    assert [item["id"] for item in items] == ["rel00001"]
    assert "_source_outcome" not in artifact
    assert "highest-scored" in artifact["_source_outcome_detail"]

    bundle = schema.RetrievalBundle()
    bundle.record_detail("digg", artifact["_source_outcome_detail"])
    bundle.add_items("primary", "digg", [object()])
    assert bundle.source_status["digg"].state == health.OK
    assert "highest-scored" in bundle.source_status["digg"].detail


def test_failed_relevance_call_is_an_error_and_makes_no_score_call(monkeypatch):
    calls: list = []

    def relevance_fails():
        return subproc.SubprocResult(returncode=2, stdout="", stderr="boom")

    out = _research_search(
        monkeypatch, _research_cli(relevance_fails, _live_envelope(_story()), calls)
    )
    assert out == {"results": [], "error": "boom"}
    assert len(_search_calls(calls)) == 1
    assert "--sort" not in _search_calls(calls)[0]


@pytest.mark.parametrize(
    "from_date,since",
    [("2026-11-04", "8d"), ("2026-11-11", "1d"), ("2026-10-14", "29d"), ("2026-10-13", "30d"), ("2026-09-12", "30d")],
)
def test_research_search_sends_a_shorter_lookback_on_both_calls(monkeypatch, from_date, since):
    calls: list = []
    answer = _live_envelope(_story())
    _research_search(monkeypatch, _research_cli(answer, answer, calls), from_date=from_date)
    searches = _search_calls(calls)
    assert len(searches) == 2
    assert [cmd[cmd.index("--since") + 1] for cmd in searches] == [since, since]


def test_cancelled_run_makes_no_score_call(monkeypatch):
    import threading

    calls: list = []
    cancel = threading.Event()
    cancel.set()
    answer = _live_envelope(_story())
    out = _research_search(monkeypatch, _research_cli(answer, answer, calls), cancel=cancel)
    assert len(_search_calls(calls)) == 1
    assert "--sort" not in _search_calls(calls)[0]
    assert len(out["results"]) == 1
    assert digg.score_pass_note(out) is None


def test_run_past_its_deadline_makes_no_score_call(monkeypatch):
    calls: list = []
    clock = _Clock()
    monkeypatch.setattr(digg, "time", clock)
    answer = _live_envelope(_story())
    _research_search(
        monkeypatch, _research_cli(answer, answer, calls), deadline=clock.now - 1
    )
    assert len(_search_calls(calls)) == 1
    assert "--sort" not in _search_calls(calls)[0]


def test_research_search_hands_the_deadline_and_cancel_signal_to_both_calls(monkeypatch):
    import threading

    seen: list = []
    clock = _Clock()
    monkeypatch.setattr(digg, "time", clock)
    cancel = threading.Event()
    deadline = clock.now + 60
    answer = _live_envelope(_story())
    _research_search(
        monkeypatch, _research_cli(answer, answer, [], seen), deadline=deadline, cancel=cancel
    )
    # The score call's deadline is earlier, so its answer can be handed
    # back before the run's own deadline.
    assert seen == [
        {"deadline_monotonic": deadline, "cancel": cancel},
        {
            "deadline_monotonic": deadline - digg.SCORE_SEARCH_MARGIN, "cancel": cancel,
            "cleanup_grace": digg.SCORE_SEARCH_CLEANUP_GRACE,
        },
    ]


def test_pipeline_research_stream_makes_both_calls_with_the_runs_bounds(monkeypatch):
    import threading

    from lib import pipeline, schema

    import time

    calls: list = []
    seen: list = []
    cancel = threading.Event()
    deadline = time.monotonic() + 60
    answer = _live_envelope(_story())
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _research_cli(answer, answer, calls, seen))
    subquery = schema.SubQuery(
        label="primary",
        search_query="exoplanet bounds",
        ranking_query="exoplanet",
        sources=["digg"],
    )
    items, artifact = pipeline._retrieve_stream_impl(
        topic="exoplanet",
        subquery=subquery,
        source="digg",
        config={"_research_deadline": deadline, "_enrich_cancel": cancel},
        depth="default",
        date_range=("2026-11-04", "2026-11-11"),
        runtime=None,
        mock=False,
    )
    assert _search_calls(calls) == [
        ["digg-pp-cli", "search", "exoplanet bounds", "--since", "8d", "--agent", "--limit", "50"],
        ["digg-pp-cli", "search", "exoplanet bounds", "--since", "8d", "--agent", "--limit", "50",
         "--sort", "score"],
    ]
    # The score call's deadline is earlier, so its answer can be handed
    # back before the run's own deadline.
    assert seen == [
        {"deadline_monotonic": deadline, "cancel": cancel},
        {
            "deadline_monotonic": deadline - digg.SCORE_SEARCH_MARGIN, "cancel": cancel,
            "cleanup_grace": digg.SCORE_SEARCH_CLEANUP_GRACE,
        },
    ]
    assert len(items) == 1
    assert artifact == {}


def test_discovery_digg_search_stays_one_call_of_20(monkeypatch):
    from lib import pipeline

    calls: list = []
    answer = _live_envelope(_story(title="Exoplanet water found by Webb"))
    monkeypatch.setattr(digg, "_today", lambda: _CAPTURE_DAY)
    monkeypatch.setattr(digg.shutil, "which", lambda _: "/fake/path")
    monkeypatch.setattr(digg.subproc, "run_with_timeout", _research_cli(answer, answer, calls))
    plan = MagicMock()
    plan.domain = "exoplanet"
    pipeline._fetch_discovery_source(
        "digg", plan, from_date="2026-11-04", to_date="2026-11-11",
        depth="default", mock=False, config={},
    )
    assert _search_calls(calls) == [[*_RELEVANCE_ARGS, "20"]]


def test_score_call_ends_before_the_run_deadline_so_the_relevance_stories_are_kept(monkeypatch):
    # The relevance call has answered. The score call must be over with time
    # to spare, or the run abandons the whole search as timed out.
    score_calls: list = []
    answer = _live_envelope(_story())

    def fake_run(cmd, *, timeout, **kwargs):
        if "--help" in cmd:
            return subproc.SubprocResult(returncode=0, stdout=_NEW_SEARCH_HELP, stderr="")
        if "--sort" in cmd:
            score_calls.append({"timeout": timeout, **kwargs})
        return _stdout_for(answer)

    deadline = digg.time.monotonic() + 12
    _research_search(monkeypatch, fake_run, deadline=deadline)
    [score_call] = score_calls
    assert 0 < score_call["timeout"] <= 12 - digg.SCORE_SEARCH_MARGIN
    assert score_call["deadline_monotonic"] == deadline - digg.SCORE_SEARCH_MARGIN
    # Stopping a stuck call takes up to two grace periods, which have to fit
    # in the margin too.
    assert 2 * score_call["cleanup_grace"] < digg.SCORE_SEARCH_MARGIN


def test_score_call_is_skipped_when_too_little_time_is_left(monkeypatch):
    calls: list = []
    answer = _live_envelope(_story())
    out = _research_search(
        monkeypatch, _research_cli(answer, answer, calls),
        deadline=digg.time.monotonic() + digg.SCORE_SEARCH_MARGIN - 1,
    )
    assert len(_search_calls(calls)) == 1
    assert len(out["results"]) == 1
    assert digg.score_pass_note(out) is None
