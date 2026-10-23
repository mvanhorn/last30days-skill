import json
import sys
from unittest import mock

import pytest

import last30days as cli
from lib import hosted, setup_wizard
from tests.test_last_run_state import _diag, _report
from tests.competitor_cli_audit_helpers import run_competitor_cli


@pytest.mark.parametrize("topic", [
    "setup", "doctor", "library feed", "library search example", "queue list",
    "queue cover example", "ordinary research", "--mock",
])
def test_research_only_dispatches_reserved_topics_as_research(monkeypatch, topic, capsys):
    research = mock.Mock(side_effect=lambda **kwargs: _report(kwargs["topic"]))
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: _diag())
    for name in ("_run_library_feed", "_run_library_search", "_run_queue_list", "_run_queue_cover"):
        monkeypatch.setattr(cli, name, mock.Mock(side_effect=AssertionError("administrative dispatch")))
    monkeypatch.setattr(setup_wizard, "run_auto_setup", mock.Mock(side_effect=AssertionError("setup invoked")))
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "--research-only", "--emit=json", "--json-profile=raw", "--mock",
        "--no-browser-cookies", "--save-dir", "", "--", topic,
    ])

    assert cli.main() == 0
    research.assert_called_once()
    assert research.call_args.kwargs["topic"] == topic
    assert json.loads(capsys.readouterr().out)["topic"] == topic
    assert not cli.env.CONFIG_FILE.exists()


def test_explicit_setup_still_dispatches(monkeypatch, capsys):
    setup = mock.Mock(return_value={})
    monkeypatch.setattr(setup_wizard, "run_auto_setup", setup)
    monkeypatch.setattr(sys, "argv", ["last30days.py", "setup", "--no-browser-cookies"])

    assert cli.main() == 0
    setup.assert_called_once()
    assert cli.env.CONFIG_FILE.exists()


def test_main_dedicated_subreddits_do_not_leak_to_peers(monkeypatch, tmp_path):
    observed = run_competitor_cli(
        monkeypatch, tmp_path,
        args=["--dedicated-subreddits", "MainHome", "--subreddits", "MainCategory"],
        config={"BRAVE_API_KEY": "dummy-brave-key"}, has_backend=True,
        resolutions={"PeerOne": {"subreddits": ["PeerHome"]}, "PeerTwo": {}},
    )

    assert observed.calls["MainBrand"]["config"]["_dedicated_subreddits"] == ["MainHome"]
    assert observed.calls["MainBrand"]["subreddits"] == ["MainCategory"]
    assert observed.calls["PeerOne"]["subreddits"] == ["PeerHome"]
    assert observed.calls["PeerTwo"]["subreddits"] is None
    for peer in ("PeerOne", "PeerTwo"):
        assert "_dedicated_subreddits" not in observed.calls[peer]["config"]


@pytest.mark.parametrize("window, cached_from, reuse", [
    ([], "2026-05-01", False),
    (["--days=30", "--as-of=2026-05-31"], "2026-05-01", True),
    (["--days=7", "--as-of=2026-05-31"], "2026-05-01", False),
    (["--days=30", "--as-of=2020-01-31"], "2026-05-01", False),
    pytest.param(["--days=0", "--as-of=2026-05-31"], "2026-05-01", False, id="zero-days-rejects-thirty-day-cache"),
    pytest.param(["--days=0", "--as-of=2026-05-31"], "2026-05-31", True, id="zero-days-reuses-same-day-cache"),
])
def test_html_cache_respects_requested_window(monkeypatch, tmp_path, window, cached_from, reuse, capsys):
    report = _report("OpenClaw")
    report.range_from, report.range_to = cached_from, "2026-05-31"
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: {})
    assert cli._write_last_run(report.topic, report)
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("# Cached synthesis", encoding="utf-8")
    research = mock.Mock(return_value=report)
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: _diag())
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--emit=html", "--synthesis-file", str(synthesis), *window,
    ])

    assert cli.main() == 0
    assert research.call_count == (0 if reuse else 1)
    if "--days=0" in window and not reuse:
        assert research.call_args.kwargs["lookback_days"] == 0
    assert ("Reusing cached report" in capsys.readouterr().err) == reuse


def test_comparison_html_cache_survives_plan_cleanup(monkeypatch, tmp_path, capsys):
    plan = tmp_path / "competitors.json"
    plan.write_text(json.dumps({"PeerOne": {"subreddits": ["peerone"]}}), encoding="utf-8")
    run_competitor_cli(monkeypatch, tmp_path, args=["--competitors-plan", str(plan)])
    plan.unlink()
    capsys.readouterr()
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("# Comparison synthesis\n\nMainBrand and peers.", encoding="utf-8")
    research = mock.Mock(side_effect=AssertionError("cached export must not research"))
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "MainBrand", "--cached-report", "--emit=html",
        "--synthesis-file", str(synthesis),
    ])

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "Comparison synthesis" in output
    assert "PeerOne" in output and "PeerTwo" in output
    research.assert_not_called()


@pytest.mark.parametrize("expired", [False, True])
def test_cache_only_export_does_not_fall_back_to_research(monkeypatch, tmp_path, expired):
    if expired:
        assert cli._write_last_run("OpenClaw", _report("OpenClaw"))
        path = cli.env.CONFIG_DIR / "last-report.json"
        payload = json.loads(path.read_text())
        payload["timestamp"] = "2020-01-01T00:00:00+00:00"
        path.write_text(json.dumps(payload))
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Existing synthesis", encoding="utf-8")
    research = mock.Mock(side_effect=AssertionError("unexpected research"))
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--cached-report", "--emit=html",
        "--synthesis-file", str(synthesis),
    ])

    assert cli.main() == 2
    research.assert_not_called()


@pytest.mark.parametrize("initial, followup, reused", [
    (["--search=reddit"], ["--search=reddit"], True),
    (["--search=reddit"], ["--search=x"], False),
    (["--subreddits=Home"], ["--subreddits=Other"], False),
    ([], ["--hiring-signals"], False),
    (["--web-backend=none"], ["--web-backend=auto"], False),
])
def test_cache_matches_explicit_research_scope(monkeypatch, tmp_path, initial, followup, reused):
    report = _report("OpenClaw")
    research = mock.Mock(return_value=report)
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: _diag())
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", "OpenClaw", *initial])
    assert cli.main() == 0
    research.reset_mock()
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Existing synthesis", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--emit=html", "--synthesis-file", str(synthesis), *followup,
    ])

    assert cli.main() == 0
    assert research.call_count == (0 if reused else 1)


def test_cache_matches_plan_content_not_temporary_filename(monkeypatch, tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"subqueries": [{"search_query": "first"}]}))
    options = cli._cache_research_options(cli.build_parser().parse_args(["OpenClaw", "--plan", str(plan)]))
    assert cli._write_last_run("OpenClaw", _report("OpenClaw"), research_options=options)
    same_plan = tmp_path / "new-plan.json"
    same_plan.write_text(plan.read_text())
    same_options = cli._cache_research_options(cli.build_parser().parse_args(["OpenClaw", "--plan", str(same_plan)]))
    assert cli._load_last_report_cache("OpenClaw", research_options=same_options)
    plan.write_text(json.dumps({"subqueries": [{"search_query": "different"}]}))
    changed_options = cli._cache_research_options(cli.build_parser().parse_args(["OpenClaw", "--plan", str(plan)]))
    assert cli._load_last_report_cache("OpenClaw", research_options=changed_options) is None


@pytest.mark.parametrize("cached", [False, True])
def test_cached_export_never_calls_hosted_research(monkeypatch, tmp_path, cached, capsys):
    if cached:
        assert cli._write_last_run("OpenClaw", _report("OpenClaw"))
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("# Stored report export", encoding="utf-8")
    remote = mock.Mock(return_value=123)
    local = mock.Mock(side_effect=AssertionError("unexpected local research"))
    monkeypatch.setattr(hosted, "run_hosted", remote)
    monkeypatch.setattr(cli.pipeline, "run", local)
    monkeypatch.setenv("LAST30DAYS_API_KEY", "dummy-hosted-key")
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example.test")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--cached-report", "--emit=html",
        "--synthesis-file", str(synthesis),
    ])

    assert cli.main() == (0 if cached else 2)
    remote.assert_not_called()
    local.assert_not_called()
    if cached:
        assert "Stored report export" in capsys.readouterr().out


def test_ordinary_synthesis_rejects_changed_source_exclusions(monkeypatch, tmp_path):
    config = {"EXCLUDE_SOURCES": "reddit", "SCRAPECREATORS_API_KEY": "dummy-private-key"}
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: dict(config))
    research = mock.Mock(side_effect=lambda **kwargs: _report(kwargs["topic"]))
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: _diag())
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setenv("LAST30DAYS_ALLOW_ENGINE_PLAN", "1")
    monkeypatch.setattr(sys, "argv", ["last30days.py", "OpenClaw"])
    assert cli.main() == 0
    assert "dummy-private-key" not in (cli.env.CONFIG_DIR / "last-report.json").read_text()
    research.reset_mock()
    config["EXCLUDE_SOURCES"] = "x"
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Stored synthesis", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--emit=html", "--synthesis-file", str(synthesis),
    ])

    assert cli.main() == 0
    research.assert_called_once()
    assert research.call_args.kwargs["config"]["EXCLUDE_SOURCES"] == "x"


