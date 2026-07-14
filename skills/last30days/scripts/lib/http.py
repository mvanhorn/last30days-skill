"""HTTP utilities for last30days skill (stdlib only)."""

import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from pathlib import Path
from typing import Any, Dict, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, quote

from . import health
from . import log as _log

DEFAULT_TIMEOUT = 30


def log(msg: str):
    """Log debug message to stderr."""
    _log.debug(msg)


MAX_RETRIES = 5
MAX_429_RETRIES = 2
RETRY_DELAY = 2.0
# DNS resolution failures (gaierror) are transient — typically resolved by a
# brief backoff and retry. Use a dedicated minimum attempt count + exponential
# delays (1s, 2s, 4s) so callers that pass a small `retries` value still get a
# meaningful chance to recover from a transient resolution failure.
MIN_DNS_RETRIES = 3
USER_AGENT = "last30days-skill/3.0 (Assistant Skill)"

_failure_sink: ContextVar[Optional[list["HTTPError"]]] = ContextVar(
    "last30days_http_failure_sink",
    default=None,
)
_expected_miss_statuses: ContextVar[frozenset[int]] = ContextVar(
    "last30days_http_expected_miss_statuses",
    default=frozenset(),
)

_FIXTURE_FORMAT = "last30days-http-fixture/v1"
_FIXTURE_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "authorization", "cookie", "key", "secret", "token"}
)
_fixture_lock = threading.Lock()
_fixture_state: Optional[dict[str, Any]] = None
_NO_FIXTURE = object()
_fixture_module_capture: ContextVar[bool] = ContextVar(
    "last30days_fixture_module_capture",
    default=False,
)


def _is_secret_key(value: object) -> bool:
    key = re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_")
    return (
        key in _FIXTURE_SECRET_KEYS
        or key.endswith(("_api_key", "_authorization", "_cookie", "_secret", "_token"))
    )


