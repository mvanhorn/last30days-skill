"""Shared logging utilities for last30days skill."""

import os
import sys

DEBUG = os.environ.get("LAST30DAYS_DEBUG", "").lower() in ("1", "true", "yes")


def debug(msg: str) -> None:
    """Log debug message to stderr (only when LAST30DAYS_DEBUG is set)."""
    if DEBUG:
        sys.stderr.write(f"[DEBUG] {msg}\n")
        sys.stderr.flush()


def source_log(prefix: str, msg: str, *, tty_only: bool = True) -> None:
    """Log a source module message to stderr.

    Args:
        prefix: Source label (e.g. "Reddit", "Bird").
        msg: Message text.
        tty_only: If True, only log when stderr is a TTY (avoids cluttering
                  non-interactive output like Claude Code).

    CONVENTION: source modules under `lib/` must call this with
    `tty_only=False`. The default exists to keep ad-hoc callers quiet, but
    a source module's logs are observability — silently dropping them under
    Claude Code, Codex, or CI hides both failures and success signals from
    the user and the synthesis LLM. The convention is enforced by
    `tests/test_source_log_visibility.py`.
    """
    if tty_only and not sys.stderr.isatty():
        return
    sys.stderr.write(f"[{prefix}] {msg}\n")
    sys.stderr.flush()
