import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

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
        with mock.patch("lib.bird_x.is_bird_installed", return_value=True), mock.patch(
            "lib.bird_x.subprocess.run",
            side_effect=AssertionError("browser-cookie whoami should not run"),
        ):
            bird_x._credentials.clear()
            with mock.patch.dict(os.environ, {}, clear=False):
                self.assertIsNone(bird_x.is_bird_authenticated())


if __name__ == "__main__":
    unittest.main()
