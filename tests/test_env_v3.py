import os
import unittest
from pathlib import Path
from unittest import mock

from lib import bird_x, env


class EnvV3Tests(unittest.TestCase):
    def setUp(self):
        self._saved_credentials = dict(bird_x._credentials)

    def tearDown(self):
        bird_x._credentials.clear()
        bird_x._credentials.update(self._saved_credentials)

    def test_x_source_prefers_xai_without_bird_probe(self):
        with mock.patch("lib.bird_x.is_bird_authenticated", side_effect=AssertionError("should not probe bird auth")):
            source = env.get_x_source({"XAI_API_KEY": "test"})
        self.assertEqual("xai", source)

    def test_x_source_uses_bird_with_explicit_cookies(self):
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            source = env.get_x_source({"AUTH_TOKEN": "a", "CT0": "b"})
        self.assertEqual("bird", source)
        self.assertEqual("a", bird_x._credentials["AUTH_TOKEN"])
        self.assertEqual("b", bird_x._credentials["CT0"])

    def test_bird_auth_never_checks_browser_cookies(self):
        # The guarantee: is_bird_authenticated() must not spawn any child
        # process to probe for cookies. All subprocess paths in bird_x go
        # through subproc.run_with_timeout, so patching that covers it.
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), mock.patch(
            "lib.bird_x.subproc.run_with_timeout",
            side_effect=AssertionError("browser-cookie whoami should not run"),
        ):
            bird_x._credentials.clear()
            with mock.patch.dict(os.environ, {}, clear=False):
                self.assertIsNone(bird_x.is_bird_authenticated())

    def test_file_permission_check_skips_windows_posix_mode_bits(self):
        path = mock.Mock(spec=Path)
        with mock.patch.object(env.os, "name", "nt"), mock.patch.object(env.sys.stderr, "write") as write:
            env._check_file_permissions(path)

        path.stat.assert_not_called()
        write.assert_not_called()


class ThreadsAvailabilityTests(unittest.TestCase):
    """Threads is in the SC default-on family: same key, same per-call cost
    shape as TikTok / Instagram, so the same default-on rule applies.
    Suppression goes through EXCLUDE_SOURCES, not gated opt-in."""

    def test_threads_available_with_sc_key_only(self):
        self.assertTrue(env.is_threads_available({"SCRAPECREATORS_API_KEY": "k"}))

    def test_threads_unavailable_without_sc_key(self):
        self.assertFalse(env.is_threads_available({}))
        self.assertFalse(env.is_threads_available({"INCLUDE_SOURCES": "threads"}))

    def test_threads_does_not_require_include_sources(self):
        """Regression guard: INCLUDE_SOURCES should not be needed."""
        self.assertTrue(env.is_threads_available({
            "SCRAPECREATORS_API_KEY": "k",
            "INCLUDE_SOURCES": "",
        }))

if __name__ == "__main__":
    unittest.main()
