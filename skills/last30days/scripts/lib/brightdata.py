"""Bright Data CLI adapter for last30days.

Shells out to the ``brightdata`` CLI (``@brightdata/cli``) to run Bright
Data Pipelines. The CLI owns authentication end to end -- ``brightdata
login`` does a gh-style zero-click browser flow and stores credentials in
a platform config directory -- so this module never handles a login, and
never reads credential *contents*: the auth probe is presence-only.

Activation gate: two-way, mirroring the digg CLI-gated precedent but with
an auth dimension the digg source does not have.

1. ``shutil.which("brightdata")`` must resolve on the **agent subprocess
   PATH** (not merely exist on disk -- Hermes/OpenClaw gateways often drop
   ``~/.local/bin``).
2. A credential signal must be present: either ``BRIGHTDATA_API_KEY``
   resolved through the normal config layering, or the CLI's own
   credentials file in the platform config dir.

The second check is deliberately offline. A stale token passes it and
then 401s fast at call time; that path degrades to empty results with the
CLI's own error line preserved in the envelope, which is the AE2 contract.

Metering note (R13): no pricing logic lives here. One pipeline request
costs one credit against the account's monthly free tier regardless of how
many records come back, so caps in the calling adapter bound *records*
(paid-tier cost), not credits. Credit and auth warnings from the CLI are
passed through verbatim rather than interpreted.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import log, subproc


CLI_BIN = "brightdata"

# Env var carrying an explicit API key. Registered in env.py so `.env` file
# and keychain users pass the gate the same way process-env users do; when
# it resolves from a non-process-env layer we hand it to the CLI via -k.
API_KEY_ENV = "BRIGHTDATA_API_KEY"

# Credentials filename written by `brightdata login`. Probed for existence
# only -- never opened, parsed, or logged.
_CREDENTIALS_FILENAME = "credentials.json"
_CONFIG_DIRNAME = "brightdata-cli"

# The CLI's own polling timeout sits below our subprocess timeout so the CLI
# exits cleanly with its own error rather than being SIGTERM'd mid-poll. Its
# timeout path throws with zero records (verified in its polling module --
# never partial output), so a timed-out pull is a clean parseable failure.
_CLI_TIMEOUT_MARGIN = 10


def _log(msg: str) -> None:
    log.source_log("BrightData", msg, tty_only=False)


def _config_dir() -> Path:
    """Platform config directory the Bright Data CLI stores credentials in.

    Mirrors the CLI's own credentials module: APPDATA on Windows, the
    Application Support tree on macOS, XDG_CONFIG_HOME (or ~/.config) on
    everything else.
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / _CONFIG_DIRNAME


def is_installed() -> bool:
    """True when the brightdata binary resolves on the agent subprocess PATH."""
    return shutil.which(CLI_BIN) is not None


def _api_key(config: Optional[Dict[str, Any]]) -> str:
    if not config:
        return ""
    return str(config.get(API_KEY_ENV) or "").strip()


def has_credentials(config: Optional[Dict[str, Any]] = None) -> bool:
    """True when some credential signal exists, without reading any secret.

    Presence-only by design: an explicit API key resolved through config
    layering, or the existence of the CLI's credentials file. The file is
    never opened. This cannot distinguish a live token from an expired one
    -- that is what the fast 401 at call time is for.
    """
    if _api_key(config):
        return True
    try:
        return (_config_dir() / _CREDENTIALS_FILENAME).exists()
    except OSError:
        return False


def is_available(config: Optional[Dict[str, Any]] = None) -> bool:
    """The full activation gate: binary on PATH *and* a credential signal."""
    return is_installed() and has_credentials(config)


