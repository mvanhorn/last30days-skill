"""Tests for scripts/lib/subproc.py.

Covers the process-group cleanup path, timeout behavior, success path,
PID callback wiring, and environment inheritance.
"""

import builtins
import errno
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


class TestSubprocTimeout(unittest.TestCase):
    def test_exception_class_raises_without_a_message(self):
        with self.assertRaises(subproc.SubprocTimeout) as caught:
            raise subproc.SubprocTimeout
        self.assertEqual(str(caught.exception), "")
        self.assertTrue(caught.exception.started)


class TestRunWithTimeout(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, subproc, "_shutting_down", False)

    def test_spawn_errors_preserve_original_type_and_errno(self):
        for error_number in (errno.ENOENT, errno.EAGAIN, errno.EMFILE):
            error = OSError(error_number, "command could not start")
            with self.subTest(error_number=error_number), \
                 patch.object(subproc.subprocess, "Popen", side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    subproc.run_with_timeout(["missing-command"], timeout=1)
                self.assertIs(caught.exception, error)
                self.assertEqual(caught.exception.errno, error_number)

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

    @unittest.skipIf(IS_WINDOWS, "process groups are POSIX-only")
    def test_timeout_kills_grandchild_after_leader_exits(self):
        import pathlib
        import signal
        import sys
        import tempfile
        import time

        child = """
import os, pathlib, signal, sys, time
if sys.argv[2] == "ignore":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
time.sleep(15)
"""
        leader = """
import signal, subprocess, sys, time
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
subprocess.Popen([sys.executable, "-c", *sys.argv[1:]],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(15)
"""

        def running(pid):
            try:
                real_os.kill(pid, 0)
                if sys.platform.startswith("linux"):
                    state = pathlib.Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()[0]
                    return state != "Z"
                return True
            except (ProcessLookupError, FileNotFoundError):
                return False

        for mode in ("default", "ignore"):
            with self.subTest(term_handler=mode), tempfile.TemporaryDirectory() as tmp:
                pidfile = pathlib.Path(tmp, "grandchild.pid")

                def wait_ready(pid):
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if pidfile.exists() and pidfile.stat().st_size:
                            return
                        time.sleep(0.01)

                try:
                    started = time.monotonic()
                    with self.assertRaises(subproc.SubprocTimeout):
                        subproc.run_with_timeout(
                            [sys.executable, "-c", leader, child, str(pidfile), mode],
                            timeout=0.1,
                            on_pid=wait_ready,
                        )
                    self.assertLess(time.monotonic() - started, 9)
                    pid = int(pidfile.read_text())
                    deadline = time.monotonic() + 1
                    while running(pid) and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(running(pid), "grandchild survived the leader's clean TERM exit")
                    self.assertEqual(subproc._child_pids, set())
                finally:
                    if pidfile.exists() and pidfile.stat().st_size:
                        pid = int(pidfile.read_text())
                        if running(pid):
                            try:
                                real_os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass

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
        """Without POSIX groups or Windows tree cleanup, kill the direct child."""
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

    def test_windows_timeout_terminates_owned_process_tree(self):
        timeout_error = subproc.subprocess.TimeoutExpired("node", 1)

        class FakeProc:
            pid = 4321
            stdin = stdout = stderr = None
            returncode = None

            def __init__(self):
                self.killed = False

            def communicate(self, **kwargs):
                raise timeout_error

            def wait(self, timeout=None):
                self.returncode = 1
                return 1

            def kill(self):
                self.killed = True

        fake = FakeProc()
        real_hasattr = builtins.hasattr

        def windows_hasattr(obj, name):
            if obj is real_os and name in ("setsid", "killpg"):
                return False
            return real_hasattr(obj, name)

        with patch.object(builtins, "hasattr", side_effect=windows_hasattr), \
             patch.object(subproc, "_WINDOWS", True), \
             patch.object(subproc, "_kill_windows_tree", return_value=True) as kill_tree, \
             patch.object(subproc.subprocess, "Popen", return_value=fake):
            with self.assertRaises(subproc.SubprocTimeout):
                subproc.run_with_timeout(["node", "bird-search.mjs"], timeout=1)

        kill_tree.assert_called_once_with(fake.pid)
        self.assertFalse(fake.killed)

    def test_windows_tree_kill_uses_pid_scoped_taskkill_and_handles_failure(self):
        import subprocess

        with patch.object(subproc.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            self.assertTrue(subproc._kill_windows_tree(4321))
        run.assert_called_once_with(
            ["taskkill", "/F", "/T", "/PID", "4321"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        with patch.object(subproc.subprocess, "run", side_effect=subprocess.TimeoutExpired("taskkill", 2)):
            self.assertFalse(subproc._kill_windows_tree(4321))

        with patch.object(subproc.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            self.assertFalse(subproc._kill_windows_tree(4321))

    def test_windows_shutdown_terminates_owned_process_tree(self):
        real_hasattr = builtins.hasattr

        def windows_hasattr(obj, name):
            if obj is real_os and name in ("setsid", "killpg"):
                return False
            return real_hasattr(obj, name)

        with patch.object(builtins, "hasattr", side_effect=windows_hasattr), \
             patch.object(subproc, "_WINDOWS", True), \
             patch.object(subproc, "_child_pids", {4321}), \
             patch.object(subproc, "_shutting_down", False), \
             patch.object(subproc, "_kill_windows_tree", return_value=True) as kill_tree:
            subproc.cleanup_children()

        kill_tree.assert_called_once_with(4321)

    def test_windows_tree_kill_failure_falls_back_to_direct_child(self):
        timeout_error = subproc.subprocess.TimeoutExpired("node", 1)

        class FakeProc:
            pid = 4321
            stdin = stdout = stderr = None
            returncode = None

            def __init__(self):
                self.killed = False

            def communicate(self, **kwargs):
                raise timeout_error

            def wait(self, timeout=None):
                self.returncode = 1
                return 1

            def kill(self):
                self.killed = True

        fake = FakeProc()
        real_hasattr = builtins.hasattr

        def windows_hasattr(obj, name):
            if obj is real_os and name in ("setsid", "killpg"):
                return False
            return real_hasattr(obj, name)

        with patch.object(builtins, "hasattr", side_effect=windows_hasattr), \
             patch.object(subproc, "_WINDOWS", True), \
             patch.object(subproc, "_kill_windows_tree", return_value=False) as kill_tree, \
             patch.object(subproc.subprocess, "Popen", return_value=fake):
            with self.assertRaises(subproc.SubprocTimeout):
                subproc.run_with_timeout(["node", "bird-search.mjs"], timeout=1)

        kill_tree.assert_called_once_with(fake.pid)
        self.assertTrue(fake.killed)

    @unittest.skipUnless(IS_WINDOWS, "native Windows process-tree validation")
    def test_windows_timeout_kills_shim_grandchild(self):
        import ctypes
        import pathlib
        import signal
        import sys
        import tempfile
        import threading
        import time

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel.GetExitCodeProcess.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)

        def running(pid):
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)))
                return exit_code.value == 259
            finally:
                kernel.CloseHandle(handle)

        grandchild = (
            "import os,pathlib,sys,time;"
            "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
            "time.sleep(12)"
        )
        shim = (
            "import subprocess,sys,time;"
            "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]);"
            "time.sleep(30)"
        )
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = pathlib.Path(tmp, "grandchild.pid")
            shim_pids = []
            observed_live = []

            def wait_for_grandchild(pid):
                shim_pids.append(pid)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if pidfile.exists() and pidfile.stat().st_size:
                        observed_live.append(running(int(pidfile.read_text())))
                        return
                    time.sleep(0.01)
                self.fail("grandchild did not write its PID")

            def kill_owned_processes():
                grandchild_pid = pidfile.read_text().strip() if pidfile.exists() else ""
                targets = shim_pids + ([int(grandchild_pid)] if grandchild_pid.isdigit() else [])
                for pid in targets:
                    if running(pid):
                        try:
                            subproc.subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(pid)],
                                stdout=subproc.subprocess.DEVNULL,
                                stderr=subproc.subprocess.DEVNULL,
                                timeout=2,
                                check=False,
                            )
                        except (OSError, subproc.subprocess.TimeoutExpired):
                            pass
                        if running(pid):
                            try:
                                real_os.kill(pid, signal.SIGTERM)
                            except OSError:
                                pass

            watchdog = threading.Timer(10, kill_owned_processes)
            watchdog.daemon = True
            watchdog.start()
            try:
                started = time.monotonic()
                with self.assertRaises(subproc.SubprocTimeout):
                    subproc.run_with_timeout(
                        [sys.executable, "-c", shim, str(pidfile), grandchild],
                        timeout=0.2,
                        on_pid=wait_for_grandchild,
                    )
                self.assertLess(time.monotonic() - started, 8, "timeout waited for the grandchild to exit")
                self.assertTrue(shim_pids)
                self.assertEqual(observed_live, [True], "grandchild was not alive before timeout")
                self.assertTrue(pidfile.exists())
                shim_pid = shim_pids[0]
                grandchild_pid = int(pidfile.read_text())
                self.assertFalse(running(shim_pid), "shim survived its timeout")
                self.assertFalse(running(grandchild_pid), "grandchild survived its timeout")
            finally:
                watchdog.cancel()
                kill_owned_processes()

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
                self.stdin = self.stdout = self.stderr = None

            def communicate(self, timeout=None, input=None):
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
