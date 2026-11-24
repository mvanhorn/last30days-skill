"""Bluesky search via AT Protocol (requires app password).

Uses bsky.social for auth and api.bsky.app for post search (the canonical
authenticated AppView). The previous default `public.api.bsky.app` is the
unauthenticated public mirror, which BunnyCDN now blocks for searchPosts
regardless of auth header (verified 2026-05-04). Override the search host
via BSKY_SEARCH_HOST env var if Bluesky migrates infrastructure again.

Requires BSKY_HANDLE and BSKY_APP_PASSWORD env vars. App passwords are
19-char xxxx-xxxx-xxxx-xxxx; generate at bsky.app/settings/app-passwords.
The createSession endpoint accepts main-account passwords too, but they're
bad hygiene (no scope, can't revoke individually).
"""

import math
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode, urlsplit

from . import auth_safety, http, log, public_host

BSKY_SESSION_URL = "https://bsky.social/xrpc/com.atproto.server.createSession"
BSKY_REFRESH_URL = "https://bsky.social/xrpc/com.atproto.server.refreshSession"
_DEFAULT_BSKY_SEARCH_HOST = "api.bsky.app"
_BSKY_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_BSKY_DISALLOWED_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain", ".home.arpa")
_BSKY_TRUSTED_HOSTS = frozenset({"bsky.app", "bsky.social"})
_BSKY_TRUSTED_SUFFIXES = (".bsky.app", ".bsky.social")


def _is_trusted_bsky_host(host: str) -> bool:
    """Return True when ``host`` is a Bluesky domain safe for bsky.social JWTs."""
    normalized = (host or "").strip().lower()
    return normalized in _BSKY_TRUSTED_HOSTS or normalized.endswith(_BSKY_TRUSTED_SUFFIXES)


def _validate_search_host(raw: Any) -> Optional[str]:
    """Validate and normalize a BSKY_SEARCH_HOST override to a bare public FQDN."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    # Strip a single pasted https:// origin (reject http:// and other schemes).
    if text.lower().startswith("https://"):
        text = text[len("https://"):]
    if text.endswith("/"):
        text = text[:-1]
    if not text or not text.isascii():
        return None
    if any(ch in text for ch in "/\\@?#:[]%"):
        return None
    try:
        parts = urlsplit(f"https://{text}")
        port = parts.port
    except ValueError:
        return None
    if (
        parts.scheme != "https"
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.path != ""
        or parts.query != ""
        or parts.fragment != ""
    ):
        return None
    host = (parts.hostname or "").lower()
    if not host or len(host) > 253 or parts.netloc.lower() != host:
        return None
    if not _BSKY_HOST_RE.fullmatch(host):
        return None
    if host == "localhost" or host.endswith(_BSKY_DISALLOWED_SUFFIXES):
        return None
    if public_host._parse_ip_literal(host) is not None or host.rsplit(".", 1)[-1].isdigit():
        return None
    if not public_host.is_public_address_syntax(f"https://{host}"):
        return None
    return host


def _bsky_search_redirect_guard(expected_host: str):
    """Allow redirects only over HTTPS to the exact validated search host."""
    target_host = expected_host.lower()

    def _guard(newurl: str) -> bool:
        if not isinstance(newurl, str) or not newurl or newurl != newurl.strip():
            return False
        if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in newurl):
            return False
        if not public_host.is_public_address_syntax(newurl):
            return False
        try:
            parts = urlsplit(newurl)
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme.lower() == "https"
            and parts.username is None
            and parts.password is None
            and "\\" not in parts.netloc
            and port in (None, 443)
            and (parts.hostname or "").lower() == target_host
        )

    return _guard


def _resolve_search_url(config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the Bluesky search URL with BSKY_SEARCH_HOST override.

    Default is api.bsky.app. Override via BSKY_SEARCH_HOST in shell env or
    .env file. The project's env.py loads .env into config but not into
    os.environ, so check both — same hybrid pattern as last30days.py for
    LAST30DAYS_STORE.

    Strictly validates user-supplied host values as bare public FQDNs:
    rejects non-HTTPS schemes, embedded paths, queries, fragments, userinfo,
    ports, whitespace/control chars, IP literals, and localhost/private hosts.
    On invalid input we log a warning and fall back to the default.
    """
    config = config or {}
    raw = (
        os.environ.get("BSKY_SEARCH_HOST")
        or config.get("BSKY_SEARCH_HOST")
        or _DEFAULT_BSKY_SEARCH_HOST
    )
    host = _validate_search_host(raw)
    if not host:
        if raw != _DEFAULT_BSKY_SEARCH_HOST:
            _log(
                f"BSKY_SEARCH_HOST is not a valid public hostname; "
                f"falling back to {_DEFAULT_BSKY_SEARCH_HOST}"
            )
        host = _DEFAULT_BSKY_SEARCH_HOST
    return f"https://{host}/xrpc/app.bsky.feed.searchPosts"


