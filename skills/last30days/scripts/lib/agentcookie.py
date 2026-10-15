"""agentcookie sidecar reader — an X cookie source for the bird backend.

``agentcookie`` is an external, user-installed CLI that can deliver browser
cookies on Linux (where Chrome's SQLite cookie store cannot be decrypted by
this engine's stdlib extractor). This module shells out to it and pulls the
``auth_token`` + ``ct0`` pair that the bird backend needs.

Deliberate constraints (see docs/plans/2026-08-31 X plan):

* **Soft dependency.** Activation is gated on ``shutil.which("agentcookie")``
  resolving on the agent subprocess PATH, exactly like the other CLI-gated
  optional sources (Digg, yt-dlp). A binary that is absent is not an error.
* **``AGENTCOOKIE=off`` disables it** regardless of PATH.
* **Independent of ``FROM_BROWSER``.** Reading the sidecar is not a browser
  extraction, so it runs even when ``FROM_BROWSER`` is unset (the state in
  which the in-process browser extractor stays off).
* We call ``agentcookie cookies --domain .x.com --json`` and parse its stdout.
  We never open ``cookies-plain.db`` directly, never import an ``agentcookie``
  Python package, and never add a Python dependency on it.
* Cookie **values are never logged**. Only counts / names are ever emitted.
* First complete pair wins: a lone ``auth_token`` or a lone ``ct0`` is not a
  usable result — both must be present.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import log

AGENTCOOKIE_BIN = "agentcookie"
X_COOKIE_DOMAIN = ".x.com"
X_COOKIE_NAMES = ("auth_token", "ct0")

# agentcookie writes its config (including its role: "source" or "sink") here.
# A SINK receives cookies delivered from another machine; on Darwin, a sink is
# an "extra host" that gets the extra cookie lookups even though it is not a
# Mac mini. Reading this file is a plain filesystem read — NEVER a subprocess —
# so a MacBook (source/unknown role) never spawns agentcookie just to be
# classified (see the AE8 no-subprocess contract). Override the path for tests
# or non-default installs with AGENTCOOKIE_CONFIG.
_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "agentcookie" / "config.json"

# The sidecar read shells out to another process; keep it bounded so a hung
# agentcookie never stalls config loading.
_TIMEOUT_SECONDS = 10


def _log(msg: str) -> None:
    log.source_log("agentcookie", msg, tty_only=False)


def is_disabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """True when ``AGENTCOOKIE=off`` in config/env disables the sidecar."""
    raw = ""
    if config is not None:
        raw = config.get("AGENTCOOKIE") or ""
    if not raw:
        from . import env

        raw = env.read_secret_env("AGENTCOOKIE") or ""
    return str(raw).strip().lower() == "off"


def is_available(config: Optional[Dict[str, Any]] = None) -> bool:
    """True when the sidecar could be used: on PATH and not disabled.

    PATH-only, side-effect free (no subprocess). Safe for the doctor /
    preflight prediction path — it reads no cookies, only whether the binary
    would be reachable at run time.
    """
    if is_disabled(config):
        return False
    return shutil.which(AGENTCOOKIE_BIN) is not None


def _config_path(config: Optional[Dict[str, Any]] = None) -> Path:
    """Path to agentcookie's config file (AGENTCOOKIE_CONFIG override wins)."""
    override = ""
    if config is not None:
        override = config.get("AGENTCOOKIE_CONFIG") or ""
    override = override or os.environ.get("AGENTCOOKIE_CONFIG") or ""
    return Path(override) if override else _DEFAULT_CONFIG_PATH


def role(config: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """agentcookie's configured role ("source"/"sink"), or None.

    Reads and parses the config FILE only — no subprocess, so it is safe on a
    MacBook that must not spawn agentcookie. Any failure (missing file, bad
    JSON, no ``role`` key, wrong type) returns None. "parse failure = not sink".
    """
    path = _config_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("role")
    return raw.strip().lower() if isinstance(raw, str) else None


def role_is_sink(config: Optional[Dict[str, Any]] = None) -> bool:
    """True only when agentcookie's configured role parses as ``sink``."""
    return role(config) == "sink"


def _consume_cookie_list(items: List[Any], names: tuple, found: Dict[str, str]) -> None:
    """Collect ``name -> value`` for wanted cookie names from a cookie list."""
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if name in names and isinstance(value, str) and value and name not in found:
            found[name] = value


def _pair_from_json(data: Any, names: tuple) -> Dict[str, str]:
    """Extract wanted cookies from agentcookie JSON, tolerant of its shape.

    Accepts a list of ``{"name","value",...}`` objects, a ``{"cookies": [...]}``
    wrapper, or a flat ``{name: value}`` / ``{name: {"value": ...}}`` mapping.
    """
    found: Dict[str, str] = {}
    if isinstance(data, list):
        _consume_cookie_list(data, names, found)
    elif isinstance(data, dict):
        cookies = data.get("cookies")
        if isinstance(cookies, list):
            _consume_cookie_list(cookies, names, found)
        else:
            for name in names:
                raw = data.get(name)
                if isinstance(raw, str) and raw:
                    found[name] = raw
                elif isinstance(raw, dict) and isinstance(raw.get("value"), str) and raw["value"]:
                    found[name] = raw["value"]
    return found


def read_x_cookies(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    """Return the complete X cookie pair from agentcookie, or None.

    Returns ``{"auth_token": ..., "ct0": ...}`` only when BOTH cookies are
    present (no half-pair). Any failure (binary absent, disabled, non-zero
    exit, unparsable JSON, timeout, incomplete pair) returns None so the
    caller falls through to the next cookie source. Never raises.
    """
    if is_disabled(config):
        return None
    binary = shutil.which(AGENTCOOKIE_BIN)
    if binary is None:
        return None

    try:
        result = subprocess.run(
            [binary, "cookies", "--domain", X_COOKIE_DOMAIN, "--json"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _log(f"timed out after {_TIMEOUT_SECONDS}s; skipping")
        return None
    except OSError as exc:
        _log(f"could not run agentcookie: {type(exc).__name__}")
        return None

    if result.returncode != 0:
        # stderr may carry a reason; do not echo it (it can contain values).
        _log(f"exited {result.returncode}; skipping")
        return None

    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        _log("returned non-JSON output; skipping")
        return None

    found = _pair_from_json(data, X_COOKIE_NAMES)
    if all(name in found for name in X_COOKIE_NAMES):
        _log(f"delivered a complete X cookie pair ({len(found)} of {len(X_COOKIE_NAMES)} names)")
        return {name: found[name] for name in X_COOKIE_NAMES}
    if found:
        _log(f"returned an incomplete pair ({sorted(found)}); ignoring per no-half-pair rule")
    return None