def _scrub_fixture_value(
    value: Any,
    *,
    key: str = "",
    redactions: frozenset[str] = frozenset(),
) -> Any:
    """Remove credentials before a recorded exchange reaches disk."""
    if key and _is_secret_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {
            str(child_key): _scrub_fixture_value(
                child_value,
                key=str(child_key),
                redactions=redactions,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_scrub_fixture_value(item, redactions=redactions) for item in value]
    if isinstance(value, str):
        scrubbed = value
        for secret in sorted(redactions, key=len, reverse=True):
            if len(secret) >= 4:
                scrubbed = scrubbed.replace(secret, "<redacted>")
        return scrubbed
    return value


def _collect_secret_values(value: Any, *, key: str = "") -> set[str]:
    values: set[str] = set()
    if key and _is_secret_key(key) and value not in (None, ""):
        values.add(str(value))
        return values
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            values.update(_collect_secret_values(child_value, key=str(child_key)))
    elif isinstance(value, list):
        for child in value:
            values.update(_collect_secret_values(child))
    return values


def _fixture_redactions(
    url: str,
    headers: dict[str, str],
    json_data: Optional[Dict[str, Any]],
) -> frozenset[str]:
    values: set[str] = set()
    try:
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if _is_secret_key(key) and value:
                values.add(value)
    except ValueError:
        pass
    values.update(_collect_secret_values(headers))
    values.update(_collect_secret_values(json_data))
    return frozenset(values)


def _scrub_fixture_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        query = urlencode(
            [
                (key, "<redacted>" if _is_secret_key(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except ValueError:
        return url


def _fixture_request(
    method: str,
    url: str,
    json_data: Optional[Dict[str, Any]],
    raw: bool,
) -> dict[str, Any]:
    request_data: dict[str, Any] = {
        "method": method.upper(),
        "url": _scrub_fixture_url(url),
        "raw": bool(raw),
    }
    if json_data is not None:
        request_data["json"] = _scrub_fixture_value(json_data)
    return request_data


def _fixture_key(request_data: dict[str, Any]) -> str:
    return json.dumps(request_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@contextmanager
def recording_requests(path: str | Path):
    """Record scrubbed HTTP exchanges to ``path`` for offline eval replay.

    This process-global session is deliberate: source requests run in worker
    threads, so a ContextVar would not observe the complete pipeline fan-out.
    Nested or concurrent recording/replay sessions are rejected.
    """
    global _fixture_state
    target = Path(path).expanduser()
    if target.suffix.lower() != ".json":
        target = target / "http.json"
    with _fixture_lock:
        if _fixture_state is not None:
            raise RuntimeError("An HTTP fixture session is already active")
        _fixture_state = {
            "mode": "record",
            "path": target,
            "exchanges": [],
            "source_exchanges": [],
            # Secret VALUES from the environment, so module-seam recordings
            # scrub tokens echoed inside normal string fields (adapter error
            # messages, parsed item text), not just secret-named keys.
            "redactions": frozenset(
                value
                for key, value in os.environ.items()
                if _is_secret_key(key) and isinstance(value, str) and len(value) >= 4
            ),
        }
    completed = False
    try:
        yield target
        completed = True
    finally:
        with _fixture_lock:
            state = _fixture_state
            _fixture_state = None
        if state is not None and completed:
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": _FIXTURE_FORMAT,
                "exchanges": state["exchanges"],
                "source_exchanges": state["source_exchanges"],
            }
            temporary = target.with_name(f".{target.name}.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if os.name != "nt":
                temporary.chmod(0o644)
            temporary.replace(target)


@contextmanager
def fixture_module_capture(enabled: bool):
    """Suppress nested HTTP recording when a whole adapter result is captured."""
    token = _fixture_module_capture.set(enabled)
    try:
        yield
    finally:
        _fixture_module_capture.reset(token)


@contextmanager
def replaying_requests(path: str | Path):
    """Replay recorded exchanges and fail closed on any unrecorded request."""
    global _fixture_state
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "http.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("format") != _FIXTURE_FORMAT:
        raise ValueError(f"Unsupported HTTP fixture format in {target}")
    queues: dict[str, list[dict[str, Any]]] = {}
    for exchange in payload.get("exchanges") or []:
        queues.setdefault(_fixture_key(exchange["request"]), []).append(exchange["response"])
    source_queues: dict[str, list[Any]] = {}
    for exchange in payload.get("source_exchanges") or []:
        source_queues.setdefault(_fixture_key(exchange["request"]), []).append(exchange)
    with _fixture_lock:
        if _fixture_state is not None:
            raise RuntimeError("An HTTP fixture session is already active")
        _fixture_state = {
            "mode": "replay",
            "path": target,
            "queues": queues,
            "source_queues": source_queues,
        }
    try:
        yield target
        with _fixture_lock:
            unused = sum(len(values) for values in queues.values()) + sum(
                len(values) for values in source_queues.values()
            )
        if unused:
            raise AssertionError(f"HTTP fixture replay left {unused} unused exchange(s): {target}")
    finally:
        with _fixture_lock:
            _fixture_state = None


def _fixture_replay(request_data: dict[str, Any]) -> Any:
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "replay":
            return _NO_FIXTURE
        queue = state["queues"].get(_fixture_key(request_data))
        if not queue:
            raise AssertionError(
                "Unrecorded HTTP request during fixture replay: "
                f"{request_data['method']} {request_data['url']}"
            )
        response = queue.pop(0)
    if response.get("error"):
        error = response["error"]
        recorded_error = HTTPError(
            str(error.get("message") or "Recorded HTTP error"),
            status_code=error.get("status_code"),
            body=error.get("body"),
            outcome_state=error.get("outcome_state"),
        )
        _raise(recorded_error)
    return response.get("value")


def _fixture_record(
    request_data: dict[str, Any],
    *,
    value: Any = None,
    error: Optional["HTTPError"] = None,
    redactions: frozenset[str] = frozenset(),
) -> None:
    if _fixture_module_capture.get():
        return
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        response: dict[str, Any]
        if error is None:
            response = {"value": _scrub_fixture_value(value, redactions=redactions)}
        else:
            response = {
                "error": _scrub_fixture_value(
                    {
                        "message": str(error),
                        "status_code": error.status_code,
                        "body": error.body,
                        "outcome_state": error.outcome_state,
                    },
                    redactions=redactions,
                )
            }
        state["exchanges"].append({"request": request_data, "response": response})


def fixture_source_replay(request_data: dict[str, Any]) -> tuple[bool, Any]:
    """Return a recorded CLI-backed source result when replay is active."""
    scrubbed = _scrub_fixture_value(request_data)
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "replay":
            return False, None
        queue = state["source_queues"].get(_fixture_key(scrubbed))
        if not queue:
            raise AssertionError(
                "Unrecorded CLI-backed source request during fixture replay: "
                f"{request_data.get('source', 'unknown')}"
            )
        exchange = queue.pop(0)
    if exchange.get("type") == "error":
        error = exchange.get("error") or {}
        raise RecordedSourceError(
            str(error.get("message") or "Recorded source error"),
            exception_type=str(error.get("exception_type") or "Exception"),
            outcome_state=error.get("outcome_state"),
        )
    return True, exchange.get("value")


def fixture_source_record(request_data: dict[str, Any], value: Any) -> None:
    """Record the parsed output of a source adapter that bypasses http.py."""
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        session_redactions = state.get("redactions") or frozenset()
        state["source_exchanges"].append(
            {
                "request": _scrub_fixture_value(request_data, redactions=session_redactions),
                "value": _scrub_fixture_value(value, redactions=session_redactions),
            }
        )


def fixture_source_record_error(request_data: dict[str, Any], error: Exception) -> None:
    """Record a replayable failure from a source adapter that bypasses http.py."""
    with _fixture_lock:
        state = _fixture_state
        if state is None or state["mode"] != "record":
            return
        session_redactions = state.get("redactions") or frozenset()
        state["source_exchanges"].append(
            {
                "request": _scrub_fixture_value(request_data, redactions=session_redactions),
                "type": "error",
                "error": _scrub_fixture_value(
                    {
                        "exception_type": type(error).__name__,
                        "message": str(error),
                        "outcome_state": getattr(error, "outcome_state", None),
                    }
                , redactions=session_redactions),
            }
        )


class RecordedSourceError(RuntimeError):
    """Failure restored from a recorded module-backed source exchange."""

    def __init__(
        self,
        message: str,
        *,
        exception_type: str,
        outcome_state: Optional[str] = None,
    ):
        super().__init__(message)
        self.exception_type = exception_type
        self.outcome_state = outcome_state


def _is_dns_failure(err: urllib.error.URLError) -> bool:
    """Return True if a URLError was caused by DNS resolution (gaierror)."""
    return isinstance(getattr(err, "reason", None), socket.gaierror)


class HTTPError(Exception):
    """HTTP request error with status code."""
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        body: Optional[str] = None,
        outcome_state: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.outcome_state = outcome_state or classify_failure(
            status_code=status_code,
            message=message,
        )


@contextmanager
def capture_failures():
    """Capture terminal request failures in the current retrieval context.

    Source modules historically catch ``HTTPError`` and return an empty result.
    The context-local sink lets the pipeline retain that failure without shared
    mutable state across its worker threads.
    """
    failures: list[HTTPError] = []
    token = _failure_sink.set(failures)
    try:
        yield failures
    finally:
        _failure_sink.reset(token)


@contextmanager
def expected_misses(*status_codes: int):
    """Exclude adapter-declared probe misses from captured run failures."""
    token = _expected_miss_statuses.set(
        _expected_miss_statuses.get().union(status_codes)
    )
    try:
        yield
    finally:
        _expected_miss_statuses.reset(token)


def submit_with_context(executor, func, /, *args, **kwargs):
    """Submit a worker with the caller's failure-capture context."""
    context = copy_context()
    return executor.submit(context.run, func, *args, **kwargs)


def _record_failure(error: HTTPError) -> None:
    if error.status_code in _expected_miss_statuses.get():
        return
    sink = _failure_sink.get()
    if sink is not None:
        sink.append(error)


def _raise(error: HTTPError) -> None:
    _record_failure(error)
    raise error


def classify_failure(*, status_code: Optional[int] = None, message: str = "") -> str:
    """Map a request failure to the doctor-aligned per-run vocabulary."""
    text = message.lower()
    if status_code == 429 or any(
        marker in text for marker in ("http 429", "status 429", "rate limit", "too many requests")
    ):
        return health.RATE_LIMITED
    if status_code in (401, 402, 403) or any(
        marker in text
        for marker in (
            "http 401",
            "http 402",
            "http 403",
            "status 401",
            "status 402",
            "status 403",
            "unauthorized",
            "forbidden",
            "authentication failed",
            "expired token",
        )
    ):
        return health.AUTH_FAILED
    if status_code == 408 or "timed out" in text or "timeout" in text:
        return health.TIMEOUT
    if any(
        marker in text
        for marker in (
            "invalid json",
            "json decode",
            "schema",
            "interstitial",
            "non-json",
        )
    ):
        return health.SCHEMA_DRIFT
    if any(
        marker in text
        for marker in (
            "url error",
            "connection error",
            "connection refused",
            "connection reset",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname",
            "dns",
            "network is unreachable",
        )
    ):
        return health.UNREACHABLE
    return health.ERROR


def request(
    method: str,
    url: str,
    headers: Optional[Dict[str, str]] = None,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
    max_429_retries: int = MAX_429_RETRIES,
    raw: bool = False,
) -> Union[Dict[str, Any], str]:
    """Make an HTTP request and return JSON response.

    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        headers: Optional headers dict
        json_data: Optional JSON body (for POST)
        params: Optional query-string params. Values are stringified. None values
            are dropped. If ``url`` already has a query string, ``params`` is appended.
        timeout: Request timeout in seconds
        retries: Number of retries on failure
        max_429_retries: Maximum 429 retries before giving up (separate cap)
        raw: If True, return raw response text instead of parsed JSON

    Returns:
        Parsed JSON response as dict, or raw text string if raw=True.

    Raises:
        HTTPError: On request failure
    """
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)

    if params:
        filtered = {k: str(v) for k, v in params.items() if v is not None}
        if filtered:
            separator = "&" if ("?" in url) else "?"
            url = f"{url}{separator}{urlencode(filtered)}"
    # Encode any non-ASCII characters to prevent UnicodeEncodeError from
    # http.client.HTTPConnection.putrequest (which uses latin-1 internally).
    # Only encode path, query, and fragment — not the hostname (netloc), which
    # needs IDNA encoding instead of percent-encoding for non-ASCII domains.
    parts = urlsplit(url)
    safe = '/:@!$&\'()*+,;=-._~%?#[]=+'
    url = urlunsplit((
        parts.scheme,
        parts.netloc,
        quote(parts.path, safe=safe),
        quote(parts.query, safe=safe),
        quote(parts.fragment, safe=safe),
    ))

    fixture_request = _fixture_request(method, url, json_data, raw)
    fixture_redactions = _fixture_redactions(url, headers, json_data)
    replayed = _fixture_replay(fixture_request)
    if replayed is not _NO_FIXTURE:
        return replayed

    data = None
    if json_data is not None:
        data = json.dumps(json_data).encode('utf-8')
        headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    safe_url = re.sub(r'([?&])(key|api_key|token|secret)=[^&]*', r'\1\2=***', url)
    log(f"{method} {safe_url}")

    last_error = None
    rate_limit_count = 0
    # DNS failures get a dedicated minimum attempt count + exponential backoff.
    # `effective_retries` is the actual loop bound; we expand it on the first
    # gaierror if the caller passed a smaller `retries` value than MIN_DNS_RETRIES.
    effective_retries = retries
    dns_attempts = 0
    attempt = 0

    def raise_recorded(error: HTTPError) -> None:
        _fixture_record(fixture_request, error=error, redactions=fixture_redactions)
        _raise(error)

    while attempt < effective_retries:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode('utf-8')
                log(f"Response: {response.status} ({len(body)} bytes)")
                if raw:
                    _fixture_record(fixture_request, value=body, redactions=fixture_redactions)
                    return body
                parsed = json.loads(body) if body else {}
                _fixture_record(fixture_request, value=parsed, redactions=fixture_redactions)
                return parsed
        except urllib.error.HTTPError as e:
            body = None
            try:
                body = e.read().decode('utf-8')
            except (OSError, UnicodeDecodeError):
                pass
            log(f"HTTP Error {e.code}: {e.reason}")
            if body:
                snippet = " ".join(body.split())
                log(f"Error body: {snippet[:200]}")
            last_error = HTTPError(f"HTTP {e.code}: {e.reason}", e.code, body)

            # Don't retry client errors (4xx) except rate limits
            if 400 <= e.code < 500 and e.code != 429:
                raise_recorded(last_error)

            # Cap 429 retries separately to avoid wasting latency
            if e.code == 429:
                rate_limit_count += 1
                if rate_limit_count >= max_429_retries:
                    raise_recorded(last_error)

            # HTTP errors respect the caller's original `retries`; only DNS
            # failures get the widened `effective_retries` budget.
            if attempt < retries - 1:
                if e.code == 429:
                    # Respect Retry-After header, fall back to exponential backoff
                    retry_after = e.headers.get("Retry-After") if hasattr(e, 'headers') else None
                    if retry_after:
                        try:
                            delay = float(retry_after)
                        except ValueError:
                            delay = RETRY_DELAY * (2 ** attempt) + 1
                    else:
                        delay = RETRY_DELAY * (2 ** attempt) + 1  # 3s, 5s, 9s...
                    log(f"Rate limited (429). Waiting {delay:.1f}s before retry {attempt + 2}/{retries}")
                else:
                    delay = RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
            else:
                # Caller's original retry budget exhausted; an earlier DNS
                # failure may have widened `effective_retries`, but that
                # widening is DNS-only — don't grant extra HTTP attempts.
                break
        except urllib.error.URLError as e:
            log(f"URL Error: {e.reason}")
            reason = getattr(e, "reason", None)
            # urllib commonly wraps socket.timeout (an alias of TimeoutError
            # since 3.10) in URLError; classify those as timeouts, not
            # unreachable hosts, so the recovery guidance is right.
            wrapped_timeout = isinstance(reason, TimeoutError) or "timed out" in str(reason).lower()
            last_error = HTTPError(
                f"URL Error: {e.reason}",
                outcome_state=health.TIMEOUT if wrapped_timeout else health.UNREACHABLE,
            )
            if _is_dns_failure(e):
                # DNS resolution failures are transient; expand the retry budget
                # to MIN_DNS_RETRIES if the caller passed fewer, and use
                # exponential backoff (1s, 2s, 4s, ...) instead of the linear
                # default. Counts DNS attempts separately so other URLError
                # causes don't bypass the regular retry budget.
                dns_attempts += 1
                if effective_retries < MIN_DNS_RETRIES:
                    log(
                        f"DNS resolution failed; expanding retry budget from "
                        f"{effective_retries} to {MIN_DNS_RETRIES}"
                    )
                    effective_retries = MIN_DNS_RETRIES
                if attempt < effective_retries - 1:
                    delay = 2 ** (dns_attempts - 1)  # 1s, 2s, 4s, 8s, ...
                    log(
                        f"DNS resolution failure (attempt {dns_attempts}); "
                        f"retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
            elif attempt < retries - 1:
                # Non-DNS URLError (e.g. ConnectionRefused) respects the
                # caller's original retry budget, not the DNS-widened bound.
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                # Caller's original retry budget exhausted; an earlier DNS
                # failure widening `effective_retries` does not carry over
                # to non-DNS error paths.
                break
        except json.JSONDecodeError as e:
            log(f"JSON decode error: {e}")
            last_error = HTTPError(
                f"Invalid JSON response: {e}",
                outcome_state=health.SCHEMA_DRIFT,
            )
            raise_recorded(last_error)
        except (OSError, TimeoutError, ConnectionResetError) as e:
            # Handle socket-level errors (connection reset, timeout, etc.)
            log(f"Connection error: {type(e).__name__}: {e}")
            state = health.TIMEOUT if isinstance(e, TimeoutError) else health.UNREACHABLE
            last_error = HTTPError(
                f"Connection error: {type(e).__name__}: {e}",
                outcome_state=state,
            )
            if attempt < retries - 1:
                # Socket errors respect the caller's original retry budget.
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                # Original budget exhausted; DNS widening doesn't apply here.
                break

        attempt += 1

    if last_error:
        raise_recorded(last_error)
    error = HTTPError("Request failed with no error details")
    raise_recorded(error)


def get(url: str, headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
    """Make a GET request."""
    return request("GET", url, headers=headers, **kwargs)


def post(url: str, json_data: Dict[str, Any], headers: Optional[Dict[str, str]] = None, **kwargs) -> Dict[str, Any]:
    """Make a POST request with JSON body."""
    return request("POST", url, headers=headers, json_data=json_data, **kwargs)


def post_raw(url: str, json_data: Dict[str, Any], headers: Optional[Dict[str, str]] = None, **kwargs) -> str:
    """Make a POST request with JSON body and return raw text."""
    return request("POST", url, headers=headers, json_data=json_data, raw=True, **kwargs)


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def get_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
    accept: str = "*/*",
    headers: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Fetch a URL and return decoded text, or None on any failure.

    Keyless helper for Reddit RSS and shreddit HTML endpoints — the free path
    that replaced the now-403 ``.json`` endpoints. Sends a browser User-Agent
    and never raises: returns None on HTTP error, network failure, or timeout
    so tiered callers can fall through to the next source.

    Args:
        url: Request URL
        timeout: HTTP timeout per attempt in seconds
        retries: Number of retries on failure (kept low — these tiers fail fast)
        accept: Accept header value (e.g. "application/atom+xml", "text/html")
        headers: Optional extra headers merged over the defaults

    Returns:
        Decoded response body as text, or None on failure.
    """
    merged = {
        "User-Agent": BROWSER_USER_AGENT,
        "Accept": accept,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        merged.update(headers)
    try:
        return request(
            "GET", url, headers=merged, timeout=timeout, retries=retries, raw=True
        )
    except HTTPError as e:
        log(f"get_text failed ({e}): {url}")
        return None


class RateLimiter:
    """Thread-safe token-bucket throttle for an endpoint family.

    The keyless source tiers run under the pipeline's ThreadPoolExecutor, so a
    multi-subquery run can fire many requests at the same host at once. A bare
    per-request retry budget does not prevent that stampede — it only reacts
    after a 429. A token bucket bounds the *sustained* rate while still allowing
    a short burst, so legitimate parallelism is preserved (unlike a strict
    min-interval gate that would serialize every concurrent caller and could
    push later futures past their result timeouts).

    ``rate_per_sec`` tokens refill per second; ``burst`` is the bucket capacity
    (max simultaneous calls before throttling kicks in). The lock is released
    while sleeping so waiting threads don't serialize on each other.
    """

    def __init__(self, rate_per_sec: float, burst: int | None = None):
        self.rate = rate_per_sec
        self.capacity = burst if burst is not None else max(1, int(rate_per_sec))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Consume one token, blocking only when the bucket is empty."""
        while True:
            with self._lock:
                now = time.monotonic()
                # Clamp elapsed to >= 0: a backward clock reading must never
                # drive tokens negative (which would spin this loop forever).
                elapsed = max(0.0, now - self._last)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            time.sleep(wait)


# Shared across all keyless Reddit tiers (RSS, listing, shreddit) so their
# combined fan-out is throttled as one family. Burst lets the parallel
# enrichment workers proceed; sustained rate caps the stampede.
REDDIT_KEYLESS_LIMITER = RateLimiter(rate_per_sec=5.0, burst=5)


def reddit_keyless_get_text(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
    accept: str = "*/*",
    headers: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """get_text for the keyless Reddit tiers, throttled by a shared limiter.

    Same contract as :func:`get_text` (returns None on any failure) but spaces
    requests via :data:`REDDIT_KEYLESS_LIMITER` so a broad multi-query run does
    not stampede Reddit's keyless endpoints and trip blocks.
    """
    REDDIT_KEYLESS_LIMITER.acquire()
    return get_text(url, timeout=timeout, retries=retries, accept=accept, headers=headers)


def scrapecreators_headers(token: str) -> Dict[str, str]:
    """Build ScrapeCreators request headers (x-api-key + JSON content type)."""
    return {
        "x-api-key": token,
        "Content-Type": "application/json",
    }


def get_reddit_json(path: str, timeout: int = DEFAULT_TIMEOUT, retries: int = MAX_RETRIES) -> Dict[str, Any]:
    """Fetch Reddit thread JSON.

    Args:
        path: Reddit path (e.g., /r/subreddit/comments/id/title)
        timeout: HTTP timeout per attempt in seconds
        retries: Number of retries on failure

    Returns:
        Parsed JSON response
    """
    # Ensure path starts with /
    if not path.startswith('/'):
        path = '/' + path

    # Remove trailing slash and add .json
    path = path.rstrip('/')
    if not path.endswith('.json'):
        path = path + '.json'

    url = f"https://www.reddit.com{path}?raw_json=1"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    return get(url, headers=headers, timeout=timeout, retries=retries)
