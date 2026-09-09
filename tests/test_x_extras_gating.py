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


# --- AE1: Grok Bot host, cookies present, no pin ---------------------------


def test_ae1_grok_bot_blocks_scraper_xquik_and_every_cookie_leg():
    """LAST30DAYS_HOST=grok-bot on Linux with every cookie source armed: the
    chain has neither bird nor xquik, no sidecar / CDP / browser-store read
    is made for any cookie domain, and the scraper is never primed."""
    config = {
        "LAST30DAYS_HOST": "grok-bot",
        "FROM_BROWSER": "firefox",
        "AGENTCOOKIE": "on",
        "BROWSER_CDP_URL": "http://127.0.0.1:18800",
        "AUTH_TOKEN": "test-auth-token",
        "CT0": "test-ct0",
        "XQUIK_API_KEY": "dummy-xquik-key",
    }
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("shutil.which", return_value="/usr/local/bin/agentcookie"),
        mock.patch("lib.agentcookie.read_x_cookies", side_effect=AssertionError("no sidecar on grok-bot")),
        mock.patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("no CDP on grok-bot")),
        mock.patch("lib.cookie_extract.extract_cookies", side_effect=AssertionError("no browser store read on grok-bot")),
        mock.patch("lib.bird_x.set_credentials", side_effect=AssertionError("scraper must not be primed on grok-bot")),
        mock.patch("lib.bird_x.is_bird_installed", return_value=True),
        mock.patch("lib.xurl_x.is_available", return_value=False),
        mock.patch("lib.xurl_x.has_stored_auth", return_value=False),
    ):
        env._discover_and_apply_x_credentials(config)
        assert env.cookie_extraction_browsers(config) == []
        chain = env.x_backend_chain(config)
        assert "bird" not in chain
        assert "xquik" not in chain
        assert chain == []
        assert env.get_x_source(config) is None
        # Preflight reports browser cookies off: no browser resolves.
        config["_BROWSER_COOKIE_MODE"] = "plan_only"
        assert env.x_pending_browser_auth(config) is False
        # X becomes available only through an official path.
        assert env.x_backend_chain({**config, "X_BEARER_TOKEN": "dummy-bearer"}) == ["xapi"]
        assert env.x_backend_chain({**config, "XAI_API_KEY": "dummy-xai-key"}) == ["xai"]


def test_ae1_get_config_read_mode_on_grok_bot_reports_no_browsers(tmp_path, monkeypatch):
    """The resolved config's _BROWSER_COOKIE_BROWSERS is empty on a Grok Bot
    host even with FROM_BROWSER set, and read mode performs no cookie leg."""
    monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(env, "CONFIG_FILE", tmp_path / "does-not-exist.env")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LAST30DAYS_HOST", "grok-bot")
    monkeypatch.setenv("FROM_BROWSER", "firefox")
    monkeypatch.setenv("AGENTCOOKIE", "on")
    monkeypatch.delenv("LAST30DAYS_X_BACKEND", raising=False)
    with (
        mock.patch.object(env, "_load_keychain", return_value={}),
        mock.patch.object(env, "_load_pass", return_value={}),
        mock.patch.object(env, "_find_project_env", return_value=None),
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.agentcookie.read_x_cookies", side_effect=AssertionError("no sidecar on grok-bot")),
        mock.patch("lib.chrome_cdp.read_x_cookies", side_effect=AssertionError("no CDP on grok-bot")),
        mock.patch("lib.cookie_extract.extract_cookies", side_effect=AssertionError("no browser store read on grok-bot")),
    ):
        config = env.get_config(env.ConfigLoadPolicy(browser_cookies="read"))
    assert config["_BROWSER_COOKIE_BROWSERS"] == []
    assert config["_BROWSER_COOKIE_MODE"] == "read"
    assert config.get("_AUTH_TOKEN_SOURCE") is None


# --- AE7a: ambient bearer on a non-Grok host never reaches xapi ------------


def test_ae7a_non_grok_host_ambient_bearer_never_reaches_xapi():
    config = {
        "AUTH_TOKEN": "test-auth-token",
        "CT0": "test-ct0",
        "X_BEARER_TOKEN": "dummy-bearer",
    }
    with (
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.http.get", side_effect=AssertionError("no X API request off Grok Bot")),
        _stub_backends(),
    ):
        chain = env.x_backend_chain(config)
    # The X loop fails over only along this chain: bird returning nothing
    # leaves no next rung, so no X API request is possible.
    assert chain == ["bird"]
    assert "xapi" not in chain
    with _stub_backends():
        assert env.x_backend_chain({**config, "LAST30DAYS_X_BACKEND": "xapi"}) == ["xapi"]


# --- AE8: Cursor agent chat without the host key is unchanged --------------


def test_ae8_cursor_agent_without_host_key_is_unchanged():
    config = {"AGENTCOOKIE": "on"}
    with (
        mock.patch.dict("os.environ", {"CURSOR_AGENT": "1"}, clear=False),
        mock.patch("platform.system", return_value="Linux"),
        mock.patch("lib.agentcookie.read_x_cookies", return_value=dict(_PAIR)) as sidecar,
        mock.patch("lib.chrome_cdp.read_x_cookies", return_value=None),
        _no_extract(),
    ):
        env._discover_and_apply_x_credentials(config)
    assert sidecar.called
    assert config["_AUTH_TOKEN_SOURCE"] == "agentcookie"
    with _stub_backends():
        assert env.x_backend_chain(config) == ["bird"]
