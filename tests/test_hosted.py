"""Tests for the remote API path (LAST30DAYS_API_KEY + LAST30DAYS_API_BASE).

Fixtures mirror the remote API contract exactly:
  POST {base}/search  {"query","depth"} -> {"search_id","status"} | clarify payload
  GET  {base}/search?id=<uuid>          -> pending|running|complete|error rows
  401 {"error"} / 402 {"error","requires_credits","balance","needed"} / 429 {"error"}
The endpoint is driven entirely through LAST30DAYS_API_BASE; there is no
built-in default. All keys/hosts in tests are obvious dummy values (see
AGENTS.md security hygiene).
"""

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import last30days as cli
from lib import hosted, http, schema

TEST_KEY = "sk_live_DUMMY_TEST_KEY_00000"
# Neutral placeholder endpoint - no product host. Ends in /api/v1 to mirror the
# API-version-root convention the billing-link derivation relies on.
TEST_BASE = "https://api.example.test/api/v1"
SEARCH_ID = "3f6c1c2e-9f6a-4a55-8f8a-2d1a9b8c7d6e"

SUBMIT_OK = {"search_id": SEARCH_ID, "status": "running"}
POLL_RUNNING = {
    "id": SEARCH_ID,
    "status": "running",
    "stderr": (
        "[narrate] step=planning queries\n"
        "[Reddit] fetched 12 threads\n"
        "[narrate] step=searching sources\n"
    ),
    "eta_ms": 45000,
}
POLL_COMPLETE = {
    "id": SEARCH_ID,
    "status": "complete",
    "synthesis_text": "## What happened\nSynthesized report body.",
    "raw_markdown": "# Raw markdown\nFull dump.",
}
CLARIFY_RESPONSE = {
    "needs_clarification": True,
    "clarify_class": "ambiguous_entity",
    "question": "Which 'mercury' do you mean?",
    "options": ["Mercury the planet", "Mercury the element", "Mercury the band"],
    "original_query": "mercury",
}

DIAG = {
    "available_sources": ["grounding"],
    "providers": {"google": True, "openai": False, "xai": False},
    "x_backend": None,
    "bird_installed": False,
    "bird_authenticated": False,
    "bird_username": None,
    "native_web_backend": "brave",
}


def make_report(topic: str = "test topic") -> schema.Report:
    return schema.Report(
        topic=topic,
        range_from="2026-06-03",
        range_to="2026-07-03",
        generated_at="2026-07-03T00:00:00+00:00",
        provider_runtime=schema.ProviderRuntime(
            reasoning_provider="gemini",
            planner_model="gemini-3.1-flash-lite",
            rerank_model="gemini-3.1-flash-lite",
        ),
        query_plan=schema.QueryPlan(
            intent="overview",
            freshness_mode="balanced_recent",
            cluster_mode="themes",
            raw_topic=topic,
            subqueries=[
                schema.SubQuery(
                    label="primary",
                    search_query=topic.lower(),
                    ranking_query=f"What happened with {topic}?",
                    sources=["grounding"],
                )
            ],
            source_weights={"grounding": 1.0},
        ),
        clusters=[],
        ranked_candidates=[],
        items_by_source={"grounding": []},
        errors_by_source={},
    )


def run_main(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    with mock.patch.object(sys, "argv", ["last30days.py", *argv]):
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli.main()
    return rc, stdout.getvalue(), stderr.getvalue()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LAST30DAYS_API_KEY", raising=False)
    monkeypatch.delenv("LAST30DAYS_API_BASE", raising=False)
    monkeypatch.delenv("LAST30DAYS_MEMORY_DIR", raising=False)


# ---------------------------------------------------------------------------
# Mode selection in the CLI entrypoint
# ---------------------------------------------------------------------------


def test_env_unset_runs_local_path_with_no_gateway_http(monkeypatch):
    """Without LAST30DAYS_API_KEY the local engine runs; the remote client and
    the API are never touched."""

    def no_hosted(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("remote path must not run when env is unset")

    def no_http(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError(f"unexpected HTTP call in local test: {args}")

    monkeypatch.setattr(hosted, "run_hosted", no_hosted)
    monkeypatch.setattr(http, "request", no_http)

    fake_progress = mock.Mock()
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=DIAG), \
         mock.patch.object(cli.pipeline, "run", return_value=make_report()) as pipeline_run, \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=fake_progress), \
         mock.patch.object(cli, "emit_output", return_value="# local rendered"):
        rc, out, _err = run_main(["test", "topic"])

    assert rc == 0
    pipeline_run.assert_called_once()
    assert "# local rendered" in out


def test_env_set_routes_to_remote_path(monkeypatch):
    # Both vars set -> remote path (KTD-2: key alone no longer activates it).
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    calls = []

    def fake_run_hosted(topic, depth, *, emit, save_dir, save_suffix):
        calls.append({"topic": topic, "depth": depth, "emit": emit,
                      "save_dir": save_dir, "save_suffix": save_suffix})
        return 0

    monkeypatch.setattr(hosted, "run_hosted", fake_run_hosted)
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "run",
                           side_effect=AssertionError("local pipeline must not run")):
        rc, _out, _err = run_main(["test", "topic"])

    assert rc == 0
    assert calls == [{
        "topic": "test topic",
        "depth": "default",
        "emit": "compact",
        "save_dir": None,
        "save_suffix": "",
    }]


