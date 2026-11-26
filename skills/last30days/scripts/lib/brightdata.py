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
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import quote as _url_quote

from . import log, subproc, usage


CLI_BIN = "brightdata"

# Env var carrying an explicit API key. Registered in env.py so `.env` file
# and keychain users pass the gate the same way process-env users do; when
# it resolves from a non-process-env layer we pass it in the child environment.
API_KEY_ENV = "BRIGHTDATA_API_KEY"

# Keep only runtime, credential-file location and network configuration.
# In particular, do not inherit other providers' secrets or NODE_OPTIONS.
_CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "XDG_CONFIG_HOME", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE",
    "COMSPEC", "PATHEXT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE",
    "NODE_EXTRA_CA_CERTS", "NODE_USE_ENV_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})

# Credentials filename written by `brightdata login`. Probed for existence
# only -- never opened, parsed, or logged.
_CREDENTIALS_FILENAME = "credentials.json"
_CONFIG_DIRNAME = "brightdata-cli"

# The CLI's own polling timeout sits below our subprocess timeout so the CLI
# exits cleanly with its own error rather than being SIGTERM'd mid-poll. Its
# timeout path throws with zero records (verified in its polling module --
# never partial output), so a timed-out pull is a clean parseable failure.
_CLI_TIMEOUT_MARGIN = 10

_METHOD = "collect_by_url"

# Printed by pre-0.4 CLIs, which have no `pipelines run` or `--input`.
_OLD_CLI_MARKERS = ("unknown option '--input'", "Unknown pipeline type")
_UPGRADE_HINT = "brightdata CLI is older than 0.4.0; upgrade with: npm i -g @brightdata/cli@latest"


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


# Pattern-based redaction for credentials echoed by the CLI on stderr even
# when authenticated via credentials.json rather than BRIGHTDATA_API_KEY.
_URL_USERINFO_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,15}://)[^\s/?#]+@")
_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[^\s,;\"']+")
_BASIC_AUTH_RE = re.compile(r"(?i)\b(Authorization\s*:\s*Basic)\s+[^\s,;\"']+")
_JSON_SECRET_RE = re.compile(
    r'(?i)(["\'](?:[A-Za-z0-9_-]*[_-])?(?:api[_-]?key|access[_-]?token|auth[_-]?token|token|authorization)["\']\s*:\s*)'
    r'(?:"(?:[^"\\\r\n]|\\.)*"|\'(?:[^\'\\\r\n]|\\.)*\')'
)
_KV_SECRET_RE = re.compile(
    r'(?i)((?<![A-Za-z0-9])(?:api[_-]?key|access[_-]?token|auth[_-]?token|token)\s*[:=]\s*)'
    r'(?:"(?:[^"\\\r\n]|\\.)*"|\'(?:[^\'\\\r\n]|\\.)*\'|[^\s,;\"\']+)'
)
_SPACED_SECRET_RE = re.compile(
    r"(?i)((?<![A-Za-z0-9-])(?:api[_-]?key|access[_-]?token|auth[_-]?token|token)\s+)"
    r"(?!(?:is|was|are|has|have|expired|invalid|missing|required|rejected|"
    r"denied|failed|error|not|for|from|in|to|of)\b)"
    r"(?:\"(?:[^\"\\\r\n]|\\.){4,}\"|'(?:[^'\\\r\n]|\\.){4,}'|[^\s,;\"']{6,})"
)

# Windows batch shims (.cmd/.bat) trigger cmd.exe via CreateProcessW even with
# shell=False (CVE-2024-24576). Unwrap @brightdata/cli to [node, entry.js] and
# fail closed whenever a batch shim would receive cmd.exe metacharacters.
_BATCH_SUFFIXES = (".cmd", ".bat")
_UNSAFE_CMD_ARG_RE = re.compile(r'[\s"&|%^\<\>\(\)!\r\n]')


def _api_key(config: Optional[Dict[str, Any]]) -> str:
    if config:
        explicit = str(config.get(API_KEY_ENV) or "").strip()
        if explicit:
            return explicit
    return str(os.environ.get(API_KEY_ENV) or "").strip()


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


