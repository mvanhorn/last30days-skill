"""Provider-reported charges for a scoped watchlist subprocess."""

from __future__ import annotations

import math
import os
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

from . import log

JOURNAL_ENV = "LAST30DAYS_USAGE_JOURNAL"
UNKNOWN_COST_MARKER = "[last30days:usage-cost-unknown]"
_JOURNAL_TIMEOUT_SECONDS = 30.0
_BUSY_TIMEOUT_MS = 30_000
_WRITE_LOCK = threading.Lock()
_STATE_LOCK = threading.Lock()
_BEGIN_FAILED_JOURNALS: set[str] = set()
_ACTIVE_DEADLINE: ContextVar[float | None] = ContextVar("last30days_usage_deadline", default=None)

_PAID_HOSTS = {
    "openrouter.ai": "openrouter",
    "api.perplexity.ai": "perplexity",
    "api.openai.com": "openai",
    "api.x.ai": "xai",
    "generativelanguage.googleapis.com": "gemini",
    "api.scrapecreators.com": "scrapecreators",
    "api.search.brave.com": "brave",
    "api.tavily.com": "tavily",
    "api.exa.ai": "exa",
    "google.serper.dev": "serper",
    "api.parallel.ai": "parallel",
    "api.x.com": "x",
    "api.twitter.com": "x",
    "xquik.com": "xquik",
    "api.groq.com": "groq",
    "code.deepline.com": "deepline",
}


def _log_journal_error(operation: str, exc: sqlite3.Error) -> None:
    log.source_log(
        "Usage",
        f"journal {operation} failed: {type(exc).__name__}: {exc}",
        tty_only=False,
    )


def _journal_key(path: Path | str) -> str:
    return os.path.abspath(os.fspath(path))


def _unknown_sentinel_path(path: Path | str) -> Path:
    return Path(f"{os.fspath(path)}.unknown")


@contextmanager
def with_deadline(deadline_monotonic: float | None):
    """Bind a wall-clock deadline to usage journal waits in the current scope."""
    token = _ACTIVE_DEADLINE.set(deadline_monotonic)
    try:
        yield
    finally:
        _ACTIVE_DEADLINE.reset(token)


def _effective_deadline(deadline_monotonic: float | None) -> float | None:
    return deadline_monotonic if deadline_monotonic is not None else _ACTIVE_DEADLINE.get()


def _remaining_budget(deadline_monotonic: float | None) -> float:
    if deadline_monotonic is None:
        return _JOURNAL_TIMEOUT_SECONDS
    return min(_JOURNAL_TIMEOUT_SECONDS, max(0.0, deadline_monotonic - time.monotonic()))


def _mark_begin_failed(path: Path | str) -> None:
    """Record a fail-closed unknown-cost marker in-process, on disk, and on stderr."""
    key = _journal_key(path)
    sentinel = _unknown_sentinel_path(path)
    flags = os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    with _STATE_LOCK:
        _BEGIN_FAILED_JOURNALS.add(key)
    try:
        fd = os.open(sentinel, flags, 0o600)
        os.close(fd)
    except OSError:
        pass
    try:
        sys.stderr.write(f"{UNKNOWN_COST_MARKER}\n")
        sys.stderr.flush()
    except OSError:
        pass


def _has_begin_failed(path: Path | str) -> bool:
    key = _journal_key(path)
    with _STATE_LOCK:
        if key in _BEGIN_FAILED_JOURNALS:
            return True
    try:
        return os.path.lexists(_unknown_sentinel_path(path))
    except OSError:
        return False


@contextmanager
def _connection(path, *, write: bool = False, deadline_monotonic: float | None = None):
    deadline = _effective_deadline(deadline_monotonic)
    if deadline is not None and deadline - time.monotonic() <= 0:
        raise sqlite3.OperationalError("usage journal deadline exceeded")
    acquired = False
    if write:
        lock_timeout = _remaining_budget(deadline)
        acquired = _WRITE_LOCK.acquire(timeout=lock_timeout)
        if not acquired:
            raise sqlite3.OperationalError("usage journal lock wait timed out")
    try:
        sql_timeout = _remaining_budget(deadline)
        if deadline is not None and sql_timeout <= 0:
            raise sqlite3.OperationalError("usage journal deadline exceeded")
        busy_ms = max(0, int(sql_timeout * 1000))
        conn = sqlite3.connect(
            path,
            timeout=sql_timeout,
            isolation_level=None,
        )
        try:
            conn.execute(f"PRAGMA busy_timeout={busy_ms}")
            if write:
                conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                if write and conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
            else:
                if write and conn.in_transaction:
                    conn.execute("COMMIT")
        finally:
            conn.close()
    finally:
        if acquired:
            _WRITE_LOCK.release()