def test_register_is_forwarded_to_remote_backend(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    calls = []

    monkeypatch.setattr(
        hosted,
        "run_hosted",
        lambda topic, depth, **kwargs: calls.append((topic, depth, kwargs)) or 0,
    )
    with mock.patch.object(cli.env, "get_config", return_value={}):
        rc, _out, _err = run_main(
            ["test", "topic", "--register=creator"]
        )

    assert rc == 0
    assert calls == [
        (
            "test topic",
            "default",
            {
                "emit": "compact",
                "save_dir": None,
                "save_suffix": "",
                "register": "creator",
            },
        )
    ]


def test_hosted_submit_adds_only_nondefault_register(remote_env, monkeypatch):
    payloads = []
    monkeypatch.setattr(
        hosted.http,
        "post",
        lambda _url, *, json_data, **_kwargs: payloads.append(json_data) or SUBMIT_OK,
    )

    hosted.submit("test topic", "quick")
    hosted.submit("test topic", "quick", register="dev")

    assert payloads == [
        {"query": "test topic", "depth": "quick"},
        {"query": "test topic", "depth": "quick", "register": "dev"},
    ]


def test_remote_json_requires_raw_profile(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    monkeypatch.setattr(
        hosted,
        "run_hosted",
        lambda *args, **kwargs: pytest.fail("remote request should not start"),
    )

    with mock.patch.object(cli.env, "get_config", return_value={}):
        rc, _out, err = run_main(["test", "topic", "--emit=json"])

    assert rc == 2
    assert "remote API backend only supports --json-profile=raw" in err


def test_remote_raw_json_preserves_existing_server_contract(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    calls = []
    monkeypatch.setattr(
        hosted,
        "run_hosted",
        lambda topic, depth, **kwargs: calls.append((topic, depth, kwargs)) or 0,
    )

    with mock.patch.object(cli.env, "get_config", return_value={}):
        rc, _out, _err = run_main(
            ["test", "topic", "--emit=json", "--json-profile=raw"]
        )

    assert rc == 0
    assert calls == [
        (
            "test topic",
            "default",
            {"emit": "json", "save_dir": None, "save_suffix": ""},
        )
    ]


@pytest.mark.parametrize(
    ("flag", "expected_depth"),
    [(["--quick"], "quick"), ([], "default"), (["--deep"], "deep")],
)
def test_depth_mapping(monkeypatch, flag, expected_depth):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    depths = []
    monkeypatch.setattr(
        hosted, "run_hosted",
        lambda topic, depth, **kwargs: depths.append(depth) or 0,
    )
    with mock.patch.object(cli.env, "get_config", return_value={}):
        rc, _out, _err = run_main(["test", "topic", *flag])
    assert rc == 0
    assert depths == [expected_depth]


def test_mock_flag_stays_local_even_with_key(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)

    def no_hosted(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("remote path must not run with --mock")

    monkeypatch.setattr(hosted, "run_hosted", no_hosted)
    fake_progress = mock.Mock()
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=DIAG), \
         mock.patch.object(cli.pipeline, "run", return_value=make_report()) as pipeline_run, \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=fake_progress), \
         mock.patch.object(cli, "emit_output", return_value="# local rendered"):
        rc, _out, _err = run_main(["test", "topic", "--mock"])
    assert rc == 0
    pipeline_run.assert_called_once()


def test_key_set_but_base_unset_stays_local(monkeypatch):
    """KTD-2 inertness: with only the key set (no LAST30DAYS_API_BASE), hosted
    mode does not activate - the local engine runs and no HTTP is attempted.
    This is the leak-proofing guarantee: a key alone can never phone anywhere."""
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    # LAST30DAYS_API_BASE deliberately left unset by the _clean_env fixture.

    def no_hosted(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError("remote path must not run without LAST30DAYS_API_BASE")

    def no_http(*args, **kwargs):  # pragma: no cover - failure path
        raise AssertionError(f"unexpected HTTP call when base is unset: {args}")

    monkeypatch.setattr(hosted, "run_hosted", no_hosted)
    monkeypatch.setattr(http, "request", no_http)

    fake_progress = mock.Mock()
    with mock.patch.object(cli.env, "get_config", return_value={}), \
         mock.patch.object(cli.pipeline, "diagnose", return_value=DIAG), \
         mock.patch.object(cli.pipeline, "run", return_value=make_report()) as pipeline_run, \
         mock.patch.object(cli.ui, "ProgressDisplay", return_value=fake_progress), \
         mock.patch.object(cli, "emit_output", return_value="# local rendered"):
        rc, out, _err = run_main(["test", "topic"])

    assert rc == 0
    pipeline_run.assert_called_once()
    assert "# local rendered" in out


# ---------------------------------------------------------------------------
# Remote client: submit -> poll -> complete
# ---------------------------------------------------------------------------


@pytest.fixture()
def remote_env(monkeypatch):
    monkeypatch.setenv("LAST30DAYS_API_KEY", TEST_KEY)
    monkeypatch.setenv("LAST30DAYS_API_BASE", TEST_BASE)
    monkeypatch.setattr(hosted.time, "sleep", lambda _s: None)


@pytest.mark.parametrize(
    ("flags", "reported_flag"),
    [
        (["--days=30"], "--days"),
        (["--lookback-days=7"], "--lookback-days"),
        (["--as-of=2026-05-13"], "--as-of"),
        (["--search=reddit"], "--search"),
        (["--hiring-signals"], "--hiring-signals"),
        (["--company=Widget Co"], "--company"),
        (["--ticker=WDGT:Widget Co"], "--ticker"),
        (["--jobs-board=https://jobs.example.test/widget"], "--jobs-board"),
        (["--x-handle=widget"], "--x-handle"),
        (["--plan", json.dumps(schema.to_dict(make_report("widget").query_plan))], "--plan"),
        (["--competitors"], "--competitors"),
        (["--competitors-list=Peer A,Peer B"], "--competitors-list"),
        (["--competitors-plan", "{}"], "--competitors-plan"),
        (["--emit=html"], "--emit=html"),
        (["--x-handle=widget", "--search=reddit"], "--x-handle, --search"),
    ],
)
def test_hosted_cli_rejects_unapplied_options_before_submitting(
    remote_env, monkeypatch, flags, reported_flag,
):
    post = mock.Mock(return_value=SUBMIT_OK)
    get = mock.Mock(return_value=POLL_COMPLETE)
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(hosted.http, "post", post)
    monkeypatch.setattr(hosted.http, "get", get)

    rc, out, err = run_main(["widget", *flags])

    post.assert_not_called()
    get.assert_not_called()
    assert rc == 2
    assert out == ""
    assert reported_flag in err
    assert "hosted backend" in err


@pytest.mark.parametrize("form", [("--as-of=2026-11-13",), ("--as-of", "2026-11-14")])
def test_hosted_cli_rejects_explicit_today_before_submitting(remote_env, monkeypatch, form):
    local_now = datetime(2026, 11, 13, 17, tzinfo=timezone(timedelta(hours=-8)))
    monkeypatch.setattr(cli.dates, "_local_now", lambda: local_now)
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    post = mock.Mock(return_value=SUBMIT_OK)
    monkeypatch.setattr(hosted.http, "post", post)
    monkeypatch.setattr(hosted.http, "get", mock.Mock(return_value=POLL_COMPLETE))

    rc, out, err = run_main(["Nvidia", *form])

    post.assert_not_called()
    assert rc == 2
    assert out == ""
    assert "--as-of" in err
    assert "hosted backend" in err


@pytest.mark.parametrize("topic", ["Nvidia vs AMD", "Nvidia compared to AMD"])
def test_hosted_cli_rejects_comparison_before_submitting(remote_env, monkeypatch, topic):
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    post = mock.Mock(return_value=SUBMIT_OK)
    monkeypatch.setattr(hosted.http, "post", post)
    monkeypatch.setattr(hosted.http, "get", mock.Mock(return_value=POLL_COMPLETE))

    rc, out, err = run_main([topic])

    post.assert_not_called()
    assert rc == 2
    assert out == ""
    assert "hosted backend cannot run comparisons" in err
    assert "Run locally" in err


def test_hosted_cli_submits_supported_query_once(remote_env, monkeypatch, tmp_path):
    save_dir = tmp_path / "saved"
    post = mock.Mock(return_value=SUBMIT_OK)
    get = mock.Mock(return_value=POLL_COMPLETE)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    monkeypatch.setattr(hosted.http, "post", post)
    monkeypatch.setattr(hosted.http, "get", get)

    rc, out, _err = run_main([
        "Nvidia", "--register=exec", "--save-dir", str(save_dir),
    ])

    assert rc == 0
    assert "Synthesized report body." in out
    post.assert_called_once()
    assert post.call_args.kwargs["json_data"] == {
        "query": "Nvidia", "depth": "default", "register": "exec",
    }
    get.assert_called_once()
    assert (save_dir / "nvidia-raw.md").read_text(encoding="utf-8") == POLL_COMPLETE["raw_markdown"]


@pytest.mark.parametrize(
    ("flags", "config", "backend"),
    [
        ([], {}, "hosted"),
        (["--mock"], {}, "local"),
        (["--deep-research"], {}, "local"),
        (["--corpus=/local-notes"], {}, "local"),
        ([], {"LAST30DAYS_CORPUS_DIRS": "/local-notes"}, "local"),
        (["--perplexity-search-type=fast"], {}, "hosted"),
        (["--perplexity-search-type=fast"], {"PERPLEXITY_API_KEY": "dummy-perplexity"}, "local"),
    ],
)
def test_diagnose_identifies_research_backend(remote_env, monkeypatch, flags, config, backend):
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: dict(config))
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *args, **kwargs: {"available_sources": []})
    post = mock.Mock()
    monkeypatch.setattr(hosted.http, "post", post)

    rc, out, _err = run_main(["--diagnose", "--include-research-backend", *flags])

    assert rc == 0
    assert json.loads(out) == {"available_sources": [], "research_backend": backend}
    post.assert_not_called()


def test_include_research_backend_requires_diagnose(remote_env, monkeypatch, capsys):
    post = mock.Mock()
    monkeypatch.setattr(hosted.http, "post", post)
    monkeypatch.setattr(sys, "argv", ["last30days.py", "Nvidia", "--include-research-backend"])

    with pytest.raises(SystemExit) as error:
        cli.main()

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert captured.out == ""
    assert "--include-research-backend requires --diagnose" in captured.err
    post.assert_not_called()


def test_happy_path_submit_poll_complete(remote_env, monkeypatch, capsys):
    posts, gets = [], []

    def fake_post(url, json_data, headers=None, **kwargs):
        posts.append({"url": url, "json": json_data, "headers": headers})
        return dict(SUBMIT_OK)

    poll_rows = [dict(POLL_RUNNING), dict(POLL_RUNNING), dict(POLL_COMPLETE)]

    def fake_get(url, headers=None, params=None, **kwargs):
        gets.append({"url": url, "headers": headers, "params": params})
        return poll_rows.pop(0)

    monkeypatch.setattr(hosted.http, "post", fake_post)
    monkeypatch.setattr(hosted.http, "get", fake_get)

    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    out = capsys.readouterr().out

    assert rc == 0
    # Contract: submit
    assert posts == [{
        "url": f"{TEST_BASE}/search",
        "json": {"query": "test topic", "depth": "default"},
        "headers": {"Authorization": f"Bearer {TEST_KEY}"},
    }]
    # Contract: poll same auth, id param
    assert all(g["url"] == f"{TEST_BASE}/search" for g in gets)
    assert all(g["params"] == {"id": SEARCH_ID} for g in gets)
    assert all(g["headers"] == {"Authorization": f"Bearer {TEST_KEY}"} for g in gets)
    assert len(gets) == 3
    # Synthesis rendered on stdout
    assert "Synthesized report body." in out


def test_happy_path_narration_printed_once_and_no_key_echo(remote_env, monkeypatch, capsys):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    poll_rows = [dict(POLL_RUNNING), dict(POLL_RUNNING), dict(POLL_COMPLETE)]
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: poll_rows.pop(0))

    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()

    assert rc == 0
    # Each narration step printed exactly once even though the stderr blob
    # was returned twice by consecutive polls.
    assert captured.err.count("[narrate] step=planning queries") == 1
    assert captured.err.count("[narrate] step=searching sources") == 1
    # Progress line with elapsed/eta shape
    assert "eta" in captured.err
    # The API key never appears anywhere in output.
    assert TEST_KEY not in captured.out
    assert TEST_KEY not in captured.err


