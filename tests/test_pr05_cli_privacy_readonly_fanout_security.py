"""Security and concurrency regression tests for PR05 (HIGH-001, 003, 004, 006, 007)."""

from __future__ import annotations

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
from lib import cookie_extract, env, html_publish, pipeline, schema


def _sample_report(topic: str = "OpenClaw") -> schema.Report:
    item = schema.SourceItem(
        item_id="reddit-1",
        source="reddit",
        title="Widget API is open",
        body="Community thread discussing Widget API status.",
        url="https://reddit.com/r/example/comments/abc/widget",
        published_at="2026-07-09T12:00:00Z",
        snippet="Widget API is open",
    )
    candidate = schema.Candidate(
        candidate_id="candidate-1",
        item_id=item.item_id,
        source=item.source,
        title=item.title,
        url=item.url,
        snippet=item.snippet,
        subquery_labels=["primary"],
        native_ranks={"primary:reddit": 1},
        local_relevance=0.9,
        freshness=90,
        engagement=25,
        source_quality=0.8,
        rrf_score=0.02,
        final_score=90,
        source_items=[item],
    )
    return schema.Report(
        topic=topic,
        range_from="2026-06-10",
        range_to="2026-07-10",
        generated_at="2026-07-10T12:00:00Z",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="local",
            planner_model="fixture",
            rerank_model="fixture",
        ),
        query_plan=schema.QueryPlan(
            intent="research",
            freshness_mode="balanced_recent",
            cluster_mode="story",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query=topic,
                    ranking_query=topic,
                    sources=["reddit"],
                )
            ],
            source_weights={"reddit": 1.0},
        ),
        clusters=[
            schema.Cluster(
                cluster_id="cluster-1",
                title="Widget API status",
                candidate_ids=[candidate.candidate_id],
                representative_ids=[candidate.candidate_id],
                sources=["reddit"],
                score=90,
            )
        ],
        ranked_candidates=[candidate],
        items_by_source={"reddit": [item]},
        errors_by_source={},
        source_status={"reddit": schema.SourceOutcome(source="reddit", state="ok", items_returned=1)},
    )


def _run_cli(argv: list[str], *, environ: dict[str, str] | None = None) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    env_patch = {"LAST30DAYS_SKIP_PREFLIGHT": "1", **(environ or {})}
    with mock.patch.dict(os.environ, env_patch, clear=False), \
         mock.patch.object(sys, "argv", ["last30days.py", *argv]), \
         redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            rc = cli.main()
        except SystemExit as exc:
            rc = int(exc.code) if isinstance(exc.code, int) else 1
    return rc, stdout.getvalue(), stderr.getvalue()


# ---------------------------------------------------------------------------
# HIGH-001: --record-fixtures logged-in Reddit session guard for --drill / --discover
# ---------------------------------------------------------------------------


SESSION_ON_CONFIG = {
    "_BROWSER_COOKIE_MODE": "read",
    env.REDDIT_CONSENT_VAR: "true",
    env.REDDIT_FROM_BROWSER_VAR: "chrome",
}


class TestRecordFixturesRedditSessionGuard:
    @pytest.mark.parametrize(
        "argv",
        [
            ["--drill", "cluster 1"],
            ["--discover"],
            ["--discover", "AI agents"],
            ["--discover", "--nominate-only"],
            ["--discover", "--judgments", "judgments.json"],
            ["--research-only", "setup"],
        ],
        ids=[
            "drill",
            "discover-global",
            "discover-domain",
            "discover-nominate-only",
            "discover-judgments",
            "research-only-setup",
        ],
    )
    def test_refuses_logged_in_reddit_session_on_drill_discover_and_research_only(
        self, tmp_path: Path, argv: list[str],
    ):
        rec_dir = tmp_path / "rec"
        with mock.patch.object(cli.env, "get_config", return_value=dict(SESSION_ON_CONFIG)), \
             mock.patch.object(cli, "_run_drill", side_effect=AssertionError("drill must not run")), \
             mock.patch.object(cli, "_run_discover", side_effect=AssertionError("discover must not run")), \
             mock.patch.object(cli, "_run_discover_protocol_leg", side_effect=AssertionError("protocol leg must not run")), \
             mock.patch.object(cli.pipeline, "run", side_effect=AssertionError("pipeline must not run")):
            rc, _out, err = _run_cli([*argv, "--record-fixtures", str(rec_dir)])

        assert rc == 2
        assert "--record-fixtures refuses" in err
        assert "--no-browser-cookies" in err

    @pytest.mark.parametrize(
        ("argv", "target"),
        [
            (["--drill", "cluster 1", "--no-browser-cookies"], "_run_drill"),
            (["--discover", "AI agents", "--no-browser-cookies"], "_run_discover"),
        ],
    )
    def test_allows_drill_and_discover_when_browser_cookies_disabled(
        self, tmp_path: Path, argv: list[str], target: str,
    ):
        def fake_get_config(*, policy):
            return {
                "_BROWSER_COOKIE_MODE": policy.browser_cookies,
                env.REDDIT_CONSENT_VAR: "true",
                env.REDDIT_FROM_BROWSER_VAR: "chrome",
            }

        with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
             mock.patch.object(cli, target, return_value=0) as handler:
            rc, _out, err = _run_cli([*argv, "--record-fixtures", str(tmp_path / "rec")])

        assert rc == 0, err
        handler.assert_called_once()

    def test_discover_finalize_is_offline_and_not_blocked_by_session_guard(self, tmp_path: Path):
        def fake_get_config(*, policy):
            return {
                "_BROWSER_COOKIE_MODE": policy.browser_cookies,
                env.REDDIT_CONSENT_VAR: "true",
                env.REDDIT_FROM_BROWSER_VAR: "chrome",
            }

        with mock.patch.object(cli.env, "get_config", side_effect=fake_get_config), \
             mock.patch.object(cli, "_run_discover_protocol_leg", return_value=0) as handler:
            rc, _out, err = _run_cli(
                ["--discover", "--finalize", "--record-fixtures", str(tmp_path / "rec")]
            )

        assert rc == 0, err
        handler.assert_called_once()

    @pytest.mark.parametrize("flag", ["--diagnose", "--preflight"])
    @pytest.mark.parametrize("target_kind", ["existing_file", "existing_dir", "missing_file"])
    def test_diagnose_and_preflight_reject_record_fixtures_without_touching_files(
        self, tmp_path: Path, flag: str, target_kind: str,
    ):
        sentinel = '{"format": "sentinel", "exchanges": [{"id": 1}]}\n'
        if target_kind == "existing_file":
            fixture_arg = tmp_path / "existing.json"
            fixture_arg.write_text(sentinel, encoding="utf-8")
            check_path = fixture_arg
        elif target_kind == "existing_dir":
            fixture_arg = tmp_path / "existing_dir"
            fixture_arg.mkdir()
            check_path = fixture_arg / "http.json"
            check_path.write_text(sentinel, encoding="utf-8")
        else:
            fixture_arg = tmp_path / "never-created.json"
            check_path = fixture_arg

        with mock.patch.object(
            cli.http, "recording_requests",
            side_effect=AssertionError("recording_requests must not start"),
        ), mock.patch.object(
            cli.env, "get_config",
            side_effect=AssertionError("must reject before loading config"),
        ):
            rc, out, err = _run_cli([flag, "--record-fixtures", str(fixture_arg)])

        assert rc == 2
        assert out == ""
        assert f"--record-fixtures cannot be combined with {flag}" in err
        if target_kind == "missing_file":
            assert not check_path.exists()
        else:
            assert check_path.read_text(encoding="utf-8") == sentinel


