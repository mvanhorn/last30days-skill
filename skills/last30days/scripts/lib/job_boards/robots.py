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
import re
import threading
from typing import Any
from urllib.parse import urlparse

from .. import http

RESPECT_ROBOTS_VAR = "LAST30DAYS_JOBS_RESPECT_ROBOTS"
TIMEOUT_SECONDS = 5

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
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.lower()
            if field == "sitemap":
                if value:
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
                if value:
                    self.paths.append(value)
                if applies and value:
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
    regex = "".join(".*" if char == "*" else re.escape(char) for char in body)
    return re.match(regex + ("$" if anchored else ""), path) is not None


def rules_for(host: str, *, deadline_monotonic: float | None = None) -> Rules:
    """The robots.txt rules of ``host``, fetched once per run. A file that is
    missing or does not answer has no rules."""
    with _lock:
        if host in _cache:
            return _cache[host]
    bounds: dict[str, Any] = {}
    if deadline_monotonic is not None:
        bounds["deadline_monotonic"] = deadline_monotonic
    try:
        with http.capture_failures():
            text = http.get_text(
                f"https://{host}/robots.txt", accept="text/plain", timeout=TIMEOUT_SECONDS,
                retries=1, retry_dns=False, **bounds,
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