# App-password format: xxxx-xxxx-xxxx-xxxx (19 chars, lowercase alphanumeric
# with three hyphens at fixed positions).
_APP_PASSWORD_RE = re.compile(r"^[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}$")


def _validate_app_password_format(value) -> bool:
    """Return True if value matches Bluesky's 19-char app-password format.

    False for non-strings (None, int, list) so callers passing config dict
    values directly don't crash. Detect-but-not-gate: the createSession
    endpoint also accepts main-account passwords, so failing this check is
    a hygiene smell, not a hard error.
    """
    if not isinstance(value, str):
        return False
    return bool(_APP_PASSWORD_RE.fullmatch(value))


DEPTH_CONFIG = {
    "quick": 15,
    "default": 30,
    "deep": 60,
}

# Module-level token cache (valid for the lifetime of a single research run)
_cached_token: Optional[str] = None
_cached_refresh_token: Optional[str] = None
_token_created_at: float = 0.0
_session_error: Optional[str] = None
_refresh_error_status: Optional[int] = None
_TOKEN_MAX_AGE_SECONDS = 5400  # 90 minutes (conservative, tokens last ~2 hours)


def _log(msg: str):
    log.source_log("Bluesky", msg, tty_only=False)


def _create_session(handle: str, app_password: str) -> Optional[str]:
    """Create an AT Protocol session and return the access token.

    Args:
        handle: Bluesky handle (e.g. user.bsky.social)
        app_password: App password from bsky.app/settings/app-passwords

    Returns:
        Access JWT string, or None on failure. Sets _session_error on failure.
    """
    global _cached_token, _cached_refresh_token, _token_created_at, _session_error
    if _cached_token and (time.monotonic() - _token_created_at < _TOKEN_MAX_AGE_SECONDS):
        return _cached_token
    if _cached_token:
        _log("Session token expired, re-authenticating")
        _cached_token = None
        _token_created_at = 0.0

    try:
        response = http.request(
            "POST",
            BSKY_SESSION_URL,
            json_data={"identifier": handle, "password": app_password},
            timeout=15,
        )
        token = response.get("accessJwt")
        if token:
            _cached_token = token
            _cached_refresh_token = response.get("refreshJwt")
            _token_created_at = time.monotonic()
            _session_error = None
            _log("Session created successfully")
            return token
        _log("No accessJwt in session response")
        _session_error = "No accessJwt in session response"
        return None
    except http.HTTPError as e:
        if e.status_code == 403 and e.body and "cloudflare" in e.body.lower():
            _session_error = "Cloudflare blocked the request (403 Forbidden). This is a network-level block, not an auth issue. Try a different network or VPN."
        elif e.status_code == 401:
            _session_error = "Invalid credentials (401 Unauthorized). Check BSKY_HANDLE and BSKY_APP_PASSWORD."
        else:
            safe_err = auth_safety.scrub_secrets(str(e), (app_password,))
            _session_error = f"Session request failed: {safe_err}"
        _log(f"Session creation failed: {_session_error}")
        return None
    except Exception as e:
        safe_err = auth_safety.scrub_secrets(str(e), (app_password,))
        _session_error = f"Session request failed: {type(e).__name__}: {safe_err}"
        _log(f"Session creation failed: {_session_error}")
        return None


