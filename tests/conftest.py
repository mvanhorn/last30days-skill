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
def _reset_probe_caches():
    """The doctor stack memoizes probe results in module-level dicts (safe for
    the one-shot CLI process, wrong across tests). Clear them around every test
    so a probe cached by one test can never leak into another."""
    from lib import health, xurl_x

    health.clear_dependency_probe_cache()
    xurl_x.clear_availability_cache()
    yield
    health.clear_dependency_probe_cache()
    xurl_x.clear_availability_cache()
