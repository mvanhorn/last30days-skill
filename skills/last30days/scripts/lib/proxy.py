"""Metered proxy client for the optional proxy mode (LAST30DAYS_API_MODE=proxy).

With LAST30DAYS_API_MODE=proxy, LAST30DAYS_API_KEY, and LAST30DAYS_API_BASE
set, the local engine runs as usual and only paid provider calls that have no
user key of their own go through the hosted service's metered proxy:

    {base}/proxy/{provider}{path}

``env.get_config`` fills each absent proxy-lane key (``PROVIDER_KEYS``) with
``SENTINEL``, so the existing lane predicates light up. ``http.request`` is the
one seam that recognizes the sentinel in a request's auth headers, rewrites the
call to the proxy, strips provider auth, and adds the bearer key, a stable
per-call Idempotency-Key, the per-run X-L30D-Session, and X-L30D-Deadline.
Calls that carry a user's own key, and every free source, never touch it.

Like ``hosted.py`` this module carries no pricing logic: the charge for each
call is whatever the service reports in ``x-l30d-charge-credits``. The API key
is never printed, logged, or persisted here.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

from . import env

# Placeholder that marks a provider key as "served by the proxy". It is never a
# credential: http.request strips it before any request leaves the machine.
SENTINEL = env.PROXY_SENTINEL

# Lanes the proxy serves, keyed by the provider slug in the proxy path.
PROVIDER_KEYS = {
    "scrapecreators": "SCRAPECREATORS_API_KEY",
    "xai": "XAI_API_KEY",
    "serper": "SERPER_API_KEY",
    "parallel": "PARALLEL_API_KEY",
}

_PROVIDER_HOSTS = {
    "api.scrapecreators.com": "scrapecreators",
    "api.x.ai": "xai",
    "google.serper.dev": "serper",
    "api.parallel.ai": "parallel",
}

# Method and path of every call the proxy forwards. The server's allowlist is
# authoritative (it also checks query params and body fields); this copy only
# keeps a call the server would refuse from leaving the machine at all.
_SCRAPECREATORS_PATHS = frozenset({
    "/v1/tiktok/search/hashtag",
    "/v1/tiktok/search/keyword",
    "/v3/tiktok/profile/videos",
    "/v1/tiktok/video/transcript",
    "/v1/tiktok/video/comments",
    "/v2/instagram/reels/search",
    "/v1/instagram/user/reels",
    "/v1/google/search",
    "/v1/instagram/post",
    "/v2/instagram/media/transcript",
    "/v2/instagram/post/comments",
    "/v1/threads/search",
    "/v1/pinterest/search",
    "/v1/youtube/search",
    "/v1/youtube/video/transcript",
    "/v1/youtube/video/comments",
    "/v1/reddit/search",
    "/v1/reddit/subreddit/search",
    "/v1/reddit/post/comments",
    "/v1/linkedin/search/posts",
    "/v1/linkedin/profile",
    "/v1/telegram/channel/posts",
    "/v1/facebook/adLibrary/search/ads",
    "/v1/facebook/adLibrary/search/companies",
    "/v1/facebook/adLibrary/company/ads",
    "/v1/facebook/adLibrary/ad/transcript",
})
_ROUTES = {
    "scrapecreators": ("GET", _SCRAPECREATORS_PATHS),
    "xai": ("POST", frozenset({"/v1/responses"})),
    "serper": ("POST", frozenset({"/search"})),
    "parallel": ("POST", frozenset({"/v1/search"})),
}

# Provider auth headers. Every one is dropped from a proxied call.
_PROVIDER_AUTH_HEADERS = frozenset({"authorization", "x-api-key"})

CHARGE_HEADER = "x-l30d-charge-credits"
SESSION_HEADER = "X-L30D-Session"
DEADLINE_HEADER = "X-L30D-Deadline"
IDEMPOTENCY_HEADER = "Idempotency-Key"

# Proxy-originated error codes that end a lane for the rest of the run.
_EXHAUSTING_CODES = frozenset({"insufficient_credits", "spend_ceiling"})


class ProxyUnavailable(Exception):
    """A proxy-backed call that must not be sent (lane off for this run)."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Routed:
    url: str
    headers: dict[str, str]
    provider: str


