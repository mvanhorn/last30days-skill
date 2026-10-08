import os
import sys
from pathlib import Path
from unittest import mock

import pytest

pytest_plugins = ["tests.network_guard"]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))


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
def _no_ambient_xurl_store(tmp_path_factory, monkeypatch):
    """Point HOME at an empty directory so no test resolves the developer's own
    xurl token store, for the same reason as the grok fixture above. xurl is an
    X-chain backend whose auth evidence is a plain filesystem check, so anyone
    who has ever run xurl has a populated ``~/.xurl`` and silently resolves
    xurl as a configured backend in tests that assume none is present.

    Observed with a populated store: three test_grok_surfacing doctor cases
    report ``status == "ok"`` instead of ``"unconfigured"``, and
    test_backend_descriptors.TestGetXSourceStatusGrokPin
    .test_unpinned_with_store_does_not_return_grok_source fails
    ``assert status["source"] is None`` with ``'xurl'``.

    Stubbing ``xurl_x.has_stored_auth`` is not enough, and three of those tests
    already do it: ``backends._xurl_finding`` calls ``stored_auth_status()``
    directly, so the real store is still read. Stubbing ``token_store_path``
    instead breaks the tests that assert on it.

    Stub the input (where HOME points), not the logic: ``token_store_path``,
    ``stored_auth_status`` and ``has_stored_auth`` all run their real code and
    resolve absent on their own, and a test asserting
    ``Path.home() / ".xurl" / "auth.yml"`` still agrees with them because both
    sides move together. Tests that want a populated store write one under the
    redirected home, as they already do."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
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
    """The Reddit run memos (keyless and ScrapeCreators) live for one command; tests are their own commands."""
    from lib import http as _http
    from lib import reddit as _reddit

    _http.reset_reddit_keyless_memo()
    _reddit.reset_scrapecreators_memo()
    yield
    _http.reset_reddit_keyless_memo()
    _reddit.reset_scrapecreators_memo()


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


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Strip credential-shaped variables from the process environment.

    ``env.get_config()`` reads API keys and cookies straight from os.environ, so
    a developer machine with real credentials exported resolves config the CI
    box never would (upstream CI is a clean Linux runner). Tests that need a
    credential set it themselves; nothing should depend on the ambient one.
    """
    suffixes = ("_API_KEY", "_TOKEN", "_KEY", "_SECRET", "_PASSWORD", "_COOKIE")
    exact = {"AUTH_TOKEN", "CT0", "SESSION_ID"}
    for name in list(os.environ):
        if name in exact or name.endswith(suffixes):
            monkeypatch.delenv(name, raising=False)
    yield
