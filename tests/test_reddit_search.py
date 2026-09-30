"""Keyless Reddit discovery via Reddit's site search fragment (reddit_search).

Fixtures under fixtures/reddit_search_*.html are trimmed live captures of
/svc/shreddit/search/ and /svc/shreddit/r/{sub}/search/, except the challenge
page, which is synthetic (see its provenance line).
"""

import itertools
import json
import re
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import dates, health, http, reddit, reddit_search, schema

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

PAGE1_URL = (
    "https://www.reddit.com/svc/shreddit/search/"
    "?q=ButcherBox&type=posts&t=month&sort=relevance"
)
PAGE1_CURSOR = (
    "eyJjYW5kaWRhdGVzX3JldHVybmVkIjoie1wic2VjdGlvbl8xX3BpcGVsaW5lXzBfZ2xvYmFsX21vZGlmaWVyc1wiOlwiMVwiLFwic2VjdGlvbl8xX3BpcGVsaW5lXzFfbG9jYWxfbW9kaWZpZXJzXCI6XCIyXCIsXCJzZWN0aW9uXzJfcGlwZWxpbmVfMTBfdXh0c191bml0XCI6XCIwXCIsXCJzZWN0aW9uXzJfcGlwZWxpbmVfMTFfcXVlcnlfc3VnZ2VzdGlvbnNcIjpcIjZcIixcInNlY3Rpb25fMl9waXBlbGluZV84X3Bvc3Rfc2VhcmNoXCI6XCI3XCIsXCJzZWN0aW9uXzNfcGlwZWxpbmVfMF9zdWJyZWRkaXRfc2VhcmNoXCI6XCI1XCIsXCJzZWN0aW9uXzNfcGlwZWxpbmVfMV9hdXRob3Jfc2VhcmNoXCI6XCI1XCJ9IiwiZXhwZXJpZW5jZV9zZWxlY3Rpb24iOiJwb3N0X3dpdGhfc2lkZWJhciIsImV4cGVyaWVuY2VfdmVyc2lvbiI6ImRlZmF1bHQiLCJzZWN0aW9uXzJfcGlwZWxpbmVfOF9wb3N0X3NlYXJjaCI6IjcifQ=="
)
PAGE2_URL = PAGE1_URL + "&cursor=" + PAGE1_CURSOR.replace("=", "%3D")

PAGE1_POSTS = [
    # (post id, subreddit, title, date, score, comments)
    ("1w6swt2", "referralcodes", "Looking for a ButcherBox referral code", "2026-09-04", 1, 27),
    ("1wkybur", "TimDillon", "Today’s Ad Read for ButcherBox", "2026-09-19", 144, 26),
    ("1w9s403", "Netherlands", "Best online butchers in the Netherlands?\U0001f969", "2026-09-07", 0, 62),
    ("1wbvi5s", "findareferralcode", "Anyone has a butcher box referral code?", "2026-09-09", 1, 1),
    ("1w7w7k3", "ButcherHero_HooknChew", "Found the free gems", "2026-09-05", 7, 8),
    ("1wmumud", "rawprimal", "What! Butcher’s website", "2026-09-22", 3, 7),
    ("1w4tbqb", "findareferralcode", "Anybody have a referral code or link for Butcher Box?", "2026-09-01", 1, 2),
]
LAST_PAGE_IDS = ["1wtoesn", "1wns3fq"]


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _post_ids(posts):
    return [p["metadata"]["post_id"] for p in posts]


def _synthetic_page(post_ids, cursor=None, sub="example"):
    """A minimal search fragment with the live markup's result-unit shape."""
    units = []
    for pid in post_ids:
        ctx = json.dumps({
            "action_info": {"type": "post"},
            "post": {"id": f"t3_{pid}", "title": f"Topic post {pid}"},
            "subreddit": {"name": sub},
        }).replace('"', "&quot;")
        units.append(
            f'<search-telemetry-tracker data-faceplate-tracking-context="{ctx}" '
            f'view-events="search/view/post"><h2><a data-testid="post-title" '
            f'href="/r/{sub}/comments/{pid}/topic_post/">Topic post {pid}</a></h2>'
            f'</search-telemetry-tracker>'
            f'<faceplate-timeago ts="2026-09-20T10:00:00.000000+0000"></faceplate-timeago>'
            f'<div data-testid="search-counter-row"><span><faceplate-number number="5" pretty>'
            f'</faceplate-number> votes</span><span><faceplate-number number="2" pretty>'
            f'</faceplate-number> comments</span></div>'
        )
    if cursor:
        units.append(
            f'<faceplate-partial loading="lazy" src="/svc/shreddit/search/?q=x&amp;'
            f'type=posts&amp;cursor={cursor}"></faceplate-partial>'
        )
    return "<html><body>" + "".join(units) + "</body></html>"


