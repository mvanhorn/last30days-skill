"""Tests for the first-run setup wizard module."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from lib import setup_wizard


class TestIsFirstRun:
    """Tests for is_first_run()."""

    def test_first_run_when_setup_complete_not_set(self):
        """SETUP_COMPLETE not in config -> first run."""
        config = {"AUTH_TOKEN": "abc", "CT0": "xyz"}
        assert setup_wizard.is_first_run(config) is True

    def test_first_run_when_setup_complete_is_none(self):
        """SETUP_COMPLETE=None -> first run."""
        config = {"SETUP_COMPLETE": None}
        assert setup_wizard.is_first_run(config) is True

    def test_first_run_when_setup_complete_is_empty(self):
        """SETUP_COMPLETE="" -> first run."""
        config = {"SETUP_COMPLETE": ""}
        assert setup_wizard.is_first_run(config) is True

    def test_not_first_run_when_setup_complete_true(self):
        """SETUP_COMPLETE=true -> not first run."""
        config = {"SETUP_COMPLETE": "true"}
        assert setup_wizard.is_first_run(config) is False

    def test_not_first_run_when_setup_complete_any_value(self):
        """SETUP_COMPLETE set to any truthy value -> not first run."""
        config = {"SETUP_COMPLETE": "yes"}
        assert setup_wizard.is_first_run(config) is False


class TestRunAutoSetup:
    """Tests for run_auto_setup()."""

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which")
    def test_cookies_found(self, mock_which, mock_extract):
        """When cookies are found, results dict includes them."""
        mock_extract.return_value = ({"auth_token": "abc", "ct0": "xyz"}, "chrome")
        mock_which.return_value = "/usr/local/bin/yt-dlp"

        config = {}
        results = setup_wizard.run_auto_setup(config)

        assert "x" in results["cookies_found"]
        assert results["cookies_found"]["x"] == "chrome"
        assert results["ytdlp_installed"] is True
        assert results["ytdlp_action"] == "already_installed"
        assert results["env_written"] is False

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which")
    def test_no_cookies_found(self, mock_which, mock_extract):
        """When no cookies found, results dict has empty cookies_found."""
        mock_extract.return_value = None
        mock_which.return_value = None

        config = {}
        results = setup_wizard.run_auto_setup(config)

        assert results["cookies_found"] == {}
        assert results["ytdlp_installed"] is False
        assert results["ytdlp_action"] == "no_homebrew"

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which")
    def test_cookie_extraction_exception(self, mock_which, mock_extract):
        """Cookie extraction raising an exception is handled gracefully."""
        mock_extract.side_effect = Exception("DB locked")
        mock_which.return_value = None

        config = {}
        results = setup_wizard.run_auto_setup(config)

        assert results["cookies_found"] == {}

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which")
    def test_multiple_sources(self, mock_which, mock_extract):
        """Multiple cookie sources can be found."""
        def side_effect(browser, domain, cookie_names):
            if domain == ".x.com":
                return ({"auth_token": "abc", "ct0": "xyz"}, "firefox")
            elif domain == ".truthsocial.com":
                return ({"_session_id": "sess123"}, "firefox")
            return None

        mock_extract.side_effect = side_effect
        mock_which.return_value = None

        config = {}
        results = setup_wizard.run_auto_setup(config)

        assert results["cookies_found"]["x"] == "firefox"
        assert results["cookies_found"]["truthsocial"] == "firefox"


class TestYtdlpAutoInstall:
    """Tests for yt-dlp auto-install via Homebrew in run_auto_setup()."""

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_ytdlp_missing_brew_available_installs(self, mock_which, mock_subproc, mock_extract):
        """yt-dlp missing + brew available -> installs via brew."""
        def which_side_effect(cmd):
            if cmd == "yt-dlp":
                return None
            if cmd == "brew":
                return "/opt/homebrew/bin/brew"
            return None
        mock_which.side_effect = which_side_effect
        mock_subproc.return_value = MagicMock(returncode=0, stderr="")

        results = setup_wizard.run_auto_setup({})

        mock_subproc.assert_called_once_with(
            ["brew", "install", "yt-dlp"],
            capture_output=True, text=True, timeout=120,
        )
        assert results["ytdlp_installed"] is True
        assert results["ytdlp_action"] == "installed"

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which")
    def test_ytdlp_missing_brew_missing(self, mock_which, mock_extract):
        """yt-dlp missing + brew missing -> no_homebrew."""
        mock_which.return_value = None

        results = setup_wizard.run_auto_setup({})

        assert results["ytdlp_installed"] is False
        assert results["ytdlp_action"] == "no_homebrew"

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which")
    def test_ytdlp_already_installed(self, mock_which, mock_extract):
        """yt-dlp already installed -> already_installed."""
        mock_which.return_value = "/usr/local/bin/yt-dlp"

        results = setup_wizard.run_auto_setup({})

        assert results["ytdlp_installed"] is True
        assert results["ytdlp_action"] == "already_installed"

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_brew_install_fails(self, mock_which, mock_subproc, mock_extract):
        """brew install yt-dlp fails -> install_failed with stderr."""
        def which_side_effect(cmd):
            if cmd == "yt-dlp":
                return None
            if cmd == "brew":
                return "/opt/homebrew/bin/brew"
            return None
        mock_which.side_effect = which_side_effect
        mock_subproc.return_value = MagicMock(returncode=1, stderr="Error: something broke")

        results = setup_wizard.run_auto_setup({})

        assert results["ytdlp_installed"] is False
        assert results["ytdlp_action"] == "install_failed"
        assert "something broke" in results["ytdlp_stderr"]


class TestWriteSetupConfig:
    """Tests for write_setup_config()."""

    def test_creates_new_env_file(self):
        """Creates .env file with SETUP_COMPLETE and FROM_BROWSER."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "subdir" / ".env"

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            assert env_path.exists()
            content = env_path.read_text()
            assert "SETUP_COMPLETE=true" in content
            assert "FROM_BROWSER=auto" in content

    def test_appends_to_existing_file(self):
        """Appends to existing .env without overwriting keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("XAI_API_KEY=my-key\nAUTH_TOKEN=tok123\n")

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            content = env_path.read_text()
            # Original keys preserved
            assert "XAI_API_KEY=my-key" in content
            assert "AUTH_TOKEN=tok123" in content
            # New keys appended
            assert "SETUP_COMPLETE=true" in content
            assert "FROM_BROWSER=auto" in content

    def test_does_not_overwrite_existing_keys(self):
        """If SETUP_COMPLETE or FROM_BROWSER already exist, don't duplicate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SETUP_COMPLETE=true\nFROM_BROWSER=firefox\n")

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            content = env_path.read_text()
            # Should only appear once
            assert content.count("SETUP_COMPLETE") == 1
            assert content.count("FROM_BROWSER") == 1
            # Original value preserved
            assert "FROM_BROWSER=firefox" in content

    def test_custom_from_browser_value(self):
        """Custom from_browser value is written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"

            result = setup_wizard.write_setup_config(env_path, from_browser="chrome")

            assert result is True
            content = env_path.read_text()
            assert "FROM_BROWSER=chrome" in content

    def test_creates_parent_directories(self):
        """Creates parent directories if they don't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "a" / "b" / "c" / ".env"

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            assert env_path.exists()

    def test_handles_file_without_trailing_newline(self):
        """Appends correctly when existing file has no trailing newline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("EXISTING_KEY=value")  # no trailing newline

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            content = env_path.read_text()
            # Should have newline separator
            lines = content.strip().split("\n")
            assert len(lines) == 3
            assert lines[0] == "EXISTING_KEY=value"
            assert "SETUP_COMPLETE=true" in lines[1]


class TestGetSetupStatusText:
    """Tests for get_setup_status_text()."""

    def test_with_cookies_and_ytdlp(self):
        """Status text mentions found cookies and yt-dlp."""
        results = {
            "cookies_found": {"x": "chrome"},
            "ytdlp_installed": True,
            "ytdlp_action": "already_installed",
            "env_written": True,
        }
        text = setup_wizard.get_setup_status_text(results)
        assert "X cookies found in chrome" in text
        assert "yt-dlp already installed" in text
        assert "Configuration saved" in text

    def test_with_no_cookies_no_ytdlp(self):
        """Status text shows no cookies and suggests yt-dlp install."""
        results = {
            "cookies_found": {},
            "ytdlp_installed": False,
            "ytdlp_action": "no_homebrew",
            "env_written": False,
        }
        text = setup_wizard.get_setup_status_text(results)
        assert "No browser cookies found" in text
        assert "Install Homebrew first" in text

    def test_status_text_installed(self):
        """Status text for freshly installed yt-dlp."""
        results = {
            "cookies_found": {},
            "ytdlp_installed": True,
            "ytdlp_action": "installed",
            "env_written": False,
        }
        text = setup_wizard.get_setup_status_text(results)
        assert "Installed yt-dlp via Homebrew" in text

    def test_status_text_install_failed(self):
        """Status text for failed yt-dlp install."""
        results = {
            "cookies_found": {},
            "ytdlp_installed": False,
            "ytdlp_action": "install_failed",
            "env_written": False,
        }
        text = setup_wizard.get_setup_status_text(results)
        assert "yt-dlp install failed" in text
        assert "manually" in text


class TestSetupSubcommand:
    """Tests for setup subcommand detection in argument parsing."""

    def test_setup_detected_as_topic(self):
        """The word 'setup' is treated as the setup subcommand."""
        # Simulate what argparse produces
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("topic", nargs="*")
        args = parser.parse_args(["setup"])
        topic = " ".join(args.topic) if args.topic else None
        assert topic is not None
        assert topic.strip().lower() == "setup"

    def test_normal_topic_not_setup(self):
        """A normal topic is not confused with setup."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("topic", nargs="*")
        args = parser.parse_args(["AI", "video", "tools"])
        topic = " ".join(args.topic) if args.topic else None
        assert topic.strip().lower() != "setup"
