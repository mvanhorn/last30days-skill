"""Run-scoped memo for keyless Reddit GETs.

Subreddit listings, listing RSS feeds, arctic supplements, and shreddit comment
pages are byte-identical across subqueries (the lane is dispatched with the raw
topic every time), so a four-subquery run fetched each of them four times.
"""

import threading
from unittest import mock

import pytest

from lib import http


@pytest.fixture(autouse=True)
def _fresh_memo():
    http.reset_reddit_keyless_memo()
    yield
    http.reset_reddit_keyless_memo()


def test_second_call_for_same_url_is_served_from_memo():
    with mock.patch.object(http, "get_text", return_value="<feed/>") as get_text, \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire") as acquire:
        first = http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/top.rss?t=month")
        second = http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/top.rss?t=month")
    assert first == second == "<feed/>"
    assert get_text.call_count == 1
    assert acquire.call_count == 1, "a memo hit must not spend a limiter token"


def test_failed_fetch_is_not_memoized():
    with mock.patch.object(http, "get_text", side_effect=[None, "<feed/>"]) as get_text, \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        assert http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/hot.rss") is None
        assert http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/hot.rss") == "<feed/>"
    assert get_text.call_count == 2


def test_reset_clears_the_memo():
    with mock.patch.object(http, "get_text", return_value="<feed/>") as get_text, \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/new.rss")
        http.reset_reddit_keyless_memo()
        http.reddit_keyless_get_text("https://www.reddit.com/r/Kanye/new.rss")
    assert get_text.call_count == 2


def test_memo_is_bounded():
    with mock.patch.object(http, "get_text", return_value="<feed/>") as get_text, \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        for i in range(http.REDDIT_KEYLESS_MEMO_MAX + 1):
            http.reddit_keyless_get_text(f"https://www.reddit.com/r/s{i}/top.rss")
        # The very first URL was evicted; the most recent one is still cached.
        http.reddit_keyless_get_text("https://www.reddit.com/r/s0/top.rss")
        http.reddit_keyless_get_text(f"https://www.reddit.com/r/s{http.REDDIT_KEYLESS_MEMO_MAX}/top.rss")
    assert get_text.call_count == http.REDDIT_KEYLESS_MEMO_MAX + 2


def test_concurrent_requesters_share_one_in_flight_fetch():
    release = threading.Event()
    calls = []

    def slow_get_text(url, **kwargs):
        calls.append(url)
        release.wait(timeout=5)
        return "<feed/>"

    results = []
    with mock.patch.object(http, "get_text", side_effect=slow_get_text), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        url = "https://www.reddit.com/svc/shreddit/community-more-posts/top/?name=Kanye&t=month"
        threads = [threading.Thread(target=lambda: results.append(http.reddit_keyless_get_text(url))) for _ in range(4)]
        for t in threads:
            t.start()
        # Give every thread a chance to reach the memo before the owner finishes.
        deadline = threading.Event()
        deadline.wait(timeout=0.2)
        release.set()
        for t in threads:
            t.join(timeout=5)
    assert results == ["<feed/>"] * 4
    assert calls == [url], "four concurrent requesters must share one fetch"


def test_retry_helper_goes_through_the_memo():
    with mock.patch.object(http, "get_text", return_value="<feed/>") as get_text, \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        body, err = http.reddit_keyless_get_text_retry_429("https://www.reddit.com/r/Kanye/top.rss")
        body2, err2 = http.reddit_keyless_get_text_retry_429("https://www.reddit.com/r/Kanye/top.rss")
    assert (body, err) == ("<feed/>", None) == (body2, err2)
    assert get_text.call_count == 1


def test_waiters_do_not_stampede_when_the_owner_fails():
    """If the in-flight owner's fetch fails, the waiters elect one new owner
    and share its fetch instead of each issuing their own."""
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    def get_text(url, **kwargs):
        with lock:
            calls.append(url)
            n = len(calls)
        release.wait(timeout=5)
        return None if n == 1 else "<feed/>"

    results = []
    with mock.patch.object(http, "get_text", side_effect=get_text), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"):
        url = "https://www.reddit.com/r/Kanye/top.rss?t=month"
        threads = [threading.Thread(target=lambda: results.append(http.reddit_keyless_get_text(url))) for _ in range(4)]
        for t in threads:
            t.start()
        threading.Event().wait(timeout=0.2)
        release.set()
        for t in threads:
            t.join(timeout=10)
    assert sorted(results, key=str) == [None, "<feed/>", "<feed/>", "<feed/>"] or results.count("<feed/>") >= 3
    assert len(calls) <= 2, f"expected the owner's fetch plus one retry, got {len(calls)}"