class FakeReddit:
    """Stands in for get_text: serves bodies by URL and records every request."""

    def __init__(self, routes=None, default=None):
        self.routes = dict(routes or {})
        self.default = default
        self.requests = []

    def __call__(self, url, **_kwargs):
        self.requests.append(url)
        body = self.routes.get(url, self.default)
        if callable(body):
            return body(url)
        return body


@pytest.fixture
def fake_reddit():
    fake = FakeReddit()
    with mock.patch.object(http, "get_text", side_effect=fake), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
         mock.patch.object(http.time, "sleep"):
        yield fake


def _status(code, reason):
    def _respond(_url):
        http._record_failure(http.HTTPError(f"HTTP {code}: {reason}", code))
        return None
    return _respond


class TestParse:
    def test_page1_yields_post_units_with_dates_scores_and_comments(self):
        posts, _cursor = reddit_search.parse_page(_fixture("reddit_search_page1.html"), "ButcherBox")
        got = [
            (p["metadata"]["post_id"], p["subreddit"], p["title"], p["date"], p["score"], p["num_comments"])
            for p in posts
        ]
        # Seven post units; the page's community links and six "People also
        # search for" suggestions are not posts.
        assert got == PAGE1_POSTS
        first = posts[0]
        assert first["engagement"] == {"score": 1, "num_comments": 27, "upvote_ratio": None}
        assert first["created_utc"] == datetime(2026, 9, 4, 3, 26, 52, 161000, tzinfo=timezone.utc).timestamp()
        assert first["author"] == ""
        assert first["selftext"] == ""
        assert first["why_relevant"] == "Reddit search"
        assert first["relevance"] > 0
        assert posts[4]["relevance"] == 0.0  # "Found the free gems" shares no query token

    def test_url_is_canonical_permalink_matching_post_id(self):
        posts, _ = reddit_search.parse_page(_fixture("reddit_search_page1.html"), "ButcherBox")
        assert posts[1]["url"] == (
            "https://www.reddit.com/r/TimDillon/comments/1wkybur/todays_ad_read_for_butcherbox/"
        )
        for p in posts:
            assert p["url"].startswith(f"https://www.reddit.com/r/{p['subreddit']}/comments/")
            assert p["url"].split("/comments/")[1].split("/")[0] == p["metadata"]["post_id"]

    def test_cursor_read_from_next_page_partial(self):
        _, cursor = reddit_search.parse_page(_fixture("reddit_search_page1.html"))
        assert cursor == PAGE1_CURSOR
        _, last_cursor = reddit_search.parse_page(_fixture("reddit_search_last_page.html"))
        assert last_cursor is None


class TestUrls:
    def test_global_and_cursor_urls(self):
        assert reddit_search.search_url("ButcherBox", "month") == PAGE1_URL
        assert reddit_search.search_url("ButcherBox", "month", cursor=PAGE1_CURSOR) == PAGE2_URL

    def test_per_sub_url_uses_sub_search_path(self):
        assert reddit_search.search_url("Tubi TV", "week", subreddit="r/TubiTV") == (
            "https://www.reddit.com/svc/shreddit/r/TubiTV/search/"
            "?q=Tubi+TV&type=posts&t=week&sort=relevance"
        )

    @pytest.mark.parametrize("days,expected", [(7, "month"), (90, "year")])
    def test_window_sets_time_filter_like_scrapecreators_path(self, fake_reddit, days, expected):
        fake_reddit.default = _fixture("reddit_search_no_results.html")
        from_date, to_date = dates.get_date_range(days)
        reddit_search.search("Tubi", depth="quick", from_date=from_date, to_date=to_date)
        sent = parse_qs(urlsplit(fake_reddit.requests[0]).query)["t"]
        assert sent == [expected]
        assert sent == [reddit._window_to_time_filter(from_date, to_date)]


class TestPaging:
    def test_follows_cursor_to_last_page_and_stops(self, fake_reddit):
        fake_reddit.routes = {
            PAGE1_URL: _fixture("reddit_search_page1.html"),
            PAGE2_URL: _fixture("reddit_search_last_page.html"),
        }
        posts = reddit_search.search("ButcherBox", depth="default")
        assert fake_reddit.requests == [PAGE1_URL, PAGE2_URL]
        assert _post_ids(posts) == [row[0] for row in PAGE1_POSTS] + LAST_PAGE_IDS
        assert [p["id"] for p in posts] == [f"R{i}" for i in range(1, 10)]

    def test_stops_on_repeated_cursor(self, fake_reddit):
        fake_reddit.routes = {
            PAGE1_URL: _fixture("reddit_search_page1.html"),
            # New posts, but the page points back at the cursor already used.
            PAGE2_URL: _synthetic_page(["aaa111", "bbb222"], cursor=PAGE1_CURSOR),
        }
        posts = reddit_search.search("ButcherBox", depth="deep")
        assert fake_reddit.requests == [PAGE1_URL, PAGE2_URL]
        assert _post_ids(posts)[-2:] == ["aaa111", "bbb222"]

    def test_stops_on_page_with_no_new_ids(self, fake_reddit):
        repeat = _fixture("reddit_search_page1.html").replace(PAGE1_CURSOR.replace("=", "%3D"), "freshcursor")
        fake_reddit.routes = {
            PAGE1_URL: _fixture("reddit_search_page1.html"),
            PAGE2_URL: repeat,
        }
        posts = reddit_search.search("ButcherBox", depth="deep")
        assert fake_reddit.requests == [PAGE1_URL, PAGE2_URL]
        assert _post_ids(posts) == [row[0] for row in PAGE1_POSTS]

    @pytest.mark.parametrize("depth,pages", [("quick", 2), ("default", 4), ("deep", 8)])
    def test_global_page_cap_by_depth(self, fake_reddit, depth, pages):
        served = []

        def endless(_url):
            n = len(served)
            served.append(n)
            return _synthetic_page([f"p{n}x{i}" for i in range(7)], cursor=f"c{n}")

        fake_reddit.default = endless
        posts = reddit_search.search("topic", depth=depth)
        assert len(fake_reddit.requests) == pages
        assert len(set(fake_reddit.requests)) == pages
        # Existing depth post caps still apply on top of the page cap.
        assert len(posts) == {"quick": 10, "default": 25, "deep": 50}[depth]

    def test_failed_second_page_keeps_first_page_and_records_failure(self, fake_reddit):
        fake_reddit.routes = {
            PAGE1_URL: _fixture("reddit_search_page1.html"),
            PAGE2_URL: _status(500, "Internal Server Error"),
        }
        with http.capture_failures() as failures:
            posts = reddit_search.search("ButcherBox", depth="default")
        assert _post_ids(posts) == [row[0] for row in PAGE1_POSTS]
        assert [f.status_code for f in failures] == [500]


class TestTargetedSubs:
    def test_targeted_sub_gets_one_page_of_sub_search(self, fake_reddit):
        sub_url = (
            "https://www.reddit.com/svc/shreddit/r/TubiTV/search/"
            "?q=Tubi&type=posts&t=month&sort=relevance"
        )
        global_url = (
            "https://www.reddit.com/svc/shreddit/search/"
            "?q=Tubi&type=posts&t=month&sort=relevance"
        )
        fake_reddit.routes = {
            global_url: _fixture("reddit_search_no_results.html"),
            sub_url: _synthetic_page(["s1", "s2"], cursor="more", sub="TubiTV"),
        }
        posts = reddit_search.search("Tubi", depth="deep", subreddits=["r/TubiTV"])
        assert sorted(fake_reddit.requests) == sorted([global_url, sub_url])
        assert _post_ids(posts) == ["s1", "s2"]
        assert {p["subreddit"] for p in posts} == {"TubiTV"}

    def test_targeted_sub_posts_survive_the_depth_cap(self, fake_reddit):
        sub_url = (
            "https://www.reddit.com/svc/shreddit/r/TubiTV/search/"
            "?q=Tubi&type=posts&t=month&sort=relevance"
        )
        pages = itertools.count()

        def endless(_url):
            n = next(pages)
            return _synthetic_page([f"g{n}x{i}" for i in range(7)], cursor=f"c{n}")

        fake_reddit.default = endless
        fake_reddit.routes = {sub_url: _synthetic_page(["s1", "s2"], sub="TubiTV")}
        posts = reddit_search.search("Tubi", depth="quick", subreddits=["TubiTV"])
        assert len(posts) == 10
        assert {"s1", "s2"} <= set(_post_ids(posts))


class TestOutcomes:
    def test_challenge_page_records_failure_and_is_not_memoized(self, fake_reddit):
        fake_reddit.default = _fixture("reddit_search_challenge.html")
        with http.capture_failures() as failures:
            first = reddit_search.search("ButcherBox", depth="quick")
            second = reddit_search.search("ButcherBox", depth="quick")
        assert first == [] and second == []
        assert fake_reddit.requests == [PAGE1_URL, PAGE1_URL]
        assert len(failures) == 2
        assert all(f.outcome_state == health.SCHEMA_DRIFT for f in failures)

    @pytest.mark.parametrize("drift", [
        # Tracking context attribute gone entirely.
        lambda body: re.sub(r'\sdata-faceplate-tracking-context="[^"]*"', "", body),
        # Attribute kept, JSON shape changed (action_info renamed).
        lambda body: body.replace("&quot;action_info&quot;", "&quot;action&quot;"),
    ], ids=["stripped", "reshaped"])
    def test_results_marker_with_no_parseable_units_is_schema_drift(self, fake_reddit, drift):
        body = drift(_fixture("reddit_search_page1.html"))
        assert reddit_search.RESULTS_MARKER in body
        assert reddit_search.parse_page(body)[0] == []
        assert reddit_search.unrecognized_body(body)
        fake_reddit.default = body
        with http.capture_failures() as failures:
            first = reddit_search.search("ButcherBox", depth="quick")
            second = reddit_search.search("ButcherBox", depth="quick")
        assert first == [] and second == []
        # Not memoized: the second run fetches again.
        assert fake_reddit.requests == [PAGE1_URL, PAGE1_URL]
        assert len(failures) == 2
        assert all(f.outcome_state == health.SCHEMA_DRIFT for f in failures)

    def test_recognized_page_is_memoized(self, fake_reddit):
        fake_reddit.routes = {PAGE1_URL: _fixture("reddit_search_no_results.html")}
        reddit_search.search("ButcherBox", depth="quick")
        reddit_search.search("ButcherBox", depth="quick")
        assert fake_reddit.requests == [PAGE1_URL]

    def test_no_results_page_returns_empty_without_failure(self, fake_reddit):
        fake_reddit.default = _fixture("reddit_search_no_results.html")
        with http.capture_failures() as failures:
            posts = reddit_search.search("zxqvbnmplkqqwerty7731", depth="default")
        assert posts == []
        assert failures == []
        assert len(fake_reddit.requests) == 1

    def test_429_then_success_retries_once_through_limiter(self):
        bodies = [None, _fixture("reddit_search_page1.html"), _fixture("reddit_search_last_page.html")]

        def fake_get(*_args, **_kwargs):
            body = bodies.pop(0)
            if body is None:
                http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", 429))
            return body

        with mock.patch.object(http, "get_text", side_effect=fake_get) as gt, \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire") as acq, \
             mock.patch.object(http.time, "sleep"), \
             http.capture_failures() as failures:
            posts = reddit_search.search("ButcherBox", depth="quick")
        assert _post_ids(posts)[:7] == [row[0] for row in PAGE1_POSTS]
        assert [c.args[0] for c in gt.call_args_list] == [PAGE1_URL, PAGE1_URL, PAGE2_URL]
        assert acq.call_count == 3
        assert failures == []

    @mock.patch("lib.http.time.sleep")
    @mock.patch("lib.http.urllib.request.urlopen")
    def test_fanout_429_reaches_pipeline_failure_sink(self, mock_urlopen, _sleep):
        # get_text launders the 429 into None; the sink must survive the
        # ThreadPoolExecutor hop into the search workers (issue #899).
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://www.reddit.com/svc/shreddit/search/", 429, "Too Many Requests", {}, None
        )
        with http.capture_failures() as failures:
            posts = reddit_search.search("test topic", depth="quick", subreddits=["example"])
        assert posts == []
        assert failures[-1].outcome_state == schema.RATE_LIMITED

    def test_unexpected_error_never_raises(self):
        with mock.patch.object(http, "reddit_keyless_get_text_retry_429", side_effect=RuntimeError("boom")):
            assert reddit_search.search("ButcherBox") == []


def test_result_timeout_includes_keyless_wait_allowance(monkeypatch):
    limiter = http.RateLimiter(rate_per_sec=1.0, burst=2)
    monkeypatch.delenv(http.REDDIT_KEYLESS_RATE_ENV, raising=False)
    with mock.patch.object(http, "REDDIT_KEYLESS_LIMITER", limiter):
        pad = http.REDDIT_KEYLESS_CONTENTION_SECONDS
        timeout = reddit_search.SEARCH_TIMEOUT
        assert reddit_search._result_timeout(13) == timeout + 5 + 13.0 + pad
        # A paging stream waits for each of its sequential pages.
        assert reddit_search._result_timeout(13, pages=4) == 4 * (timeout + 5) + 13.0 + pad
