"""Tests for the --diagnose / --preflight browser-auth pending signal.

`--diagnose` and `--preflight` load config in `plan_only` mode, which skips
browser-cookie extraction (no Keychain popup). Before this fix that made X drop
out of `available_sources` whenever auth came from FROM_BROWSER, even though a
real run authenticates X fine — a false-negative that sent a debugging session
down a 30-minute wrong path. `env.x_pending_browser_auth` reports the
"available pending browser auth" state without reading any cookie, and
`pipeline.diagnose` / `pipeline.available_sources` surface it.
"""

from unittest import mock

from lib import env, pipeline


def _cfg(**over):
    cfg = {"_BROWSER_COOKIE_MODE": "plan_only"}
    cfg.update(over)
    return cfg


class TestXPendingBrowserAuth:
    """The no-read predicate in env.py."""

    def test_pending_true_with_from_browser_and_bird(self):
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(cfg) is True

    def test_false_when_no_browser_resolved(self):
        # FROM_BROWSER=off -> cookie_extraction_browsers() returns [].
        cfg = _cfg(FROM_BROWSER="off")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(cfg) is False

    def test_false_when_from_browser_unset(self):
        cfg = _cfg()
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(cfg) is False

    def test_false_when_x_available_outright(self):
        # Static bird creds / xurl / xquik -> get_x_source truthy -> not pending.
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value="bird"):
            assert env.x_pending_browser_auth(cfg) is False

    def test_false_when_xai_key_present(self):
        # Real get_x_source path: an xAI key makes X available outright.
        cfg = _cfg(FROM_BROWSER="chrome", XAI_API_KEY="dummy-not-real")
        assert env.x_pending_browser_auth(cfg) is False

    def test_false_when_bird_not_installed(self):
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=False):
            assert env.x_pending_browser_auth(cfg) is False

    def test_false_in_read_mode(self):
        # A real run (mode=read) has already attempted extraction; its status
        # must be unchanged, never "pending".
        cfg = _cfg(FROM_BROWSER="chrome", _BROWSER_COOKIE_MODE="read")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            assert env.x_pending_browser_auth(cfg) is False

    def test_reads_no_cookies(self):
        # R3: the predicate must never read cookie values or hit Keychain.
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True), \
             mock.patch("lib.env.extract_browser_credentials",
                        side_effect=AssertionError("must not read cookies")), \
             mock.patch("lib.cookie_extract.extract_cookies",
                        side_effect=AssertionError("must not read cookies")):
            assert env.x_pending_browser_auth(cfg) is True


class TestDiagnoseSurfacesPending:
    """available_sources + diagnose consume the predicate."""

    def test_diagnose_includes_pending_x_and_flag(self):
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            diag = pipeline.diagnose(cfg, safe=True)
        assert "x" in diag["available_sources"]
        assert diag["x_pending_browser_auth"] is True
        # The safe contract is preserved: no cookie values read.
        assert diag["browser_cookies"]["reads_values"] is False

    def test_diagnose_excludes_x_when_browser_off(self):
        cfg = _cfg(FROM_BROWSER="off")
        with mock.patch("lib.env.get_x_source", return_value=None), \
             mock.patch("lib.bird_x.is_bird_installed", return_value=True):
            diag = pipeline.diagnose(cfg, safe=True)
        assert "x" not in diag["available_sources"]
        assert diag["x_pending_browser_auth"] is False

    def test_diagnose_x_outright_not_pending(self):
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value="bird"):
            diag = pipeline.diagnose(cfg, safe=True)
        assert "x" in diag["available_sources"]
        assert diag["x_pending_browser_auth"] is False

    def test_available_sources_no_double_x(self):
        # When X is available outright, the elif must not also append a 2nd "x".
        cfg = _cfg(FROM_BROWSER="chrome")
        with mock.patch("lib.env.get_x_source", return_value="bird"):
            sources = pipeline.available_sources(cfg)
        assert sources.count("x") == 1