def gate_status(config: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    """Two-field probe for ``pipeline.diagnose`` (bird_installed precedent).

    Network-free, so it is safe on the ``--diagnose`` / doctor path.
    """
    installed = is_installed()
    return {
        "brightdata_installed": installed,
        "brightdata_authenticated": installed and has_credentials(config),
    }


def _build_args(
    pipeline_type: str,
    params: Sequence[str],
    *,
    cli_timeout: int,
) -> List[str]:
    """Assemble the CLI invocation.

    The API key is deliberately **absent** here -- it travels in the child's
    environment instead (see ``_child_env``). Process arguments are not a
    secret channel: ``/proc/<pid>/cmdline`` is world-readable under the
    default ``hidepid=0``, and a review pull lives for up to 180s, so a key
    on the command line is readable by any other local user and is captured
    verbatim by execve auditing, process accounting, and any monitoring
    agent that snapshots ``ps``. Mirrors the ``bird_x`` cookie-injection
    precedent.

    Positional params are fenced behind ``--`` so a keyword that happens to
    begin with a dash is parsed as a search term rather than as an option.
    """
    return [
        CLI_BIN,
        "pipelines",
        pipeline_type,
        "--json",
        "--timeout",
        str(cli_timeout),
        "--",
        *(str(p) for p in params),
    ]


def _child_env(api_key: str) -> Optional[Dict[str, str]]:
    """Environment for the child process, carrying the key when we have one.

    Returns None when there is nothing to inject, so the child simply
    inherits the parent environment (the common case: the CLI owns its own
    credentials file, or the key is already exported).
    """
    if not api_key:
        return None
    return {**os.environ, API_KEY_ENV: api_key}


def _scrub(text: str, secret: str) -> str:
    """Remove a secret from text before it is logged or returned.

    Defense in depth for the passthrough paths: the stderr lines this
    module deliberately surfaces are auth and quota failures, which are
    exactly the messages a CLI is most likely to echo the rejected
    credential back in.
    """
    if not secret or not text:
        return text
    return text.replace(secret, "***")


def _extract_records(payload: Any) -> List[Dict[str, Any]]:
    """Pull the record list out of a parsed CLI payload.

    Verified live (2026-08-13): both amazon pipelines return a **bare JSON
    array** of flat record dicts, not the ``{"results": [...]}`` envelope the
    digg CLI uses. The dict branches below are defensive against CLI churn,
    which is a live risk on a package this young.
    """
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("records", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def run_pipeline(
    pipeline_type: str,
    params: Sequence[str],
    *,
    timeout: int,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one Bright Data pipeline and return ``{"records", "error"}``.

    Never raises. Every failure mode -- missing binary, spawn failure,
    subprocess timeout, non-zero exit, unparseable stdout -- returns empty
    records plus a one-line ``error`` string, so callers can record the
    failure in ``errors_by_source`` without branching on exception types.

    The CLI's first stderr line is preserved verbatim as the error (auth
    401s and low-credit warnings are the cases that matter), and also
    mirrored to ``source_log`` so the failure is visible in non-TTY hosts.

    Args:
        pipeline_type: pipeline name, e.g. ``amazon_product_search``.
        params: positional pipeline params, passed through in order.
        timeout: subprocess timeout in seconds. The CLI's own polling
            timeout is set just below this so it can fail cleanly first.
        config: resolved config dict, consulted only for the API key.

    Returns:
        ``{"records": [...]}`` on success, else ``{"records": [], "error": str}``.
    """
    if not is_installed():
        return {"records": [], "error": f"{CLI_BIN} not on PATH"}

    cli_timeout = max(5, int(timeout) - _CLI_TIMEOUT_MARGIN)
    key = _api_key(config)
    cmd = _build_args(pipeline_type, params, cli_timeout=cli_timeout)

    try:
        result = subproc.run_with_timeout(cmd, timeout=timeout, env=_child_env(key))
    except subproc.SubprocTimeout as exc:
        _log(f"Timeout: {exc}")
        return {"records": [], "error": str(exc)}
    except FileNotFoundError as exc:
        _log(f"Binary missing: {exc}")
        return {"records": [], "error": str(exc)}
    except OSError as exc:
        _log(f"Spawn failed: {exc}")
        return {"records": [], "error": str(exc)}

    stderr = _scrub(result.stderr or "", key)
    _passthrough_warnings(stderr)

    if result.returncode != 0:
        lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
        # The CLI narrates polling progress on stderr, so the *last* line is
        # the actual failure; the first line is "Triggering pipeline...".
        first = lines[-1] if lines else f"exit {result.returncode}"
        _log(f"CLI exit {result.returncode}: {first}")
        return {"records": [], "error": first}

    stdout = result.stdout or ""
    if not stdout.strip():
        return {"records": []}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        _log(f"JSON decode failed: {exc}")
        return {"records": [], "error": f"json decode: {exc}"}

    return {"records": _extract_records(payload)}


# Substrings that mark a stderr line worth surfacing even on a successful
# run -- credit exhaustion and auth trouble are the two the user must see.
# Matched case-insensitively against the CLI's own wording, and echoed
# verbatim rather than reworded (R13: no pricing logic, no interpretation).
_WARNING_MARKERS = ("credit", "quota", "balance", "unauthor", "401", "expired", "login")


def _passthrough_warnings(stderr: str) -> None:
    """Echo credit/auth warning lines from the CLI verbatim.

    Skips the routine polling narration so a normal run stays quiet.
    """
    for line in (stderr or "").splitlines():
        text = line.strip()
        if not text or text.lower().startswith(("status:", "triggering", "triggered", "data received")):
            continue
        lowered = text.lower()
        if any(marker in lowered for marker in _WARNING_MARKERS):
            _log(text)
