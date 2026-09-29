"""Subprocess helpers: safe timeout + process-group cleanup.

Used by bird_x.py (Node.js Bird search) and youtube_yt.py (yt-dlp search
and transcript download). Both need the same os.setsid/killpg cleanup
dance on timeout to avoid orphaning child processes.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional, Sequence


class SubprocTimeout(Exception):
    """Raised when a subprocess exceeds its timeout and is killed."""


# Live run_with_timeout children, process-wide. Each child is a session
# leader (os.setsid), so a group kill aimed at the engine never reaches it;
# the engine's SIGTERM handler and atexit hook drain this set instead. It
# lives here, not in last30days.py, because the entrypoint runs as
# __main__: importing it by name from lib/ executes a second module copy
# with its own empty registry, and on a worker thread that copy cannot
# install its signal handler either. RLock because cleanup_children runs
# inside a signal handler on the main thread, which may already hold the
# lock in register_child_pid.
_child_pids: set[int] = set()
_child_pids_lock = threading.RLock()
# Set once cleanup_children starts. Worker threads keep running while the
# handler sleeps through the grace, so a source can spawn a child after the
# snapshot; register_child_pid kills such late children itself.
_shutting_down = False


def register_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.add(pid)
        late = _shutting_down
    if late:
        _kill_child_group(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def unregister_child_pid(pid: int) -> None:
    with _child_pids_lock:
        _child_pids.discard(pid)


# Upper bound on how long cleanup_children waits for SIGTERMed groups before
# SIGKILLing them. It runs inside the engine's SIGTERM handler, so it must
# finish well inside the MCP server's termGracePeriod
# (mcp/internal/engine/run.go), after which the engine group is SIGKILLed and
# the handler never reaches the escalation. Pinned by tests/test_subproc.py.
CLEANUP_TERM_GRACE_SECONDS = 0.8
_CLEANUP_POLL_SECONDS = 0.05


def _signal_group(pgid: int, sig: int) -> bool:
    """Signal a child's process group; False once the group has no members."""
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def _kill_child_group(pid: int, sig: int) -> None:
    if hasattr(os, "setsid") and hasattr(os, "killpg"):
        _signal_group(pid, sig)
        return
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def cleanup_children(grace: float = CLEANUP_TERM_GRACE_SECONDS) -> None:
    """Terminate the process group of every registered child.

    SIGTERM every group, wait up to ``grace`` seconds for the groups to
    empty, then SIGKILL the survivors. run_with_timeout starts each child
    with os.setsid, so the child's pid is its pgid; signalling the pgid
    directly still reaches grandchildren after the leader has been reaped,
    and the kernel does not reuse a pid while a group of that id has
    members. A group whose only member is an unreaped zombie still reads as
    live, which at worst costs the full grace and a harmless SIGKILL.
    Children registered after this call starts are SIGKILLed as they
    register, since the caller is about to exit.
    """
    global _shutting_down
    with _child_pids_lock:
        _shutting_down = True
        pids = list(_child_pids)
    if not pids:
        return
    if not (hasattr(os, "setsid") and hasattr(os, "killpg")):
        for pid in pids:
            _kill_child_group(pid, signal.SIGTERM)
        return
    live = [pgid for pgid in pids if _signal_group(pgid, signal.SIGTERM)]
    deadline = time.monotonic() + grace
    while live and time.monotonic() < deadline:
        time.sleep(_CLEANUP_POLL_SECONDS)
        live = [pgid for pgid in live if _signal_group(pgid, 0)]
    for pgid in live:
        _signal_group(pgid, signal.SIGKILL)


@dataclass
class SubprocResult:
    """Result of a subprocess run that captured stdout and stderr."""

    returncode: int
    stdout: str
    stderr: str


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
            spawn. Exceptions raised by the callback are suppressed. The
            child is registered for cleanup_children() regardless.

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

    register_child_pid(proc.pid)
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.kill()
            except (ProcessLookupError, PermissionError, OSError, AttributeError):
                proc.kill()
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
                    if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except (ProcessLookupError, PermissionError, OSError, AttributeError):
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # process unkillable (e.g. D-state); leave as zombie
            raise SubprocTimeout(f"Command {cmd[0]} timed out after {timeout}s")
    finally:
        unregister_child_pid(proc.pid)

    return SubprocResult(
        returncode=proc.returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )
