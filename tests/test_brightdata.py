"""Tests for the Bright Data CLI adapter (U1).

Covers the activation gate (PATH + presence-only credential probe), the
never-raise error envelope across every failure mode, and the verbatim
passthrough of the CLI's own auth/credit warnings.

No test in this file spawns a real subprocess or touches the network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))

from lib import brightdata, subproc  # noqa: E402


class _Result:
    """Stand-in for subproc.SubprocResult."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# One real search record, trimmed. Field names verified against a live
# amazon_product_search payload on 2026-08-13.
SEARCH_RECORD = {
    "asin": "B0DWD47BW7",
    "url": "https://www.amazon.com/dp/B0DWD47BW7",
    "name": "Chill Max Leak-Proof XL Bento-Style Lunch Box | Ice Pack Included",
    "brand": "Bentgo",
    "sponsored": "false",
    "rating": 4.4,
    "num_ratings": 459,
    "final_price": 39.99,
    "currency": "USD",
}


# ---------------------------------------------------------------- gate


def test_gate_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: None)
    assert brightdata.is_installed() is False
    assert brightdata.is_available({}) is False


def test_gate_reports_unauthenticated_when_no_credential_signal(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")
    monkeypatch.setattr(brightdata, "_config_dir", lambda: tmp_path / "absent")
    status = brightdata.gate_status({})
    assert status["brightdata_installed"] is True
    assert status["brightdata_authenticated"] is False
    assert brightdata.is_available({}) is False


def test_api_key_alone_satisfies_the_credential_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")
    monkeypatch.setattr(brightdata, "_config_dir", lambda: tmp_path / "absent")
    assert brightdata.is_available({"BRIGHTDATA_API_KEY": "dummy-key-not-real"}) is True


def test_credentials_file_satisfies_the_probe_without_being_read(monkeypatch, tmp_path):
    cfg = tmp_path / "brightdata-cli"
    cfg.mkdir()
    creds = cfg / "credentials.json"
    creds.write_text('{"token": "dummy-not-real"}')
    creds.chmod(0o600)
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")
    monkeypatch.setattr(brightdata, "_config_dir", lambda: cfg)

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("credential contents must never be read")

    monkeypatch.setattr(Path, "read_text", _explode)
    assert brightdata.has_credentials({}) is True


def test_gate_does_not_spawn_a_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: None)

    def _explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("gate must stay offline")

    monkeypatch.setattr(subproc, "run_with_timeout", _explode)
    brightdata.gate_status({})
    assert brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)["records"] == []


# ------------------------------------------------------------ envelope


def _installed(monkeypatch):
    monkeypatch.setattr(brightdata.shutil, "which", lambda _: "/usr/local/bin/brightdata")


def test_happy_path_parses_a_bare_json_array(monkeypatch):
    """Live payloads are a top-level list, not a {"results": ...} envelope."""
    _installed(monkeypatch)
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda *a, **k: _Result(stdout=json.dumps([SEARCH_RECORD, SEARCH_RECORD])),
    )
    out = brightdata.run_pipeline("amazon_product_search", ["bentgo", "https://www.amazon.com"], timeout=60)
    assert "error" not in out
    assert len(out["records"]) == 2
    assert out["records"][0]["asin"] == "B0DWD47BW7"


@pytest.mark.parametrize("wrapper", ["records", "results", "data"])
def test_dict_envelopes_are_tolerated_for_cli_churn(monkeypatch, wrapper):
    _installed(monkeypatch)
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda *a, **k: _Result(stdout=json.dumps({wrapper: [SEARCH_RECORD]})),
    )
    assert len(brightdata.run_pipeline("amazon_product", ["u"], timeout=30)["records"]) == 1


def test_auth_401_surfaces_the_cli_error_without_raising(monkeypatch):
    _installed(monkeypatch)
    stderr = "Triggering pipeline collection...\nError: 401 Unauthorized - run `brightdata login`"
    monkeypatch.setattr(
        subproc, "run_with_timeout", lambda *a, **k: _Result(returncode=1, stderr=stderr)
    )
    out = brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
    assert out["records"] == []
    assert "401" in out["error"]


def test_error_uses_the_last_stderr_line_not_the_polling_narration(monkeypatch):
    """The CLI narrates polling on stderr; the failure is the final line."""
    _installed(monkeypatch)
    stderr = (
        "Triggering pipeline collection for amazon_product_reviews...\n"
        "Status: running - polling again (attempt 1/600)\n"
        "Error: snapshot failed"
    )
    monkeypatch.setattr(
        subproc, "run_with_timeout", lambda *a, **k: _Result(returncode=1, stderr=stderr)
    )
    assert brightdata.run_pipeline("amazon_product_reviews", ["u", "50"], timeout=30)["error"] == "Error: snapshot failed"


def test_subprocess_timeout_returns_empty_records_and_error(monkeypatch):
    _installed(monkeypatch)

    def _timeout(*a, **k):
        raise subproc.SubprocTimeout("Command brightdata timed out after 180s")

    monkeypatch.setattr(subproc, "run_with_timeout", _timeout)
    out = brightdata.run_pipeline("amazon_product_reviews", ["u", "50"], timeout=180)
    assert out["records"] == []
    assert "timed out" in out["error"]


def test_malformed_json_returns_empty_records_and_error(monkeypatch):
    _installed(monkeypatch)
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: _Result(stdout="not json{"))
    out = brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
    assert out["records"] == []
    assert "json decode" in out["error"]


