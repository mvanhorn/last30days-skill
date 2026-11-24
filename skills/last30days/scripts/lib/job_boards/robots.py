"""A site's robots.txt: whether a path is disallowed, and the sitemaps it lists.

The jobs lane reads a company's job listing whatever the site's robots.txt
says, by the owner's decision (SECURITY.md). ``LAST30DAYS_JOBS_RESPECT_ROBOTS=1``
turns that off: a listing the ``*`` group disallows is then not read, and the
lane falls to the site's job sitemap. The sitemaps a robots.txt lists are
read either way: they are what the site publishes for crawlers.

Only the ``User-agent: *`` group is read, with the ``*`` and ``$`` wildcards
and the longest-match rule (an ``Allow`` wins a tie).
"""

from __future__ import annotations

import os
import threading
from typing import Any
from urllib.parse import urlparse

from .. import http, public_host

RESPECT_ROBOTS_VAR = "LAST30DAYS_JOBS_RESPECT_ROBOTS"
TIMEOUT_SECONDS = 5
MAX_ROBOTS_CHARS = 512 * 1024
MAX_LINES = 10_000
MAX_LINE_LENGTH = 4096
MAX_RULES = 2_000
MAX_PATHS = 2_000
MAX_SITEMAPS = 200

_cache: dict[str, "Rules"] = {}
_lock = threading.Lock()


def respect_robots(environ: Any = None) -> bool:
    """True when ``LAST30DAYS_JOBS_RESPECT_ROBOTS`` asks the lane to skip a
    job listing its site's robots.txt disallows."""
    source = os.environ if environ is None else environ
    return str(source.get(RESPECT_ROBOTS_VAR) or "").strip().lower() in {"1", "true", "yes", "on"}


class Rules:
    """The ``*`` group's rules and the file's sitemaps. ``paths`` is every
    ``Allow`` and ``Disallow`` path in the file, whatever group it is in."""

    def __init__(self, text: str = "") -> None:
        self.rules: list[tuple[bool, str]] = []
        self.sitemaps: list[str] = []
        self.paths: list[str] = []
        agents: list[str] = []
        in_rules = False
        applies = False
        raw_text = text or ""
        if len(raw_text) > MAX_ROBOTS_CHARS:
            bounded = raw_text[:MAX_ROBOTS_CHARS]
            if (
                bounded
                and bounded[-1] not in ("\n", "\r")
                and raw_text[MAX_ROBOTS_CHARS] not in ("\n", "\r")
            ):
                # Discard a trailing line cut mid-rule by MAX_ROBOTS_CHARS so a
                # shortened Allow/Disallow prefix never takes effect.
                last_nl = max(bounded.rfind("\n"), bounded.rfind("\r"))
                bounded = bounded[:last_nl] if last_nl != -1 else ""
        else:
            bounded = raw_text
        for line_count, raw in enumerate(bounded.splitlines()):
            if line_count >= MAX_LINES:
                break
            line = raw.split("#", 1)[0].strip()
            if len(line) > MAX_LINE_LENGTH or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "sitemap":
                if value and len(self.sitemaps) < MAX_SITEMAPS:
                    self.sitemaps.append(value)
                continue
            if field == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(value.lower())
                applies = "*" in agents
                continue
            if field in ("allow", "disallow"):
                in_rules = True
                if value and len(self.paths) < MAX_PATHS:
                    self.paths.append(value)
                if applies and value and len(self.rules) < MAX_RULES:
                    self.rules.append((field == "allow", value))

    def allows(self, path: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if _matches(pattern, path):
                rank = (len(pattern), allow)
                if best is None or rank > best:
                    best = rank
        return True if best is None else best[1]


def _matches(pattern: str, path: str) -> bool:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    parts = body.split("*")
    if len(parts) == 1:
        return path == parts[0] if anchored else path.startswith(parts[0])
    first = parts[0]
    if not path.startswith(first):
        return False
    pos = len(first)
    middle = parts[1:-1] if anchored else parts[1:]
    for part in middle:
        if not part:
            continue
        idx = path.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    if not anchored:
        return True
    last = parts[-1]
    if not last:
        return True
    return path.endswith(last) and len(path) - len(last) >= pos


def rules_for(host: str, *, deadline_monotonic: float | None = None) -> Rules:
    """The robots.txt rules of ``host``, fetched once per run. A file that is
    missing or does not answer has no rules."""
    with _lock:
        if host in _cache:
            return _cache[host]
    robots_url = f"https://{host}/robots.txt"
    if not public_host.is_public_address_syntax(robots_url):
        return Rules("")
    bounds: dict[str, Any] = {}
    if deadline_monotonic is not None:
        bounds["deadline_monotonic"] = deadline_monotonic
    try:
        with http.capture_failures():
            text = http.get_text(
                robots_url, accept="text/plain", timeout=TIMEOUT_SECONDS,
                retries=1, retry_dns=False, public_only=True, **bounds,
            )
    except UnicodeDecodeError:
        text = None
    found = Rules(text or "")
    with _lock:
        _cache[host] = found
    return found


def disallowed(url: str, *, deadline_monotonic: float | None = None) -> bool:
    """Whether ``LAST30DAYS_JOBS_RESPECT_ROBOTS`` is on and the site's robots.txt
    disallows ``url``. Always False with the setting off: no request is made."""
    if not respect_robots():
        return False
    parsed = urlparse(url)
    if not parsed.hostname:
        return False
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return not rules_for(parsed.hostname, deadline_monotonic=deadline_monotonic).allows(path)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
