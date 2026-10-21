"""Tests for reddit_arctic — keyless post-score lookup via arctic-shift (U6)."""

import socket
from unittest import mock
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import reddit_arctic, reddit_keyless


@pytest.fixture(autouse=True)
def _no_arctic_network():
    # Override conftest's stub so this module exercises the real fetch_scores;
    # network is mocked per-test at http.get. Clear the in-run cache each time.
    reddit_arctic._cache.clear()
    yield
    reddit_arctic._cache.clear()


def _resp(rows):
    return {"data": rows}


class TestFetchScores:
    def test_returns_scores_by_id(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([{"id": "abc", "score": 1531, "num_comments": 336}])):
            out = reddit_arctic.fetch_scores(["abc"])
        assert out == {"abc": {"score": 1531, "num_comments": 336}}

    def test_strips_t3_prefix(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([{"id": "t3_xyz", "score": 5, "num_comments": 2}])):
            out = reddit_arctic.fetch_scores(["xyz"])
        assert out["xyz"]["score"] == 5

    def test_rate_limit_response_degrades(self):
        # arctic-shift answers {"error": "...slow down"} with no data list.
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value={"error": "Timeout. Maybe slow down a bit"}):
            out = reddit_arctic.fetch_scores(["abc"])
        assert out == {}

    def test_network_error_degrades(self):
        with mock.patch.object(reddit_arctic.http, "get", side_effect=Exception("boom")):
            out = reddit_arctic.fetch_scores(["abc"])
        assert out == {}

    def test_in_run_cache_avoids_refetch(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([{"id": "abc", "score": 9, "num_comments": 1}])) as g:
            reddit_arctic.fetch_scores(["abc"])
            reddit_arctic.fetch_scores(["abc"])  # served from cache, no 2nd call
        assert g.call_count == 1

    def test_dedupes_into_single_batch(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([{"id": "a", "score": 1, "num_comments": 0},
                                                   {"id": "b", "score": 2, "num_comments": 0}])) as g:
            out = reddit_arctic.fetch_scores(["a", "b", "a", ""])
        assert g.call_count == 1
        assert set(out) == {"a", "b"}


def _listing_row(pid="abc123", title="matcha farm tour", score=406, ncmt=88,
                 created=1783000000, subreddit="tea", permalink="/r/tea/comments/abc123/x/"):
    return {
        "id": pid, "title": title, "score": score, "num_comments": ncmt,
        "created_utc": created, "subreddit": subreddit,
        "permalink": permalink, "author": "u", "selftext": "",
    }


