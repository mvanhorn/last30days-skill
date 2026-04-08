"""Tests for OpenClaw setup and device auth functions."""

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

# Add scripts dir to path
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from lib import setup_wizard


class TestRunOpenclawSetup:
    """Tests for run_openclaw_setup()."""

    @patch("shutil.which")
    def test_all_tools_present_no_keys(self, mock_which):
        """All CLI tools found, no API keys configured."""
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}"
        config = {}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["yt_dlp"] is True
        assert result["node"] is True
        assert result["python3"] is True
        assert all(v is False for v in result["keys"].values())
        assert result["x_method"] is None

    @patch("shutil.which")
    def test_missing_tools(self, mock_which):
        """Some CLI tools missing."""
        def which_side(cmd):
            if cmd == "node":
                return None
            return f"/usr/bin/{cmd}"
        mock_which.side_effect = which_side
        config = {}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["yt_dlp"] is True
        assert result["node"] is False
        assert result["python3"] is True

    @patch("shutil.which")
    def test_keys_detected(self, mock_which):
        """API keys in config are reported as present."""
        mock_which.return_value = None
        config = {
            "XAI_API_KEY": "xai-abc123",
            "BRAVE_API_KEY": "brav-xyz",
            "SCRAPECREATORS_API_KEY": "",  # empty = falsy
        }

        result = setup_wizard.run_openclaw_setup(config)

        assert result["keys"]["xai"] is True
        assert result["keys"]["brave"] is True
        assert result["keys"]["scrapecreators"] is False

    @patch("shutil.which")
    def test_x_method_xai(self, mock_which):
        """x_method is 'xai' when XAI_API_KEY is set."""
        mock_which.return_value = None
        config = {"XAI_API_KEY": "xai-key"}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["x_method"] == "xai"

    @patch("shutil.which")
    def test_x_method_cookies(self, mock_which):
        """x_method is 'cookies' when AUTH_TOKEN + CT0 are set."""
        mock_which.return_value = None
        config = {"AUTH_TOKEN": "tok", "CT0": "ct0val"}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["x_method"] == "cookies"

    @patch("shutil.which")
    def test_x_method_xai_over_cookies(self, mock_which):
        """XAI takes priority over cookies for x_method."""
        mock_which.return_value = None
        config = {"XAI_API_KEY": "xai-key", "AUTH_TOKEN": "tok", "CT0": "ct0val"}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["x_method"] == "xai"

    @patch("shutil.which")
    def test_x_method_null_when_nothing(self, mock_which):
        """x_method is None when no X access configured."""
        mock_which.return_value = None
        config = {}

        result = setup_wizard.run_openclaw_setup(config)

        assert result["x_method"] is None

    @patch("shutil.which")
    def test_output_is_json_serializable(self, mock_which):
        """Result can be serialized to JSON without errors."""
        mock_which.return_value = "/usr/bin/something"
        config = {"XAI_API_KEY": "k", "OPENAI_API_KEY": "ok"}

        result = setup_wizard.run_openclaw_setup(config)
        serialized = json.dumps(result)
        parsed = json.loads(serialized)

        assert parsed["yt_dlp"] is True
        assert parsed["keys"]["xai"] is True


