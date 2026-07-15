from lib import env


def test_default_search_defaults_to_empty_string(monkeypatch, tmp_path):
    # Ensure the env var is not set
    monkeypatch.delenv("LAST30DAYS_DEFAULT_SEARCH", raising=False)

    # Avoid reading any real user config file by patching the resolved module path directly
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")

    cfg = env.get_config()

    assert "LAST30DAYS_DEFAULT_SEARCH" in cfg
    assert cfg["LAST30DAYS_DEFAULT_SEARCH"] == ""


def test_default_search_read_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LAST30DAYS_DEFAULT_SEARCH", "reddit,x,youtube")
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")

    cfg = env.get_config()

    assert cfg["LAST30DAYS_DEFAULT_SEARCH"] == "reddit,x,youtube"


def test_default_search_read_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("LAST30DAYS_DEFAULT_SEARCH", raising=False)
    env_file = tmp_path / "config.env"
    env_file.write_text("LAST30DAYS_DEFAULT_SEARCH=reddit,hn\n")
    monkeypatch.setattr(env, "CONFIG_FILE", env_file)

    cfg = env.get_config()

    assert cfg["LAST30DAYS_DEFAULT_SEARCH"] == "reddit,hn"