@pytest.fixture
def listing_clock(monkeypatch):
    now = [0.0]

    def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr(reddit_arctic.time, "time", lambda: now[0])
    monkeypatch.setattr(reddit_arctic.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(reddit_arctic.time, "sleep", advance)
    return advance


class TestFetchListings:
    """fetch_listings serves scored subreddit listings from the archive —
    the keyless fallback for hosts where shreddit partials 403."""

    def test_returns_normalized_scored_posts(self):
        with mock.patch.object(
            reddit_arctic.http, "get",
            return_value=_resp([_listing_row()]),
        ) as g:
            out = reddit_arctic.fetch_listings(["tea"], query="matcha")
        assert len(out) == 1
        post = out[0]
        assert post["title"] == "matcha farm tour"
        assert post["score"] == 406
        assert post["engagement"]["score"] == 406
        assert post["num_comments"] == 88
        assert post["subreddit"] == "tea"
        assert post["metadata"]["post_id"] == "abc123"  # t3_ prefix stripped
        assert post["date"] == "2026-07-02"  # created_utc -> YYYY-MM-DD
        assert post["url"].startswith("https://www.reddit.com/r/tea/comments/")
        assert post["why_relevant"] == "Reddit listing (arctic-shift)"
        # one call per subreddit, recent-first, depth-default volume
        assert g.call_count == 1
        assert parse_qs(urlsplit(g.call_args[0][0]).query) == {
            "subreddit": ["tea"], "limit": ["25"], "sort": ["desc"],
        }

    def test_strips_r_prefix_and_skips_all(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([_listing_row(subreddit="tea")])) as g:
            out = reddit_arctic.fetch_listings(["r/tea", "all", ""])
        assert len(out) == 1
        assert g.call_count == 1  # only r/tea fetched; "all"/"" skipped

    def test_processes_all_subreddits_no_cap(self):
        """All requested subreddits are processed (no hard cap)."""
        subs = [f"sub{i}" for i in range(20)]
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([_listing_row()])) as g:
            reddit_arctic.fetch_listings(subs)
        # All 20 should be fetched (no cap).
        assert g.call_count == 20

    def test_depth_controls_volume(self):
        for depth, want in (("quick", 10), ("default", 25), ("deep", 50)):
            with mock.patch.object(reddit_arctic.http, "get",
                                   return_value=_resp([_listing_row()])) as g:
                reddit_arctic.fetch_listings(["tea"], depth=depth)
            assert f"limit={want}" in g.call_args[0][0], depth

    def test_multi_sort_request_increases_limit(self):
        """When multiple sorts are requested, fetch 2x posts to compensate."""
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([_listing_row()])) as g:
            reddit_arctic.fetch_listings(["tea"], depth="default", sorts=["top", "hot", "new"])
        # default depth = 25, with 3 sorts → 25 * 2 = 50
        assert "limit=50" in g.call_args[0][0]

    def test_single_sort_uses_base_limit(self):
        """Single sort uses base limit, no multiplier."""
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([_listing_row()])) as g:
            reddit_arctic.fetch_listings(["tea"], depth="default", sorts=["top"])
        assert "limit=25" in g.call_args[0][0]

    def test_dedupes_by_url(self):
        rows = [_listing_row(), _listing_row()]
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp(rows)):
            out = reddit_arctic.fetch_listings(["tea"])
        assert len(out) == 1

    def test_network_error_degrades_to_empty(self):
        with mock.patch.object(reddit_arctic.http, "get", side_effect=Exception("boom")):
            assert reddit_arctic.fetch_listings(["tea"]) == []

    def test_rate_limit_response_degrades_to_empty(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value={"error": "Timeout. Maybe slow down a bit"}):
            assert reddit_arctic.fetch_listings(["tea"]) == []

    def test_empty_subreddits_returns_empty(self):
        assert reddit_arctic.fetch_listings([]) == []

    def test_removed_author_normalized(self):
        row = _listing_row()
        row["author"] = "[deleted]"
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp([row])):
            out = reddit_arctic.fetch_listings(["tea"])
        assert out[0]["author"] == "[deleted]"

    def test_cache_is_size_bounded(self):
        # The in-run memo never grows past CACHE_MAX; scores are still returned
        # for the current call once the cache is full.
        with mock.patch.object(reddit_arctic, "CACHE_MAX", 1), \
             mock.patch.object(reddit_arctic.http, "get",
                               return_value=_resp([{"id": "a", "score": 1, "num_comments": 0},
                                                   {"id": "b", "score": 2, "num_comments": 0}])):
            out = reddit_arctic.fetch_scores(["a", "b"])
        assert set(out) == {"a", "b"}            # both returned
        assert len(reddit_arctic._cache) <= 1    # cache stayed bounded

    def test_empty_input_makes_no_call(self):
        with mock.patch.object(reddit_arctic.http, "get") as g:
            out = reddit_arctic.fetch_scores([])
        assert out == {}
        g.assert_not_called()

    def test_malformed_rows_skipped(self):
        rows = [{"id": "ok", "score": 7, "num_comments": 3},
                {"id": "", "score": 1},          # no id
                "not-a-dict",                     # junk
                {"id": "bad", "score": "x"}]      # unparseable score
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp(rows)):
            out = reddit_arctic.fetch_scores(["ok", "bad"])
        assert out == {"ok": {"score": 7, "num_comments": 3}}


