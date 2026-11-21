"""X browser-session failover: an expired session in an earlier browser must
not shadow a live session in a later browser (FROM_BROWSER=auto)."""

from unittest import mock

import pytest

from lib import bird_x, env, health
from lib.subproc import SubprocResult

DEAD = {"auth_token": "dead-session-token-0000", "ct0": "dead-ct0-0000000000"}
LIVE = {"auth_token": "live-session-token-1111", "ct0": "live-ct0-1111111111"}

SEARCH_500 = '{"error": "Internal server error", "items": []}'
SEARCH_401 = '{"error": "HTTP 401: {\\"errors\\":[{\\"message\\":\\"Could not authenticate you\\",\\"code\\":32}]}", "items": []}'
SEARCH_OK = '[{"id": "1", "text": "tweet"}]'


@pytest.fixture(autouse=True)
def _reset_bird_state():
    saved = dict(bird_x._credentials)
    bird_x._credentials.clear()
    bird_x.set_session_failover(None, None)
    yield
    bird_x._credentials.clear()
    bird_x._credentials.update(saved)
    bird_x.set_session_failover(None, None)


def _config(**overrides):
    cfg = {
        "AUTH_TOKEN": None,
        "CT0": None,
        "TRUTHSOCIAL_TOKEN": None,
        "FROM_BROWSER": "auto",
        "BROWSER_CONSENT": "true",
        "SETUP_COMPLETE": None,
    }
    cfg.update(overrides)
    return cfg


def _browser_cookies(by_browser):
    def fake(browser, domain, names):
        if domain != ".x.com":
            return None
        return by_browser.get(browser)
    return fake


def _bird_outputs(*stdouts):
    return [(SubprocResult(returncode=0, stdout=s, stderr=""), None) for s in stdouts]


def _discover(cfg, by_browser):
    with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies(by_browser)):
        env._apply_browser_extract(cfg)
    return cfg


class TestEnvBookkeeping:
    def test_records_source_browser_and_remaining_browsers(self):
        cfg = _discover(_config(), {"safari": DEAD, "chrome": LIVE})
        assert cfg["AUTH_TOKEN"] == DEAD["auth_token"]
        state = cfg[env.X_SESSION_STATE_KEY]
        assert state["browser"] == "safari"
        assert state["remaining"][0] == "chrome"
        assert "firefox" not in state["remaining"]

    def test_explicit_env_pair_records_nothing(self):
        cfg = _discover(_config(AUTH_TOKEN="x" * 20, CT0="y" * 20), {"safari": DEAD})
        assert env.X_SESSION_STATE_KEY not in cfg

    def test_next_pair_skips_incomplete_and_same_session(self):
        cfg = _discover(_config(), {"safari": DEAD, "chrome": {"auth_token": DEAD["auth_token"], "ct0": "z" * 12},
                                    "brave": {"ct0": "only-ct0"}, "edge": LIVE})
        with mock.patch("lib.cookie_extract.extract_cookies",
                        side_effect=_browser_cookies({"chrome": {"auth_token": DEAD["auth_token"], "ct0": "z" * 12},
                                                      "brave": {"ct0": "only-ct0"}, "edge": LIVE})):
            pair = env.next_x_browser_pair(cfg)
        assert pair == (LIVE["auth_token"], LIVE["ct0"], "edge")
        assert cfg["AUTH_TOKEN"] == LIVE["auth_token"]
        assert cfg[env.X_SESSION_STATE_KEY]["browser"] == "edge"

    def test_next_pair_none_when_exhausted(self):
        cfg = _discover(_config(), {"safari": DEAD})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({})):
            assert env.next_x_browser_pair(cfg) is None
            assert env.next_x_browser_pair(cfg) is None

    def test_next_pair_never_replaces_non_browser_session(self):
        cfg = _config(AUTH_TOKEN="a" * 20, CT0="b" * 20, _AUTH_TOKEN_SOURCE="agentcookie",
                      **{env.X_SESSION_STATE_KEY: {"browser": "safari", "remaining": ["chrome"],
                                                   "rejected": set(), "auth_token": "a" * 20, "ct0": "b" * 20}})
        with mock.patch("lib.cookie_extract.extract_cookies") as extract:
            assert env.next_x_browser_pair(cfg) is None
        extract.assert_not_called()

    def test_inject_registers_failover_only_for_browser_sessions(self):
        cfg = _config(AUTH_TOKEN="a" * 20, CT0="b" * 20, _AUTH_TOKEN_SOURCE="env")
        env._inject_bird_credentials(cfg)
        assert bird_x._next_session is None
        cfg = _discover(_config(), {"safari": DEAD})
        env._inject_bird_credentials(cfg)
        assert bird_x._session_browser == "safari"
        assert bird_x._next_session is not None