_lock = threading.Lock()
_session_id: Optional[str] = None
_charged = Decimal(0)
_calls = 0
_exhausted: Optional[str] = None
_unavailable: set[str] = set()


def reset() -> None:
    """Forget this process's session (tests, or a fresh run in one process)."""
    global _session_id, _charged, _calls, _exhausted
    with _lock:
        _session_id = None
        _charged = Decimal(0)
        _calls = 0
        _exhausted = None
        _unavailable.clear()


def _api_base() -> str:
    return (os.environ.get("LAST30DAYS_API_BASE") or "").strip().rstrip("/")


def _api_key() -> str:
    return (env.read_secret_env("LAST30DAYS_API_KEY") or "").strip()


def configured() -> bool:
    """True when proxy mode is selected and its key and base are usable."""
    if not env.proxy_mode_requested() or not _api_key():
        return False
    from . import providers

    base = _api_base()
    return bool(base) and providers.allowed_base_url_override(base)


def is_sentinel(value: Any) -> bool:
    return value == SENTINEL


def real_key(value: Any) -> Any:
    """The value unless it only marks a proxy lane (then None).

    For readers that hand a key to something other than ``http.request``
    (a subprocess, a raw-urllib client): the sentinel must never reach them.
    """
    return None if is_sentinel(value) else value


def fill_config(config: dict[str, Any]) -> None:
    """Mark each absent proxy-lane key as served by the proxy."""
    if not configured():
        return
    for var in PROVIDER_KEYS.values():
        if not config.get(var):
            config[var] = SENTINEL


def via_api_key(config: Mapping[str, Any]) -> list[str]:
    """Config keys whose lane is served by the proxy, in a stable order."""
    return [var for var in PROVIDER_KEYS.values() if is_sentinel(config.get(var))]


def _carries_sentinel(headers: Mapping[str, str]) -> bool:
    return any(isinstance(value, str) and SENTINEL in value for value in headers.values())


def _session() -> str:
    global _session_id
    with _lock:
        if _session_id is None:
            _session_id = f"skill-{uuid.uuid4().hex}"
        return _session_id


def new_idempotency_key() -> str:
    return uuid.uuid4().hex


def route(method: str, url: str, headers: Mapping[str, str]) -> Optional[Routed]:
    """Rewrite a sentinel-backed provider call to the proxy.

    Returns None for every call that does not carry the sentinel (free
    sources, and paid lanes using the user's own key). Raises
    ``ProxyUnavailable`` for a sentinel-backed call the proxy cannot serve, so
    the sentinel never leaves the machine.
    """
    if not _carries_sentinel(headers):
        return None
    if not configured():
        raise ProxyUnavailable("proxy mode is not configured for this call")
    parts = urlsplit(url)
    provider = _PROVIDER_HOSTS.get((parts.hostname or "").lower())
    if provider is None or parts.scheme != "https" or parts.port not in (None, 443):
        raise ProxyUnavailable("this endpoint is not available through the API key")
    with _lock:
        exhausted = _exhausted
        unavailable = provider in _unavailable
    if exhausted:
        raise ProxyUnavailable(exhausted, status_code=402)
    if unavailable:
        raise ProxyUnavailable(f"{provider} is not available through the API key", status_code=503)
    allowed_method, paths = _ROUTES[provider]
    if method.upper() != allowed_method or parts.path not in paths:
        raise ProxyUnavailable("this endpoint is not available through the API key", status_code=404)

    target = f"{_api_base()}/proxy/{provider}{parts.path}"
    if parts.query:
        target = f"{target}?{parts.query}"
    rewritten = {
        name: value
        for name, value in headers.items()
        if name.lower() not in _PROVIDER_AUTH_HEADERS
        and not (isinstance(value, str) and SENTINEL in value)
    }
    rewritten["Authorization"] = f"Bearer {_api_key()}"
    rewritten[SESSION_HEADER] = _session()
    return Routed(url=target, headers=rewritten, provider=provider)


