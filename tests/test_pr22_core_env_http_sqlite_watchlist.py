"""Regression tests for PR 22 (HIGH-057, HIGH-058, HIGH-062, HIGH-208, HIGH-209, HIGH-213, HIGH-214)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import briefing
import store
import watchlist
from lib import bluesky, bounded_get, env, health, http, library_index, usage, xquik


# ---------------------------------------------------------------------------
# HIGH-057: usage.py SQLite WAL/locking & sqlite3.Error resilience
# ---------------------------------------------------------------------------


def test_high057_journal_uses_wal_busy_timeout_and_closes_connection(tmp_path):
    journal = tmp_path / "usage.db"
    usage.create_journal(journal)

    with sqlite3.connect(journal) as check:
        mode = check.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"

    leaked_ref = None
    with usage._connection(journal, write=False) as conn:
        leaked_ref = conn
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy == usage._BUSY_TIMEOUT_MS

    with pytest.raises(sqlite3.ProgrammingError):
        leaked_ref.execute("SELECT 1")

    with usage._connection(journal, write=False, deadline_monotonic=time.monotonic() + 0.4) as conn:
        bounded_busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert 0 < bounded_busy <= 400


def test_high057_concurrent_http_journal_writes_under_heavy_contention(tmp_path, monkeypatch):
    journal = tmp_path / "usage.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    payload = json.dumps({
        "usage": {"cost": 0.1, "prompt_tokens": 10, "completion_tokens": 5},
    }).encode("utf-8")

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(payload))

    def worker(_index: int) -> None:
        for _ in range(5):
            result = http.post("https://openrouter.ai/api/v1/chat/completions", {}, retries=1)
            assert result["usage"]["cost"] == 0.1

    with ThreadPoolExecutor(max_workers=20) as pool:
        list(pool.map(worker, range(20)))

    totals = usage.read_journal(journal)
    assert totals["token_cost"] == pytest.approx(10.0)
    assert totals["cost_unknown"] == 0
    assert totals["prompt_tokens"] == 1000
    assert totals["completion_tokens"] == 500


def test_high057_journal_sqlite_errors_never_crash_http_or_mask_transport_errors(
    tmp_path, monkeypatch
):
    journal = tmp_path / "usage.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    class NonTTYStderr(io.StringIO):
        def isatty(self) -> bool:
            return False

    class Response(io.BytesIO):
        status = 200

    # 1. Dropped table breaks begin(), but live HTTP request still succeeds and logs non-TTY.
    with sqlite3.connect(journal) as conn:
        conn.execute("DROP TABLE attempts")
    monkeypatch.setattr(
        http,
        "_open_request",
        lambda req, timeout: Response(b'{"choices": [], "usage": {"cost": 0.25}}'),
    )
    fake_stderr = NonTTYStderr()
    with patch.object(sys, "stderr", fake_stderr):
        result = http.post("https://openrouter.ai/api/v1/chat/completions", {}, retries=1)
    assert result["choices"] == []
    assert "[Usage]" in fake_stderr.getvalue()
    assert usage.read_journal(journal)["cost_unknown"] == 1

    # 2. finish() sqlite3.Error does not crash HTTP and leaves attempt unknown=1.
    journal2 = tmp_path / "usage2.db"
    usage.create_journal(journal2)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal2))
    real_connect = usage._connection

    def fail_finish_only(path, *, write=False, **kwargs):
        if write:
            with sqlite3.connect(path) as conn:
                if conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] > 0:
                    raise sqlite3.OperationalError("database is locked")
        return real_connect(path, write=write, **kwargs)

    monkeypatch.setattr(usage, "_connection", fail_finish_only)
    fake_stderr2 = NonTTYStderr()
    with patch.object(sys, "stderr", fake_stderr2):
        result2 = http.post("https://openrouter.ai/api/v1/chat/completions", {}, retries=1)
    assert result2["usage"]["cost"] == 0.25
    assert "[Usage]" in fake_stderr2.getvalue()
    monkeypatch.setattr(usage, "_connection", real_connect)
    assert usage.read_journal(journal2)["cost_unknown"] == 1

    # 3. cancel() sqlite3.Error never masks bounded_get.GetLaunchError and leaves unknown=1.
    journal3 = tmp_path / "usage3.db"
    usage.create_journal(journal3)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal3))

    def fail_cancel_only(path, *, write=False, **kwargs):
        if write:
            with sqlite3.connect(path) as conn:
                if conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] > 0:
                    raise sqlite3.OperationalError("database is locked")
        return real_connect(path, write=write, **kwargs)

    monkeypatch.setattr(usage, "_connection", fail_cancel_only)
    monkeypatch.setattr(
        bounded_get,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(bounded_get.GetLaunchError("spawn failed")),
    )
    fake_stderr3 = NonTTYStderr()
    with patch.object(sys, "stderr", fake_stderr3):
        with pytest.raises(http.HTTPError, match=r"GetLaunchError: spawn failed"):
            http.request(
                "GET",
                "https://openrouter.ai/api/v1/models",
                retries=1,
                owned_get=True,
                deadline_monotonic=time.monotonic() + 10.0,
            )
    assert "[Usage]" in fake_stderr3.getvalue()
    monkeypatch.setattr(usage, "_connection", real_connect)
    assert usage.read_journal(journal3)["cost_unknown"] == 1

    # 4. cancel() sqlite3.Error never masks bounded_get.GetTimeout -> DeadlineExceeded.
    journal4 = tmp_path / "usage4.db"
    usage.create_journal(journal4)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal4))
    monkeypatch.setattr(usage, "_connection", fail_cancel_only)
    monkeypatch.setattr(
        bounded_get,
        "get",
        lambda *a, **kw: (_ for _ in ()).throw(bounded_get.GetTimeout("deadline", started=False)),
    )
    fake_stderr4 = NonTTYStderr()
    with patch.object(sys, "stderr", fake_stderr4):
        with pytest.raises(http.DeadlineExceeded):
            http.request(
                "GET",
                "https://openrouter.ai/api/v1/models",
                retries=1,
                owned_get=True,
                deadline_monotonic=time.monotonic() + 10.0,
            )
    assert "[Usage]" in fake_stderr4.getvalue()
    monkeypatch.setattr(usage, "_connection", real_connect)
    assert usage.read_journal(journal4)["cost_unknown"] == 1


# ---------------------------------------------------------------------------
# HIGH-058: env.py per-layer ${user_config.*} stripping & precedence
# ---------------------------------------------------------------------------


def _isolate_env(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(env, "_find_project_env", lambda: None)
    monkeypatch.setattr(env, "_load_keychain", lambda *a, **k: {})
    monkeypatch.setattr(env, "_load_pass", lambda *a, **k: {})
    monkeypatch.chdir(tmp_path)
    for key in (
        *env.KEYCHAIN_KEYS,
        "SCRAPE_CREATORS_API_KEY",
        "SCRAPE_CREATORS_BASE_URL",
        "SCRAPECREATORS_BASE_URL",
        "BROWSER_CONSENT",
        "FROM_BROWSER",
        "LAST30DAYS_HOST",
        "LAST30DAYS_MEMORY_DIR",
        "LAST30DAYS_YT_PLAYER_CLIENT",
        "LAST30DAYS_DEBUG",
        "LAST30DAYS_TRUST_PROJECT_CONFIG",
        "LAST30DAYS_PASS_PREFIX",
        "SETUP_COMPLETE",
        env.X_HOST_LANE_VAR,
        env.SCHEDULED_RUN_VAR,
    ):
        monkeypatch.delenv(key, raising=False)


def test_high058_templated_and_empty_env_fall_through_to_pass_and_openai_auth(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "${user_config.openai_api_key}")
    monkeypatch.setenv("XAI_API_KEY", "${user_config.xai_api_key}")
    monkeypatch.setenv("BRAVE_API_KEY", "")

    probed: list[str] = []

    def fake_pass(keys, _prefix):
        probed.extend(keys)
        return {
            "OPENAI_API_KEY": "sk-from-pass",
            "XAI_API_KEY": "xai-from-pass",
            "BRAVE_API_KEY": "brave-from-pass",
        }

    monkeypatch.setattr(env, "_load_pass", fake_pass)
    cfg = env.get_config()

    assert "OPENAI_API_KEY" in probed
    assert "XAI_API_KEY" in probed
    assert "BRAVE_API_KEY" in probed
    assert cfg["OPENAI_API_KEY"] == "sk-from-pass"
    assert cfg["OPENAI_AUTH_SOURCE"] == env.AUTH_SOURCE_API_KEY
    assert cfg["OPENAI_AUTH_STATUS"] == env.AUTH_STATUS_OK
    assert cfg["XAI_API_KEY"] == "xai-from-pass"
    assert cfg["BRAVE_API_KEY"] == "brave-from-pass"
    assert cfg["_CONFIG_SOURCE"] == "pass"
    assert cfg[env.TEMPLATE_CONFIG_KEYS] == []
    assert env.read_secret_env("OPENAI_API_KEY") is None
    assert env.read_secret_env("XAI_API_KEY") is None


def test_high058_browser_cookie_preflight_matches_restored_dotenv(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
    dotenv.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", dotenv)
    monkeypatch.setenv("FROM_BROWSER", "${user_config.from_browser}")
    monkeypatch.setenv("BROWSER_CONSENT", "${user_config.browser_consent}")
    monkeypatch.setenv("LAST30DAYS_HOST", "${user_config.host}")

    cfg = env.get_config(env.ConfigLoadPolicy(browser_cookies="plan_only"))

    assert cfg["FROM_BROWSER"] == "chrome"
    assert cfg["BROWSER_CONSENT"] == "true"
    assert cfg["_BROWSER_COOKIE_BROWSERS"] == ["chrome"]
    assert env.cookie_access_denied(cfg) is False
    assert cfg[env.TEMPLATE_CONFIG_KEYS] == ["LAST30DAYS_HOST"]


def test_high058_process_env_only_keys_never_fall_back_to_dotenv_on_template(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        f"{env.X_HOST_LANE_VAR}=1\n{env.SCHEDULED_RUN_VAR}=1\n",
        encoding="utf-8",
    )
    dotenv.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", dotenv)
    monkeypatch.setenv(env.X_HOST_LANE_VAR, "${user_config.x_host_lane}")
    monkeypatch.setenv(env.SCHEDULED_RUN_VAR, "${user_config.scheduled_run}")

    cfg = env.get_config()

    assert cfg[env.X_HOST_LANE_VAR] is None
    assert cfg[env.SCHEDULED_RUN_VAR] is None
    assert cfg["_X_HOST_LANE_FILE_IGNORED"] is True
    assert env.X_HOST_LANE_VAR in cfg[env.TEMPLATE_CONFIG_KEYS]
    assert env.SCHEDULED_RUN_VAR in cfg[env.TEMPLATE_CONFIG_KEYS]
    assert os.environ.get(env.X_HOST_LANE_VAR) is None
    assert os.environ.get(env.SCHEDULED_RUN_VAR) is None


def test_high058_preserves_none_vs_empty_for_browser_consent_and_memory_dir(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BROWSER_CONSENT", "${user_config.browser_consent}")
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", "${user_config.memory_dir}")

    cfg = env.get_config()

    assert cfg["BROWSER_CONSENT"] is None
    assert env.cookie_access_denied(cfg) is False
    assert cfg["LAST30DAYS_MEMORY_DIR"] is None
    assert "BROWSER_CONSENT" in cfg[env.TEMPLATE_CONFIG_KEYS]
    assert "LAST30DAYS_MEMORY_DIR" in cfg[env.TEMPLATE_CONFIG_KEYS]

    # Explicit persisted disable in .env remains "" and is not reported as unset template.
    dotenv = tmp_path / ".env"
    dotenv.write_text("LAST30DAYS_MEMORY_DIR=\n", encoding="utf-8")
    dotenv.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", dotenv)
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", "${user_config.memory_dir}")

    cfg_disabled = env.get_config()
    assert cfg_disabled["LAST30DAYS_MEMORY_DIR"] == ""
    assert "LAST30DAYS_MEMORY_DIR" not in cfg_disabled[env.TEMPLATE_CONFIG_KEYS]


def test_high058_project_template_falls_through_legacy_alias_and_reexports_lazy_env(
    monkeypatch, tmp_path
):
    _isolate_env(monkeypatch, tmp_path)
    global_env = tmp_path / "global.env"
    global_env.write_text(
        "XAI_API_KEY=xai-from-global\n"
        "SCRAPE_CREATORS_API_KEY=sc-legacy-key\n"
        "LAST30DAYS_DEBUG=1\n"
        "LAST30DAYS_YT_PLAYER_CLIENT=ios\n"
        "SCRAPECREATORS_BASE_URL=https://sc-proxy.example\n",
        encoding="utf-8",
    )
    global_env.chmod(0o600)
    project_dir = tmp_path / ".claude"
    project_dir.mkdir()
    project_env = project_dir / "last30days.env"
    project_env.write_text("XAI_API_KEY=${user_config.xai_api_key}\n", encoding="utf-8")
    project_env.chmod(0o600)

    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.setattr(env, "_find_project_env", lambda: project_env)
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "${user_config.scrapecreators_api_key}")
    monkeypatch.setenv("LAST30DAYS_DEBUG", "${user_config.debug}")
    monkeypatch.setenv("LAST30DAYS_YT_PLAYER_CLIENT", "${user_config.yt_player_client}")
    monkeypatch.setenv("SCRAPECREATORS_BASE_URL", "${user_config.sc_base}")

    cfg = env.get_config(env.ConfigLoadPolicy(allow_project_config=True))

    assert cfg["XAI_API_KEY"] == "xai-from-global"
    assert cfg["SCRAPECREATORS_API_KEY"] == "sc-legacy-key"
    assert cfg["LAST30DAYS_DEBUG"] == "1"
    assert cfg["LAST30DAYS_YT_PLAYER_CLIENT"] == "ios"
    assert cfg["SCRAPECREATORS_BASE_URL"] == "https://sc-proxy.example"
    assert os.environ.get("LAST30DAYS_DEBUG") == "1"
    assert os.environ.get("LAST30DAYS_YT_PLAYER_CLIENT") == "ios"
    assert os.environ.get("SCRAPECREATORS_BASE_URL") == "https://sc-proxy.example"
    assert cfg[env.TEMPLATE_CONFIG_KEYS] == []


# ---------------------------------------------------------------------------
# HIGH-062: http.request(..., retries=0) executes 1 attempt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("retries_arg", [0, -2, 1])
def test_high062_request_executes_one_attempt_for_zero_or_negative_retries(
    monkeypatch, retries_arg
):
    calls = 0

    class Response(io.BytesIO):
        status = 200

    def fake_open(req, timeout):
        nonlocal calls
        calls += 1
        return Response(b'{"ok": true}')

    monkeypatch.setattr(http, "_open_request", fake_open)
    assert http.request("GET", "https://example.com/ok", retries=retries_arg) == {"ok": True}
    assert calls == 1

    fail_calls = 0

    def fake_500(req, timeout):
        nonlocal fail_calls
        fail_calls += 1
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b"err"))

    monkeypatch.setattr(http, "_open_request", fake_500)
    with patch("lib.http.time.sleep") as mock_sleep:
        with pytest.raises(http.HTTPError) as exc_info:
            http.request("GET", "https://example.com/fail", retries=retries_arg)
    assert exc_info.value.status_code == 500
    assert fail_calls == 1
    mock_sleep.assert_not_called()


def test_high062_bluesky_refresh_session_and_xquik_probe_work_with_retries_zero(monkeypatch):
    class Response(io.BytesIO):
        status = 200

    bluesky._reset_session_cache()
    monkeypatch.setattr(
        http,
        "_open_request",
        lambda req, timeout: Response(
            json.dumps({"accessJwt": "fresh-access", "refreshJwt": "fresh-refresh"}).encode()
        ),
    )
    token = bluesky._refresh_session("dummy-refresh-token")
    assert token == "fresh-access"
    assert bluesky._refresh_error_status is None

    def fail_401(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"expired"))

    monkeypatch.setattr(http, "_open_request", fail_401)
    assert bluesky._refresh_session("dummy-refresh-token") is None
    assert bluesky._refresh_error_status == 401
    bluesky._reset_session_cache()

    # xquik.probe_works passes retries=0; verify 200 -> True, 402 -> False, 401 -> False.
    monkeypatch.setattr(xquik, "_probe_cache", ("unset", ""))
    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(b'{"tweets": []}'))
    assert xquik.probe_works("dummy-xquik-key") is True
    assert xquik.probe_reason() == "ok"

    def fail_402(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 402, "Payment Required", {}, io.BytesIO(b"unpaid"))

    monkeypatch.setattr(xquik, "_probe_cache", ("unset", ""))
    monkeypatch.setattr(http, "_open_request", fail_402)
    assert xquik.probe_works("dummy-xquik-key") is False
    assert "402" in xquik.probe_reason()

    monkeypatch.setattr(xquik, "_probe_cache", ("unset", ""))
    monkeypatch.setattr(http, "_open_request", fail_401)
    assert xquik.probe_works("dummy-xquik-key") is False
    assert "401" in xquik.probe_reason()
    monkeypatch.setattr(xquik, "_probe_cache", ("unset", ""))


# ---------------------------------------------------------------------------
# HIGH-208: Slack & generic webhooks accept plain-text 2xx bodies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "body"),
    [
        ("https://hooks.slack.com/services/T000/B000/DUMMY", b"ok"),
        ("https://webhook.example.com/notify", b"accepted"),
        ("https://webhook.example.com/notify", b""),
        ("https://webhook.example.com/notify", b'{"status": "ok"}'),
    ],
)
def test_high208_webhooks_succeed_on_plaintext_and_empty_2xx_bodies(
    tmp_path, monkeypatch, capsys, url, body
):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()
    store.set_setting("delivery_channel", url)
    store.set_setting("delivery_mode", "announce")

    class Response(io.BytesIO):
        status = 200

    sent_payloads: list[dict] = []

    def fake_open(req, timeout):
        sent_payloads.append(json.loads(req.data.decode("utf-8")))
        return Response(body)

    monkeypatch.setattr(http, "_open_request", fake_open)
    watchlist._deliver_findings("AI agents", {"new": 3, "updated": 1})

    captured = capsys.readouterr()
    assert captured.err == ""
    assert len(sent_payloads) == 1
    if "hooks.slack.com" in url:
        assert "text" in sent_payloads[0]
    else:
        assert "message" in sent_payloads[0]


def test_high208_webhook_still_reports_non_2xx_http_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()
    store.set_setting("delivery_channel", "https://hooks.slack.com/services/T000/B000/DUMMY")

    def fake_500(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "Server Error", {}, io.BytesIO(b"fail"))

    monkeypatch.setattr(http, "_open_request", fake_500)
    watchlist._deliver_findings("AI agents", {"new": 1, "updated": 0})
    assert "Delivery failed: HTTP 500" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# HIGH-209: daily_budget validation & fail-closed parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    ["nan", "NaN", "-nan", "inf", "+inf", "-inf", "Infinity", "-1", "-0.01", "$5", "abc", "", "   ", float("nan"), float("inf"), True, None],
)
def test_high209_cmd_config_rejects_invalid_budget_values(tmp_path, monkeypatch, bad_value):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()
    store.set_setting("daily_budget", "5.00")

    with pytest.raises(SystemExit):
        watchlist.cmd_config(SimpleNamespace(key="budget", value=bad_value))
    assert store.get_setting("daily_budget") == "5.00"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("0", "0.0"), ("-0.0", "0.0"), ("5", "5.0"), ("5.25", "5.25"), (10.0, "10.0"), ("1e-5", "0.00001")],
)
def test_high209_cmd_config_normalizes_valid_budget_values(
    tmp_path, monkeypatch, capsys, raw_value, expected
):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()

    watchlist.cmd_config(SimpleNamespace(key="budget", value=raw_value))
    assert store.get_setting("daily_budget") == expected
    output = json.loads(capsys.readouterr().out)
    assert output == {"action": "config", "key": "daily_budget", "value": expected}


@pytest.mark.parametrize("corrupt_budget", ["nan", "inf", "-inf", "-5", "$5", "abc", ""])
def test_high209_corrupt_sqlite_budget_fails_closed_across_watchlist_and_briefing(
    tmp_path, monkeypatch, capsys, corrupt_budget
):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    monkeypatch.setattr(briefing, "BRIEFS_DIR", tmp_path / "briefs")
    store.init_db()
    topic = store.add_topic("Budget Guard Topic")
    store.set_setting("daily_budget", corrupt_budget)

    # _budget_skip, cmd_run_one, cmd_run_all fail closed and never launch _run_topic.
    with patch("watchlist._run_topic") as mock_run:
        skipped = watchlist._budget_skip(topic)
        assert skipped is not None
        assert skipped["status"] == "skipped"
        assert "daily_budget" in skipped["reason"]

        watchlist.cmd_run_one(SimpleNamespace(topic=topic["name"]))
        run_one_out = json.loads(capsys.readouterr().out)
        assert run_one_out["status"] == "skipped"

        watchlist.cmd_run_all(None)
        run_all_out = json.loads(capsys.readouterr().out)
        assert run_all_out["budget_limit"] is None
        assert "budget_error" in run_all_out
        assert [row["status"] for row in run_all_out["results"]] == ["skipped"]
        mock_run.assert_not_called()

    # cmd_list surfaces budget_limit=None and budget_error as valid JSON without crashing.
    watchlist.cmd_list(None)
    list_out = json.loads(capsys.readouterr().out)
    assert list_out["budget_limit"] is None
    assert "budget_error" in list_out

    # briefing.generate_daily does not crash with ValueError or emit NaN/Infinity.
    daily = briefing.generate_daily()
    assert daily["status"] == "ok"
    assert daily["cost"]["budget"] is None
    assert "budget_error" in daily["cost"]
    json.dumps(daily, allow_nan=False)


# ---------------------------------------------------------------------------
# HIGH-213: Atomic store_findings vs concurrent remove_topic / delete_finding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("remover_first", [True, False], ids=["remove-first", "store-first"])
def test_high213_concurrent_remove_other_topic_does_not_abort_store_findings(
    tmp_path, monkeypatch, remover_first
):
    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()

    topic_a = store.add_topic("Topic A")
    topic_b = store.add_topic("Topic B")
    run_a = store.record_run(topic_a["id"], source_mode="v3")
    shared_url = "https://example.test/shared-finding"
    exclusive_b_url = "https://example.test/topic-b-new"
    store.store_findings(
        run_a,
        topic_a["id"],
        [{"source": "reddit", "source_url": shared_url, "source_title": "Shared", "engagement_score": 10}],
    )
    run_b = store.record_run(topic_b["id"], source_mode="v3")

    start_barrier = threading.Barrier(2, timeout=5)
    first_locked = threading.Event()
    role = threading.local()
    real_connect = store._connect

    class RacingConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if sql == "BEGIN IMMEDIATE":
                start_barrier.wait()
                is_first = (role.name == "remover") if remover_first else (role.name == "storer")
                if not is_first:
                    assert first_locked.wait(timeout=5)
                cursor = self._conn.execute(sql, params)
                if is_first:
                    first_locked.set()
                return cursor
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(store, "_connect", lambda *a, **k: RacingConn(real_connect(*a, **k)))

    def do_remove():
        role.name = "remover"
        return store.remove_topic("Topic A")

    def do_store():
        role.name = "storer"
        return store.store_findings(
            run_b,
            topic_b["id"],
            [
                {"source": "reddit", "source_url": shared_url, "source_title": "Shared", "engagement_score": 25},
                {"source": "x", "source_url": exclusive_b_url, "source_title": "New B", "engagement_score": 15},
            ],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_remove = pool.submit(do_remove)
        fut_store = pool.submit(do_store)
        assert fut_remove.result(timeout=10) is True
        counts = fut_store.result(timeout=10)

    if remover_first:
        assert counts == {"new": 2, "updated": 0}
    else:
        assert counts == {"new": 1, "updated": 1}

    sightings = store.get_sightings_for_run(topic_b["id"], run_b)
    assert {s["source_url"] for s in sightings} == {shared_url, exclusive_b_url}
    with sqlite3.connect(db_path) as check:
        assert check.execute("PRAGMA foreign_key_check").fetchall() == []
        stored_urls = {row[0] for row in check.execute("SELECT source_url FROM findings").fetchall()}
        assert stored_urls == {shared_url, exclusive_b_url}


def test_high213_concurrent_delete_finding_and_removed_own_topic_shared_url(tmp_path, monkeypatch):
    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()

    topic_a = store.add_topic("Topic A")
    topic_b = store.add_topic("Topic B")
    run_a = store.record_run(topic_a["id"], source_mode="v3")
    shared_url = "https://example.test/deleted-mid-flight"
    store.store_findings(
        run_a,
        topic_a["id"],
        [{"source": "reddit", "source_url": shared_url, "source_title": "Transient", "engagement_score": 5}],
    )
    with sqlite3.connect(db_path) as conn:
        finding_id = conn.execute("SELECT id FROM findings WHERE source_url = ?", (shared_url,)).fetchone()[0]

    # Delete the finding immediately before store_findings runs -> cleanly re-inserts both items.
    store.delete_finding(finding_id)
    run_b = store.record_run(topic_b["id"], source_mode="v3")
    counts = store.store_findings(
        run_b,
        topic_b["id"],
        [
            {"source": "reddit", "source_url": shared_url, "source_title": "Transient", "engagement_score": 12},
            {"source": "x", "source_url": "https://example.test/second", "source_title": "Second", "engagement_score": 3},
        ],
    )
    assert counts == {"new": 2, "updated": 0}
    assert len(store.get_sightings_for_run(topic_b["id"], run_b)) == 2

    # Late store_findings for a removed topic on a shared URL still returns {0, 0}.
    run_a2 = store.record_run(topic_a["id"], source_mode="v3")
    assert store.remove_topic("Topic A") is True
    late_counts = store.store_findings(
        run_a2,
        topic_a["id"],
        [{"source": "reddit", "source_url": shared_url, "source_title": "Transient", "engagement_score": 99}],
    )
    assert late_counts == {"new": 0, "updated": 0}


# ---------------------------------------------------------------------------
# HIGH-214: library_index.py closes all SQLite connections unconditionally
# ---------------------------------------------------------------------------


def test_high214_all_library_index_paths_close_sqlite_connections(tmp_path, monkeypatch):
    memory = tmp_path / "memory"
    briefs = tmp_path / "briefs"
    memory.mkdir()
    briefs.mkdir()

    report_path = memory / "topic-raw.md"
    report_path.write_text(
        "# last30days v4.5.0: Closing Check\n\n"
        "- Date range: 2026-06-10 to 2026-07-10\n\n"
        "## Ranked Evidence Clusters\n\n"
        "### 1. Connection closing token (score 80, 1 item, sources: Reddit)\n"
        "1. [reddit] UniqueClosableToken\n"
        "   - URL: https://reddit.com/r/test/close\n"
        "   - Evidence: UniqueClosableToken verified.\n",
        encoding="utf-8",
    )

    store_db = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", store_db)
    store.init_db()
    topic = store.add_topic("Closing Check")
    run_id = store.record_run(topic["id"], status="completed")
    store.store_findings(
        run_id,
        topic["id"],
        [
            {
                "source": "reddit",
                "source_url": "https://reddit.com/r/test/store-close",
                "source_title": "Store closing finding",
                "content": "UniqueClosableToken in store sighting.",
                "summary": "Store summary.",
                "engagement_score": 10.0,
                "relevance_score": 0.9,
            }
        ],
    )

    opened: list[tuple[str, sqlite3.Connection]] = []
    real_connect = sqlite3.connect

    def tracked_connect(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        opened.append((str(database), conn))
        return conn

    monkeypatch.setattr(library_index.sqlite3, "connect", tracked_connect)

    def assert_all_closed() -> None:
        assert opened
        for db_label, conn in opened:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")
        opened.clear()

    db_path = tmp_path / "library.db"

    assert library_index.fts5_available() is True
    assert_all_closed()

    assert library_index.index_entry(report_path, db_path=db_path) is True
    assert_all_closed()

    assert library_index.index_brief(report_path, db_path=db_path) is True
    assert_all_closed()

    matches = library_index.search(
        "UniqueClosableToken", db_path=db_path, store_db_path=store_db
    )
    assert {m.source_kind for m in matches} == {"brief", "store"}
    assert_all_closed()

    sync_res = library_index.sync_library(memory, briefs, db_path=db_path)
    assert sync_res.rebuilt is False
    assert_all_closed()

    # Corrupt DB rebuild: connection must be closed BEFORE _remove_database unlinks the files.
    closed_before_remove: list[bool] = []
    real_remove = library_index._remove_database

    def check_closed_before_remove(target: Path) -> None:
        for _label, conn in opened:
            try:
                conn.execute("SELECT 1")
                closed_before_remove.append(False)
            except sqlite3.ProgrammingError:
                closed_before_remove.append(True)
        real_remove(target)

    monkeypatch.setattr(library_index, "_remove_database", check_closed_before_remove)

    real_lib_connect = library_index._connect
    corrupt_once = {"done": False}

    class CorruptOnSelectConn:
        def __init__(self, inner: sqlite3.Connection):
            self._inner = inner

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, exc_type, exc, tb):
            return self._inner.__exit__(exc_type, exc, tb)

        def execute(self, sql, *args, **kwargs):
            if "FROM library_documents" in sql and not corrupt_once["done"]:
                corrupt_once["done"] = True
                raise sqlite3.DatabaseError("database disk image is malformed")
            return self._inner.execute(sql, *args, **kwargs)

        def close(self):
            self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def connect_then_corrupt(path: Path):
        conn = real_lib_connect(path)
        if not corrupt_once["done"]:
            return CorruptOnSelectConn(conn)
        return conn

    monkeypatch.setattr(library_index, "_connect", connect_then_corrupt)

    rebuilt = library_index.sync_library(memory, briefs, db_path=db_path)
    assert rebuilt.rebuilt is True
    assert closed_before_remove and all(closed_before_remove)
    assert_all_closed()


def test_high057_transient_begin_failure_forces_cost_unknown_in_and_cross_process(
    tmp_path, monkeypatch
):
    journal = tmp_path / "usage-transient.db"
    sentinel = Path(f"{journal}.unknown")
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(
        http,
        "_open_request",
        lambda req, timeout: Response(b'{"choices": [], "usage": {"cost": 0.42}}'),
    )
    real_connection = usage._connection

    def fail_begin_locked(path, *, write=False, **kwargs):
        if write:
            raise sqlite3.OperationalError("database is locked")
        return real_connection(path, write=write, **kwargs)

    monkeypatch.setattr(usage, "_connection", fail_begin_locked)
    result = http.post("https://openrouter.ai/api/v1/chat/completions", {}, retries=1)
    assert result["choices"] == []

    # Restore real SQLite access: attempts table is intact & empty, yet read_journal fails closed.
    monkeypatch.setattr(usage, "_connection", real_connection)
    with sqlite3.connect(journal) as check:
        assert check.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert sentinel.exists()
    if os.name == "posix":
        assert sentinel.stat().st_mode & 0o777 == 0o600

    # In-process sentinel forces cost_unknown=1.
    assert usage.read_journal(journal)["cost_unknown"] == 1

    # Cross-process simulation: clearing in-memory state still sees the .unknown sidecar.
    usage._BEGIN_FAILED_JOURNALS.clear()
    assert usage.read_journal(journal)["cost_unknown"] == 1

    # Recreating the journal at the same path clears both the sentinel file and memory state.
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{journal}{suffix}").unlink(missing_ok=True)
    usage.create_journal(journal)
    assert not sentinel.exists()
    assert usage.read_journal(journal)["cost_unknown"] == 0


def test_high058_project_config_trusted_ignores_unsubstituted_template(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    global_env = tmp_path / "global.env"
    global_env.write_text("LAST30DAYS_TRUST_PROJECT_CONFIG=1\n", encoding="utf-8")
    global_env.chmod(0o600)
    project_dir = tmp_path / ".claude"
    project_dir.mkdir()
    project_env = project_dir / "last30days.env"
    project_env.write_text("BROWSER_CONSENT=true\nFROM_BROWSER=chrome\n", encoding="utf-8")
    project_env.chmod(0o600)

    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.setattr(env, "_find_project_env", lambda: project_env)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "${user_config.trust_project_config}")

    assert env.has_recorded_browser_consent() is True


def test_high209_parse_budget_converts_overflow_error_to_value_error():
    with pytest.raises(ValueError, match="overflow"):
        store.parse_budget(10**400)


def test_high214_connect_closes_connection_on_base_exception(tmp_path, monkeypatch):
    db_path = tmp_path / "interrupted.db"
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    class InterruptOnScriptConn:
        def __init__(self, inner: sqlite3.Connection):
            self._inner = inner

        def executescript(self, _sql):
            raise KeyboardInterrupt("interrupted during schema setup")

        def close(self):
            self._inner.close()

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            if name == "_inner":
                super().__setattr__(name, value)
            else:
                setattr(self._inner, name, value)

    def tracked_connect(database, *args, **kwargs):
        conn = real_connect(database, *args, **kwargs)
        opened.append(conn)
        return InterruptOnScriptConn(conn)

    monkeypatch.setattr(library_index.sqlite3, "connect", tracked_connect)
    with pytest.raises(KeyboardInterrupt, match="interrupted during schema setup"):
        library_index._connect(db_path)

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


# ---------------------------------------------------------------------------
# Greptile review regressions (P1 + P2)
# ---------------------------------------------------------------------------


def test_greptile_p1_stderr_unknown_cost_marker_forces_unknown_when_fs_unwritable(
    tmp_path, monkeypatch
):
    """Even when both SQLite and .unknown sentinel creation fail (e.g. ENOSPC/EROFS),
    the stderr marker propagates to watchlist._run_topic() and forces cost_unknown=1."""
    import subprocess as sp

    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()
    topic = store.add_topic("Unwritable FS Topic")

    # Verify usage.begin() emits UNKNOWN_COST_MARKER on stderr even if .unknown open fails.
    journal = tmp_path / "usage.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))
    real_open = os.open

    def fail_sentinel_open(path, *args, **kwargs):
        if str(path).endswith(".unknown"):
            raise OSError("No space left on device")
        return real_open(path, *args, **kwargs)

    fake_stderr = io.StringIO()
    monkeypatch.setattr(usage.os, "open", fail_sentinel_open)
    monkeypatch.setattr(
        usage,
        "_connection",
        lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("database or disk is full")),
    )
    with patch.object(sys, "stderr", fake_stderr):
        assert usage.begin("openrouter") is None
    assert usage.UNKNOWN_COST_MARKER in fake_stderr.getvalue()
    assert not Path(f"{journal}.unknown").exists()

    # Verify watchlist._run_topic() forces cost_unknown=1 from child stderr even when
    # the journal DB is intact & empty and no .unknown file exists.
    monkeypatch.undo()
    monkeypatch.setattr(store, "_db_override", db_path)

    fake_report = json.dumps({
        "topic": topic["name"],
        "range_from": "2026-01-01",
        "range_to": "2026-04-01",
        "generated_at": "2026-04-01T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "local",
            "planner_model": "local",
            "rerank_model": "local",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": topic["name"],
        },
    })

    def fake_run(*args, **kwargs):
        nonce = kwargs["env"][usage.NONCE_ENV]
        marker = usage.unknown_cost_marker(nonce)
        return sp.CompletedProcess(
            args[0], 0, stdout=fake_report, stderr=f"warn\n{marker}\n"
        )

    monkeypatch.setattr(watchlist.subprocess, "run", fake_run)
    result = watchlist._run_topic(topic)
    assert result["cost_unknown"] == 1
    assert store.get_daily_unknown_cost_runs() == 1


def test_greptile_p1_report_stdout_and_spoofed_stderr_cannot_block_watchlist(
    tmp_path, monkeypatch
):
    """Report stdout containing UNKNOWN_COST_MARKER or stderr without the per-run nonce
    must NOT force cost_unknown=1."""
    import subprocess as sp

    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()
    topic = store.add_topic("Untrusted Report Text")

    spoofed_report = json.dumps({
        "topic": f"{topic['name']} {usage.UNKNOWN_COST_MARKER}",
        "range_from": "2026-01-01",
        "range_to": "2026-04-01",
        "generated_at": "2026-04-01T00:00:00Z",
        "provider_runtime": {
            "reasoning_provider": "local",
            "planner_model": "local",
            "rerank_model": "local",
        },
        "query_plan": {
            "intent": "test",
            "freshness_mode": "recent",
            "cluster_mode": "standard",
            "raw_topic": topic["name"],
        },
    })

    def fake_run(*args, **kwargs):
        assert kwargs["env"].get(usage.NONCE_ENV)
        return sp.CompletedProcess(
            args[0],
            0,
            stdout=spoofed_report,
            stderr=f"source echoed {usage.UNKNOWN_COST_MARKER} and {usage.UNKNOWN_COST_MARKER}:wrongnonce\n",
        )

    monkeypatch.setattr(watchlist.subprocess, "run", fake_run)
    result = watchlist._run_topic(topic)
    assert result["cost_unknown"] == 0
    assert store.get_daily_unknown_cost_runs() == 0


def test_greptile_p1_http_request_timeout_bounds_usage_journal_wait_without_deadline(
    tmp_path, monkeypatch
):
    """Even when deadline_monotonic is None, http.request(timeout=...) bounds usage journal waits."""
    journal = tmp_path / "usage-req-timeout.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    class Response(io.BytesIO):
        status = 200

    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(b'{"ok": true}'))

    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with usage._WRITE_LOCK:
            locked.set()
            release.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert locked.wait(timeout=2.0)
    try:
        started = time.monotonic()
        with patch.object(sys, "stderr", io.StringIO()):
            res = http.post(
                "https://openrouter.ai/api/v1/chat/completions",
                {},
                timeout=0.08,
                retries=1,
            )
        elapsed = time.monotonic() - started
        assert res == {"ok": True}
        assert elapsed < 0.5
    finally:
        release.set()
        holder.join(timeout=2.0)

    assert usage.read_journal(journal)["cost_unknown"] == 1


def test_greptile_p1_store_findings_sqlite_pre_3_35_fallback_without_returning(
    tmp_path, monkeypatch
):
    """When SQLite lacks RETURNING (< 3.35), store_findings still stays atomic inside
    BEGIN IMMEDIATE without a redundant follow-up conflict count query."""
    db_path = tmp_path / "research-legacy-sqlite.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    monkeypatch.setattr(store, "_SUPPORTS_RETURNING", False)
    store.init_db()
    topic = store.add_topic("Legacy SQLite")
    run1 = store.record_run(topic["id"], source_mode="v3")
    run2 = store.record_run(topic["id"], source_mode="v3")

    executed_sql: list[str] = []
    real_connect = store._connect

    class TracingConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            executed_sql.append(sql.strip())
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(store, "_connect", lambda *a, **k: TracingConn(real_connect(*a, **k)))

    counts1 = store.store_findings(
        run1,
        topic["id"],
        [
            {"source": "reddit", "source_url": "https://example.test/a", "source_title": "A", "engagement_score": 10},
            {"source": "reddit", "source_url": "https://example.test/a", "source_title": "A dup", "engagement_score": 25},
            {"source": "x", "source_url": "https://example.test/b", "source_title": "B", "engagement_score": 5},
        ],
    )
    assert counts1 == {"new": 2, "updated": 0}
    assert not any("RETURNING" in sql for sql in executed_sql)
    assert not any("sighting_count > 1" in sql for sql in executed_sql)
    assert executed_sql[0] == "BEGIN IMMEDIATE"

    executed_sql.clear()
    counts2 = store.store_findings(
        run2,
        topic["id"],
        [
            {"source": "reddit", "source_url": "https://example.test/a", "source_title": "A", "engagement_score": 15},
            {"source": "x", "source_url": "https://example.test/c", "source_title": "C", "engagement_score": 30},
        ],
    )
    assert counts2 == {"new": 1, "updated": 1}
    assert not any("RETURNING" in sql for sql in executed_sql)
    assert not any("sighting_count > 1" in sql for sql in executed_sql)
    assert len(store.get_sightings_for_run(topic["id"], run1)) == 2
    assert len(store.get_sightings_for_run(topic["id"], run2)) == 2
    with sqlite3.connect(db_path) as check:
        eng_a, cnt_a = check.execute(
            "SELECT engagement_score, sighting_count FROM findings WHERE source_url = ?",
            ("https://example.test/a",),
        ).fetchone()
    assert eng_a == 25.0
    assert cnt_a == 2


def test_greptile_p1_journal_wait_bounded_by_deadline_monotonic(tmp_path, monkeypatch):
    """Holding _WRITE_LOCK must not stall usage.begin()/http.request() past deadline_monotonic."""
    journal = tmp_path / "usage-deadline.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with usage._WRITE_LOCK:
            locked.set()
            release.wait(timeout=5.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert locked.wait(timeout=2.0)
    try:
        started = time.monotonic()
        fake_stderr = io.StringIO()
        with patch.object(sys, "stderr", fake_stderr):
            attempt = usage.begin("openrouter", deadline_monotonic=started + 0.08)
        elapsed = time.monotonic() - started
        assert attempt is None
        assert elapsed < 0.5
        assert usage.UNKNOWN_COST_MARKER in fake_stderr.getvalue()
    finally:
        release.set()
        holder.join(timeout=2.0)

    assert usage.read_journal(journal)["cost_unknown"] == 1

    # Already-expired deadline fails fast without touching SQLite at all.
    journal2 = tmp_path / "usage-expired.db"
    usage.create_journal(journal2)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal2))
    started = time.monotonic()
    with patch.object(sys, "stderr", io.StringIO()):
        assert usage.begin("openrouter", deadline_monotonic=started - 1.0) is None
    assert time.monotonic() - started < 0.2
    assert usage.read_journal(journal2)["cost_unknown"] == 1


def test_greptile_p2_project_pass_prefix_placeholder_falls_back_to_global_file(
    monkeypatch, tmp_path
):
    _isolate_env(monkeypatch, tmp_path)
    global_env = tmp_path / "global.env"
    global_env.write_text("LAST30DAYS_PASS_PREFIX=custom/global/\n", encoding="utf-8")
    global_env.chmod(0o600)
    project_dir = tmp_path / ".claude"
    project_dir.mkdir()
    project_env = project_dir / "last30days.env"
    project_env.write_text("LAST30DAYS_PASS_PREFIX=${user_config.pass_prefix}\n", encoding="utf-8")
    project_env.chmod(0o600)

    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.setattr(env, "_find_project_env", lambda: project_env)

    seen_prefixes: list[str] = []

    def fake_load_pass(_keys, prefix):
        seen_prefixes.append(prefix)
        return {"XAI_API_KEY": "xai-saved"}

    monkeypatch.setattr(env, "_load_pass", fake_load_pass)
    cfg = env.get_config(env.ConfigLoadPolicy(allow_project_config=True))

    assert seen_prefixes == ["custom/global/"]
    assert cfg["XAI_API_KEY"] == "xai-saved"


def test_greptile_p2_request_retries_zero_does_not_retry_dns_failures(monkeypatch):
    import socket

    calls = 0

    def fail_dns(req, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))

    monkeypatch.setattr(http, "_open_request", fail_dns)
    with patch("lib.http.time.sleep") as mock_sleep:
        with pytest.raises(http.HTTPError):
            http.request("GET", "https://dns-fail.example/probe", retries=0)

    assert calls == 1
    mock_sleep.assert_not_called()


def test_greptile_p2_webhooks_succeed_on_non_utf8_2xx_response_body(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()

    class Response(io.BytesIO):
        status = 200

    # Latin-1 / invalid UTF-8 bytes in a 200 response body must not fail delivery.
    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(b"ok \xff\xfe"))

    for channel in (
        "https://hooks.slack.com/services/T000/B000/DUMMY",
        "https://webhook.example.com/notify",
    ):
        store.set_setting("delivery_channel", channel)
        watchlist._deliver_findings("AI agents", {"new": 1, "updated": 0})
        assert capsys.readouterr().err == ""


@pytest.mark.parametrize("tiny_neg", ["-1e-400", "-1e-500", "-1e-1000", "-0.0000000000000000000001"])
def test_greptile_p2_parse_budget_rejects_tiny_negative_underflow(tmp_path, monkeypatch, tiny_neg):
    with pytest.raises(ValueError):
        store.parse_budget(tiny_neg)

    monkeypatch.setattr(store, "_db_override", tmp_path / "research.db")
    store.init_db()
    store.set_setting("daily_budget", "5.00")
    with pytest.raises(SystemExit):
        watchlist.cmd_config(SimpleNamespace(key="budget", value=tiny_neg))
    assert store.get_setting("daily_budget") == "5.00"


@pytest.mark.skipif(
    not store._SUPPORTS_RETURNING,
    reason="SQLite < 3.35 uses fallback SELECT path",
)
def test_greptile_p2_store_findings_has_no_redundant_select_or_conflict_count_query(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "research.db"
    monkeypatch.setattr(store, "_db_override", db_path)
    store.init_db()
    topic = store.add_topic("No Redundant Select")
    run1 = store.record_run(topic["id"], source_mode="v3")
    run2 = store.record_run(topic["id"], source_mode="v3")

    executed_sql: list[str] = []
    real_connect = store._connect

    class TracingConn:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            executed_sql.append(sql.strip())
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(store, "_connect", lambda *a, **k: TracingConn(real_connect(*a, **k)))

    counts1 = store.store_findings(
        run1,
        topic["id"],
        [
            {"source": "reddit", "source_url": "https://example.test/1", "source_title": "One", "engagement_score": 10},
            {"source": "reddit", "source_url": "https://example.test/1", "source_title": "One dup", "engagement_score": 20},
            {"source": "x", "source_url": "https://example.test/2", "source_title": "Two", "engagement_score": 5},
        ],
    )
    assert counts1 == {"new": 2, "updated": 0}
    assert not any("FROM findings" in sql and sql.upper().startswith("SELECT") for sql in executed_sql)

    executed_sql.clear()
    counts2 = store.store_findings(
        run2,
        topic["id"],
        [
            {"source": "reddit", "source_url": "https://example.test/1", "source_title": "One", "engagement_score": 15},
            {"source": "x", "source_url": "https://example.test/3", "source_title": "Three", "engagement_score": 7},
        ],
    )
    assert counts2 == {"new": 1, "updated": 1}
    assert not any("FROM findings" in sql and sql.upper().startswith("SELECT") for sql in executed_sql)


def test_greptile_p1_finish_gets_fresh_window_after_slow_response_without_hard_deadline(
    tmp_path, monkeypatch
):
    """When deadline_monotonic is None and a paid response arrives after the pre-request
    begin cutoff, usage.finish() gets its own fresh short window and records the charge."""
    journal = tmp_path / "usage-slow-resp.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    class Response(io.BytesIO):
        status = 200

    payload = json.dumps({
        "choices": [],
        "usage": {"cost": 0.42, "prompt_tokens": 12, "completion_tokens": 8},
    }).encode("utf-8")

    def slow_open(req, timeout):
        # Sleep longer than timeout=0.05 so the pre-request begin_deadline has already expired
        # by the time the response finishes, while the socket itself returns 200 cleanly.
        time.sleep(0.12)
        return Response(payload)

    monkeypatch.setattr(http, "_open_request", slow_open)
    result = http.post(
        "https://openrouter.ai/api/v1/chat/completions",
        {},
        timeout=0.05,
        retries=1,
    )
    assert result["choices"] == []
    totals = usage.read_journal(journal)
    assert totals["token_cost"] == pytest.approx(0.42)
    assert totals["cost_unknown"] == 0
    assert totals["prompt_tokens"] == 12
    assert totals["completion_tokens"] == 8


@pytest.mark.parametrize("weird_status", [["in_progress"], {"state": "ok"}])
def test_greptile_p1_unhashable_usage_status_returns_response_and_keeps_unknown(
    tmp_path, monkeypatch, weird_status
):
    """An OpenRouter-compatible response with an unhashable status field must still
    return the valid payload while leaving the charge marked cost_unknown=1."""
    journal = tmp_path / "usage-weird-status.db"
    usage.create_journal(journal)
    monkeypatch.setenv(usage.JOURNAL_ENV, str(journal))

    class Response(io.BytesIO):
        status = 200

    payload = {
        "choices": [{"message": {"content": "paid answer"}}],
        "status": weird_status,
        "usage": {"cost": 0.15, "prompt_tokens": 5, "completion_tokens": 3},
    }
    encoded = json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(http, "_open_request", lambda req, timeout: Response(encoded))

    result = http.post("https://openrouter.ai/api/v1/chat/completions", {}, retries=1)
    assert result["choices"][0]["message"]["content"] == "paid answer"
    assert usage.read_journal(journal)["cost_unknown"] == 1