class TestBirdFailover:
    def _inject(self, by_browser):
        cfg = _discover(_config(), by_browser)
        env._inject_bird_credentials(cfg)
        return cfg

    def test_stale_first_browser_fails_over_to_live_session(self):
        cfg = self._inject({"safari": DEAD, "chrome": LIVE})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({"chrome": LIVE})), \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_500, SEARCH_OK)) as run:
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert response["items"] == [{"id": "1", "text": "tweet"}]
        assert run.call_count == 2
        assert bird_x._credentials["AUTH_TOKEN"] == LIVE["auth_token"]
        assert cfg[env.X_SESSION_STATE_KEY]["browser"] == "chrome"

    def test_switched_session_sticks_for_later_searches(self):
        self._inject({"safari": DEAD, "chrome": LIVE})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({"chrome": LIVE})) as extract, \
             mock.patch.object(bird_x, "_invoke_bird_subprocess",
                               side_effect=_bird_outputs(SEARCH_401, SEARCH_OK, SEARCH_OK)):
            bird_x._run_bird_search("one", count=10, timeout=30)
            bird_x._run_bird_search("two", count=10, timeout=30)
        assert extract.call_count == 1

    def test_rejected_session_without_alternate_is_auth_failed(self):
        self._inject({"safari": DEAD})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({})), \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_401)):
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert "session rejected (cookies from safari)" in response["error"]
        assert bird_x.classify_run_failure(response["error"]) == health.AUTH_FAILED

    def test_internal_error_without_alternate_hints_but_stays_generic(self):
        self._inject({"safari": DEAD})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({})), \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_500)):
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert response["error"].startswith("Internal server error")
        assert "safari session" in response["error"]
        assert bird_x.classify_run_failure(response["error"]) != health.AUTH_FAILED

    def test_no_failover_registered_leaves_error_untouched(self):
        bird_x.set_credentials("a" * 20, "b" * 20)
        with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_500)) as run:
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert response["error"] == "Internal server error"
        assert run.call_count == 1

    def test_unrelated_error_does_not_read_other_browsers(self):
        self._inject({"safari": DEAD, "chrome": LIVE})
        timeout_error = (None, {"error": "Search timed out after 30s", "items": []})
        with mock.patch("lib.cookie_extract.extract_cookies") as extract, \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=[timeout_error]):
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert response["error"] == "Search timed out after 30s"
        extract.assert_not_called()

    def test_failover_respects_exhausted_deadline(self):
        self._inject({"safari": DEAD, "chrome": LIVE})
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({"chrome": LIVE})) as extract, \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_500)) as run, \
             mock.patch.object(bird_x, "_clamped_bird_timeout", return_value=None):
            response = bird_x._run_bird_search("kanye", count=10, timeout=30, deadline=1.0)
        assert response["error"].startswith("Internal server error")
        assert run.call_count == 1
        assert extract.call_count == 0


