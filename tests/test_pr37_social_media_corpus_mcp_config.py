"""Regression tests for PR 37: YouTube root comments, Bluesky lock/cache,
LinkedIn 404 isolation, Corpus PDF streaming/encoding, and MCP manifest config.

Covers:
- HIGH-179: yt-dlp max_comments zero-reply 4-tuple, root-parent filtering, post-filter cap
- HIGH-180: Bluesky thread-safe SHA-256 keyed session cache, stampede/rotation races, eviction
- HIGH-188: LinkedIn "No posts found" 404 & search_profile failure-sink isolation
- HIGH-217: Corpus pdftotext UTF-8/separator flags, streaming cap, timeout, UnicodeError safety
- HIGH-221: MCP manifest BSKY_HANDLE + BSKY_APP_PASSWORD pairing
- HIGH-222: MCP manifest LAST30DAYS_X_BACKEND + X_BEARER_TOKEN xapi opt-in
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from lib import backends, bluesky, corpus, doctor, env, health, http, linkedin, pipeline, subproc, youtube_yt
from lib.subproc import SubprocResult

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# HIGH-179: yt-dlp root comments, zero replies, post-filter cap
# ---------------------------------------------------------------------------


class TestHigh179YoutubeRootComments:
    def test_ytdlp_command_requests_zero_replies_with_buffer_and_no_cookies(self) -> None:
        payload = json.dumps({"id": "abc123", "comments": []})
        res = SubprocResult(returncode=0, stdout=payload, stderr="")

        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=res) as run_mock:
            comments, ran_cleanly = youtube_yt._ytdlp_comments_result("abc123", max_comments=5)

        assert comments == []
        assert ran_cleanly is True
        cmd = run_mock.call_args.args[0]
        assert "--no-cookies-from-browser" in cmd
        spec = cmd[cmd.index("--extractor-args") + 1]
        assert "comment_sort=top" in spec
        assert "max_comments=7,7,0,0" in spec
        assert ",all," not in spec

    def test_zero_max_comments_short_circuits_cleanly_without_spawning(self) -> None:
        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout") as run_mock:
            comments, ran_cleanly = youtube_yt._ytdlp_comments_result("abc123", max_comments=0)

        assert comments == []
        assert ran_cleanly is True
        run_mock.assert_not_called()

    def test_filters_nested_replies_non_dicts_and_empty_text_before_cap(self) -> None:
        raw_comments = [
            {
                "id": "c1",
                "parent": "root",
                "author": "@ChannelOwner",
                "author_is_uploader": True,
                "text": "Pinned creator note",
                "like_count": 500,
                "_time_text": "3 days ago",
            },
            {
                "id": "c1.r1",
                "parent": "c1",
                "author": "@replier1",
                "text": "Nested reply 1 to pinned note",
                "like_count": 2,
            },
            {
                "id": "c1.r2",
                "parent": "c1",
                "author": "@replier2",
                "text": "Nested reply 2 to pinned note",
                "like_count": 1,
            },
            "not-a-dict-entry",
            None,
            {"id": "c2", "parent": "root", "author": "@empty", "text": "   \n\t  ", "like_count": 9},
            {
                "id": "c3",
                "parent": "root",
                "author": "@viewerA",
                "author_is_uploader": False,
                "text": "  First real community parent comment  ",
                "like_count": 210,
                "_time_text": "2 days ago",
            },
            {
                "id": "c4",
                "author": "@viewerB",
                "text": "Legacy shape omitting parent defaults to root",
                "like_count": 95,
                "_time_text": "1 day ago",
            },
            {
                "id": "c5",
                "parent": "root",
                "author": "@viewerC",
                "text": "Fourth root comment beyond max_comments=3",
                "like_count": 40,
            },
        ]
        payload = json.dumps({"id": "abc123", "channel_id": "UC_CH", "comments": raw_comments})
        res = SubprocResult(returncode=0, stdout=payload, stderr="")

        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=res):
            comments, ran_cleanly = youtube_yt._ytdlp_comments_result("abc123", max_comments=3)

        assert ran_cleanly is True
        assert [c["author"] for c in comments] == ["@ChannelOwner", "@viewerA", "@viewerB"]
        assert comments[0]["is_uploader"] is True
        assert comments[1]["text"] == "First real community parent comment"
        assert comments[1]["is_uploader"] is False

    def test_malformed_top_level_json_or_non_list_comments_is_handled(self) -> None:
        list_res = SubprocResult(returncode=0, stdout="[]", stderr="")
        bad_comments_res = SubprocResult(returncode=0, stdout='{"comments": "not-a-list"}', stderr="")

        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True):
            with mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=list_res):
                assert youtube_yt._ytdlp_comments_result("abc123", max_comments=3) == ([], False)
            with mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=bad_comments_res):
                assert youtube_yt._ytdlp_comments_result("abc123", max_comments=3) == ([], True)

    def test_skips_non_string_text_values_instead_of_stringifying(self) -> None:
        raw_comments = [
            {"parent": "root", "author": "@bool", "text": False, "like_count": 50},
            {"parent": "root", "author": "@list", "text": [], "like_count": 40},
            {"parent": "root", "author": "@dict", "text": {}, "like_count": 30},
            {"parent": "root", "author": "@none", "text": None, "like_count": 20},
            {"parent": "root", "author": "@int", "text": 123, "like_count": 15},
            {"parent": "root", "author": "@real", "text": "Actual viewer comment", "like_count": 10},
        ]
        payload = json.dumps({"id": "abc123", "comments": raw_comments})
        res = SubprocResult(returncode=0, stdout=payload, stderr="")

        with mock.patch.object(youtube_yt, "is_ytdlp_installed", return_value=True), \
             mock.patch.object(youtube_yt.subproc, "run_with_timeout", return_value=res):
            comments, ran_cleanly = youtube_yt._ytdlp_comments_result("abc123", max_comments=3)

        assert ran_cleanly is True
        assert [c["author"] for c in comments] == ["@real"]
        assert comments[0]["text"] == "Actual viewer comment"


# ---------------------------------------------------------------------------
# HIGH-180: Bluesky locked/keyed cache, stampede & refresh rotation safety
# ---------------------------------------------------------------------------


class TestHigh180BlueskySessionConcurrency:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        bluesky._reset_session_cache()
        yield
        bluesky._reset_session_cache()

    def test_module_error_globals_removed_and_cache_key_hashes_password(self) -> None:
        for legacy_global in (
            "_session_error",
            "_refresh_error_status",
            "_cached_token",
            "_cached_refresh_token",
            "_token_created_at",
        ):
            assert not hasattr(bluesky, legacy_global), f"race-prone global {legacy_global} still exists"

        secret_pw = "super-secret-app-pw-1234"
        expected_digest = hashlib.sha256(secret_pw.encode("utf-8")).hexdigest()
        with mock.patch.object(
            bluesky.http, "request", return_value={"accessJwt": "tok-a", "refreshJwt": "ref-a"}
        ):
            tok, err, status = bluesky._create_session("alice.bsky.social", secret_pw)

        assert (tok, err, status) == ("tok-a", None, None)
        keys = list(bluesky._session_cache.keys())
        assert keys == [("alice.bsky.social", expected_digest)]
        assert all(secret_pw not in str(k) for k in keys)
        assert all(secret_pw not in str(k) for k in bluesky._inflight_auth.keys())

    def test_cross_account_and_password_change_isolation(self) -> None:
        issued = iter([
            {"accessJwt": "tok-alice-1", "refreshJwt": "ref-a1"},
            {"accessJwt": "tok-bob-1", "refreshJwt": "ref-b1"},
            {"accessJwt": "tok-alice-2", "refreshJwt": "ref-a2"},
        ])

        def fake_request(method, url, **kwargs):
            if url == bluesky.BSKY_SESSION_URL:
                return next(issued)
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/1", "author": {"handle": "u"}, "record": {"text": kwargs["headers"]["Authorization"]}}]}

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request) as req_mock:
            r1 = bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config={"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "pw-1"})
            r2 = bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config={"BSKY_HANDLE": "bob.bsky.social", "BSKY_APP_PASSWORD": "pw-2"})
            r3 = bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config={"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "pw-new"})
            # Repeat alice/pw-1 hits cache, not createSession again.
            r4 = bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config={"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "pw-1"})

        assert r1["posts"][0]["record"]["text"] == "Bearer tok-alice-1"
        assert r2["posts"][0]["record"]["text"] == "Bearer tok-bob-1"
        assert r3["posts"][0]["record"]["text"] == "Bearer tok-alice-2"
        assert r4["posts"][0]["record"]["text"] == "Bearer tok-alice-1"
        create_calls = [c for c in req_mock.call_args_list if c.args[1] == bluesky.BSKY_SESSION_URL]
        assert len(create_calls) == 3

    def test_concurrent_create_session_prevents_stampede(self) -> None:
        create_calls = 0
        create_lock = threading.Lock()
        start_barrier = threading.Barrier(8)

        def fake_request(method, url, **kwargs):
            nonlocal create_calls
            if url == bluesky.BSKY_SESSION_URL:
                with create_lock:
                    create_calls += 1
                time.sleep(0.05)
                return {"accessJwt": "tok-shared", "refreshJwt": "ref-shared"}
            assert kwargs["headers"] == {"Authorization": "Bearer tok-shared"}
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/1", "author": {"handle": "u"}, "record": {"text": "ok"}}]}

        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}

        def worker(idx: int):
            start_barrier.wait(timeout=5)
            return bluesky.search_bluesky(f"topic {idx}", "2026-08-01", "2026-09-01", config=cfg)

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(worker, range(8)))

        assert create_calls == 1
        assert all("error" not in r and len(r["posts"]) == 1 for r in results)

    def test_concurrent_failed_create_session_shares_error_and_allows_later_retry(self) -> None:
        create_calls = 0
        create_lock = threading.Lock()
        start_barrier = threading.Barrier(6)
        fail_first = True

        def fake_request(method, url, **kwargs):
            nonlocal create_calls
            if url == bluesky.BSKY_SESSION_URL:
                with create_lock:
                    create_calls += 1
                    should_fail = fail_first
                if should_fail:
                    time.sleep(0.08)
                    raise http.HTTPError("HTTP 503: Service Unavailable", 503, "")
                return {"accessJwt": "tok-recovered", "refreshJwt": "ref-recovered"}
            assert kwargs["headers"] == {"Authorization": "Bearer tok-recovered"}
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/ok", "author": {"handle": "u"}, "record": {"text": "ok"}}]}

        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}

        def worker(idx: int):
            start_barrier.wait(timeout=5)
            return bluesky.search_bluesky(f"topic {idx}", "2026-08-01", "2026-09-01", config=cfg)

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            start = time.monotonic()
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(worker, range(6)))
            elapsed = time.monotonic() - start

            assert create_calls == 1
            assert elapsed < 1.0
            assert all(r["posts"] == [] and "503" in r["error"] for r in results)
            assert bluesky._inflight_auth == {}

            # A subsequent search after the in-flight attempt completes tries again.
            fail_first = False
            later = bluesky.search_bluesky("later", "2026-08-01", "2026-09-01", config=cfg)

        assert create_calls == 2
        assert "error" not in later
        assert len(later["posts"]) == 1

    def test_singleflight_clears_slot_and_wakes_waiters_on_leader_base_exception(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def aborting_request(method, url, **kwargs):
            entered.set()
            release.wait(timeout=5)
            raise KeyboardInterrupt("simulated abort")

        with mock.patch.object(bluesky.http, "request", side_effect=aborting_request):
            leader_exc: list[BaseException] = []
            waiter_result: list[tuple] = []

            def run_leader() -> None:
                try:
                    bluesky._create_session("alice.bsky.social", "abcd-efgh-ijkl-mnop")
                except BaseException as exc:
                    leader_exc.append(exc)

            def run_waiter() -> None:
                entered.wait(timeout=5)
                waiter_result.append(
                    bluesky._create_session("alice.bsky.social", "abcd-efgh-ijkl-mnop")
                )

            t1 = threading.Thread(target=run_leader)
            t2 = threading.Thread(target=run_waiter)
            t1.start()
            t2.start()
            entered.wait(timeout=5)
            time.sleep(0.05)
            release.set()
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert not t1.is_alive() and not t2.is_alive()
        assert len(leader_exc) == 1 and isinstance(leader_exc[0], KeyboardInterrupt)
        assert waiter_result[0][0] is None
        assert "KeyboardInterrupt" in (waiter_result[0][1] or "")
        assert bluesky._inflight_auth == {}

    def test_concurrent_401_single_use_refresh_rotation_reuses_fresh_token(self) -> None:
        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}
        cred_key = bluesky._credential_key(cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"])
        bluesky._session_cache[cred_key] = bluesky._SessionEntry(
            access_token="tok-stale",
            refresh_token="ref-single-use",
            created_at=time.monotonic(),
        )

        # Synchronize both threads so both observe 401 on tok-stale before either recovers.
        both_got_401 = threading.Barrier(2)
        state_lock = threading.Lock()
        refresh_calls = 0
        session_calls = 0
        ref_consumed = False

        def fake_request(method, url, **kwargs):
            nonlocal refresh_calls, session_calls, ref_consumed
            if url == bluesky.BSKY_SESSION_URL:
                with state_lock:
                    session_calls += 1
                raise AssertionError("createSession must not run when single-use refresh succeeded")
            if url == bluesky.BSKY_REFRESH_URL:
                with state_lock:
                    refresh_calls += 1
                    if ref_consumed:
                        # Real AT Protocol rejects a consumed single-use refresh JWT.
                        raise http.HTTPError("HTTP 400: Refresh token already used", 400, "")
                    ref_consumed = True
                time.sleep(0.05)
                return {"accessJwt": "tok-fresh", "refreshJwt": "ref-fresh"}
            auth = kwargs["headers"]["Authorization"]
            if auth == "Bearer tok-stale":
                both_got_401.wait(timeout=5)
                raise http.HTTPError("HTTP 401: ExpiredToken", 401, "")
            assert auth == "Bearer tok-fresh"
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/ok", "author": {"handle": "u"}, "record": {"text": "rotated"}}]}

        def worker(idx: int):
            return bluesky.search_bluesky(f"query {idx}", "2026-08-01", "2026-09-01", config=cfg)

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker, range(2)))

        assert refresh_calls == 1
        assert session_calls == 0
        assert all("error" not in r and len(r["posts"]) == 1 for r in results)
        entry = bluesky._session_cache[cred_key]
        assert entry.access_token == "tok-fresh"
        assert entry.refresh_token == "ref-fresh"

    def test_concurrent_401_rejected_refresh_reauthenticates_once(self) -> None:
        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}
        cred_key = bluesky._credential_key(cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"])
        bluesky._session_cache[cred_key] = bluesky._SessionEntry(
            access_token="tok-stale",
            refresh_token="ref-dead",
            created_at=time.monotonic(),
        )

        both_got_401 = threading.Barrier(2)
        state_lock = threading.Lock()
        refresh_calls = 0
        session_calls = 0

        def fake_request(method, url, **kwargs):
            nonlocal refresh_calls, session_calls
            if url == bluesky.BSKY_REFRESH_URL:
                with state_lock:
                    refresh_calls += 1
                raise http.HTTPError("HTTP 401: Invalid refresh", 401, "")
            if url == bluesky.BSKY_SESSION_URL:
                with state_lock:
                    session_calls += 1
                time.sleep(0.04)
                return {"accessJwt": "tok-reauthed", "refreshJwt": "ref-reauthed"}
            if kwargs["headers"]["Authorization"] == "Bearer tok-stale":
                both_got_401.wait(timeout=5)
                raise http.HTTPError("HTTP 401: Expired", 401, "")
            assert kwargs["headers"]["Authorization"] == "Bearer tok-reauthed"
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/ok", "author": {"handle": "u"}, "record": {"text": "reauthed"}}]}

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda i: bluesky.search_bluesky(f"q{i}", "2026-08-01", "2026-09-01", config=cfg), range(2)))

        assert refresh_calls == 1
        assert session_calls == 1
        assert all("error" not in r and len(r["posts"]) == 1 for r in results)

    @pytest.mark.parametrize("refresh_succeeds", [True, False])
    def test_post_recovery_401_evicts_rejected_token_from_cache(self, refresh_succeeds: bool) -> None:
        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}
        cred_key = bluesky._credential_key(cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"])
        unauth = http.HTTPError("HTTP 401: Unauthorized", 401, "")

        if refresh_succeeds:
            side_effects = [
                {"accessJwt": "tok-1", "refreshJwt": "ref-1"},
                unauth,
                {"accessJwt": "tok-bad-refreshed", "refreshJwt": "ref-2"},
                unauth,
            ]
        else:
            side_effects = [
                {"accessJwt": "tok-1", "refreshJwt": "ref-1"},
                unauth,
                unauth,  # refresh rejected
                {"accessJwt": "tok-bad-reauthed", "refreshJwt": "ref-3"},
                unauth,
            ]

        with mock.patch.object(bluesky.http, "request", side_effect=side_effects):
            result = bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config=cfg)

        assert result["posts"] == []
        assert "remained unauthorized" in result["error"]
        assert cred_key not in bluesky._session_cache

    def test_401_recovery_joins_inflight_login_without_overwriting_newer_session(self) -> None:
        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}
        cred_key = bluesky._credential_key(cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"])
        bluesky._session_cache[cred_key] = bluesky._SessionEntry(
            access_token="tok-old",
            refresh_token="ref-old",
            created_at=time.monotonic() - bluesky._TOKEN_MAX_AGE_SECONDS - 10,
        )

        login_entered = threading.Event()
        release_login = threading.Event()
        state_lock = threading.Lock()
        session_calls = 0
        refresh_calls = 0

        def fake_request(method, url, **kwargs):
            nonlocal session_calls, refresh_calls
            if url == bluesky.BSKY_SESSION_URL:
                with state_lock:
                    session_calls += 1
                login_entered.set()
                assert release_login.wait(timeout=5)
                return {"accessJwt": "tok-newer-login", "refreshJwt": "ref-newer-login"}
            if url == bluesky.BSKY_REFRESH_URL:
                with state_lock:
                    refresh_calls += 1
                raise AssertionError("refreshSession must not race an in-flight createSession")
            assert kwargs["headers"] == {"Authorization": "Bearer tok-newer-login"}
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/ok", "author": {"handle": "u"}, "record": {"text": "fresh"}}]}

        login_result: list[dict] = []
        recovery_result: list[tuple] = []

        def run_expired_search() -> None:
            login_result.append(
                bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config=cfg)
            )

        def run_401_recovery() -> None:
            assert login_entered.wait(timeout=5)
            recovery_result.append(
                bluesky._recover_session(
                    cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"], cred_key, "tok-old"
                )
            )

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            t_login = threading.Thread(target=run_expired_search)
            t_recover = threading.Thread(target=run_401_recovery)
            t_login.start()
            t_recover.start()
            assert login_entered.wait(timeout=5)
            # Wait briefly so t_recover enters _recover_session and awaits _inflight_auth.
            time.sleep(0.05)
            release_login.set()
            t_login.join(timeout=5)
            t_recover.join(timeout=5)

        assert not t_login.is_alive() and not t_recover.is_alive()
        assert session_calls == 1
        assert refresh_calls == 0
        assert "error" not in login_result[0]
        assert len(login_result[0]["posts"]) == 1
        assert recovery_result == [("tok-newer-login", None, True)]
        entry = bluesky._session_cache[cred_key]
        assert entry.access_token == "tok-newer-login"
        assert entry.refresh_token == "ref-newer-login"
        assert bluesky._inflight_auth == {}

    def test_expired_login_joins_inflight_401_recovery_without_racing_refresh(self) -> None:
        cfg = {"BSKY_HANDLE": "alice.bsky.social", "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}
        cred_key = bluesky._credential_key(cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"])
        bluesky._session_cache[cred_key] = bluesky._SessionEntry(
            access_token="tok-old",
            refresh_token="ref-old",
            created_at=time.monotonic() - bluesky._TOKEN_MAX_AGE_SECONDS - 10,
        )

        refresh_entered = threading.Event()
        release_refresh = threading.Event()
        state_lock = threading.Lock()
        session_calls = 0
        refresh_calls = 0

        def fake_request(method, url, **kwargs):
            nonlocal session_calls, refresh_calls
            if url == bluesky.BSKY_REFRESH_URL:
                with state_lock:
                    refresh_calls += 1
                refresh_entered.set()
                assert release_refresh.wait(timeout=5)
                return {"accessJwt": "tok-refreshed", "refreshJwt": "ref-refreshed"}
            if url == bluesky.BSKY_SESSION_URL:
                with state_lock:
                    session_calls += 1
                raise AssertionError("createSession must not race an in-flight 401 refresh")
            assert kwargs["headers"] == {"Authorization": "Bearer tok-refreshed"}
            return {"posts": [{"uri": "at://did/app.bsky.feed.post/ok", "author": {"handle": "u"}, "record": {"text": "refreshed"}}]}

        recovery_result: list[tuple] = []
        search_result: list[dict] = []

        def run_401_recovery() -> None:
            recovery_result.append(
                bluesky._recover_session(
                    cfg["BSKY_HANDLE"], cfg["BSKY_APP_PASSWORD"], cred_key, "tok-old"
                )
            )

        def run_expired_search() -> None:
            assert refresh_entered.wait(timeout=5)
            search_result.append(
                bluesky.search_bluesky("ai", "2026-08-01", "2026-09-01", config=cfg)
            )

        with mock.patch.object(bluesky.http, "request", side_effect=fake_request):
            t_recover = threading.Thread(target=run_401_recovery)
            t_search = threading.Thread(target=run_expired_search)
            t_recover.start()
            t_search.start()
            assert refresh_entered.wait(timeout=5)
            # Wait briefly so t_search enters _create_session and awaits _inflight_auth.
            time.sleep(0.05)
            release_refresh.set()
            t_recover.join(timeout=5)
            t_search.join(timeout=5)

        assert not t_recover.is_alive() and not t_search.is_alive()
        assert refresh_calls == 1
        assert session_calls == 0
        assert recovery_result == [("tok-refreshed", None, False)]
        assert "error" not in search_result[0]
        assert len(search_result[0]["posts"]) == 1
        entry = bluesky._session_cache[cred_key]
        assert entry.access_token == "tok-refreshed"
        assert entry.refresh_token == "ref-refreshed"
        assert bluesky._inflight_auth == {}


# ---------------------------------------------------------------------------
# HIGH-188: LinkedIn 404 "No posts found" & search_profile failure-sink isolation
# ---------------------------------------------------------------------------


class TestHigh188LinkedinFailureSinkIsolation:
    def test_no_posts_found_404_is_clean_empty_without_polluting_outer_sink(self, monkeypatch) -> None:
        def open_404_no_posts(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(b'{"message": "No posts found"}'),
            )

        monkeypatch.setattr(http, "_open_request", open_404_no_posts)

        with http.capture_failures() as outer_failures:
            result = linkedin.search_linkedin(
                "hyper-niche-topic", "2026-06-01", "2026-06-30", token="dummy-sc-key"
            )

        assert result == {"posts": []}
        assert "error" not in result
        assert outer_failures == []
        # Verify pipeline outcome resolver sees no failure either.
        outcome = pipeline._resolve_stream_outcome("linkedin", {}, outer_failures)
        assert outcome is None

    @pytest.mark.parametrize(
        "body",
        [
            b"<html><title>404 Not Found</title><body>Cannot GET /v1/linkedin/search/posts</body></html>",
            b'{"message": "Route not found"}',
            b"",
        ],
    )
    def test_non_empty_result_404_surfaces_as_error_and_records_in_outer_sink(
        self, monkeypatch, body: bytes
    ) -> None:
        def open_bad_404(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url,
                404,
                "Not Found",
                {},
                io.BytesIO(body),
            )

        monkeypatch.setattr(http, "_open_request", open_bad_404)

        with http.capture_failures() as outer_failures:
            result = linkedin.search_linkedin(
                "ai agents", "2026-06-01", "2026-06-30", token="dummy-sc-key"
            )

        assert result["posts"] == []
        assert "error" in result
        assert len(outer_failures) >= 1
        assert outer_failures[0].status_code == 404

    def test_search_profile_error_does_not_pollute_outer_failure_sink(self, monkeypatch) -> None:
        def open_500(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url,
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b"upstream error"),
            )

        monkeypatch.setattr(http, "_open_request", open_500)
        monkeypatch.setattr(http.time, "sleep", lambda _s: None)

        with http.capture_failures() as outer_failures:
            profile = linkedin.search_profile("https://www.linkedin.com/in/janedoe", "dummy-sc-key")

        assert profile == {}
        assert outer_failures == []


# ---------------------------------------------------------------------------
# HIGH-217: Corpus pdftotext UTF-8 flag, `--`, bounded streaming, UnicodeError
# ---------------------------------------------------------------------------


def _write_fake_pdftotext(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_pdftotext.py"
    script.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class TestHigh217CorpusPdfHardening:
    def test_pdftotext_uses_utf8_and_separator_and_decodes_invalid_bytes_safely(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        args_log = tmp_path / "argv.json"
        fake_bin = _write_fake_pdftotext(
            tmp_path,
            f"""
