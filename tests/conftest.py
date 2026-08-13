import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "last30days" / "scripts"))


@pytest.fixture(autouse=True)
def _no_arctic_network():
    """Default the arctic-shift score lookup to a no-op so the suite never makes
    a real network call. test_reddit_arctic overrides this fixture (same name) to
    exercise the real lookup with http.get mocked."""
    with mock.patch("lib.reddit_arctic.fetch_scores", return_value={}):
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
