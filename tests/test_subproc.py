"""Tests for scripts/lib/subproc.py.

Covers the process-group cleanup path, timeout behavior, success path,
PID callback wiring, and environment inheritance.
"""

import builtins
import os as real_os
import platform
import unittest
from unittest.mock import patch

from lib import subproc

IS_WINDOWS = platform.system() == "Windows"

def get_shell_cmd(cmd_str: str) -> list[str]:
    if IS_WINDOWS:
        if cmd_str == "echo hello":
            return ["cmd", "/c", "echo hello"]
        elif cmd_str == "exit 3":
            return ["cmd", "/c", "exit 3"]
        elif cmd_str == "echo err >&2":
            return ["cmd", "/c", "echo err 1>&2"]
        elif cmd_str in ("sleep 10", "sleep 10 & wait"):
            return ["powershell", "-Command", "Start-Sleep 10"]
        elif cmd_str == "echo $LAST30DAYS_TEST_VAR":
            return ["cmd", "/c", "echo %LAST30DAYS_TEST_VAR%"]
        elif cmd_str == "true":
            return ["cmd", "/c", "exit 0"]
        elif cmd_str == "echo ok":
            return ["cmd", "/c", "echo ok"]
        else:
            raise ValueError(f"No Windows command mapping for: {cmd_str}")
    return ["sh", "-c", cmd_str]