class TestGreptileRegressions:
    def test_config_copy_cannot_reinject_rejected_session(self):
        """pipeline.run searches on dict(config); a later phase reuses config."""
        cfg = _discover(_config(), {"safari": DEAD, "chrome": LIVE})
        run_copy = dict(cfg)
        env._inject_bird_credentials(run_copy)
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies({"chrome": LIVE})), \
             mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_500, SEARCH_OK)):
            bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert cfg["AUTH_TOKEN"] == DEAD["auth_token"]  # the original copy is stale
        env._inject_bird_credentials(cfg)  # supplemental phase re-injects
        assert bird_x._credentials["AUTH_TOKEN"] == LIVE["auth_token"]

    def test_rejected_sessions_are_never_retried(self):
        """Browser order A, B, A, C: after A and B fail, go straight to C."""
        b_pair = {"auth_token": "second-dead-token-2222", "ct0": "second-dead-ct0-22"}
        c_pair = {"auth_token": "third-live-token-3333", "ct0": "third-live-ct0-333"}
        cfg = _discover(_config(FROM_BROWSER="safari,chrome,brave,edge"), {"safari": DEAD})
        env._inject_bird_credentials(cfg)
        others = {"chrome": b_pair, "brave": DEAD, "edge": c_pair}
        with mock.patch("lib.cookie_extract.extract_cookies", side_effect=_browser_cookies(others)), \
             mock.patch.object(bird_x, "_invoke_bird_subprocess",
                               side_effect=_bird_outputs(SEARCH_401, SEARCH_401, SEARCH_OK)) as run:
            response = bird_x._run_bird_search("kanye", count=10, timeout=30)
        assert response["items"] == [{"id": "1", "text": "tweet"}]
        assert run.call_count == 3
        assert bird_x._credentials["AUTH_TOKEN"] == c_pair["auth_token"]

    def test_browser_walk_stops_at_deadline(self):
        cfg = _discover(_config(), {"safari": DEAD})
        with mock.patch("lib.cookie_extract.extract_cookies") as extract, \
             mock.patch("time.monotonic", return_value=100.0):
            assert env.next_x_browser_pair(cfg, deadline=101.0) is None
        extract.assert_not_called()
        assert cfg[env.X_SESSION_STATE_KEY]["remaining"]  # left for a later search

    def test_browser_walk_stops_on_cancel(self):
        import threading
        cfg = _discover(_config(), {"safari": DEAD})
        cancel = threading.Event(); cancel.set()
        with mock.patch("lib.cookie_extract.extract_cookies") as extract:
            assert env.next_x_browser_pair(cfg, cancel=cancel) is None
        extract.assert_not_called()

    def test_lock_wait_is_bounded_by_deadline(self):
        import time
        cfg = _discover(_config(), {"safari": DEAD, "chrome": LIVE})
        env._inject_bird_credentials(cfg)
        bird_x._session_lock.acquire()
        try:
            with mock.patch.object(bird_x, "_invoke_bird_subprocess", side_effect=_bird_outputs(SEARCH_401)):
                started = time.monotonic()
                response = bird_x._run_bird_search("kanye", count=10, timeout=30,
                                                   deadline=time.monotonic() + 1.5)
            assert time.monotonic() - started < 5
            assert "session rejected (cookies from safari)" in response["error"]
        finally:
            bird_x._session_lock.release()

    def test_slow_keychain_read_cannot_outlast_deadline(self):
        import threading
        import time
        cfg = _discover(_config(), {"safari": DEAD})
        release = threading.Event()

        def slow_read(browser, domain, names):
            release.wait(10)  # a Keychain prompt nobody answers
            return LIVE

        try:
            with mock.patch("lib.cookie_extract.extract_cookies", side_effect=slow_read):
                started = time.monotonic()
                pair = env.next_x_browser_pair(cfg, deadline=time.monotonic() + 3.0)
                elapsed = time.monotonic() - started
        finally:
            release.set()
        assert pair is None
        assert elapsed < 3.0
