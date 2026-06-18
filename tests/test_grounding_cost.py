"""U4: web-search backends and Perplexity each emit a [cost] marker.

Brave/Exa/Serper/Parallel funnel through http.request() and are billed at that
chokepoint by host. Perplexity shares openrouter.ai with the reasoning LLMs, so
it is billed at its own call site, model-keyed (deep research vs sonar-pro).
"""

import io
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import http, perplexity

_COST_LINE_RE = re.compile(r"^\[cost\]\s+(.*)$", re.MULTILINE)


class _FakeResponse:
    status = 200

    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestPaidProviderFor(unittest.TestCase):
    def test_each_backend_host_maps(self):
        cases = {
            "https://api.search.brave.com/res/v1/web/search?q=x": "brave",
            "https://api.exa.ai/search": "exa",
            "https://google.serper.dev/search": "serper",
            "https://api.parallel.ai/v1/search": "parallel",
            "https://api.scrapecreators.com/v1/reddit/search": "scrapecreators",
        }
        for url, provider in cases.items():
            self.assertEqual(http._paid_provider_for(url), provider, url)

    def test_openrouter_is_not_billed_at_chokepoint(self):
        self.assertIsNone(
            http._paid_provider_for("https://openrouter.ai/api/v1/chat/completions")
        )


class TestSearchBackendCost(unittest.TestCase):
    def _markers_for(self, url):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with mock.patch("urllib.request.urlopen", return_value=_FakeResponse('{"web":{"results":[]}}')):
                http.request("GET", url)
        return _COST_LINE_RE.findall(buf.getvalue())

    def test_exa_emits_one_marker(self):
        markers = self._markers_for("https://api.exa.ai/search")
        self.assertEqual(len(markers), 1)
        self.assertIn("provider=exa", markers[0])

    def test_serper_emits_one_marker(self):
        markers = self._markers_for("https://google.serper.dev/search")
        self.assertEqual(len(markers), 1)
        self.assertIn("provider=serper", markers[0])


class TestPerplexityCost(unittest.TestCase):
    def _markers(self, deep):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with mock.patch.object(
                perplexity.http, "post",
                return_value={"choices": [{"message": {"content": "x"}}]},
            ):
                perplexity.search(
                    "ai", ("2026-06-01", "2026-06-17"),
                    {"OPENROUTER_API_KEY": "k"}, deep=deep,
                )
        return _COST_LINE_RE.findall(buf.getvalue())

    def test_sonar_pro_marker(self):
        markers = self._markers(deep=False)
        self.assertTrue(any("model=sonar-pro" in m and "provider=perplexity" in m for m in markers))

    def test_deep_research_marker_priced_high(self):
        markers = [m for m in self._markers(deep=True) if "provider=perplexity" in m]
        self.assertEqual(len(markers), 1)
        self.assertIn("model=sonar-deep-research", markers[0])
        cost = float(re.search(r"cost_usd=([0-9.]+)", markers[0]).group(1))
        self.assertGreater(cost, 0.5)


if __name__ == "__main__":
    unittest.main()
