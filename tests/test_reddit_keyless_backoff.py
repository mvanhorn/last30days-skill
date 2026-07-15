"""Tests for the shared keyless-Reddit throttle (U4)."""

from unittest import mock

from lib import http, reddit_rss


class TestRateLimiter:
    def test_burst_does_not_sleep(self):
        # A full bucket lets `burst` calls through immediately.
        limiter = http.RateLimiter(rate_per_sec=5.0, burst=3)
        with mock.patch.object(http.time, "monotonic", return_value=100.0), \
             mock.patch.object(http.time, "sleep") as slept:
            limiter.acquire()
            limiter.acquire()
            limiter.acquire()
        slept.assert_not_called()

    def test_sleeps_when_bucket_empty(self):
        # burst=1: first call passes, second (same instant) must wait ~1/rate.
        limiter = http.RateLimiter(rate_per_sec=2.0, burst=1)
        times = iter([100.0, 100.0, 100.0, 100.5])
        with mock.patch.object(http.time, "monotonic", side_effect=lambda: next(times)), \
             mock.patch.object(http.time, "sleep") as slept:
            limiter.acquire()  # consumes the one token
            limiter.acquire()  # bucket empty -> sleep, then refilled token consumed
        slept.assert_called()
        waited = slept.call_args.args[0]
        assert abs(waited - 0.5) < 1e-6  # (1 token deficit) / 2 per sec

    def test_refill_over_time_avoids_sleep(self):
        limiter = http.RateLimiter(rate_per_sec=2.0, burst=1)
        # Second call 1s later: bucket refilled (2/s * 1s capped at burst=1) -> no sleep.
        times = iter([100.0, 101.0])
        with mock.patch.object(http.time, "monotonic", side_effect=lambda: next(times)), \
             mock.patch.object(http.time, "sleep") as slept:
            limiter.acquire()
            limiter.acquire()
        slept.assert_not_called()


class TestRedditKeylessGetText:
    def test_acquires_limiter_then_delegates(self):
        with mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire") as acq, \
             mock.patch.object(http, "get_text", return_value="body") as gt:
            out = http.reddit_keyless_get_text("https://www.reddit.com/x.rss", accept="application/atom+xml")
        assert out == "body"
        acq.assert_called_once()
        gt.assert_called_once()

    def test_reddit_rss_routes_through_throttle(self):
        # The RSS tier must use the throttled helper, not raw get_text.
        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", return_value=None) as throttled:
            reddit_rss.search_rss("test query")
        assert throttled.called
