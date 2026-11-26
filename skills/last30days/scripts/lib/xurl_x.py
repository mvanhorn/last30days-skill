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
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import health, http, log, private_files, usage
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
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        return (
            result.returncode == 0
            and _BEARER_CONFIGURED_RE.search(result.stdout or "") is not None
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

# Substrings a populated store carries in both YAML and legacy JSON formats.
# Runtime search pins ``--auth app`` (app-only bearer), so only a non-empty
# ``bearer_token`` satisfies AUTH_OK; OAuth1/OAuth2 user tokens alone do not.
_TOKEN_STORE_MARKERS = (
    "bearer_token",
)
_OAUTH_USER_TOKEN_MARKERS = (
    "access_token",
    "oauth2_tokens",
    "oauth1_tokens",
)
_YAML_EMPTY_VALUES = frozenset({"", "''", '""', "null", "~", "{}", "[]"})
_BEARER_VALUE_KEYS = frozenset({"bearer", "token", "value"})
_BEARER_KEY_LINE_RE = re.compile(r"^(\s*)['\"]?bearer_token['\"]?\s*:\s*(.*)$")
_MAX_TOKEN_STORE_BYTES = 65536


def _json_has_bearer_token(obj: Any) -> bool:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "bearer_token":
                if isinstance(value, str) and value.strip():
                    return True
                if isinstance(value, dict) and any(
                    isinstance(subkey, str)
                    and subkey.strip().lower() in _BEARER_VALUE_KEYS
                    and isinstance(subval, str)
                    and subval.strip()
                    for subkey, subval in value.items()
                ):
                    return True
            elif _json_has_bearer_token(value):
                return True
    elif isinstance(obj, list):
        return any(_json_has_bearer_token(item) for item in obj)
    return False


def _content_has_bearer_token(content: str) -> bool:
    stripped = content.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return _json_has_bearer_token(json.loads(content))
        except (ValueError, TypeError):
            pass
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        match = _BEARER_KEY_LINE_RE.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        rest = match.group(2).split("#", 1)[0].strip()
        if rest.lower() not in _YAML_EMPTY_VALUES:
            return True
        if rest:
            continue
        for child in lines[idx + 1 :]:
            child_stripped = child.strip()
            if not child_stripped or child_stripped.startswith("#"):
                continue
            child_indent = len(child) - len(child.lstrip())
            if child_indent <= indent:
                break
            if ":" in child_stripped:
                child_key, child_raw = child_stripped.split(":", 1)
                if child_key.strip().strip("'\"").lower() not in _BEARER_VALUE_KEYS:
                    continue
                child_val = child_raw.split("#", 1)[0].strip()
                if child_val.lower() not in _YAML_EMPTY_VALUES:
                    return True
    return False


def _read_token_store_text(path: Any) -> str:
    """Read an on-disk xurl token store safely (no symlink follow, non-blocking, bounded)."""
    if not isinstance(path, (str, os.PathLike)):
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_TOKEN_STORE_BYTES]
    target = Path(path)
    if os.name == "posix":
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd = os.open(target, flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"Expected a regular token store file: {target}")
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as stream:
                fd = -1
                return stream.read(_MAX_TOKEN_STORE_BYTES)
        finally:
            if fd != -1:
                os.close(fd)
    private_files._reject_symlink_or_nonregular(target)
    with target.open("r", encoding="utf-8", errors="replace") as stream:
        return stream.read(_MAX_TOKEN_STORE_BYTES)