def test_empty_stdout_is_not_an_error(monkeypatch):
    _installed(monkeypatch)
    monkeypatch.setattr(subproc, "run_with_timeout", lambda *a, **k: _Result(stdout="  \n"))
    out = brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
    assert out["records"] == []
    assert "error" not in out


def test_spawn_failures_never_raise(monkeypatch):
    _installed(monkeypatch)
    for exc in (FileNotFoundError("no binary"), OSError("spawn failed")):
        def _raise(*a, __e=exc, **k):
            raise __e

        monkeypatch.setattr(subproc, "run_with_timeout", _raise)
        out = brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
        assert out["records"] == [] and out["error"]


# ------------------------------------------------------------ arguments


def test_cli_timeout_sits_below_the_subprocess_timeout(monkeypatch):
    """The CLI must fail cleanly on its own before we SIGTERM it."""
    _installed(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda cmd, **k: seen.update(cmd=cmd, timeout=k["timeout"]) or _Result(stdout="[]"),
    )
    brightdata.run_pipeline("amazon_product_reviews", ["u", "50"], timeout=180)
    cli_timeout = int(seen["cmd"][seen["cmd"].index("--timeout") + 1])
    assert cli_timeout < seen["timeout"] == 180


def test_params_are_passed_positionally_in_order(monkeypatch):
    _installed(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda cmd, **k: seen.update(cmd=cmd) or _Result(stdout="[]"),
    )
    brightdata.run_pipeline("amazon_product_reviews", ["https://a/dp/X", 50], timeout=60)
    cmd = seen["cmd"]
    assert cmd[:3] == ["brightdata", "pipelines", "amazon_product_reviews"]
    # Positionals sit after the option fence, in the order supplied.
    assert cmd[cmd.index("--") + 1:] == ["https://a/dp/X", "50"]
    assert "--json" in cmd


def test_api_key_travels_in_the_environment_never_in_argv(monkeypatch):
    """argv is not a secret channel: /proc/<pid>/cmdline is world-readable."""
    _installed(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda cmd, **k: seen.update(cmd=cmd, env=k.get("env")) or _Result(stdout="[]"),
    )
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    brightdata.run_pipeline(
        "amazon_product", ["u"], timeout=30, config={"BRIGHTDATA_API_KEY": "dummy-key"}
    )
    assert "-k" not in seen["cmd"]
    assert "dummy-key" not in " ".join(seen["cmd"])
    assert seen["env"]["BRIGHTDATA_API_KEY"] == "dummy-key"


def test_no_key_means_the_child_simply_inherits_the_parent_env(monkeypatch):
    _installed(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda cmd, **k: seen.update(env=k.get("env")) or _Result(stdout="[]"),
    )
    brightdata.run_pipeline("amazon_product", ["u"], timeout=30, config={})
    assert seen["env"] is None


def test_positional_params_are_fenced_behind_a_double_dash(monkeypatch):
    """A keyword beginning with '-' must not be parsed as an option."""
    _installed(monkeypatch)
    seen = {}
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda cmd, **k: seen.update(cmd=cmd) or _Result(stdout="[]"),
    )
    brightdata.run_pipeline("amazon_product_search", ["--help", "https://a"], timeout=30)
    cmd = seen["cmd"]
    assert "--" in cmd
    assert cmd.index("--") < cmd.index("--help")


def test_the_key_is_scrubbed_out_of_surfaced_stderr(monkeypatch):
    """Auth failures are exactly where a CLI echoes the rejected key back."""
    _installed(monkeypatch)
    logged = []
    monkeypatch.setattr(brightdata.log, "source_log", lambda n, m, **k: logged.append(m))
    monkeypatch.setattr(
        subproc, "run_with_timeout",
        lambda *a, **k: _Result(returncode=1, stderr="Error: 401 for key dummy-key"),
    )
    out = brightdata.run_pipeline(
        "amazon_product", ["u"], timeout=30, config={"BRIGHTDATA_API_KEY": "dummy-key"}
    )
    assert "dummy-key" not in out["error"]
    assert not any("dummy-key" in m for m in logged)


# ------------------------------------------------------------- warnings


def test_credit_and_auth_warnings_pass_through_verbatim(monkeypatch):
    _installed(monkeypatch)
    warned = []
    monkeypatch.setattr(brightdata.log, "source_log", lambda name, msg, **k: warned.append(msg))
    stderr = (
        "Triggering pipeline collection for amazon_product_search...\n"
        "Status: running - polling again (attempt 1/600)\n"
        "Warning: only 12 credits remaining on your free tier\n"
    )
    monkeypatch.setattr(
        subproc, "run_with_timeout", lambda *a, **k: _Result(stdout="[]", stderr=stderr)
    )
    brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
    assert "Warning: only 12 credits remaining on your free tier" in warned
    # Routine polling narration stays out of the log.
    assert not any("polling again" in m for m in warned)


def test_source_log_is_called_with_tty_only_false(monkeypatch):
    """Repo rule: tty_only=True silently drops output in non-TTY hosts."""
    _installed(monkeypatch)
    kwargs = {}
    monkeypatch.setattr(
        brightdata.log, "source_log", lambda name, msg, **k: kwargs.update(k)
    )
    monkeypatch.setattr(
        subproc, "run_with_timeout", lambda *a, **k: _Result(returncode=1, stderr="Error: 401")
    )
    brightdata.run_pipeline("amazon_product_search", ["x"], timeout=30)
    assert kwargs.get("tty_only") is False
