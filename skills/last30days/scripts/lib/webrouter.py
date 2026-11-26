"""Opt-in web search through the WebRouter command line.

``--web-backend webrouter`` shells out to the ``webrouter`` CLI from the
open-source WebRouter package (``uv tool install webrouter`` or
``pipx install webrouter``, version 0.16 or later). The backend is used only
when the user pins it, and only when ``webrouter`` resolves on the agent
subprocess PATH; it is never auto-selected.

Each call asks one keyless backend at a time, You.com's public MCP endpoint
first and Exa's public MCP endpoint second, so a failure, rate limit or empty
answer from the first falls through to the second. Both return a publish date
on each result. WebRouter's own default keyless route does not date its
results, so it is not used: like the other opt-in backends, only dated
evidence inside the research window is kept.

The child process gets an allowlisted environment with no API keys, so every
call is keyless and no credential leaves this process. Queries are passed as
argv after ``--`` (never through a shell) and output is read with a size cap.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from . import dates, log, subproc

CLI_BIN = "webrouter"

# Keyless WebRouter backends that date their results, tried in order.
PROVIDER_CHAIN: Tuple[str, ...] = ("you-mcp", "exa-mcp")

# Seconds WebRouter waits on one backend; the subprocess gets a few more for
# interpreter start-up.
_CALL_TIMEOUT = 15.0
_STARTUP_GRACE = 5.0
_MAX_OUTPUT_BYTES = 2_000_000
_SNIPPET_CHARS = 500

_CHILD_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
    "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "XDG_STATE_HOME", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
    "PATHEXT", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "NO_COLOR", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})


def is_available() -> bool:
    """True when the ``webrouter`` CLI is on the agent subprocess PATH."""
    return shutil.which(CLI_BIN) is not None


def _log(msg: str) -> None:
    log.source_log("WebRouter", msg, tty_only=False)


def _child_env() -> Dict[str, str]:
    """Allowlisted child environment: no API keys reach the CLI."""
    return {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _CHILD_ENV_ALLOWLIST
    }


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _recency(date_range: Tuple[str, str]) -> Optional[str]:
    """The narrowest WebRouter recency period that covers the window's start."""
    try:
        span = (_today() - date.fromisoformat(date_range[0])).days
    except ValueError:
        return None
    for period, days in (("day", 1), ("week", 7), ("month", 31), ("year", 366)):
        if span <= days:
            return period
    return None


def _command(query: str, provider: str, count: int, recency: Optional[str]) -> List[str]:
    cmd = [
        CLI_BIN, "search", "--provider", provider, "--max-results", str(count),
        "--timeout", str(int(_CALL_TIMEOUT)), "--no-data-apis",
    ]
    if recency:
        cmd += ["--recency", recency]
    return cmd + ["--", query]


def _error_message(stderr: str, returncode: int) -> str:
    """The CLI's ``{"error": {...}}`` line as ``Type: message``, else the exit code."""
    for line in (stderr or "").splitlines():
        try:
            error = json.loads(line).get("error")
        except (ValueError, AttributeError):
            continue
        if isinstance(error, dict):
            return f"{error.get('type', 'Error')}: {str(error.get('message', ''))[:200]}"
    return f"exit {returncode}"


def _run(
    cmd: List[str],
    deadline_monotonic: Optional[float],
    cancel: Optional[threading.Event],
) -> Dict[str, Any]:
    """Run one CLI call and return its JSON object; raises RuntimeError on failure."""
    bounds: Dict[str, Any] = {}
    if deadline_monotonic is not None:
        bounds["deadline_monotonic"] = deadline_monotonic
    if cancel is not None:
        bounds["cancel"] = cancel
    try:
        result = subproc.run_with_timeout(
            cmd,
            timeout=_CALL_TIMEOUT + _STARTUP_GRACE,
            env=_child_env(),
            capture_limit_bytes=_MAX_OUTPUT_BYTES,
            **bounds,
        )
    except subproc.SubprocTimeout as exc:
        raise RuntimeError(f"timed out: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"could not start {CLI_BIN}: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(_error_message(result.stderr, result.returncode))
    try:
        data = json.loads(result.stdout or "")
    except ValueError as exc:
        raise RuntimeError("unreadable JSON from webrouter") from exc
    if not isinstance(data, dict):
        raise RuntimeError("unexpected JSON shape from webrouter")
    return data


def _items(data: Dict[str, Any], date_range: Tuple[str, str], count: int, provider: str) -> List[dict]:
    """Map CLI results to web items, keeping only dated results inside the window."""
    items: List[dict] = []
    results = data.get("results")
    for row in results if isinstance(results, list) else []:
        if len(items) >= count:
            break
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        try:
            parsed_url = urlparse(url)
        except ValueError:
            continue
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            continue
        raw_date = row.get("published_date")
        parsed_date = dates.parse_date(raw_date[:10]) if isinstance(raw_date, str) else None
        pub_date = parsed_date.date().isoformat() if parsed_date else None
        if not pub_date or not date_range[0] <= pub_date <= date_range[1]:
            continue
        snippet = str(row.get("content") or row.get("description") or "")
        items.append({
            "id": f"WR{len(items) + 1}",
            "title": str(row.get("title") or parsed_url.netloc),
            "url": url,
            "source_domain": parsed_url.netloc.strip().lower(),
            "snippet": snippet[:_SNIPPET_CHARS],
            "date": pub_date,
            "relevance": 0.8,
            "why_relevant": f"WebRouter web search ({provider})",
        })
    return items


def search(
    query: str,
    date_range: Tuple[str, str],
    count: int = 5,
    *,
    deadline_monotonic: Optional[float] = None,
    cancel: Optional[threading.Event] = None,
) -> Tuple[List[dict], dict]:
    """Search through the keyless chain; the first backend with dated results wins.

    Raises RuntimeError when the CLI is missing or every backend failed, so the
    web source reports a failure instead of an empty result.
    """
    if not is_available():
        raise RuntimeError(f"{CLI_BIN} is not on PATH; install it with: uv tool install webrouter")
    recency = _recency(date_range)
    errors: List[str] = []
    for provider in PROVIDER_CHAIN:
        if cancel is not None and cancel.is_set():
            break
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        try:
            data = _run(_command(query, provider, count, recency), deadline_monotonic, cancel)
        except RuntimeError as exc:
            _log(f"{provider} failed: {exc}")
            errors.append(f"{provider}: {exc}")
            continue
        items = _items(data, date_range, count, provider)
        if items:
            return items, {
                "label": "webrouter",
                "provider": provider,
                "webSearchQueries": [query],
                "resultCount": len(items),
            }
        _log(f"{provider} returned no dated results inside the window")
    if errors and len(errors) == len(PROVIDER_CHAIN):
        raise RuntimeError("WebRouter search failed: " + "; ".join(errors))
    return [], {"label": "webrouter", "webSearchQueries": [query], "resultCount": 0}
