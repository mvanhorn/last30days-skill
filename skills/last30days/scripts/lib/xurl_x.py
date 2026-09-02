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
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import log
from .relevance import token_overlap_relevance as _compute_relevance

# xurl auth status marks a configured app-only bearer as "bearer: ✓".
# Search uses --auth app, so availability must require this — oauth1 alone
# is not enough.
_BEARER_CONFIGURED_RE = re.compile(r"bearer:\s*✓")


def _log(msg: str) -> None:
    log.source_log("xurl", msg, tty_only=False)


# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 10,
    "default": 30,
    "deep": 60,
}


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
    the network. ``status`` is AUTH_OK (store holds credentials),
    AUTH_MISSING (no store / empty store / no credential markers), or
    AUTH_ERROR (store exists but cannot be read — surfaced as a typed
    error, not as "unconfigured").
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
        with an "error" key on failure.
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
            return {"error": f"xurl search failed: {error_text}"}

        return json.loads(result.stdout)

    except FileNotFoundError:
        return {"error": "xurl not found in PATH"}
    except subprocess.TimeoutExpired:
        return {"error": "xurl search timed out (30s)"}
    except json.JSONDecodeError as exc:
        return {"error": f"Invalid JSON from xurl: {exc}"}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def parse_x_response(
    response: Dict[str, Any],
    topic: str = "",
) -> List[Dict[str, Any]]:
    """Parse xurl search response into normalized item dicts.

    Output format matches the existing XItem schema used by xai_x and bird_x:
    id, text, url, author_handle, date, engagement, why_relevant, relevance.

    Args:
        response: Raw X API v2 response dict from search_x()
        topic: Original search topic (used for relevance scoring)

    Returns:
        List of item dicts.  Empty list on error or no results.
    """
    items: List[Dict[str, Any]] = []

    if "error" in response:
        _log(f"Error in response: {response['error']}")
        return items

    data = response.get("data") or []
    if not data:
        return items

    # Build author lookup from includes.users
    authors: Dict[str, Dict[str, Any]] = {}
    for user in (response.get("includes") or {}).get("users") or []:
        authors[user["id"]] = user

    for i, tweet in enumerate(data):
        author_id = tweet.get("author_id", "")
        author = authors.get(author_id, {})
        username = author.get("username", "")

        tweet_id = tweet.get("id", "")
        url = f"https://x.com/{username}/status/{tweet_id}" if username else ""

        # Parse public_metrics
        engagement: Optional[Dict[str, Any]] = None
        metrics = tweet.get("public_metrics") or {}
        if metrics:
            engagement = {
                "likes": metrics.get("like_count", 0),
                "reposts": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "quotes": metrics.get("quote_count", 0),
            }

        # Parse ISO 8601 date → YYYY-MM-DD
        date: Optional[str] = None
        created = tweet.get("created_at", "")
        if created:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", created)
            if m:
                date = m.group(1)

        text = tweet.get("text", "").strip()

        # Relevance score via shared token-overlap function
        relevance = _compute_relevance(topic, text) if topic else 0.5

        items.append({
            "id": f"XURL{i + 1}",
            "text": text[:500],
            "url": url,
            "author_handle": username,
            "date": date,
            "engagement": engagement,
            "why_relevant": "",
            "relevance": relevance,
        })

    return items
