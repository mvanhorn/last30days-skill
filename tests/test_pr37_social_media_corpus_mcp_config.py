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
import stat
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from lib import bluesky, corpus, env, http, linkedin, pipeline, youtube_yt
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
        assert all(secret_pw not in str(k) for k in bluesky._key_locks.keys())

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

    def test_high222_manifest_exposes_x_backend_and_xapi_opt_in_selects_xapi(self) -> None:
        manifest = json.loads((ROOT / "mcp" / "manifest.json").read_text(encoding="utf-8"))
        env_map = manifest["server"]["mcp_config"]["env"]
        user_config = manifest["user_config"]

        assert env_map["LAST30DAYS_X_BACKEND"] == "${user_config.last30days_x_backend}"
        assert user_config["last30days_x_backend"]["sensitive"] is False
        assert user_config["last30days_x_backend"]["required"] is False
        assert "xapi" in user_config["x_bearer_token"]["description"]
        assert "LAST30DAYS_X_BACKEND" in user_config["x_bearer_token"]["description"]

        # Off grok-bot, bare X_BEARER_TOKEN is opt-in, while LAST30DAYS_X_BACKEND=xapi activates it.
        assert env.x_backend_chain({"X_BEARER_TOKEN": "dummy-bearer"}, local_only=True) == []
        assert env.x_backend_chain(
            {"X_BEARER_TOKEN": "dummy-bearer", "LAST30DAYS_X_BACKEND": "xapi"},
            local_only=True,
        ) == ["xapi"]
