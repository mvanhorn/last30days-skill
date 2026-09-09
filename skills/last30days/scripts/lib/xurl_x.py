"""X (Twitter) search via xurl CLI — official X API v2.

xurl is X's official CLI for the X API
(https://github.com/xdevplatform/xurl). It requires only a free
X Developer App. No xAI subscription or browser cookies needed.

Install: npm install -g @xdevplatform/xurl
Auth:    xurl auth app-only <bearer-token>   (search / availability)
         xurl auth oauth1 ...                (optional; not used for search)

Priority: xAI API > Bird/GraphQL > xurl > web-only fallback
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import health, http, log
from . import x_api
# One X API v2 depth table and one parser: xurl_x re-imports both.
from .x_api import DEPTH_CONFIG

# xurl auth status marks a configured app-only bearer as "bearer: ✓".
# Search uses --auth app, so availability must require this — oauth1 alone
# is not enough.
_BEARER_CONFIGURED_RE = re.compile(r"bearer:\s*✓")


def _log(msg: str) -> None:
    log.source_log("xurl", msg, tty_only=False)



# Memoized availability, mirroring health.py's per-process dependency-probe
# cache: each uncached is_available() check spawns an `xurl auth status`
# subprocess (local credential status; no network). The doctor/safe-diagnose
# path never uses it — see stored_auth_status()/has_stored_auth() below —
# but research-time callers may consult it more than once per process.
# None means "not yet probed".
_availability_cache: Optional[bool] = None


def clear_availability_cache() -> None:
    """Reset the memoized is_available() result (tests, or a re-check after auth)."""
    global _availability_cache
    _availability_cache = None


def is_available() -> bool:
    """Check if xurl is installed and has app-only bearer auth.

    Returns True only if xurl binary is found AND ``xurl auth status``
    exits 0 with a configured app-only bearer (``bearer: ✓``). OAuth1
    alone is insufficient — ``search_x`` pins ``--auth app``.
    Memoized per process; ``clear_availability_cache()`` resets.
    """
    global _availability_cache
    if _availability_cache is None:
        _availability_cache = _is_available_uncached()
    return _availability_cache


def _is_available_uncached() -> bool:
    try:
        result = subprocess.run(
            ["xurl", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (
            result.returncode == 0
            and _BEARER_CONFIGURED_RE.search(result.stdout) is not None
        )
    except (OSError, subprocess.TimeoutExpired):
        # OSError covers FileNotFoundError (no xurl on PATH) and
        # PermissionError (a non-executable match on PATH, e.g. WSL's
        # /mnt/c/.../WindowsApps shim returning EACCES on exec).
        return False


# ---------------------------------------------------------------------------
# Local auth evidence (doctor / safe-diagnose path — no subprocess, no
# network).
#
# xurl persists OAuth credentials to an on-disk token store at ~/.xurl
# (YAML in current releases; legacy versions wrote JSON — see the upstream
# store package at github.com/xdevplatform/xurl). A populated store is the
# strongest LOCAL evidence of authentication obtainable without spending a
# network call, so doctor keys on it and reports "auth not live-verified"
# instead of running `xurl whoami` (a real, authenticated X API request
# that would violate doctor's no-network guarantee).
# ---------------------------------------------------------------------------

AUTH_OK = "ok"            # token store present with stored credentials
AUTH_MISSING = "missing"  # no token store, or no credentials stored in it
AUTH_ERROR = "error"      # token store exists but could not be read

# Substrings a populated store carries in both the YAML and legacy JSON
# formats (per-user oauth2 token blocks, or an app-only bearer token).
_TOKEN_STORE_MARKERS = (
    "access_token",
    "bearer_token",
    "oauth2_tokens",
    "oauth1_tokens",
)


def token_store_path() -> Path:
    """xurl's on-disk OAuth token store (~/.xurl)."""
    return Path.home() / ".xurl"


def stored_auth_status() -> Tuple[str, str]:
    """Local-only evidence of xurl authentication: ``(status, detail)``.

    Reads only the on-disk token store — never spawns xurl, never touches
    the network. ``status`` is AUTH_OK (store holds credentials),
    AUTH_MISSING (no store / empty store / no credential markers), or
    AUTH_ERROR (store exists but cannot be read — surfaced as a typed
    error, not as "unconfigured").
    """
    path = token_store_path()
    try:
        if not path.is_file():
            return AUTH_MISSING, f"no token store at {path}"
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return (
            AUTH_ERROR,
            f"token store {path} unreadable: {type(exc).__name__}: {exc}",
        )
    if any(marker in content for marker in _TOKEN_STORE_MARKERS):
        return AUTH_OK, f"stored OAuth credentials found in {path}"
    return AUTH_MISSING, f"token store {path} has no stored credentials"


