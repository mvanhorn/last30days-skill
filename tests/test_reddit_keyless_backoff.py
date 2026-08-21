"""Tests for the shared keyless-Reddit throttle (U4) and 429 in-lane retry."""

import os
from unittest import mock

from lib import http, reddit_listing, reddit_rss, render, schema


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


class TestRedditKeylessRateKnob:
    def test_default_rate_is_one_per_sec_small_burst(self):
        limiter = http.make_reddit_keyless_limiter(environ={})
        assert limiter.rate == 1.0
        assert limiter.capacity == 2

    def test_env_override(self):
        limiter = http.make_reddit_keyless_limiter(
            environ={http.REDDIT_KEYLESS_RATE_ENV: "0.25"}
        )
        assert limiter.rate == 0.25
        assert limiter.capacity == 2

    def test_invalid_and_nonpositive_fall_back_to_default(self):
        for raw in ("fast", "", "-1", "0", "nan", "inf"):
            limiter = http.make_reddit_keyless_limiter(
                environ={http.REDDIT_KEYLESS_RATE_ENV: raw}
            )
            assert limiter.rate == http.DEFAULT_REDDIT_KEYLESS_RATE, raw

    def test_process_env_syncs_onto_shared_limiter(self, monkeypatch):
        monkeypatch.setattr(http.REDDIT_KEYLESS_LIMITER, "rate", 1.0)
        monkeypatch.setenv(http.REDDIT_KEYLESS_RATE_ENV, "0.5")
        with mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http, "get_text", return_value="ok"):
            http.reddit_keyless_get_text("https://www.reddit.com/x.rss")
        assert http.REDDIT_KEYLESS_LIMITER.rate == 0.5

    def test_env_file_value_is_exported_for_limiter(self, tmp_path, monkeypatch):
        from lib import env

        config_file = tmp_path / ".env"
        config_file.write_text(f"{http.REDDIT_KEYLESS_RATE_ENV}=0.25\n", encoding="utf-8")
        config_file.chmod(0o600)
        monkeypatch.setattr(env, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(env, "CONFIG_FILE", config_file)
        monkeypatch.setenv("LAST30DAYS_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv(http.REDDIT_KEYLESS_RATE_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        with mock.patch.object(env, "_load_keychain", return_value={}), \
             mock.patch.object(env, "_load_pass", return_value={}):
            config = env.get_config()
        assert config[http.REDDIT_KEYLESS_RATE_ENV] == "0.25"
        assert http.parse_reddit_keyless_rate(
            config[http.REDDIT_KEYLESS_RATE_ENV]
        ) == 0.25
        assert os.environ.get(http.REDDIT_KEYLESS_RATE_ENV) == "0.25"


def _record_status(code: int, reason: str) -> None:
    http._record_failure(http.HTTPError(f"HTTP {code}: {reason}", code))


class TestRedditKeyless429Retry:
    def test_429_then_200_recovers_via_single_retry(self):
        bodies = [None, "<feed xmlns='http://www.w3.org/2005/Atom'/>"]

        def fake_get(*_args, **_kwargs):
            val = bodies.pop(0)
            if val is None:
                _record_status(429, "Too Many Requests")
            return val

        with mock.patch.object(http, "get_text", side_effect=fake_get), \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire") as acq, \
             mock.patch.object(http.time, "sleep") as slept, \
             mock.patch.object(http.random, "uniform", return_value=0.0):
            text, err = http.reddit_keyless_get_text_retry_429(
                "https://www.reddit.com/search.rss"
            )
        assert text.startswith("<feed")
        assert err is None
        assert acq.call_count == 2
        slept.assert_called_once()
        assert bodies == []

    def test_429_then_429_records_failure_and_stops(self):
        def fake_get(*_args, **_kwargs):
            _record_status(429, "Too Many Requests")
            return None

        with mock.patch.object(http, "get_text", side_effect=fake_get) as gt, \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http.random, "uniform", return_value=0.0), \
             http.capture_failures() as failures:
            text, err = http.reddit_keyless_get_text_retry_429(
                "https://www.reddit.com/search.rss"
            )
        assert text is None
        assert err is not None and "429" in err
        assert gt.call_count == 2
        assert any(f.status_code == 429 for f in failures)

    def test_non_429_miss_is_not_retried(self):
        def fake_get(*_args, **_kwargs):
            _record_status(403, "Forbidden")
            return None

        with mock.patch.object(http, "get_text", side_effect=fake_get) as gt, \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep") as slept, \
             http.capture_failures() as failures:
            text, err = http.reddit_keyless_get_text_retry_429(
                "https://www.reddit.com/search.rss"
            )
        assert text is None
        assert "403" in (err or "")
        assert gt.call_count == 1
        slept.assert_not_called()
        assert any(f.status_code == 403 for f in failures)

    def test_rss_fetch_feed_recovers_after_one_429(self):
        feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            "<title>Recovered</title>"
            '<link href="https://www.reddit.com/r/test/comments/abc/x/" />'
            "<updated>2026-05-20T00:00:00+00:00</updated></entry></feed>"
        )
        bodies = [None, feed]

        def fake_get(*_args, **_kwargs):
            val = bodies.pop(0)
            if val is None:
                _record_status(429, "Too Many Requests")
            return val

        with mock.patch.object(http, "get_text", side_effect=fake_get), \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep"):
            posts = reddit_rss._fetch_feed(
                "https://www.reddit.com/search.rss", "Recovered"
            )
        assert len(posts) == 1
        assert posts[0]["title"] == "Recovered"

    def test_listing_fetch_recovers_after_one_429(self):
        from pathlib import Path

        html = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "reddit_listing_cards_sample.html"
        ).read_text(encoding="utf-8")
        bodies = [None, html]

        def fake_get(*_args, **_kwargs):
            val = bodies.pop(0)
            if val is None:
                _record_status(429, "Too Many Requests")
            return val

        with mock.patch.object(http, "get_text", side_effect=fake_get), \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep"):
            items, error = reddit_listing._fetch_one_with_status(
                "technology", "hot", "netherlands"
            )
        assert error is None
        assert items
        assert bodies == []

    def test_listing_fetch_records_double_429(self):
        def fake_get(*_args, **_kwargs):
            _record_status(429, "Too Many Requests")
            return None

        with mock.patch.object(http, "get_text", side_effect=fake_get) as gt, \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep"), \
             http.capture_failures() as failures:
            items, error = reddit_listing._fetch_one_with_status(
                "technology", "hot", "x"
            )
        assert items == []
        assert error is not None and "429" in error
        assert gt.call_count == 2
        assert any(f.status_code == 429 for f in failures)


class TestPartialOutcomeWording:
    def test_rate_limited_partial_does_not_read_as_cutoff(self):
        outcome = schema.SourceOutcome(
            source="reddit",
            state=schema.PARTIAL,
            items_returned=8,
            detail="HTTP 429: Too Many Requests",
        )
        text = render._format_outcome(outcome)
        assert "partial after" not in text
        assert "8 items returned" in text
        assert "some requests rate-limited" in text
        assert "HTTP 429" in text

    def test_non_rate_limit_partial_keeps_count_without_429_claim(self):
        outcome = schema.SourceOutcome(
            source="instagram",
            state=schema.PARTIAL,
            items_returned=1,
            detail="HTTP 400: Bad Request",
        )
        text = render._format_outcome(outcome)
        assert "partial after" not in text
        assert "1 item returned" in text
        assert "rate-limited" not in text
        assert "HTTP 400" in text
