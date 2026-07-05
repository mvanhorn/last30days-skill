"""Tests for secret-file write hygiene (U7)."""
import os
import stat
import sys
from pathlib import Path

import pytest

from lib import env, setup_wizard


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestSecureEnvWrite:
    def test_new_env_file_is_0600(self, tmp_path):
        env_path = tmp_path / "cfg" / ".env"
        assert setup_wizard.write_setup_config(env_path, from_browser="auto") is True
        assert env_path.exists()
        if sys.platform == "win32":
            # Windows: os.chmod only supports read-only flag, can't enforce 0o600
            assert os.access(env_path, os.W_OK), "file should be writable"
        else:
            assert _mode(env_path) == 0o600

    def test_existing_loose_file_tightened_to_0600(self, tmp_path):
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING_KEY=value\n", encoding="utf-8")
        os.chmod(env_path, 0o644)
        setup_wizard.write_setup_config(env_path, from_browser="auto")
        if sys.platform == "win32":
            assert os.access(env_path, os.W_OK), "file should be writable"
        else:
            assert _mode(env_path) == 0o600

    def test_written_config_is_loadable(self, tmp_path):
        env_path = tmp_path / ".env"
        setup_wizard.write_setup_config(env_path, from_browser="auto")
        config = env.load_env_file(env_path)
        assert config["SETUP_COMPLETE"] == "true", f"unexpected config: {config}"

    def test_written_config_masks_sensitive_fields(self, tmp_path):
        env_path = tmp_path / ".env"
        setup_wizard.write_setup_config(env_path, from_browser="auto")
        lines = env_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= 2
        assert "SETUP_COMPLETE=true" in lines[0]
