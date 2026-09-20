"""Keyless URL-to-markdown fetch (floor tier for engine-side page reads).

Turns any URL into clean, JS-rendered markdown via Jina Reader's free hosted
endpoint (``https://r.jina.ai/{url}``) with no API key. This is a *fallback*
tier, never the primary firehose:

- On agent hosts with a native fetch tool, prefer that (this module exists for
  headless/cron and hosts without one).
- The free tier is rate-limited and returns cached snapshots (staleness), so
  callers should treat results as best-effort and record when it was used.
- The target URL is sent to a third party; only use for public-research fetches.

Never raises. Returns a typed :class:`KeylessFetchResult` carrying the failure
reason on any error, so tiered callers (and the source-health layer) can fall
through or report degradation instead of seeing a bare empty string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from . import http

JINA_READER_PREFIX = "https://r.jina.ai/"

# Conservative timeout: Jina renders the page server-side, so it is slower than
# a plain GET, but we are the floor tier and must not stall the pipeline.
DEFAULT_FETCH_TIMEOUT = 30


@dataclass
class KeylessFetchResult:
    """Result of a keyless page fetch.

    ``ok`` is True only when markdown was retrieved. On failure, ``markdown`` is
    empty and ``reason`` explains why (consumed by the source-health layer).
    """

    url: str
    ok: bool
    markdown: str = ""
    reason: str = ""
    cached_snapshot: bool = False


def _looks_like_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _detect_cached_snapshot(markdown: str) -> bool:
    """Best-effort detection of Jina's cached-snapshot warning.

    Jina prepends a small metadata/warning header to the text response; when it
    serves a cached copy it says so. We scan only the leading window to avoid
    false positives from article bodies that happen to mention caching.
    """
    head = markdown[:600].lower()
    return "cached" in head and "snapshot" in head


def fetch_markdown(
    url: str,
    timeout: int = DEFAULT_FETCH_TIMEOUT,
    retries: int = 2,
) -> KeylessFetchResult:
    """Fetch ``url`` as clean markdown via the keyless reader endpoint.

    Args:
        url: The http(s) URL to fetch.
        timeout: Per-attempt HTTP timeout in seconds.
        retries: Retry budget (kept low; this is a fail-fast floor tier).

    Returns:
        A :class:`KeylessFetchResult`. ``ok`` is False with a populated
        ``reason`` on invalid input, network failure, or empty body.
    """
    if not url or not _looks_like_http_url(url):
        return KeylessFetchResult(url=url or "", ok=False, reason="invalid-url")

    reader_url = f"{JINA_READER_PREFIX}{url}"
    text = http.get_text(
        reader_url,
        timeout=timeout,
        retries=retries,
        accept="text/plain",
    )

    if text is None:
        return KeylessFetchResult(url=url, ok=False, reason="fetch-failed")

    if not text.strip():
        return KeylessFetchResult(url=url, ok=False, reason="empty-body")

    return KeylessFetchResult(
        url=url,
        ok=True,
        markdown=text,
        cached_snapshot=_detect_cached_snapshot(text),
    )