def deadline_value(seconds: float) -> str:
    """X-L30D-Deadline text for a per-attempt timeout in seconds."""
    seconds = max(float(seconds), 0.001)
    if seconds == int(seconds):
        return str(int(seconds))
    return f"{seconds:.3f}".rstrip("0").rstrip(".")


def _header(headers: Any, name: str) -> Optional[str]:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    value = getter(name)
    if value is None:
        value = getter(name.title())
    if value is None and isinstance(headers, Mapping):
        for key, candidate in headers.items():
            if isinstance(key, str) and key.lower() == name:
                value = candidate
                break
    return value if isinstance(value, str) else None


def record_charge(headers: Any, *, settled: bool = True) -> None:
    """Add one call's reported charge to the session total.

    ``settled`` counts the call: a successful response, or the replay of a
    call already charged. A passed-through upstream error reports a zero
    charge and is not a charged call.
    """
    global _charged, _calls
    raw = _header(headers, CHARGE_HEADER)
    if raw is None:
        return
    try:
        amount = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        return
    if not amount.is_finite() or amount < 0:
        return
    with _lock:
        _charged += amount
        if settled:
            _calls += 1


def _error_code(body: Optional[str]) -> Optional[str]:
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    code = parsed.get("code")
    return code if isinstance(code, str) else None


def _billing_hint() -> str:
    root = re.sub(r"/api/v\d+$", "", _api_base())
    return f"Add credits at {root}/dashboard/billing" if root else "Add credits to keep using paid sources."


def observe_error(provider: str, status: int, body: Optional[str], headers: Any) -> bool:
    """Note a proxied error response. True when the caller must not retry.

    Only proxy-originated errors carry a ``code``; an upstream provider's own
    401/402/429 passes through without one and is handled as before.
    """
    global _exhausted
    code = _error_code(body)
    record_charge(headers, settled=code == "already_settled")
    if code is None:
        return False
    if code in _EXHAUSTING_CODES:
        if code == "insufficient_credits":
            message = "API key is out of credits; paid sources are skipped for the rest of this run"
        else:
            message = "API key reached its hourly spend ceiling; paid sources are skipped for the rest of this run"
        first = False
        with _lock:
            if _exhausted is None:
                _exhausted = message
                first = True
        if first:
            sys.stderr.write(f"[last30days] {message}. {_billing_hint()}\n")
            sys.stderr.flush()
        return True
    if code == "provider_unavailable":
        with _lock:
            _unavailable.add(provider)
        return True
    return code in {"unsupported_endpoint", "in_flight", "idempotency_conflict", "already_settled"}


def exhausted() -> bool:
    with _lock:
        return _exhausted is not None


def session_summary() -> tuple[int, Decimal]:
    """(charged calls, credits) reported by the proxy for this run."""
    with _lock:
        return _calls, _charged


def session_id() -> Optional[str]:
    """This run's X-L30D-Session id, once a call has used the proxy."""
    with _lock:
        return _session_id


def format_credits(amount: Decimal) -> str:
    text = f"{amount.quantize(Decimal('0.000001')):f}".rstrip("0").rstrip(".")
    return text or "0"


def footer_line() -> Optional[str]:
    """The footer line for a run that used the proxy, else None."""
    with _lock:
        used = _session_id is not None
    if not used:
        return None
    calls, credits = session_summary()
    noun = "call" if calls == 1 else "calls"
    return f"💳 API key: {format_credits(credits)} credits across {calls} paid {noun}"
