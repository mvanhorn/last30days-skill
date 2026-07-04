"""Tests for the ScrapeCreators GitHub device-auth flow.

Covers the U4/U5/U6 fixes: the device code is surfaced on stdout as an early
structured line, a non-device-shaped user_code is never copied/labeled/emitted,
and an already-registered account short-circuits without a fresh device dance.

All hermetic — the network, clipboard, and browser are patched.
"""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

from lib import setup_wizard


class TestUserCodeValidation:
    """U5: user_code must match the XXXX-XXXX device-code shape."""

    @patch("lib.setup_wizard.subprocess.run")
    @patch("lib.setup_wizard.run_device_auth")
    def test_malformed_user_code_is_rejected(self, mock_run_device, mock_subprocess_run):
        # mock_subprocess_run patches setup_wizard.subprocess.run; the only such
        # call in run_full_device_auth is the pbcopy of the code.
        # A key-shaped value (no dash, 28 chars) must never be treated as a code.
        mock_run_device.return_value = (
            "dev-code-123",
            "m08LboBUJpRz82AMyuCWP9sqwnk2",
            "https://github.com/login/device",
            5,
        )
        out = io.StringIO()
        with redirect_stdout(out):
            result = setup_wizard.run_full_device_auth(timeout=1)

        assert result["status"] == "error"
        # Not copied to the clipboard (no pbcopy subprocess call)...
        mock_subprocess_run.assert_not_called()
        # ...and never emitted as a device_code_ready line.
        assert "device_code_ready" not in out.getvalue()
        assert "m08LboBUJpRz82AMyuCWP9sqwnk2" not in out.getvalue()


class TestDeviceCodeReadyEmission:
    """U4: a validated code is emitted to stdout before polling."""

    @patch("webbrowser.open")
    @patch("lib.setup_wizard.subprocess.run")
    @patch("lib.setup_wizard.poll_device_auth")
    @patch("lib.setup_wizard.run_device_auth")
    def test_valid_code_emitted_to_stdout(
        self, mock_run_device, mock_poll, mock_pbcopy, mock_browser
    ):
        mock_run_device.return_value = (
            "dev-code-123",
            "819B-F71B",
            "https://github.com/login/device",
            5,
        )
        mock_poll.return_value = None  # simulate timeout, no token

        out = io.StringIO()
        with redirect_stdout(out):
            result = setup_wizard.run_full_device_auth(timeout=1)

        lines = [ln for ln in out.getvalue().splitlines() if ln.strip()]
        ready = [json.loads(ln) for ln in lines if "device_code_ready" in ln]
        assert ready, "expected a device_code_ready stdout line"
        assert ready[0]["user_code"] == "819B-F71B"
        assert ready[0]["verification_uri"] == "https://github.com/login/device"
        # The final status is still returned (timeout here).
        assert result["status"] == "timeout"