class TestRunWithTimeout(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, subproc, "_shutting_down", False)

    def test_success_returns_stdout(self):
        result = subproc.run_with_timeout(
            get_shell_cmd("echo hello"),
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr, "")

    def test_nonzero_exit_returns_returncode_not_exception(self):
        result = subproc.run_with_timeout(
            get_shell_cmd("exit 3"),
            timeout=5,
        )
        self.assertEqual(result.returncode, 3)

    def test_captures_stderr(self):
        result = subproc.run_with_timeout(
            get_shell_cmd("echo err >&2"),
            timeout=5,
        )
        self.assertEqual(result.stderr.strip(), "err")

    def test_timeout_raises_subproctimeout(self):
        with self.assertRaises(subproc.SubprocTimeout):
            subproc.run_with_timeout(
                get_shell_cmd("sleep 10"),
                timeout=1,
            )

    def test_timeout_kills_process_group(self):
        """A slow child inside a shell should be killed when the group is signaled."""
        with self.assertRaises(subproc.SubprocTimeout):
            # Parent shell spawns a child that sleeps long.
            # Without process-group cleanup, the child would orphan.
            subproc.run_with_timeout(
                get_shell_cmd("sleep 10 & wait"),
                timeout=1,
            )

    def test_missing_command_raises_oserror(self):
        """Missing executables raise FileNotFoundError (or PermissionError on
        some filesystems if a same-named junk file exists)."""
        with self.assertRaises(OSError):
            subproc.run_with_timeout(
                ["/nonexistent-path/last30days-test-no-such-bin"],
                timeout=5,
            )

    def test_env_is_passed_through(self):
        import os
        env = {"LAST30DAYS_TEST_VAR": "custom_value"}
        if IS_WINDOWS:
            for k in ("SystemRoot", "SystemDrive", "PATH", "COMSPEC", "TEMP", "TMP"):
                if k in os.environ:
                    env[k] = os.environ[k]
        else:
            env["PATH"] = "/usr/bin:/bin"
        result = subproc.run_with_timeout(
            get_shell_cmd("echo $LAST30DAYS_TEST_VAR"),
            timeout=5,
            env=env,
        )
        self.assertEqual(result.stdout.strip(), "custom_value")

    def test_on_pid_callback_receives_pid(self):
        seen_pids = []
        subproc.run_with_timeout(
            get_shell_cmd("true"),
            timeout=5,
            on_pid=lambda pid: seen_pids.append(pid),
        )
        self.assertEqual(len(seen_pids), 1)
        self.assertIsInstance(seen_pids[0], int)
        self.assertGreater(seen_pids[0], 0)

    def test_child_pid_registered_during_run_and_cleared_after(self):
        """Every run_with_timeout child is in the cleanup registry while alive."""
        seen = []
        with patch.object(
            subproc, "register_child_pid", wraps=subproc.register_child_pid
        ) as reg, patch.object(
            subproc, "unregister_child_pid", wraps=subproc.unregister_child_pid
        ) as unreg:
            result = subproc.run_with_timeout(
                get_shell_cmd("echo ok"),
                timeout=5,
                on_pid=seen.append,
            )
        self.assertEqual(result.stdout.strip(), "ok")
        reg.assert_called_once_with(seen[0])
        unreg.assert_called_once_with(seen[0])
        self.assertEqual(subproc._child_pids, set())

    def test_timed_out_child_is_unregistered(self):
        with self.assertRaises(subproc.SubprocTimeout):
            subproc.run_with_timeout(get_shell_cmd("sleep 10"), timeout=1)
        self.assertEqual(subproc._child_pids, set())

    @unittest.skipIf(IS_WINDOWS, "process groups are POSIX-only")
    def test_cleanup_children_kills_group_spawned_on_worker_thread(self):
        """Pipeline sources spawn on ThreadPoolExecutor workers; the main-thread
        SIGTERM handler must see those children in the same registry and kill
        the whole setsid group, grandchildren included."""
        import tempfile
        import threading
        import time

        outcome = {}
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = real_os.path.join(tmp, "grandchild.pid")

            def worker():
                try:
                    subproc.run_with_timeout(
                        ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"],
                        timeout=20,
                        on_pid=lambda pid: outcome.update(pid=pid),
                    )
                    outcome["error"] = None
                except Exception as exc:
                    outcome["error"] = exc

            thread = threading.Thread(target=worker)
            start = time.monotonic()
            thread.start()
            deadline = start + 5
            while time.monotonic() < deadline and not (
                real_os.path.exists(pidfile) and real_os.path.getsize(pidfile)
            ):
                time.sleep(0.01)
            self.assertTrue(real_os.path.getsize(pidfile))
            self.assertIn(outcome["pid"], subproc._child_pids)

            subproc.cleanup_children()
            thread.join(10)

        # The backgrounded sleep inherits the stdout pipe, so communicate()
        # returns well before the 20s timeout only if the grandchild died too.
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, 10)
        self.assertIsNone(outcome["error"])
        self.assertEqual(subproc._child_pids, set())

    @unittest.skipIf(IS_WINDOWS, "process groups are POSIX-only")
    def test_cleanup_children_sigkills_term_ignoring_group(self):
        """Engine SIGTERM cannot fall through to run_with_timeout's own SIGKILL
        escalation, so cleanup_children must escalate a group whose leader and
        grandchild both ignore SIGTERM."""
        import tempfile
        import threading
        import time

        outcome = {}
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = real_os.path.join(tmp, "grandchild.pid")

            def worker():
                try:
                    outcome["result"] = subproc.run_with_timeout(
                        ["sh", "-c", f"trap '' TERM; sleep 30 & echo $! > {pidfile}; wait"],
                        timeout=20,
                    )
                except Exception as exc:
                    outcome["error"] = exc

            thread = threading.Thread(target=worker)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (
                real_os.path.exists(pidfile) and real_os.path.getsize(pidfile)
            ):
                time.sleep(0.01)
            self.assertTrue(real_os.path.getsize(pidfile))

            start = time.monotonic()
            subproc.cleanup_children(grace=0.3)
            elapsed = time.monotonic() - start
            thread.join(5)

        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 1.0)
        self.assertFalse(thread.is_alive())
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["result"].returncode, -9)
        self.assertEqual(subproc._child_pids, set())

    @unittest.skipIf(IS_WINDOWS, "process groups are POSIX-only")
    def test_child_registered_after_cleanup_started_is_killed(self):
        """Workers keep running while the handler waits out the grace; a child
        spawned after the snapshot must not outlive the engine."""
        import time

        subproc._shutting_down = True
        start = time.monotonic()
        result = subproc.run_with_timeout(get_shell_cmd("sleep 10"), timeout=5)
        self.assertEqual(result.returncode, -9)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(subproc._child_pids, set())

    def test_cleanup_children_returns_immediately_when_registry_empty(self):
        import time

        self.assertEqual(subproc._child_pids, set())
        start = time.monotonic()
        subproc.cleanup_children()
        self.assertLess(time.monotonic() - start, 0.05)

    def test_cleanup_grace_fits_inside_mcp_term_grace(self):
        """The engine's SIGTERM handler runs cleanup_children; the MCP server
        SIGKILLs the engine termGracePeriod after its SIGTERM. Keep at least
        half of that window as margin for the SIGKILL pass and handler exit."""
        import pathlib
        import re

        run_go = pathlib.Path(__file__).resolve().parents[1] / "mcp" / "internal" / "engine" / "run.go"
        match = re.search(
            r"^const termGracePeriod = (\d+) \* time\.Second$",
            run_go.read_text(encoding="utf-8"),
            re.M,
        )
        self.assertIsNotNone(match, "termGracePeriod declaration not found in run.go")
        self.assertLessEqual(subproc.CLEANUP_TERM_GRACE_SECONDS * 2, int(match.group(1)))

    def test_lib_never_imports_engine_entrypoint(self):
        """last30days.py runs as __main__; importing it by name from lib/
        executes a second copy whose state (child registry, signal handler)
        the running engine never sees."""
        import pathlib
        import re

        lib_dir = pathlib.Path(subproc.__file__).parent
        pattern = re.compile(r"^\s*(from\s+last30days\s+import|import\s+last30days\b)", re.M)
        offenders = [
            str(path.relative_to(lib_dir))
            for path in lib_dir.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(offenders, [])

    def test_timeout_falls_back_to_kill_when_killpg_unavailable(self):
        """Simulate Windows (no killpg/getpgid) — should fall back to proc.kill()."""
        real_hasattr = builtins.hasattr

        def selective_hasattr(obj, name):
            if obj is real_os and name in ("killpg", "getpgid", "setsid"):
                return False
            return real_hasattr(obj, name)

        with patch.object(builtins, "hasattr", side_effect=selective_hasattr):
            with self.assertRaises(subproc.SubprocTimeout):
                subproc.run_with_timeout(
                    ["sh", "-c", "sleep 10"],
                    timeout=1,
                )

    def test_on_pid_callback_exceptions_are_suppressed(self):
        """If the PID callback raises, the subprocess should still run to completion."""
        def raising_callback(pid):
            raise RuntimeError("boom")

        # Should not raise, callback exception is swallowed.
        result = subproc.run_with_timeout(
            get_shell_cmd("echo ok"),
            timeout=5,
            on_pid=raising_callback,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_sigterm_ignoring_child_is_sigkill_escalated(self):
        """A child that ignores SIGTERM must be escalated to SIGKILL.

        Without escalation, ``proc.wait(timeout=5)`` raises
        ``subprocess.TimeoutExpired`` past the ``except`` block instead of the
        documented ``SubprocTimeout``, and the child stays alive.
        """
        with self.assertRaises(subproc.SubprocTimeout):
            subproc.run_with_timeout(
                ["sh", "-c", "trap '' TERM; sleep 30"],
                timeout=1,
            )

    def test_escalation_path_guards_killpg_attributeerror(self):
        """The SIGKILL escalation must not crash if killpg is unavailable (Windows).

        Regression for the #588 class of bug on the escalation path added in
        #433: os.killpg raising AttributeError must be caught and fall back to
        proc.kill(), so the documented SubprocTimeout surfaces instead of a bare
        AttributeError. The primary SIGTERM path was already guarded (#552); this
        mirrors that guard on the escalation path.
        """
        TimeoutExpired = subproc.subprocess.TimeoutExpired

        class _FakeProc:
            def __init__(self):
                self.pid = 4321
                self.kill_count = 0

            def communicate(self, timeout=None):
                raise TimeoutExpired(cmd="x", timeout=timeout)

            def wait(self, timeout=None):
                # First wait (timeout=5) forces the SIGKILL escalation branch;
                # the final bounded wait is swallowed if the process still
                # refuses to exit.
                if timeout is not None:
                    raise TimeoutExpired(cmd="x", timeout=timeout)
                return 0

            def kill(self):
                self.kill_count += 1

        fake = _FakeProc()
        with patch.object(subproc.subprocess, "Popen", return_value=fake), \
             patch.object(subproc.os, "getpgid", lambda pid: pid), \
             patch.object(subproc.os, "killpg", side_effect=AttributeError("no killpg on Windows")):
            with self.assertRaises(subproc.SubprocTimeout):
                subproc.run_with_timeout(["x"], timeout=1)
        # Both the primary and escalation paths must have fallen back to kill().
        self.assertGreaterEqual(fake.kill_count, 2)


if __name__ == "__main__":
    unittest.main()
