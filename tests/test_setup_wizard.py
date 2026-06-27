"""Tests for the first-run setup wizard module."""

import subprocess
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
        results = setup_wizard.run_auto_setup(config, allow_browser_cookies=True)

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
        mock_extract.assert_not_called()
        assert results["ytdlp_installed"] is False
        assert results["ytdlp_action"] == "no_homebrew"

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which")
    def test_cookie_extraction_exception(self, mock_which, mock_extract):
        """Cookie extraction raising an exception is handled gracefully."""
        mock_extract.side_effect = Exception("DB locked")
        mock_which.return_value = None

        config = {}
        results = setup_wizard.run_auto_setup(config, allow_browser_cookies=True)

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
        results = setup_wizard.run_auto_setup(config, allow_browser_cookies=True)

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


class TestDiggAutoInstall:
    """Tests for digg-pp-cli auto-install via npx in run_auto_setup()."""

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which")
    def test_digg_already_installed(self, mock_which, mock_extract):
        """digg-pp-cli already on PATH -> already_installed, no subprocess."""
        # yt-dlp missing + brew missing keeps the yt-dlp path subprocess-free;
        # digg-pp-cli present short-circuits before any npx call.
        def which_side_effect(cmd):
            return "/Users/me/go/bin/digg-pp-cli" if cmd == "digg-pp-cli" else None
        mock_which.side_effect = which_side_effect

        with patch("subprocess.run") as mock_subproc:
            results = setup_wizard.run_auto_setup({})
            mock_subproc.assert_not_called()

        assert results["digg_installed"] is True
        assert results["digg_action"] == "already_installed"

    # Redirect HOME/GOPATH so real ~/.local/bin or ~/go/bin digg-pp-cli on the
    # dev box does not make the binary look present during absence tests.
    @staticmethod
    def _empty_home(tmp_path, monkeypatch):
        monkeypatch.delenv("GOPATH", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which")
    def test_digg_no_npx(self, mock_which, mock_extract, tmp_path, monkeypatch):
        """digg-pp-cli missing + npx missing -> no_npx, no subprocess."""
        self._empty_home(tmp_path, monkeypatch)
        mock_which.return_value = None

        with patch("subprocess.run") as mock_subproc:
            results = setup_wizard.run_auto_setup({})
            mock_subproc.assert_not_called()

        assert results["digg_installed"] is False
        assert results["digg_action"] == "no_npx"

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_digg_install_succeeds(self, mock_which, mock_subproc, mock_extract, tmp_path, monkeypatch):
        """npx present + install succeeds + binary verifiable -> installed."""
        self._empty_home(tmp_path, monkeypatch)
        # First which("digg-pp-cli") (pre-install) -> None, npx -> present,
        # then post-install which("digg-pp-cli") -> resolves.
        calls = {"digg": 0}

        def which_side_effect(cmd):
            if cmd == "digg-pp-cli":
                calls["digg"] += 1
                return None if calls["digg"] == 1 else "/Users/me/go/bin/digg-pp-cli"
            if cmd == "npx":
                return "/opt/homebrew/bin/npx"
            return None
        mock_which.side_effect = which_side_effect
        mock_subproc.return_value = MagicMock(returncode=0, stderr="")

        results = setup_wizard.run_auto_setup({})

        # The wizard now also best-effort-installs the additional default-on
        # Printing Press sources (arxiv/techmeme/trustpilot), so digg is one of
        # several install calls rather than the only one.
        mock_subproc.assert_any_call(
            ["npx", "-y", setup_wizard.PRINTING_PRESS_NPM, "install", "digg", "--cli-only"],
            capture_output=True, text=True, timeout=setup_wizard.DIGG_INSTALL_TIMEOUT,
        )
        assert results["digg_installed"] is True
        assert results["digg_action"] == "installed"

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_digg_install_fails_nonzero(self, mock_which, mock_subproc, mock_extract, tmp_path, monkeypatch):
        """npx install returns non-zero -> install_failed with stderr."""
        self._empty_home(tmp_path, monkeypatch)
        def which_side_effect(cmd):
            return "/opt/homebrew/bin/npx" if cmd == "npx" else None
        mock_which.side_effect = which_side_effect
        mock_subproc.return_value = MagicMock(returncode=1, stderr="npm ERR! boom")

        results = setup_wizard.run_auto_setup({})

        assert results["digg_installed"] is False
        assert results["digg_action"] == "install_failed"
        assert "boom" in results["digg_stderr"]

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which")
    def test_digg_prior_install_off_path(self, mock_which, mock_extract, tmp_path, monkeypatch):
        """pp-digg CLI at ~/.local/bin but not on PATH -> installed_off_path, no npx."""
        self._empty_home(tmp_path, monkeypatch)
        local_bin = tmp_path / ".local" / "bin"
        local_bin.mkdir(parents=True)
        binary = local_bin / "digg-pp-cli"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        mock_which.return_value = None

        with patch("subprocess.run") as mock_subproc:
            results = setup_wizard.run_auto_setup({})
            mock_subproc.assert_not_called()

        assert results["digg_installed"] is False
        assert results["digg_action"] == "installed_off_path"
        assert results["digg_path"] == str(binary)

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_digg_install_zero_but_not_on_path(self, mock_which, mock_subproc, mock_extract, tmp_path, monkeypatch):
        """rc=0, binary at $HOME/.local/bin but not on PATH -> installed_off_path."""
        self._empty_home(tmp_path, monkeypatch)
        def which_side_effect(cmd):
            return "/opt/homebrew/bin/npx" if cmd == "npx" else None
        mock_which.side_effect = which_side_effect

        local_bin = tmp_path / ".local" / "bin"

        def fake_install(*args, **kwargs):
            local_bin.mkdir(parents=True, exist_ok=True)
            binary = local_bin / "digg-pp-cli"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(0o755)
            return MagicMock(returncode=0, stderr="")
        mock_subproc.side_effect = fake_install

        results = setup_wizard.run_auto_setup({})

        assert results["digg_installed"] is False
        assert results["digg_action"] == "installed_off_path"
        assert results["digg_path"] == str(local_bin / "digg-pp-cli")

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which")
    def test_digg_install_timeout_does_not_raise(self, mock_which, mock_subproc, mock_extract, tmp_path, monkeypatch):
        """subprocess raising (e.g. timeout) -> install_failed, no exception escapes."""
        self._empty_home(tmp_path, monkeypatch)
        def which_side_effect(cmd):
            return "/opt/homebrew/bin/npx" if cmd == "npx" else None
        mock_which.side_effect = which_side_effect
        mock_subproc.side_effect = subprocess.TimeoutExpired(cmd="npx", timeout=300)

        results = setup_wizard.run_auto_setup({})

        assert results["digg_installed"] is False
        assert results["digg_action"] == "install_failed"


class TestWriteSetupConfig:
    """Tests for write_setup_config()."""

    def test_creates_new_env_file(self):
        """Creates .env with SETUP_COMPLETE; omits FROM_BROWSER when unspecified.

        With no detected browser we must NOT pin FROM_BROWSER=auto, because
        that makes every later run probe Chrome and re-trigger the macOS
        Keychain prompt. Leaving it unset applies the safe Firefox/Safari
        default instead.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "subdir" / ".env"

            result = setup_wizard.write_setup_config(env_path)

            assert result is True
            assert env_path.exists()
            content = env_path.read_text()
            assert "SETUP_COMPLETE=true" in content
            assert "FROM_BROWSER" not in content

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
            # SETUP_COMPLETE appended; FROM_BROWSER omitted (no browser detected)
            assert "SETUP_COMPLETE=true" in content
            assert "FROM_BROWSER" not in content

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

            result = setup_wizard.write_setup_config(env_path, from_browser="firefox")

            assert result is True
            content = env_path.read_text()
            # Should have newline separator
            lines = content.strip().split("\n")
            assert len(lines) == 3
            assert lines[0] == "EXISTING_KEY=value"
            assert "SETUP_COMPLETE=true" in lines[1]


class TestWriteApiKey:
    """Tests for write_api_key() — persisting the ScrapeCreators signup key."""

    def test_writes_key_with_secret_permissions(self):
        """Key is written and the file is 0o600 (owner read/write only)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "subdir" / ".env"

            result = setup_wizard.write_api_key(env_path, "sc_live_abcdef123456")

            assert result is True
            assert env_path.exists()
            assert "SCRAPECREATORS_API_KEY=sc_live_abcdef123456" in env_path.read_text()
            assert (env_path.stat().st_mode & 0o777) == 0o600

    def test_value_round_trips_through_env_loader(self):
        """Persisted key reloads to the exact original value."""
        from lib import env as env_mod
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"

            setup_wizard.write_api_key(env_path, "sc_live_abcdef123456")

            loaded = env_mod.load_env_file(env_path)
            assert loaded["SCRAPECREATORS_API_KEY"] == "sc_live_abcdef123456"

    def test_idempotent_when_key_already_present(self):
        """If the key already exists, do not duplicate or overwrite it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SCRAPECREATORS_API_KEY=existing_key\n")

            result = setup_wizard.write_api_key(env_path, "sc_new_value")

            assert result is True
            content = env_path.read_text()
            assert content.count("SCRAPECREATORS_API_KEY") == 1
            assert "existing_key" in content
            assert "sc_new_value" not in content

    def test_appends_without_clobbering_other_keys(self):
        """Existing unrelated keys are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SETUP_COMPLETE=true\nFROM_BROWSER=firefox\n")

            setup_wizard.write_api_key(env_path, "sc_key_xyz")

            content = env_path.read_text()
            assert "SETUP_COMPLETE=true" in content
            assert "FROM_BROWSER=firefox" in content
            assert "SCRAPECREATORS_API_KEY=sc_key_xyz" in content

    def test_value_with_whitespace_is_quoted(self):
        """A pathological value with whitespace is quoted so it round-trips."""
        from lib import env as env_mod
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"

            setup_wizard.write_api_key(env_path, "key with space")

            content = env_path.read_text()
            assert 'SCRAPECREATORS_API_KEY="key with space"' in content
            assert env_mod.load_env_file(env_path)["SCRAPECREATORS_API_KEY"] == "key with space"

    def test_empty_key_returns_false_and_writes_nothing(self):
        """An empty api_key persists nothing and reports failure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"

            assert setup_wizard.write_api_key(env_path, "") is False
            assert not env_path.exists()

    def test_unwritable_target_returns_false(self):
        """Unwritable target dir -> False, no exception escapes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ro_dir = Path(tmpdir) / "ro"
            ro_dir.mkdir()
            ro_dir.chmod(0o500)  # no write
            try:
                result = setup_wizard.write_api_key(ro_dir / "sub" / ".env", "sc_key")
                assert result is False
            finally:
                ro_dir.chmod(0o700)  # restore so tempdir cleanup succeeds


class TestMaskApiKey:
    """Tests for mask_api_key() — non-secret display form."""

    def test_masks_long_key(self):
        masked = setup_wizard.mask_api_key("sc_live_abcdef123456")
        assert "abcdef" not in masked
        assert masked.endswith("3456")
        assert masked.startswith("sc_")

    def test_short_key_collapses_to_placeholder(self):
        assert setup_wizard.mask_api_key("short") == "sc_…"

    def test_empty_key_collapses_to_placeholder(self):
        assert setup_wizard.mask_api_key("") == "sc_…"


class TestCookieExtractionBrowsers:
    """Tests for env.cookie_extraction_browsers() — the shared browser policy."""

    def test_default_disables_extraction(self):
        """FROM_BROWSER unset -> no browser-cookie reads."""
        from lib import env
        browsers = env.cookie_extraction_browsers({})
        assert browsers == []

    def test_off_disables_extraction(self):
        from lib import env
        assert env.cookie_extraction_browsers({"FROM_BROWSER": "off"}) == []

    def test_auto_opts_into_chrome(self):
        from lib import env
        assert "chrome" in env.cookie_extraction_browsers({"FROM_BROWSER": "auto"})

    def test_specific_browser(self):
        from lib import env
        assert env.cookie_extraction_browsers({"FROM_BROWSER": "chrome"}) == ["chrome"]


class TestWizardDoesNotProbeChromeByDefault:
    """Regression: first-run setup must not silently read Chrome cookies."""

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which", return_value=None)
    def test_default_run_never_requests_chrome(self, _mock_which, mock_extract):
        setup_wizard.run_auto_setup({})
        requested_browsers = {call.args[0] for call in mock_extract.call_args_list}
        assert requested_browsers == set()

    @patch("lib.cookie_extract.extract_cookies_with_source", return_value=None)
    @patch("shutil.which", return_value=None)
    def test_from_browser_auto_does_request_chrome(self, _mock_which, mock_extract):
        setup_wizard.run_auto_setup({"FROM_BROWSER": "auto"}, allow_browser_cookies=True)
        requested_browsers = {call.args[0] for call in mock_extract.call_args_list}
        assert "chrome" in requested_browsers

    @patch("lib.cookie_extract.extract_cookies_with_source")
    @patch("shutil.which", return_value=None)
    def test_from_browser_off_skips_extraction(self, _mock_which, mock_extract):
        results = setup_wizard.run_auto_setup({"FROM_BROWSER": "off"})
        mock_extract.assert_not_called()
        assert results["cookies_found"] == {}


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

    def test_status_text_digg_installed(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "installed", "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "Installed Digg CLI" in text

    def test_status_text_digg_already_installed(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "already_installed", "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "Digg CLI already installed" in text

    def test_status_text_digg_install_failed(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "install_failed", "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "Digg CLI install failed" in text
        assert "printing-press-library" in text

    def test_status_text_digg_installed_off_path(self):
        home = Path.home()
        digg_path = str(home / ".local" / "bin" / "digg-pp-cli")
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "installed_off_path",
                   "digg_path": digg_path,
                   "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "not on PATH" in text
        assert "$HOME/.local/bin" in text
        assert "now active" not in text.lower()

    def test_status_text_digg_installed_off_path_legacy_go_bin(self):
        """PATH hint names the actual install dir as $HOME-relative, not ~/.local/bin."""
        home = Path.home()
        digg_path = str(home / "go" / "bin" / "digg-pp-cli")
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "installed_off_path",
                   "digg_path": digg_path,
                   "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "$HOME/go/bin" in text
        assert ".local/bin" not in text

    def test_status_text_digg_installed_off_path_missing_path(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "installed_off_path",
                   "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "not on PATH" in text
        assert "add its install directory to PATH" in text

    def test_status_text_digg_installed_off_path_empty_path(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "installed_off_path",
                   "digg_path": "",
                   "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "add its install directory to PATH" in text

    def test_digg_bin_dir_hint_windows_returns_absolute_parent(self):
        home = Path.home()
        digg_path = str(home / ".local" / "bin" / "digg-pp-cli")
        expected = str(home / ".local" / "bin")
        with patch.object(setup_wizard.os, "name", "nt"):
            assert setup_wizard._digg_bin_dir_hint(digg_path) == expected

    def test_status_text_digg_no_npx(self):
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "digg_action": "no_npx", "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "Digg CLI not installed" in text

    def test_status_text_digg_absent_key_renders(self):
        """No digg_action key (defensive) -> no Digg line, no error."""
        results = {"cookies_found": {}, "ytdlp_action": "already_installed",
                   "env_written": False}
        text = setup_wizard.get_setup_status_text(results)
        assert "Digg" not in text


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