def test_save_dir_writes_raw_markdown(remote_env, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    poll_rows = [dict(POLL_COMPLETE)]
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: poll_rows.pop(0))

    rc = hosted.run_hosted("Test Topic!", "default", emit="compact",
                           save_dir=str(tmp_path), save_suffix="")
    captured = capsys.readouterr()

    assert rc == 0
    saved = tmp_path / "test-topic-raw.md"
    assert saved.exists()
    assert "# Raw markdown" in saved.read_text(encoding="utf-8")
    assert "Saved output to" in captured.err
    assert TEST_KEY not in captured.err


def test_save_dir_uses_unique_dated_fallback(remote_env, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    poll_rows = [dict(POLL_COMPLETE)]
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: poll_rows.pop(0))
    today = datetime.now().strftime("%Y-%m-%d")
    base = tmp_path / "test-topic-raw.md"
    dated = tmp_path / f"test-topic-raw-{today}.md"
    base.write_text("base content", encoding="utf-8")
    dated.write_text("dated content", encoding="utf-8")

    rc = hosted.run_hosted("Test Topic!", "default", emit="compact",
                           save_dir=str(tmp_path), save_suffix="")
    captured = capsys.readouterr()

    saved = tmp_path / f"test-topic-raw-{today}-1.md"
    assert rc == 0
    assert saved.exists()
    assert "# Raw markdown" in saved.read_text(encoding="utf-8")
    assert base.read_text(encoding="utf-8") == "base content"
    assert dated.read_text(encoding="utf-8") == "dated content"
    assert f"Saved output to {saved.resolve()}" in captured.err
    assert TEST_KEY not in captured.err


