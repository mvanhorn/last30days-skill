"""Auth-token header validation and exception secret scrubbing."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_INVALID_HEADER_RE = re.compile(r"(Invalid header value\s+).*", re.IGNORECASE | re.DOTALL)
_AUTH_SCHEME_VALUE_RE = re.compile(r"(?i)\b(Bearer|Basic|Token)\s+[^\s'\"\\]+")
_HEADER_KEY_VALUE_RE = re.compile(r"(?i)(x-api-key\s*[:=]\s*)[^\s'\"\\,;]+")


def normalize_header_token(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Strip surrounding whitespace from a header token and reject control/non-ASCII chars.

    Returns ``(clean_token, error)``:
    - Missing or whitespace-only input -> ``(None, None)``.
    - Valid ASCII header token -> ``(clean_token, None)``.
    - Malformed token (embedded CR/LF, NUL, other CTLs, or non-ASCII) ->
      ``(None, "Invalid token format")`` without echoing the value.
    """
    if raw is None or raw == "":
        return None, None
    if not isinstance(raw, str):
        return None, "Invalid token format"
    cleaned = raw.strip(" \t\r\n")
    if not cleaned:
        return None, None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in cleaned) or not cleaned.isascii():
        return None, "Invalid token format"
    return cleaned, None


def scrub_secrets(text: str, secrets: Iterable[Any] = ()) -> str:
    """Remove credential values and putheader ValueError header dumps from text."""
    if not isinstance(text, str) or not text:
        return ""
    scrubbed = _INVALID_HEADER_RE.sub(r"\1<redacted>", text)
    scrubbed = _AUTH_SCHEME_VALUE_RE.sub(r"\1 <redacted>", scrubbed)
    scrubbed = _HEADER_KEY_VALUE_RE.sub(r"\1<redacted>", scrubbed)

    candidates: set[str] = set()
    for secret in secrets:
        if not isinstance(secret, str) or not secret:
            continue
        variants = {secret, secret.strip()}
        for piece in re.split(r"[\r\n\x00]+", secret):
            if piece:
                variants.add(piece)
                variants.add(piece.strip())
        for variant in list(variants):
            if not variant:
                continue
            escaped = repr(variant)[1:-1]
            if len(variant) >= 4:
                candidates.add(variant)
            if len(escaped) >= 4:
                candidates.add(escaped)

    for needle in sorted(candidates, key=len, reverse=True):
        scrubbed = scrubbed.replace(needle, "<redacted>")
    return scrubbed
