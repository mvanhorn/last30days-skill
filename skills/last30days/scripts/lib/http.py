"""HTTP utilities for last30days skill (stdlib only)."""

import json
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

from . import cost_markers
from . import log as _log

DEFAULT_TIMEOUT = 30

# Paid per-call APIs identifiable by a distinct host. All callers funnel through
# request(), so host detection here captures every call for real-cost billing
# (plan 2026-06-17-001 U3 ScrapeCreators, U4 search backends). NOTE: Perplexity
# is intentionally absent — it shares openrouter.ai with the reasoning LLM
# clients (already billed in providers.generate_json), so it is billed at its own
# call site (perplexity.py) where the model is known.
_PAID_HOSTS = {
    "api.scrapecreators.com": "scrapecreators",
    "api.search.brave.com": "brave",
    "api.exa.ai": "exa",
    "google.serper.dev": "serper",
    "api.parallel.ai": "parallel",
}
_SC_SOURCE_RE = re.compile(r"api\.scrapecreators\.com/v\d+/([a-z0-9_-]+)", re.I)


def _paid_provider_for(url: str):
    """Return the paid-API provider slug for a URL's host, or None."""
    for host, provider in _PAID_HOSTS.items():
        if host in (url or ""):
            return provider
    return None


def _scrapecreators_source(url: str) -> str:
    """Short source slug (reddit/tiktok/instagram/...) from a ScrapeCreators URL,
    for the per-model cost breakdown. Falls back to 'scrapecreators'."""
    m = _SC_SOURCE_RE.search(url or "")
    return m.group(1).lower() if m else "scrapecreators"


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


def _is_dns_failure(err: urllib.error.URLError) -> bool:
    """Return True if a URLError was caused by DNS resolution (gaierror)."""
    return isinstance(getattr(err, "reason", None), socket.gaierror)


class HTTPError(Exception):
    """HTTP request error with status code."""
    def __init__(self, message: str, status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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
    while attempt < effective_retries:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode('utf-8')
                log(f"Response: {response.status} ({len(body)} bytes)")
                # One marker per successful paid call (U3 ScrapeCreators, U4
                # search backends). Emitted once on success, so retries of a
                # failed call are not charged. ScrapeCreators carries its source
                # as the model for a per-source breakdown.
                _paid = _paid_provider_for(url)
                if _paid:
                    _model = _scrapecreators_source(url) if _paid == "scrapecreators" else ""
                    cost_markers.emit_cost(_paid, model=_model)
                if raw:
                    return body
                return json.loads(body) if body else {}
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
                raise last_error

            # Cap 429 retries separately to avoid wasting latency
            if e.code == 429:
                rate_limit_count += 1
                if rate_limit_count >= max_429_retries:
                    raise last_error

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
            last_error = HTTPError(f"URL Error: {e.reason}")
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
            last_error = HTTPError(f"Invalid JSON response: {e}")
            raise last_error
        except (OSError, TimeoutError, ConnectionResetError) as e:
            # Handle socket-level errors (connection reset, timeout, etc.)
            log(f"Connection error: {type(e).__name__}: {e}")
            last_error = HTTPError(f"Connection error: {type(e).__name__}: {e}")
            if attempt < retries - 1:
                # Socket errors respect the caller's original retry budget.
                time.sleep(RETRY_DELAY * (attempt + 1))
            else:
                # Original budget exhausted; DNS widening doesn't apply here.
                break

        attempt += 1

    if last_error:
        raise last_error
    raise HTTPError("Request failed with no error details")


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