@pytest.mark.parametrize("other_mode", ["--welcome", "--preflight", "--discover=ai", "--verify-freshness"])
def test_cache_only_rejects_other_modes_before_dispatch(monkeypatch, tmp_path, other_mode):
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Stored synthesis", encoding="utf-8")
    monkeypatch.setattr(cli.env, "get_config", mock.Mock(side_effect=AssertionError("dispatch began")))
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "OpenClaw", "--cached-report", "--emit=html", "--synthesis-file", str(synthesis), other_mode,
    ])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_cache_only_without_topic_rejects_freshness_before_dispatch(monkeypatch, tmp_path):
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("Stored synthesis", encoding="utf-8")
    monkeypatch.setattr(cli.env, "get_config", mock.Mock(side_effect=AssertionError("dispatch began")))
    monkeypatch.setattr(cli, "_run_cached_freshness", mock.Mock(side_effect=AssertionError("freshness dispatched")))
    monkeypatch.setattr(sys, "argv", [
        "last30days.py", "--cached-report", "--emit=html", "--synthesis-file", str(synthesis), "--verify-freshness",
    ])

    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


@pytest.fixture
def corpus_cache_cli(monkeypatch, tmp_path):
    projects = {name: tmp_path / name for name in ("project-a", "project-b")}
    for name, directory in projects.items():
        notes = directory / "notes"
        notes.mkdir(parents=True)
        (notes / "mcp.md").write_text(
            f"MCP servers provide local tools and resources. Private notes from {name}.",
            encoding="utf-8",
        )
    synthesis = tmp_path / "synthesis.md"
    synthesis.write_text("# MCP server synthesis", encoding="utf-8")
    config = {}
    research = mock.Mock(wraps=cli.pipeline.run)
    monkeypatch.setattr(cli.pipeline, "run", research)
    monkeypatch.setattr(cli.pipeline, "diagnose", lambda *_args, **_kwargs: _diag())
    monkeypatch.setattr(cli.env, "get_config", lambda **kwargs: dict(config))
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")

    def run(project, *, cli_path=None, config_path=None, synthesize=False, cached_only=False):
        monkeypatch.chdir(projects[project])
        config.clear()
        if config_path is not None:
            config["LAST30DAYS_CORPUS_DIRS"] = config_path
        argv = [
            "last30days.py", "MCP servers", "--mock", "--quick", "--search=corpus",
            "--no-browser-cookies", "--corpus-all-time", "--save-dir=",
        ]
        if cli_path is not None:
            argv += ["--corpus", cli_path]
        if synthesize:
            argv += ["--emit=html", "--synthesis-file", str(synthesis)]
        else:
            argv += ["--emit=json", "--json-profile=raw"]
        if cached_only:
            argv.append("--cached-report")
        monkeypatch.setattr(sys, "argv", argv)
        return cli.main()

    return projects, research, run


@pytest.mark.parametrize("configured", [False, True], ids=["cli", "config"])
def test_relative_corpus_cache_cannot_cross_project_directories(corpus_cache_cli, configured, capsys):
    projects, research, run = corpus_cache_cli
    scope = {"config_path" if configured else "cli_path": "notes"}
    assert run("project-a", **scope) == 0
    first = json.loads(capsys.readouterr().out)
    assert "project-a" in first["items_by_source"]["corpus"][0]["body"]
    research.reset_mock()

    assert run("project-b", synthesize=True, **scope) == 0
    research.assert_called_once()
    assert research.call_args.kwargs["config"]["_CORPUS_DIRS"] == [str(projects["project-b"] / "notes")]
    stored = json.loads((cli.env.CONFIG_DIR / "last-report.json").read_text())
    assert "project-b" in stored["reports"][0]["report"]["items_by_source"]["corpus"][0]["body"]


@pytest.mark.parametrize("initial_layer, followup_layer", [
    ("cli_path", "cli_path"), ("config_path", "config_path"),
    ("config_path", "cli_path"), ("cli_path", "config_path"),
])
def test_corpus_cache_matches_effective_paths_across_aliases_and_layers(
    corpus_cache_cli, initial_layer, followup_layer,
):
    projects, research, run = corpus_cache_cli
    assert run("project-a", **{initial_layer: "notes"}) == 0
    research.reset_mock()

    assert run("project-b", synthesize=True, **{followup_layer: str(projects["project-a"] / "notes")}) == 0
    research.assert_not_called()


@pytest.mark.parametrize("explicit, configured, expected", [
    (None, "notes", 0),
    ("notes", None, 2),
    ("absolute-a", None, 0),
    ("absolute-a", "notes", 2),
])
def test_cached_export_corpus_override_matches_effective_directories(
    corpus_cache_cli, explicit, configured, expected,
):
    projects, research, run = corpus_cache_cli
    assert run("project-a", config_path="notes") == 0
    research.reset_mock()
    if explicit == "absolute-a":
        explicit = str(projects["project-a"] / "notes")

    assert run("project-b", cli_path=explicit, config_path=configured, synthesize=True, cached_only=True) == expected
    research.assert_not_called()
