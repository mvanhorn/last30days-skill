"""Security regression tests for PR10: Reddit session privacy & envelope TOCTOU.

Covers:
- HIGH-126: Non-public subreddit_type values (gold_only, gold_restricted,
  employees_only, user, missing/unknown) are dropped unless explicitly named
  and never enter session.public_subs.
- HIGH-127: Per-entity named subreddit allowlists are isolated via ContextVar
  across comparison entities sharing the process-wide singleton Session.
- HIGH-134: Credential-directory existence oracle, symlink/FIFO/inode TOCTOU
  races, and unhandled stdin/NUL-byte exceptions in reddit_envelope._read_bytes
  (shared by shortform_envelope).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import io
import json
import os
import sys
import threading
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest

import last30days as cli
from lib import (
    dates,
    env,
    http,
    pipeline,
    reddit_arctic,
    reddit_envelope,
    reddit_hydrate,
    reddit_keyless,
    reddit_search,
    reddit_session,
    reddit_shreddit,
    shortform_envelope,
)
from lib.reddit_refs import canonical_thread

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DUMMY_COOKIE = "dummy-pr10-session-cookie-0123456789"
TOPIC = "Ghostty terminal"
FROM, TO = "2026-10-16", "2026-11-15"
IN_WINDOW_UTC = 1794355200  # 2026-11-11T00:00:00Z

THREAD_FIXTURE = json.loads((FIXTURES / "reddit_session_thread.json").read_text(encoding="utf-8"))
POSTS_FIXTURE = (FIXTURES / "reddit_session_posts.html").read_text(encoding="utf-8")
NO_COMMENTS_FIXTURE = (FIXTURES / "reddit_session_comments_no_results.html").read_text(encoding="utf-8")


def _thread_json(post_id: str, *, clear_comments: bool = False, **fields) -> str:
    data = json.loads(json.dumps(THREAD_FIXTURE))
    post = data[0]["data"]["children"][0]["data"]
    sub = fields.get("subreddit", post["subreddit"])
    post.update({
        "id": post_id,
        "created_utc": IN_WINDOW_UTC,
        "permalink": f"/r/{sub}/comments/{post_id}/t/",
    })
    for key, value in fields.items():
        if value is _OMIT:
            post.pop(key, None)
        else:
            post[key] = value
    if clear_comments:
        data[1]["data"]["children"] = []
    return json.dumps(data)


_OMIT = object()


def _ref(post_id: str, sub: str = "Ghostty", provenance: str = "session"):
    ref = canonical_thread(f"https://www.reddit.com/r/{sub}/comments/{post_id}/")
    ref.provenance.add(provenance)
    ref.title = "Ghostty terminal discussion"
    ref.date = "2026-11-11"
    return ref


def _session(**kwargs) -> reddit_session.Session:
    return reddit_session.Session(DUMMY_COOKIE, browser="chrome", **kwargs)


def _envelope_payload(threads, topic: str = TOPIC) -> dict:
    return {"schema": reddit_envelope.SCHEMA, "topic": topic, "threads": threads}


def _write_json(path: Path, payload) -> Path:
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def no_archive(monkeypatch):
    monkeypatch.setattr(reddit_arctic, "fetch_posts_by_ids", lambda ids, **_k: ({}, "down"))
    monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)
    monkeypatch.setattr(reddit_shreddit, "fetch_comments", lambda *_a, **_k: {})


# ---------------------------------------------------------------------------
# HIGH-126: Non-public subreddit_type allowlist & public_subs isolation
# ---------------------------------------------------------------------------


class TestNonPublicSubredditTypes:
    @pytest.mark.parametrize(
        "sub_type",
        [
            "private",
            "gold_only",
            "gold_restricted",
            "employees_only",
            "user",
            "",
            None,
            _OMIT,
            "future_restricted_type",
        ],
    )
    def test_non_public_subreddit_types_dropped_and_never_enter_public_subs(
        self, monkeypatch, no_archive, sub_type,
    ):
        raw = json.loads(_thread_json("1np0001", subreddit="lounge", subreddit_type=sub_type))
        session = _session()
        assert reddit_session.thread_post(raw, session) is reddit_session.DROP
        assert "lounge" not in session.public_subs

        # End-to-end hydration: neither the hydrated thread nor a second
        # unhydrated search-hit copy from the same non-public sub may survive.
        def fake_get(url, _cookie, **_k):
            if "1np0001.json" in url:
                return 200, json.dumps(raw), False
            return 404, "", False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        found = reddit_session.Discovery()
        ref1 = _ref("1np0001", "lounge")
        ref2 = _ref("1np0002", "lounge")
        found.hits["1np0002"] = {
            "title": "Ghostty terminal in lounge",
            "subreddit": "lounge",
            "url": ref2.url,
            "metadata": {"post_id": "1np0002"},
        }
        hydrated = reddit_hydrate.hydrate(
            [ref1, ref2],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session, found),
            now=1.9e9,
        )
        assert hydrated.items == []
        assert "lounge" not in session.public_subs

    @pytest.mark.parametrize("sub_type", ["public", "restricted", "archived", " Restricted "])
    def test_public_subreddit_types_admitted_and_recorded_in_public_subs(
        self, monkeypatch, no_archive, sub_type,
    ):
        raw = json.loads(_thread_json("1pb0001", subreddit="Ghostty", subreddit_type=sub_type))
        session = _session(cap=1)
        post = reddit_session.thread_post(raw, session)
        assert post is not None and post is not reddit_session.DROP
        assert "ghostty" in session.public_subs

        # Hydrating one public thread allows an unhydrated search-hit copy from
        # that same confirmed-public subreddit when cap runs out.
        session2 = _session(cap=1)
        monkeypatch.setattr(
            http,
            "reddit_session_get",
            lambda url, _c, **_k: (200, json.dumps(raw), False) if "1pb0001.json" in url else (404, "", False),
        )
        found = reddit_session.Discovery()
        ref1 = _ref("1pb0001", "Ghostty")
        ref2 = _ref("1pb0002", "Ghostty")
        found.hits["1pb0002"] = {
            "title": "Ghostty terminal second hit",
            "subreddit": "Ghostty",
            "url": ref2.url,
            "date": "2026-11-11",
            "metadata": {"post_id": "1pb0002"},
        }
        hydrated = reddit_hydrate.hydrate(
            [ref1, ref2],
            topic=TOPIC,
            from_date=FROM,
            to_date=TO,
            comments=False,
            session_hydrator=reddit_session.hydrator(session2, found),
            now=1.9e9,
        )
        assert [i["metadata"]["post_id"] for i in hydrated.items] == ["1pb0001", "1pb0002"]

    def test_explicitly_named_gold_only_sub_admitted_without_polluting_public_subs(self):
        raw = json.loads(_thread_json("1gld001", subreddit="lounge", subreddit_type="gold_only"))
        session = _session(named=["lounge"])
        post = reddit_session.thread_post(raw, session)
        assert post is not None and post is not reddit_session.DROP
        assert post["metadata"]["post_id"] == "1gld001"
        # Named non-public subreddits must never be promoted into public_subs.
        assert "lounge" not in session.public_subs

    def test_explicitly_named_quarantined_public_sub_never_enters_public_subs(self):
        raw = json.loads(_thread_json("1qr0001", subreddit="qsub", subreddit_type="public", quarantine=True))
        session = _session(named=["qsub"])
        post = reddit_session.thread_post(raw, session)
        assert post is not None and post is not reddit_session.DROP
        assert "qsub" not in session.public_subs

    def test_empty_subreddit_name_never_enters_public_subs(self):
        raw = json.loads(_thread_json("1emp001", subreddit="", subreddit_type="public"))
        session = _session()
        post = reddit_session.thread_post(raw, session)
        assert post is not None and post is not reddit_session.DROP
        assert "" not in session.public_subs


# ---------------------------------------------------------------------------
# HIGH-127: Per-entity named subreddit isolation across comparison runs
# ---------------------------------------------------------------------------


class TestPerEntityNamedSubreddits:
    def _config(self, dedicated=None) -> dict:
        cfg = {
            "_BROWSER_COOKIE_MODE": "read",
            env.REDDIT_CONSENT_VAR: "true",
            env.REDDIT_FROM_BROWSER_VAR: "chrome",
            "_comparison_entity_count": 2,
        }
        if dedicated:
            cfg["_dedicated_subreddits"] = list(dedicated)
        return cfg

    def test_lane_named_override_isolates_singleton_session_across_entities(self, monkeypatch, no_archive):
        cfg1 = self._config(dedicated=["DedA"])
        cfg2 = self._config()
        s1 = reddit_session.session_for(cfg1, reader=lambda *_a: (DUMMY_COOKIE, "ok"), topic="updates in r/PrivateA")
        s2 = reddit_session.session_for(cfg2, reader=lambda *_a: (DUMMY_COOKIE, "ok"), topic="updates in r/PrivateB")
        assert s1 is not None and s1 is s2
        assert s1.named == frozenset({"privatea", "deda"})

        threads = {
            "1pa0001": _thread_json("1pa0001", subreddit="PrivateA", subreddit_type="private"),
            "1da0001": _thread_json("1da0001", subreddit="DedA", subreddit_type="private"),
            "1pb0001": _thread_json("1pb0001", subreddit="PrivateB", subreddit_type="private"),
        }

        def fake_get(url, _cookie, **_k):
            pid = url.split("/comments/", 1)[1].split(".json", 1)[0]
            return 200, threads[pid], False

        monkeypatch.setattr(http, "reddit_session_get", fake_get)
        refs = [
            _ref("1pa0001", "PrivateA"),
            _ref("1da0001", "DedA"),
            _ref("1pb0001", "PrivateB"),
        ]

        # Entity 2 explicitly names r/PrivateB: must admit PrivateB and block
        # Entity 1's PrivateA and DedA.
        named_e2 = reddit_session.named_subreddits("updates in r/PrivateB")
        with reddit_session.lane(s2, "updates in r/PrivateB", named=named_e2):
            out_e2 = reddit_hydrate.hydrate(
                refs, topic=TOPIC, from_date=FROM, to_date=TO, comments=False,
                session_hydrator=reddit_session.hydrator(s2), now=1.9e9,
            )
        assert [i["metadata"]["post_id"] for i in out_e2.items] == ["1pb0001"]

        # Entity 3 names no subreddits (empty frozenset): must block ALL
        # private subreddits instead of falling back to s1.named.
        with reddit_session.lane(s2, "plain entity", named=frozenset()):
            out_e3 = reddit_hydrate.hydrate(
                refs, topic=TOPIC, from_date=FROM, to_date=TO, comments=False,
                session_hydrator=reddit_session.hydrator(s2), now=1.9e9,
            )
        assert out_e3.items == []

        # Outside any lane(), session.named fallback stays intact.
        out_fallback = reddit_hydrate.hydrate(
            refs, topic=TOPIC, from_date=FROM, to_date=TO, comments=False,
            session_hydrator=reddit_session.hydrator(s1), now=1.9e9,
        )
        assert sorted(i["metadata"]["post_id"] for i in out_fallback.items) == ["1da0001", "1pa0001"]

    def test_concurrent_comparison_entities_do_not_leak_named_allowlists(self, monkeypatch):
        session = _session(named=["PrivateA"], entity_count=3)
        post_a = json.loads(_thread_json("1ca0001", subreddit="PrivateA", subreddit_type="private"))
        post_b = json.loads(_thread_json("1cb0001", subreddit="PrivateB", subreddit_type="private"))
        barrier = threading.Barrier(3)

        def worker(entity: str, named: frozenset[str]):
            with reddit_session.lane(session, entity, named=named):
                barrier.wait(timeout=5)
                admitted_a = reddit_session.thread_post(post_a, session) is not reddit_session.DROP
                admitted_b = reddit_session.thread_post(post_b, session) is not reddit_session.DROP
                return admitted_a, admitted_b

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            f1 = pool.submit(worker, "entity-a", frozenset({"privatea"}))
            f2 = pool.submit(worker, "entity-b", frozenset({"privateb"}))
            f3 = pool.submit(worker, "entity-c", frozenset())
            assert f1.result(timeout=5) == (True, False)
            assert f2.result(timeout=5) == (False, True)
            assert f3.result(timeout=5) == (False, False)

    def test_hydrator_created_inside_lane_preserves_named_without_context_copy(self, monkeypatch, no_archive):
        session = _session(named=["PrivateA"])
        monkeypatch.setattr(
            http,
            "reddit_session_get",
            lambda url, _c, **_k: (
                200,
                _thread_json(
                    "1pa0001" if "1pa0001" in url else "1pb0001",
                    subreddit="PrivateA" if "1pa0001" in url else "PrivateB",
                    subreddit_type="private",
                ),
                False,
            ),
        )
        with reddit_session.lane(session, "entity-b", named=frozenset({"privateb"})):
            hyd = reddit_session.hydrator(session)

        # Invoke hydrator on a plain worker thread that did NOT copy contextvars.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(hyd, [_ref("1pa0001", "PrivateA"), _ref("1pb0001", "PrivateB")]).result(timeout=5)
        assert result["1pa0001"].get("_drop") is True
        assert result["1pb0001"].get("_drop") is not True
        assert result["1pb0001"]["subreddit"] == "PrivateB"

    def test_pipeline_passes_per_entity_named_subreddits_to_reddit_session_lane(self, monkeypatch, no_archive):
        cfg1 = self._config(dedicated=["DedA"])
        cfg2 = self._config()
        monkeypatch.setattr(env, "read_reddit_session_cookie", lambda *_a, **_k: (DUMMY_COOKIE, "ok"))

        # Make session discovery emit quarantined hits from PrivateA, DedA, PrivateB
        # and thread JSON return private threads for all three.
        def make_post_page(sub: str, pid: str) -> str:
            # Include action_info.type="post" so reddit_search._post_units and
            # reddit_session.post_flags parse quarantined=True during discover().
            ctx = json.dumps({
                "action_info": {"type": "post"},
                "post": {"id": f"t3_{pid}", "nsfw": False},
                "subreddit": {"name": sub, "nsfw": False, "quarantined": True},
            }).replace('"', "&quot;")
            return (
                'user-logged-in '
                f'<search-telemetry-tracker view-events="search/view/post" '
                f'data-faceplate-tracking-context="{ctx}"></search-telemetry-tracker>'
            )

        titles = {
            "1pa0001": "Ghostty shader pipeline latency benchmarks on Linux",
            "1da0001": "Ghostty window splits keybinding guide for macOS",
            "1pb0001": "Ghostty font ligature rendering memory profile",
        }
        threads = {
            "1pa0001": _thread_json(
                "1pa0001", clear_comments=True, subreddit="PrivateA", subreddit_type="private",
                title=titles["1pa0001"],
                selftext="Ghostty PrivateA PrivateB Linux shader measurements.",
            ),
            "1da0001": _thread_json(
                "1da0001", clear_comments=True, subreddit="DedA", subreddit_type="private",
                title=titles["1da0001"],
                selftext="Ghostty PrivateA PrivateB macOS keybinding setup.",
            ),
            "1pb0001": _thread_json(
                "1pb0001", clear_comments=True, subreddit="PrivateB", subreddit_type="private",
                title=titles["1pb0001"],
                selftext="Ghostty PrivateA PrivateB font ligature profiling.",
            ),
        }

        def fake_session_get(url, _cookie, **_k):
            if ".json" in url:
                pid = url.split("/comments/", 1)[1].split(".json", 1)[0]
                return 200, threads[pid], False
            if "type=posts" in url:
                return 200, (
                    make_post_page("PrivateA", "1pa0001")
                    + make_post_page("DedA", "1da0001")
                    + make_post_page("PrivateB", "1pb0001")
                ), False
            return 200, NO_COMMENTS_FIXTURE, False

        def fake_parse_page(_body, _topic):
            return [
                {
                    "title": titles[pid],
                    "url": f"https://www.reddit.com/r/{sub}/comments/{pid}/",
                    "subreddit": sub,
                    "date": "2026-11-11",
                    "metadata": {"post_id": pid},
                }
                for pid, sub in (("1pa0001", "PrivateA"), ("1da0001", "DedA"), ("1pb0001", "PrivateB"))
            ], None

        monkeypatch.setattr(http, "reddit_session_get", fake_session_get)
        monkeypatch.setattr(reddit_search, "parse_page", fake_parse_page)
        monkeypatch.setattr(reddit_search, "unrecognized_body", lambda _b: None)
        monkeypatch.setattr(reddit_search, "next_cursor", lambda _b: None)
        monkeypatch.setattr(reddit_search, "search", lambda *_a, **_k: [])
        monkeypatch.setattr(reddit_keyless, "_enrich", lambda posts, *_a, **_k: posts)
        monkeypatch.setattr(reddit_keyless, "_scored_listings", lambda *_a, **_k: [])

        def _plan(topic: str) -> dict:
            return {
                "intent": "opinion",
                "freshness_mode": "balanced_recent",
                "cluster_mode": "story",
                "subqueries": [
                    {"label": "primary", "search_query": topic, "ranking_query": topic, "sources": ["reddit"]},
                ],
                "source_weights": {"reddit": 1.0},
            }

        with redirect_stderr(io.StringIO()):
            report1 = pipeline.run(
                topic="Ghostty r/PrivateA",
                config=cfg1,
                depth="quick",
                requested_sources=["reddit"],
                mock=False,
                external_plan=_plan("Ghostty"),
                web_backend="none",
                save_dir="",
                as_of_date=TO,
            )
            # Do NOT call reset_run_state(): mirrors comparison fanout in one process.
            report2 = pipeline.run(
                topic="Ghostty r/PrivateB",
                config=cfg2,
                depth="quick",
                requested_sources=["reddit"],
                mock=False,
                external_plan=_plan("Ghostty"),
                web_backend="none",
                save_dir="",
                as_of_date=TO,
            )

        subs1 = {item.container for item in report1.items_by_source.get("reddit") or []}
        subs2 = {item.container for item in report2.items_by_source.get("reddit") or []}
        assert subs1 == {"PrivateA", "DedA"}
        assert subs2 == {"PrivateB"}


# ---------------------------------------------------------------------------
# HIGH-134: Credential-dir oracle, symlink/FIFO TOCTOU, stdin & NUL-byte errors
# ---------------------------------------------------------------------------


class TestEnvelopeReadSecurity:
    def test_protected_directory_no_existence_or_type_oracle(self, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        subdir = cfg / "subdir"
        subdir.mkdir()
        non_json = cfg / "credentials"
        non_json.write_text("dummy-secret", encoding="utf-8")
        json_file = _write_json(cfg / "secrets.json", _envelope_payload([{"id": "abc123"}]))
        missing_json = cfg / "does_not_exist.json"
        missing_plain = cfg / "does_not_exist"
        dangling_link = tmp_path / "dangling.json"
        dangling_link.symlink_to(missing_json)
        outside = _write_json(tmp_path / "outside.json", _envelope_payload([{"id": "abc123"}]))
        escape_link = cfg / "escape.json"
        escape_link.symlink_to(outside)

        probes = [
            missing_json,
            missing_plain,
            subdir,
            non_json,
            json_file,
            dangling_link,
            escape_link,
        ]
        with mock.patch.object(reddit_envelope.env, "CONFIG_DIR", cfg), \
             mock.patch.object(reddit_envelope, "_stat", side_effect=AssertionError("_stat must not run for protected paths")):
            reasons = []
            for probe in probes:
                with pytest.raises(reddit_envelope.EnvelopeContractError) as exc_info:
                    reddit_envelope.read(probe, TOPIC)
                msg = exc_info.value.message
                assert "is inside a configuration or credential directory" in msg
                # Strip the echoed path so we verify every probe returns the exact same error text.
                reasons.append(msg.replace(str(probe), "<PATH>"))
            assert len(set(reasons)) == 1

    def test_symlink_toctou_swap_into_protected_dir_fails_closed(self, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        secret = _write_json(cfg / "secret.json", _envelope_payload([{"id": "sec999"}]))
        target = _write_json(tmp_path / "threads.json", _envelope_payload([{"id": "ok1111"}]))
        real_lstat = os.lstat

        def stat_then_swap_to_symlink(path: Path):
            info = real_lstat(path)
            os.unlink(path)
            os.symlink(secret, path)
            return info

        with mock.patch.object(reddit_envelope.env, "CONFIG_DIR", cfg), \
             mock.patch.object(reddit_envelope, "_stat", side_effect=stat_then_swap_to_symlink), \
             pytest.raises(reddit_envelope.EnvelopeContractError) as exc_info:
            reddit_envelope.read(target, TOPIC)
        assert "cannot be read" in exc_info.value.message

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFOs only")
    def test_fifo_direct_and_toctou_swap_do_not_hang(self, tmp_path):
        fifo = tmp_path / "pipe.json"
        os.mkfifo(fifo)
        # Direct FIFO fails closed immediately without opening/hanging.
        with pytest.raises(reddit_envelope.EnvelopeContractError, match="regular file"):
            reddit_envelope.read(fifo, TOPIC)

        # TOCTOU swap from regular file to writerless FIFO after _stat()
        # must NOT hang (O_NONBLOCK) and must reject via fstat S_ISREG.
        target = _write_json(tmp_path / "race.json", _envelope_payload([{"id": "ok1111"}]))
        real_lstat = os.lstat

        def stat_then_swap_to_fifo(path: Path):
            info = real_lstat(path)
            os.unlink(path)
            os.mkfifo(path)
            return info

        done = threading.Event()
        errors: list[BaseException] = []

        def run_read():
            try:
                with mock.patch.object(reddit_envelope, "_stat", side_effect=stat_then_swap_to_fifo):
                    reddit_envelope.read(target, TOPIC)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_read, daemon=True)
        worker.start()
        assert done.wait(timeout=3.0), "FIFO TOCTOU hung in _read_bytes"
        assert len(errors) == 1 and isinstance(errors[0], reddit_envelope.EnvelopeContractError)
        assert "regular file" in errors[0].message

    def test_inode_swap_toctou_between_stat_and_open_fails_closed(self, tmp_path):
        target = _write_json(tmp_path / "threads.json", _envelope_payload([{"id": "orig01"}]))
        replacement = _write_json(tmp_path / "other.json", _envelope_payload([{"id": "swap02"}]))
        real_lstat = os.lstat

        def stat_then_replace(path: Path):
            info = real_lstat(path)
            os.replace(replacement, path)
            return info

        with mock.patch.object(reddit_envelope, "_stat", side_effect=stat_then_replace), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="changed while being read"):
            reddit_envelope.read(target, TOPIC)

    @pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="Linux /proc/self/fd only")
    def test_parent_directory_swap_into_protected_dir_caught_via_fd_path(self, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        _write_json(cfg / "threads.json", _envelope_payload([{"id": "sec999"}]))
        work = tmp_path / "work"
        work.mkdir()
        target = _write_json(work / "threads.json", _envelope_payload([{"id": "ok1111"}]))
        real_lstat = os.lstat
        real_realpath = os.path.realpath

        def realpath_then_swap_parent(p):
            res = real_realpath(p)
            if str(p) == str(target) and not work.is_symlink():
                backup = tmp_path / "work_backup"
                os.rename(work, backup)
                os.symlink(cfg, work)
            return res

        with mock.patch.object(reddit_envelope.env, "CONFIG_DIR", cfg), \
             mock.patch.object(reddit_envelope.os.path, "realpath", side_effect=realpath_then_swap_parent), \
             mock.patch.object(reddit_envelope, "_stat", side_effect=real_lstat), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="credential directory"):
            reddit_envelope.read(target, TOPIC)

    def test_legitimate_symlink_outside_protected_dirs_still_works(self, tmp_path):
        target = _write_json(tmp_path / "real.json", _envelope_payload([{"id": "ok1111"}]))
        link = tmp_path / "link.json"
        link.symlink_to(target)
        with redirect_stderr(io.StringIO()):
            env_obj = reddit_envelope.read(link, TOPIC)
        assert [r.post_id for r in env_obj.refs] == ["ok1111"]

    def test_nul_byte_path_raises_contract_error_and_cli_exits_2(self, tmp_path):
        bad_path = str(tmp_path / "evil\x00.json")
        with pytest.raises(reddit_envelope.EnvelopeContractError, match="cannot be read"):
            reddit_envelope.read(bad_path, TOPIC)

        # CLI must map it to exit code 2 (not exit 1 with unhandled ValueError).
        out, err = io.StringIO(), io.StringIO()
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir(exist_ok=True)
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(cli.env, "get_config", return_value={}))
            stack.enter_context(mock.patch.object(cli.env, "CONFIG_DIR", cfg_dir))
            stack.enter_context(mock.patch.object(cli.env, "CONFIG_FILE", cfg_dir / ".env"))
            stack.enter_context(mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()))
            stack.enter_context(mock.patch.dict(os.environ, {"LAST30DAYS_SKIP_PREFLIGHT": "1"}))
            stack.enter_context(mock.patch.object(sys, "argv", ["last30days.py", TOPIC, "--reddit-threads", bad_path]))
            stack.enter_context(mock.patch.object(cli.pipeline, "diagnose", return_value={"available_sources": ["reddit"]}))
            stack.enter_context(mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("must not run")))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            rc = cli.main()
        assert rc == 2
        assert "cannot be read" in err.getvalue()

    def test_stdin_errors_raise_contract_error(self):
        # Missing .buffer (e.g., StringIO)
        with mock.patch.object(sys, "stdin", io.StringIO("{}")), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="cannot be read"):
            reddit_envelope.read(reddit_envelope.STDIN, TOPIC)

        # sys.stdin is None
        with mock.patch.object(sys, "stdin", None), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="cannot be read"):
            reddit_envelope.read(reddit_envelope.STDIN, TOPIC)

        # OSError during read
        broken = mock.Mock()
        broken.buffer.read.side_effect = BrokenPipeError("pipe closed")
        with mock.patch.object(sys, "stdin", broken), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="cannot be read"):
            reddit_envelope.read(reddit_envelope.STDIN, TOPIC)

        # Closed buffer raises ValueError
        closed_buf = io.BytesIO(b"{}")
        closed_buf.close()
        with mock.patch.object(sys, "stdin", mock.Mock(buffer=closed_buf)), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="cannot be read"):
            reddit_envelope.read(reddit_envelope.STDIN, TOPIC)

        # Oversized stdin
        oversized = io.BytesIO(b" " * (reddit_envelope.MAX_BYTES + 1))
        with mock.patch.object(sys, "stdin", mock.Mock(buffer=oversized)), \
             pytest.raises(reddit_envelope.EnvelopeContractError, match="KiB limit"):
            reddit_envelope.read(reddit_envelope.STDIN, TOPIC)

    def test_shortform_envelope_inherits_read_bytes_protections(self, tmp_path):
        cfg = tmp_path / "cfg"
        cfg.mkdir()
        with mock.patch.object(reddit_envelope.env, "CONFIG_DIR", cfg), \
             pytest.raises(shortform_envelope.EnvelopeContractError, match="credential directory"):
            shortform_envelope.read(cfg / "nonexistent.txt", TOPIC)

        with pytest.raises(shortform_envelope.EnvelopeContractError, match="cannot be read"):
            shortform_envelope.read(str(tmp_path / "bad\x00.json"), TOPIC)

        with mock.patch.object(sys, "stdin", io.StringIO("{}")), \
             pytest.raises(shortform_envelope.EnvelopeContractError, match="cannot be read"):
            shortform_envelope.read(shortform_envelope.STDIN, TOPIC)