class TestAlreadyRegistered:
    """An existing key short-circuits run_github_start without the device dance."""

    @patch("lib.setup_wizard.run_device_auth")
    @patch("lib.setup_wizard._existing_scrapecreators_key")
    def test_existing_key_short_circuits_start(self, mock_existing, mock_device):
        mock_existing.return_value = "sc_live_realkey1234567890"

        result = setup_wizard.run_github_start()

        assert result["status"] == "already_registered"
        assert result["persisted"] is True
        mock_device.assert_not_called()  # no /code submit, no browser

    @patch("webbrowser.open")
    @patch("lib.setup_wizard.subprocess.run")
    @patch("lib.setup_wizard.run_device_auth")
    @patch("lib.setup_wizard._existing_scrapecreators_key")
    def test_no_existing_key_starts_device_flow(
        self, mock_existing, mock_device, mock_pbcopy, mock_browser
    ):
        mock_existing.return_value = None
        mock_device.return_value = ("dev-code", "819B-F71B", "https://github.com/login/device", 5)

        out = io.StringIO()
        with redirect_stdout(out):
            result = setup_wizard.run_github_start()

        assert result["status"] == "awaiting_authorization"
        assert result["user_code"] == "819B-F71B"
        mock_device.assert_called_once()
        # The code is printed to stdout as a plain human line (the whole point).
        assert "819B-F71B" in out.getvalue()

    @patch("lib.setup_wizard._device_handle_path")
    @patch("lib.setup_wizard.fetch_api_key")
    @patch("lib.setup_wizard.poll_device_auth")
    def test_poll_reads_handle_and_returns_key(self, mock_poll, mock_fetch, mock_handle, tmp_path):
        import json as _json
        handle = tmp_path / "h.json"
        handle.write_text(_json.dumps({"device_code": "dc", "interval": 1, "user_code": "819B-F71B"}))
        mock_handle.return_value = handle
        mock_poll.return_value = "access-token"
        mock_fetch.return_value = "sc_polled_key"

        result = setup_wizard.run_github_poll(timeout=1)

        assert result["status"] == "success"
        assert result["api_key"] == "sc_polled_key"
        assert not handle.exists()  # handle cleaned up

    @patch("lib.setup_wizard._device_handle_path")
    def test_poll_without_handle_errors_cleanly(self, mock_handle, tmp_path):
        mock_handle.return_value = tmp_path / "missing.json"
        result = setup_wizard.run_github_poll(timeout=1)
        assert result["status"] == "error"
        assert "github-start" in result["message"]

    @patch("lib.setup_wizard._device_handle_path")
    @patch("lib.setup_wizard.fetch_api_key")
    @patch("lib.setup_wizard.poll_device_auth")
    def test_poll_passes_real_clipboard_state(self, mock_poll, mock_fetch, mock_handle, tmp_path):
        """clipboard_ok is read from the handle, not hardcoded True, so the poll
        reminder never falsely claims the code is on the clipboard."""
        import json as _json
        handle = tmp_path / "h.json"
        handle.write_text(
            _json.dumps({"device_code": "dc", "interval": 1, "user_code": "819B-F71B", "clipboard_ok": False})
        )
        mock_handle.return_value = handle
        mock_poll.return_value = "tok"
        mock_fetch.return_value = "sc_k"

        setup_wizard.run_github_poll(timeout=1)

        assert mock_poll.call_args.kwargs["clipboard_ok"] is False

    @patch("lib.setup_wizard._device_handle_path")
    @patch("lib.setup_wizard.fetch_api_key")
    @patch("lib.setup_wizard.poll_device_auth")
    @patch("lib.setup_wizard._start_device_flow")
    def test_oneshot_uses_in_memory_handle_when_file_write_fails(
        self, mock_start, mock_poll, mock_fetch, mock_handle, tmp_path
    ):
        """run_full_device_auth hands the handle to poll in-memory, so a broken
        handle-file path can't strand the one-shot."""
        mock_handle.return_value = tmp_path / "does-not-exist" / "h.json"  # unwritable/unreadable
        mock_start.return_value = (
            {"status": "awaiting_authorization", "user_code": "819B-F71B"},
            {"device_code": "dc", "interval": 1, "user_code": "819B-F71B", "clipboard_ok": True},
        )
        mock_poll.return_value = "tok"
        mock_fetch.return_value = "sc_oneshot_key"

        result = setup_wizard.run_full_device_auth(timeout=1)

        assert result["status"] == "success"
        assert result["api_key"] == "sc_oneshot_key"
        mock_poll.assert_called_once()  # reached poll without a readable handle file

    def test_already_registered_key_is_masked_before_output(self):
        # Defense-in-depth: the mask helper must not echo the raw key.
        masked = setup_wizard.mask_api_key("sc_live_realkey1234567890")
        assert "realkey" not in masked
        assert masked != "sc_live_realkey1234567890"


class TestFetchApiKeyLogging:
    """U4: the /profile no-key path logs field NAMES only, never values."""

    @patch("lib.setup_wizard.logger")
    @patch("lib.setup_wizard.urlopen")
    def test_no_key_logs_field_names_not_values(self, mock_urlopen, mock_logger):
        # An already-linked account: /profile parses but carries no api_key,
        # and could carry a secret under another field (here, "token").
        body = json.dumps({"linked": True, "token": "sc_secret_value_xyz"}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        result = setup_wizard.fetch_api_key("gh_access_token")

        assert result is None
        # The warning must include the field names but never the secret value.
        logged = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "linked" in logged and "token" in logged
        assert "sc_secret_value_xyz" not in logged
