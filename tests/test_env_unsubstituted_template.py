"""Unsubstituted `${user_config.*}` placeholders must read as unconfigured.

A Claude Desktop extension writes the literal placeholder for every field the
user has not filled in. The value is non-empty, so before this fix every
presence check downstream read it as a real credential: doctor reported the
source healthy, preflight returned ready, and the backend sent the placeholder
upstream and surfaced the vendor's auth error instead of falling back.
"""

from __future__ import annotations

from unittest import mock

from lib import env


TEMPLATE = "${user_config.scrapecreators_api_key}"

_KEYS = (
    "SCRAPECREATORS_API_KEY",
    "SCRAPE_CREATORS_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "LAST30DAYS_MEMORY_DIR",
    "LAST30DAYS_YT_PLAYER_CLIENT",
)


def _isolate(monkeypatch, tmp_path):
    """Point config loading at an empty world so only the test's env applies."""
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.setattr(env, "_find_project_env", lambda: None)
    monkeypatch.setattr(env, "_load_keychain", lambda *a, **k: {})
    monkeypatch.setattr(env, "_load_pass", lambda *a, **k: {})
    monkeypatch.chdir(tmp_path)
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    # SETUP_COMPLETE has no default and would otherwise be absent from config.
    monkeypatch.delenv("SETUP_COMPLETE", raising=False)


def test_whole_value_template_is_emptied_and_recorded(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", TEMPLATE)

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] == ""
    assert "SCRAPECREATORS_API_KEY" in config["_TEMPLATE_CONFIG_KEYS"]


def test_template_is_removed_from_the_process_environment(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "${user_config.github_token}")

    env.get_config()

    # doctor's GitHub record and the GitHub backend read this name straight
    # from os.environ, bypassing the config dict entirely.
    assert env.read_secret_env("GITHUB_TOKEN") is None


def test_real_credential_is_untouched_and_stays_in_the_environment(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "sc_abc123")

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] == "sc_abc123"
    assert config["_TEMPLATE_CONFIG_KEYS"] == []
    assert env.read_secret_env("SCRAPECREATORS_API_KEY") == "sc_abc123"


def test_placeholder_alongside_other_text_is_kept(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "prefix-${user_config.x}-suffix")

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] == "prefix-${user_config.x}-suffix"
    assert config["_TEMPLATE_CONFIG_KEYS"] == []


def test_shell_default_syntax_is_not_a_template(monkeypatch, tmp_path):
    # SKILL.md itself ships this shape as a .env example.
    _isolate(monkeypatch, tmp_path)
    shell_default = "${LAST30DAYS_MEMORY_DIR:-$HOME/Documents/Last30Days}"
    monkeypatch.setenv("LAST30DAYS_MEMORY_DIR", shell_default)

    config = env.get_config()

    assert config["LAST30DAYS_MEMORY_DIR"] == shell_default
    assert config["_TEMPLATE_CONFIG_KEYS"] == []


def test_key_assembled_before_the_registered_key_loop_is_covered(monkeypatch, tmp_path):
    # OPENAI_API_KEY is set from get_openai_auth() before the `keys` loop.
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "${user_config.openai_api_key}")

    config = env.get_config()

    assert config["OPENAI_API_KEY"] == ""
    assert "OPENAI_API_KEY" in config["_TEMPLATE_CONFIG_KEYS"]


def test_templated_legacy_spelling_does_not_repopulate_the_canonical_key(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("SCRAPE_CREATORS_API_KEY", "${user_config.scrapecreators_api_key}")

    config = env.get_config()

    assert config["SCRAPECREATORS_API_KEY"] == ""


def test_templated_credential_reads_as_absent_to_diagnose(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "${user_config.gemini_api_key}")

    config = env.get_config()
    with mock.patch("lib.bird_x.get_bird_status", return_value={
        "installed": False, "authenticated": False, "username": None,
        "can_install": False,
    }), mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None), \
         mock.patch("lib.grok_x.has_stored_auth", return_value=False), \
         mock.patch("lib.xurl_x.has_stored_auth", return_value=False):
        from lib import pipeline

        diag = pipeline.diagnose(config, None, safe=True)

    assert diag["providers"]["google"] is False


def test_no_templates_leaves_an_empty_record(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    config = env.get_config()

    assert config["_TEMPLATE_CONFIG_KEYS"] == []


def test_is_unsubstituted_template_matches_only_the_whole_placeholder():
    assert env.is_unsubstituted_template(TEMPLATE) is True
    assert env.is_unsubstituted_template("  ${user_config.x}  ") is True
    assert env.is_unsubstituted_template("${user_config.x} ") is True
    assert env.is_unsubstituted_template("${user_config.x}-tail") is False
    assert env.is_unsubstituted_template("${LAST30DAYS_MEMORY_DIR:-x}") is False
    assert env.is_unsubstituted_template("") is False
    assert env.is_unsubstituted_template(None) is False
    assert env.is_unsubstituted_template(1234) is False
