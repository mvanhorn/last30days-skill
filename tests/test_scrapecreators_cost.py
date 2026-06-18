"""U3: every successful ScrapeCreators request emits one [cost] marker.

All ScrapeCreators callers (reddit/tiktok/instagram/threads/pinterest/youtube)
funnel through http.request(), so mocking the transport proves coverage at the
single chokepoint.
"""

import io
import re
import unittest
from contextlib import redirect_stderr
from unittest import mock

from lib import http

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


class TestScrapecreatorsSource(unittest.TestCase):
    def test_extracts_source_slug(self):
        self.assertEqual(
            http._scrapecreators_source(
                "https://api.scrapecreators.com/v1/reddit/search?q=x"
            ),
            "reddit",
        )
        self.assertEqual(
            http._scrapecreators_source(
                "https://api.scrapecreators.com/v3/tiktok/profile/videos"
            ),
            "tiktok",
        )

    def test_non_sc_url_falls_back(self):
        self.assertEqual(
            http._scrapecreators_source("https://example.com/api"), "scrapecreators"
        )


class TestRequestCost(unittest.TestCase):
    def _run(self, url):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with mock.patch("urllib.request.urlopen", return_value=_FakeResponse('{"ok":1}')):
                http.request("GET", url)
        return _COST_LINE_RE.findall(buf.getvalue())

    def test_scrapecreators_call_emits_one_marker(self):
        markers = self._run("https://api.scrapecreators.com/v1/reddit/search?q=ai")
        self.assertEqual(len(markers), 1)
        self.assertIn("provider=scrapecreators", markers[0])
        self.assertIn("model=reddit", markers[0])

    def test_non_scrapecreators_call_emits_nothing(self):
        markers = self._run("https://www.reddit.com/r/test.json")
        self.assertEqual(markers, [])


if __name__ == "__main__":
    unittest.main()