import json, sys
with open({str(args_log)!r}, "w", encoding="utf-8") as fh:
    json.dump(sys.argv[1:], fh)
sys.stdout.buffer.write(b"MCP servers \\xff\\xfe local pdf notes")
sys.stdout.buffer.flush()
""",
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        pdf = docs / "-dash-name.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        monkeypatch.setattr(corpus, "which", lambda _name: str(fake_bin))

        result = corpus.search(
            "MCP servers",
            [docs],
            from_date="2026-06-01",
            to_date="2026-07-01",
            all_time=True,
            cache_dir=tmp_path / "cache",
        )

        argv = json.loads(args_log.read_text(encoding="utf-8"))
        assert argv == ["-enc", "UTF-8", "--", str(pdf.resolve()), "-"]
        assert len(result.items) == 1
        assert "MCP servers" in result.items[0].body
        assert "\ufffd" in result.items[0].body

    def test_pdftotext_caps_streamed_bytes_and_terminates_huge_output_early(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_bin = _write_fake_pdftotext(
            tmp_path,
            """
import sys
chunk = b"MCP servers " * 4096  # ~48 KiB per write
try:
    for _ in range(1000):  # ~48 MiB total if uncapped
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
except BrokenPipeError:
    pass
""",
        )
        pdf = tmp_path / "huge.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(corpus, "MAX_TEXT_CHARS", 256)

        start = time.monotonic()
        extracted = corpus._extract_text(pdf, pdftotext=str(fake_bin))
        elapsed = time.monotonic() - start

        assert len(extracted) == 256
        assert extracted.startswith("MCP servers ")
        assert elapsed < 5.0

    def test_pdftotext_timeout_kills_hanging_child_and_search_continues(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_bin = _write_fake_pdftotext(
            tmp_path,
            """
