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
