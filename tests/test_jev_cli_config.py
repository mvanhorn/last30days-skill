"""CLI and credential contract for key-driven Jev use. No network."""
import json
import sys
from unittest.mock import patch

import pytest

import last30days as cli
from lib import env, http, jev


def parse(*argv):
    return cli.build_parser().parse_args(argv)


@pytest.mark.parametrize("config,route", [
    ({}, None), ({"TYPESAFE_API_KEY": "dummy-typesafe-key"}, "typesafe"),
    ({"OPENROUTER_API_KEY": "dummy-openrouter-key"}, "openrouter"),
    ({"TYPESAFE_API_KEY": "dummy-typesafe-key", "OPENROUTER_API_KEY": "dummy-openrouter-key"}, "typesafe"),
    ({"LAST30DAYS_JEV_PROVIDER": "off", "TYPESAFE_API_KEY": "dummy-key"}, None),
])
def test_default_uses_available_key_and_explicit_off_wins(config, route, capsys):
    original = dict(config)
    cli._configure_jev_research(parse("topic"), "topic", config)
    assert config == original
    assert jev.configured_route(config)[0] == route
    assert "_research_facets" not in config
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("mode", ["off", "auto", "typesafe", "openrouter"])
def test_cli_provider_overrides_saved_preference(mode):
    config = {"LAST30DAYS_JEV_PROVIDER": "typesafe"}
    cli._configure_jev_research(parse("topic", "--jev-provider", mode), "topic", config)
    assert config["LAST30DAYS_JEV_PROVIDER"] == mode


@pytest.fixture
def facets_file(tmp_path):
    path = tmp_path / "facets.json"
    path.write_text(json.dumps([{"id": "release", "question": "What changed?", "query": "Rust release changes"}]))
    return path


def test_facets_load_and_normalize_without_enabling_a_collector(facets_file):
    config = {"TYPESAFE_API_KEY": "dummy-key"}
    args = parse("Rust releases", "--research-facets", str(facets_file), "--mock")
    cli._configure_jev_research(args, "Rust releases", config)
    assert config["_research_facets"] == [{"id": "release", "question": "What changed?", "query": "Rust release changes", "search_type": "fast", "required_role": "any"}]
    assert "INCLUDE_SOURCES" not in config
    assert "PERPLEXITY_API_KEY" not in config


def test_facets_reject_explicit_off(facets_file):
    args = parse("topic", "--jev-provider", "off", "--research-facets", str(facets_file))
    with pytest.raises(ValueError, match="requires --jev-provider"):
        cli._configure_jev_research(args, "topic", {"TYPESAFE_API_KEY": "dummy-key"})


@pytest.mark.parametrize("topic,flags", [
    ("topic", ["--discover", "technology"]),
    ("topic", ["--competitors", "2"]),
    ("Rust vs Go", []),
    ("topic", ["--drill", "cluster-1"]),
    ("topic", ["--deep-research"]),
    ("topic", ["--emit", "html", "--synthesis-file", "existing.md"]),
    ("topic", ["--diagnose"]),
    ("topic", ["--preflight"]),
    ("doctor", []), ("setup", []), ("library search rust", []), ("queue list", []),
])
def test_facets_reject_unsupported_modes_before_reading_input(topic, flags):
    args = parse(topic, "--jev-provider", "auto", "--research-facets", "/missing/file.json", *flags)
    with pytest.raises(ValueError, match="fresh, normal single-topic"):
        cli._configure_jev_research(args, topic, {"TYPESAFE_API_KEY": "dummy-key"})


@pytest.mark.parametrize("contents", [b"not json", b"\xff", b" " * 65_537, b"{}", b"[]",
    b'[{"id":"x","question":"q","query":"q","search_type":{}}]'])
def test_facets_reject_bad_file_input(tmp_path, contents):
    path = tmp_path / "invalid.json"
    path.write_bytes(contents)
    args = parse("topic", "--jev-provider", "auto", "--research-facets", str(path))
    with pytest.raises(ValueError):
        cli._configure_jev_research(args, "topic", {"TYPESAFE_API_KEY": "dummy-key"})