# ---------------------------------------------------------------------------
# HIGH-003: Offline, --mock, hosted-API, empty/invalid, non-auto setup cookie guard
# ---------------------------------------------------------------------------


class TestConfigPolicyNoUnexpectedCookieReads:
    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["some topic", "--mock"], "off"),
            (["--drill", "cluster 1", "--mock"], "off"),
            (["--discover", "AI agents", "--mock"], "off"),
            (["--discover", "--mock"], "off"),
            (["--discover", "--finalize"], "off"),
            (["--discover", "--finalize", "--angles", "angles.json"], "off"),
            ([], "off"),
            (["--discover-shallow"], "off"),
            (["--deep-research"], "off"),
            (["--nominate-only"], "off"),
            (["--finalize"], "off"),
            (["--angles", "angles.json"], "off"),
            (["--discover", "AI agents", "--emit=html"], "off"),
            (["--drill", "cluster 1", "--publish-html"], "off"),
            (["some topic", "--publish-html"], "off"),
            (["setup", "--openclaw", "--allow-browser-cookies"], "off"),
            (["setup", "--github-start", "--allow-browser-cookies"], "off"),
            (["setup", "--github-poll", "--allow-browser-cookies"], "off"),
            (["setup", "--github", "--allow-browser-cookies"], "off"),
            (["setup", "--device-auth", "--allow-browser-cookies"], "off"),
            (["setup", "--reddit-session", "--allow-browser-cookies"], "off"),
            (["setup", "--no-reddit-session", "--allow-browser-cookies"], "off"),
            (["setup", "--store-key", "XAI_API_KEY", "--allow-browser-cookies"], "off"),
            (["setup", "--install-hooks", "--allow-browser-cookies"], "off"),
            # Positive controls: legitimate live flows still allow reads.
            (["some topic"], "read"),
            (["--discover", "AI agents"], "read"),
            (["--discover", "--verify-freshness"], "read"),
            (["--discover", "AI agents", "--verify-freshness"], "read"),
            (["--drill", "cluster 1"], "read"),
            (["--drill", "cluster 1", "--verify-freshness"], "read"),
            (["setup", "--allow-browser-cookies"], "read"),
            (["setup", "--use-recorded-browser-consent"], "read"),
            # Cache-only freshness verification and --diagnose/--preflight stay plan_only.
            (["--verify-freshness"], "plan_only"),
            (["--diagnose", "--mock"], "plan_only"),
            (["--preflight", "--mock"], "plan_only"),
        ],
    )
    def test_config_policy_matrix(self, monkeypatch, argv: list[str], expected: str):
        for var in (
            *env.AGENT_HOST_ENV_VARS,
            env.HOST_AGENT_VAR,
            env.X_HOST_VAR,
            "LAST30DAYS_API_KEY",
            "LAST30DAYS_API_BASE",
            "LAST30DAYS_API_MODE",
            "LAST30DAYS_CORPUS_DIRS",
            "PERPLEXITY_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        parser = cli.build_parser()
        args, extra = parser.parse_known_args(argv)
        topic = " ".join(args.topic).strip()
        assert cli._config_policy_for_args(args, topic, extra).browser_cookies == expected

    def test_hosted_api_run_sets_browser_cookies_off_and_skips_cookie_extraction(
        self, tmp_path: Path, monkeypatch,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        monkeypatch.setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")

        parser = cli.build_parser()
        args, extra = parser.parse_known_args(["Ghostty terminal"])
        hosted_policy = cli._config_policy_for_args(args, "Ghostty terminal", extra)
        assert hosted_policy.browser_cookies == "off"
        assert hosted_policy.load_secret_stores is False

        # --corpus under hosted env stays deferred ("off", no secret stores)
        # until .env EXCLUDE_SOURCES is checked; unconditional local modes keep "read".
        corpus_args, corpus_extra = parser.parse_known_args(
            ["Ghostty terminal", "--corpus", str(tmp_path)]
        )
        corpus_policy = cli._config_policy_for_args(corpus_args, "Ghostty terminal", corpus_extra)
        assert corpus_policy.browser_cookies == "off"
        assert corpus_policy.load_secret_stores is False

        for bypass_argv in (
            ["Ghostty terminal", "--deep-research"],
            ["Ghostty terminal", "--record-fixtures", str(tmp_path / "rec")],
        ):
            b_args, b_extra = parser.parse_known_args(bypass_argv)
            b_policy = cli._config_policy_for_args(b_args, "Ghostty terminal", b_extra)
            assert b_policy.browser_cookies == "read"
            assert b_policy.load_secret_stores is True

        with mock.patch.object(
            env, "_load_keychain",
            side_effect=AssertionError("hosted run must not query Keychain"),
        ), mock.patch.object(
            env, "_load_pass",
            side_effect=AssertionError("hosted run must not query pass"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("hosted run must not extract browser cookies"),
        ), mock.patch.object(
            cookie_extract, "extract_cookies",
            side_effect=AssertionError("hosted run must not extract browser cookies"),
        ), mock.patch("lib.hosted.run_hosted", return_value=0) as run_hosted:
            rc, _out, err = _run_cli(["Ghostty terminal"])

        assert rc == 0, err
        run_hosted.assert_called_once()

    def test_hosted_excluded_corpus_stays_hosted_without_cookie_or_secret_store_reads(
        self, tmp_path: Path, monkeypatch,
    ):
        notes = tmp_path / "notes"
        notes.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n"
            f"LAST30DAYS_CORPUS_DIRS={notes}\nEXCLUDE_SOURCES=corpus\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        monkeypatch.setenv("LAST30DAYS_CORPUS_DIRS", str(notes))
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(
            env, "_load_keychain",
            side_effect=AssertionError("hosted excluded-corpus run must not query Keychain"),
        ), mock.patch.object(
            env, "_load_pass",
            side_effect=AssertionError("hosted excluded-corpus run must not query pass"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("hosted excluded-corpus run must not read browser cookies"),
        ), mock.patch.object(
            cookie_extract, "extract_cookies",
            side_effect=AssertionError("hosted excluded-corpus run must not read browser cookies"),
        ), mock.patch("lib.hosted.run_hosted", return_value=0) as run_hosted:
            rc, _out, err = _run_cli(["Ghostty terminal", "--corpus", str(notes)])

        assert rc == 0, err
        run_hosted.assert_called_once()

    @pytest.mark.parametrize("no_cookies", [False, True])
    def test_hosted_dotenv_corpus_bypass_reloads_browser_cookies_for_local_run(
        self, tmp_path: Path, monkeypatch, no_cookies: bool,
    ):
        notes = tmp_path / "notes"
        notes.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"FROM_BROWSER=chrome\nBROWSER_CONSENT=true\nLAST30DAYS_CORPUS_DIRS={notes}\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        monkeypatch.delenv("LAST30DAYS_CORPUS_DIRS", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)
        monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
        monkeypatch.setenv("LAST30DAYS_NO_PATENT_DIGEST", "1")

        discovered: list[str] = []
        keychain_calls = 0

        def fake_discover(cfg):
            discovered.append(str(cfg.get("_BROWSER_COOKIE_MODE")))
            cfg["AUTH_TOKEN"] = "dummy-reloaded-auth-token"
            cfg["CT0"] = "dummy-reloaded-ct0"

        def fake_keychain(keys, aliases=None):
            nonlocal keychain_calls
            keychain_calls += 1
            return {"XQUIK_API_KEY": "dummy-keychain-xquik"}

        argv = [
            "Ghostty",
            "--search=corpus",
            "--quick",
            "--web-backend=none",
            "--x-handle=ghostty",
        ]
        if no_cookies:
            argv.append("--no-browser-cookies")

        with mock.patch.object(env, "_load_keychain", side_effect=fake_keychain), \
             mock.patch.object(env, "_load_pass", return_value={}), \
             mock.patch.object(env, "_discover_and_apply_x_credentials", side_effect=fake_discover), \
             mock.patch.object(cli, "_resolved_company", return_value={}), \
             mock.patch.object(cli.pipeline, "diagnose", return_value={"available_sources": ["corpus"]}), \
             mock.patch.object(cli.pipeline, "run", return_value=_sample_report("Ghostty")) as run_mock, \
             mock.patch.object(cli, "_render_save_and_print", return_value=0):
            rc, _out, err = _run_cli(argv)

        assert rc == 0, err
        # Keychain is skipped on the tentative hosted load and queried once on local reload.
        assert keychain_calls == 1
        reloaded_cfg = run_mock.call_args.kwargs["config"]
        assert reloaded_cfg["XQUIK_API_KEY"] == "dummy-keychain-xquik"
        if no_cookies:
            assert discovered == []
            assert reloaded_cfg["_BROWSER_COOKIE_MODE"] == "off"
            assert not reloaded_cfg.get("AUTH_TOKEN")
        else:
            assert discovered == ["read"]
            assert reloaded_cfg["_BROWSER_COOKIE_MODE"] == "read"
            assert reloaded_cfg["AUTH_TOKEN"] == "dummy-reloaded-auth-token"
        # Verify cached research_options refreshed config_sha256 after local reload.
        cached_payload = json.loads((tmp_path / "last-report.json").read_text(encoding="utf-8"))
        cached_sha = cached_payload["research_options"]["config_sha256"]
        parser = cli.build_parser()
        check_args = parser.parse_args(argv)
        without_reloaded = dict(reloaded_cfg)
        without_reloaded.pop("AUTH_TOKEN", None)
        without_reloaded.pop("CT0", None)
        without_reloaded.pop("XQUIK_API_KEY", None)
        expected_with = cli._cache_research_options(
            check_args, reloaded_cfg, resolved_corpus_dirs=[notes],
        )["config_sha256"]
        expected_without = cli._cache_research_options(
            check_args, without_reloaded, resolved_corpus_dirs=[notes],
        )["config_sha256"]
        assert expected_with != expected_without
        assert cached_sha == expected_with

    @pytest.mark.parametrize(
        "clarify_args",
        [["--clarify-angle", "pricing"], ["--clarify-broadly"]],
    )
    def test_clarify_flags_with_dotenv_corpus_reject_before_cookie_or_secret_store_reload(
        self, tmp_path: Path, monkeypatch, clarify_args: list[str],
    ):
        notes = tmp_path / "notes"
        notes.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"FROM_BROWSER=chrome\nBROWSER_CONSENT=true\nLAST30DAYS_CORPUS_DIRS={notes}\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        monkeypatch.delenv("LAST30DAYS_CORPUS_DIRS", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(
            env, "_load_keychain",
            side_effect=AssertionError("invalid clarify run must not query Keychain"),
        ), mock.patch.object(
            env, "_load_pass",
            side_effect=AssertionError("invalid clarify run must not query pass"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("invalid clarify run must not extract browser cookies"),
        ):
            rc, out, err = _run_cli(["Ghostty", *clarify_args])

        assert rc == 2
        assert out == ""
        assert "--clarify-angle and --clarify-broadly" in err

    @pytest.mark.parametrize(
        "clarify_args",
        [["--clarify-angle", "pricing"], ["--clarify-broadly"]],
    )
    def test_clarify_flags_without_hosted_env_skip_cookies_and_secret_stores(
        self, tmp_path: Path, monkeypatch, clarify_args: list[str],
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("LAST30DAYS_API_KEY", raising=False)
        monkeypatch.delenv("LAST30DAYS_API_BASE", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(
            env, "_load_keychain",
            side_effect=AssertionError("invalid clarify run must not query Keychain"),
        ), mock.patch.object(
            env, "_load_pass",
            side_effect=AssertionError("invalid clarify run must not query pass"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("invalid clarify run must not extract browser cookies"),
        ):
            rc, out, err = _run_cli(["Ghostty", *clarify_args])

        assert rc == 2
        assert out == ""
        assert "--clarify-angle and --clarify-broadly" in err

    @pytest.mark.parametrize(
        "argv",
        [
            ["OpenAI vs Anthropic", "--deep-research"],
            ["OpenAI", "--competitors", "--deep-research"],
            ["OpenAI", "--competitors-list", "Anthropic", "--deep-research"],
        ],
    )
    def test_deep_research_with_comparison_rejects_before_cookies_or_secret_stores(
        self, tmp_path: Path, monkeypatch, argv: list[str],
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(
            env, "_load_keychain",
            side_effect=AssertionError("invalid deep-research comparison must not query Keychain"),
        ), mock.patch.object(
            env, "_load_pass",
            side_effect=AssertionError("invalid deep-research comparison must not query pass"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("invalid deep-research comparison must not extract browser cookies"),
        ):
            rc, out, err = _run_cli(argv)

        assert rc == 2
        assert out == ""
        assert "--deep-research cannot be combined with competitor or vs-mode" in err

    def test_env_get_config_skips_keychain_and_pass_when_policy_disables_secret_stores(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "missing.env")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(
            env, "_load_keychain", side_effect=AssertionError("Keychain must not be queried"),
        ), mock.patch.object(
            env, "_load_pass", side_effect=AssertionError("pass must not be queried"),
        ):
            cfg = env.get_config(policy=env.ConfigLoadPolicy(load_secret_stores=False))

        assert cfg["_CONFIG_SOURCE"] == "env_only"

    @pytest.mark.parametrize(
        ("argv", "expected_rc"),
        [
            (["Ghostty", "--mock", "--quick"], 0),
            (["--diagnose", "--mock"], 0),
            (["--discover", "--finalize"], 0),
            ([], 2),
            (["--discover-shallow"], 2),
            (["--deep-research"], 2),
            (["OpenAI vs Anthropic", "--deep-research"], 2),
            (["--verify-freshness", "--mock"], 0),
            (["queue", "list"], 0),
            (["setup", "--github-start"], 0),
        ],
    )
    def test_offline_mock_finalize_and_invalid_modes_never_query_keychain_or_pass(
        self, tmp_path: Path, monkeypatch, argv: list[str], expected_rc: int,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("LAST30DAYS_API_KEY", raising=False)
        monkeypatch.delenv("LAST30DAYS_API_BASE", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)
        monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
        if "--verify-freshness" in argv:
            assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))

        with mock.patch.object(
            env, "_load_keychain", side_effect=AssertionError("Keychain must not be queried"),
        ), mock.patch.object(
            env, "_load_pass", side_effect=AssertionError("pass must not be queried"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("browser cookies must not be extracted"),
        ), mock.patch.object(cli.pipeline, "diagnose", return_value={"available_sources": ["reddit"]}), \
             mock.patch.object(cli.pipeline, "run", return_value=_sample_report("Ghostty")), \
             mock.patch.object(cli, "_render_save_and_print", return_value=0), \
             mock.patch.object(cli, "_run_discover_protocol_leg", return_value=0), \
             mock.patch.object(cli, "_run_queue_list", return_value=0), \
             mock.patch("lib.setup_wizard.run_github_start", return_value={"status": "started"}):
            rc, _out, err = _run_cli(argv)

        assert rc == expected_rc, err

    def test_cached_report_export_skips_keychain_pass_and_browser_cookies(
        self, tmp_path: Path, monkeypatch,
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        syn = tmp_path / "syn.md"
        syn.write_text("# Cached export synthesis\n", encoding="utf-8")

        with mock.patch.object(
            env, "_load_keychain", side_effect=AssertionError("Keychain must not be queried"),
        ), mock.patch.object(
            env, "_load_pass", side_effect=AssertionError("pass must not be queried"),
        ), mock.patch.object(
            env, "_discover_and_apply_x_credentials",
            side_effect=AssertionError("browser cookies must not be extracted"),
        ):
            rc, out, err = _run_cli([
                "OpenClaw", "--cached-report", "--emit=html", "--synthesis-file", str(syn),
            ])

        assert rc == 0, err
        assert "Cached export synthesis" in out

    @pytest.mark.parametrize("secret_store", ["keychain", "pass"])
    def test_hosted_corpus_agent_host_html_cache_reuse_reloads_secret_stores_without_cookies(
        self, tmp_path: Path, monkeypatch, secret_store: str,
    ):
        notes = tmp_path / "notes"
        notes.mkdir()
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"FROM_BROWSER=chrome\nBROWSER_CONSENT=true\nLAST30DAYS_CORPUS_DIRS={notes}\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_API_KEY", "sk_live_DUMMY_TEST_KEY_00000")
        monkeypatch.setenv("LAST30DAYS_API_BASE", "https://api.example.test/api/v1")
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.delenv("LAST30DAYS_API_MODE", raising=False)
        monkeypatch.delenv("LAST30DAYS_CORPUS_DIRS", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)
        monkeypatch.delenv("LAST30DAYS_ALLOW_ENGINE_PLAN", raising=False)
        monkeypatch.delenv("XAI_API_KEY", raising=False)

        syn = tmp_path / "syn.md"
        syn.write_text("# Agent host cached synthesis\n", encoding="utf-8")
        stored_secret = {"XAI_API_KEY": f"dummy-{secret_store}-xai-key"}
        kc_return = stored_secret if secret_store == "keychain" else {}
        pass_return = stored_secret if secret_store == "pass" else {}

        # Build and save a cached report as produced by an earlier local run
        # that had the Keychain/pass key loaded (without browser cookies).
        with mock.patch.object(env, "_load_keychain", return_value=kc_return), \
             mock.patch.object(env, "_load_pass", return_value=pass_return):
            prior_config = env.get_config(
                policy=env.ConfigLoadPolicy(browser_cookies="off", load_secret_stores=True)
            )
        prior_args = cli.build_parser().parse_args([
            "OpenClaw", "--emit=html", "--synthesis-file", str(syn),
        ])
        prior_opts = cli._cache_research_options(
            prior_args, prior_config, resolved_corpus_dirs=[notes],
        )
        prior_report = _sample_report("OpenClaw")
        prior_report.range_from, prior_report.range_to = cli.dates.get_date_range(30)
        assert cli._write_last_run("OpenClaw", prior_report, research_options=prior_opts)

        kc_mock = mock.Mock(return_value=kc_return)
        pass_mock = mock.Mock(return_value=pass_return)
        with mock.patch.object(env, "_load_keychain", kc_mock), \
             mock.patch.object(env, "_load_pass", pass_mock), \
             mock.patch.object(
                 env, "_discover_and_apply_x_credentials",
                 side_effect=AssertionError("HTML cache reuse must not read browser cookies"),
             ), \
             mock.patch.object(
                 cookie_extract, "extract_cookies",
                 side_effect=AssertionError("HTML cache reuse must not read browser cookies"),
             ), \
             mock.patch.object(
                 cli.pipeline, "run",
                 side_effect=AssertionError("HTML cache reuse must not start research"),
             ):
            rc, out, err = _run_cli([
                "OpenClaw", "--emit=html", "--synthesis-file", str(syn),
            ])

        assert rc == 0, err
        assert "Reusing cached report data" in err
        assert "Agent host cached synthesis" in out
        # Initial tentative hosted load skipped secret stores; local HTML cache
        # check reloaded them once without touching browser cookies.
        assert kc_mock.call_count == 1
        assert pass_mock.call_count == 1

    @pytest.mark.parametrize(
        ("argv", "expected_load_stores"),
        [
            (["setup", "--openclaw", "--github-start"], True),
            (["setup", "--openclaw", "--device-auth"], True),
            (["setup", "--openclaw", "--reddit-session"], True),
            (["setup", "--github-start"], False),
            (["setup", "--store-key", "XAI_API_KEY", "--openclaw"], False),
            (["setup", "--install-hooks", "--openclaw"], False),
        ],
    )
    def test_setup_openclaw_precedence_preserves_secret_stores_in_policy(
        self, argv: list[str], expected_load_stores: bool,
    ):
        parser = cli.build_parser()
        args, extra = parser.parse_known_args(argv)
        policy = cli._config_policy_for_args(args, "setup", extra)
        assert policy.browser_cookies == "off"
        assert policy.load_secret_stores is expected_load_stores

    def test_setup_openclaw_with_github_start_reports_keychain_keys(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "missing.env")
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("LAST30DAYS_SKIP_KEYCHAIN", raising=False)

        with mock.patch.object(env, "_load_keychain", return_value={"XAI_API_KEY": "dummy-kc-xai"}) as kc, \
             mock.patch.object(env, "_load_pass", return_value={}), \
             mock.patch.object(
                 env, "_discover_and_apply_x_credentials",
                 side_effect=AssertionError("openclaw setup must not read browser cookies"),
             ), \
             mock.patch("lib.setup_wizard._install_digg_cli", return_value=(False, "no_npx", "", "")):
            rc, out, err = _run_cli(["setup", "--openclaw", "--github-start"])

        assert rc == 0, err
        kc.assert_called_once()
        payload = json.loads(out)
        assert payload["keys"]["xai"] is True
        assert payload["x_method"] == "xai"

    @pytest.mark.parametrize(
        "argv",
        [
            [],
            ["--discover-shallow"],
            ["--deep-research"],
            ["--nominate-only"],
            ["setup", "--openclaw", "--allow-browser-cookies"],
            ["setup", "--github-start", "--allow-browser-cookies"],
        ],
    )
    def test_empty_invalid_and_non_auto_setup_never_trigger_cookie_extraction(
        self, tmp_path: Path, monkeypatch, argv: list[str],
    ):
        env_file = tmp_path / ".env"
        env_file.write_text("FROM_BROWSER=chrome\nBROWSER_CONSENT=true\n", encoding="utf-8")
        env_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_FILE", env_file)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_SKIP_KEYCHAIN", "1")

        with mock.patch.object(env, "_load_keychain", return_value={}), \
             mock.patch.object(env, "_load_pass", return_value={}), \
             mock.patch.object(
                 env, "_discover_and_apply_x_credentials",
                 side_effect=AssertionError("must not extract browser cookies"),
             ), \
             mock.patch("lib.setup_wizard.run_openclaw_setup", return_value={"ok": True}), \
             mock.patch("lib.setup_wizard.run_github_start", return_value={"status": "started"}):
            rc, _out, _err = _run_cli(argv)

        expected_rc = 0 if argv and argv[0] == "setup" else 2
        assert rc == expected_rc


# ---------------------------------------------------------------------------
# HIGH-004: Shared config dict mutation race & cross-entity contamination
# ---------------------------------------------------------------------------


class TestComparisonFanoutConfigIsolation:
    def test_pipeline_run_does_not_mutate_caller_config_on_default_path(self):
        caller_config: dict[str, object] = {"INCLUDE_SOURCES": ""}
        snapshot = dict(caller_config)

        report = pipeline.run(
            topic="$NVDA stock",
            config=caller_config,
            depth="quick",
            requested_sources=["reddit"],
            mock=True,
            web_backend="none",
            as_of_date="2026-07-10",
        )

        assert report.topic == "$NVDA stock"
        assert caller_config == snapshot
        assert "_financial_topic" not in caller_config
        assert "_jobs_lane" not in caller_config
        assert "stocktwits" not in pipeline.available_sources(caller_config)

    def test_concurrent_comparison_fanout_isolates_configs_under_heavy_mutation(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
        monkeypatch.setenv("LAST30DAYS_NO_PATENT_DIGEST", "1")

        outer_config: dict[str, object] = {
            "INCLUDE_SOURCES": "",
            "EXCLUDE_SOURCES": "yahoo_finance",
            "_BROWSER_COOKIE_MODE": "off",
        }
        peers = ["PeerOne", "PeerTwo", "PeerThree", "PeerFour", "PeerFive"]
        barrier = threading.Barrier(1 + len(peers), timeout=10.0)
        seen_configs: dict[str, dict[str, object]] = {}
        seen_ids: dict[str, int] = {}
        lock = threading.Lock()

        def racing_pipeline_run(**kwargs):
            entity = kwargs["topic"]
            cfg = kwargs["config"]
            with lock:
                seen_configs[entity] = dict(cfg)
                seen_ids[entity] = id(cfg)
            # Synchronize all 6 worker threads so mutation and dict(cfg) overlap.
            barrier.wait()
            if entity == "$NVDA stock":
                for i in range(250):
                    cfg[f"_race_key_{i}"] = i
                    cfg["_financial_topic"] = True
                    cfg["_jobs_story_terms"] = ["nvda-only-term"]
            else:
                for _ in range(250):
                    _ = dict(cfg)
                    _ = list(cfg.items())
            return _sample_report(entity)

        with mock.patch.object(cli.env, "get_config", return_value=outer_config), \
             mock.patch.object(cli.pipeline, "diagnose", return_value={"available_sources": ["reddit"]}), \
             mock.patch.object(cli.pipeline, "run", side_effect=racing_pipeline_run), \
             mock.patch.object(cli, "_show_runtime_ui"), \
             mock.patch.object(cli, "_render_save_and_print", return_value=0) as render_mock:
            rc, _out, err = _run_cli([
                "$NVDA stock",
                "--competitors-list", ",".join(peers),
                "--mock",
                "--quick",
                "--amazon-query", "Nvidia GPU",
                "--devto-tags", "cuda,gpu",
                "--polymarket-keywords", "nvidia",
                "--dedicated-subreddits", "r/nvidia",
                "--jobs-board", "https://jobs.example.test/nvidia",
                "--company", "Nvidia",
            ])

        assert rc == 0, err
        # Every entity thread must have received a distinct config object.
        assert len(seen_ids) == 1 + len(peers)
        assert len(set(seen_ids.values())) == 1 + len(peers)
        assert id(outer_config) not in seen_ids.values()

        # All 6 entity reports survived without RuntimeError during iteration.
        entity_reports = render_mock.call_args.args[2]
        assert [label for label, _ in entity_reports] == ["$NVDA stock", *peers]

        # Main entity-specific transient keys must not contaminate any peer.
        for peer in peers:
            peer_cfg = seen_configs[peer]
            assert "_financial_topic" not in peer_cfg
            assert "_jobs_story_terms" not in peer_cfg
            assert "_company_signal" not in peer_cfg
            assert "_jobs_board" not in peer_cfg
            assert "_amazon_query" not in peer_cfg
            assert "_devto_tags" not in peer_cfg
            assert "_meta_ads_page" not in peer_cfg
            assert "_dedicated_subreddits" not in peer_cfg
            assert "_polymarket_keywords" not in peer_cfg
            assert "_polymarket_hints" not in peer_cfg
            assert "_auto_resolve_context" not in peer_cfg
            assert not any(k.startswith("_race_key_") for k in peer_cfg)
            assert peer_cfg["_company"]["entity"] == peer

        # Outer config must remain untouched by worker mutations.
        assert "_financial_topic" not in outer_config
        assert "_jobs_story_terms" not in outer_config
        assert not any(k.startswith("_race_key_") for k in outer_config)


# ---------------------------------------------------------------------------
# HIGH-006: Read-only --preflight and --diagnose never bypassed by subcommands
# ---------------------------------------------------------------------------


class TestReadOnlyPreflightAndDiagnoseGuard:
    @pytest.mark.parametrize("flag", ["--preflight", "--diagnose"])
    def test_store_key_rejected_before_reading_stdin_or_writing_env(
        self, tmp_path: Path, flag: str,
    ):
        env_path = tmp_path / ".env"
        stdin_read = mock.Mock(side_effect=AssertionError("stdin must not be read"))

        class _GuardStdin:
            readline = stdin_read

        with mock.patch.object(cli.env, "CONFIG_FILE", env_path), \
             mock.patch.object(cli.env, "get_config", side_effect=AssertionError("must not load config")), \
             mock.patch("lib.setup_wizard.write_api_key", side_effect=AssertionError("must not write key")), \
             mock.patch.object(sys, "stdin", _GuardStdin()):
            rc, out, err = _run_cli(["setup", "--store-key", "XAI_API_KEY", flag])

        assert rc == 2
        assert out == ""
        assert f"{flag} cannot be combined with the 'setup' command" in err
        assert not env_path.exists()
        stdin_read.assert_not_called()

    @pytest.mark.parametrize("flag", ["--preflight", "--diagnose"])
    @pytest.mark.parametrize(
        "cmd_argv",
        [
            ["setup"],
            ["setup", "--allow-browser-cookies"],
            ["setup", "--openclaw"],
            ["setup", "--github-start"],
            ["setup", "--reddit-session"],
            ["setup", "--install-hooks"],
            ["library", "feed"],
            ["library", "feed", "--publish"],
            ["library", "harvest"],
            ["library", "search", "MCP"],
            ["queue", "cover", "Agent memory"],
            ["queue", "list"],
            ["doctor"],
        ],
    )
    def test_admin_subcommands_rejected_with_preflight_or_diagnose(
        self, flag: str, cmd_argv: list[str],
    ):
        with mock.patch.object(cli.env, "get_config", side_effect=AssertionError("must not load config")), \
             mock.patch("lib.setup_wizard.run_auto_setup", side_effect=AssertionError("setup must not run")), \
             mock.patch("lib.setup_wizard.write_setup_config", side_effect=AssertionError("setup must not write")), \
             mock.patch("lib.setup_wizard.run_openclaw_setup", side_effect=AssertionError("openclaw must not run")), \
             mock.patch("lib.hook_installer.install_hooks", side_effect=AssertionError("hooks must not install")), \
             mock.patch.object(cli, "_run_library_feed", side_effect=AssertionError("library feed must not run")), \
             mock.patch.object(cli, "_run_library_harvest", side_effect=AssertionError("library harvest must not run")), \
             mock.patch.object(cli, "_run_library_search", side_effect=AssertionError("library search must not run")), \
             mock.patch.object(cli, "_run_queue_cover", side_effect=AssertionError("queue cover must not run")), \
             mock.patch.object(cli, "_run_queue_list", side_effect=AssertionError("queue list must not run")), \
             mock.patch("lib.doctor.run", side_effect=AssertionError("doctor must not run")):
            rc, out, err = _run_cli([*cmd_argv, flag])

        assert rc == 2
        assert out == ""
        assert f"{flag} cannot be combined" in err

    @pytest.mark.parametrize("mode_args", [["--diagnose"], ["--preflight", "--emit=json"]])
    @pytest.mark.parametrize(
        "base_argv",
        [
            ["--discover"],
            ["--discover", "AI agents"],
            ["--discover", "--nominate-only"],
            ["--drill", "cluster 1"],
            ["--verify-freshness"],
        ],
    )
    def test_diagnose_and_preflight_not_bypassed_by_discover_drill_or_cached_freshness(
        self, base_argv: list[str], mode_args: list[str],
    ):
        diag_payload = {
            "available_sources": ["reddit"],
            "providers": {"google": False, "openai": False, "xai": False, "openrouter": False, "perplexity": False},
            "has_scrapecreators": False,
            "has_github": False,
            "safe": True,
            "config_source": "env_only",
            "ignored_project_config": None,
            "ignored_project_config_keys": [],
            "ignored_endpoint_overrides": [],
            "browser_cookies": {"mode": "plan_only", "browsers": [], "reads_values": False},
            "external_commands": {"yt-dlp": False, "digg-pp-cli": False, "gh": False},
            "credential_destinations": {"global_env": None},
            "local_writes": [],
            "native_search": False,
        }
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(cli.pipeline, "diagnose", return_value=diag_payload) as diag_mock, \
             mock.patch.object(cli, "_run_discover", side_effect=AssertionError("discover must not run")), \
             mock.patch.object(cli, "_run_discover_protocol_leg", side_effect=AssertionError("protocol must not run")), \
             mock.patch.object(cli, "_run_drill", side_effect=AssertionError("drill must not run")), \
             mock.patch.object(cli, "_run_cached_freshness", side_effect=AssertionError("freshness must not run")):
            rc, out, err = _run_cli([*base_argv, *mode_args])

        assert rc == 0, err
        diag_mock.assert_called_once()
        parsed = json.loads(out)
        if mode_args[0] == "--diagnose":
            assert parsed["available_sources"] == ["reddit"]
        else:
            assert "local_reads" in parsed
            assert parsed["safe"] is True

    @pytest.mark.parametrize(
        ("argv", "expected_key"),
        [
            (["--research-only", "setup", "--diagnose"], "available_sources"),
            (["--research-only", "library", "feed", "--preflight", "--emit=json"], "local_reads"),
        ],
    )
    def test_research_only_allows_inspecting_admin_named_topics(
        self, argv: list[str], expected_key: str,
    ):
        diag_payload = {
            "available_sources": ["reddit"],
            "providers": {"google": False, "openai": False, "xai": False, "openrouter": False, "perplexity": False},
            "has_scrapecreators": False,
            "has_github": False,
            "safe": True,
            "config_source": "env_only",
            "ignored_project_config": None,
            "ignored_project_config_keys": [],
            "ignored_endpoint_overrides": [],
            "browser_cookies": {"mode": "plan_only", "browsers": [], "reads_values": False},
            "external_commands": {"yt-dlp": False, "digg-pp-cli": False, "gh": False},
            "credential_destinations": {"global_env": None},
            "local_writes": [],
            "native_search": False,
        }
        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(cli.pipeline, "diagnose", return_value=diag_payload):
            rc, out, err = _run_cli(argv)

        assert rc == 0, err
        assert expected_key in json.loads(out)


# ---------------------------------------------------------------------------
# HIGH-007: Cache-only --verify-freshness --publish-html guard & --synthesis-file
# ---------------------------------------------------------------------------


class TestCachedFreshnessPublishAndSynthesisContract:
    @pytest.mark.parametrize("emit_args", [[], ["--emit=compact"], ["--emit=md"], ["--emit=json"]])
    def test_cached_freshness_publish_html_requires_html_emit_before_dispatch(
        self, tmp_path: Path, monkeypatch, emit_args: list[str],
    ):
        monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        cache_path = tmp_path / "last-report.json"
        before = cache_path.read_bytes()

        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(cli, "_run_cached_freshness", side_effect=AssertionError("must not dispatch")), \
             mock.patch.object(html_publish, "publish_html", side_effect=AssertionError("must not publish")):
            rc, out, err = _run_cli(["--verify-freshness", "--publish-html", *emit_args])

        assert rc == 2
        assert out == ""
        assert "--publish-html requires --emit=html" in err
        assert cache_path.read_bytes() == before

    def test_run_cached_freshness_direct_rejects_publish_html_without_html_emit(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        cache_path = tmp_path / "last-report.json"
        before = cache_path.read_bytes()
        args = cli.build_parser().parse_args(["--verify-freshness", "--publish-html"])

        stderr = io.StringIO()
        with mock.patch.object(html_publish, "publish_html", side_effect=AssertionError("must not publish")), \
             redirect_stderr(stderr):
            rc = cli._run_cached_freshness(args, {})

        assert rc == 2
        assert "--publish-html requires --emit=html" in stderr.getvalue()
        assert cache_path.read_bytes() == before

    def test_cached_freshness_honors_synthesis_file_for_html_and_publish(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        synthesis_path = tmp_path / "synthesis.md"
        synthesis_path.write_text(
            "# Verified Briefing\n\nCustom synthesis paragraph for freshness HTML export.\n",
            encoding="utf-8",
        )
        published_docs: list[str] = []

        def fake_publish(doc: str, *, password=None):
            published_docs.append(doc)
            return {"url": "https://verified.ht-ml.app", "site_id": "s1", "status": "active"}

        with mock.patch.object(cli.env, "get_config", return_value={}), \
             mock.patch.object(html_publish, "publish_html", side_effect=fake_publish):
            rc, out, err = _run_cli([
                "--verify-freshness",
                "--mock",
                "--emit=html",
                "--synthesis-file", str(synthesis_path),
                "--publish-html",
            ])

        assert rc == 0, err
        assert "Custom synthesis paragraph for freshness HTML export." in out
        assert "<html" in out.lower()
        assert len(published_docs) == 1
        assert "<html" in published_docs[0].lower()
        assert "Custom synthesis paragraph for freshness HTML export." in published_docs[0]
        assert "Published HTML to https://verified.ht-ml.app" in err
        payload = json.loads((tmp_path / "last-report.json").read_text(encoding="utf-8"))
        assert payload["reports"][0]["report"]["freshness_verdicts"][0]["verdict"] == "unsupported"

    def test_cached_freshness_warns_and_ignores_synthesis_file_when_emit_not_html(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        synthesis_path = tmp_path / "synthesis.md"
        synthesis_path.write_text("# Ignored synthesis", encoding="utf-8")

        with mock.patch.object(cli.env, "get_config", return_value={}):
            rc, out, err = _run_cli([
                "--verify-freshness",
                "--mock",
                "--emit=compact",
                "--synthesis-file", str(synthesis_path),
            ])

        assert rc == 0, err
        assert "--synthesis-file is only used with --emit=html; ignoring." in err
        assert "Ignored synthesis" not in out

    def test_cached_freshness_missing_synthesis_file_exits_2_without_mutating_cache(
        self, tmp_path: Path, monkeypatch,
    ):
        monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
        assert cli._write_last_run("OpenClaw", _sample_report("OpenClaw"))
        cache_path = tmp_path / "last-report.json"
        before = cache_path.read_bytes()

        with mock.patch.object(cli.env, "get_config", return_value={}):
            rc, _out, err = _run_cli([
                "--verify-freshness",
                "--mock",
                "--emit=html",
                "--synthesis-file", str(tmp_path / "does-not-exist.md"),
            ])

        assert rc == 2
        assert "Cannot read --synthesis-file" in err
        assert cache_path.read_bytes() == before
