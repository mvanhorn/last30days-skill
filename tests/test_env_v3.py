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

    def test_get_config_includes_perplexity_knobs(self):
        overrides = {
            "LAST30DAYS_PERPLEXITY_MODE": "search",
            "LAST30DAYS_PERPLEXITY_MODEL": "sonar-reasoning-pro",
            "LAST30DAYS_PERPLEXITY_MAX_RESULTS": "3",
            "LAST30DAYS_PERPLEXITY_SEARCH_CONTEXT_SIZE": "low",
            "LAST30DAYS_PERPLEXITY_SEARCH_MODE": "academic",
            "LAST30DAYS_PERPLEXITY_DOMAIN_FILTER": "example.com",
            "LAST30DAYS_PERPLEXITY_LANGUAGE_FILTER": "en",
            "LAST30DAYS_PERPLEXITY_COUNTRY": "US",
            "LAST30DAYS_PERPLEXITY_RECENCY_FILTER": "week",
            "LAST30DAYS_PERPLEXITY_REASONING_EFFORT": "high",
            "LAST30DAYS_PERPLEXITY_DEEP_TIMEOUT_SECONDS": "600",
        }
        with mock.patch.object(env, "CONFIG_FILE", None), \
             mock.patch.object(env, "_find_project_env", return_value=None), \
             mock.patch("lib.env._load_keychain", return_value={}), \
             mock.patch("lib.env._load_pass", return_value={}), \
             mock.patch.dict(os.environ, overrides, clear=False):
            config = env.get_config()

        for key, value in overrides.items():
            self.assertEqual(value, config[key])


class XurlSafePathGatingTests(unittest.TestCase):
    """F1: the safe/diagnose path (probe=False — what doctor uses) never
    runs xurl's live `whoami` network check; it keys on local evidence
    (xurl_x.has_stored_auth) instead."""

    BIRD_OFF = {
        "installed": False,
        "authenticated": False,
        "username": None,
        "can_install": True,
    }

    def _status(self, config, probe, stored_mock, live_mock):
        with mock.patch("lib.bird_x.get_bird_status", return_value=dict(self.BIRD_OFF)), \
             mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None), \
             mock.patch("lib.xurl_x.has_stored_auth", **stored_mock), \
             mock.patch("lib.xurl_x.is_available", **live_mock):
            return env.get_x_source_status(config, probe=probe)

    _LIVE_FORBIDDEN = {
        "side_effect": AssertionError(
            "probe=False must not run the live `xurl whoami` network check"
        )
    }
    _STORED_FORBIDDEN = {
        "side_effect": AssertionError("probe=True should use the live check")
    }

    def test_probe_false_uses_local_evidence_only(self):
        status = self._status(
            {}, probe=False,
            stored_mock={"return_value": True}, live_mock=self._LIVE_FORBIDDEN,
        )
        self.assertEqual("xurl", status["source"])
        self.assertTrue(status["xurl_available"])

    def test_probe_false_without_stored_auth_reports_unavailable(self):
        status = self._status(
            {}, probe=False,
            stored_mock={"return_value": False}, live_mock=self._LIVE_FORBIDDEN,
        )
        self.assertIsNone(status["source"])
        self.assertFalse(status["xurl_available"])

    def test_probe_true_keeps_the_live_check(self):
        status = self._status(
            {}, probe=True,
            stored_mock=self._STORED_FORBIDDEN, live_mock={"return_value": True},
        )
        self.assertEqual("xurl", status["source"])
        self.assertTrue(status["xurl_available"])

    def test_x_backend_chain_local_only_never_calls_live_check(self):
        with mock.patch(
            "lib.xurl_x.is_available",
            side_effect=AssertionError("local_only chain must not run `xurl whoami`"),
        ), mock.patch("lib.xurl_x.has_stored_auth", return_value=True):
            chain = env.x_backend_chain({}, local_only=True)
        self.assertEqual(["xurl"], chain)

    def test_x_backend_chain_default_stays_live(self):
        with mock.patch("lib.xurl_x.is_available", return_value=True), \
             mock.patch(
                 "lib.xurl_x.has_stored_auth",
                 side_effect=AssertionError("default chain should use the live check"),
             ):
            chain = env.x_backend_chain({})
        self.assertEqual(["xurl"], chain)


class ThreadsAvailabilityTests(unittest.TestCase):
    """Threads is in the SC default-on family: same key, same per-call cost
    shape as TikTok / Instagram, so the same default-on rule applies.
    Suppression goes through EXCLUDE_SOURCES, not gated opt-in."""

    def test_threads_available_with_sc_key_only(self):
        self.assertTrue(env.is_threads_available({"SCRAPECREATORS_API_KEY": "k"}))

    def test_threads_unavailable_without_sc_key(self):
        self.assertFalse(env.is_threads_available({}))
        self.assertFalse(env.is_threads_available({"INCLUDE_SOURCES": "threads"}))

    def test_threads_availability_predicate_is_key_only(self):
        """is_threads_available is availability-only (key present).

        Scheduling is gated separately by INCLUDE_SOURCES in the pipeline's
        available_sources (see TestScrapeCreatorsTierGating) — the predicate
        itself only reports whether the credential exists.
        """
        self.assertTrue(env.is_threads_available({
            "SCRAPECREATORS_API_KEY": "k",
            "INCLUDE_SOURCES": "",
        }))

if __name__ == "__main__":
    unittest.main()