import time
time.sleep(30)
""",
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "hang.pdf").write_bytes(b"%PDF-1.4")
        (docs / "good.md").write_text("MCP servers survive a hanging PDF", encoding="utf-8")

        monkeypatch.setattr(corpus, "which", lambda _name: str(fake_bin))
        monkeypatch.setattr(corpus, "PDF_TIMEOUT_SECONDS", 0.3)

        start = time.monotonic()
        result = corpus.search(
            "MCP servers",
            [docs],
            from_date="2026-06-01",
            to_date="2026-07-01",
            all_time=True,
            cache_dir=tmp_path / "cache",
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5.0
        assert [item.title for item in result.items] == ["good"]
        assert any("TimeoutExpired" in note for note in result.notes)
        assert all(str(tmp_path) not in note for note in result.notes)

    def test_search_catches_unicode_error_and_continues_other_files(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        docs = tmp_path / "docs"
        docs.mkdir()
        bad = docs / "bad.pdf"
        bad.write_bytes(b"%PDF-1.4")
        good = docs / "good.md"
        good.write_text("MCP servers good markdown doc", encoding="utf-8")

        orig_extract = corpus._extract_text

        def flaky_extract(path: Path, *, pdftotext: str | None) -> str:
            if path.name == "bad.pdf":
                raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
            return orig_extract(path, pdftotext=pdftotext)

        monkeypatch.setattr(corpus, "which", lambda _name: "/usr/bin/pdftotext")
        monkeypatch.setattr(corpus, "_extract_text", flaky_extract)

        result = corpus.search(
            "MCP servers",
            [docs],
            from_date="2026-06-01",
            to_date="2026-07-01",
            all_time=True,
            cache_dir=tmp_path / "cache",
        )

        assert [item.title for item in result.items] == ["good"]
        assert any("UnicodeDecodeError" in note for note in result.notes)
        assert all(str(tmp_path) not in note for note in result.notes)

    def test_pdftotext_timeout_does_not_deadlock_when_detached_descendant_holds_stdout(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        pid_file = tmp_path / "descendant.pid"
        fake_bin = _write_fake_pdftotext(
            tmp_path,
            f"""
