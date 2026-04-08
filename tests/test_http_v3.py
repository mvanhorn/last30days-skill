import sys
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lib import http


class Test429RetryLimit(unittest.TestCase):
    """429 retries must be capped at max_429_retries to avoid wasting latency."""

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")  # Don't actually sleep in tests
    def test_429_retries_limited_to_2_by_default(self, mock_sleep, mock_urlopen):
        """With default max_429_retries=2, should attempt 2 times then raise."""
        error = urllib.error.HTTPError(
            "http://example.com", 429, "Too Many Requests", {}, None
        )
        mock_urlopen.side_effect = error

        with self.assertRaises(http.HTTPError) as ctx:
            http.request("GET", "http://example.com", retries=5)

        self.assertEqual(ctx.exception.status_code, 429)
        # Should be called exactly 2 times (initial + 1 retry), not 5
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_non_429_errors_still_use_full_retries(self, mock_sleep, mock_urlopen):
        """500 errors should still retry up to the full retries count."""
        error = urllib.error.HTTPError(
            "http://example.com", 500, "Internal Server Error", {}, None
        )
        mock_urlopen.side_effect = error

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://example.com", retries=3)

        self.assertEqual(mock_urlopen.call_count, 3)