def create_journal(path: Path) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with _WRITE_LOCK:
        with _STATE_LOCK:
            _BEGIN_FAILED_JOURNALS.discard(_journal_key(path))
        try:
            _unknown_sentinel_path(path).unlink(missing_ok=True)
        except OSError:
            pass
        conn = sqlite3.connect(
            path,
            timeout=_JOURNAL_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "CREATE TABLE attempts (id TEXT PRIMARY KEY, provider TEXT NOT NULL, "
                    "cost REAL, unknown INTEGER NOT NULL DEFAULT 1, "
                    "prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0)"
                )
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                raise
        finally:
            conn.close()


def begin(
    provider: str,
    *,
    deadline_monotonic: float | None = None,
) -> tuple[str, str, str] | None:
    path = os.environ.get(JOURNAL_ENV)
    if not path:
        return None
    attempt_id = uuid.uuid4().hex
    try:
        with _connection(path, write=True, deadline_monotonic=deadline_monotonic) as conn:
            conn.execute("INSERT INTO attempts (id, provider) VALUES (?, ?)", (attempt_id, provider))
    except sqlite3.Error as exc:
        _log_journal_error("begin", exc)
        _mark_begin_failed(path)
        return None
    return path, attempt_id, provider


def begin_http(
    url: str,
    method: str,
    *,
    deadline_monotonic: float | None = None,
) -> tuple[str, str, str] | None:
    if not os.environ.get(JOURNAL_ENV):
        return None
    parts = urlsplit(url)
    provider = _PAID_HOSTS.get(parts.hostname)
    if provider == "perplexity" and method == "GET":
        return None  # Retrieving an existing background operation does not start another charge.
    if provider is None:
        for name in ("OPENAI", "XAI", "OPENROUTER", "SCRAPECREATORS"):
            override = os.environ.get(f"{name}_BASE_URL")
            if override and urlsplit(override).netloc == parts.netloc:
                provider = name.lower()
                break
    return begin(provider, deadline_monotonic=deadline_monotonic) if provider else None


def cancel(
    attempt: tuple[str, str, str] | None,
    *,
    deadline_monotonic: float | None = None,
) -> None:
    """Remove an attempt when the transport provably never started."""
    if attempt is not None:
        path, attempt_id, _ = attempt
        try:
            with _connection(path, write=True, deadline_monotonic=deadline_monotonic) as conn:
                conn.execute("DELETE FROM attempts WHERE id = ?", (attempt_id,))
        except sqlite3.Error as exc:
            _log_journal_error("cancel", exc)


def finish(
    attempt: tuple[str, str, str] | None,
    payload: object,
    *,
    deadline_monotonic: float | None = None,
) -> None:
    if attempt is None or not isinstance(payload, dict):
        return
    path, attempt_id, provider = attempt
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    cost = usage.get("cost")
    if provider == "perplexity":
        if not isinstance(cost, dict) or cost.get("currency") != "USD":
            return
        cost = cost.get("total_cost")
    elif provider != "openrouter":
        return
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return
    # A background response may expose a running subtotal rather than its final bill.
    unknown = int(payload.get("status") in {"queued", "in_progress"})
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0))
    prompt = prompt if type(prompt) is int and prompt >= 0 else 0
    completion = completion if type(completion) is int and completion >= 0 else 0
    try:
        with _connection(path, write=True, deadline_monotonic=deadline_monotonic) as conn:
            conn.execute(
                "UPDATE attempts SET cost = ?, unknown = ?, prompt_tokens = ?, completion_tokens = ? WHERE id = ?",
                (cost, unknown, prompt, completion, attempt_id),
            )
    except sqlite3.Error as exc:
        _log_journal_error("finish", exc)


def read_journal(path: Path) -> dict:
    unknown_from_begin = _has_begin_failed(path)
    try:
        with _connection(path, write=False) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(unknown), 0), "
                "COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) FROM attempts"
            ).fetchone()
    except sqlite3.Error:
        return {"token_cost": 0.0, "cost_unknown": 1, "prompt_tokens": 0, "completion_tokens": 0}
    return {
        "token_cost": row[0],
        "cost_unknown": int(row[1] > 0 or unknown_from_begin),
        "prompt_tokens": row[2],
        "completion_tokens": row[3],
    }
