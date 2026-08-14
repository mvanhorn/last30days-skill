"""Tests for reddit_arctic — keyless post-score lookup via arctic-shift (U6)."""

from unittest import mock

import pytest

from lib import reddit_arctic


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
        assert "subreddit=tea" in g.call_args[0][0]
        assert "limit=25" in g.call_args[0][0]

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