def has_stored_auth() -> bool:
    """Local-only availability: xurl on PATH with stored credentials.

    The doctor/safe-diagnose counterpart of ``is_available()`` — the same
    "installed and authenticated" question answered from local evidence
    only (PATH lookup + token store), never a live ``xurl whoami``. A
    broken token store reads as unavailable here; the doctor probe layer
    (``backends._probe_xurl``) reports that case as a typed error.
    """
    return shutil.which("xurl") is not None and stored_auth_status()[0] == AUTH_OK


# Engine-authored fixed error strings (the same rule as x_api): xurl's
# stderr can echo the request, the bearer, or an account id, so it never
# reaches the outcome detail. Each string carries a marker that
# http.classify_failure recognizes.
ERR_PAYMENT_REQUIRED = "xurl: payment required (X API credits exhausted)"
ERR_UNAUTHORIZED = "xurl: unauthorized (bearer token rejected)"
ERR_FORBIDDEN = "xurl: forbidden (bearer token lacks access)"
ERR_RATE_LIMITED = "xurl: rate limit exceeded (X API)"
ERR_FAILED = "xurl: search failed"
ERR_INVALID_JSON = "xurl: invalid JSON from xurl"
ERR_NOT_FOUND = "xurl not found in PATH"
ERR_TIMED_OUT = "xurl search timed out (30s)"


def _classify_cli_failure(output: str) -> str:
    """Map xurl's stderr/stdout to a fixed string via the shared classifier."""
    text = output or ""
    state = http.classify_failure(message=text)
    if state == health.PAYMENT_REQUIRED:
        return ERR_PAYMENT_REQUIRED
    if state == health.AUTH_FAILED:
        # The shared vocabulary has one auth state; split the wording only.
        lowered = text.lower()
        if "401" in lowered or "unauthorized" in lowered:
            return ERR_UNAUTHORIZED
        return ERR_FORBIDDEN
    if state == health.RATE_LIMITED:
        return ERR_RATE_LIMITED
    return ERR_FAILED


def search_x(
    query: str,
    depth: str = "default",
) -> Dict[str, Any]:
    """Search X via xurl CLI using X API v2 search/recent.

    Args:
        query: Search query string
        depth: "quick", "default", or "deep"

    Returns:
        Raw JSON response from X API v2 tweets/search/recent, or a dict
        with an "error" key holding a fixed string on failure. The CLI's
        own output reaches only the debug log, never the error.
    """
    max_results = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    # X API v2 search/recent requires max_results in 10–100 range
    max_results = max(10, min(100, max_results))

    try:
        # --auth app (app-only bearer): xurl >=1.1 mis-signs OAuth1 requests
        # whose query needs percent-encoding (spaces, parens, ...) -> 401.
        # Bearer auth sends no signature, so multi-word queries work.
        result = subprocess.run(
            ["xurl", "search", query, "-n", str(max_results), "--auth", "app"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            error_text = result.stderr.strip() or result.stdout.strip()
            classified = _classify_cli_failure(error_text)
            # The raw CLI output can echo the bearer; only the fixed string
            # and its size reach the debug log.
            log.debug(f"xurl exit {result.returncode}: {classified} ({len(error_text)} chars)")
            return {"error": classified}

        return json.loads(result.stdout)

    except FileNotFoundError:
        return {"error": ERR_NOT_FOUND}
    except subprocess.TimeoutExpired:
        return {"error": ERR_TIMED_OUT}
    except json.JSONDecodeError:
        return {"error": ERR_INVALID_JSON}
    except Exception as exc:
        return {"error": f"xurl: {type(exc).__name__}"}


def parse_x_response(
    response: Dict[str, Any],
    topic: str = "",
) -> List[Dict[str, Any]]:
    """Parse an xurl search response into normalized item dicts.

    Delegates to the shared X API v2 parser (``x_api.parse_v2_response``)
    so xurl and xapi never drift; only the id prefix differs.

    Args:
        response: Raw X API v2 response dict from search_x()
        topic: Original search topic (used for relevance scoring)

    Returns:
        List of item dicts.  Empty list on error or no results.
    """
    if isinstance(response, dict) and "error" in response:
        _log(f"Error in response: {response['error']}")
        return []
    return x_api.parse_v2_response(response, topic, None, id_prefix="XURL")
