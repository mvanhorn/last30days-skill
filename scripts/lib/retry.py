"""Retry decorator for source-level search functions.

Provides exponential backoff for transient errors (429, 503, connection
errors) without retrying permanent client errors (4xx except 429).

Usage:
    from lib.retry import retry

    @retry(max_attempts=2, backoff_base=1.5)
    def _search_reddit(topic, ...):
        ...
"""

import functools
import time

from lib.http import HTTPError
from lib.log import logger


# Exceptions that indicate transient failures worth retrying
_TRANSIENT_EXCEPTIONS = (
    ConnectionError,
    ConnectionResetError,
    TimeoutError,
    OSError,
)


def _is_transient(exc: Exception) -> bool:
    """Determine if an exception represents a transient failure."""
    # Direct transient exceptions
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return True

    # HTTP errors: retry 429 (rate limit) and 5xx, not other 4xx
    if isinstance(exc, HTTPError):
        if exc.status_code is None:
            return True  # Connection-level error
        if exc.status_code == 429:
            return True
        if exc.status_code >= 500:
            return True
        return False  # 4xx client errors are permanent

    return False


def retry(max_attempts: int = 2, backoff_base: float = 1.5):
    """Decorator that retries a function on transient failures.

    Args:
        max_attempts: Total attempts (1 = no retry, 2 = one retry). Max 5.
        backoff_base: Base for exponential backoff in seconds. Max 10.
            Delay = backoff_base * (2 ** attempt_index), so:
            attempt 1 retry: 1.5s, attempt 2 retry: 3.0s

    Non-transient errors (4xx except 429) are raised immediately.

    Note: Functions that call http.request() already have HTTP-level retries.
    This decorator is for source-level retries (e.g., retrying the entire
    search function when the API backend itself is down).
    """
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("max_attempts must be between 1 and 5")
    if backoff_base < 0 or backoff_base > 10:
        raise ValueError("backoff_base must be between 0 and 10")

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not _is_transient(exc):
                        raise  # Permanent error, don't retry

                    if attempt < max_attempts - 1:
                        delay = backoff_base * (2 ** attempt)
                        logger.warning(
                            "[retry] %s failed (attempt %d/%d): %s. "
                            "Retrying in %.1fs",
                            func.__name__, attempt + 1, max_attempts,
                            exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        logger.warning(
                            "[retry] %s failed (attempt %d/%d): %s. "
                            "No more retries.",
                            func.__name__, attempt + 1, max_attempts, exc,
                        )
            raise last_exc
        return wrapper
    return decorator
