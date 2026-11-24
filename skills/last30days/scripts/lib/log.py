"""Shared logging utilities for last30days skill."""

import os
import sys


def _sanitize_for_stream(text: str, stream) -> str:
    """Pre-sanitize text for streams with an explicit narrow encoding.

    Buffered ``io.TextIOWrapper`` streams (e.g. non-TTY pipes on Windows cp1252
    or POSIX ASCII locales) may defer encoding until ``flush()``, so sanitizing
    before ``write()`` prevents poisoning the stream's pending text buffer.
    """
    encoding = getattr(stream, "encoding", None)
    if not encoding:
        return text
    try:
        text.encode(encoding)
        return text
    except UnicodeEncodeError:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    except LookupError:
        return text.encode("ascii", errors="replace").decode("ascii")


def safe_stderr_write(text: str, stream=None, *, flush: bool = True) -> None:
    """Write text to stderr without crashing on narrow encodings or broken pipes."""
    target = sys.stderr if stream is None else stream
    if target is None:
        return
    sanitized = _sanitize_for_stream(text, target)
    try:
        target.write(sanitized)
    except UnicodeEncodeError:
        try:
            target.write(text.encode("ascii", errors="replace").decode("ascii"))
        except (UnicodeEncodeError, BrokenPipeError, OSError):
            return
    except (BrokenPipeError, OSError):
        return
    if flush:
        try:
            target.flush()
        except (UnicodeEncodeError, BrokenPipeError, OSError):
            pass


def is_debug() -> bool:
    val = os.environ.get("LAST30DAYS_DEBUG", "")
    return val.lower() in ("1", "true", "yes", "on")


def debug(msg: str) -> None:
    """Log debug message to stderr (only when LAST30DAYS_DEBUG is set)."""
    if is_debug():
        safe_stderr_write(f"[DEBUG] {msg}\n")


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
    if tty_only:
        try:
            if sys.stderr is None or not sys.stderr.isatty():
                return
        except OSError:
            return
    safe_stderr_write(f"[{prefix}] {msg}\n")