def _find_node_entrypoint(shim_path: Path) -> Optional[Path]:
    """Locate @brightdata/cli's JS entrypoint relative to a Windows .cmd/.bat shim."""
    candidate_roots = (
        shim_path.parent / "node_modules" / "@brightdata" / "cli",
        shim_path.parent.parent / "lib" / "node_modules" / "@brightdata" / "cli",
    )
    for pkg_dir in candidate_roots:
        if not pkg_dir.is_dir():
            continue
        try:
            pkg_root = pkg_dir.resolve()
        except OSError:
            continue
        rel_candidates: List[str] = []
        pkg_json = pkg_root / "package.json"
        if pkg_json.is_file():
            try:
                manifest = json.loads(pkg_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                manifest = {}
            bin_field = manifest.get("bin") if isinstance(manifest, dict) else None
            if isinstance(bin_field, str) and bin_field.strip():
                rel_candidates.append(bin_field.strip())
            elif isinstance(bin_field, dict):
                entry = bin_field.get(CLI_BIN)
                if isinstance(entry, str) and entry.strip():
                    rel_candidates.append(entry.strip())
        rel_candidates.append("dist/index.js")
        for rel in rel_candidates:
            rel_norm = rel.replace("\\", "/")
            if not rel_norm or "\x00" in rel_norm or Path(rel_norm).is_absolute():
                continue
            try:
                candidate = (pkg_root / rel_norm).resolve()
            except OSError:
                continue
            if (
                candidate.is_relative_to(pkg_root)
                and candidate.is_file()
                and candidate.suffix.lower() not in _BATCH_SUFFIXES
            ):
                return candidate
    return None


def _find_node_binary(shim_dir: Path) -> Optional[str]:
    """Locate a direct (non-batch) Node.js executable for running a JS entrypoint."""
    for name in ("node.exe", "node"):
        sibling = shim_dir / name
        if sibling.is_file() and (
            name == "node.exe" or os.name == "nt" or os.access(sibling, os.X_OK)
        ):
            return str(sibling)
    node_bin = shutil.which("node")
    if node_bin and not node_bin.lower().endswith(_BATCH_SUFFIXES):
        return node_bin
    return None


def _resolve_cli_command(binary: str) -> List[str]:
    """Return the argv prefix for invoking ``binary`` without cmd.exe when possible."""
    if not binary.lower().endswith(_BATCH_SUFFIXES):
        return [binary]
    shim_path = Path(binary)
    entry = _find_node_entrypoint(shim_path)
    if entry is not None:
        node_bin = _find_node_binary(shim_path.parent)
        if node_bin is not None:
            return [node_bin, str(entry)]
    return [binary]


def _has_unsafe_cmd_args(args: Sequence[str]) -> bool:
    """True if any argument contains whitespace or cmd.exe metacharacters."""
    return any(_UNSAFE_CMD_ARG_RE.search(arg) for arg in args)


def _build_args(
    dataset_id: str,
    record: Dict[str, Any],
    *,
    cli_timeout: int,
    binary: str = CLI_BIN,
    command_prefix: Optional[Sequence[str]] = None,
) -> List[str]:
    """Assemble a ``pipelines run`` invocation (CLI >= 0.4.0).

    The API key is deliberately **absent** here -- it travels in the child's
    environment instead (see ``_child_env``). Process arguments are not a
    secret channel: ``/proc/<pid>/cmdline`` is world-readable under the
    default ``hidepid=0``, and a review pull lives for up to 180s, so a key
    on the command line is readable by any other local user and is captured
    verbatim by execve auditing, process accounting, and any monitoring
    agent that snapshots ``ps``. Mirrors the ``bird_x`` cookie-injection
    precedent.

    The record goes in as ``--input`` JSON so numbers keep their type: the
    API rejects ``"1"`` for a number field.
    """
    prefix = list(command_prefix) if command_prefix is not None else [binary]
    return [
        *prefix,
        "pipelines",
        "run",
        dataset_id,
        _METHOD,
        "--input",
        json.dumps([record]),
        "--json",
        "--timeout",
        str(cli_timeout),
    ]


def _child_env(api_key: str) -> Dict[str, str]:
    """Allowlisted child environment, even when the CLI uses its login file.

    An explicit key takes precedence over an exported Bright Data key.
    Always return a mapping: None would inherit every parent credential.
    """
    child_env = {
        name: value for name, value in os.environ.items()
        if name.upper() in _CHILD_ENV_ALLOWLIST
    }
    key = str(api_key or "").strip() or str(os.environ.get(API_KEY_ENV) or "").strip()
    return {**child_env, API_KEY_ENV: key} if key else child_env


def _scrub(text: str, secret: str = "") -> str:
    """Remove secrets and auth tokens from text before it is logged or returned.

    Defense in depth for the passthrough paths: the stderr lines this
    module deliberately surfaces are auth and quota failures, which are
    exactly the messages a CLI is most likely to echo the rejected
    credential back in.
    """
    if not text:
        return text
    scrubbed = text
    ambient = os.environ.get(API_KEY_ENV) or ""
    raw_candidates = {
        candidate
        for raw in (secret, ambient)
        if isinstance(raw, str)
        for candidate in (raw, raw.strip())
        if candidate and candidate.strip()
    }
    candidates = set(raw_candidates)
    for candidate in raw_candidates:
        encoded = _url_quote(candidate, safe="")
        if encoded:
            candidates.add(encoded)
    for candidate in sorted(candidates, key=len, reverse=True):
        scrubbed = scrubbed.replace(candidate, "***")
    scrubbed = _URL_USERINFO_RE.sub(r"\1***@", scrubbed)
    scrubbed = _BEARER_RE.sub(r"\1 ***", scrubbed)
    scrubbed = _BASIC_AUTH_RE.sub(r"\1 ***", scrubbed)
    scrubbed = _JSON_SECRET_RE.sub(r'\1"***"', scrubbed)
    scrubbed = _KV_SECRET_RE.sub(r"\1***", scrubbed)
    scrubbed = _SPACED_SECRET_RE.sub(r"\1***", scrubbed)
    return scrubbed


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
    dataset_id: str,
    record: Dict[str, Any],
    *,
    timeout: int,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run one Bright Data scraper on one input and return ``{"records", "error"}``.

    Never raises. Every failure mode -- missing binary, spawn failure,
    subprocess timeout, non-zero exit, unparseable stdout -- returns empty
    records plus a one-line ``error`` string, so callers can record the
    failure in ``errors_by_source`` without branching on exception types.

    The CLI's first stderr line is preserved verbatim as the error (auth
    401s and low-credit warnings are the cases that matter), and also
    mirrored to ``source_log`` so the failure is visible in non-TTY hosts.

    Args:
        dataset_id: scraper ID, e.g. ``gd_lwdb4vjm1ehb499uxs``.
        record: input fields for that scraper, with JSON-native types.
        timeout: subprocess timeout in seconds. The CLI's own polling
            timeout is set just below this so it can fail cleanly first.
        config: resolved config dict, consulted only for the API key.

    Returns:
        ``{"records": [...]}`` on success, else ``{"records": [], "error": str}``.
    """
    resolved = shutil.which(CLI_BIN)
    if resolved is None:
        return {"records": [], "error": f"{CLI_BIN} not on PATH"}

    cli_timeout = max(5, int(timeout) - _CLI_TIMEOUT_MARGIN)
    key = _api_key(config)
    command_prefix = _resolve_cli_command(resolved)
    cmd = _build_args(
        dataset_id,
        record,
        cli_timeout=cli_timeout,
        command_prefix=command_prefix,
    )
    if cmd and cmd[0].lower().endswith(_BATCH_SUFFIXES) and _has_unsafe_cmd_args(cmd[1:]):
        err = (
            f"cannot safely invoke {CLI_BIN} via Windows batch shim without "
            "the @brightdata/cli Node entrypoint"
        )
        _log(err)
        return {"records": [], "error": err}

    try:
        usage.begin("brightdata")
        result = subproc.run_with_timeout(cmd, timeout=timeout, env=_child_env(key))
    except subproc.SubprocTimeout as exc:
        msg = _scrub(str(exc), key)
        _log(f"Timeout: {msg}")
        return {"records": [], "error": msg}
    except FileNotFoundError as exc:
        msg = _scrub(str(exc), key)
        _log(f"Binary missing: {msg}")
        return {"records": [], "error": msg}
    except OSError as exc:
        msg = _scrub(str(exc), key)
        _log(f"Spawn failed: {msg}")
        return {"records": [], "error": msg}

    stderr = _scrub(result.stderr or "", key)
    _passthrough_warnings(stderr)

    if result.returncode != 0:
        lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
        # The CLI narrates polling progress on stderr, so the *last* line is
        # the actual failure, not the "Snapshot ... started" narration.
        first = lines[-1] if lines else f"exit {result.returncode}"
        if any(marker in stderr for marker in _OLD_CLI_MARKERS):
            first = _UPGRADE_HINT
        _log(f"CLI exit {result.returncode}: {first}")
        return {"records": [], "error": first}

    stdout = result.stdout or ""
    if not stdout.strip():
        return {"records": []}
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        msg = _scrub(str(exc), key)
        _log(f"JSON decode failed: {msg}")
        return {"records": [], "error": f"json decode: {msg}"}

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