# ---------------------------------------------------------------------------
# Save-suffix sanitization: --save-suffix must never shape the path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("suffix", "expected_name"),
    [
        pytest.param("../../evil", "test-topic-raw-evil.md", id="parent-ref"),
        pytest.param("/var/tmp/evil", "test-topic-raw-var-tmp-evil.md", id="abs-path"),
        pytest.param("Report V3", "test-topic-raw-report-v3.md", id="mixed-case"),
        pytest.param("GEMINI-DEEP", "test-topic-raw-gemini-deep.md", id="upper"),
        pytest.param("", "test-topic-raw.md", id="empty"),
        pytest.param("...?!..", "test-topic-raw.md", id="punct-only-drops"),
        pytest.param("-edge-", "test-topic-raw-edge.md", id="edge-hyphens"),
    ],
)
def test_save_suffix_sanitized_stays_in_save_dir(
    remote_env, monkeypatch, capsys, tmp_path, suffix, expected_name
):
    """Whatever --save-suffix holds, the saved file lands directly under the
    requested save dir with a [a-z0-9-] suffix token (or none at all)."""
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: dict(POLL_COMPLETE))

    rc = hosted.run_hosted("Test Topic!", "default", emit="compact",
                           save_dir=str(tmp_path), save_suffix=suffix)
    captured = capsys.readouterr()

    assert rc == 0
    assert [saved_path.name for saved_path in tmp_path.iterdir()] == [expected_name]
    saved = tmp_path / expected_name
    # Directly under the requested save directory - no separators, no `..`.
    assert saved.is_file()
    assert saved.resolve().parent == tmp_path.resolve()
    assert "# Raw markdown" in saved.read_text(encoding="utf-8")
    assert f"Saved output to {saved.resolve()}" in captured.err
    assert TEST_KEY not in captured.err


