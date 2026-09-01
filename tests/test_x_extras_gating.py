"""Host-gated X cookie extras: MacBook stays on main; Linux/Mac mini/sink get
the two extra bird cookie lookups (agentcookie sidecar, live Chrome CDP).

Locks the acceptance examples:
  * AE7  Linux, no bird cookies, grok AUTH_OK + XAI_API_KEY -> xai (not grok).
  * AE8  MacBook (hw.model MacBookPro), FROM_BROWSER unset, agentcookie on PATH,
         grok AUTH_OK + XAI_API_KEY -> xai; NO agentcookie subprocess, NO CDP.
  * AE10 Darwin Mac mini (hw.model Macmini9,1), agentcookie sidecar pair,
         FROM_BROWSER unset -> bird from agentcookie. ~/.hermes never flips a
         MacBook into extras.

Only obvious dummy cookie values are used (test-auth-token / test-ct0).
"""

from unittest import mock

from lib import env

_PAIR = {"auth_token": "test-auth-token", "ct0": "test-ct0"}


def _no_extract():
    """Patch the mainline browser extractor to a no-op (FROM_BROWSER unset)."""
    return mock.patch.object(env, "extract_browser_credentials", return_value={})


def _stub_backends():
    """Local-only backend probes so get_x_source touches no network.

    Returns an ExitStack already entered; use as ``with _stub_backends():``.
    """
    import contextlib
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("lib.bird_x.is_bird_installed", return_value=True))
    stack.enter_context(mock.patch("lib.bird_x.set_credentials", lambda *a, **k: None))
    stack.enter_context(mock.patch("lib.xurl_x.is_available", return_value=False))
    return stack


# --- x_extras_enabled matrix ----------------------------------------------


def test_extras_enabled_on_linux():
    with mock.patch("platform.system", return_value="Linux"):
        assert env.x_extras_enabled({}) is True


def test_extras_enabled_on_mac_mini():
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="Macmini9,1"),
    ):
        assert env.x_extras_enabled({}) is True


def test_extras_enabled_on_darwin_sink_role():
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
        mock.patch("lib.agentcookie.role_is_sink", return_value=True),
    ):
        assert env.x_extras_enabled({}) is True


def test_extras_disabled_on_plain_macbook():
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
        mock.patch("lib.agentcookie.role_is_sink", return_value=False),
    ):
        assert env.x_extras_enabled({}) is False


def test_agentcookie_on_opts_in_a_macbook():
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
    ):
        assert env.x_extras_enabled({"AGENTCOOKIE": "on"}) is True


def test_agentcookie_off_keeps_macbook_off():
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
        mock.patch("lib.agentcookie.role_is_sink", return_value=False),
    ):
        assert env.x_extras_enabled({"AGENTCOOKIE": "off"}) is False


def test_windows_has_no_extras():
    with mock.patch("platform.system", return_value="Windows"):
        assert env.x_extras_enabled({}) is False


def test_hermes_home_and_env_never_flip_extras():
    """~/.hermes / HERMES_AGENT / OPENCLAW_CLI must NOT enable extras (AE10)."""
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
        mock.patch("lib.agentcookie.role_is_sink", return_value=False),
        mock.patch.dict("os.environ", {"HERMES_AGENT": "1", "OPENCLAW_CLI": "1"}, clear=False),
    ):
        assert env.x_extras_enabled({}) is False


# --- AE8: MacBook is untouched --------------------------------------------


def test_ae8_macbook_no_extras_no_subprocess_and_picks_xai():
    config = {"XAI_API_KEY": "dummy-xai-key"}  # no AUTH_TOKEN/CT0, FROM_BROWSER unset
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="MacBookPro18,2"),
        mock.patch("lib.agentcookie.role_is_sink", return_value=False),
        # These MUST NOT run on a MacBook:
        mock.patch("lib.agentcookie.read_x_cookies", side_effect=AssertionError("no agentcookie subprocess on MacBook")),
        mock.patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("no CDP on MacBook")),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
        assert config.get("AUTH_TOKEN") is None
        assert config.get("CT0") is None
        # Backend selection: leftover grok + XAI_API_KEY must use xai (grok pin-only).
        with mock.patch("lib.grok_x.has_stored_auth", return_value=True), _stub_backends():
            assert env.get_x_source(config) == "xai"


# --- AE10: Mac mini gets the sidecar pair ---------------------------------


def test_ae10_mac_mini_reads_bird_pair_from_agentcookie():
    config = {}  # FROM_BROWSER unset
    with (
        mock.patch("platform.system", return_value="Darwin"),
        mock.patch.object(env, "_mac_model", return_value="Macmini9,1"),
        mock.patch("lib.agentcookie.read_x_cookies", return_value=dict(_PAIR)),
        mock.patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("agentcookie already won")),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
    assert config["AUTH_TOKEN"] == "test-auth-token"
    assert config["CT0"] == "test-ct0"
    assert config["_AUTH_TOKEN_SOURCE"] == "agentcookie"
    with mock.patch("lib.grok_x.has_stored_auth", return_value=False), _stub_backends():
        assert env.get_x_source(config) == "bird"


# --- AE7: Linux picks xai over a stale grok --------------------------------


def test_ae7_linux_no_cookies_grok_and_xai_picks_xai():
    config = {"XAI_API_KEY": "dummy-xai-key"}
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.agentcookie.read_x_cookies", return_value=None),
        mock.patch("lib.chrome_cdp.read_x_cookies", return_value=None),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
        assert config.get("AUTH_TOKEN") is None
        assert config.get("CT0") is None
        with mock.patch("lib.grok_x.has_stored_auth", return_value=True), _stub_backends():
            assert env.get_x_source(config) == "xai"


# --- discovery ordering / no-overwrite ------------------------------------


def test_explicit_env_pair_not_overwritten_on_extra_host():
    config = {"AUTH_TOKEN": "explicit-token", "CT0": "explicit-ct0"}
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.agentcookie.read_x_cookies", side_effect=AssertionError("no discovery with complete env pair")),
        mock.patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("no discovery with complete env pair")),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
    assert config["AUTH_TOKEN"] == "explicit-token"
    assert config["CT0"] == "explicit-ct0"


def test_cdp_used_when_agentcookie_empty_on_extra_host():
    config = {}
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.agentcookie.read_x_cookies", return_value=None),
        mock.patch("lib.chrome_cdp.read_x_cookies", return_value=dict(_PAIR)),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
    assert config["AUTH_TOKEN"] == "test-auth-token"
    assert config["_AUTH_TOKEN_SOURCE"] == "chrome cdp"