def _refresh_session(refresh_jwt: Optional[str]) -> Optional[str]:
    """Refresh an access token using the cached AT Protocol refresh token.

    Returns the new access token on success.  ``_refresh_error_status`` records
    an HTTP status so the caller can distinguish an invalid refresh token
    (which should fall back to createSession) from a transient failure.
    """
    global _cached_token, _cached_refresh_token, _token_created_at
    global _session_error, _refresh_error_status

    _refresh_error_status = None
    clean_refresh, token_err = auth_safety.normalize_header_token(refresh_jwt)
    if token_err:
        _session_error = f"Bluesky session refresh failed: {token_err}"
        _log(f"Session refresh failed: {_session_error}")
        return None
    if not clean_refresh:
        _session_error = "No Bluesky refresh token is available"
        return None

    try:
        response = http.request(
            "POST",
            BSKY_REFRESH_URL,
            headers={"Authorization": f"Bearer {clean_refresh}"},
            timeout=15,
            retries=0,
        )
        token = response.get("accessJwt")
        if not token:
            _session_error = "No accessJwt in refresh session response"
            return None
        _cached_token = token
        _cached_refresh_token = response.get("refreshJwt") or clean_refresh
        _token_created_at = time.monotonic()
        _session_error = None
        return token
    except http.HTTPError as e:
        _refresh_error_status = e.status_code
        _session_error = (
            "Bluesky refresh token is invalid (401/400)"
            if e.status_code in (400, 401)
            else f"Bluesky session refresh failed (HTTP {e.status_code})"
        )
        _log(f"Session refresh failed: {_session_error}")
        return None
    except Exception as e:
        _session_error = f"Bluesky session refresh failed: {type(e).__name__}"
        _log(f"Session refresh failed: {_session_error}")
        return None


def _reset_session_cache() -> None:
    global _cached_token, _cached_refresh_token, _token_created_at
    global _session_error, _refresh_error_status
    _cached_token = None
    _cached_refresh_token = None
    _token_created_at = 0.0
    _session_error = None
    _refresh_error_status = None


def _extract_core_subject(topic: str) -> str:
    """Extract core subject from verbose query for Bluesky search."""
    from .query import SOCIAL_NOISE, extract_core_subject
    return extract_core_subject(topic, noise=SOCIAL_NOISE)


def _parse_date(item: Dict[str, Any]) -> Optional[str]:
    """Parse date from Bluesky post to YYYY-MM-DD.

    AT Protocol uses ISO 8601 format in indexedAt and createdAt fields.
    """
    for key in ("indexedAt", "createdAt"):
        val = item.get(key)
        if val and isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass
    return None


def _exclusive_until_date(to_date: str) -> str:
    """Convert the inclusive pipeline end date to an RFC3339 exclusive bound."""
    try:
        value = datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)
        return value.strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        # Keep the request usable for callers that pass a provider-specific date value.
        return to_date


def _inclusive_since_date(from_date: str) -> str:
    """Convert the inclusive pipeline start date to an RFC3339 lower bound."""
    try:
        return datetime.strptime(from_date, "%Y-%m-%d").strftime("%Y-%m-%dT00:00:00Z")
    except ValueError:
        # Keep the request usable for callers that pass a provider-specific date value.
        return from_date


