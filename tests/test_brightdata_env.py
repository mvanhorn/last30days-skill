"""Regression checks for the Bright Data child-process environment."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))
from lib import brightdata


class ChildEnvironmentTests(unittest.TestCase):
    def test_unrelated_secrets_and_runtime_injection_are_excluded(self):
        parent = {
            "PATH": "runtime-path", "HOME": "home-path",
            "OPENAI_API_KEY": "fake-openai", "GITHUB_TOKEN": "fake-github",
            "GOOGLE_API_KEY": "fake-google", "CUSTOM_SECRET": "fake-secret",
            "NODE_OPTIONS": "--require untrusted.js", "PYTHONPATH": "untrusted",
            "BRIGHTDATA_OTHER_TOKEN": "fake-unneeded",
        }
        with patch.dict(os.environ, parent, clear=True):
            child = brightdata._child_env("fake-brightdata")
            self.assertEqual(child, {"PATH": "runtime-path", "HOME": "home-path",
                                     "BRIGHTDATA_API_KEY": "fake-brightdata"})
            self.assertNotIn("BRIGHTDATA_API_KEY", os.environ)

    def test_no_key_still_returns_a_filtered_mapping(self):
        with patch.dict(os.environ, {"PATH": "runtime-path", "OPENAI_API_KEY": "fake"}, clear=True):
            self.assertEqual(brightdata._child_env(""), {"PATH": "runtime-path"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(brightdata._child_env(""), {})

    def test_explicit_key_overrides_ambient_and_ambient_is_fallback(self):
        with patch.dict(os.environ, {"BRIGHTDATA_API_KEY": "fake-ambient"}, clear=True):
            self.assertEqual(brightdata._child_env("fake-explicit")["BRIGHTDATA_API_KEY"], "fake-explicit")
            self.assertEqual(brightdata._child_env("")["BRIGHTDATA_API_KEY"], "fake-ambient")
            self.assertEqual(os.environ["BRIGHTDATA_API_KEY"], "fake-ambient")

    def test_platform_config_and_network_settings_are_preserved(self):
        names = (
            "PATH", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
            "LOCALAPPDATA", "XDG_CONFIG_HOME", "SystemRoot", "WINDIR",
            "SystemDrive", "COMSPEC", "PATHEXT", "TMPDIR", "TEMP", "TMP",
            "LANG", "LC_ALL", "LC_CTYPE", "NODE_EXTRA_CA_CERTS", "NODE_USE_ENV_PROXY",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY",
            "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy",
        )
        # A plain mapping also checks case variants independently of Windows' os.environ.
        parent = {name: "setting" for name in names}
        with patch.object(brightdata.os, "environ", parent):
            self.assertEqual(brightdata._child_env(""), parent)

    def test_real_child_receives_only_intended_credentials(self):
        probe = (
            "import json,os; print(json.dumps([{'key_ok': "
            "os.getenv('BRIGHTDATA_API_KEY') == 'fake-brightdata', "
            "'leaked': any(k in os.environ for k in "
            "('OPENAI_API_KEY','GITHUB_TOKEN','GOOGLE_API_KEY','CUSTOM_SECRET','NODE_OPTIONS')), "
            "'path_ok': 'PATH' in os.environ}]))"
        )
        for config in ({"BRIGHTDATA_API_KEY": "fake-brightdata"}, None):
            with self.subTest(explicit_key=config is not None):
                with patch.dict(os.environ, {
                    "BRIGHTDATA_API_KEY": "fake-brightdata", "OPENAI_API_KEY": "fake",
                    "GITHUB_TOKEN": "fake", "GOOGLE_API_KEY": "fake",
                    "CUSTOM_SECRET": "fake", "NODE_OPTIONS": "--require untrusted.js",
                }), patch.object(brightdata, "_build_args", return_value=[sys.executable, "-I", "-c", probe]), patch.object(brightdata, "CLI_BIN", sys.executable):
                    result = brightdata.run_pipeline("probe", {}, timeout=10, config=config)
                self.assertEqual(result, {"records": [{"key_ok": True, "leaked": False, "path_ok": True}]})

    def test_credentials_file_location_survives_without_env_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = {"APPDATA": temp_dir, "HOME": temp_dir, "USERPROFILE": temp_dir,
                        "XDG_CONFIG_HOME": temp_dir, "OPENAI_API_KEY": "fake"}
            with patch.dict(os.environ, settings):
                os.environ.pop("BRIGHTDATA_API_KEY", None)
                credential = brightdata._config_dir() / "credentials.json"
                credential.parent.mkdir(parents=True, exist_ok=True)
                credential.write_text("{}", encoding="utf-8")
                self.assertTrue(brightdata.has_credentials())
                probe = (
                    "import json,os,pathlib,sys; "
                    "root=(pathlib.Path(os.environ['APPDATA']) if sys.platform=='win32' "
                    "else pathlib.Path.home()/'Library'/'Application Support' if sys.platform=='darwin' "
                    "else pathlib.Path(os.environ['XDG_CONFIG_HOME'])); "
                    "print(json.dumps({'found':(root/'brightdata-cli'/'credentials.json').exists(), "
                    "'key_present':'BRIGHTDATA_API_KEY' in os.environ,'leaked':'OPENAI_API_KEY' in os.environ}))"
                )
                result = brightdata.subproc.run_with_timeout(
                    [sys.executable, "-I", "-c", probe], timeout=10, env=brightdata._child_env(""))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {"found": True, "key_present": False, "leaked": False})


if __name__ == "__main__":
    unittest.main()
