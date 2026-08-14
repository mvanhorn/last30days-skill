"""Behavioral tests for configurable credential environment aliases."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from lib import env


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """Keep get_config away from user files, stores, and browser cookies."""
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "missing.env")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "_load_keychain", lambda *args, **kwargs: {})
    monkeypatch.setattr(env, "_load_pass", lambda *args, **kwargs: {})
    for key in (
        *env.KEYCHAIN_KEYS,
        "LAST30DAYS_CREDENTIAL_ALIASES",
        "LAST30DAYS_X_BACKEND",
        "TEAM_X_AUTH",
        "TEAM_X_CT0",
        "TEAM_SC_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_aliases(monkeypatch, aliases: dict[str, object]) -> None:
    monkeypatch.setenv("LAST30DAYS_CREDENTIAL_ALIASES", json.dumps(aliases))


def test_process_aliases_resolve_into_config_without_exporting_canonical_names(
    isolated_config, monkeypatch
):
    _set_aliases(
        monkeypatch,
        {
            "AUTH_TOKEN": "TEAM_X_AUTH",
            "CT0": "TEAM_X_CT0",
            "SCRAPECREATORS_API_KEY": "TEAM_SC_KEY",
        },
    )
    monkeypatch.setenv("TEAM_X_AUTH", "dummy-auth-token")
    monkeypatch.setenv("TEAM_X_CT0", "dummy-ct0-token")
    monkeypatch.setenv("TEAM_SC_KEY", "dummy-sc-key")

    config = env.get_config()

    assert config["AUTH_TOKEN"] == "dummy-auth-token"
    assert config["CT0"] == "dummy-ct0-token"
    assert config["SCRAPECREATORS_API_KEY"] == "dummy-sc-key"
    assert "AUTH_TOKEN" not in os.environ
    assert "CT0" not in os.environ
    assert "SCRAPECREATORS_API_KEY" not in os.environ


def test_alias_values_override_canonical_values_and_preserve_backend_selection(
    isolated_config, monkeypatch
):
    _set_aliases(
        monkeypatch,
        {"AUTH_TOKEN": "TEAM_X_AUTH", "CT0": "TEAM_X_CT0"},
    )
    monkeypatch.setenv("TEAM_X_AUTH", "alias-auth")
    monkeypatch.setenv("TEAM_X_CT0", "alias-ct0")
    monkeypatch.setenv("AUTH_TOKEN", "canonical-auth")
    monkeypatch.setenv("CT0", "canonical-ct0")
    monkeypatch.setenv("LAST30DAYS_X_BACKEND", "xai")

    config = env.get_config()

    assert config["AUTH_TOKEN"] == "alias-auth"
    assert config["CT0"] == "alias-ct0"
    assert config["LAST30DAYS_X_BACKEND"] == "xai"


def test_absent_alias_values_preserve_canonical_pair(isolated_config, monkeypatch):
    _set_aliases(
        monkeypatch,
        {"AUTH_TOKEN": "TEAM_X_AUTH", "CT0": "TEAM_X_CT0"},
    )
    monkeypatch.setenv("AUTH_TOKEN", "canonical-auth")
    monkeypatch.setenv("CT0", "canonical-ct0")

    config = env.get_config()

    assert config["AUTH_TOKEN"] == "canonical-auth"
    assert config["CT0"] == "canonical-ct0"


def test_partial_alias_pair_does_not_mix_with_canonical_or_browser_credentials(
    isolated_config, monkeypatch
):
    _set_aliases(
        monkeypatch,
        {"AUTH_TOKEN": "TEAM_X_AUTH", "CT0": "TEAM_X_CT0"},
    )
    monkeypatch.setenv("TEAM_X_AUTH", "alias-auth")
    monkeypatch.setenv("AUTH_TOKEN", "canonical-auth")
    monkeypatch.setenv("CT0", "canonical-ct0")

    with mock.patch.object(
        env,
        "extract_browser_credentials",
        return_value={"AUTH_TOKEN": "browser-auth", "CT0": "browser-ct0"},
    ):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))

    assert config["AUTH_TOKEN"] is None
    assert config["CT0"] is None


def test_aliases_can_be_declared_and_supplied_by_trusted_config_file(
    isolated_config, monkeypatch, tmp_path
):
    config_file = tmp_path / "last30days.env"
    config_file.write_text(
        'LAST30DAYS_CREDENTIAL_ALIASES={"SCRAPECREATORS_API_KEY":"TEAM_SC_KEY"}\n'
        "TEAM_SC_KEY=dummy-file-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "CONFIG_FILE", config_file)

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] == "dummy-file-key"
    assert config["LAST30DAYS_CREDENTIAL_ALIASES"].startswith(
        '{"SCRAPECREATORS_API_KEY"'
    )


def test_project_file_cannot_choose_an_ambient_credential_alias(
    isolated_config, monkeypatch, tmp_path
):
    global_file = tmp_path / "global.env"
    global_file.write_text("LAST30DAYS_TRUST_PROJECT_CONFIG=true\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_config = project_dir / ".claude" / "last30days.env"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        'LAST30DAYS_CREDENTIAL_ALIASES={"SCRAPECREATORS_API_KEY":"AMBIENT_SECRET"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(env, "CONFIG_FILE", global_file)
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("AMBIENT_SECRET", "must-not-be-read")

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] is None
    assert config["LAST30DAYS_CREDENTIAL_ALIASES"] is None


def test_invalid_alias_metadata_warns_without_exposing_values_or_breaking_canonical(
    isolated_config, monkeypatch, capsys
):
    monkeypatch.setenv("LAST30DAYS_CREDENTIAL_ALIASES", "not-json")
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "canonical-secret-value")

    config = env.get_config()
    warning = capsys.readouterr().err

    assert config["SCRAPECREATORS_API_KEY"] == "canonical-secret-value"
    assert "LAST30DAYS_CREDENTIAL_ALIASES is not valid JSON" in warning
    assert "canonical-secret-value" not in warning
