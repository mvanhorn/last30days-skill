"""Subprocess helpers: safe timeout + process-group cleanup.

Used by bird_x.py (Node.js Bird search) and youtube_yt.py (yt-dlp search
and transcript download). Both need the same os.setsid/killpg cleanup
dance on timeout to avoid orphaning child processes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Sequence


class SubprocTimeout(Exception):
    """Raised when a subprocess exceeds its timeout and is killed."""


@dataclass
class SubprocResult:
    """Result of a subprocess run that captured stdout and stderr."""

    returncode: int
    stdout: str
    stderr: str


def _taskkill_tree(pid: int) -> bool:
    """Kill a Windows process tree rooted at *pid* via ``taskkill``.

    Uses ``taskkill /F /T /PID`` to forcibly terminate the process and all
    of its children.  This is a no-op on non-Windows platforms (callers
    should guard on ``sys.platform == "win32"`` before calling).

    Best-effort and never raises: ``check=False`` suppresses
    ``CalledProcessError`` so a concurrent exit or denied termination cannot
    bypass the ``SubprocTimeout`` contract. Returns ``True`` only when
    ``taskkill`` exited 0; a nonzero exit (failure) returns ``False`` so the
    caller can fall back to ``proc.kill()`` / ``proc.wait()`` cleanup and
    still reap the direct child instead of leaving the whole tree alive
    (#823 Greptile re-review).
    """
    result = subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _safe_kill(proc: subprocess.Popen) -> None:
    """Best-effort ``proc.kill()`` that never raises.

    ``proc.kill()`` raises ``ProcessLookupError`` when the child has already
    exited (it exited concurrently with the timeout, or our `taskkill`/killpg
    already reaped it).  Without this guard, ``run_with_timeout``'s cleanup
    handlers would re-raise that exception *after* catching it, escaping the
    documented ``SubprocTimeout`` contract and surfacing a generic subprocess
    error to adapters instead of a timeout (Greptile P1 re-review, #927).
    """
    try:
        proc.kill()
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        pass


def run_with_timeout(
    cmd: Sequence[str],
    *,
    timeout: int,
    env: Optional[dict] = None,
    on_pid: Optional[callable] = None,
) -> SubprocResult:
    """Run a subprocess with process-group cleanup on timeout.

    Spawns ``cmd`` inside its own process group via ``os.setsid`` where
    available. If ``communicate(timeout=...)`` raises ``TimeoutExpired``,
    signals ``SIGTERM`` to the entire group, falls back to ``proc.kill()``
    if the signal fails, then waits up to 5 seconds for cleanup, and
    raises ``SubprocTimeout``.

    Args:
        cmd: Command and arguments to spawn.
        timeout: Timeout in seconds passed to ``communicate()``.
        env: Optional environment dict. If None, inherits parent env.
        on_pid: Optional callable invoked with the child PID right after
            spawn. Used by bird_x.py to register child PIDs for cleanup
            tracking. Exceptions raised by the callback are suppressed.

    Returns:
        SubprocResult with returncode, stdout, and stderr as strings.

    Raises:
        SubprocTimeout: If the process exceeded ``timeout``.
        FileNotFoundError: If the executable is not found.
        OSError: For other spawn failures.
    """
    preexec = os.setsid if hasattr(os, "setsid") else None

    proc = subprocess.Popen(
        list(cmd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        preexec_fn=preexec,
        env=env,
    )

    if on_pid is not None:
        try:
            on_pid(proc.pid)
        except Exception:
            pass

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if sys.platform == "win32":
                # Windows: version-manager shims (proto, nodenv, fnm) create a
                # deeper spawn tree than a direct Node binary.  ``proc.kill()``
                # only terminates the immediate child, leaving proto.exe +
                # grandchild node.exe alive.  Under agent retries / parallel
                # probes these orphan trees accumulate and can exhaust RAM
                # (#823).  Use ``taskkill /F /T`` to terminate the entire
                # process tree; if it fails (nonzero exit — e.g. termination
                # denied or the PID already reaped), fall back to proc.kill()
                # so the direct child is still reaped instead of leaving the
                # whole tree alive (Greptile re-review).
                if not _taskkill_tree(proc.pid):
                    _safe_kill(proc)
            elif hasattr(os, "killpg") and hasattr(os, "getpgid"):
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                _safe_kill(proc)
        except (ProcessLookupError, PermissionError, OSError, AttributeError):
            _safe_kill(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Child ignored SIGTERM (or our killpg lost the race); escalate.
            # Guard killpg/getpgid the same way the SIGTERM path above does:
            # they are POSIX-only and raise AttributeError on Windows. The
            # primary path was hardened in #552; this mirrors that guard on the
            # escalation path (added later in #433) so the same crash can't
            # re-surface here (#588).
            try:
                if sys.platform == "win32":
                    if not _taskkill_tree(proc.pid):
                        _safe_kill(proc)
                elif hasattr(os, "killpg") and hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    _safe_kill(proc)
            except (ProcessLookupError, PermissionError, OSError, AttributeError):
                _safe_kill(proc)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass  # process unkillable (e.g. D-state); leave as zombie
        raise SubprocTimeout(f"Command {cmd[0]} timed out after {timeout}s")

    return SubprocResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )
