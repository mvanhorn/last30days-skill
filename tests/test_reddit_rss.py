"""Tests for scripts/lib/reddit_rss.py — keyless Reddit RSS discovery."""

import threading
from pathlib import Path
from unittest import mock
from urllib.parse import urlsplit

import pytest

from lib import reddit_rss

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "reddit_search_rss_sample.xml"


def _feed_text():
    return FIXTURE.read_text(encoding="utf-8")


def _atom_feed(*urls):
    entries = "".join(
        f"<entry><title>Post {i}</title>"
        f"<link href=\"{url}\" />"
        "<updated>2026-05-20T00:00:00+00:00</updated></entry>"
        for i, url in enumerate(urls, 1)
    )
    return (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        f"{entries}</feed>"
    )


@pytest.fixture(autouse=True)
def reset_reddit_rss_state():
    reddit_rss._reset_state()
    yield
    reddit_rss._reset_state()


class TestParseFeed:
    """_parse_feed turns Atom entries into normalized post dicts."""

    def test_parses_entries(self):
        posts = reddit_rss._parse_feed(_feed_text(), query="lifelock")
        assert len(posts) == 5
        for p in posts:
            assert p["title"]
            assert "/comments/" in p["url"]
            assert p["url"].startswith("https://www.reddit.com/")

    def test_normalized_shape_matches_scrapecreators(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        required = {"id", "title", "url", "score", "num_comments", "subreddit",
                    "created_utc", "author", "selftext", "date",
                    "engagement", "relevance", "why_relevant", "metadata"}
        assert required.issubset(set(post.keys()))
        assert set(post["engagement"].keys()) == {"score", "num_comments", "upvote_ratio"}
        assert post["why_relevant"] == "Reddit RSS"

    def test_score_is_placeholder_zero(self):
        # RSS carries no engagement score; it is backfilled during enrichment.
        for p in reddit_rss._parse_feed(_feed_text(), query="x"):
            assert p["score"] == 0
            assert p["engagement"]["score"] == 0

    def test_subreddit_derivation(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        assert post["subreddit"] == "Rakuten"

    def test_date_parsed_to_iso(self):
        post = reddit_rss._parse_feed(_feed_text(), query="x")[0]
        assert post["date"] and len(post["date"]) == 10  # YYYY-MM-DD
        assert isinstance(post["created_utc"], float)

    def test_author_strips_u_prefix(self):
        authors = [p["author"] for p in reddit_rss._parse_feed(_feed_text(), query="x")]
        assert all(not a.startswith("/u/") and not a.startswith("u/") for a in authors)

    def test_empty_and_malformed_feed_never_raises(self):
        assert reddit_rss._parse_feed("", query="x") == []
        assert reddit_rss._parse_feed("<not xml", query="x") == []
        assert reddit_rss._parse_feed("<feed></feed>", query="x") == []

    def test_entry_without_comments_link_skipped(self):
        feed = (
            '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
            '<title>Subreddit itself</title>'
            '<link href="https://www.reddit.com/r/test/" />'
            '<updated>2026-05-20T00:00:00+00:00</updated></entry></feed>'
        )
        assert reddit_rss._parse_feed(feed, query="x") == []


class TestSearchRss:
    """search_rss fans out, dedupes, assigns IDs, and honors depth limits."""

    def test_dedupe_and_ids(self):
        # Same feed returned for every URL -> deduped to 5 unique posts.
        with mock.patch.object(
            reddit_rss.http, "reddit_keyless_get_text", return_value=_feed_text()
        ):
            posts = reddit_rss.search_rss("lifelock", depth="default",
                                          subreddits=["Rakuten", "ConsumerAdvice"])
        urls = [p["url"] for p in posts]
        assert len(urls) == len(set(urls))  # no duplicates
        assert [p["id"] for p in posts] == [f"R{i+1}" for i in range(len(posts))]

    def test_depth_limit_quick(self):
        with mock.patch.object(
            reddit_rss.http, "reddit_keyless_get_text", return_value=_feed_text()
        ):
            posts = reddit_rss.search_rss("lifelock", depth="quick")
        assert len(posts) <= reddit_rss.DEPTH_LIMITS["quick"]

    def test_all_feeds_fail_returns_empty(self):
        with mock.patch.object(
            reddit_rss.http, "reddit_keyless_get_text", return_value=None
        ):
            posts = reddit_rss.search_rss("lifelock", subreddits=["Rakuten"])
        assert posts == []

    def test_builds_keyless_rss_urls(self):
        urls = reddit_rss._build_urls("life lock", "default", ["Rakuten"])
        assert any("search.rss?q=life+lock" in u and "/r/" not in u.split("?")[0] for u in urls)
        assert any("/r/Rakuten/search.rss" in u and "restrict_sr=on" in u for u in urls)
        assert any("/r/Rakuten/top.rss" in u for u in urls)
        assert all(".json" not in u for u in urls)  # never the dead endpoint

    def test_old_host_fallback_is_serialized_after_primary_403(self):
        primary = "https://www.reddit.com/r/Rakuten/top.rss?t=month"
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            if urlsplit(url).hostname == reddit_rss.WWW_REDDIT_HOST:
                return None  # 403 -> failure
            return _feed_text()

        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch):
            posts = reddit_rss._fetch_feed(primary, "lifelock")

        assert posts
        assert [urlsplit(url).hostname for url in calls] == [
            reddit_rss.WWW_REDDIT_HOST,
            reddit_rss.OLD_REDDIT_HOST,
        ]
        assert all(post["url"].startswith("https://www.reddit.com/") for post in posts)

    def test_concurrent_fallback_callers_share_old_host_result(self, monkeypatch):
        primary = "https://www.reddit.com/r/Rakuten/top.rss?t=month"
        calls = []
        calls_lock = threading.Lock()
        old_waiting = threading.Event()
        start = threading.Barrier(2)

        class _SignalingLock:
            def __init__(self):
                self._lock = threading.Lock()

            def __enter__(self):
                if self._lock.acquire(False):
                    return self
                old_waiting.set()
                self._lock.acquire()
                return self

            def __exit__(self, exc_type, exc, tb):
                self._lock.release()

        old_lock = _SignalingLock()
        monkeypatch.setitem(reddit_rss._HOST_LOCKS, reddit_rss.OLD_REDDIT_HOST, old_lock)
        results = [None, None]
        errors = []

        def fetch(url, **kwargs):
            host = urlsplit(url).hostname
            with calls_lock:
                calls.append(host)
            if host == reddit_rss.WWW_REDDIT_HOST:
                return None  # 403
            old_waiting.wait(timeout=5)
            return _feed_text()

        def run(index):
            try:
                start.wait(timeout=5)
                results[index] = reddit_rss._fetch_feed(primary, "x")
            except BaseException as exc:  # report worker failures in the main thread
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(index,), daemon=True)
            for index in range(2)
        ]
        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=8)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert old_waiting.is_set()
        assert results[0] and results[0] == results[1]
        assert calls == [reddit_rss.WWW_REDDIT_HOST, reddit_rss.OLD_REDDIT_HOST]

    def test_malformed_primary_falls_back_to_old_host(self):
        primary = "https://www.reddit.com/r/Rakuten/search.rss?q=x"
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            if urlsplit(url).hostname == reddit_rss.WWW_REDDIT_HOST:
                return "<html>anti-bot page</html>"
            return _feed_text()

        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch):
            posts = reddit_rss._fetch_feed(primary, "x")

        assert posts
        assert [urlsplit(url).hostname for url in calls] == [
            reddit_rss.WWW_REDDIT_HOST,
            reddit_rss.OLD_REDDIT_HOST,
        ]

    def test_old_links_are_canonicalized_before_dedupe(self):
        old_url = "https://old.reddit.com/r/test/comments/abc123/post/"
        www_url = "https://www.reddit.com/r/test/comments/abc123/post/"
        parsed = reddit_rss._parse_feed(_atom_feed(old_url), query="x")
        assert parsed[0]["url"] == www_url

        old_post = {"url": old_url, "id": "", "title": "old"}
        www_post = {"url": www_url, "id": "", "title": "www"}

        def fake_fetch(url, query):
            if "/r/test/search.rss" in url:
                return [old_post]
            if "/r/test/top.rss" in url:
                return [www_post]
            return []

        with mock.patch.object(reddit_rss, "_fetch_feed", side_effect=fake_fetch):
            posts = reddit_rss.search_rss("x", depth="quick", subreddits=["test"])

        assert len(posts) == 1
        assert posts[0]["url"] == www_url

    def test_fresh_cache_avoids_network_and_stale_cache_survives_refresh_failure(self):
        primary = "https://www.reddit.com/r/Rakuten/top.rss?t=month"
        clock = [100.0]
        first = [True]

        def fetch(url, **kwargs):
            if first[0]:
                first[0] = False
                return _feed_text()
            return None  # 503 -> failure

        with mock.patch.object(reddit_rss, "_now", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch) as fetched:
            fresh = reddit_rss._fetch_feed(primary, "x")
            assert fresh
            assert fetched.call_count == 1

            clock[0] += reddit_rss.CACHE_FRESH_TTL + 1
            stale = reddit_rss._fetch_feed(primary, "x")
            assert stale
            assert [p["url"] for p in stale] == [p["url"] for p in fresh]
            # www 503 plus old 503; the old response must be tried serially.
            assert fetched.call_count == 3

            clock[0] += reddit_rss.CACHE_STALE_TTL + 1
            assert reddit_rss._fetch_feed(primary, "x") == []

    def test_cache_has_strict_entry_bound(self, monkeypatch):
        monkeypatch.setattr(reddit_rss, "MAX_CACHE_ENTRIES", 2)
        feed = _feed_text()

        with mock.patch.object(
            reddit_rss.http, "reddit_keyless_get_text", return_value=feed
        ):
            for subreddit in ("one", "two", "three"):
                reddit_rss._fetch_feed(
                    f"https://www.reddit.com/r/{subreddit}/top.rss?t=month", "x"
                )

        assert len(reddit_rss._FEED_CACHE) == 2

    @pytest.mark.parametrize("status", [403, 429])
    def test_403_429_cooldown_retry_after_is_bounded(self, status):
        primary = "https://www.reddit.com/search.rss?q=x"
        clock = [10.0]

        with mock.patch.object(reddit_rss, "_now", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_rss.time, "sleep") as sleep, \
             mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", return_value=None) as fetched:
            assert reddit_rss._fetch_feed(primary, "x") == []
            assert fetched.call_count == 1
            state = reddit_rss._HOST_STATE[reddit_rss.WWW_REDDIT_HOST]
            assert state.cooldown_until <= clock[0] + reddit_rss.HOST_COOLDOWN_MAX
            sleep.assert_not_called()

            # The cooldown suppresses a second request from the same host.
            assert reddit_rss._fetch_feed(primary, "x") == []
            assert fetched.call_count == 1

            clock[0] += reddit_rss.HOST_COOLDOWN_MAX + 1
            assert reddit_rss._fetch_feed(primary, "x") == []
            assert fetched.call_count == 2

    def test_cooldown_is_per_host_and_old_fallback_remains_available(self):
        first = "https://www.reddit.com/r/one/top.rss?t=month"
        second = "https://www.reddit.com/r/two/top.rss?t=month"
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            if urlsplit(url).hostname == reddit_rss.WWW_REDDIT_HOST:
                return None  # 429
            return _feed_text()

        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch):
            assert reddit_rss._fetch_feed(first, "x")
            assert reddit_rss._fetch_feed(second, "x")

        hosts = [urlsplit(url).hostname for url in calls]
        assert hosts.count(reddit_rss.WWW_REDDIT_HOST) == 1
        assert hosts.count(reddit_rss.OLD_REDDIT_HOST) == 2

    def test_parallel_fanout_does_not_amplify_429s_per_host(self):
        calls = []

        def fetch(url, **kwargs):
            calls.append(url)
            return None  # 429

        with mock.patch.object(reddit_rss.http, "reddit_keyless_get_text", side_effect=fetch):
            assert reddit_rss.search_rss(
                "x", depth="quick", subreddits=["one", "two", "three"]
            ) == []

        hosts = [urlsplit(url).hostname for url in calls]
        assert hosts.count(reddit_rss.WWW_REDDIT_HOST) == 1
        assert hosts.count(reddit_rss.OLD_REDDIT_HOST) == 1
        assert len(reddit_rss._HOST_STATE) <= 2
