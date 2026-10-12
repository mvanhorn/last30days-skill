"""LAW 7 enforcement: an agent-hosted research run without --plan stops.

SKILL.md LAW 7 says the hosting reasoning model writes the query plan and
passes --plan. When the engine detects an agent host (Claude Code, Codex, or
an explicit LAST30DAYS_HOST_AGENT=1) and no plan was passed, it must stop
before retrieval with a clear message instead of silently spending on its
internal planner, unless the mode is exempt or LAST30DAYS_ALLOW_ENGINE_PLAN=1.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest

import last30days as cli
from lib import env, http, planner

DUMMY_CONFIG = {"OPENROUTER_API_KEY": "dummy-openrouter-key-000"}
DIAG = {
    "available_sources": ["reddit", "hackernews"],
    "providers": {"google": False, "openai": False, "xai": False},
    "x_backend": None,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": None,
    "permission_preflight": {},
}
TOPIC = ["best", "espresso", "grinders"]
VALID_PLAN = {
    "intent": "product",
    "freshness_mode": "balanced_recent",
    "cluster_mode": "market",
    "subqueries": [
        {
            "label": "primary",
            "search_query": "espresso grinder",
            "ranking_query": "Which espresso grinders do people recommend?",
            "sources": ["reddit", "hackernews"],
            "weight": 1.0,
        }
    ],
}


class _RetrievalReached(Exception):
    """pipeline.run was entered: the gate let the run through."""


def _parse(*argv: str):
    args, _extra = cli.build_parser().parse_known_args(list(argv))
    return args


def _run_main(monkeypatch, argv, *, host_env=None, config=None):
    """Drive cli.main() up to retrieval with every side effect stubbed.

    Returns (rc, stderr, mocks). rc is None when pipeline.run was reached.
    """
    for name in ("LAST30DAYS_API_KEY", "LAST30DAYS_API_BASE", "LAST30DAYS_MEMORY_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    for name, value in (host_env or {}).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv])

    run_mock = mock.Mock(side_effect=_RetrievalReached)
    diagnose_mock = mock.Mock(return_value=dict(DIAG))
    runtime_mock = mock.Mock(side_effect=AssertionError("provider resolved"))
    resolve_mock = mock.Mock(return_value={})
    err = io.StringIO()
    with mock.patch.object(cli.env, "get_config", return_value=dict(config or DUMMY_CONFIG)), \
         mock.patch.object(cli.pipeline, "diagnose", diagnose_mock), \
         mock.patch.object(cli.pipeline, "run", run_mock), \
         mock.patch("lib.providers.resolve_runtime", runtime_mock), \
         mock.patch("lib.resolve.auto_resolve", resolve_mock), \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=mock.Mock()), \
         redirect_stdout(io.StringIO()), redirect_stderr(err):
        try:
            rc = cli.main()
        except _RetrievalReached:
            rc = None
    return rc, err.getvalue(), {
        "diagnose": diagnose_mock, "run": run_mock,
        "runtime": runtime_mock, "resolve": resolve_mock,
    }


# ---------------------------------------------------------------------------
# Host detection helpers (lib/env.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", env.AGENT_HOST_ENV_VARS)
def test_each_runtime_marker_signals_an_agent_host(name):
    assert env.agent_host_signal({name: "1"}) == name
    assert env.agent_hosted_run({name: "anything"})


def test_explicit_host_agent_opt_in_signals_an_agent_host():
    assert env.agent_host_signal({"LAST30DAYS_HOST_AGENT": "1"}) == "LAST30DAYS_HOST_AGENT"
    assert not env.agent_hosted_run({"LAST30DAYS_HOST_AGENT": "0"})


def test_host_self_identification_signals_an_agent_host():
    assert env.agent_host_signal({"LAST30DAYS_HOST": "grok-bot"}) == "LAST30DAYS_HOST"


def test_no_markers_or_blank_markers_are_not_agent_hosted():
    assert env.agent_host_signal({}) == ""
    assert not env.agent_hosted_run({"CLAUDECODE": "", "CODEX_THREAD_ID": "  "})


def test_signal_names_the_variable_never_its_value():
    assert env.agent_host_signal({"CODEX_THREAD_ID": "thread-secret-000"}) == "CODEX_THREAD_ID"


@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("on", True), ("0", False), ("", False)])
def test_engine_plan_override_is_truthy_only(value, expected):
    assert env.engine_plan_allowed({"LAST30DAYS_ALLOW_ENGINE_PLAN": value}) is expected


def test_helpers_read_the_process_environment(monkeypatch):
    monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
    monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
    assert env.agent_host_signal() == "CODEX_SANDBOX"
    assert env.engine_plan_allowed()


# ---------------------------------------------------------------------------
# The gate on the research path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [*env.AGENT_HOST_ENV_VARS, "LAST30DAYS_HOST_AGENT"])
def test_agent_hosted_run_without_plan_stops_before_retrieval(monkeypatch, name):
    rc, stderr, mocks = _run_main(monkeypatch, TOPIC, host_env={name: "1"})

    assert rc == cli.LAW7_HOST_PLAN_EXIT != 0
    mocks["run"].assert_not_called()
    mocks["runtime"].assert_not_called()  # internal LLM planner never resolved
    mocks["resolve"].assert_not_called()
    assert "LAW 7" in stderr
    assert f"({name} is set)" in stderr
    assert "Step 0.75" in stderr
    assert "references/research-runbook.md" in stderr
    assert '--plan "$QUERY_PLAN_FILE"' in stderr
    assert "<<'PLAN_EOF'" in stderr
    assert "LAST30DAYS_ALLOW_ENGINE_PLAN=1" in stderr
    assert "dummy-openrouter-key-000" not in stderr


def test_entity_topic_stops_before_engine_auto_resolve(monkeypatch):
    # Entity-shaped topics switch on auto-resolve (a paid web search) when no
    # handle is given; the gate must come first.
    rc, _stderr, mocks = _run_main(
        monkeypatch, ["Peter", "Steinberger"], host_env={"CLAUDECODE": "1"}
    )
    assert rc == cli.LAW7_HOST_PLAN_EXIT
    mocks["resolve"].assert_not_called()
    mocks["run"].assert_not_called()


def test_agent_hosted_run_without_plan_stops_before_live_diagnostics(monkeypatch):
    rc, _stderr, mocks = _run_main(
        monkeypatch, TOPIC, host_env={"CLAUDECODE": "1"}
    )
    assert rc == cli.LAW7_HOST_PLAN_EXIT
    mocks["diagnose"].assert_not_called()


def test_agent_hosted_run_with_plan_reaches_retrieval(monkeypatch, tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(VALID_PLAN), encoding="utf-8")
    rc, stderr, mocks = _run_main(
        monkeypatch, [*TOPIC, "--plan", str(plan_file)], host_env={"CLAUDECODE": "1"}
    )
    assert rc is None
    assert mocks["run"].call_args.kwargs["external_plan"] == VALID_PLAN
    assert "LAW 7" not in stderr


def test_override_restores_engine_planning_under_an_agent(monkeypatch):
    rc, stderr, mocks = _run_main(
        monkeypatch,
        TOPIC,
        host_env={"CLAUDECODE": "1", "LAST30DAYS_ALLOW_ENGINE_PLAN": "1"},
    )
    assert rc is None
    assert mocks["run"].call_args.kwargs["external_plan"] is None
    assert "LAW 7" not in stderr


def test_non_agent_run_is_unchanged(monkeypatch):
    rc, stderr, mocks = _run_main(monkeypatch, TOPIC)
    assert rc is None
    assert mocks["run"].call_args.kwargs["external_plan"] is None
    assert "LAW 7" not in stderr


@pytest.mark.parametrize(
    "extra",
    [
        ["--mock"],
        ["--hiring-signals"],
        ["--auto-resolve"],
    ],
    ids=["mock", "hiring-signals", "auto-resolve"],
)
def test_exempt_research_modes_reach_retrieval_under_an_agent(monkeypatch, extra):
    rc, stderr, mocks = _run_main(monkeypatch, [*TOPIC, *extra], host_env={"CLAUDECODE": "1"})
    assert rc is None
    mocks["run"].assert_called_once()
    assert "LAW 7" not in stderr


@pytest.mark.parametrize(
    "argv",
    [
        ["OpenAI", "vs", "Anthropic"],
        ["OpenAI", "--competitors"],
        ["OpenAI", "--competitors-list", "Anthropic,Google"],
        ["OpenAI", "--competitors-plan", '{"Anthropic": {"x_handle": "AnthropicAI"}}'],
    ],
    ids=["vs-topic", "competitors", "competitors-list", "competitors-plan"],
)
def test_comparison_runs_are_exempt(monkeypatch, argv):
    # Peers never take a query plan (SKILL.md vs-mode invokes without --plan).
    monkeypatch.setenv("CLAUDECODE", "1")
    args = _parse(*argv)
    topic = " ".join(args.topic).strip()
    assert cli._law7_host_plan_gate(args, topic) is None


def test_gate_function_blocks_a_plain_topic(monkeypatch):
    monkeypatch.setenv("CODEX_THREAD_ID", "t")
    args = _parse(*TOPIC)
    err = io.StringIO()
    with redirect_stderr(err):
        assert cli._law7_host_plan_gate(args, " ".join(TOPIC)) == cli.LAW7_HOST_PLAN_EXIT
    assert "CODEX_THREAD_ID" in err.getvalue()


# ---------------------------------------------------------------------------
# Modes that dispatch before the research path never see the gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,target",
    [
        (["doctor"], "lib.doctor.run"),
        (["setup", "--openclaw"], "lib.setup_wizard.run_openclaw_setup"),
        (["library", "search", "espresso"], "last30days._run_library_search"),
        (["library", "feed"], "last30days._run_library_feed"),
        (["queue", "list"], "last30days._run_queue_list"),
        (["--discover"], "last30days._run_discover"),
        (["--discover", "ai", "--discover-shallow"], "last30days._run_discover"),
        (["--discover", "--nominate-only"], "last30days._run_discover_protocol_leg"),
        (["--discover", "--finalize"], "last30days._run_discover_protocol_leg"),
        (["--drill", "3"], "last30days._run_drill"),
        (["--verify-freshness"], "last30days._run_cached_freshness"),
    ],
    ids=[
        "doctor", "setup", "library-search", "library-feed", "queue-list",
        "discover", "discover-shallow", "nominate-only", "finalize", "drill",
        "verify-freshness-cached",
    ],
)
def test_non_research_commands_are_not_gated(monkeypatch, argv, target):
    with mock.patch(target, return_value={} if "setup_wizard" in target else 0) as dispatched:
        rc, stderr, mocks = _run_main(monkeypatch, argv, host_env={"CLAUDECODE": "1"})
    assert rc == 0
    dispatched.assert_called_once()
    mocks["run"].assert_not_called()
    assert "LAW 7" not in stderr


def test_diagnose_is_not_gated(monkeypatch):
    rc, stderr, mocks = _run_main(monkeypatch, [*TOPIC, "--diagnose"], host_env={"CLAUDECODE": "1"})
    assert rc == 0
    mocks["diagnose"].assert_called_once()
    assert mocks["diagnose"].call_args.kwargs["safe"] is True
    mocks["run"].assert_not_called()
    assert "LAW 7" not in stderr


def test_preflight_is_not_gated(monkeypatch):
    with mock.patch.object(cli.permission_preflight, "render_text", return_value="ok\n"):
        rc, stderr, _mocks = _run_main(monkeypatch, ["--preflight"], host_env={"CLAUDECODE": "1"})
    assert rc == 0
    assert "LAW 7" not in stderr


def test_hosted_api_path_is_not_gated(monkeypatch):
    # The remote API plans server-side and takes no --plan.
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example.test")
    monkeypatch.setattr(sys, "argv", ["last30days.py", *TOPIC])
    monkeypatch.setenv("CLAUDECODE", "1")
    err = io.StringIO()
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.env, "read_secret_env", return_value="dummy-hosted-key-000"), \
         mock.patch("lib.hosted.run_hosted", return_value=0) as hosted, \
         redirect_stdout(io.StringIO()), redirect_stderr(err):
        rc = cli.main()
    assert rc == 0
    hosted.assert_called_once()
    assert "LAW 7" not in err.getvalue()


# ---------------------------------------------------------------------------
# Unattended callers opt back into engine planning
# ---------------------------------------------------------------------------


def test_watchlist_subprocess_allows_engine_planning(monkeypatch):
    import watchlist

    monkeypatch.setenv("CLAUDECODE", "1")
    fake = mock.Mock(returncode=1, stdout="", stderr="boom")
    with mock.patch.object(watchlist, "store") as store, \
         mock.patch.object(watchlist.subprocess, "run", return_value=fake) as run:
        store.record_run.return_value = 1
        watchlist._run_topic({"id": 1, "name": "test topic", "search_queries": None})
    child_env = run.call_args.kwargs["env"]
    assert child_env["LAST30DAYS_ALLOW_ENGINE_PLAN"] == "1"
    assert child_env["CLAUDECODE"] == "1"  # rest of the environment passes through


# ---------------------------------------------------------------------------
# Non-agent path: a failed internal planner still prints the LAW 7 reminder
# ---------------------------------------------------------------------------


def _plan_with_failing_provider(*, internal_subrun: bool) -> tuple:
    provider = mock.Mock()
    provider.generate_json.side_effect = http.HTTPError(
        "HTTP 402: insufficient credits", status_code=402
    )
    err = io.StringIO()
    with redirect_stderr(err):
        plan = planner.plan_query(
            topic="best espresso grinders",
            available_sources=["reddit", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=provider,
            model="dummy-model",
            internal_subrun=internal_subrun,
        )
    return plan, err.getvalue()


def test_provider_failure_prints_law7_reminder():
    plan, stderr = _plan_with_failing_provider(internal_subrun=False)
    assert "LLM planning failed" in stderr
    assert "No --plan passed" in stderr
    assert "YOU ARE the planner" in stderr
    assert plan.subqueries
    assert any("fallback-plan" in note for note in plan.notes)


def test_provider_failure_in_internal_subrun_stays_quiet():
    plan, stderr = _plan_with_failing_provider(internal_subrun=True)
    assert "LLM planning failed" in stderr
    assert "No --plan passed" not in stderr
    assert plan.subqueries


def test_successful_provider_plan_prints_no_reminder():
    provider = mock.Mock()
    provider.generate_json.return_value = VALID_PLAN
    err = io.StringIO()
    with redirect_stderr(err):
        plan = planner.plan_query(
            topic="best espresso grinders",
            available_sources=["reddit", "hackernews"],
            requested_sources=None,
            depth="default",
            provider=provider,
            model="dummy-model",
        )
    assert plan.subqueries
    assert "No --plan passed" not in err.getvalue()