import subprocess, sys, time
# Detached descendant inherits stdout and survives proc.kill()/killpg(),
# keeping the reader thread blocked inside BufferedReader.read().
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=sys.stdout,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
with open({str(pid_file)!r}, "w", encoding="utf-8") as fh:
    fh.write(str(child.pid))
time.sleep(30)
""",
        )
        docs = tmp_path / "docs"
        docs.mkdir()
        pdf = docs / "leaked-fd.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        (docs / "good.md").write_text("MCP servers survive leaked pipe fd", encoding="utf-8")

        monkeypatch.setattr(corpus, "which", lambda _name: str(fake_bin))
        monkeypatch.setattr(corpus, "PDF_TIMEOUT_SECONDS", 0.2)

        descendant_pids: list[int] = []
        spawned: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(corpus.subprocess, "Popen", tracking_popen)
        threads_before = threading.active_count()
        try:
            start = time.monotonic()
            with pytest.raises(subprocess.TimeoutExpired):
                corpus._extract_pdf_text(pdf, str(fake_bin))
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"_extract_pdf_text hung for {elapsed:.2f}s"
            assert spawned and spawned[-1].stdout is not None and spawned[-1].stdout.closed
            assert threading.active_count() == threads_before
            if pid_file.exists():
                descendant_pids.append(int(pid_file.read_text(encoding="utf-8").strip()))
                pid_file.unlink()

            start = time.monotonic()
            result = corpus.search(
                "MCP servers",
                [docs],
                from_date="2026-06-01",
                to_date="2026-07-01",
                all_time=True,
                cache_dir=tmp_path / "cache",
            )
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"corpus.search hung for {elapsed:.2f}s"
            assert [item.title for item in result.items] == ["good"]
            assert any("TimeoutExpired" in note for note in result.notes)
            assert spawned[-1].stdout is not None and spawned[-1].stdout.closed
            assert threading.active_count() == threads_before
            if pid_file.exists():
                descendant_pids.append(int(pid_file.read_text(encoding="utf-8").strip()))
        finally:
            for pid in descendant_pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def test_pdftotext_registers_with_subproc_and_cleanup_children_kills_it(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        started_flag = tmp_path / "started.txt"
        fake_bin = _write_fake_pdftotext(
            tmp_path,
            f"""
