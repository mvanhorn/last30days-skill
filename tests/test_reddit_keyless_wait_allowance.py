"""Per-future result timeouts must cover the keyless bucket's queue.

At 1 req/s, a batch of thirteen feed URLs on four workers queues about
thirteen seconds of token waits before the last fetch even starts, and four
subquery streams share the same bucket. A fixed 20-second future timeout then
expires while the fetch is still waiting for a token, and the feed is dropped
(`[RedditRSS] feed future failed:` with an empty message on the 2026-08-31
smoke run).
"""

import threading
from unittest import mock

from lib import http, reddit_listing, reddit_rss


def test_limiter_reports_waiting_threads():
    limiter = http.RateLimiter(rate_per_sec=1000.0, burst=1)
    assert limiter.waiting == 0
    limiter.acquire()  # drains the single token
    started = threading.Event()

    def _wait():
        started.set()
        limiter.acquire()

    t = threading.Thread(target=_wait)
    t.start()
    started.wait(timeout=1)
    # The waiter is inside acquire() until a token refills.
    deadline = threading.Event()
    deadline.wait(timeout=0.01)
    t.join(timeout=2)
    assert limiter.waiting == 0


def test_wait_allowance_scales_with_batch_and_queue():
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        assert http.reddit_keyless_wait_allowance(13) == 13.0 + pad
        limiter._waiting = 5
        assert http.reddit_keyless_wait_allowance(13) == 18.0 + pad
        limiter.rate = 2.0
        assert http.reddit_keyless_wait_allowance(13) == 9.0 + pad


def test_rss_and_listing_result_timeouts_include_the_allowance():
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        assert reddit_rss._result_timeout(13) == reddit_rss.FEED_TIMEOUT + 5 + 13.0 + pad
        assert reddit_listing._result_timeout(20) == reddit_listing.LISTING_TIMEOUT + 5 + 20.0 + pad