def search_bluesky(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search Bluesky via AT Protocol API.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        config: Config dict with BSKY_HANDLE and BSKY_APP_PASSWORD

    Returns:
        Dict with 'posts' list from AT Protocol response.
    """
    config = config or {}
    handle = config.get("BSKY_HANDLE", "")
    app_password = config.get("BSKY_APP_PASSWORD", "")

    if not handle or not app_password:
        return {"posts": [], "error": "Bluesky credentials not configured"}

    # One-shot hygiene warning if BSKY_APP_PASSWORD is not in app-password
    # form. createSession accepts main-account passwords too — but main
    # passwords have no scope (full account access), can't be revoked
    # individually, and rotating them breaks every service that holds them.
    # We warn but do not gate, matching the project's detect-don't-block
    # philosophy elsewhere.
    if not _validate_app_password_format(app_password):
        _log(
            "BSKY_APP_PASSWORD does not look like an app password "
            "(expected xxxx-xxxx-xxxx-xxxx, 19 chars). It may be a main "
            "account password — those work but are bad hygiene. Generate "
            "an app password at https://bsky.app/settings/app-passwords"
        )

    count = DEPTH_CONFIG.get(depth, DEPTH_CONFIG["default"])
    core_topic = _extract_core_subject(topic)

    _log(f"Searching for '{core_topic}' (depth={depth}, limit={count})")

    params = {
        "q": core_topic,
        "limit": str(min(count, 100)),
        "sort": "top",
        "since": _inclusive_since_date(from_date),
        "until": _exclusive_until_date(to_date),
    }
    search_base_url = _resolve_search_url(config)
    search_host = (urlsplit(search_base_url).hostname or _DEFAULT_BSKY_SEARCH_HOST).lower()
    trusted_host = _is_trusted_bsky_host(search_host)
    redirect_guard = _bsky_search_redirect_guard(search_host)
    url = f"{search_base_url}?{urlencode(params)}"

    def _search_with_token(token: Optional[str]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        req_kwargs: Dict[str, Any] = {
            "timeout": 30,
            "redirect_guard": redirect_guard,
        }
        clean_token: Optional[str] = None
        if token is not None:
            clean_token, token_err = auth_safety.normalize_header_token(token)
            if token_err or not clean_token:
                err_msg = token_err or "Invalid token format"
                _log(f"Search failed: {err_msg}")
                return None, f"Bluesky search failed: {err_msg}"
            req_kwargs["headers"] = {"Authorization": f"Bearer {clean_token}"}
        if not trusted_host:
            req_kwargs["public_only"] = True
        try:
            response = http.request("GET", url, **req_kwargs)
            return response, None
        except http.HTTPError as e:
            safe_err = auth_safety.scrub_secrets(str(e), (token, clean_token, app_password))
            _log(f"Search failed: {safe_err}")
            if e.status_code == 401:
                if token is None:
                    return None, (
                        f"Bluesky search host {search_host!r} rejected unauthenticated search (401); "
                        "bsky.social Bearer tokens are only sent to *.bsky.app / *.bsky.social hosts"
                    )
                return None, "refresh"
            if e.status_code == 403 and e.body and "cloudflare" in e.body.lower():
                return None, "Bluesky search blocked by Cloudflare (403). This is a network-level block - try a different network or VPN."
            return None, f"Bluesky search failed: {safe_err}"
        except Exception as e:
            safe_err = auth_safety.scrub_secrets(str(e), (token, clean_token, app_password))
            _log(f"Search failed: {type(e).__name__}: {safe_err}")
            return None, f"Bluesky search failed: {type(e).__name__}: {safe_err}"

    def _auth_and_search() -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not trusted_host:
            # Never exfiltrate a live bsky.social session JWT to an arbitrary third-party host.
            _log(f"Withholding bsky.social Bearer token from non-Bluesky search host {search_host!r}")
            return _search_with_token(None)
        token = _create_session(handle, app_password)
        if not token:
            error_msg = _session_error or "Bluesky session creation failed (unknown error)"
            return None, error_msg
        return _search_with_token(token)

    response, error_msg = _auth_and_search()
    if error_msg == "refresh":
        _log("Session expired; refreshing access token and retrying once")
        refreshed_token = _refresh_session(_cached_refresh_token)
        if refreshed_token:
            response, error_msg = _search_with_token(refreshed_token)
        elif not _cached_refresh_token or _refresh_error_status in (400, 401):
            _log("Refresh token missing or rejected; recreating session once")
            _reset_session_cache()
            response, error_msg = _auth_and_search()
            if error_msg == "refresh":
                error_msg = (
                    "Bluesky session remained unauthorized after refresh and re-authentication; "
                    "check credentials or app password"
                )
        else:
            response, error_msg = None, _session_error
        if error_msg == "refresh":
            error_msg = (
                "Bluesky session remained unauthorized after token refresh; "
                "check credentials or app password"
            )
    if error_msg:
        return {"posts": [], "error": error_msg}
    if response is None:
        return {"posts": [], "error": "Bluesky search failed (unknown error)"}

    posts = response.get("posts", [])
    _log(f"Found {len(posts)} posts")
    return response


def parse_bluesky_response(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse AT Protocol response into normalized item dicts.

    Returns:
        List of item dicts ready for normalization.
    """
    posts = response.get("posts", [])
    items = []

    for i, post in enumerate(posts):
        record = post.get("record") or {}
        text = record.get("text") or ""

        author = post.get("author") or {}
        handle = author.get("handle") or ""
        display_name = author.get("displayName") or handle

        # Post URI -> URL
        # URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
        uri = post.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""

        likes = post.get("likeCount") or 0
        reposts = post.get("repostCount") or 0
        replies = post.get("replyCount") or 0
        quotes = post.get("quoteCount") or 0

        date_str = _parse_date(post) or _parse_date(record)

        # Relevance: position-based (AT Protocol sorts by relevance with sort=top)
        rank_score = max(0.3, 1.0 - (i * 0.02))
        engagement_boost = min(0.2, math.log1p(likes + reposts) / 40)
        relevance = min(1.0, rank_score * 0.7 + engagement_boost + 0.1)

        items.append({
            "handle": handle,
            "display_name": display_name,
            "text": text,
            "url": url,
            "date": date_str,
            "engagement": {
                "likes": likes,
                "reposts": reposts,
                "replies": replies,
                "quotes": quotes,
            },
            "relevance": round(relevance, 2),
            "why_relevant": f"Bluesky: @{handle}: {text[:60]}" if text else f"Bluesky: {handle}",
        })

    return items
