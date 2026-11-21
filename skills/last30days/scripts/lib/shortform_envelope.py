"""Host-found TikTok and Instagram URLs: the ``--shortform-urls`` lane.

A host with its own web search collects TikTok and Instagram post URLs (and
TikTok creator profile URLs) during its pre-research search and writes them
to a ``.json`` file. The engine reads only each entry's URL. Post URLs go
through the keyless short-form lane (``shortform_oss``): the id-date filter
drops stale posts before any fetch, yt-dlp hydrates the rest logged out, and
the window and the topic bar drop what does not belong. TikTok creator
profiles become leads for creator expansion. A caption, count or date the
host wrote is never used.

Shape (``schema: "last30days-shortform-urls/1"``)::

    {
      "schema": "last30days-shortform-urls/1",
      "topic": "<the run topic>",
      "urls": [
        {"url": "https://www.tiktok.com/@<handle>/video/<id>"},
        {"url": "https://www.instagram.com/reel/<code>/"},
        {"url": "https://www.tiktok.com/@<creator>"}
      ]
    }

The file is read with the same safety contract as ``--reddit-threads``
(``reddit_envelope``): a file path or ``-`` only, a regular ``.json`` file
outside the config dir and credential stores, 1 MiB, strict UTF-8, a
matching ``topic``. The reader is strict at the envelope level (exit-2
contract error) and lenient per entry (a bad entry is dropped with a named
counter). At most ``MAX_URLS`` distinct URLs are used.

The URLs are used only when the keyless lane runs (no ScrapeCreators key, or
ScrapeCreators returned 402); with a working key ScrapeCreators stays the
TikTok and Instagram path.

Security contract: contract errors name the path and the field, never a
field value, and nothing the host wrote reaches an outcome detail.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from . import log
from .reddit_envelope import (
    _CONTROL_RE,
    _REDIRECT_HOST,
    MAX_BYTES,
    MAX_ENTRIES,
    MAX_FIELD_CHARS,
    STDIN,
    EnvelopeContractError,
    _normalize_topic,
    _read_bytes,
    looks_inline_json,
)

SCHEMA = "last30days-shortform-urls/1"
MAX_URLS = 40

ENTRY_FIELDS = frozenset({"url"})
# Counters, in receipt order. All are judged when the file is read; the
# hydration outcome is logged per platform by the keyless lane.
COUNTERS = (
    "non-shortform",
    "redirect",
    "not-a-post",
    "instagram-profile",
    "invalid",
    "duplicate",
    "truncated",
    "extra-fields",
)

_REMEDY = (
    "Rewrite the file to the last30days-shortform-urls/1 shape, or re-run "
    "without --shortform-urls."
)

_TIKTOK_POST = re.compile(r"^/@([\w.\-]{1,64})/video/(\d{6,25})/?$")
_TIKTOK_PROFILE = re.compile(r"^/@([\w.\-]{1,64})/?$")
_INSTAGRAM_POST = re.compile(r"^/(?:reel|reels|p)/([\w\-]{5,40})/?$")
_INSTAGRAM_PROFILE = re.compile(r"^/[\w.]{1,30}/?$")

__all__ = [
    "SCHEMA", "MAX_URLS", "MAX_BYTES", "MAX_ENTRIES", "STDIN",
    "Envelope", "EnvelopeContractError", "looks_inline_json", "read",
]


@dataclass
class Envelope:
    """A validated ``--shortform-urls`` file, ready for the keyless lane."""

    path: str
    sha256: str
    topic: str
    tiktok_posts: list[str]
    instagram_posts: list[str]
    tiktok_creators: list[str]
    counters: dict[str, int]
    total: int
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> int:
        return len(self.tiktok_posts) + len(self.instagram_posts) + len(self.tiktok_creators)

    def posts(self, kind: str) -> list[str]:
        """Canonical post URLs for ``kind`` (``tiktok`` or ``instagram``)."""
        return list(self.tiktok_posts if kind == "tiktok" else self.instagram_posts if kind == "instagram" else [])

    def creators(self, kind: str) -> list[str]:
        """TikTok creator handles; Instagram has no logged-out profile listing."""
        return list(self.tiktok_creators) if kind == "tiktok" else []

    def receipt(self) -> str:
        dropped = ", ".join(
            f"{name} {count}" for name, count in self.counters.items() if count
        ) or "none"
        line = (
            f"host short-form URLs: accepted {self.accepted} of {self.total} "
            f"(TikTok posts {len(self.tiktok_posts)}, TikTok creators "
            f"{len(self.tiktok_creators)}, Instagram posts {len(self.instagram_posts)}; "
            f"dropped: {dropped})"
        )
        if self.notes:
            line += "; notes: " + "; ".join(self.notes)
        return line


def _log(msg: str) -> None:
    log.source_log("TikTok", msg, tty_only=False)


def _classify(text: str) -> tuple[str, str]:
    """``(kind, value)`` for one URL, where kind is a counter name or one of
    ``tiktok-post`` / ``instagram-post`` / ``tiktok-creator``."""
    candidate = text if "://" in text else "https://" + text
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower()
    except ValueError:
        return "invalid", ""
    if not host or parts.scheme.lower() not in ("http", "https"):
        return "invalid", ""
    if _REDIRECT_HOST.search(host):
        return "redirect", ""
    path = parts.path or "/"
    if host in ("tiktok.com", "www.tiktok.com", "m.tiktok.com"):
        m = _TIKTOK_POST.match(path)
        if m:
            return "tiktok-post", f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
        m = _TIKTOK_PROFILE.match(path)
        if m:
            return "tiktok-creator", m.group(1)
        return "not-a-post", ""
    if host in ("instagram.com", "www.instagram.com", "m.instagram.com"):
        m = _INSTAGRAM_POST.match(path)
        if m:
            return "instagram-post", f"https://www.instagram.com/reel/{m.group(1)}/"
        if _INSTAGRAM_PROFILE.match(path):
            return "instagram-profile", ""
        return "not-a-post", ""
    if host.endswith(".tiktok.com") or host.endswith(".instagram.com"):
        # Short links (vm.tiktok.com) need a network round trip to resolve.
        return "not-a-post", ""
    return "non-shortform", ""


def read(path: str | os.PathLike[str], topic: str) -> Envelope:
    """Validate a ``--shortform-urls`` file (or ``-`` for stdin) for this run.

    ``topic`` is the run topic. Raises ``EnvelopeContractError`` (exit 2) for
    every fail-closed case; a bad entry is only counted.
    """
    raw_path = os.fspath(path)

    def fail(what: str) -> EnvelopeContractError:
        where = "on stdin" if raw_path == STDIN else raw_path
        return EnvelopeContractError(f"--shortform-urls file {where} {what}. {_REMEDY}")

    data = _read_bytes(raw_path, fail)
    digest = hashlib.sha256(data).hexdigest()
    try:
        payload = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise fail("is not valid UTF-8") from None
    except (RecursionError, MemoryError):
        raise fail("is too deeply nested or too large to parse") from None
    except ValueError:
        raise fail("is not valid JSON") from None

    if not isinstance(payload, dict):
        raise fail("must be a JSON object")
    if payload.get("schema") != SCHEMA:
        raise fail('has a missing or unsupported "schema" field')
    if not _normalize_topic(topic) or _normalize_topic(payload.get("topic")) != _normalize_topic(topic):
        raise fail('has a "topic" field that does not match this run\'s topic')
    entries = payload.get("urls")
    if not isinstance(entries, list):
        raise fail('has a "urls" field that is not a list')
    if not entries:
        raise fail('has an empty "urls" list (omit --shortform-urls when no URL was found)')
    if len(entries) > MAX_ENTRIES:
        raise fail(f'has more than {MAX_ENTRIES} entries in "urls"')

    counters = {name: 0 for name in COUNTERS}
    buckets: dict[str, list[str]] = {"tiktok-post": [], "instagram-post": [], "tiktok-creator": []}
    seen: set[str] = set()
    kept = 0
    for entry in entries:
        if not isinstance(entry, dict):
            counters["invalid"] += 1
            continue
        if set(entry) - ENTRY_FIELDS:
            counters["extra-fields"] += 1
        raw = entry.get("url")
        if not isinstance(raw, str) or not raw.strip() or len(raw) > MAX_FIELD_CHARS:
            counters["invalid"] += 1
            continue
        kind, value = _classify(_CONTROL_RE.sub("", raw).strip())
        if kind not in buckets:
            counters[kind] += 1
            continue
        key = f"{kind}:{value.lower()}"
        if key in seen:
            counters["duplicate"] += 1
            continue
        if kept >= MAX_URLS:
            counters["truncated"] += 1
            continue
        seen.add(key)
        kept += 1
        buckets[kind].append(value)
    notes: list[str] = []
    if counters["truncated"]:
        notes.append(f"only the first {MAX_URLS} URLs are used")

    envelope = Envelope(
        path=raw_path, sha256=digest, topic=topic,
        tiktok_posts=buckets["tiktok-post"],
        instagram_posts=buckets["instagram-post"],
        tiktok_creators=buckets["tiktok-creator"],
        counters=counters, total=len(entries), notes=notes,
    )
    _log(envelope.receipt())
    return envelope


def from_config(config: Optional[dict[str, Any]]) -> Optional[Envelope]:
    """The run's envelope, carried on the config dict by the pipeline."""
    value = (config or {}).get("_shortform_envelope")
    return value if isinstance(value, Envelope) else None
