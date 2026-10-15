"""U1/U5: agentcookie sidecar reader (lib/agentcookie.py).

Subprocess is always mocked; only obvious dummy cookie values are used
(test-auth-token / test-ct0). Enforces the soft-dep, AGENTCOOKIE=off,
FROM_BROWSER-independence, complete-pair, and no-value-logging contracts.
"""

import json
import subprocess
from unittest import mock

from lib import agentcookie

_DUMMY = {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def _run_ok(stdout):
    return mock.Mock(returncode=0, stdout=stdout, stderr="")


def test_off_disables_without_subprocess():
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", side_effect=AssertionError("should not run")),
    ):
        assert agentcookie.read_x_cookies({"AGENTCOOKIE": "off"}) is None
    assert agentcookie.is_disabled({"AGENTCOOKIE": "off"}) is True
    assert agentcookie.is_available({"AGENTCOOKIE": "off"}) is False


def test_absent_binary_is_soft_skip():
    with mock.patch("shutil.which", return_value=None):
        assert agentcookie.read_x_cookies({}) is None
        assert agentcookie.is_available({}) is False


def test_complete_pair_from_list_json():
    payload = json.dumps([
        {"name": "auth_token", "value": "test-auth-token", "domain": ".x.com"},
        {"name": "ct0", "value": "test-ct0", "domain": ".x.com"},
        {"name": "guest_id", "value": "irrelevant", "domain": ".x.com"},
    ])
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok(payload)) as run,
    ):
        result = agentcookie.read_x_cookies({})
    assert result == _DUMMY
    # It asks for the x.com domain in JSON.
    args = run.call_args[0][0]
    assert "--domain" in args and ".x.com" in args and "--json" in args


def test_complete_pair_from_cookies_wrapper():
    payload = json.dumps({"cookies": [
        {"name": "auth_token", "value": "test-auth-token"},
        {"name": "ct0", "value": "test-ct0"},
    ]})
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok(payload)),
    ):
        assert agentcookie.read_x_cookies({}) == _DUMMY


def test_complete_pair_from_flat_mapping():
    payload = json.dumps({"auth_token": "test-auth-token", "ct0": "test-ct0"})
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok(payload)),
    ):
        assert agentcookie.read_x_cookies({}) == _DUMMY


def test_half_pair_is_rejected():
    payload = json.dumps([{"name": "auth_token", "value": "test-auth-token"}])
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok(payload)),
    ):
        assert agentcookie.read_x_cookies({}) is None


def test_nonzero_exit_is_none():
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=mock.Mock(returncode=2, stdout="", stderr="nope")),
    ):
        assert agentcookie.read_x_cookies({}) is None


def test_timeout_is_none():
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("agentcookie", 10)),
    ):
        assert agentcookie.read_x_cookies({}) is None


def test_non_json_is_none():
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok("<html>blocked</html>")),
    ):
        assert agentcookie.read_x_cookies({}) is None


def test_never_logs_cookie_values():
    payload = json.dumps({"auth_token": "test-auth-token", "ct0": "test-ct0"})
    logged = []
    with (
        mock.patch("shutil.which", return_value="/usr/bin/agentcookie"),
        mock.patch("subprocess.run", return_value=_run_ok(payload)),
        mock.patch("lib.agentcookie.log.source_log", lambda src, msg, **k: logged.append(msg)),
    ):
        agentcookie.read_x_cookies({})
    joined = "\n".join(logged)
    assert "test-auth-token" not in joined
    assert "test-ct0" not in joined


# --- role detection (subprocess-free config read) --------------------------


def test_role_is_sink_reads_config_file(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"role": "sink"}))
    config = {"AGENTCOOKIE_CONFIG": str(cfg)}
    # No subprocess is spawned to classify the role.
    with mock.patch("subprocess.run", side_effect=AssertionError("no subprocess for role")):
        assert agentcookie.role(config) == "sink"
        assert agentcookie.role_is_sink(config) is True


def test_role_source_is_not_sink(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"role": "source"}))
    config = {"AGENTCOOKIE_CONFIG": str(cfg)}
    assert agentcookie.role_is_sink(config) is False


def test_role_missing_or_unparsable_is_not_sink(tmp_path):
    missing = {"AGENTCOOKIE_CONFIG": str(tmp_path / "nope.json")}
    assert agentcookie.role_is_sink(missing) is False
    bad = tmp_path / "bad.json"
    bad.write_text("not json {")
    assert agentcookie.role_is_sink({"AGENTCOOKIE_CONFIG": str(bad)}) is False