class TestWindowedFetchListings:
    def test_dns_retries_cannot_spend_other_subreddits_request_time(self, listing_clock):
        requested = []

        def open_request(request, timeout):
            requested.append(parse_qs(urlsplit(request.full_url).query)["subreddit"][0])
            listing_clock(timeout)
            raise URLError(socket.gaierror(socket.EAI_AGAIN, "temporary DNS failure"))

        with mock.patch.object(reddit_arctic.http, "_open_request", side_effect=open_request):
            out = reddit_arctic.fetch_listings(
                ["tea", "coffee", "cocoa"], from_date="2026-09-20", to_date="2026-10-20",
            )

        assert out == []
        assert requested == ["tea", "coffee", "cocoa"]
        assert reddit_arctic.time.monotonic() <= reddit_arctic._LISTING_DEADLINE_SECONDS

    def test_slow_first_subreddit_does_not_starve_other_subreddits(self, listing_clock):
        requested = []

        def get(url, **kwargs):
            sub = parse_qs(urlsplit(url).query)["subreddit"][0]
            requested.append(sub)
            listing_clock(kwargs["timeout"])
            if sub == "tea":
                raise TimeoutError("slow subreddit")
            return _resp([_listing_row(
                pid=sub, subreddit=sub, permalink=f"/r/{sub}/comments/{sub}/x/",
            )])

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get):
            out = reddit_arctic.fetch_listings(
                ["tea", "coffee", "cocoa"], from_date="2026-09-20", to_date="2026-10-20",
            )

        assert requested == ["tea", "coffee", "cocoa"]
        assert [post["subreddit"] for post in out] == ["coffee", "cocoa"]
        assert reddit_arctic.time.time() <= reddit_arctic._LISTING_DEADLINE_SECONDS

    @pytest.mark.parametrize(("start", "end", "boundaries"), [
        ("2026-09-20", "2026-10-20", [
            ("2026-09-20", "2026-09-28"),
            ("2026-09-28", "2026-10-06"),
            ("2026-10-06", "2026-10-14"),
            ("2026-10-14", "2026-10-20"),
        ]),
        ("2026-10-19", "2026-10-20", [("2026-10-19", "2026-10-20")]),
    ])
    def test_slices_cover_requested_boundaries(self, listing_clock, start, end, boundaries):
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp([])) as get:
            reddit_arctic.fetch_listings(["tea"], from_date=start, to_date=end)

        queries = [parse_qs(urlsplit(call.args[0]).query) for call in get.call_args_list]
        assert [(query["after"][0], query["before"][0]) for query in queries] == boundaries
        assert all(query["limit"] == ["100"] and query["sort"] == ["desc"] for query in queries)

    def test_visits_every_subreddit_before_the_next_slice(self, listing_clock):
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp([])) as get:
            reddit_arctic.fetch_listings(
                ["tea", "coffee"], from_date="2026-09-20", to_date="2026-10-20",
            )

        queries = [parse_qs(urlsplit(call.args[0]).query) for call in get.call_args_list]
        assert [query["subreddit"][0] for query in queries] == ["tea", "coffee"] * 4
        assert [query["after"][0] for query in queries] == [
            "2026-09-20", "2026-09-20", "2026-09-28", "2026-09-28",
            "2026-10-06", "2026-10-06", "2026-10-14", "2026-10-14",
        ]

    def test_keeps_highest_scores_across_slices_per_subreddit(self, listing_clock):
        scores = {"tea": iter([500, 1, 300, 2]), "coffee": iter([4, 70, 10, 60])}

        def get(url, **kwargs):
            sub = parse_qs(urlsplit(url).query)["subreddit"][0]
            score = next(scores[sub])
            return _resp([_listing_row(
                pid=f"{sub}{score}", subreddit=sub, score=str(score),
                permalink=f"/r/{sub}/comments/{sub}{score}/x/",
            )])

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get):
            out = reddit_arctic.fetch_listings(
                ["tea", "coffee"], limit=2, from_date="2026-09-20", to_date="2026-10-20",
            )

        assert [(post["subreddit"], post["score"]) for post in out] == [
            ("tea", 500), ("tea", 300), ("coffee", 70), ("coffee", 60),
        ]

    def test_duplicate_slice_rows_do_not_consume_selection_slots(self, listing_clock):
        best = _listing_row(score=100)
        second = _listing_row(pid="second", score=90, permalink="/r/tea/comments/second/x/")
        with mock.patch.object(reddit_arctic.http, "get", side_effect=[
            _resp([best]), _resp([best, second]), _resp([]), _resp([]),
        ]):
            out = reddit_arctic.fetch_listings(
                ["tea"], limit=2, from_date="2026-09-20", to_date="2026-10-20",
            )

        assert [post["metadata"]["post_id"] for post in out] == ["abc123", "second"]

    @pytest.mark.parametrize("failure", [TimeoutError("unreachable"), {"data": None}])
    def test_failed_slice_keeps_other_slices(self, listing_clock, failure):
        later = _listing_row(pid="later", score=900, permalink="/r/tea/comments/later/x/")
        with mock.patch.object(reddit_arctic.http, "get", side_effect=[
            _resp([_listing_row()]), failure, _resp([later]), _resp([]),
        ]) as get:
            out = reddit_arctic.fetch_listings(
                ["tea"], from_date="2026-09-20", to_date="2026-10-20",
            )

        assert get.call_count == 4
        assert [post["metadata"]["post_id"] for post in out] == ["later", "abc123"]

    def test_deadline_keeps_best_posts_from_completed_slices(self, listing_clock):
        scores = iter([10, 90, 70, 1000])
        timeouts = []

        def get(url, **kwargs):
            timeouts.append(kwargs["timeout"])
            listing_clock(kwargs["timeout"])
            score = next(scores)
            return _resp([_listing_row(
                pid=str(score), score=score, permalink=f"/r/tea/comments/{score}/x/",
            )])

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get):
            out = reddit_arctic.fetch_listings(
                ["tea"], limit=2, from_date="2026-09-20", to_date="2026-10-20",
            )

        assert [post["score"] for post in out] == [90, 70]
        assert timeouts == pytest.approx([15, 15, 14.2])
        assert reddit_arctic.time.time() <= reddit_arctic._LISTING_DEADLINE_SECONDS

    def test_pacing_does_not_start_request_after_deadline(self, listing_clock):
        def get(url, **kwargs):
            listing_clock(0.8)
            return _resp([_listing_row()])

        with mock.patch.object(reddit_arctic, "_LISTING_DEADLINE_SECONDS", 1), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=get) as request:
            out = reddit_arctic.fetch_listings(
                ["tea", "coffee"], from_date="2026-09-20", to_date="2026-10-20",
            )

        assert request.call_count == 1
        assert [post["metadata"]["post_id"] for post in out] == ["abc123"]
        assert reddit_arctic.time.time() == pytest.approx(1)

    def test_discovery_forwards_dates_to_dedicated_and_category_listings(self, listing_clock):
        queries = []

        def get(url, **kwargs):
            query = parse_qs(urlsplit(url).query)
            queries.append(query)
            sub = query["subreddit"][0]
            return _resp([_listing_row(
                pid=sub, subreddit=sub, permalink=f"/r/{sub}/comments/{sub}/x/",
            )])

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=get):
            out = reddit_keyless._discover(
                "matcha", "quick", ["tea"], dedicated_subreddits=["matcha"],
                from_date="2026-09-20", to_date="2026-10-20",
            )

        assert [post["subreddit"] for post in out] == ["matcha", "tea"]
        assert out[0]["dedicated"] is True
        for sub in ("matcha", "tea"):
            sub_queries = [query for query in queries if query["subreddit"] == [sub]]
            assert len(sub_queries) == 4
            assert sub_queries[0]["after"] == ["2026-09-20"]
            assert sub_queries[-1]["before"] == ["2026-10-20"]