def test_save_suffix_collision_falls_back_to_dated_sanitized_name(
    remote_env, monkeypatch, capsys, tmp_path
):
    """Collision fallback still applies once the suffix is sanitized: hostile
    '../Report V3' -> '-report-v3', and pre-existing files keep their content."""
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: dict(POLL_COMPLETE))
    today = datetime.now().strftime("%Y-%m-%d")
    base = tmp_path / "test-topic-raw-report-v3.md"
    dated = tmp_path / f"test-topic-raw-report-v3-{today}.md"
    base.write_text("prior base", encoding="utf-8")
    dated.write_text("prior dated", encoding="utf-8")

    rc = hosted.run_hosted("Test Topic!", "default", emit="compact",
                           save_dir=str(tmp_path), save_suffix="../Report V3")
    captured = capsys.readouterr()

    saved = tmp_path / f"test-topic-raw-report-v3-{today}-1.md"
    assert rc == 0
    assert saved.exists()
    assert saved.resolve().parent == tmp_path.resolve()
    assert "# Raw markdown" in saved.read_text(encoding="utf-8")
    assert base.read_text(encoding="utf-8") == "prior base"
    assert dated.read_text(encoding="utf-8") == "prior dated"
    assert f"Saved output to {saved.resolve()}" in captured.err
    assert TEST_KEY not in captured.err


