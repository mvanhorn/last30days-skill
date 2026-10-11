import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

pytest_plugins = ["tests.network_guard"]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))


_CONFIG_ENV_NAMES = {
    "AUTH_TOKEN", "CT0", "TWITTER_CT0", "SESSION_ID",
    "FROM_BROWSER", "BROWSER_CONSENT", "BROWSER_CDP_URL", "SETUP_COMPLETE",
    "AGENTCOOKIE", "AGENTCOOKIE_CONFIG", "INCLUDE_SOURCES", "EXCLUDE_SOURCES",
    "FUN_LEVEL", "ELI5_MODE", "DEGRADED_TRANSCRIPT_THRESHOLD", "TELEGRAM_SOURCES",
    "OPENAI_MODEL_PIN", "XAI_MODEL_PIN", "OPENAI_BASE_URL", "XAI_BASE_URL",
    "OPENROUTER_BASE_URL", "XIAOHONGSHU_API_BASE",
}
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_KEY", "_SECRET", "_PASSWORD", "_COOKIE")
_system_which = shutil.which
_system_popen = subprocess.Popen


def _without_credential_tools(command, *args, **kwargs):
    if Path(command).name in {"security", "pass"}:
        return None
    return _system_which(command, *args, **kwargs)


def _isolate_environment(monkeypatch, home):
    for name in list(os.environ):
        if (
            name in _CONFIG_ENV_NAMES
            or name.startswith(("LAST30DAYS_", "BSKY_", "BIRD_"))
            or name.endswith(_CREDENTIAL_SUFFIXES)
        ):
            monkeypatch.delenv(name, raising=False)
    config_dir = home / ".config" / "last30days"
    config_dir.mkdir(parents=True)
    for name, value in {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": home / ".config",
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "PASSWORD_STORE_DIR": home / ".password-store",
        "LAST30DAYS_CONFIG_DIR": config_dir,
    }.items():
        monkeypatch.setenv(name, str(value))

    class IsolatedPopen(_system_popen):
        def __init__(self, *args, **kwargs):
            child_env = dict(os.environ if kwargs.get("env") is None else kwargs["env"])
            # Python mocks do not cross process boundaries; Keychain ignores HOME.
            child_env.setdefault("LAST30DAYS_SKIP_KEYCHAIN", "1")
            child_env.setdefault("HOME", str(home))
            child_env.setdefault("USERPROFILE", child_env["HOME"])
            child_env.setdefault("PASSWORD_STORE_DIR", str(Path(child_env["HOME"]) / ".password-store"))
            kwargs["env"] = child_env
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(shutil, "which", _without_credential_tools)
    monkeypatch.setattr(subprocess, "Popen", IsolatedPopen)
    return config_dir


def pytest_configure(config):
    # CONFIG_FILE and other home-relative constants are frozen during collection.
    home = tempfile.TemporaryDirectory(prefix="last30days-tests-")
    config.add_cleanup(home.cleanup)
    patch = pytest.MonkeyPatch()
    config.add_cleanup(patch.undo)
    _isolate_environment(patch, Path(home.name))


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path_factory, monkeypatch):
    """Start each test with empty config, stores, and project discovery inputs.

    Redirect HOME rather than stubbing store lookups: xurl and grok resolve
    auth from plain files under HOME (``~/.xurl/auth.yml``), and
    ``backends._xurl_finding`` calls ``stored_auth_status()`` directly, so a
    stubbed ``has_stored_auth`` still reads a developer's real store. Tests
    that need a populated store write one under the redirected home."""
    from lib import env

    home = tmp_path_factory.mktemp("home")
    config_dir = _isolate_environment(monkeypatch, home)
    monkeypatch.chdir(home)
    monkeypatch.setattr(env, "_config_override", str(config_dir))
    monkeypatch.setattr(env, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(env, "CONFIG_FILE", config_dir / ".env")


@pytest.fixture(autouse=True)
def _no_arctic_network():
    """Default Arctic transport collaborators to no-ops outside adapter tests."""
    with mock.patch("lib.reddit_arctic.fetch_scores", return_value={}), \
         mock.patch("lib.reddit_arctic.fetch_listings", return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _no_ambient_grok_cli():
    """Default the grok CLI to absent so no test resolves it from the developer's
    own machine. grok is a new X-chain backend whose availability is a plain
    filesystem check, so a machine with it installed and signed in would
    otherwise silently change chain resolution in every existing X test.
    Tests that exercise grok stub these themselves (test_grok_x,
    test_backend_descriptors._x_env) and override this by patching inside the
    test body."""
    # Stub the input (PATH resolution), not the logic: has_stored_auth and
    # _is_available_uncached then both resolve absent on their own, leaving the
    # module's real control flow intact for tests that exercise it.
    with mock.patch("lib.grok_x.binary_path", return_value=None):
        yield


@pytest.fixture(autouse=True)
def _reset_probe_caches():
    """The doctor stack memoizes probe results in module-level dicts (safe for
    the one-shot CLI process, wrong across tests). Clear them around every test
    so a probe cached by one test can never leak into another."""
    from lib import grok_x, health, xurl_x

    health.clear_dependency_probe_cache()
    xurl_x.clear_availability_cache()
    grok_x.clear_availability_cache()
    yield
    health.clear_dependency_probe_cache()
    xurl_x.clear_availability_cache()
    grok_x.clear_availability_cache()

@pytest.fixture(autouse=True)
def _reset_reddit_keyless_memo():
    """The run memos (Reddit keyless and ScrapeCreators, source fetches) live for one command; tests are their own commands."""
    from lib import fetch_memo as _fetch_memo
    from lib import http as _http
    from lib import reddit as _reddit

    _http.reset_reddit_keyless_memo()
    _reddit.reset_scrapecreators_memo()
    _fetch_memo.reset()
    yield
    _http.reset_reddit_keyless_memo()
    _reddit.reset_scrapecreators_memo()
    _fetch_memo.reset()


@pytest.fixture(autouse=True)
def _no_ambient_agent_host(monkeypatch):
    """Strip agent-host markers so the LAW 7 host-plan gate stays off.

    Contributors mostly run this suite from inside Claude Code or Codex, whose
    shells export CLAUDECODE / CODEX_* markers that CI never has. Tests that
    exercise the gate set these themselves.
    """
    from lib import env as _env

    for name in (
        *_env.AGENT_HOST_ENV_VARS,
        _env.HOST_AGENT_VAR,
        _env.X_HOST_VAR,
        _env.ALLOW_ENGINE_PLAN_VAR,
    ):
        monkeypatch.delenv(name, raising=False)
    yield
