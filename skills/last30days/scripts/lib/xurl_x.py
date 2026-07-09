"""X (Twitter) search via xurl CLI — official X API v2 with OAuth2.

xurl is an open-source CLI for the X API (https://github.com/openclaw/xurl).
It uses OAuth2 with PKCE and automatic token refresh, requiring only a free
X Developer App. No xAI subscription or browser cookies needed.

Install: npm install -g xurl
Auth:    xurl auth oauth2 login

Priority: xAI API > Bird/GraphQL > xurl > web-only fallback
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import log
from .relevance import token_overlap_relevance as _compute_relevance


def _log(msg: str) -> None:
    log.source_log("xurl", msg, tty_only=False)


# Depth configurations: number of results to request
DEPTH_CONFIG = {
    "quick": 10,
    "default": 30,
    "deep": 60,
}


# Memoized availability, mirroring health.py's per-process dependency-probe
# cache: each uncached is_available() check spawns an `xurl whoami`
# subprocess (a live, authenticated X API call). The doctor/safe-diagnose
# path never uses it — see stored_auth_status()/has_stored_auth() below —
# but research-time callers may consult it more than once per process.
# None means "not yet probed".
_availability_cache: Optional[bool] = None


def clear_availability_cache() -> None:
    """Reset the memoized is_available() result (tests, or a re-check after auth)."""
    global _availability_cache
    _availability_cache = None


def is_available() -> bool:
    """Check if xurl is installed and has valid authentication.

    Returns True only if xurl binary is found AND the user is authenticated
    (i.e. ``xurl whoami`` exits 0 and returns a username field).
    Memoized per process; ``clear_availability_cache()`` resets.
    """
    global _availability_cache
    if _availability_cache is None:
        _availability_cache = _is_available_uncached()
    return _availability_cache


def _is_available_uncached() -> bool:
    try:
        result = subprocess.run(
            ["xurl", "whoami"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and '"username"' in result.stdout
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
        result = subprocess.run(
            ["xurl", "search", query, "-n", str(max_results)],
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
