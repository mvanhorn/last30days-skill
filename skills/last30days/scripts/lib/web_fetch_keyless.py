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
import re
from typing import Optional
from urllib.parse import urlsplit

from . import http, public_host

JINA_READER_PREFIX = "https://r.jina.ai/"
_RESERVED_HOST_SUFFIXES = frozenset({"localhost", "local", "internal", "localdomain", "arpa"})
_VALID_DOMAIN_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+"
)
_VALID_TLD_RE = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{2,59}")

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
    if not isinstance(url, str) or not url or "\\" in url:
        return False
    if not public_host.is_public_address_syntax(url):
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    # Reject percent-escapes, non-ASCII, and backslashes in authority to avoid
    # RFC-3986 vs WHATWG URL parser differentials at Jina Reader.
    if not parsed.netloc.isascii() or "%" in parsed.netloc or "\\" in parsed.netloc:
        return False
    if port is not None and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or not host.isascii() or not _VALID_DOMAIN_RE.fullmatch(host):
        return False
    if public_host._parse_ip_literal(host) is not None:
        return False
    labels = host.split(".")
    if host == "localhost" or labels[-1] in _RESERVED_HOST_SUFFIXES:
        return False
    if not _VALID_TLD_RE.fullmatch(labels[-1]):
        return False
    return True


def _reader_redirect_guard(target: str) -> bool:
    if not isinstance(target, str) or not target or "\\" in target:
        return False
    if not public_host.is_public_address_syntax(target):
        return False
    try:
        parsed = urlsplit(target)
        port = parsed.port
    except ValueError:
        return False
    if not parsed.netloc.isascii() or "%" in parsed.netloc or "\\" in parsed.netloc:
        return False
    if port is not None and port != 443:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    return parsed.scheme.lower() == "https" and (
        host == "r.jina.ai" or host.endswith(".r.jina.ai")
    )


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
    try:
        text = http.get_text(
            reader_url,
            timeout=timeout,
            retries=retries,
            accept="text/plain",
            public_only=True,
            redirect_guard=_reader_redirect_guard,
        )
    except UnicodeDecodeError:
        return KeylessFetchResult(url=url, ok=False, reason="decode-error")

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
