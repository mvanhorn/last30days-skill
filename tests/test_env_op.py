"""Tests for 1Password op:// config references."""

from __future__ import annotations

import subprocess
from unittest import mock

import pytest

from lib import env


def _run_result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Hide real host config so op:// tests are deterministic."""
    for var in [
        "OPENAI_API_KEY", "XAI_API_KEY", "BRAVE_API_KEY", "AUTH_TOKEN", "CT0",
        "SCRAPECREATORS_API_KEY", "SCRAPE_CREATORS_API_KEY", "APIFY_API_TOKEN",
        "BSKY_HANDLE", "BSKY_APP_PASSWORD", "TRUTHSOCIAL_TOKEN", "EXA_API_KEY",
        "SERPER_API_KEY", "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY",
        "PARALLEL_API_KEY", "XQUIK_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_GENAI_API_KEY", "INCLUDE_SOURCES", "FROM_BROWSER",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.setattr(env, "_load_pass", lambda *a, **k: {})
    monkeypatch.chdir(tmp_path)


def test_resolve_secret_reference_passes_through_plain_values():
    assert env.resolve_secret_reference("plain-token") == "plain-token"
    assert env.resolve_secret_reference(None) is None


def test_resolve_secret_reference_reads_op_value(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "service-token")

    with mock.patch("shutil.which", return_value="/opt/homebrew/bin/op"), \
         mock.patch("pathlib.Path.exists", return_value=True), \
         mock.patch("subprocess.run", return_value=_run_result(0, "resolved-token\n")) as run:
        assert env.resolve_secret_reference("op://Vault/Item/credential") == "resolved-token"

    run.assert_called_once()
    assert run.call_args.args[0][:2] == ["/opt/homebrew/bin/op", "read"]
    assert run.call_args.kwargs["env"]["OP_SERVICE_ACCOUNT_TOKEN"] == "service-token"


def test_resolve_secret_reference_fails_closed():
    with mock.patch("shutil.which", return_value="/opt/homebrew/bin/op"), \
         mock.patch("pathlib.Path.exists", return_value=True), \
         mock.patch("subprocess.run", return_value=_run_result(1, "")):
        assert env.resolve_secret_reference("op://Vault/Item/credential") is None


def test_get_config_resolves_op_value_from_config_file(clean_env, tmp_path, monkeypatch):
    cfg_file = tmp_path / "global.env"
    cfg_file.write_text("XAI_API_KEY=op://Vault/XAI/credential\n", encoding="utf-8")
    monkeypatch.setattr(env, "CONFIG_FILE", cfg_file)

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "resolve_secret_reference", side_effect=lambda value: {
             "op://Vault/XAI/credential": "xai-resolved",
         }.get(value, value)):
        cfg = env.get_config()

    assert cfg["XAI_API_KEY"] == "xai-resolved"


def test_get_config_resolves_op_value_from_process_env(clean_env, monkeypatch):
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "op://Vault/ScrapeCreators/credential")

    with mock.patch.object(env, "_load_keychain", return_value={}), \
         mock.patch.object(env, "resolve_secret_reference", side_effect=lambda value: {
             "op://Vault/ScrapeCreators/credential": "sc-resolved",
         }.get(value, value)):
        cfg = env.get_config()

    assert cfg["SCRAPECREATORS_API_KEY"] == "sc-resolved"