import pathlib, time
pathlib.Path({str(started_flag)!r}).write_text("ready", encoding="utf-8")
time.sleep(30)
""",
        )
        pdf = tmp_path / "cancel-me.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        registered: list[int] = []
        unregistered: list[int] = []
        orig_reg = subproc.register_child_pid
        orig_unreg = subproc.unregister_child_pid

        def spy_reg(pid: int) -> None:
            registered.append(pid)
            orig_reg(pid)

        def spy_unreg(pid: int) -> None:
            unregistered.append(pid)
            orig_unreg(pid)

        monkeypatch.setattr(subproc, "register_child_pid", spy_reg)
        monkeypatch.setattr(subproc, "unregister_child_pid", spy_unreg)
        monkeypatch.setattr(corpus, "PDF_TIMEOUT_SECONDS", 15.0)
        monkeypatch.setattr(subproc, "_shutting_down", False)

        outcome: list[BaseException] = []

        def run_extract() -> None:
            try:
                corpus._extract_pdf_text(pdf, str(fake_bin))
            except BaseException as exc:
                outcome.append(exc)

        worker = threading.Thread(target=run_extract)
        worker.start()
        try:
            wait_until = time.monotonic() + 5.0
            while not started_flag.exists() and time.monotonic() < wait_until:
                time.sleep(0.02)
            assert started_flag.exists()
            assert len(registered) == 1
            assert registered[0] in subproc._child_pids

            start = time.monotonic()
            subproc.cleanup_children(grace=0.3)
            worker.join(timeout=5.0)
            elapsed = time.monotonic() - start
        finally:
            subproc._shutting_down = False

        assert not worker.is_alive()
        assert elapsed < 3.0
        assert len(outcome) == 1 and isinstance(outcome[0], subprocess.CalledProcessError)
        assert unregistered == registered
        assert registered[0] not in subproc._child_pids

    def test_pdftotext_fallback_supports_mock_popen_without_fileno(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        pdf = tmp_path / "mocked.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        class _FakeProc:
            def __init__(self, payload: bytes, returncode: int = 0) -> None:
                self.pid = 999991
                self.stdout = io.BytesIO(payload)
                self.returncode: int | None = None
                self._final_rc = returncode

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout=None) -> int:
                self.returncode = self._final_rc
                return self._final_rc

            def terminate(self) -> None:
                self.returncode = -15

            def kill(self) -> None:
                self.returncode = -9

        fake_proc = _FakeProc(b"MCP servers mocked stream \xff\xfe ok")
        monkeypatch.setattr(corpus.subprocess, "Popen", lambda *a, **k: fake_proc)
        monkeypatch.setattr(subproc, "register_child_pid", lambda _pid: None)
        monkeypatch.setattr(subproc, "unregister_child_pid", lambda _pid: None)

        extracted = corpus._extract_pdf_text(pdf, "/usr/bin/pdftotext")
        assert "MCP servers mocked stream" in extracted
        assert "\ufffd" in extracted
        assert fake_proc.stdout.closed

    def test_pdftotext_cleans_up_child_when_reader_thread_start_fails(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fake_bin = _write_fake_pdftotext(tmp_path, "import time\ntime.sleep(30)\n")
        pdf = tmp_path / "thread-fail.pdf"
        pdf.write_bytes(b"%PDF-1.4")

        spawned: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            spawned.append(proc)
            return proc

        monkeypatch.setattr(corpus.subprocess, "Popen", tracking_popen)
        monkeypatch.setattr(corpus, "_stdout_selectable_fd", lambda _stream: None)
        monkeypatch.setattr(
            corpus.threading.Thread,
            "start",
            mock.Mock(side_effect=RuntimeError("can't start new thread")),
        )

        with pytest.raises(RuntimeError, match="can't start new thread"):
            corpus._extract_pdf_text(pdf, str(fake_bin))

        assert len(spawned) == 1
        assert spawned[0].poll() is not None


# ---------------------------------------------------------------------------
# HIGH-221 & HIGH-222: MCP manifest Bluesky handle & X backend config
# ---------------------------------------------------------------------------


class TestHigh221And222McpManifestConfig:
    def test_high221_manifest_enables_bluesky_with_handle_and_app_password(self) -> None:
        manifest = json.loads((ROOT / "mcp" / "manifest.json").read_text(encoding="utf-8"))
        env_map = manifest["server"]["mcp_config"]["env"]
        user_config = manifest["user_config"]

        assert env_map["BSKY_HANDLE"] == "${user_config.bsky_handle}"
        assert env_map["BSKY_APP_PASSWORD"] == "${user_config.bsky_app_password}"
        assert user_config["bsky_handle"]["sensitive"] is False
        assert user_config["bsky_handle"]["required"] is False
        assert user_config["bsky_app_password"]["sensitive"] is True
        assert env.is_bluesky_available({"BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop"}) is False
        assert env.is_bluesky_available({
            "BSKY_HANDLE": "alice.bsky.social",
            "BSKY_APP_PASSWORD": "abcd-efgh-ijkl-mnop",
        }) is True

    def test_high222_manifest_exposes_x_backend_and_xapi_opt_in_selects_xapi(self, monkeypatch) -> None:
        manifest = json.loads((ROOT / "mcp" / "manifest.json").read_text(encoding="utf-8"))
        env_map = manifest["server"]["mcp_config"]["env"]
        user_config = manifest["user_config"]

        assert env_map["LAST30DAYS_X_BACKEND"] == "${user_config.last30days_x_backend}"
        assert user_config["last30days_x_backend"]["sensitive"] is False
        assert user_config["last30days_x_backend"]["required"] is False
        assert "xapi" in user_config["x_bearer_token"]["description"]
        assert "LAST30DAYS_X_BACKEND" in user_config["x_bearer_token"]["description"]
        for higher_priority in ("XAI_API_KEY", "XQUIK_API_KEY", "AUTH_TOKEN+CT0"):
            assert higher_priority in user_config["last30days_x_backend"]["description"]
            assert higher_priority in user_config["x_bearer_token"]["description"]

        # Off grok-bot without opt-in, bare X_BEARER_TOKEN stays inactive.
        monkeypatch.delenv(env.X_API_OPT_IN_VAR, raising=False)
        assert env.x_backend_chain({"X_BEARER_TOKEN": "dummy-bearer"}, local_only=True) == []
        assert env.x_backend_chain(
            {"X_BEARER_TOKEN": "dummy-bearer", "LAST30DAYS_X_BACKEND": "xapi"},
            local_only=True,
        ) == ["xapi"]

        # With MCP wrapper opt-in (LAST30DAYS_X_API_OPT_IN=1), xapi joins the
        # tail of x_policy(config).auto_chain without overriding explicit pins
        # or higher-priority backends.
        monkeypatch.setenv(env.X_API_OPT_IN_VAR, "1")
        assert env.x_policy({}).auto_chain == env.X_BACKEND_ORDER + ("xapi",)
        assert env.x_backend_chain({"X_BEARER_TOKEN": "dummy-bearer"}, local_only=True) == ["xapi"]
        assert env.x_backend_chain(
            {"X_BEARER_TOKEN": "dummy-bearer", "XAI_API_KEY": "dummy-xai"},
            local_only=True,
        ) == ["xai", "xapi"]
        assert env.x_backend_chain(
            {"X_BEARER_TOKEN": "dummy-bearer", "XQUIK_API_KEY": "dummy-xquik"},
            local_only=True,
        ) == ["xquik", "xapi"]
        assert env.x_backend_chain(
            {"X_BEARER_TOKEN": "dummy-bearer", "LAST30DAYS_X_BACKEND": "bird"},
            local_only=True,
        ) == []

    def test_high222_x_api_opt_in_parity_across_all_x_surfaces(self, monkeypatch) -> None:
        bird_off = {"installed": True, "authenticated": False, "username": "", "can_install": True}

        def _node_ok(name, timeout=health.PROBE_TIMEOUT):
            return health.DependencyProbe(name=name, status=health.OK, detail=f"{name} 1.0.0")

        with (
            mock.patch("lib.bird_x.is_bird_installed", return_value=False),
            mock.patch("lib.bird_x.get_bird_status", return_value=dict(bird_off)),
            mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
            mock.patch("lib.xurl_x.is_available", return_value=False),
            mock.patch("lib.grok_x.has_stored_auth", return_value=False),
            mock.patch("lib.backends.which", return_value=None),
            mock.patch("lib.health.probe_dependency", _node_ok),
        ):
            cfg_bearer = {"X_BEARER_TOKEN": "dummy-bearer", "_BROWSER_COOKIE_MODE": "plan_only"}

            # Baseline without opt-in: xapi stays opt-in everywhere.
            monkeypatch.delenv(env.X_API_OPT_IN_VAR, raising=False)
            fp_off = doctor._config_fingerprint(cfg_bearer)
            assert env.x_backend_chain(cfg_bearer, local_only=True) == []
            assert env.get_x_source_status(cfg_bearer)["source"] is None
            assert env.get_x_source_with_method(cfg_bearer) == (None, "none")
            assert backends.resolve("x", cfg_bearer).active_backend is None
            assert doctor._x_record(cfg_bearer)["status"] == "unconfigured"
            assert pipeline.diagnose(cfg_bearer, safe=True)["x_backend"] is None

            # With LAST30DAYS_X_API_OPT_IN=1: all 6 surfaces agree on xapi.
            monkeypatch.setenv(env.X_API_OPT_IN_VAR, "1")
            fp_on = doctor._config_fingerprint(cfg_bearer)
            assert fp_on != fp_off
            assert env.x_backend_chain(cfg_bearer, local_only=True) == ["xapi"]
            assert env.get_x_source_status(cfg_bearer)["source"] == "xapi"
            assert env.get_x_source_with_method(cfg_bearer) == ("xapi", "bearer")
            resolved = backends.resolve("x", cfg_bearer)
            assert resolved.active_backend == "xapi"
            assert resolved.tier == backends.TIER_OK
            rec = doctor._x_record(cfg_bearer)
            assert rec["status"] == health.OK
            assert rec["active_backend"] == "xapi"
            diag = pipeline.diagnose(cfg_bearer, safe=True)
            assert diag["x_backend"] == "xapi"
            assert "x" in diag["available_sources"]

            # Higher-priority XAI_API_KEY still wins across all 6 surfaces.
            cfg_xai = {**cfg_bearer, "XAI_API_KEY": "dummy-xai"}
            assert env.x_backend_chain(cfg_xai, local_only=True)[0] == "xai"
            assert env.get_x_source_status(cfg_xai)["source"] == "xai"
            assert env.get_x_source_with_method(cfg_xai) == ("xai", "xai")
            assert backends.resolve("x", cfg_xai).active_backend == "xai"
            assert doctor._x_record(cfg_xai)["active_backend"] == "xai"
            assert pipeline.diagnose(cfg_xai, safe=True)["x_backend"] == "xai"

            # Higher-priority XQUIK_API_KEY still wins across all 6 surfaces.
            cfg_xquik = {**cfg_bearer, "XQUIK_API_KEY": "dummy-xquik"}
            assert env.x_backend_chain(cfg_xquik, local_only=True)[0] == "xquik"
            assert env.get_x_source_status(cfg_xquik)["source"] == "xquik"
            assert env.get_x_source_with_method(cfg_xquik) == ("xquik", "api_key")
            assert backends.resolve("x", cfg_xquik).active_backend == "xquik"
            assert doctor._x_record(cfg_xquik)["active_backend"] == "xquik"
            assert pipeline.diagnose(cfg_xquik, safe=True)["x_backend"] == "xquik"

            # Explicit LAST30DAYS_X_BACKEND=bird pin is never overridden by xapi opt-in.
            cfg_pin = {**cfg_bearer, "LAST30DAYS_X_BACKEND": "bird"}
            assert env.x_backend_chain(cfg_pin, local_only=True) == []
            assert env.get_x_source_status(cfg_pin)["source"] is None
            res_pin = backends.resolve("x", cfg_pin)
            assert res_pin.pinned is True and res_pin.pin == "bird"
            assert res_pin.active_backend is None
            assert doctor._x_record(cfg_pin).get("active_backend") is None
            assert pipeline.diagnose(cfg_pin, safe=True)["x_backend"] is None

            # LAST30DAYS_X_BACKEND=auto is treated as unpinned -> xapi activates cleanly.
            cfg_auto = {**cfg_bearer, "LAST30DAYS_X_BACKEND": "auto"}
            assert env.x_backend_chain(cfg_auto, local_only=True) == ["xapi"]
            assert env.get_x_source_status(cfg_auto)["source"] == "xapi"
            assert backends.resolve("x", cfg_auto).active_backend == "xapi"
            assert doctor._x_record(cfg_auto)["active_backend"] == "xapi"
            assert pipeline.diagnose(cfg_auto, safe=True)["x_backend"] == "xapi"

            # AUTH_TOKEN+CT0 present without node (bird unavailable) falls back to xapi.
            cfg_cookies_no_node = {
                **cfg_bearer,
                "AUTH_TOKEN": "dummy-auth",
                "CT0": "dummy-ct0",
            }
            assert env.x_backend_chain(cfg_cookies_no_node, local_only=True) == ["xapi"]
            assert env.get_x_source_status(cfg_cookies_no_node)["source"] == "xapi"
            assert backends.resolve("x", cfg_cookies_no_node).active_backend == "xapi"
            assert doctor._x_record(cfg_cookies_no_node)["active_backend"] == "xapi"
            assert pipeline.diagnose(cfg_cookies_no_node, safe=True)["x_backend"] == "xapi"

    def test_high222_report_cache_key_changes_when_x_api_opt_in_toggles(self, monkeypatch) -> None:
        import last30days as cli

        args = cli.build_parser().parse_args(["MCP servers", "--emit=html"])
        cfg = {"X_BEARER_TOKEN": "dummy-bearer"}

        monkeypatch.delenv(env.X_API_OPT_IN_VAR, raising=False)
        opts_off = cli._cache_research_options(args, cfg)

        monkeypatch.setenv(env.X_API_OPT_IN_VAR, "1")
        opts_on_1 = cli._cache_research_options(args, cfg)

        monkeypatch.setenv(env.X_API_OPT_IN_VAR, "true")
        opts_on_true = cli._cache_research_options(args, cfg)

        assert opts_off["config_sha256"] != opts_on_1["config_sha256"]
        assert opts_on_1["config_sha256"] == opts_on_true["config_sha256"]
