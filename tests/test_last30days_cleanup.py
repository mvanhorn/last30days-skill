import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))

import last30days


class TestCleanupChildren(unittest.TestCase):
    def setUp(self) -> None:
        with last30days._child_pids_lock:
            last30days._child_pids.clear()

    def tearDown(self) -> None:
        with last30days._child_pids_lock:
            last30days._child_pids.clear()

    def test_cleanup_uses_process_group_when_available(self) -> None:
        with last30days._child_pids_lock:
            last30days._child_pids.update({1234})

        with (
            mock.patch.object(last30days.os, "getpgid", return_value=7777) as getpgid_mock,
            mock.patch.object(last30days.os, "killpg") as killpg_mock,
            mock.patch.object(last30days.os, "kill") as kill_mock,
        ):
            last30days._cleanup_children()

        getpgid_mock.assert_called_once_with(1234)
        killpg_mock.assert_called_once_with(7777, last30days.signal.SIGTERM)
        kill_mock.assert_not_called()

    def test_cleanup_falls_back_to_kill_when_process_group_apis_missing(self) -> None:
        with last30days._child_pids_lock:
            last30days._child_pids.update({4321})

        with (
            mock.patch.object(last30days.os, "killpg", None),
            mock.patch.object(last30days.os, "getpgid", None),
            mock.patch.object(last30days.os, "kill") as kill_mock,
        ):
            last30days._cleanup_children()

        kill_mock.assert_called_once_with(4321, last30days.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