@pytest.mark.parametrize("key", ["TYPESAFE_API_KEY", "OPENROUTER_API_KEY"])
@pytest.mark.parametrize("selection", [[], ["--jev-provider", "auto"], ["--jev-provider", "off"]])
def test_configured_jev_runs_locally_unless_explicitly_disabled(monkeypatch, tmp_path, capsys, key, selection):
    from tests.test_hosted import DIAG, make_report

    config = {key: "dummy-key"}
    monkeypatch.setattr(sys, "argv", ["last30days.py", "Rust releases", "--no-browser-cookies", *selection])
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example.test")
    monkeypatch.setenv("LAST30DAYS_SKIP_PREFLIGHT", "1")
    monkeypatch.setattr(cli.env, "CONFIG_DIR", tmp_path)
    with patch.object(cli.env, "get_config", return_value=config), patch.object(cli.env, "read_secret_env", return_value="dummy-hosted-key"), patch.object(cli, "_propagate_config_to_environ"), patch("lib.hosted.run_hosted", return_value=0) as hosted, patch.object(cli.pipeline, "diagnose", return_value=DIAG), patch.object(cli.pipeline, "run", return_value=make_report()) as run, patch.object(cli, "_render_save_and_print", return_value=0), patch("lib.resolve.auto_resolve", return_value={}):
        assert cli.main() == 0
    if "off" in selection:
        hosted.assert_called_once()
        run.assert_not_called()
    else:
        hosted.assert_not_called()
        run.assert_called_once()
        assert "using local research" in capsys.readouterr().err


def test_typesafe_file_key_enables_route_and_environment_can_disable_it(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("TYPESAFE_API_KEY=dummy-file-key\n")
    path.chmod(0o600)
    monkeypatch.setattr(env, "CONFIG_FILE", path)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("LAST30DAYS_JEV_PROVIDER", raising=False)
    with patch.object(env, "_find_project_env", return_value=None), patch.object(env, "_load_keychain", return_value={}), patch.object(env, "_load_pass", return_value={}):
        config = env.get_config()
        assert config["TYPESAFE_API_KEY"] == "dummy-file-key"
        assert config["LAST30DAYS_JEV_PROVIDER"] == "auto"
        assert jev.configured_route(config) == ("typesafe", "ready")
        monkeypatch.setenv("LAST30DAYS_JEV_PROVIDER", "off")
        assert env.get_config()["LAST30DAYS_JEV_PROVIDER"] == "off"
    assert "dummy-file-key" in http.config_secret_values(config)


@pytest.mark.parametrize("argv", [
    ["--welcome"], ["setup", "--store-key", "TYPESAFE_API_KEY"],
])
def test_internal_early_dispatch_cannot_ignore_facets(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["last30days.py", *argv, "--jev-provider", "auto", "--research-facets", "/missing/file.json"])
    with patch.object(cli, "_run_store_key") as store, patch.object(cli.env, "get_config") as config:
        assert cli.main() == 2
    store.assert_not_called()
    config.assert_not_called()


@pytest.mark.parametrize("mode,credentials", [
    ("auto", {}), ("auto", {"TYPESAFE_API_KEY": " ", "OPENROUTER_API_KEY": "\t"}),
    ("typesafe", {"OPENROUTER_API_KEY": "dummy-other-route"}),
    ("openrouter", {"TYPESAFE_API_KEY": "dummy-other-route"}),
])
def test_missing_selected_key_skips_facet_file_and_validation(mode, credentials, capsys):
    args = parse("topic", "--jev-provider", mode, "--research-facets", "/not/read.json", "--research-policy", "fixed")
    config = dict(credentials)
    with patch.object(cli.Path, "open", side_effect=AssertionError("facet read")), patch("lib.adaptive_research.validate_facets", side_effect=AssertionError("facet validation")), patch.object(jev, "JevClient", side_effect=AssertionError("client factory")):
        cli._configure_jev_research(args, "topic", config)
    assert config["LAST30DAYS_JEV_PROVIDER"] == mode
    assert "_research_facets" not in config and "_research_policy" not in config
    assert capsys.readouterr().err == "[last30days] Jev and research follow-up skipped: selected route has no configured key; ordinary research continues.\n"


def test_missing_key_skips_facet_specific_mode_validation():
    args = parse("topic", "--jev-provider", "auto", "--research-facets", "/not/read.json", "--discover", "technology")
    with patch.object(cli.Path, "open", side_effect=AssertionError("facet read")):
        cli._configure_jev_research(args, "topic", {})


def test_hosted_missing_jev_key_keeps_ordinary_submission(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["last30days.py", "Rust releases", "--jev-provider", "auto", "--research-facets", "/not/read.json", "--research-policy", "fixed"])
    monkeypatch.setenv("LAST30DAYS_API_BASE", "https://hosted.example.test")
    with patch.object(cli.env, "get_config", return_value={}), patch.object(cli.env, "read_secret_env", return_value="dummy-hosted-key"), patch.object(cli, "_propagate_config_to_environ"), patch("lib.hosted.run_hosted", return_value=0) as hosted, patch.object(cli.pipeline, "run") as run, patch.object(jev, "JevClient", side_effect=AssertionError("client factory")):
        assert cli.main() == 0
    hosted.assert_called_once()
    run.assert_not_called()
    assert "ordinary research continues" in capsys.readouterr().err
