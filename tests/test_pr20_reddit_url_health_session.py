"""Regression tests for PR 20: Reddit URL safety, health locking & session concurrency.

Covers:
- HIGH-124: Outbound URL validation and encoding in reddit_shreddit,
  reddit_listing, reddit_arctic, reddit_public, and reddit_refs.
- HIGH-128: Per-thread parse exception isolation, mid-hydration consecutive-403
  session expiry, and single-ref 403 handling in reddit_session.hydrator.
- HIGH-129: Single-flight in-flight deduplication in reddit_session.discover
  and Session.fetch with deadline/cancellation handling and named-subreddit
  privacy isolation.
- HIGH-130: Owner-only (0o600) atomic writes, symlink refusal, temp cleanup,
  and cross-process file locking in reddit_health.
- HIGH-131: Monotonic same-run health transitions so stale in-flight OK cannot
  clobber a persisted BLOCKED cooldown on disk.
- HIGH-133: Strict host, scheme, port, userinfo, and thread-path validation in
  reddit_enrich.extract_reddit_path.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import stat
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import (
    env,
    http,
    reddit_arctic,
    reddit_enrich,
    reddit_health,
    reddit_hydrate,
    reddit_listing,
    reddit_public,
    reddit_refs,
    reddit_session,
    reddit_shreddit,
)
from lib.reddit_refs import canonical_thread

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"
DUMMY_COOKIE = "dummy-pr20-session-cookie-0123456789"
TOPIC = "Ghostty terminal"
FROM, TO = "2026-10-16", "2026-11-15"
IN_WINDOW_UTC = 1794355200  # 2026-11-11T00:00:00Z

POSTS_HTML = (FIXTURES / "reddit_session_posts.html").read_text(encoding="utf-8")
COMMENTS_HTML = (FIXTURES / "reddit_session_comments.html").read_text(encoding="utf-8")
COMMUNITIES_HTML = (FIXTURES / "reddit_session_communities.html").read_text(encoding="utf-8")
NO_COMMENTS_HTML = (FIXTURES / "reddit_session_comments_no_results.html").read_text(encoding="utf-8")
BLOCKED_HTML = (FIXTURES / "reddit_session_blocked_403.html").read_text(encoding="utf-8")
THREAD_DATA = json.loads((FIXTURES / "reddit_session_thread.json").read_text(encoding="utf-8"))


def _thread_json(post_id: str, **fields) -> str:
    data = json.loads(json.dumps(THREAD_DATA))
    post = data[0]["data"]["children"][0]["data"]
    sub = fields.get("subreddit", post["subreddit"])
    post.update({
        "id": post_id,
        "created_utc": IN_WINDOW_UTC,
        "permalink": f"/r/{sub}/comments/{post_id}/t/",
    })
    post.update(fields)
    return json.dumps(data)


def _ref(post_id: str, sub: str = "Ghostty", provenance: str = "session"):
    ref = canonical_thread(f"https://www.reddit.com/r/{sub}/comments/{post_id}/")
    ref.provenance.add(provenance)
    ref.title = "Ghostty terminal thread"
    ref.date = "2026-11-11"
    return ref


def _archive_post(post_id: str, sub: str = "Ghostty", title: str = "Ghostty terminal thread"):
    return reddit_arctic._normalize_listing_row({
        "id": post_id,
        "title": title,
        "score": 120,
        "num_comments": 15,
        "subreddit": sub,
        "created_utc": IN_WINDOW_UTC,
        "permalink": f"/r/{sub}/comments/{post_id}/t/",
        "author": "public_user",
    })


def _session(**kwargs) -> reddit_session.Session:
    return reddit_session.Session(DUMMY_COOKIE, browser="chrome", **kwargs)


# ---------------------------------------------------------------------------
# HIGH-124: URL validation & encoding across Reddit modules
# ---------------------------------------------------------------------------


class TestHigh124ShredditUrlSafety:
    @pytest.mark.parametrize("bad_url", [
        "",
        None,
        123,
        "1taeiw0",
        "https://evil.com/r/Rakuten/comments/1taeiw0/title/",
        "https://evil-reddit.com/r/Rakuten/comments/1taeiw0/title/",
        "https://reddit.com.attacker.com/r/Rakuten/comments/1taeiw0/title/",
        "https://user:pass@www.reddit.com/r/Rakuten/comments/1taeiw0/title/",
        "https://www.reddit.com:8443/r/Rakuten/comments/1taeiw0/title/",
        "javascript://www.reddit.com/r/Rakuten/comments/1taeiw0/title/",
        "https://www.reddit.com/r/../comments/1taeiw0/title/",
        "https://www.reddit.com/r/foo%2Fbar/comments/1taeiw0/title/",
        "https://www.reddit.com/r/a/comments/1taeiw0/title/",
        "https://www.reddit.com/r/Rakuten/comments/1taeiw0/../../admin",
        "https://[::1/r/Rakuten/comments/1taeiw0/title/",
    ])
    def test_extract_post_ref_rejects_malicious_or_non_reddit_urls(self, bad_url):
        assert reddit_shreddit.extract_post_ref(bad_url) is None

    @pytest.mark.parametrize("url", [
        "https://www.reddit.com/comments/1taeiw0/",
        "https://old.reddit.com/comments/1taeiw0/post_slug/",
        "https://redd.it/1taeiw0",
    ])
    def test_subredditless_canonical_url_falls_back_to_arctic(self, url):
        assert reddit_shreddit.extract_post_ref(url) == ("", "1taeiw0")
        arctic_comment = {
            "score": 55,
            "date": "2026-11-11",
            "author": "alice",
            "body": "Helpful archive comment on subredditless thread.",
            "excerpt": "Helpful archive comment on subredditless thread.",
            "permalink": "/r/Rakuten/comments/1taeiw0/x/c1/",
            "url": "https://reddit.com/r/Rakuten/comments/1taeiw0/x/c1/",
            "depth": 0,
        }
        future_deadline = time.monotonic() + 999.0
        with mock.patch.object(reddit_shreddit.http, "reddit_keyless_get_text") as get_text, \
             mock.patch.object(
                 reddit_shreddit.reddit_arctic, "fetch_post_comments", return_value=[arctic_comment],
             ) as arctic:
            out = reddit_shreddit.fetch_comments(url, deadline=future_deadline)
        get_text.assert_not_called()
        arctic.assert_called_once_with(
            "1taeiw0",
            limit=reddit_shreddit.MAX_COMMENTS,
            skip_author=reddit_shreddit._is_bot_author,
            deadline=future_deadline,
        )
        assert len(out["top_comments"]) == 1
        assert out["top_comments"][0]["score"] == 55

    @pytest.mark.parametrize(("sub", "pid"), [
        ("", "1taeiw0"),
        ("../admin", "1taeiw0"),
        ("sub?x=1", "1taeiw0"),
        ("sub#frag", "1taeiw0"),
        ("a", "1taeiw0"),
        ("Rakuten", ""),
        ("Rakuten", "../evil"),
        ("Rakuten", "1taeiw0?x=1"),
    ])
    def test_svc_url_rejects_invalid_subreddit_or_post_id(self, sub, pid):
        with pytest.raises(ValueError):
            reddit_shreddit._svc_url(sub, pid)

    def test_svc_url_normalizes_r_prefix_and_encodes(self):
        assert reddit_shreddit._svc_url("/R/Rakuten/", "t3_1TAEIW0") == (
            "https://www.reddit.com/svc/shreddit/comments/r/Rakuten/t3_1taeiw0?sort=top"
        )


class TestHigh124ListingUrlSafety:
    def test_listing_url_strips_case_insensitive_prefix_and_slashes(self):
        u1 = reddit_listing._listing_url("/R/Technology/", "top", "week")
        assert u1 == "https://www.reddit.com/svc/shreddit/community-more-posts/top/?name=Technology&t=week"
        u2 = reddit_listing._listing_url("/r/ALL/", "hot")
        assert u2 == "https://www.reddit.com/r/all/hot/"

    @pytest.mark.parametrize(("sub", "sort", "tf"), [
        ("", "top", "month"),
        ("r/", "top", "month"),
        ("   ", "hot", "month"),
        ("../admin", "top", "month"),
        ("tech&evil=1", "top", "month"),
        ("tech#frag", "hot", "month"),
        ("technology", "../evil", "month"),
        ("technology", "top", "month&evil=1"),
    ])
    def test_listing_url_rejects_invalid_inputs(self, sub, sort, tf):
        with pytest.raises(ValueError):
            reddit_listing._listing_url(sub, sort, tf)

    def test_fetch_listings_skips_empty_and_invalid_subreddits(self):
        with mock.patch.object(reddit_listing.http, "reddit_keyless_get_text_retry_429") as fetch:
            assert reddit_listing.fetch_listings(["", "r/", "../evil", "a&b=c"]) == []
        fetch.assert_not_called()

    def test_fetch_discovery_listings_skips_invalid_subreddits(self):
        with mock.patch.object(reddit_listing.http, "reddit_keyless_get_text_retry_429") as fetch, \
             mock.patch("lib.reddit_arctic.fetch_listings") as arctic:
            result = reddit_listing.fetch_discovery_listings(["", "r/", "../evil"], query="test")
        assert result == {"items": [], "errors": []}
        fetch.assert_not_called()
        arctic.assert_not_called()


class TestHigh124ArcticUrlSafety:
    @pytest.fixture(autouse=True)
    def _no_arctic_network(self):
        # Override conftest's stub so these tests exercise real arctic functions.
        reddit_arctic.reset_memo()
        yield
        reddit_arctic.reset_memo()

    def test_fetch_scores_filters_invalid_ids_and_urlencodes(self):
        with mock.patch.object(
            reddit_arctic.http, "get",
            return_value={"data": [{"id": "abc123", "score": 10, "num_comments": 2}]},
        ) as g:
            out = reddit_arctic.fetch_scores(["abc&limit=999", "../evil", "t3_abc123", ""])
        assert out == {"abc123": {"score": 10, "num_comments": 2}}
        g.assert_called_once()
        assert parse_qs(urlsplit(g.call_args[0][0]).query) == {"ids": ["abc123"]}

    def test_fetch_posts_by_ids_makes_no_call_for_invalid_ids(self):
        with mock.patch.object(reddit_arctic.http, "get") as g:
            out, state = reddit_arctic.fetch_posts_by_ids(["../etc", "bad&limit=1", "not_valid!"])
        assert out == {} and state == "ok"
        g.assert_not_called()

    def test_fetch_listings_validates_subreddits_and_dates(self):
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}) as g:
            reddit_arctic.fetch_listings(
                ["/R/Tea/", "all", "", "../evil", "tea&limit=999"],
                from_date="2026-09-20&evil=1",
                to_date="not-a-date#frag",
            )
        assert g.call_count == 1
        query = parse_qs(urlsplit(g.call_args[0][0]).query)
        assert query == {"subreddit": ["Tea"], "limit": ["25"], "sort": ["desc"]}
        assert "evil" not in g.call_args[0][0]
        assert "frag" not in g.call_args[0][0]

    @pytest.mark.parametrize("bad_id", ["", None, 123, "abc&limit=999", "../evil", "bad_id"])
    def test_comment_endpoints_reject_invalid_post_ids(self, bad_id):
        with mock.patch.object(reddit_arctic.http, "get") as g:
            assert reddit_arctic.fetch_comment_tree(bad_id) is None
            assert reddit_arctic.fetch_post_comments(bad_id) == []
        g.assert_not_called()

    def test_comment_endpoints_accept_valid_post_id_and_urlencode(self):
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}) as g:
            assert reddit_arctic.fetch_comment_tree("T3_ABC123") == []
            assert reddit_arctic.fetch_post_comments("T3_ABC123") == []
        assert g.call_count == 2
        tree_q = parse_qs(urlsplit(g.call_args_list[0][0][0]).query)
        post_q = parse_qs(urlsplit(g.call_args_list[1][0][0]).query)
        assert tree_q == {"link_id": ["t3_abc123"], "limit": [str(reddit_arctic.MAX_COMMENT_ROWS)]}
        assert post_q == {"link_id": ["t3_abc123"], "limit": [str(reddit_arctic.FALLBACK_COMMENT_ROWS)]}


class TestHigh124PublicSearchUrlSafety:
    @pytest.mark.parametrize("bad_sub", [
        "../api/v1/me",
        "sub?evil=1",
        "sub#frag",
        "sub%2Fadmin",
        "r/",
        "   ",
        "a",
    ])
    def test_public_search_rejects_malicious_subreddit_without_request(self, bad_sub):
        with mock.patch("lib.reddit_public.urllib.request.urlopen") as urlopen:
            assert reddit_public.search("python", subreddit=bad_sub) == []
        urlopen.assert_not_called()

    def test_public_search_normalizes_uppercase_prefix_and_encodes_query(self):
        resp = mock.MagicMock()
        resp.read.return_value = b'{"data": {"children": []}}'
        resp.headers = {"Content-Type": "application/json"}
        resp.__enter__ = mock.MagicMock(return_value=resp)
        resp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch("lib.reddit_public.urllib.request.urlopen", return_value=resp) as urlopen:
            reddit_public.search("a&b=c #test", subreddit="/R/ClaudeAI/")
        req = urlopen.call_args[0][0]
        parts = urlsplit(req.full_url)
        assert parts.path == "/r/ClaudeAI/search.json"
        assert parse_qs(parts.query)["q"] == ["a&b=c #test"]
        assert parse_qs(parts.query)["restrict_sr"] == ["on"]


# ---------------------------------------------------------------------------
# HIGH-128: hydrator parse resilience & mid-hydration / single-ref expiry
# ---------------------------------------------------------------------------


class TestHigh128HydratorResilienceAndExpiry:
    def test_malformed_thread_json_preserves_prior_privacy_drop_and_continues(self, monkeypatch):
        priv_body = _thread_json("1priv01", subreddit="secretclub", subreddit_type="private")
        malformed_body = _thread_json("1mal002", subreddit="Ghostty", subreddit_type="public")
        good_body = _thread_json("1good03", subreddit="Ghostty", subreddit_type="public")

        def fake_get(url, _cookie, **_k):
            if "1priv01.json" in url:
                return 200, priv_body, False
            if "1mal002.json" in url:
                return 200, malformed_body, False
            if "1good03.json" in url:
                return 200, good_body, False
            return 404, "", False

        real_parse = reddit_enrich.parse_thread_data

        def failing_parse(data):
            if (
                isinstance(data, list)
                and data
                and data[0]["data"]["children"][0]["data"].get("id") == "1mal002"
            ):
                raise TypeError("simulated unexpected parser failure")
            return real_parse(data)

        logs: list[str] = []
        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        monkeypatch.setattr(reddit_enrich, "parse_thread_data", failing_parse)
        monkeypatch.setattr(reddit_session, "_log", logs.append)
        # Even if Arctic Shift holds a copy of 1priv01, DROP must block resurrection.
        monkeypatch.setattr(
            reddit_arctic,
            "fetch_posts_by_ids",
            lambda ids, **_k: (
                {pid: _archive_post(pid) for pid in ids},
                "ok",
            ),
        )
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        refs = [
            _ref("1priv01", "secretclub", provenance="host"),
            _ref("1mal002", "Ghostty", provenance="host"),
            _ref("1good03", "Ghostty", provenance="host"),
        ]
        result = reddit_hydrate.hydrate(
            refs,
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(_session()),
            now=1.9e9,
        )
        ids = [item["metadata"]["post_id"] for item in result.items]
        assert "1priv01" not in ids
        assert "1good03" in ids
        assert any("1mal002" in line and ("TypeError" in line or "AttributeError" in line) for line in logs)

    def test_null_comment_score_typeerror_does_not_discard_prior_drop(self, monkeypatch):
        priv_body = _thread_json("1priv09", subreddit="secretclub", subreddit_type="private")
        null_score_body = json.dumps([
            {"data": {"children": [{"data": {
                "id": "1null10", "subreddit": "Ghostty", "subreddit_type": "public",
                "title": "Ghostty terminal", "score": 5, "num_comments": 2,
                "created_utc": IN_WINDOW_UTC, "permalink": "/r/Ghostty/comments/1null10/t/",
                "selftext": "ok",
            }}]}},
            {"data": {"children": [
                {"kind": "t1", "data": {"author": "a", "body": "one", "score": None}},
                {"kind": "t1", "data": {"author": "b", "body": "two", "score": 10}},
            ]}},
        ])
        good_body = _thread_json("1good11", subreddit="Ghostty", subreddit_type="public")

        def fake_get(url, _cookie, **_k):
            if "1priv09.json" in url:
                return 200, priv_body, False
            if "1null10.json" in url:
                return 200, null_score_body, False
            return 200, good_body, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        monkeypatch.setattr(
            reddit_arctic,
            "fetch_posts_by_ids",
            lambda ids, **_k: ({pid: _archive_post(pid) for pid in ids}, "ok"),
        )
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        refs = [
            _ref("1priv09", "secretclub", provenance="host"),
            _ref("1null10", "Ghostty", provenance="host"),
            _ref("1good11", "Ghostty", provenance="host"),
        ]
        result = reddit_hydrate.hydrate(
            refs,
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(_session()),
            now=1.9e9,
        )
        ids = [item["metadata"]["post_id"] for item in result.items]
        assert "1priv09" not in ids
        assert "1good11" in ids

    def test_mid_hydration_expiry_preserves_prior_drop_and_releases_trailing_streak(self, monkeypatch):
        responses = {
            "1good01": (200, _thread_json("1good01", subreddit="Ghostty")),
            "1priv02": (403, '{"reason": "private", "message": "Forbidden", "error": 403}'),
            "1good03": (200, _thread_json("1good03", subreddit="Ghostty")),
            "1pub004": (403, BLOCKED_HTML),
            "1pub005": (403, BLOCKED_HTML),
            "1pub006": (403, BLOCKED_HTML),
        }
        fetched_by_session: list[str] = []

        def fake_get(url, _cookie, **_k):
            pid = url.split("/comments/", 1)[1].split(".json", 1)[0]
            fetched_by_session.append(pid)
            status, body = responses[pid]
            return status, body, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        arctic_requested: list[list[str]] = []

        def fake_arctic_ids(ids, **_k):
            arctic_requested.append(list(ids))
            return ({pid: _archive_post(pid) for pid in ids if pid.startswith("1pub")}, "ok")

        monkeypatch.setattr(reddit_arctic, "fetch_posts_by_ids", fake_arctic_ids)
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        session = _session()
        refs = [_ref(pid, provenance="host") for pid in responses]
        result = reddit_hydrate.hydrate(
            refs,
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session),
            now=1.9e9,
        )
        assert session.state == reddit_session.EXPIRED
        assert reddit_health.entries()["session"]["state"] == reddit_health.BLOCKED
        assert "1pub006" not in fetched_by_session
        assert arctic_requested == [["1pub004", "1pub005", "1pub006"]]
        returned_ids = [item["metadata"]["post_id"] for item in result.items]
        assert returned_ids == ["1good01", "1good03", "1pub004", "1pub005", "1pub006"]
        assert "1priv02" not in returned_ids

    def test_single_ref_logged_out_403_on_unverified_session_expires_and_allows_arctic(self, monkeypatch):
        monkeypatch.setattr(http, "reddit_session_get", lambda *_a, **_k: (403, BLOCKED_HTML, False))
        monkeypatch.setattr(
            reddit_arctic,
            "fetch_posts_by_ids",
            lambda ids, **_k: ({pid: _archive_post(pid) for pid in ids}, "ok"),
        )
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        session = _session()
        assert not session.verified
        result = reddit_hydrate.hydrate(
            [_ref("1pub101", provenance="host")],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session),
            now=1.9e9,
        )
        assert session.state == reddit_session.EXPIRED
        assert [item["metadata"]["post_id"] for item in result.items] == ["1pub101"]

    def test_single_ref_opaque_403_on_unverified_host_ref_fails_closed_with_drop(self, monkeypatch):
        monkeypatch.setattr(http, "reddit_session_get", lambda *_a, **_k: (403, "Forbidden", False))
        monkeypatch.setattr(
            reddit_arctic,
            "fetch_posts_by_ids",
            lambda ids, **_k: ({pid: _archive_post(pid) for pid in ids}, "ok"),
        )
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        session = _session()
        result = reddit_hydrate.hydrate(
            [_ref("1pub102", provenance="web")],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session),
            now=1.9e9,
        )
        assert session.alive
        assert result.items == []

    def test_explicit_private_json_403_always_drops_and_does_not_expire_session(self, monkeypatch):
        private_403 = '{"reason": "private", "message": "Forbidden", "error": 403}'
        monkeypatch.setattr(http, "reddit_session_get", lambda *_a, **_k: (403, private_403, False))
        monkeypatch.setattr(
            reddit_arctic,
            "fetch_posts_by_ids",
            lambda ids, **_k: ({pid: _archive_post(pid) for pid in ids}, "ok"),
        )
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        session = _session()
        result = reddit_hydrate.hydrate(
            [_ref("1priv10", "secret", provenance="host"), _ref("1priv11", "secret", provenance="host")],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session),
            now=1.9e9,
        )
        assert session.alive
        assert result.items == []

    def test_single_ref_session_only_logged_out_403_still_drops_if_absent_from_arctic(self, monkeypatch):
        monkeypatch.setattr(http, "reddit_session_get", lambda *_a, **_k: (403, BLOCKED_HTML, False))
        monkeypatch.setattr(reddit_arctic, "fetch_posts_by_ids", lambda *_a, **_k: ({}, "ok"))
        monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)

        session = _session()
        result = reddit_hydrate.hydrate(
            [_ref("1priv12", "secret", provenance="session")],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session),
            now=1.9e9,
        )
        assert session.state == reddit_session.EXPIRED
        assert result.items == []


# ---------------------------------------------------------------------------
# HIGH-129: Single-flight deduplication in discover() and Session.fetch()
# ---------------------------------------------------------------------------


class TestHigh129SessionSingleFlight:
    def test_parallel_discover_coalesces_into_single_run_and_preserves_budget(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        calls_lock = threading.Lock()

        def fake_get(url, _cookie, **_k):
            with calls_lock:
                calls.append(url)
                first = len(calls) == 1
            if first:
                started.set()
                release.wait(timeout=5.0)
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            if "type=comments" in url:
                return 200, NO_COMMENTS_HTML, False
            if "type=communities" in url:
                return 200, COMMUNITIES_HTML, False
            return 404, "", False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        session = _session()

        def run_one():
            with reddit_session.lane(session, TOPIC):
                return reddit_session.discover(session, TOPIC, depth="quick", from_date=FROM, to_date=TO)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            f0 = pool.submit(run_one)
            assert started.wait(timeout=3.0)
            waiters = [pool.submit(run_one) for _ in range(3)]
            time.sleep(0.05)
            release.set()
            results = [f.result(timeout=5.0) for f in (f0, *waiters)]

        assert all(r.state == reddit_health.OK and r.refs for r in results)
        # Every thread gets its own deep copy of refs.
        results[0].refs[0].title = "mutated"
        assert all(r.refs[0].title != "mutated" for r in results[1:])
        # Only one discovery run executed against Reddit and charged REQUEST_CAP.
        post_search_calls = [u for u in calls if "type=posts" in u and "/r/" not in u]
        assert len(post_search_calls) == 1
        assert session.requests == len(calls)

    def test_discover_cache_isolates_different_named_subreddits(self, monkeypatch):
        calls: list[str] = []

        def fake_get(url, _cookie, **_k):
            calls.append(url)
            if "type=posts" in url:
                ctx = json.dumps({
                    "action_info": {"type": "post"},
                    "post": {"id": "t3_1priv99", "nsfw": False},
                    "subreddit": {"name": "PrivateA", "nsfw": False, "quarantined": True},
                }).replace('"', "&quot;")
                body = (
                    "user-logged-in "
                    f'<search-telemetry-tracker view-events="search/view/post" '
                    f'data-faceplate-tracking-context="{ctx}"></search-telemetry-tracker>'
                )
                return 200, body, False
            return 200, NO_COMMENTS_HTML, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        monkeypatch.setattr(
            reddit_session.reddit_search,
            "parse_page",
            lambda _b, _t: ([{
                "title": "Ghostty private",
                "url": "https://www.reddit.com/r/PrivateA/comments/1priv99/",
                "subreddit": "PrivateA",
                "date": "2026-11-11",
                "metadata": {"post_id": "1priv99"},
            }], None),
        )
        monkeypatch.setattr(reddit_session.reddit_search, "unrecognized_body", lambda _b: None)
        monkeypatch.setattr(reddit_session.reddit_search, "next_cursor", lambda _b: None)

        session = _session()
        with reddit_session.lane(session, "entity-a", named=frozenset({"privatea"})):
            found_a = reddit_session.discover(session, TOPIC, depth="quick")
        with reddit_session.lane(session, "entity-b", named=frozenset()):
            found_b = reddit_session.discover(session, TOPIC, depth="quick")

        assert [r.post_id for r in found_a.refs] == ["1priv99"]
        assert found_b.refs == []

    def test_discover_waiter_respects_deadline_and_cancel_without_poisoning_cache(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()

        def fake_get(url, _cookie, **_k):
            started.set()
            release.wait(timeout=5.0)
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            return 200, NO_COMMENTS_HTML, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        session = _session()
        cancel_ev = threading.Event()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            owner_fut = pool.submit(reddit_session.discover, session, TOPIC, depth="quick")
            assert started.wait(timeout=3.0)

            short_deadline = time.monotonic() + 0.08
            t0 = time.monotonic()
            timed_out = reddit_session.discover(session, TOPIC, depth="quick", deadline=short_deadline)
            assert timed_out.state == reddit_session.ERROR
            assert time.monotonic() - t0 < 1.0

            cancel_fut = pool.submit(reddit_session.discover, session, TOPIC, depth="quick", cancel=cancel_ev)
            time.sleep(0.03)
            cancel_ev.set()
            cancelled = cancel_fut.result(timeout=2.0)
            assert cancelled.state == reddit_session.ERROR

            release.set()
            owner_res = owner_fut.result(timeout=5.0)
            assert owner_res.state == reddit_health.OK and owner_res.refs

        # Subsequent call hits the clean cached result at zero extra requests.
        before = session.requests
        cached = reddit_session.discover(session, TOPIC, depth="quick")
        assert cached.state == reddit_health.OK and len(cached.refs) == len(owner_res.refs)
        assert session.requests == before

    def test_discover_owner_error_does_not_poison_cache(self, monkeypatch):
        attempt = {"n": 0}

        def fake_get(url, _cookie, **_k):
            attempt["n"] += 1
            if attempt["n"] == 1:
                return None, None, False
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            return 200, NO_COMMENTS_HTML, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        session = _session()
        first = reddit_session.discover(session, TOPIC, depth="quick")
        assert first.state == reddit_session.ERROR
        second = reddit_session.discover(session, TOPIC, depth="quick")
        assert second.state == reddit_health.OK and second.refs

    def test_partial_discover_timeout_or_budget_truncation_is_not_cached(self, monkeypatch):
        comment_attempts = {"n": 0}

        def fake_get(url, _cookie, **_k):
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            if "type=comments" in url:
                comment_attempts["n"] += 1
                if comment_attempts["n"] == 1:
                    # Simulate timeout on comments page after posts succeeded.
                    return None, None, False
                return 200, COMMENTS_HTML, False
            if "type=communities" in url:
                return 200, NO_COMMENTS_HTML, False
            return 404, "", False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        session = _session()
        partial = reddit_session.discover(session, TOPIC, depth="quick")
        assert partial.state == reddit_health.OK
        assert partial.refs and not partial.comments
        assert session._discover_cache == {}

        # A second caller with more time must NOT reuse the partial search.
        complete = reddit_session.discover(session, TOPIC, depth="quick")
        assert complete.state == reddit_health.OK
        assert complete.comments
        assert comment_attempts["n"] == 2
        assert len(session._discover_cache) == 1

        # Cap exhaustion before comments also leaves the partial search uncached.
        capped_session = _session(cap=1)
        capped = reddit_session.discover(capped_session, TOPIC, depth="quick")
        assert capped.state == reddit_health.OK and capped.refs and not capped.comments
        assert capped_session._discover_cache == {}

    def test_waiting_discover_falls_back_to_partial_posts_when_retry_cannot_fetch(self, monkeypatch):
        # Case 1: owner gets posts then exhausts shared cap while a waiter is waiting.
        # Partial result stays uncached, and the waiter keeps the owner's partial posts.
        owner_started = threading.Event()
        waiter_waiting = threading.Event()
        orig_wait = reddit_session._wait_event

        def spy_wait(event, deadline, cancel=None):
            waiter_waiting.set()
            return orig_wait(event, deadline, cancel)

        def fake_get_capped(url, _cookie, **_k):
            owner_started.set()
            assert waiter_waiting.wait(timeout=5.0)
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            return 200, COMMENTS_HTML, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get_capped)
        monkeypatch.setattr(reddit_session, "_wait_event", spy_wait)
        capped = _session(cap=1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_owner = pool.submit(reddit_session.discover, capped, TOPIC, depth="quick")
            assert owner_started.wait(timeout=3.0)
            f_waiter = pool.submit(reddit_session.discover, capped, TOPIC, depth="quick")
            owner_res = f_owner.result(timeout=5.0)
            waiter_res = f_waiter.result(timeout=5.0)

        assert owner_res.state == reddit_health.OK and owner_res.refs
        assert waiter_res.state == reddit_health.OK
        assert [r.post_id for r in waiter_res.refs] == [r.post_id for r in owner_res.refs]
        assert waiter_res.refs[0] is not owner_res.refs[0]
        assert capped._discover_cache == {}

        # Case 2: owner gets posts then comments times out; waiter retries with room
        # left, its retry errors out empty, so it falls back to the owner's partial
        # posts (never cached); a later caller with working responses completes & caches.
        owner_started.clear()
        waiter_waiting.clear()
        phase = {"calls": 0}

        def fake_get_retry_fail(url, _cookie, **_k):
            phase["calls"] += 1
            if phase["calls"] == 1:
                owner_started.set()
                assert waiter_waiting.wait(timeout=5.0)
                return 200, POSTS_HTML, False
            if phase["calls"] == 2:
                # Owner's comments request times out.
                return None, None, False
            if phase["calls"] == 3:
                # Waiter's retry hits a transient error on posts page.
                return None, None, False
            if "type=posts" in url:
                return 200, POSTS_HTML, False
            if "type=comments" in url:
                return 200, COMMENTS_HTML, False
            return 200, NO_COMMENTS_HTML, False

        monkeypatch.setattr(http, "reddit_session_get", fake_get_retry_fail)
        session = _session()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_owner = pool.submit(reddit_session.discover, session, TOPIC, depth="quick")
            assert owner_started.wait(timeout=3.0)
            f_waiter = pool.submit(reddit_session.discover, session, TOPIC, depth="quick")
            owner_res2 = f_owner.result(timeout=5.0)
            waiter_res2 = f_waiter.result(timeout=5.0)

        assert owner_res2.state == reddit_health.OK and owner_res2.refs
        assert waiter_res2.state == reddit_health.OK
        assert [r.post_id for r in waiter_res2.refs] == [r.post_id for r in owner_res2.refs]
        assert session._discover_cache == {}

        # Later caller still retries and caches the complete search.
        monkeypatch.setattr(reddit_session, "_wait_event", orig_wait)
        complete_res = reddit_session.discover(session, TOPIC, depth="quick")
        assert complete_res.state == reddit_health.OK and complete_res.comments
        assert len(session._discover_cache) == 1

    def test_parallel_session_fetch_coalesces_same_url_and_respects_waiter_cancel(self, monkeypatch):
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def fake_get(url, _cookie, **_k):
            calls.append(url)
            started.set()
            release.wait(timeout=5.0)
            return 200, '{"ok": true}', False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        session = _session()
        url = reddit_session.thread_url("1same01")
        cancel_ev = threading.Event()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            f0 = pool.submit(session.fetch, url, accept="application/json")
            assert started.wait(timeout=3.0)
            waiters = [pool.submit(session.fetch, url, accept="application/json") for _ in range(3)]
            cancel_waiter = pool.submit(session.fetch, url, accept="application/json", cancel=cancel_ev)
            time.sleep(0.03)
            cancel_ev.set()
            assert cancel_waiter.result(timeout=2.0) == (None, None)
            release.set()
            results = [f.result(timeout=5.0) for f in (f0, *waiters)]

        assert len(calls) == 1
        assert session.requests == 1
        assert all(res == (200, '{"ok": true}') for res in results)

    def test_fetch_exhausted_owner_entity_does_not_starve_other_entity_waiter(self, monkeypatch):
        calls: list[str] = []

        def fake_get(url, _cookie, **_k):
            calls.append(url)
            return 200, '{"ok": true}', False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        # 2 entities, cap=2 => 1 request per entity.
        session = _session(entity_count=2, cap=2)
        with reddit_session.lane(session, "entity-a"):
            assert session.fetch(reddit_session.thread_url("1used01"), accept="application/json") == (200, '{"ok": true}')

        # entity-a is now out of budget; hold entity-a inside _take() while its
        # inflight slot is active until entity-b is genuinely waiting on it.
        url2 = reddit_session.thread_url("1used02")
        owner_in_take = threading.Event()
        waiter_waiting = threading.Event()
        waiter_entered = {"seen": False}

        orig_take = session._take

        def gated_take(entity: str) -> bool:
            if entity == "entity-a":
                owner_in_take.set()
                assert waiter_waiting.wait(timeout=5.0), "waiter never entered _wait_event"
            return orig_take(entity)

        orig_wait_event = reddit_session._wait_event

        def spy_wait_event(event, deadline, cancel=None):
            waiter_entered["seen"] = True
            waiter_waiting.set()
            return orig_wait_event(event, deadline, cancel)

        monkeypatch.setattr(session, "_take", gated_take)
        monkeypatch.setattr(reddit_session, "_wait_event", spy_wait_event)

        def run_entity(name: str):
            with reddit_session.lane(session, name):
                return session.fetch(url2, accept="application/json")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(run_entity, "entity-a")
            assert owner_in_take.wait(timeout=3.0)
            fut_b = pool.submit(run_entity, "entity-b")
            res_a = fut_a.result(timeout=5.0)
            res_b = fut_b.result(timeout=5.0)

        assert waiter_entered["seen"] is True
        assert res_a == (None, None)
        assert res_b == (200, '{"ok": true}')
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# HIGH-130 & HIGH-131: reddit_health private writes, locking & monotonicity
# ---------------------------------------------------------------------------


class TestHigh130HealthPersistenceAndLocking:
    @pytest.mark.skipif(os.name != "posix", reason="POSIX mode bits only")
    def test_health_file_written_0600_and_refuses_symlink(self, tmp_path):
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403", cookie_fp="abcdef0123456789")
        cache = reddit_health.cache_path()
        assert cache is not None and cache.is_file()
        assert stat.S_IMODE(cache.stat().st_mode) == 0o600

        # Replace cache with a symlink pointing outside; _write must refuse and never overwrite target.
        outside = tmp_path / "outside.txt"
        outside.write_text("sentinel", encoding="utf-8")
        cache.unlink()
        cache.symlink_to(outside)
        reddit_health.reset_run_state()
        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")
        assert outside.read_text(encoding="utf-8") == "sentinel"

    def test_failed_replace_leaves_no_temp_files(self, monkeypatch):
        cache = reddit_health.cache_path()
        assert cache is not None
        monkeypatch.setattr(os, "replace", mock.Mock(side_effect=OSError("disk full")))
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403")
        leftovers = [p.name for p in cache.parent.iterdir() if ".tmp" in p.name or p.name.startswith(".reddit-health")]
        assert leftovers == []

    def test_cross_process_concurrent_records_preserve_all_paths(self):
        cache = reddit_health.cache_path()
        assert cache is not None
        config_dir = str(cache.parent)
        paths = ["search", "brave", "ddg", "startpage", "arctic", "session"]
        worker_code = (
            "import sys\n"
            f"sys.path.insert(0, {str(SCRIPTS_DIR)!r})\n"
            "from pathlib import Path\n"
            "from lib import env, reddit_health\n"
            "env.CONFIG_DIR = Path(sys.argv[1])\n"
            "path_name = sys.argv[2]\n"
            "for i in range(6):\n"
            "    reddit_health.reset_run_state()\n"
            "    reddit_health.record(path_name, reddit_health.BLOCKED, f'worker-{i}')\n"
        )
        procs = [
            subprocess.Popen([sys.executable, "-c", worker_code, config_dir, name])
            for name in paths
        ]
        for proc in procs:
            assert proc.wait(timeout=20) == 0
        saved = reddit_health.entries()
        assert set(saved.keys()) == set(paths)
        assert all(saved[p]["state"] == reddit_health.BLOCKED for p in paths)

    def test_lock_failure_skips_disk_write_keeps_memory_and_closes_fd(self, monkeypatch):
        # Seed a persisted entry from an earlier writer.
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403")
        assert "search" in reddit_health.entries()

        closed_fds: list[int] = []
        real_close = os.close

        def spy_close(fd: int):
            closed_fds.append(fd)
            return real_close(fd)

        monkeypatch.setattr(os, "close", spy_close)
        if os.name == "posix":
            import fcntl
            monkeypatch.setattr(fcntl, "flock", mock.Mock(side_effect=OSError("ENOLCK")))
        else:
            monkeypatch.setattr(os, "open", mock.Mock(side_effect=OSError("EACCES")))

        # record() must update in-memory state while skipping the unlocked disk write.
        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")
        assert reddit_health.run_state("brave") == reddit_health.BLOCKED
        assert "brave" not in reddit_health.entries()
        assert reddit_health.entries()["search"]["state"] == reddit_health.BLOCKED
        if os.name == "posix":
            assert len(closed_fds) >= 1

        # clear() must clear in-memory state while skipping the unlocked disk write.
        assert reddit_health.clear() == []
        assert reddit_health.run_state("brave") is None
        assert reddit_health.entries()["search"]["state"] == reddit_health.BLOCKED

    def test_windows_msvcrt_lock_and_unlock_path(self, monkeypatch):
        ops: list[tuple] = []
        fake_msvcrt = types.SimpleNamespace(
            LK_LOCK=1,
            LK_UNLCK=2,
            locking=lambda fd, mode, nbytes: ops.append(("locking", mode, nbytes)),
        )
        monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
        monkeypatch.setattr(reddit_health.os, "name", "nt")
        monkeypatch.setattr(reddit_health.private_files.os, "name", "nt")
        monkeypatch.setattr(
            reddit_health.os,
            "lseek",
            lambda fd, offset, how: ops.append(("lseek", offset, how)) or 0,
        )

        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")
        assert ("locking", 1, 1) in ops
        assert ("locking", 2, 1) in ops
        assert ops.index(("locking", 1, 1)) < ops.index(("locking", 2, 1))
        assert reddit_health.entries()["brave"]["state"] == reddit_health.BLOCKED


class TestHigh131MonotonicHealthState:
    def test_stale_inflight_ok_never_overwrites_persisted_blocked_in_same_run(self):
        fp = "0123456789abcdef"
        reddit_health.record("session", reddit_health.BLOCKED, "session-expired: HTTP 403", cookie_fp=fp)
        reddit_health.record("session", reddit_health.OK, "session search", cookie_fp=fp)

        assert reddit_health.run_state("session") == reddit_health.BLOCKED
        assert reddit_health.entries()["session"]["state"] == reddit_health.BLOCKED
        assert reddit_health.skip_until("session", cookie_fp=fp) is not None

    def test_stale_inflight_ok_after_throttled_does_not_touch_disk(self):
        reddit_health.record("ddg", reddit_health.THROTTLED, "HTTP 429")
        reddit_health.record("ddg", reddit_health.OK, "web search ok")

        assert reddit_health.run_state("ddg") == reddit_health.THROTTLED
        assert "ddg" not in reddit_health.entries()

    def test_throttled_can_escalate_to_blocked_in_same_run(self):
        reddit_health.record("brave", reddit_health.THROTTLED, "HTTP 429")
        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")

        assert reddit_health.run_state("brave") == reddit_health.BLOCKED
        assert reddit_health.entries()["brave"]["state"] == reddit_health.BLOCKED

    def test_new_run_ok_still_clears_prior_run_blocked_skip(self):
        fp = "0123456789abcdef"
        reddit_health.record("session", reddit_health.BLOCKED, "session-expired", cookie_fp=fp)
        reddit_health.reset_run_state()
        reddit_health.record("session", reddit_health.OK, "session search", cookie_fp=fp)

        assert reddit_health.entries()["session"]["state"] == reddit_health.OK
        assert reddit_health.skip_until("session", cookie_fp=fp) is None


# ---------------------------------------------------------------------------
# HIGH-133: reddit_enrich.extract_reddit_path strict URL validation
# ---------------------------------------------------------------------------


class TestHigh133ExtractRedditPath:
    @pytest.mark.parametrize("malicious_url", [
        "",
        None,
        123,
        ["https://www.reddit.com/r/ClaudeAI/comments/abc123/post/"],
        "https://evil-reddit.com/r/ClaudeAI/comments/abc123/post/",
        "https://reddit.com.attacker.com/r/ClaudeAI/comments/abc123/post/",
        "https://reddit.com@attacker.com/r/ClaudeAI/comments/abc123/post/",
        "https://attacker@www.reddit.com/r/ClaudeAI/comments/abc123/post/",
        "https://www.reddit.com:8443/r/ClaudeAI/comments/abc123/post/",
        "https://www.reddit.com:99999/r/ClaudeAI/comments/abc123/post/",
        "https://[::1/r/ClaudeAI/comments/abc123/post/",
        "javascript://reddit.com/r/ClaudeAI/comments/abc123/post/",
        "ftp://www.reddit.com/r/ClaudeAI/comments/abc123/post/",
        "https://www.reddit.com/api/v1/me",
        "https://www.reddit.com//attacker.com/r/ClaudeAI/comments/abc123/post/",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/../../api/v1/me",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/%2e%2e/%2e%2e/api",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%2f..%2fapi",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%3Fextra=1",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%23frag",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%0Ainjected",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%253Fextra=1",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%2523frag",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/slug%2500nul",
        "https://www.reddit.com/r/ClaudeAI/comments/abc123/a/b/c",
        "https://redd.it/abc123",
    ])
    def test_rejects_lookalike_userinfo_port_scheme_and_non_thread_urls(self, malicious_url):
        assert reddit_enrich.extract_reddit_path(malicious_url) is None

    @pytest.mark.parametrize(("url", "expected"), [
        (
            "https://www.reddit.com/r/ClaudeAI/comments/abc123/post_title/",
            "/r/ClaudeAI/comments/abc123/post_title/",
        ),
        (
            "https://old.reddit.com/r/ClaudeAI/comments/abc123/post_title/?utm_source=x#top",
            "/r/ClaudeAI/comments/abc123/post_title/",
        ),
        (
            "https://reddit.com/comments/abc123/",
            "/comments/abc123/",
        ),
        (
            "https://sh.reddit.com/r/Ghostty/comments/1taeiw0/slug/c1d2e3/",
            "/r/Ghostty/comments/1taeiw0/slug/c1d2e3/",
        ),
        (
            "http://www.reddit.com:80/r/Ghostty/comments/1taeiw0/",
            "/r/Ghostty/comments/1taeiw0/",
        ),
        (
            "https://www.reddit.com:443/r/Ghostty/comments/1taeiw0/",
            "/r/Ghostty/comments/1taeiw0/",
        ),
    ])
    def test_accepts_valid_reddit_thread_urls(self, url, expected):
        assert reddit_enrich.extract_reddit_path(url) == expected

    def test_fetch_thread_data_refuses_attacker_url_without_http_call(self):
        with mock.patch.object(reddit_enrich.http, "get_reddit_json") as get_json:
            assert reddit_enrich.fetch_thread_data("https://evil-reddit.com/api/v1/me") is None
            assert reddit_enrich.fetch_thread_data("https://reddit.com@attacker.com/r/x/comments/abc/") is None
        get_json.assert_not_called()
