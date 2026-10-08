"""Per-future result timeouts must cover the keyless bucket's queue.

At 1 req/s, a batch of thirteen keyless URLs on four workers queues about
thirteen seconds of token waits before the last fetch even starts, and four
subquery streams share the same bucket. A fixed 20-second future timeout then
expires while the fetch is still waiting for a token, and the result is
dropped with an empty future-failure message (seen on the 2026-08-31 smoke
run). The site search lane's timeout is covered in test_reddit_search.py.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from lib import http, reddit_listing


def test_limiter_reports_waiting_threads(monkeypatch):
    sleeping = threading.Event()
    release = threading.Event()
    now = [0.0]

    def blocked_sleep(delay):
        sleeping.set()
        assert release.wait(timeout=5), "queued worker was not released"
        now[0] += delay

    monkeypatch.setattr(http, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=blocked_sleep))
    monkeypatch.delenv(http.REDDIT_KEYLESS_RATE_ENV, raising=False)
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=1)
    monkeypatch.setattr(http, "REDDIT_KEYLESS_LIMITER", limiter)
    assert limiter.waiting == 0
    limiter.acquire()  # drains the single token
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(limiter.acquire)
        try:
            assert sleeping.wait(timeout=5), "worker did not reach token wait"
            assert not worker.done()
            assert limiter.waiting == 1
            assert http.reddit_keyless_wait_allowance(2) == 3.0 + http.REDDIT_KEYLESS_CONTENTION_SECONDS
        finally:
            release.set()
        worker.result(timeout=5)
    assert limiter.waiting == 0
    assert http.reddit_keyless_wait_allowance(2) == 2.0 + http.REDDIT_KEYLESS_CONTENTION_SECONDS


def test_wait_allowance_scales_with_batch_and_queue(monkeypatch):
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    monkeypatch.delenv(http.REDDIT_KEYLESS_RATE_ENV, raising=False)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        assert http.reddit_keyless_wait_allowance(13) == 13.0 + pad
        limiter._waiting = 5
        assert http.reddit_keyless_wait_allowance(13) == 18.0 + pad
        # The allowance syncs the configured rate before computing.
        monkeypatch.setenv(http.REDDIT_KEYLESS_RATE_ENV, "2")
        assert http.reddit_keyless_wait_allowance(13) == 9.0 + pad


def test_listing_result_timeout_includes_the_allowance(monkeypatch):
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    monkeypatch.delenv(http.REDDIT_KEYLESS_RATE_ENV, raising=False)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        assert reddit_listing._result_timeout(20) == reddit_listing.LISTING_TIMEOUT + 5 + 20.0 + pad


def test_allowance_reflects_a_process_env_rate_override(monkeypatch):
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        monkeypatch.setenv(http.REDDIT_KEYLESS_RATE_ENV, "0.5")
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        assert http.reddit_keyless_wait_allowance(10) == 20.0 + pad
