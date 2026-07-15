"""Tests for secret-file write hygiene (U7)."""

import os
import stat
from pathlib import Path

from lib import env, setup_wizard


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestSecureEnvWrite:
    def test_new_env_file_is_0600(self, tmp_path):
        env_path = tmp_path / "cfg" / ".env"
        assert setup_wizard.write_setup_config(env_path, from_browser="auto") is True
        assert env_path.exists()
        assert _mode(env_path) == 0o600

    def test_existing_loose_file_tightened_to_0600(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING_KEY=value\n", encoding="utf-8")
        os.chmod(env_path, 0o644)
        setup_wizard.write_setup_config(env_path, from_browser="auto")
        assert _mode(env_path) == 0o600

    def test_written_config_is_loadable(self, tmp_path):
        env_path = tmp_path / ".env"
        setup_wizard.write_setup_config(env_path, from_browser="auto")
        loaded = env.load_env_file(env_path)
        assert loaded.get("SETUP_COMPLETE") == "true"
        assert loaded.get("FROM_BROWSER") == "auto"


class TestFormatEnvValue:
    def test_plain_token_unchanged(self):
        assert setup_wizard._format_env_value("abc123-TOKEN_x") == "abc123-TOKEN_x"

    def test_value_with_spaces_roundtrips(self, tmp_path):
        env_path = tmp_path / ".env"
        line = f"SOME_KEY={setup_wizard._format_env_value('two words')}\n"
        # Write 0o600 via the loader-compatible path and confirm the loader
        # strips the quoting back to the original value.
        env_path.write_text(line, encoding="utf-8")
        os.chmod(env_path, 0o600)
        loaded = env.load_env_file(env_path)
        assert loaded["SOME_KEY"] == "two words"

    def test_newline_is_stripped(self):
        out = setup_wizard._format_env_value("line1\nline2")
        assert "\n" not in out