class TestRunDeviceAuth:
    """Tests for run_device_auth()."""

    @patch("lib.setup_wizard.urlopen")
    def test_success(self, mock_urlopen):
        """Successful device code request returns tuple."""
        resp_data = {
            "device_code": "dc-123",
            "user_code": "ABCD-1234",
            "verification_uri": "https://github.com/login/device",
            "interval": 5,
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = setup_wizard.run_device_auth()

        assert result is not None
        device_code, user_code, verification_uri, interval = result
        assert device_code == "dc-123"
        assert user_code == "ABCD-1234"
        assert verification_uri == "https://github.com/login/device"
        assert interval == 5

    @patch("lib.setup_wizard.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        """HTTP error during code request returns None."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            "https://example.com", 500, "Server Error", {}, None
        )

        result = setup_wizard.run_device_auth()
        assert result is None

    @patch("lib.setup_wizard.urlopen")
    def test_missing_device_code_returns_none(self, mock_urlopen):
        """Incomplete response (no device_code) returns None."""
        resp_data = {"user_code": "ABCD-1234"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = setup_wizard.run_device_auth()
        assert result is None


class TestPollDeviceAuth:
    """Tests for poll_device_auth()."""

    @patch("lib.setup_wizard.time")
    @patch("lib.setup_wizard.urlopen")
    def test_success_on_second_poll(self, mock_urlopen, mock_time):
        """Returns access_token after initial pending then success."""
        # First call: time check (within deadline), second: after sleep, etc.
        mock_time.time = MagicMock(side_effect=[0, 0, 0, 0])
        mock_time.sleep = MagicMock()

        pending_resp = MagicMock()
        pending_resp.read.return_value = json.dumps({"error": "authorization_pending"}).encode()
        pending_resp.__enter__ = lambda s: s
        pending_resp.__exit__ = MagicMock(return_value=False)

        success_resp = MagicMock()
        success_resp.read.return_value = json.dumps({"access_token": "gho_abc123"}).encode()
        success_resp.__enter__ = lambda s: s
        success_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [pending_resp, success_resp]

        result = setup_wizard.poll_device_auth("dc-123", interval=1, timeout=300)
        assert result == "gho_abc123"

    @patch("lib.setup_wizard.time")
    @patch("lib.setup_wizard.urlopen")
    def test_timeout_returns_none(self, mock_urlopen, mock_time):
        """Returns None when timeout is exceeded."""
        # Simulate time passing beyond deadline
        mock_time.time = MagicMock(side_effect=[0, 301])
        mock_time.sleep = MagicMock()

        result = setup_wizard.poll_device_auth("dc-123", interval=5, timeout=300)
        assert result is None

    @patch("lib.setup_wizard.time")
    @patch("lib.setup_wizard.urlopen")
    def test_expired_token_returns_none(self, mock_urlopen, mock_time):
        """Returns None on expired_token error."""
        mock_time.time = MagicMock(side_effect=[0, 0])
        mock_time.sleep = MagicMock()

        expired_resp = MagicMock()
        expired_resp.read.return_value = json.dumps({"error": "expired_token"}).encode()
        expired_resp.__enter__ = lambda s: s
        expired_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.return_value = expired_resp

        result = setup_wizard.poll_device_auth("dc-123", interval=1, timeout=300)
        assert result is None

    @patch("lib.setup_wizard.time")
    @patch("lib.setup_wizard.urlopen")
    def test_http_400_continues_polling(self, mock_urlopen, mock_time):
        """HTTP 400 during polling continues (authorization pending)."""
        from urllib.error import HTTPError

        mock_time.time = MagicMock(side_effect=[0, 0, 0])
        mock_time.sleep = MagicMock()

        success_resp = MagicMock()
        success_resp.read.return_value = json.dumps({"access_token": "gho_ok"}).encode()
        success_resp.__enter__ = lambda s: s
        success_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [
            HTTPError("url", 400, "Bad Request", {}, None),
            success_resp,
        ]

        result = setup_wizard.poll_device_auth("dc-123", interval=1, timeout=300)
        assert result == "gho_ok"


class TestFetchApiKey:
    """Tests for fetch_api_key()."""

    @patch("lib.setup_wizard.urlopen")
    def test_success(self, mock_urlopen):
        """Returns api_key from profile response."""
        resp_data = {"api_key": "sc-key-abc123", "username": "testuser"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = setup_wizard.fetch_api_key("gho_token")
        assert result == "sc-key-abc123"

    @patch("lib.setup_wizard.urlopen")
    def test_no_api_key_in_response(self, mock_urlopen):
        """Returns None when api_key is not in the response."""
        resp_data = {"username": "testuser"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = setup_wizard.fetch_api_key("gho_token")
        assert result is None

    @patch("lib.setup_wizard.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        """HTTP error returns None."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            "https://example.com", 401, "Unauthorized", {}, None
        )

        result = setup_wizard.fetch_api_key("bad_token")
        assert result is None


class TestRunFullDeviceAuth:
    """Tests for run_full_device_auth()."""

    @patch("lib.setup_wizard.fetch_api_key")
    @patch("lib.setup_wizard.poll_device_auth")
    @patch("lib.setup_wizard.run_device_auth")
    @patch("webbrowser.open")
    def test_happy_path(self, mock_browser, mock_start, mock_poll, mock_fetch):
        """Full flow succeeds: start -> poll -> fetch -> return api_key."""
        mock_start.return_value = ("dev123", "ABCD-1234", "https://example.com/device", 5)
        mock_poll.return_value = "access_tok"
        mock_fetch.return_value = "sc_live_abc123"

        result = setup_wizard.run_full_device_auth(timeout=10)

        assert result["status"] == "success"
        assert result["api_key"] == "sc_live_abc123"
        assert result["user_code"] == "ABCD-1234"
        mock_browser.assert_called_once_with("https://example.com/device")

    @patch("lib.setup_wizard.run_device_auth")
    def test_start_fails(self, mock_start):
        """Device code request fails -> error status."""
        mock_start.return_value = None

        result = setup_wizard.run_full_device_auth()

        assert result["status"] == "error"
        assert "Failed to start" in result["message"]

    @patch("lib.setup_wizard.poll_device_auth")
    @patch("lib.setup_wizard.run_device_auth")
    @patch("webbrowser.open")
    def test_poll_timeout(self, mock_browser, mock_start, mock_poll):
        """Poll times out -> timeout status with user_code."""
        mock_start.return_value = ("dev123", "WXYZ-5678", "https://example.com/device", 5)
        mock_poll.return_value = None

        result = setup_wizard.run_full_device_auth(timeout=10)

        assert result["status"] == "timeout"
        assert result["user_code"] == "WXYZ-5678"

    @patch("lib.setup_wizard.fetch_api_key")
    @patch("lib.setup_wizard.poll_device_auth")
    @patch("lib.setup_wizard.run_device_auth")
    @patch("webbrowser.open")
    def test_fetch_fails_after_auth(self, mock_browser, mock_start, mock_poll, mock_fetch):
        """Auth succeeds but profile fetch fails -> error status."""
        mock_start.return_value = ("dev123", "CODE-1111", "https://example.com/device", 5)
        mock_poll.return_value = "access_tok"
        mock_fetch.return_value = None

        result = setup_wizard.run_full_device_auth(timeout=10)

        assert result["status"] == "error"
        assert "failed to fetch" in result["message"].lower()

    @patch("lib.setup_wizard.run_device_auth")
    @patch("webbrowser.open")
    def test_browser_open_fails_gracefully(self, mock_browser, mock_start):
        """webbrowser.open raises -> flow continues without crashing."""
        mock_start.return_value = ("dev123", "CODE-2222", "https://example.com/device", 5)
        mock_browser.side_effect = Exception("no display")

        with patch("lib.setup_wizard.poll_device_auth", return_value=None):
            result = setup_wizard.run_full_device_auth(timeout=1)

        # Should not crash, just timeout
        assert result["status"] == "timeout"

    @patch("lib.setup_wizard.run_device_auth")
    @patch("webbrowser.open")
    def test_no_verification_uri_skips_browser(self, mock_browser, mock_start):
        """Empty verification_uri -> browser not opened."""
        mock_start.return_value = ("dev123", "CODE-3333", "", 5)

        with patch("lib.setup_wizard.poll_device_auth", return_value=None):
            setup_wizard.run_full_device_auth(timeout=1)

        mock_browser.assert_not_called()


class TestAuthWithPat:
    """Tests for auth_with_pat()."""

    @patch("lib.setup_wizard.urlopen")
    def test_success(self, mock_urlopen):
        """Valid PAT -> returns dict with api_key."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({
            "api_key": "sc_live_test123",
            "github_username": "testuser",
            "credits_remaining": 100,
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = setup_wizard.auth_with_pat("gho_validtoken")

        assert result is not None
        assert result["api_key"] == "sc_live_test123"
        assert result["github_username"] == "testuser"

    @patch("lib.setup_wizard.urlopen")
    def test_invalid_token_returns_none(self, mock_urlopen):
        """HTTP 401 (invalid token) -> returns None."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.scrapecreators.com/v1/github/pat/auth",
            code=401, msg="Unauthorized", hdrs={}, fp=None,
        )

        result = setup_wizard.auth_with_pat("gho_badtoken")
        assert result is None

    @patch("lib.setup_wizard.urlopen")
    def test_insufficient_scope_returns_none(self, mock_urlopen):
        """HTTP 422 (insufficient scope) -> returns None."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.scrapecreators.com/v1/github/pat/auth",
            code=422, msg="Unprocessable", hdrs={}, fp=None,
        )

        result = setup_wizard.auth_with_pat("gho_noscope")
        assert result is None

    @patch("lib.setup_wizard.urlopen")
    def test_network_error_returns_none(self, mock_urlopen):
        """URLError -> returns None."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        result = setup_wizard.auth_with_pat("gho_anytoken")
        assert result is None

    @patch("lib.setup_wizard.urlopen")
    def test_no_api_key_in_response(self, mock_urlopen):
        """Response without api_key -> returns None."""
        resp = MagicMock()
        resp.read.return_value = json.dumps({"error": "something"}).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = setup_wizard.auth_with_pat("gho_validtoken")
        assert result is None


class TestClipboardDeviceAuth:
    """Tests for clipboard-first behavior in run_full_device_auth()."""

    @patch("lib.setup_wizard.run_device_auth")
    @patch("lib.setup_wizard.poll_device_auth", return_value=None)
    @patch("webbrowser.open")
    @patch("subprocess.run")
    def test_pbcopy_called_on_macos(self, mock_subproc, mock_browser, mock_poll, mock_start):
        """On macOS, pbcopy is called with the user code before browser opens."""
        mock_start.return_value = ("dev123", "CLIP-CODE", "https://github.com/login/device", 5)

        with patch("sys.platform", "darwin"):
            setup_wizard.run_full_device_auth(timeout=1)

        mock_subproc.assert_called_once()
        call_args = mock_subproc.call_args
        assert call_args[0][0] == ["pbcopy"]
        assert call_args[1]["input"] == b"CLIP-CODE"

    @patch("lib.setup_wizard.run_device_auth")
    @patch("lib.setup_wizard.poll_device_auth", return_value=None)
    @patch("webbrowser.open")
    @patch("subprocess.run")
    def test_no_pbcopy_on_linux(self, mock_subproc, mock_browser, mock_poll, mock_start):
        """On Linux, subprocess.run (pbcopy) is not called."""
        mock_start.return_value = ("dev123", "CLIP-CODE", "https://github.com/login/device", 5)

        with patch("sys.platform", "linux"):
            setup_wizard.run_full_device_auth(timeout=1)

        mock_subproc.assert_not_called()

    @patch("lib.setup_wizard.run_device_auth")
    @patch("lib.setup_wizard.poll_device_auth", return_value=None)
    @patch("webbrowser.open")
    @patch("subprocess.run", side_effect=Exception("pbcopy not found"))
    def test_pbcopy_failure_continues(self, mock_subproc, mock_browser, mock_poll, mock_start):
        """pbcopy failing -> flow continues, browser still opens."""
        mock_start.return_value = ("dev123", "CLIP-CODE", "https://github.com/login/device", 5)

        with patch("sys.platform", "darwin"):
            result = setup_wizard.run_full_device_auth(timeout=1)

        # Should not crash, browser still called
        mock_browser.assert_called_once()
        assert result["status"] == "timeout"


class TestRunGithubAuth:
    """Tests for run_github_auth() — PAT-first with device fallback."""

    @patch("lib.setup_wizard.auth_with_pat")
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/gh")
    def test_pat_success(self, mock_which, mock_subproc, mock_pat):
        """gh found + valid token + PAT endpoint success -> pat method."""
        mock_subproc.return_value = MagicMock(
            returncode=0, stdout="gho_testtoken123\n",
        )
        mock_pat.return_value = {
            "api_key": "sc_live_fromPAT",
            "github_username": "testuser",
        }

        result = setup_wizard.run_github_auth(timeout=10)

        assert result["status"] == "success"
        assert result["method"] == "pat"
        assert result["api_key"] == "sc_live_fromPAT"

    @patch("lib.setup_wizard.run_full_device_auth")
    @patch("lib.setup_wizard.auth_with_pat", return_value=None)
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/gh")
    def test_pat_fails_falls_to_device(self, mock_which, mock_subproc, mock_pat, mock_device):
        """gh found + PAT endpoint fails -> falls through to device flow."""
        mock_subproc.return_value = MagicMock(
            returncode=0, stdout="gho_badtoken\n",
        )
        mock_device.return_value = {
            "status": "success", "method": "device",
            "api_key": "sc_live_fromDevice",
        }

        result = setup_wizard.run_github_auth(timeout=10)

        assert result["status"] == "success"
        assert result["method"] == "device"
        mock_device.assert_called_once()

    @patch("lib.setup_wizard.run_full_device_auth")
    @patch("shutil.which", return_value=None)
    def test_no_gh_goes_to_device(self, mock_which, mock_device):
        """gh not installed -> straight to device flow."""
        mock_device.return_value = {
            "status": "success", "method": "device",
            "api_key": "sc_live_deviceOnly",
        }

        result = setup_wizard.run_github_auth(timeout=10)

        assert result["status"] == "success"
        assert result["method"] == "device"

    @patch("lib.setup_wizard.run_full_device_auth")
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/gh")
    def test_gh_not_logged_in_falls_to_device(self, mock_which, mock_subproc, mock_device):
        """gh exists but not logged in (exit code 1) -> device flow."""
        mock_subproc.return_value = MagicMock(
            returncode=1, stdout="", stderr="not logged in",
        )
        mock_device.return_value = {
            "status": "success", "method": "device",
            "api_key": "sc_live_fallback",
        }

        result = setup_wizard.run_github_auth(timeout=10)

        assert result["status"] == "success"
        assert result["method"] == "device"
