"""Reddit answers an anonymous 429 with x-ratelimit-reset and no Retry-After.

Reading only Retry-After sends the caller into exponential backoff (3s, 5s, 9s),
every step of which is shorter than the ~42s Reddit actually requires, so each
retry re-429s and the source is reported dead when it was only early.
"""

from lib import http


class TestRetryDelayFromHeaders:
    def test_prefers_retry_after_when_present(self):
        assert http.retry_delay_from_headers({"Retry-After": "17"}, 3.0) == 17.0

    def test_falls_back_to_x_ratelimit_reset(self):
        # The header Reddit actually sends on search/RSS 429s.
        assert http.retry_delay_from_headers({"x-ratelimit-reset": "42"}, 3.0) == 42.0

    def test_retry_after_wins_over_ratelimit_reset(self):
        headers = {"Retry-After": "5", "x-ratelimit-reset": "42"}
        assert http.retry_delay_from_headers(headers, 3.0) == 5.0

    def test_real_reddit_429_headers(self):
        # Captured verbatim from an anonymous GET to
        # https://www.reddit.com/search.rss?q=... on 2026-08-23.
        headers = {
            "x-ratelimit-used": "1",
            "x-ratelimit-remaining": "0.0",
            "x-ratelimit-reset": "42",
            "server-timing": 'reddit-ct;desc="dn=FT,p=LON,cs=MISS"',
        }
        # Must not fall through to the 3s fallback -- that is the bug.
        assert http.retry_delay_from_headers(headers, 3.0) == 42.0

    def test_missing_headers_use_fallback(self):
        assert http.retry_delay_from_headers({}, 7.5) == 7.5
        assert http.retry_delay_from_headers(None, 7.5) == 7.5

    def test_unparseable_values_use_fallback(self):
        # Retry-After may be an HTTP-date rather than a delta-seconds integer.
        assert http.retry_delay_from_headers({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, 4.0) == 4.0
        assert http.retry_delay_from_headers({"x-ratelimit-reset": ""}, 4.0) == 4.0

    def test_non_positive_values_use_fallback(self):
        # A spent bucket sometimes reports 0; sleeping 0 just re-429s immediately.
        assert http.retry_delay_from_headers({"x-ratelimit-reset": "0"}, 4.0) == 4.0
        assert http.retry_delay_from_headers({"Retry-After": "-1"}, 4.0) == 4.0

    def test_skips_unparseable_header_and_reads_the_next(self):
        headers = {"Retry-After": "soon", "x-ratelimit-reset": "42"}
        assert http.retry_delay_from_headers(headers, 3.0) == 42.0
