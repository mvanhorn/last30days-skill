"""Per-path health for the Reddit stream, and the served-path detail.

The Reddit stream reaches threads through several paths: Reddit's own site
search (``search``), the keyless web rungs (``brave``, ``ddg``, ``startpage``),
subreddit listings (``listing``), the Arctic Shift archive (``arctic``) and the
ScrapeCreators backfill (``sc``). Each path keeps a state for the run, and the
decisive ones persist across runs, the way the grok build audit persists its
pass and fail verdicts while an inconclusive result stays in memory:

* ``blocked`` (a 403, a challenge page, or a logged-out marker on a search
  page) persists for :data:`SKIP_TTL_SECONDS`. Later runs skip the path
  without asking it until the skip expires, ``doctor`` clears it, or the
  cookie fingerprint it was recorded under changes.
* ``ok`` persists too: it clears any skip and dates the last good answer.
* ``throttled`` (a 429 that survived its one retry) skips the path for the
  rest of this run only and is never written.
* ``error`` and ``empty`` skip nothing.

The cache is ``reddit-health.json`` in the last30days config dir, written by
atomic replace::

    {"schema": 1, "paths": {"brave": {"state": "blocked", "at": "<iso>",
                                      "reason": "HTTP 403", "cookie_fp": null}}}

``cookie_fp`` is the first 16 hex characters of a session cookie's sha256,
never the cookie. No keyless path has a cookie, so nothing writes it yet.

:func:`served_detail` turns one lane call's path states and the items it
delivered into the ``source_status`` detail line, in a fixed vocabulary:
path tokens ``session``, ``host``, ``search``, ``web``, ``listing``,
``arctic``, ``sc``; failure tokens ``session-expired``, ``blocked``,
``throttled``, ``skipped-cached`` and ``down``. Example:
``web+arctic served 14; search blocked``. Never raises.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import env, log, private_files

SCHEMA = 1
CACHE_FILENAME = "reddit-health.json"
SKIP_TTL_SECONDS = 6 * 3600

WEB_RUNGS = ("brave", "ddg", "startpage")
PATHS = ("search", *WEB_RUNGS, "arctic")

OK = "ok"
EMPTY = "empty"
ERROR = "error"
THROTTLED = "throttled"
BLOCKED = "blocked"
SKIPPED_CACHED = "skipped-cached"
SESSION_EXPIRED = "session-expired"
DOWN = "down"
# States that persist (pass and fail); the rest stay in this run's memory.
_PERSISTED = frozenset({OK, BLOCKED})
# States that take a path out of the rest of the run.
_RUN_DOWN = frozenset({BLOCKED, THROTTLED})
# Failure tokens the detail line uses, per state.
_FAILURE_TOKEN = {
    BLOCKED: BLOCKED,
    THROTTLED: THROTTLED,
    SKIPPED_CACHED: SKIPPED_CACHED,
    SESSION_EXPIRED: SESSION_EXPIRED,
    ERROR: DOWN,
    DOWN: DOWN,
}
SERVED_ORDER = ("session", "host", "search", "web", "listing", "arctic", "sc")
_FAILURE_ORDER = ("session", "search", *WEB_RUNGS, "web", "listing", "arctic", "sc")
_DETAIL_LIMIT = 140

_LOCK = threading.Lock()
_RUN: dict[str, str] = {}
_seeded = False


def _log(msg: str) -> None:
    log.source_log("RedditHealth", msg, tty_only=False)


def _now(now: Optional[datetime]) -> datetime:
    return now or datetime.now(timezone.utc)


def cache_path() -> Optional[Path]:
    return env.CONFIG_DIR / CACHE_FILENAME if env.CONFIG_DIR is not None else None


def _read() -> dict[str, dict[str, Any]]:
    path = cache_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    paths = data.get("paths")
    if not isinstance(paths, dict):
        return {}
    return {k: v for k, v in paths.items() if isinstance(v, dict)}


@contextlib.contextmanager
def _file_lock(path: Optional[Path]):
    """Serialize read-modify-write on ``reddit-health.json`` across processes.

    Yields ``True`` when the cross-process lock is held, or ``False`` when no
    cache path is configured or locking failed (callers must skip disk writes
    when ``False`` so concurrent writers cannot clobber each other).
    """
    if path is None:
        yield False
        return
    # Persistent sidecar lock file keeps writers on the same inode across atomic replaces.
    lock_path = path.with_suffix(".lock")
    fd: Optional[int] = None
    locked = False
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = (
            os.O_CREAT
            | os.O_RDWR
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        fd = os.open(lock_path, flags, 0o600)
        if os.name == "posix":
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise OSError(f"Expected a regular lock file: {lock_path}")
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            locked = True
        elif os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            locked = True
    except OSError as exc:
        _log(f"could not lock Reddit path health: {exc}")
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
    try:
        yield locked
    finally:
        if fd is not None:
            try:
                if locked:
                    if os.name == "posix":
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
                    elif os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass


def _write(paths: Mapping[str, Any]) -> None:
    path = cache_path()
    if path is None:
        return
    payload = json.dumps({"schema": SCHEMA, "paths": dict(paths)}, indent=2, sort_keys=True)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_files.replace_private_text(path, payload)
    except OSError as exc:
        _log(f"could not save Reddit path health: {exc}")


def _parse_at(value: Any) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def entries() -> dict[str, dict[str, Any]]:
    """The persisted entry for each path that has one (doctor's view)."""
    return _read()


def skip_until(
    path: str, cookie_fp: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[datetime]:
    """When a persisted skip on ``path`` lifts, or None when it is not skipped.

    A skip recorded under another cookie fingerprint does not apply.
    """
    entry = _read().get(path)
    if not entry or entry.get("state") != BLOCKED:
        return None
    if (entry.get("cookie_fp") or None) != (cookie_fp or None):
        return None
    at = _parse_at(entry.get("at"))
    if at is None:
        return None
    until = at + timedelta(seconds=SKIP_TTL_SECONDS)
    return until if until > _now(now) else None


def run_state(path: str) -> Optional[str]:
    """This run's recorded state for ``path``, if any."""
    with _LOCK:
        return _RUN.get(path)


def skip_reason(
    path: str, cookie_fp: Optional[str] = None, now: Optional[datetime] = None,
) -> Optional[str]:
    """Why ``path`` should not be asked this run: ``blocked`` or ``throttled``
    (it failed earlier this run), ``skipped-cached`` (a persisted skip), or
    None when it may run."""
    state = run_state(path)
    if state in _RUN_DOWN or state == SKIPPED_CACHED:
        return state
    if skip_until(path, cookie_fp, now) is not None:
        with _LOCK:
            _RUN.setdefault(path, SKIPPED_CACHED)
        return SKIPPED_CACHED
    return None


def record(
    path: str,
    state: str,
    reason: str = "",
    *,
    cookie_fp: Optional[str] = None,
    now: Optional[datetime] = None,
) -> None:
    """Note ``path``'s answer this run; ``ok`` and ``blocked`` also persist."""
    if not path or not state:
        return
    with _LOCK:
        current = _RUN.get(path)
        # A path already down this run stays down for it; never let a stale
        # in-flight OK clobber a persisted BLOCKED cooldown on disk.
        if current in _RUN_DOWN and state == OK:
            return
        if current == BLOCKED and state != BLOCKED:
            return
        if current not in _RUN_DOWN or state == BLOCKED:
            _RUN[path] = state
        if state not in _PERSISTED:
            return
        with _file_lock(cache_path()) as locked:
            if not locked:
                return
            paths = _read()
            previous = paths.get(path) or {}
            if state == OK and previous.get("state") == OK:
                # Re-dating a good answer on every run is churn; one write a TTL is plenty.
                at = _parse_at(previous.get("at"))
                if at is not None and _now(now) - at < timedelta(seconds=SKIP_TTL_SECONDS):
                    return
            paths[path] = {
                "state": state,
                "at": _now(now).isoformat(),
                "reason": str(reason or "")[:120],
                "cookie_fp": cookie_fp or None,
            }
            _write(paths)
    if state == BLOCKED:
        _log(f"{path} refused this network ({reason or 'blocked'}); skipping it for "
             f"{SKIP_TTL_SECONDS // 3600} h")


def cached_web_skips(now: Optional[datetime] = None) -> dict[str, str]:
    """The web rungs a persisted skip takes out of this run."""
    return {rung: BLOCKED for rung in WEB_RUNGS if skip_until(rung, now=now) is not None}


def seed_web_rungs(now: Optional[datetime] = None) -> dict[str, str]:
    """Once per run, start the keyless web ladder with the rungs a persisted
    skip takes out. Returns the rungs seeded this call."""
    global _seeded
    with _LOCK:
        if _seeded:
            return {}
        _seeded = True
    skips = cached_web_skips(now)
    if skips:
        from . import web_search_keyless

        web_search_keyless.seed_rung_health(skips)
        with _LOCK:
            for rung in skips:
                _RUN.setdefault(rung, SKIPPED_CACHED)
        _log(f"skipping {', '.join(skips)} this run (blocked within the last "
             f"{SKIP_TTL_SECONDS // 3600} h)")
    return skips


def clear() -> list[str]:
    """Drop every persisted skip and this run's states (doctor). Returns the
    paths whose skip was still in force."""
    global _seeded
    cleared: list[str] = []
    with _LOCK:
        with _file_lock(cache_path()) as locked:
            if locked:
                paths = _read()
                cleared = [p for p in paths if paths[p].get("state") == BLOCKED]
                if paths:
                    _write({})
        _RUN.clear()
        _seeded = False
    return cleared


def reset_run_state() -> None:
    """Forget this run's path states (call between runs in one process)."""
    global _seeded
    with _LOCK:
        _RUN.clear()
        _seeded = False


# --- served-path detail -----------------------------------------------------

def served_paths(items: Iterable[Mapping[str, Any]]) -> set[str]:
    """The paths that delivered ``items``: their discovery provenance, plus
    ``arctic`` for a thread whose score or comments came from the archive."""
    served: set[str] = set()
    for item in items or []:
        if not isinstance(item, Mapping):
            continue
        served.update(p for p in item.get("provenance") or [] if p in SERVED_ORDER)
        if str(item.get("engagement_source") or "").startswith("arctic") or "arctic" in str(
            item.get("comments_source") or ""
        ):
            served.add("arctic")
    return served


def failures(paths: Mapping[str, str]) -> list[str]:
    """``"<path> <token>"`` for every failed path, in a fixed order."""
    out = []
    for path in _FAILURE_ORDER:
        token = _FAILURE_TOKEN.get(paths.get(path) or "")
        if token:
            out.append(f"{path} {token}")
    return out


def every_path_failed(paths: Mapping[str, str]) -> bool:
    """True when every path this lane asked failed (none answered, even empty)."""
    asked = {p: s for p, s in paths.items() if p != "web" and s}
    if not asked:
        return False
    return all(s in _FAILURE_TOKEN for s in asked.values())


def failure_outcome(paths: Mapping[str, str]) -> tuple[str, str]:
    """The source outcome when every path failed: ``(state, detail)``.

    A transport failure, never no-results: ``rate-limited`` when every
    failed path was throttled (the run can only wait), else ``unreachable``.
    """
    from . import health

    states = {s for p, s in paths.items() if p != "web" and s in _FAILURE_TOKEN}
    detail = served_detail([], paths)
    if states == {THROTTLED}:
        return health.RATE_LIMITED, f"{detail}; rate-limited (HTTP 429)"
    return health.UNREACHABLE, detail


def served_detail(
    items: Iterable[Mapping[str, Any]],
    paths: Optional[Mapping[str, str]] = None,
    *,
    extra: Iterable[str] = (),
    count: Optional[int] = None,
) -> str:
    """The detail line for one Reddit lane call (see the module docstring).

    ``extra`` names paths that served beyond the items' own provenance (such as
    ``sc`` after a backfill); ``count`` overrides the item count.
    """
    items = [i for i in items or [] if isinstance(i, Mapping)]
    served = served_paths(items) | set(extra)
    total = len(items) if count is None else count
    parts = []
    if total:
        named = "+".join(p for p in SERVED_ORDER if p in served) or "reddit"
        parts.append(f"{named} served {total}")
    else:
        parts.append("no path served")
    detail = "; ".join([*parts, *failures(paths or {})])
    if len(detail) > _DETAIL_LIMIT:
        detail = detail[:_DETAIL_LIMIT - 3].rstrip("; ") + "..."
    return detail


def parse_detail(detail: Optional[str]) -> dict[str, str]:
    """Each path's last-run outcome read back from a detail line: ``served``
    or a failure token. Doctor reads ``last-report.json`` through this."""
    out: dict[str, str] = {}
    for part in str(detail or "").split(";"):
        words = part.strip().split()
        if len(words) >= 3 and words[1] == "served":
            for path in words[0].split("+"):
                if path in SERVED_ORDER:
                    out.setdefault(path, "served")
        elif len(words) == 2 and words[1] in _FAILURE_TOKEN.values():
            out.setdefault(words[0], words[1])
    return out


# --- doctor's live check ----------------------------------------------------
#
# The keyless floor's two halves, probed the way the lane asks them: Brave's
# HTML results for a month-filtered site:reddit.com query (discovery), and an
# Arctic Shift /posts/ids lookup (hydration). Neither reads a cookie.

PROBE_THREAD_ID = "1gzqsxk"


def brave_probe_url() -> str:
    from urllib.parse import urlencode

    from . import web_search_keyless

    query = urlencode({"q": "site:reddit.com reddit", "source": "web", "tf": "pm"})
    return f"{web_search_keyless._BRAVE_HTML_URL}?{query}"


def arctic_probe_url() -> str:
    from . import reddit_arctic

    return f"{reddit_arctic.API}?ids={PROBE_THREAD_ID}"


def arctic_listing_probe_url() -> str:
    """Probe the same subreddit listing endpoint used by the fallback lane."""
    from . import reddit_arctic

    return f"{reddit_arctic.SEARCH_API}?subreddit=python&limit=1&sort=desc"


def brave_probe_body(text: str) -> Optional[str]:
    """None when the page is Brave results the lane can parse, else why not."""
    from . import web_search_keyless

    if web_search_keyless.parse_brave(text or "", 3):
        return None
    return "no Brave results on the page"


def arctic_probe_body(text: str) -> Optional[str]:
    """None when the body is an Arctic Shift /posts/ids answer, else why not."""
    try:
        data = json.loads(text or "")
    except ValueError:
        return "not an Arctic Shift JSON answer"
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return None
    return "not an Arctic Shift /posts/ids answer"