def test_api_base_override(remote_env, monkeypatch, capsys):
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://staging.example.dev/api/v1/")
    urls = []

    def fake_post(url, json_data, headers=None, **kwargs):
        urls.append(url)
        return dict(SUBMIT_OK)

    monkeypatch.setattr(hosted.http, "post", fake_post)
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: dict(POLL_COMPLETE))

    rc = hosted.run_hosted("test topic", "quick", emit="compact",
                           save_dir=None, save_suffix="")
    capsys.readouterr()
    assert rc == 0
    assert urls == ["https://staging.example.dev/api/v1/search"]


@pytest.mark.parametrize("base", [
    TEST_BASE,
    "http://localhost:8080/api/v1",
    "http://127.0.0.1:8080/api/v1",
    "http://[::1]:8080/api/v1",
])
def test_api_base_allows_https_and_loopback_http(remote_env, monkeypatch, base):
    monkeypatch.setenv("LAST30DAYS_API_BASE", base)
    with mock.patch.object(hosted.http, "post", return_value=SUBMIT_OK) as post, \
         mock.patch.object(hosted.http, "get", return_value=POLL_COMPLETE) as get:
        assert hosted.run_hosted("test topic", "quick") == 0

    assert post.call_args.args == (f"{base}/search",)
    assert get.call_args.args == (f"{base}/search",)
    assert post.call_args.kwargs["headers"] == {"Authorization": f"Bearer {TEST_KEY}"}
    assert get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {TEST_KEY}"}


@pytest.mark.parametrize("base", [
    "http://remote.example.test/api/v1",
    f"http://remote.example.test/{TEST_KEY}",
    "ftp://remote.example.test/api/v1",
])
def test_api_base_rejects_insecure_remote_without_request(
    remote_env, monkeypatch, capsys, base,
):
    monkeypatch.setenv("LAST30DAYS_API_BASE", base)
    with mock.patch.object(hosted.http, "post", return_value=SUBMIT_OK) as post, \
         mock.patch.object(hosted.http, "get", return_value=POLL_COMPLETE) as get:
        rc = hosted.run_hosted("test topic", "quick")

    captured = capsys.readouterr()
    post.assert_not_called()
    get.assert_not_called()
    assert rc == 1
    assert "LAST30DAYS_API_BASE" in captured.err
    assert "https://" in captured.err
    assert "loopback" in captured.err
    assert TEST_KEY not in captured.out + captured.err


@pytest.mark.parametrize("operation", ["submit", "poll"])
def test_direct_hosted_requests_reject_insecure_remote(remote_env, monkeypatch, operation):
    monkeypatch.setenv("LAST30DAYS_API_BASE", "http://remote.example.test/api/v1")
    with mock.patch.object(hosted.http, "post") as post, \
         mock.patch.object(hosted.http, "get") as get:
        with pytest.raises(ValueError, match="LAST30DAYS_API_BASE.*https://") as exc:
            if operation == "submit":
                hosted.submit("test topic", "quick")
            else:
                hosted.poll(SEARCH_ID)

    post.assert_not_called()
    get.assert_not_called()
    assert TEST_KEY not in str(exc.value)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_401_invalid_or_revoked_key(remote_env, monkeypatch, capsys):
    def fake_post(*a, **k):
        raise http.HTTPError("HTTP 401: Unauthorized", 401,
                             json.dumps({"error": "Invalid API key"}))

    monkeypatch.setattr(hosted.http, "post", fake_post)
    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 1
    assert "invalid or revoked" in captured.err.lower()
    assert TEST_KEY not in captured.err


