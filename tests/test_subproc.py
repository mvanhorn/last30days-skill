"""Tests for scripts/lib/subproc.py.

Covers the process-group cleanup path, timeout behavior, success path,
PID callback wiring, and environment inheritance.
"""

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
    return ["sh", "-c", cmd_str]


class TestRunWithTimeout(unittest.TestCase):
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

if __name__ == "__main__":
    unittest.main()
