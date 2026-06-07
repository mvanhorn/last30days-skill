"""Tests for scripts/lib/subproc.py.

Covers the process-group cleanup path, timeout behavior, success path,
PID callback wiring, and environment inheritance.
"""

import shutil
import unittest
from unittest.mock import patch

from lib import subproc

HAS_SH = shutil.which("sh") is not None


class TestRunWithTimeout(unittest.TestCase):
    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_success_returns_stdout(self):
        result = subproc.run_with_timeout(
            ["sh", "-c", "echo hello"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr, "")

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_nonzero_exit_returns_returncode_not_exception(self):
        result = subproc.run_with_timeout(
            ["sh", "-c", "exit 3"],
            timeout=5,
        )
        self.assertEqual(result.returncode, 3)

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_captures_stderr(self):
        result = subproc.run_with_timeout(
            ["sh", "-c", "echo err >&2"],
            timeout=5,
        )
        self.assertEqual(result.stderr.strip(), "err")

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_timeout_raises_subproctimeout(self):
        with self.assertRaises(subproc.SubprocTimeout):
            subproc.run_with_timeout(
                ["sh", "-c", "sleep 10"],
                timeout=1,
            )

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_timeout_kills_process_group(self):
        """A slow child inside a shell should be killed when the group is signaled."""
        with self.assertRaises(subproc.SubprocTimeout):
            # Parent shell spawns a child that sleeps long.
            # Without process-group cleanup, the child would orphan.
            subproc.run_with_timeout(
                ["sh", "-c", "sleep 10 & wait"],
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

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_env_is_passed_through(self):
        result = subproc.run_with_timeout(
            ["sh", "-c", "echo $LAST30DAYS_TEST_VAR"],
            timeout=5,
            env={"LAST30DAYS_TEST_VAR": "custom_value", "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(result.stdout.strip(), "custom_value")

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_on_pid_callback_receives_pid(self):
        seen_pids = []
        subproc.run_with_timeout(
            ["sh", "-c", "true"],
            timeout=5,
            on_pid=lambda pid: seen_pids.append(pid),
        )
        self.assertEqual(len(seen_pids), 1)
        self.assertIsInstance(seen_pids[0], int)
        self.assertGreater(seen_pids[0], 0)

    @unittest.skipIf(not HAS_SH, "requires sh (POSIX shell)")
    def test_on_pid_callback_exceptions_are_suppressed(self):
        """If the PID callback raises, the subprocess should still run to completion."""
        def raising_callback(pid):
            raise RuntimeError("boom")

        # Should not raise, callback exception is swallowed.
        result = subproc.run_with_timeout(
            ["sh", "-c", "echo ok"],
            timeout=5,
            on_pid=raising_callback,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ok")

if __name__ == "__main__":
    unittest.main()