def test_402_shows_balance_needed_and_billing_url(remote_env, monkeypatch, capsys):
    body = {"error": "Insufficient credits", "requires_credits": True,
            "balance": 40, "needed": 200}

    def fake_post(*a, **k):
        raise http.HTTPError("HTTP 402: Payment Required", 402, json.dumps(body))

    monkeypatch.setattr(hosted.http, "post", fake_post)
    rc = hosted.run_hosted("test topic", "deep", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 1
    # Balance and needed shown verbatim from the API response.
    assert "40" in captured.err
    assert "200" in captured.err
    # Billing link is derived from the configured base (base minus /api/v1,
    # plus /dashboard/billing) - never hardcoded.
    assert "https://api.example.test/dashboard/billing" in captured.err
    assert TEST_KEY not in captured.err


def test_429_rate_limited(remote_env, monkeypatch, capsys):
    def fake_post(*a, **k):
        raise http.HTTPError("HTTP 429: Too Many Requests", 429,
                             json.dumps({"error": "Rate limit exceeded"}))

    monkeypatch.setattr(hosted.http, "post", fake_post)
    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 1
    assert "rate limit" in captured.err.lower()


def test_clarify_response_prints_question_options_distinct_exit(remote_env, monkeypatch, capsys):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(CLARIFY_RESPONSE))

    def no_get(*a, **k):  # pragma: no cover - failure path
        raise AssertionError("must not poll on clarify")

    monkeypatch.setattr(hosted.http, "get", no_get)
    rc = hosted.run_hosted("mercury", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == hosted.EXIT_CLARIFY
    assert rc not in (0, 1)
    assert "Which 'mercury' do you mean?" in captured.err
    assert "Mercury the planet" in captured.err
    assert "Mercury the band" in captured.err
    assert "re-run" in captured.err.lower()


def test_error_status_run_prints_server_message(remote_env, monkeypatch, capsys):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    error_row = {"id": SEARCH_ID, "status": "error",
                 "error": "Synthesis failed upstream"}
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: dict(error_row))
    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 1
    assert "Synthesis failed upstream" in captured.err


def test_network_timeout_mid_poll_retries_get(remote_env, monkeypatch, capsys):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    attempts = []

    def flaky_get(*a, **k):
        attempts.append(1)
        if len(attempts) < 3:
            raise http.HTTPError("Connection error: TimeoutError: timed out")
        return dict(POLL_COMPLETE)

    monkeypatch.setattr(hosted.http, "get", flaky_get)
    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 0
    assert len(attempts) == 3
    assert "Synthesized report body." in captured.out


def test_persistent_network_failure_gives_up_with_message(remote_env, monkeypatch, capsys):
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))

    def dead_get(*a, **k):
        raise http.HTTPError("Connection error: TimeoutError: timed out")

    monkeypatch.setattr(hosted.http, "get", dead_get)
    rc = hosted.run_hosted("test topic", "default", emit="compact",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 1
    assert "poll" in captured.err.lower()
    assert TEST_KEY not in captured.err


def test_emit_json_prints_terminal_row_without_stderr(remote_env, monkeypatch, capsys, tmp_path):
    terminal = {
        **POLL_COMPLETE,
        "stderr": f"PRIVATE-STDERR-{tmp_path.name}",
        "internal_trace": f"PRIVATE-TRACE-{tmp_path.name}",
    }
    monkeypatch.setattr(hosted.http, "post", lambda *a, **k: dict(SUBMIT_OK))
    monkeypatch.setattr(hosted.http, "get", lambda *a, **k: dict(terminal))
    rc = hosted.run_hosted("test topic", "default", emit="json",
                           save_dir=None, save_suffix="")
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload == {
        "id": SEARCH_ID,
        "status": "complete",
        "synthesis_text": "## What happened\nSynthesized report body.",
        "raw_markdown": "# Raw markdown\nFull dump.",
    }
    assert terminal["stderr"] not in captured.out
    assert terminal["stderr"] not in captured.err
    assert terminal["internal_trace"] not in captured.out
    assert terminal["internal_trace"] not in captured.err
    assert TEST_KEY not in captured.out
