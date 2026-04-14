from scripts.lib import env


def test_include_sources_defaults_to_empty_string(monkeypatch, tmp_path):
    # Ensure the env var is not set
    monkeypatch.delenv("INCLUDE_SOURCES", raising=False)

    # Avoid reading any real user config file by patching the resolved module path directly
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")

    cfg = env.get_config()

    assert "INCLUDE_SOURCES" in cfg
    assert cfg["INCLUDE_SOURCES"] == ""
