import os

from scripts.lib.env import get_config


def test_include_sources_defaults_to_empty_string(monkeypatch, tmp_path):
    # Ensure the env var is not set
    monkeypatch.delenv("INCLUDE_SOURCES", raising=False)

    # Avoid reading any real user config file
    monkeypatch.setenv("LAST30DAYS_CONFIG_FILE", str(tmp_path / "does-not-exist.env"))

    cfg = get_config()

    assert "INCLUDE_SOURCES" in cfg
    assert cfg["INCLUDE_SOURCES"] == ""
