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


def _comment_row(pid="mjwb1hg", author="u", body="great thread", score=42,
                 created=1783000000, permalink="/r/tea/comments/abc123/x/mjwb1hg/"):
    return {
        "id": pid, "author": author, "body": body, "score": score,
        "created_utc": created, "permalink": permalink, "link_id": "t3_abc123",
    }


class TestFetchPostComments:
    """fetch_post_comments serves a post's top comments from the archive —
    the keyless fallback for hosts where the shreddit comment partials 403."""

    def test_returns_normalized_comments_sorted_desc(self):
        rows = [_comment_row(score=1), _comment_row(score=99), _comment_row(score=42)]
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp(rows)) as g:
            out = reddit_arctic.fetch_post_comments("abc123", limit=10)
        assert [c["score"] for c in out] == [99, 42, 1]
        first = out[0]
        assert first["author"] == "u"
        assert first["body"] == "great thread"
        assert first["excerpt"] == "great thread"
        assert first["date"] == "2026-07-02"
        assert first["url"] == "https://reddit.com/r/tea/comments/abc123/x/mjwb1hg/"
        # link_id uses the t3_ prefix; wide recency slice, ranked client-side
        assert "link_id=t3_abc123" in g.call_args[0][0]
        assert f"limit={reddit_arctic.MAX_COMMENT_ROWS}" in g.call_args[0][0]

    def test_strips_t3_prefix_from_post_id(self):
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp([_comment_row()])) as g:
            reddit_arctic.fetch_post_comments("t3_abc123")
        assert "link_id=t3_abc123" in g.call_args[0][0]

    def test_limit_capped(self):
        rows = [_comment_row(score=i) for i in range(5)]
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp(rows)):
            out = reddit_arctic.fetch_post_comments("abc123", limit=2)
        assert len(out) == 2

    def test_deleted_and_removed_filtered(self):
        rows = [_comment_row(author="[deleted]"), _comment_row(body="[removed]"),
                _comment_row(score=7)]
        with mock.patch.object(reddit_arctic.http, "get", return_value=_resp(rows)):
            out = reddit_arctic.fetch_post_comments("abc123")
        assert len(out) == 1 and out[0]["score"] == 7

    def test_network_error_degrades_to_empty(self):
        with mock.patch.object(reddit_arctic.http, "get", side_effect=Exception("boom")):
            assert reddit_arctic.fetch_post_comments("abc123") == []

    def test_rate_limit_response_degrades_to_empty(self):
        with mock.patch.object(reddit_arctic.http, "get",
                               return_value={"error": "Timeout. Maybe slow down a bit"}):
            assert reddit_arctic.fetch_post_comments("abc123") == []

    def test_empty_post_id_returns_empty(self):
        with mock.patch.object(reddit_arctic.http, "get") as g:
            assert reddit_arctic.fetch_post_comments("") == []
            assert reddit_arctic.fetch_post_comments(None) == []
        g.assert_not_called()
