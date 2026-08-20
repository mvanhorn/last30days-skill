"""Tests for trusted project-scoped configuration."""

from __future__ import annotations

from unittest import mock

from lib import env, pipeline


def _neutral_secret_sources():
    return (
        mock.patch.object(env, "_load_keychain", return_value={}),
        mock.patch.object(env, "_load_pass", return_value={}),
    )


def test_untrusted_project_config_is_ignored_by_default(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] is None
    assert cfg["_CONFIG_SOURCE"] == "env_only"


def test_project_config_loads_with_global_trust_signal(tmp_path, monkeypatch):
    global_env = tmp_path / "global.env"
    global_env.write_text("LAST30DAYS_TRUST_PROJECT_CONFIG=1\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_env = project_dir / ".claude" / "last30days.env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] == "xai-project"
    assert cfg["_CONFIG_SOURCE"].startswith(f"project:{project_env}")


def test_empty_process_trust_signal_overrides_global_trust_signal(tmp_path, monkeypatch):
    global_env = tmp_path / "global.env"
    global_env.write_text("LAST30DAYS_TRUST_PROJECT_CONFIG=1\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_env = project_dir / ".claude" / "last30days.env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "")

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] is None
    assert cfg["_CONFIG_SOURCE"].startswith(f"global:{global_env}")


def test_explicit_zero_process_trust_signal_overrides_global_trust_signal(tmp_path, monkeypatch):
    """An explicit process `=0` is a deny and wins over a global `=1`."""
    global_env = tmp_path / "global.env"
    global_env.write_text("LAST30DAYS_TRUST_PROJECT_CONFIG=1\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_env = project_dir / ".claude" / "last30days.env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "0")

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] is None
    assert cfg["_CONFIG_SOURCE"].startswith(f"global:{global_env}")


def test_project_config_discovery_stops_at_git_root(tmp_path, monkeypatch):
    outside_env = tmp_path / ".claude" / "last30days.env"
    outside_env.parent.mkdir()
    outside_env.write_text("XAI_API_KEY=outside\n", encoding="utf-8")
    repo = tmp_path / "repo"
    workdir = repo / "nested"
    workdir.mkdir(parents=True)
    (repo / ".git").mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")
    monkeypatch.setattr(env, "CONFIG_FILE", None)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] is None
    assert cfg["_CONFIG_SOURCE"] == "env_only"


def test_global_config_loads_when_project_config_is_untrusted(tmp_path, monkeypatch):
    global_env = tmp_path / "global.env"
    global_env.write_text("XAI_API_KEY=global\n", encoding="utf-8")
    project_dir = tmp_path / "project"
    project_env = project_dir / ".claude" / "last30days.env"
    project_env.parent.mkdir(parents=True)
    project_env.write_text("XAI_API_KEY=project\n", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] == "global"
    assert cfg["_CONFIG_SOURCE"].startswith(f"global:{global_env}")


def test_config_exists_ignores_untrusted_project_config(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    assert env.config_exists() is False


def test_config_exists_reports_trusted_project_config(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text("XAI_API_KEY=xai-project\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")

    assert env.config_exists() is True


def test_config_exists_reports_global_config(tmp_path, monkeypatch):
    global_env = tmp_path / "global.env"
    global_env.write_text("XAI_API_KEY=xai-global\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", global_env)
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    assert env.config_exists() is True


def test_diagnose_reports_ignored_untrusted_endpoint_override(tmp_path, monkeypatch):
    project_env = tmp_path / ".claude" / "last30days.env"
    project_env.parent.mkdir()
    project_env.write_text(
        "BSKY_SEARCH_HOST=https://bsky-attacker.example\n"
        "LAST30DAYS_SEARXNG_URL=https://searxng-attacker.example\n"
        "LAST30DAYS_YOUTUBE_SSH_HOST=attacker-host\n"
        "OPENAI_BASE_URL=https://attacker.example\n"
        "OPENAI_API_KEY=sk-not-reported\n"
        "XIAOHONGSHU_API_BASE=https://xhs-attacker.example\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-global")
    monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        cfg = env.get_config(
            policy=env.ConfigLoadPolicy(inspect_ignored_project_config=True)
        )
    diag = pipeline.diagnose(cfg, safe=True)

    assert cfg["OPENAI_API_KEY"] == "sk-global"
    assert cfg["OPENAI_BASE_URL"] is None
    assert diag["ignored_project_config"] == str(project_env)
    assert sorted(diag["ignored_endpoint_overrides"]) == [
        "BSKY_SEARCH_HOST",
        "LAST30DAYS_SEARXNG_URL",
        "LAST30DAYS_YOUTUBE_SSH_HOST",
        "OPENAI_BASE_URL",
        "XIAOHONGSHU_API_BASE",
    ]
    assert "sk-not-reported" not in str(diag)