def _is_file(path: Path) -> bool:
    """True when *path* is a regular file; raise on an unreadable stat.

    ``pathlib.Path.is_file()`` swallows ``OSError`` into ``False``, which
    would silently misreport a permission-denied (EACCES) store as absent.
    Probing via ``stat`` keeps the typed ``AUTH_ERROR`` path reachable for
    real stat failures. A missing path (``FileNotFoundError``) returns False —
    absence is a normal state (this also covers a dangling symlink whose
    target is gone). Path stubs without ``stat``/``is_file`` support (test
    doubles) fall back to their own behavior.
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except FileNotFoundError:
        return False
    except (AttributeError, TypeError):
        try:
            return bool(path.is_file())
        except AttributeError:
            return False


def _is_dir(path: Path) -> bool:
    """True when *path* is a directory; raise on an unreadable stat.

    Same rationale as :func:`_is_file` — pathlib's ``is_dir()`` hides
    ``OSError``, so a permission-denied parent would read as a missing store.
    """
    try:
        return stat.S_ISDIR(path.stat().st_mode)
    except FileNotFoundError:
        return False
    except (AttributeError, TypeError):
        try:
            return bool(path.is_dir())
        except AttributeError:
            return False


def token_store_path() -> Path:
    """xurl's on-disk OAuth token store (~/.xurl/auth.yml).

    Current xurl (>=1.1) stores credentials in a YAML file at
    ``~/.xurl/auth.yml``; ``~/.xurl`` itself is a directory. Legacy
    releases wrote a flat file at ``~/.xurl``. ``stored_auth_status()``
    resolves both layouts; this returns the canonical current path.
    """
    return Path.home() / ".xurl" / "auth.yml"


def stored_auth_status() -> Tuple[str, str]:
    """Local-only evidence of xurl authentication: ``(status, detail)``.

    Reads only the on-disk token store — never spawns xurl, never touches
    the network. ``status`` is AUTH_OK (store holds an app-only bearer token),
    AUTH_MISSING (no store / empty store / OAuth user tokens only without
    ``bearer_token``), or AUTH_ERROR (store exists but cannot be read —
    surfaced as a typed error, not as "unconfigured").
    """
    # Resolve the actual credential file across layouts. Current xurl keeps
    # ~/.xurl/auth.yml inside the ~/.xurl directory; legacy installs wrote a
    # flat ~/.xurl file. Honor whatever token_store_path() resolves to and
    # check both shapes so a valid directory layout is never misread as a
    # missing token store. Path stubs that cannot combine (e.g. an unreadable
    # store stub without / support) still reach the typed error path below.
    path = token_store_path()
    try:
        base = path.parent if path.name == "auth.yml" else path
    except (AttributeError, TypeError):
        # A path stub with no parent/name support: treat it as the file itself.
        base = path
    try:
        # Use stat rather than is_file()/is_dir(): pathlib swallows OSError
        # into False, which would silently turn a permission-denied store into
        # "no token store" instead of the typed AUTH_ERROR.
        candidates = [base / "auth.yml", base] if _is_dir(base) else [base, base / "auth.yml"]
    except (TypeError, AttributeError):
        # A path stub that cannot combine (no __truediv__): the path itself is
        # the only candidate.
        candidates = [path]
    except OSError as exc:
        return (
            AUTH_ERROR,
            f"token store {base} unreadable: {type(exc).__name__}: {exc}",
        )
    try:
        path = next(c for c in candidates if _is_file(c))
    except StopIteration:
        return AUTH_MISSING, f"no token store at {token_store_path()}"
    except OSError as exc:
        return (
            AUTH_ERROR,
            f"token store {base} unreadable: {type(exc).__name__}: {exc}",
        )
    try:
        content = _read_token_store_text(path)
    except OSError as exc:
        return (
            AUTH_ERROR,
            f"token store {path} unreadable: {type(exc).__name__}: {exc}",
        )
    if _content_has_bearer_token(content):
        return AUTH_OK, f"stored app-only bearer credentials found in {path}"
    if any(marker in content for marker in _OAUTH_USER_TOKEN_MARKERS):
        return (
            AUTH_MISSING,
            f"token store {path} has OAuth user tokens only; search requires app-only bearer auth",
        )
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
# HTTP 400 from X's query grammar (for example a bare lowercase "and").
ERR_INVALID_REQUEST = "xurl: X rejected the query (invalid request)"
ERR_EMPTY_QUERY = "xurl: no searchable keywords left after removing X search operators"
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
    # X's v2 problem type (".../2/problems/invalid-request") or its title
    # ("Invalid Request"). Only the fixed string leaves; the body may echo
    # the query or request headers.
    lowered = text.lower()
    if "invalid-request" in lowered or "invalid request" in lowered:
        return ERR_INVALID_REQUEST
    return ERR_FAILED


def search_x(
    query: str,
    depth: str = "default",
    deadline: Optional[float] = None,
) -> Dict[str, Any]:
    """Search X via xurl CLI using X API v2 search/recent.

    The topic is sanitized into space-joined keywords first
    (``x_api.build_keyword_query``): X's v2 grammar rejects a bare lowercase
    ``and``/``or`` with HTTP 400 and treats ``from:``-style tokens, leading
    ``-``, parentheses and quotes as operators, and the pipeline always
    passes the raw user topic here.

    Args:
        query: Search topic (raw user text is fine; it is sanitized)
        depth: "quick", "default", or "deep"
        deadline: Optional shared wall-clock deadline (``time.monotonic()``
            instant) from the X backend chain. The single subprocess timeout
            is clamped to the time left; a call past the deadline never starts.

    Returns:
        Raw JSON response from X API v2 tweets/search/recent, or a dict
        with an "error" key holding a fixed string on failure. The CLI's
        own output reaches only the debug log, never the error.
    """
    search_query = x_api.build_keyword_query(query)
    if not search_query:
        return {"error": ERR_EMPTY_QUERY}

    max_results = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    # X API v2 search/recent requires max_results in 10–100 range
    max_results = max(10, min(100, max_results))
    timeout = 30
    if deadline is not None:
        remaining = deadline - time.monotonic()
        if remaining < 1:
            return {"error": "xurl: chain budget exhausted"}
        timeout = max(1, min(timeout, int(remaining)))

    charge = None
    try:
        # --auth app (app-only bearer): xurl >=1.1 mis-signs OAuth1 requests
        # whose query needs percent-encoding (spaces, parens, ...) -> 401.
        # Bearer auth sends no signature, so multi-word queries work.
        charge = usage.begin("x")
        result = subprocess.run(
            ["xurl", "search", search_query, "-n", str(max_results), "--auth", "app"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
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
        usage.cancel(charge)
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
