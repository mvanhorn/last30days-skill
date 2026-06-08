import urllib.error
import unittest
from unittest.mock import patch, MagicMock

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


def _mock_response(body: str = '{"ok": true}', status: int = 200):
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = body.encode("utf-8")
    resp.status = status
    return resp


class TestParamsEncoding(unittest.TestCase):
    """request() should urlencode the params dict into the URL."""

    def _sent_url(self, mock_urlopen) -> str:
        request_arg = mock_urlopen.call_args[0][0]
        return request_arg.full_url

    @patch("lib.http.urllib.request.urlopen")
    def test_params_appended_to_url(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search", params={"q": "test", "limit": 10})
        sent_url = self._sent_url(mock_urlopen)
        self.assertIn("q=test", sent_url)
        self.assertIn("limit=10", sent_url)

    @patch("lib.http.urllib.request.urlopen")
    def test_params_appended_with_existing_query_string(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search?api_key=secret", params={"q": "test"})
        sent_url = self._sent_url(mock_urlopen)
        self.assertTrue(sent_url.startswith("https://api.example.com/search?api_key=secret&"))
        self.assertIn("q=test", sent_url)

    @patch("lib.http.urllib.request.urlopen")
    def test_none_values_dropped(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search", params={"q": "test", "filter": None})
        sent_url = self._sent_url(mock_urlopen)
        self.assertIn("q=test", sent_url)
        self.assertNotIn("filter", sent_url)

    @patch("lib.http.urllib.request.urlopen")
    def test_empty_params_leaves_url_unchanged(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search", params={})
        sent_url = self._sent_url(mock_urlopen)
        self.assertEqual(sent_url, "https://api.example.com/search")

    @patch("lib.http.urllib.request.urlopen")
    def test_no_params_kwarg_leaves_url_unchanged(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search")
        sent_url = self._sent_url(mock_urlopen)
        self.assertEqual(sent_url, "https://api.example.com/search")

    @patch("lib.http.urllib.request.urlopen")
    def test_int_and_bool_params_stringified(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response()
        http.get("https://api.example.com/search", params={"count": 25, "raw": True})
        sent_url = self._sent_url(mock_urlopen)
        self.assertIn("count=25", sent_url)
        self.assertIn("raw=True", sent_url)


class TestDNSResolutionRetry(unittest.TestCase):
    """DNS resolution failures (gaierror) must retry with exponential backoff.

    Caller-passed `retries` values smaller than MIN_DNS_RETRIES are expanded
    on the first gaierror so a transient resolution failure doesn't wipe a
    request just because the caller passed retries=2.
    """

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_gaierror_retries_up_to_min_dns_retries_even_when_caller_passes_fewer(
        self, mock_sleep, mock_urlopen
    ):
        """Caller passed retries=2; gaierror should still get MIN_DNS_RETRIES attempts."""
        import socket
        err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        mock_urlopen.side_effect = err

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://nonexistent.example", retries=2)

        # Caller passed retries=2, but the budget expanded to MIN_DNS_RETRIES=3.
        self.assertEqual(mock_urlopen.call_count, http.MIN_DNS_RETRIES)

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_gaierror_succeeds_after_transient_failure(self, mock_sleep, mock_urlopen):
        """gaierror on attempt 1, then success — should NOT raise."""
        import socket
        success_response = MagicMock()
        success_response.read.return_value = b'{"ok": true}'
        success_response.status = 200
        success_response.__enter__ = lambda self: self
        success_response.__exit__ = lambda *args: None

        err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        mock_urlopen.side_effect = [err, success_response]

        result = http.request("GET", "http://flaky.example", retries=2)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_gaierror_uses_exponential_backoff(self, mock_sleep, mock_urlopen):
        """Backoff delays for gaierror should be 1s, 2s, 4s — not the linear default."""
        import socket
        err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        mock_urlopen.side_effect = err

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://nonexistent.example", retries=3)

        # Expected sleep calls: 1s (after attempt 1), 2s (after attempt 2).
        # No sleep after the final attempt (the loop exits to raise).
        sleep_delays = [call.args[0] for call in mock_sleep.call_args_list]
        self.assertEqual(sleep_delays, [1, 2])

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_non_dns_urlerror_uses_linear_backoff_not_dns_branch(
        self, mock_sleep, mock_urlopen
    ):
        """A URLError that's NOT a gaierror must NOT expand the retry budget."""
        # ConnectionRefusedError-style URLError reason (not gaierror)
        err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        mock_urlopen.side_effect = err

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://refused.example", retries=2)

        # Caller passed retries=2, and non-DNS URLError doesn't expand it.
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_dns_widening_does_not_leak_into_subsequent_non_dns_urlerror(
        self, mock_sleep, mock_urlopen
    ):
        """Mixed sequence: DNS-then-non-DNS must respect caller's original retries.

        Without the fix, the first gaierror widens effective_retries from 2 to
        MIN_DNS_RETRIES=3, and a subsequent ConnectionRefused on attempt 1
        slips into a third overall attempt — exceeding what the caller asked
        for. Each non-DNS error path must gate on the original `retries`.
        """
        import socket
        dns_err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        conn_err = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        mock_urlopen.side_effect = [dns_err, conn_err, conn_err]  # 3rd would only fire if budget leaked

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://flaky.example", retries=2)

        # Caller asked for at most 2 attempts. DNS widening must not give us a 3rd.
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("lib.http.urllib.request.urlopen")
    @patch("lib.http.time.sleep")
    def test_dns_widening_does_not_leak_into_subsequent_oserror(
        self, mock_sleep, mock_urlopen
    ):
        """Mixed sequence: DNS-then-OSError must respect caller's original retries."""
        import socket
        dns_err = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        mock_urlopen.side_effect = [dns_err, TimeoutError("timed out"), TimeoutError("timed out")]

        with self.assertRaises(http.HTTPError):
            http.request("GET", "http://flaky.example", retries=2)

        self.assertEqual(mock_urlopen.call_count, 2)
