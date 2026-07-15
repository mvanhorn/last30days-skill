from __future__ import annotations

import re
from pathlib import Path
from unittest import mock

from lib import env


ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = [
    ROOT / "skills" / "last30days" / "SKILL.md",
    ROOT / "README.md",
]
CONFIG_ENV_KEY_RE = re.compile(
    r"(?<![A-Z0-9_])(?:"
    r"LAST30DAYS_[A-Z0-9_]+|"
    r"(?:XAI|GOOGLE|GEMINI|GOOGLE_GENAI|XIAOHONGSHU|SCRAPECREATORS|APIFY|"
    r"BSKY|TRUTHSOCIAL|BRAVE|EXA|SERPER|OPENROUTER|PERPLEXITY|PARALLEL|"
    r"XQUIK|GROQ)_[A-Z0-9_]+|"
    r"OPENAI_API_KEY|AUTH_TOKEN|CT0|FROM_BROWSER|INCLUDE_SOURCES|"
    r"EXCLUDE_SOURCES|SETUP_COMPLETE|FUN_LEVEL"
    r")(?![A-Z0-9_])"
)
DOC_ONLY_KEYS = {
    "LAST30DAYS_API_BASE",
    "LAST30DAYS_API_KEY",
    "LAST30DAYS_CACHE_DIR",
    "LAST30DAYS_MCP_TIMEOUT",
    "LAST30DAYS_PYTHON",
}


def _neutral_secret_sources():
    return (
        mock.patch.object(env, "_load_keychain", return_value={}),
        mock.patch.object(env, "_load_pass", return_value={}),
    )


def _documented_env_keys() -> set[str]:
    keys: set[str] = set()
    for path in DOC_PATHS:
        keys.update(CONFIG_ENV_KEY_RE.findall(path.read_text(encoding="utf-8")))
    return keys


def test_registered_user_config_keys_round_trip_from_env_file(tmp_path, monkeypatch):
    config_file = tmp_path / ".env"
    config_file.write_text(
        "FUN_LEVEL=high\nLAST30DAYS_REPORT_CACHE_TTL_SECONDS=120\n",
        encoding="utf-8",
    )
    config_file.chmod(0o600)
    monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FUN_LEVEL", raising=False)
    monkeypatch.delenv("LAST30DAYS_REPORT_CACHE_TTL_SECONDS", raising=False)
    monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", config_file)
    monkeypatch.chdir(tmp_path)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        config = env.get_config()

    assert config["FUN_LEVEL"] == "high"
    assert config["LAST30DAYS_REPORT_CACHE_TTL_SECONDS"] == "120"


def test_documented_env_keys_are_registered_in_get_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)

    keychain, pass_store = _neutral_secret_sources()
    with keychain, pass_store:
        registered_keys = set(env.get_config())

    missing = sorted(_documented_env_keys() - DOC_ONLY_KEYS - registered_keys)

    assert missing == []
