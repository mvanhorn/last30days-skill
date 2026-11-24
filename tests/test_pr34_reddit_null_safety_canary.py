"""Regression tests for PR 34: Reddit null-safety and canary outage reporting.

Covers:
- HIGH-118: Non-dict JSON payloads and non-list/non-dict nested structures across
  all Arctic Shift functions in `lib/reddit_arctic.py`.
- HIGH-120: ScrapeCreators null/non-list/non-dict payloads and score/field
  coercion in `lib/reddit.py` (and `lib/reddit_enrich.py:enrich_reddit_item_sc`).
- HIGH-132: Null, non-dict, non-list, non-string, bool, NaN, Inf, and huge-int
  inputs in `lib/reddit_enrich.py` (`parse_thread_data`, `get_top_comments`,
  `stored_top_comments`, `extract_comment_insights`) and via
  `reddit_session.thread_post`.
- HIGH-135: `reddit_canary.check_hydration` fails the canary when Arctic Shift
  hydrates 0 of N discovered threads (ingestion stall) while still passing
  hydrated threads with genuinely empty comment trees.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import reddit_canary as canary
from lib import (
    env,
    health,
    http,
    pipeline,
    reddit,
    reddit_arctic,
    reddit_enrich,
    reddit_health,
    reddit_hydrate,
    reddit_session,
    schema,
)
from lib.reddit_refs import ARCTIC, ARCTIC_PENDING, UNKNOWN, ThreadRef, canonical_thread

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
BRAVE_PAGE = (FIXTURES / "brave_reddit_serp.html").read_text(encoding="utf-8")
THREAD_SAMPLE = json.loads((FIXTURES / "reddit_session_thread.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 15, 13, 30, tzinfo=timezone.utc)
ARCTIC_KNOWN_OK = json.dumps({"data": [
    {
        "id": pid,
        "title": f"Known thread {pid}",
        "subreddit": "test",
        "score": 10,
        "num_comments": 2,
        "created_utc": 1740800000,
        "permalink": f"/r/test/comments/{pid}/x/",
    }
    for pid in canary.KNOWN_THREAD_IDS
]})

NON_DICT_OR_BAD_DATA_PAYLOADS = [
    None,
    [],
    ["unexpected"],
    "error string",
    123,
    True,
    {"data": None},
    {"data": "not-a-list"},
    {"data": 42},
    {"data": {"nested": 1}},
]


@pytest.fixture(autouse=True)
def _clean_arctic_and_sc_state():
    reddit_arctic.reset_memo()
    reddit.reset_scrapecreators_memo()
    reddit_health.reset_run_state()
    yield
    reddit_arctic.reset_memo()
    reddit.reset_scrapecreators_memo()
    reddit_health.reset_run_state()


@pytest.fixture(autouse=True)
def _no_arctic_network():
    """Override conftest stub so Arctic Shift functions execute directly."""
    yield


# ---------------------------------------------------------------------------
# HIGH-118: Arctic Shift non-dict / non-list / hostile-row null safety
# ---------------------------------------------------------------------------


class TestHigh118ArcticShiftNullSafety:
    @pytest.mark.parametrize("payload", NON_DICT_OR_BAD_DATA_PAYLOADS)
    def test_search_once_does_not_raise_or_memoize_bad_payloads(self, payload):
        url = f"{reddit_arctic.SEARCH_API}?subreddit=python&limit=5&sort=desc"
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload) as get:
            first = reddit_arctic._search_once(url, deadline=reddit_arctic.time.monotonic() + 30)
            second = reddit_arctic._search_once(url, deadline=reddit_arctic.time.monotonic() + 30)
        assert first == payload
        assert second == payload
        assert get.call_count == 2  # invalid payloads must never be memoized

    @pytest.mark.parametrize("payload", NON_DICT_OR_BAD_DATA_PAYLOADS)
    def test_fetch_scores_never_raises_on_malformed_payload(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            assert reddit_arctic.fetch_scores(["abc123"]) == {}

    def test_fetch_scores_skips_hostile_rows_and_keeps_valid_ones(self):
        rows = [
            None,
            "not-a-dict",
            123,
            {"id": "bool_score", "score": True, "num_comments": 1},
            {"id": "nan_score", "score": float("nan"), "num_comments": 1},
            {"id": "inf_score", "score": float("inf"), "num_comments": 1},
            {"id": "bad_str", "score": "nope", "num_comments": 1},
            {"id": "t3_good1", "score": 19, "num_comments": 4},
            {"id": "good2", "score": 0, "num_comments": None},
            {"id": "good3", "score": "42", "num_comments": "7"},
        ]
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": rows}):
            out = reddit_arctic.fetch_scores(
                ["bool_score", "nan_score", "inf_score", "bad_str", "good1", "good2", "good3"],
            )
        assert out == {
            "good1": {"score": 19, "num_comments": 4},
            "good2": {"score": 0, "num_comments": 0},
            "good3": {"score": 42, "num_comments": 7},
        }

    @pytest.mark.parametrize("payload", NON_DICT_OR_BAD_DATA_PAYLOADS)
    def test_fetch_listings_never_raises_on_malformed_payload(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            assert reddit_arctic.fetch_listings(["python"]) == []

    def test_fetch_listings_handles_non_dict_rows_and_hostile_fields(self):
        rows = [
            None,
            "bad-row",
            {
                "id": "p1",
                "permalink": 12345,  # non-string permalink -> empty url -> skipped
                "title": None,
                "score": float("inf"),
            },
            {
                "id": "t3_p2",
                "permalink": "/r/python/comments/p2/ok/",
                "title": 999,
                "score": True,
                "num_comments": float("nan"),
                "created_utc": 10**20,
                "author": None,
                "subreddit": 42,
                "selftext": True,
            },
            {
                "id": "t3_p3",
                "permalink": "/r/python/comments/p3/good/",
                "title": "Valid Python thread",
                "score": "55",
                "num_comments": 8,
                "created_utc": 1783000000,
                "author": "alice",
                "subreddit": "python",
                "selftext": None,
            },
        ]
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": rows}):
            out = reddit_arctic.fetch_listings(["python"], query="python")
        assert [p["metadata"]["post_id"] for p in out] == ["p2", "p3"]
        p2, p3 = out
        assert p2["score"] == 0
        assert p2["num_comments"] == 0
        assert p2["date"] is None
        assert p2["author"] == "[deleted]"
        assert p2["subreddit"] == ""
        assert p2["selftext"] == ""
        assert p3["score"] == 55
        assert p3["date"] == "2026-07-02"
        assert p3["selftext"] == ""

    @pytest.mark.parametrize("payload", [
        None,
        [],
        ["err"],
        "slow down",  # non-dict string must not crash .get("error")
        123,
        True,
        {"data": None},
        {"data": "bad"},
        {"error": 500},
        {"error": None},
    ])
    def test_fetch_posts_by_ids_never_raises_on_non_dict_or_bad_data(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            out, state = reddit_arctic.fetch_posts_by_ids(["abc123"])
        assert out == {}
        assert state == "down"

    @pytest.mark.parametrize("payload", [
        {"error": "Timeout. Maybe slow down a bit"},
        {"error": {"message": "please slow down"}},
    ])
    def test_fetch_posts_by_ids_detects_slow_down_error_dict_safely(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            out, state = reddit_arctic.fetch_posts_by_ids(["abc123"])
        assert out == {}
        assert state == "throttled"

    @pytest.mark.parametrize("payload", NON_DICT_OR_BAD_DATA_PAYLOADS)
    def test_fetch_comment_tree_never_raises_on_malformed_payload(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            assert reddit_arctic.fetch_comment_tree("abc123") is None

    @pytest.mark.parametrize("payload", NON_DICT_OR_BAD_DATA_PAYLOADS)
    def test_fetch_post_comments_never_raises_on_malformed_payload(self, payload):
        with mock.patch.object(reddit_arctic.http, "get", return_value=payload):
            assert reddit_arctic.fetch_post_comments("abc123") == []

    def test_comment_tree_and_post_comments_survive_malformed_replies_and_fields(self):
        tree_payload = {
            "data": [
                None,
                "junk",
                {"kind": "t1", "data": None},
                {
                    "kind": "t1",
                    "data": {
                        "id": "t1_c1",
                        "author": None,
                        "body": "should be skipped because author defaults to deleted",
                        "score": 100,
                        "replies": {"data": "not-a-dict"},
                    },
                },
                {
                    "kind": "t1",
                    "data": {
                        "id": "t1_c2",
                        "author": "bob",
                        "body": 12345,
                        "score": float("inf"),
                        "depth": True,
                        "parent_id": "t3_abc123",
                        "permalink": 999,
                        "created_utc": 10**20,
                        "replies": {"data": {"children": "not-a-list"}},
                    },
                },
                {
                    "kind": "t1",
                    "data": {
                        "id": "t1_c3",
                        "author": "carol",
                        "body": "Solid benchmark result.",
                        "score": "27",
                        "depth": 0,
                        "permalink": "/r/test/comments/abc123/x/c3/",
                        "created_utc": True,
                        "replies": {
                            "data": {
                                "children": [
                                    None,
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "id": "t1_c4",
                                            "author": "dave",
                                            "body": "Nested reply.",
                                            "score": True,
                                            "depth": float("nan"),
                                            "parent_id": "t1_c3",
                                            "permalink": "/r/test/comments/abc123/x/c4/",
                                            "created_utc": 1783000000,
                                        },
                                    },
                                ],
                            },
                        },
                    },
                },
            ],
        }
        with mock.patch.object(reddit_arctic.http, "get", return_value=tree_payload):
            tree_comments = reddit_arctic.fetch_comment_tree("abc123")
            reddit_arctic.reset_memo()
            post_comments = reddit_arctic.fetch_post_comments("abc123")

        assert tree_comments is not None
        assert [c["author"] for c in tree_comments] == ["bob", "carol", "dave"]
        assert tree_comments[0]["score"] == 0
        assert tree_comments[0]["depth"] == 0
        assert tree_comments[0]["body"] == "12345"
        assert tree_comments[0]["url"] == ""
        assert tree_comments[0]["date"] is None
        assert tree_comments[1]["score"] == 27
        assert tree_comments[1]["date"] is None
        assert tree_comments[2]["score"] == 0
        assert tree_comments[2]["depth"] == 1  # parent_id t1_c3 when depth is NaN
        assert tree_comments[2]["date"] == "2026-07-02"

        assert [c["author"] for c in post_comments] == ["carol", "bob", "dave"]
        assert [c["score"] for c in post_comments] == [27, 0, 0]
        assert post_comments[1]["depth"] == 0  # parent_id t3_
        assert post_comments[2]["depth"] == 1  # parent_id t1_

    @pytest.mark.parametrize("raw_depth,parent_id,expected", [
        (True, "t1_abc", 1),
        (False, "t1_abc", 1),
        ("", "t1_abc", 1),
        ("   ", "t1_abc", 1),
        (None, "t1_abc", 1),
        (float("nan"), "t1_abc", 1),
        (float("inf"), "t1_abc", 1),
        (10**400, "t1_abc", 1),
        ("bad", "t1_abc", 1),
        (True, "t3_post", 0),
        ("", "t3_post", 0),
        ("   ", "t3_post", 0),
        (0, "t1_abc", 0),
        (2, "t3_post", 2),
        ("3", "t3_post", 3),
    ])
    def test_comment_tree_and_post_comments_infer_nested_depth_from_parent_id(
        self, raw_depth, parent_id, expected,
    ):
        tree_payload = {
            "data": [
                {
                    "kind": "t1",
                    "data": {
                        "id": "t1_nested",
                        "author": "alice",
                        "body": "Nested reply body.",
                        "score": 11,
                        "depth": raw_depth,
                        "parent_id": parent_id,
                        "permalink": "/r/test/comments/abc123/x/nested/",
                        "created_utc": 1783000000,
                    },
                },
            ],
        }
        with mock.patch.object(reddit_arctic.http, "get", return_value=tree_payload):
            tree_comments = reddit_arctic.fetch_comment_tree("abc123")
            reddit_arctic.reset_memo()
            post_comments = reddit_arctic.fetch_post_comments("abc123")
        assert tree_comments is not None and len(tree_comments) == 1
        assert len(post_comments) == 1
        assert tree_comments[0]["depth"] == expected
        assert post_comments[0]["depth"] == expected

    def test_huge_int_created_utc_and_score_do_not_overflow_downstream_hydrate(self):
        post_row_huge_cnt = {
            "id": "huge01",
            "title": "Huge int Arctic post",
            "permalink": "/r/python/comments/huge01/huge/",
            "subreddit": "python",
            "score": 10**400,
            "num_comments": 10**400,
            "created_utc": 10**400,
            "author": "alice",
        }
        post_row_with_comments = {
            "id": "huge02",
            "title": "Huge int Arctic post with comments",
            "permalink": "/r/python/comments/huge02/huge/",
            "subreddit": "python",
            "score": 10**400,
            "num_comments": 5,
            "created_utc": 10**400,
            "author": "alice",
        }
        comment_tree = {
            "data": [
                {
                    "kind": "t1",
                    "data": {
                        "id": "t1_chuge",
                        "author": "bob",
                        "body": "Comment with overflowing int fields.",
                        "score": 10**400,
                        "depth": 0,
                        "permalink": "/r/python/comments/huge02/huge/chuge/",
                        "created_utc": 10**400,
                    },
                },
            ],
        }
        ref1 = canonical_thread("https://www.reddit.com/r/python/comments/huge01/huge/")
        ref2 = canonical_thread("https://www.reddit.com/r/python/comments/huge02/huge/")
        assert ref1 is not None and ref2 is not None
        ref1.provenance.add("web")
        ref2.provenance.add("web")

        def fake_get(url, **_kwargs):
            if reddit_arctic.COMMENTS_TREE_API in url:
                return comment_tree
            return {"data": [post_row_huge_cnt, post_row_with_comments]}

        with mock.patch.object(reddit_arctic.http, "get", side_effect=fake_get):
            scores = reddit_arctic.fetch_scores(["huge01"])
            assert scores == {}  # 10**400 score rejected by _strict_int

            hydration = reddit_hydrate.hydrate([ref1, ref2], topic="python", depth="quick", comments=True)

        assert hydration.arctic_state == "ok"
        by_id = {it["metadata"]["post_id"]: it for it in hydration.items}
        assert set(by_id) == {"huge01", "huge02"}
        assert by_id["huge01"]["score"] == 0
        assert by_id["huge01"]["num_comments"] == 0
        assert by_id["huge01"]["created_utc"] is None
        assert by_id["huge01"]["date"] is None
        assert reddit_hydrate._discussion(by_id["huge01"]) == 0.0

        assert by_id["huge02"]["score"] == 0
        assert by_id["huge02"]["num_comments"] == 5
        assert by_id["huge02"]["created_utc"] is None
        assert reddit_hydrate._discussion(by_id["huge02"]) == 5.0
        assert len(by_id["huge02"]["top_comments"]) == 1
        assert by_id["huge02"]["top_comments"][0]["score"] == 0
        assert by_id["huge02"]["top_comments"][0]["created_utc"] is None
        assert by_id["huge02"]["top_comments"][0]["date"] is None


# ---------------------------------------------------------------------------
# HIGH-120: ScrapeCreators null/non-list/non-dict payloads and score coercion
# ---------------------------------------------------------------------------


class TestHigh120ScrapeCreatorsNullSafety:
    @pytest.mark.parametrize("payload", [
        None,
        [],
        ["oops"],
        "error",
        123,
        True,
        {"posts": None},
        {"posts": "bad"},
        {"posts": 42},
        {"posts": {"nested": 1}},
        {"data": None},
        {"data": "bad"},
        {"posts": None, "data": None},
    ])
    def test_global_and_subreddit_search_coerce_malformed_payloads_to_empty(self, payload):
        with mock.patch.object(reddit.http, "get", return_value=payload):
            with http.capture_failures() as global_failures:
                assert reddit._global_search("python", "dummy-token") == []
            with http.capture_failures() as sub_failures:
                assert reddit._subreddit_search("python", "python", "dummy-token") == []
        assert len(global_failures) == 1
        assert global_failures[0].outcome_state == health.SCHEMA_DRIFT
        assert len(sub_failures) == 1
        assert sub_failures[0].outcome_state == health.SCHEMA_DRIFT

    @pytest.mark.parametrize("payload", [
        {"posts": []},
        {"success": True, "posts": []},
        {"data": []},
        {"posts": None, "data": []},
        {"posts": None, "data": [{"id": "a"}]},
    ])
    def test_valid_empty_or_fallback_payloads_do_not_flag_schema_drift(self, payload):
        with mock.patch.object(reddit.http, "get", return_value=payload):
            with http.capture_failures() as failures:
                reddit._global_search("python", "dummy-token")
                reddit._subreddit_search("python", "python", "dummy-token")
        assert failures == []

    def test_partial_malformed_payload_keeps_valid_rows_and_flags_schema_drift(self):
        with mock.patch.object(
            reddit.http, "get", return_value={"posts": [{"id": "good"}, "bad", None]},
        ):
            with http.capture_failures() as failures:
                got = reddit._global_search("python", "dummy-token")
        assert got == [{"id": "good"}]
        assert len(failures) == 1
        assert failures[0].outcome_state == health.SCHEMA_DRIFT

        with mock.patch.object(
            reddit.http, "get", return_value={"posts": "oops", "data": [{"id": "from_data"}]},
        ):
            with http.capture_failures() as fallback_failures:
                got = reddit._global_search("python", "dummy-token")
        assert got == [{"id": "from_data"}]
        assert len(fallback_failures) == 1
        assert fallback_failures[0].outcome_state == health.SCHEMA_DRIFT

    def test_pinned_scrapecreators_schema_drift_not_hidden_by_public_fallback(self):
        cfg = {
            "SCRAPECREATORS_API_KEY": "dummy-key",
            env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
        }
        subquery = schema.SubQuery(
            label="t", search_query="python", ranking_query="python", sources=["reddit"],
        )
        runtime = schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
        )
        public_item = {
            "url": "https://www.reddit.com/r/python/comments/pub01/t/",
            "title": "python fallback thread",
            "engagement": {"score": 40, "num_comments": 5},
        }

        # Case 1: pinned SC returns {"posts": null} and public fallback returns items ->
        # fallback items survive, AND schema-drift is surfaced as the primary failure.
        with mock.patch("lib.http.get", return_value={"posts": None}), \
             mock.patch("lib.reddit_public.search_reddit_public", return_value=[public_item]):
            items, artifact = pipeline._retrieve_stream(
                topic="python",
                subquery=subquery,
                source="reddit",
                config=cfg,
                depth="quick",
                date_range=("2026-10-16", "2026-11-15"),
                runtime=runtime,
                mock=False,
            )
        assert items == [public_item]
        outcome = artifact.get("_source_outcome") or {}
        assert outcome.get("state") == health.SCHEMA_DRIFT
        assert "Reddit primary failed" in (outcome.get("detail") or "")

        # Case 2: pinned SC returns {"posts": null} and public fallback is empty ->
        # fails with SourceRunError(SCHEMA_DRIFT), never silent empty no-results.
        reddit.reset_scrapecreators_memo()
        with mock.patch("lib.http.get", return_value={"posts": None}), \
             mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
            with pytest.raises(pipeline.SourceRunError) as exc_info:
                pipeline._retrieve_stream(
                    topic="python",
                    subquery=subquery,
                    source="reddit",
                    config=cfg,
                    depth="quick",
                    date_range=("2026-10-16", "2026-11-15"),
                    runtime=runtime,
                    mock=False,
                )
        assert exc_info.value.outcome_state == health.SCHEMA_DRIFT

        # Case 3: pinned SC returns valid empty {"posts": []} and public fallback is empty ->
        # clean empty result with no schema-drift error.
        reddit.reset_scrapecreators_memo()
        with mock.patch("lib.http.get", return_value={"posts": []}), \
             mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
            items, artifact = pipeline._retrieve_stream(
                topic="python",
                subquery=subquery,
                source="reddit",
                config=cfg,
                depth="quick",
                date_range=("2026-10-16", "2026-11-15"),
                runtime=runtime,
                mock=False,
            )
        assert items == []
        assert not artifact.get("_source_outcome")

    @pytest.mark.parametrize("payload", [
        None,
        [],
        ["oops"],
        "error",
        123,
        True,
        {"comments": None},
        {"comments": "bad"},
        {"comments": 99},
        {"data": None},
        {"data": "bad"},
        {"comments": None, "data": None},
    ])
    def test_fetch_post_comments_coerces_malformed_payloads_to_empty(self, payload):
        with mock.patch.object(reddit.http, "get", return_value=payload):
            assert reddit.fetch_post_comments("https://www.reddit.com/r/test/comments/abc/", "dummy-token") == []

    def test_extract_payloads_filter_non_dicts_and_fallback_only_when_primary_not_list(self):
        with mock.patch.object(
            reddit.http, "get", return_value={"posts": None, "data": [{"id": "a"}, None, "bad", 7]},
        ):
            assert reddit._global_search("python", "dummy-token") == [{"id": "a"}]

        with mock.patch.object(
            reddit.http, "get", return_value={"posts": [], "data": [{"id": "should_not_use"}]},
        ):
            assert reddit._global_search("python", "dummy-token") == []

        with mock.patch.object(
            reddit.http, "get", return_value={"comments": [None, {"body": "real"}, "bad", 1]},
        ):
            assert reddit.fetch_post_comments("https://www.reddit.com/r/test/comments/abc/", "dummy-token") == [
                {"body": "real"},
            ]

    def test_search_reddit_survives_null_non_list_and_non_dict_entries(self):
        good_post = {
            "id": "t3_abc1",
            "title": "Python 3.14 benchmarks",
            "permalink": "/r/python/comments/abc1/benchmarks/",
            "subreddit": "python",
            "ups": 0,
            "score": 99,  # ups=0 must win over score=99
            "num_comments": None,
            "created_utc": 1794268800,
            "selftext": None,
        }
        hostile_post = {
            "id": "t3_abc2",
            "title": "Python release notes",
            "permalink": None,
            "url": 12345,
            "subreddit": None,
            "ups": True,
            "score": float("nan"),
            "votes": "14",
            "num_comments": "bad",
            "created_utc": 10**20,
            "selftext": True,
        }
        with mock.patch.object(reddit, "_subreddit_search", side_effect=[None, "bad"]), \
             mock.patch.object(reddit, "_global_search", return_value=[None, "junk", good_post, hostile_post]):
            res = reddit.search_reddit(
                "Python",
                "2026-10-16",
                "2026-11-15",
                depth="quick",
                token="dummy-token",
                subreddits=["python"],
            )
        items = res["items"]
        assert len(items) == 2
        by_rid = {it["reddit_id"]: it for it in items}
        assert by_rid["abc1"]["engagement"]["score"] == 0
        assert by_rid["abc1"]["engagement"]["num_comments"] == 0
        assert by_rid["abc1"]["selftext"] == ""
        assert by_rid["abc2"]["engagement"]["score"] == 14
        assert by_rid["abc2"]["engagement"]["num_comments"] == 0
        assert by_rid["abc2"]["subreddit"] == ""
        assert by_rid["abc2"]["url"] == ""
        assert by_rid["abc2"]["date"] is None

    def test_enrich_with_comments_coerces_scores_and_survives_malformed_comments(self):
        items = [
            {
                "id": "R1",
                "url": "https://www.reddit.com/r/test/comments/abc1/post/",
                "engagement": {"score": None, "num_comments": "12"},
            },
            {
                "id": "R2",
                "url": "https://www.reddit.com/r/test/comments/abc2/post/",
                "engagement": None,
            },
        ]
        mixed_comments = [
            None,
            "not-a-dict",
            123,
            {"body": "both null scores", "ups": None, "score": None, "author": "null_user"},
            {"body": "zero ups must beat score 5", "ups": 0, "score": 5, "author": "zero_ups"},
            {"body": "fallback to score 5", "ups": None, "score": 5, "author": "fallback_score"},
            {"body": "bool ups must not be 1", "ups": True, "score": None, "author": "bool_ups"},
            {"body": "nan ups", "ups": float("nan"), "author": "nan_ups"},
            {"body": "inf ups", "ups": float("inf"), "author": "inf_ups"},
            {"body": "string int score", "ups": "25", "author": "str_int"},
            {"body": "bad ups falls back to score", "ups": "oops", "score": 12, "author": "bad_ups_fallback"},
            {"body": "negative score", "ups": -3, "author": "neg_user"},
            {"body": 12345, "ups": 1, "author": None, "permalink": 999, "created_utc": 10**20},
        ]

        def fake_fetch(url, token, **_kwargs):
            if "abc1" in url:
                return mixed_comments
            return "not-a-list"

        with mock.patch.object(reddit, "fetch_post_comments", side_effect=fake_fetch):
            out = reddit.enrich_with_comments(items, token="dummy-token", depth="quick", budget_seconds=10)

        enriched = {it["id"]: it for it in out}
        top = enriched["R1"]["top_comments"]
        assert len(top) == 10
        assert [c["score"] for c in top] == [25, 12, 5, 1, 0, 0, 0, 0, 0, -3]
        by_author = {c["author"]: c for c in top}
        assert by_author["zero_ups"]["score"] == 0
        assert by_author["fallback_score"]["score"] == 5
        assert by_author["bool_ups"]["score"] == 0
        assert by_author["nan_ups"]["score"] == 0
        assert by_author["inf_ups"]["score"] == 0
        assert by_author["str_int"]["score"] == 25
        assert by_author["bad_ups_fallback"]["score"] == 12
        assert by_author["[deleted]"]["excerpt"] == "12345"
        assert by_author["[deleted]"]["url"] == ""
        assert by_author["[deleted]"]["date"] is None
        assert "top_comments" not in enriched["R2"]

    def test_enrich_reddit_item_sc_coerces_scores_and_survives_malformed_comments(self):
        item = {"url": "https://www.reddit.com/r/test/comments/abc1/post/"}
        raw_comments = [
            None,
            "bad",
            {"body": "null score", "ups": None, "score": None, "author": "u_null"},
            {"body": "zero ups", "ups": 0, "score": 9, "author": "u_zero"},
            {"body": "bool score", "ups": True, "score": None, "author": "u_bool"},
            {"body": "string score", "ups": "18", "author": "u_str", "created_utc": 10**20},
            {"body": 54321, "ups": 4, "author": None, "permalink": 123},
        ]
        with mock.patch.object(reddit, "fetch_post_comments", return_value=raw_comments):
            out = reddit_enrich.enrich_reddit_item_sc(item, "dummy-token")
        scores = [(c["author"], c["score"]) for c in out["top_comments"]]
        assert scores == [
            ("u_str", 18),
            ("[deleted]", 4),
            ("u_null", 0),
            ("u_zero", 0),
            ("u_bool", 0),
        ]
        assert out["top_comments"][0]["date"] is None
        assert out["top_comments"][1]["url"] == ""
        assert out["top_comments"][1]["excerpt"] == "54321"

    def test_discover_subreddits_and_extract_helpers_handle_malformed_fields(self):
        results = [
            None,
            "bad",
            {"subreddit": None, "ups": 500},
            {"subreddit": "python", "ups": "500"},
            {"subreddit": "rust", "ups": True, "score": float("nan")},
            {"subreddit": {"name": "golang"}, "ups": 0, "score": 999},
        ]
        subs = reddit.discover_subreddits(results, topic="python", max_subs=5)
        assert subs[0] == "python"
        assert "None" not in subs
        assert reddit._extract_score({"ups": 0, "score": 5}) == 0
        assert reddit._extract_score({"ups": True, "score": None}) == 0
        assert reddit._extract_score({"ups": float("nan"), "score": float("inf")}) == 0
        assert reddit._extract_score({"ups": "33"}) == 33
        assert reddit.parse_reddit_response(None) == []  # type: ignore[arg-type]
        assert reddit.parse_reddit_response({"items": [None, {"id": "R1"}]}) == [{"id": "R1"}]

    @pytest.mark.parametrize("bad_ratio,expected", [
        ("bad", None),
        ([], None),
        (10**400, None),
        (float("nan"), None),
        (float("inf"), None),
        (True, None),
        (0.87, 0.87),
        ("0.92", 0.92),
    ])
    def test_normalize_post_coerces_upvote_ratio_safely(self, bad_ratio, expected):
        post = {
            "id": "t3_ur1",
            "title": "Python ratios",
            "permalink": "/r/python/comments/ur1/ratios/",
            "subreddit": "python",
            "ups": 10,
            "num_comments": 2,
            "upvote_ratio": bad_ratio,
        }
        normalized = reddit._normalize_post(post, 1, "global", query="python")
        assert normalized["engagement"]["upvote_ratio"] == expected

    def test_extract_subreddit_name_and_date_fallback_past_corrupt_primary_fields(self):
        assert reddit._extract_subreddit_name({"name": True, "display_name": "python"}) == "python"
        assert reddit._extract_subreddit_name({"name": "   ", "display_name": "LocalLLM"}) == "LocalLLM"
        assert reddit._extract_subreddit_name({"name": True, "display_name": False}) == ""

        assert reddit._extract_date({
            "created_utc": float("nan"),
            "created_at": "2026-11-10T12:00:00Z",
        }) == "2026-11-10"
        assert reddit._extract_date({
            "created_utc": True,
            "created_at": "not-a-date",
            "created_at_iso": "2026-11-09T00:00:00Z",
        }) == "2026-11-09"
        assert reddit._extract_date({
            "created_utc": 10**20,
            "created_at": "2026-11-08T00:00:00Z",
        }) == "2026-11-08"


# ---------------------------------------------------------------------------
# HIGH-132: reddit_enrich parse_thread_data & get_top_comments null safety
# ---------------------------------------------------------------------------


class TestHigh132RedditEnrichNullSafety:
    @pytest.mark.parametrize("payload", [
        [{"data": None}, {"data": None}],
        [{"data": "not-a-dict"}, {"data": 123}],
        [{"data": {"children": None}}, {"data": {"children": None}}],
        [{"data": {"children": "not-a-list"}}, {"data": {"children": 42}}],
        [{"data": {"children": [None]}}, {"data": {"children": [None, "junk", 123]}}],
        [{"data": {"children": [{"data": None}]}}, {"data": {"children": [{"kind": "t1", "data": None}]}}],
        [{"data": {"children": [{"data": "bad"}]}}, {"data": {"children": [{"kind": "t1", "data": "bad"}]}}],
    ])
    def test_parse_thread_data_survives_malformed_nested_structures(self, payload):
        parsed = reddit_enrich.parse_thread_data(payload)
        assert parsed == {"submission": None, "comments": []}

    def test_parse_thread_data_coerces_hostile_submission_and_comment_fields(self):
        payload = [
            {
                "data": {
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "score": True,
                                "num_comments": float("inf"),
                                "upvote_ratio": float("nan"),
                                "created_utc": 10**20,
                                "permalink": 123,
                                "title": None,
                                "selftext": None,
                            },
                        },
                    ],
                },
            },
            {
                "data": {
                    "children": [
                        None,
                        "junk",
                        {"kind": "t1", "data": {"body": None, "score": 50, "author": "skip_null"}},
                        {"kind": "t1", "data": {"body": True, "score": 50, "author": "skip_bool"}},
                        {
                            "kind": "t1",
                            "data": {
                                "body": "Hidden score comment",
                                "score": None,
                                "author": "alice",
                                "created_utc": True,
                                "permalink": "/r/test/comments/x/t/c1/",
                            },
                        },
                        {
                            "kind": "t1",
                            "data": {
                                "body": 123456,
                                "score": True,
                                "author": None,
                                "created_utc": float("inf"),
                                "permalink": 999,
                            },
                        },
                        {
                            "kind": "t1",
                            "data": {
                                "body": "NaN score comment",
                                "score": float("nan"),
                                "author": "bob",
                                "created_utc": float("nan"),
                            },
                        },
                        {
                            "kind": "t1",
                            "data": {
                                "body": "Inf score comment",
                                "score": float("inf"),
                                "author": "carol",
                                "created_utc": 10**20,
                            },
                        },
                        {
                            "kind": "t1",
                            "data": {
                                "body": "x" * 700,
                                "score": "42",
                                "author": "dave",
                                "created_utc": 1714694957,
                                "is_submitter": True,
                            },
                        },
                    ],
                },
            },
        ]

        parsed = reddit_enrich.parse_thread_data(payload)
        sub = parsed["submission"]
        assert sub == {
            "score": None,
            "num_comments": None,
            "upvote_ratio": None,
            "created_utc": None,
            "permalink": None,
            "title": None,
            "selftext": "",
        }
        comments = parsed["comments"]
        assert len(comments) == 5
        assert [c["score"] for c in comments] == [0, 0, 0, 0, 42]
        assert comments[0]["created_utc"] is None
        assert comments[1]["body"] == "123456"
        assert comments[1]["author"] == "[deleted]"
        assert comments[1]["permalink"] is None
        assert comments[1]["created_utc"] is None
        assert comments[2]["created_utc"] is None
        assert comments[3]["created_utc"] is None
        assert len(comments[4]["body"]) == 600
        assert comments[4]["created_utc"] == 1714694957
        assert comments[4]["is_submitter"] is True

        top = reddit_enrich.get_top_comments(comments, limit=10)
        assert top[0]["author"] == "dave"
        assert all(c["author"] != "[deleted]" for c in top)

        stored = reddit_enrich.stored_top_comments(top)
        assert stored[0]["score"] == 42
        assert stored[0]["date"] == "2024-05-03"
        assert stored[0]["is_uploader"] is True
        assert all(c["date"] is None for c in stored[1:])

    def test_parse_thread_data_handles_huge_int_upvote_ratio_and_scores_without_overflow(self):
        payload = [
            {
                "data": {
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "score": 10**400,
                                "num_comments": 10**400,
                                "upvote_ratio": 10**400,
                                "created_utc": 10**400,
                                "title": "Overflow test",
                                "selftext": "ok",
                            },
                        },
                    ],
                },
            },
            {
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "body": "Overflow comment",
                                "score": 10**400,
                                "author": "alice",
                                "created_utc": 10**400,
                            },
                        },
                    ],
                },
            },
        ]
        parsed = reddit_enrich.parse_thread_data(payload)
        assert parsed["submission"]["score"] is None
        assert parsed["submission"]["num_comments"] is None
        assert parsed["submission"]["upvote_ratio"] is None
        assert parsed["submission"]["created_utc"] is None
        assert len(parsed["comments"]) == 1
        assert parsed["comments"][0]["score"] == 0
        assert parsed["comments"][0]["created_utc"] is None

    def test_get_top_comments_stored_and_insights_handle_direct_hostile_inputs(self):
        assert reddit_enrich.get_top_comments(None) == []  # type: ignore[arg-type]
        assert reddit_enrich.stored_top_comments(None) == []  # type: ignore[arg-type]
        assert reddit_enrich.extract_comment_insights(None) == []  # type: ignore[arg-type]

        raw = [
            None,
            "junk",
            {"score": None, "author": "null_s", "body": "a" * 40, "created_utc": 10**20},
            {"score": True, "author": "bool_s", "body": 98765, "created_utc": True, "permalink": 42},
            {"score": float("nan"), "author": "nan_s", "body": None, "created_utc": float("inf")},
            {"score": float("inf"), "author": "inf_s", "body": "b" * 40},
            {"score": "20", "author": "str_s", "body": "Substantive technical comment with real detail."},
            {"score": 10, "author": "int_s", "body": "Another helpful technical explanation here."},
            {"score": -5, "author": "neg_s", "body": "Downvoted remark."},
            {"score": 999, "author": "[deleted]", "body": "Should be filtered out."},
        ]
        top = reddit_enrich.get_top_comments(raw, limit=10)
        assert [c["author"] for c in top[:2]] == ["str_s", "int_s"]
        assert top[-1]["author"] == "neg_s"
        assert all(c["author"] != "[deleted]" for c in top)

        stored = reddit_enrich.stored_top_comments(top)
        assert [c["score"] for c in stored[:2]] == [20, 10]
        assert stored[-1]["score"] == -5
        assert all(c["date"] is None for c in stored)

        insights = reddit_enrich.extract_comment_insights(top)
        assert "Substantive technical comment with real detail." in insights

    def test_reddit_session_thread_post_survives_null_selftext_and_hidden_comment_scores(self):
        data = json.loads(json.dumps(THREAD_SAMPLE))
        post_data = data[0]["data"]["children"][0]["data"]
        post_data["selftext"] = None
        data[1]["data"]["children"] = [
            None,
            {
                "kind": "t1",
                "data": {
                    "body": "Hidden-score comment on a real thread",
                    "score": None,
                    "author": "hidden_user",
                    "created_utc": 10**20,
                    "permalink": "/r/Ghostty/comments/abc/t/c1/",
                },
            },
            {
                "kind": "t1",
                "data": {
                    "body": 404,
                    "score": True,
                    "author": "weird_user",
                    "created_utc": True,
                },
            },
            {
                "kind": "t1",
                "data": {
                    "body": "Top scored real comment",
                    "score": 88,
                    "author": "real_user",
                    "created_utc": 1794268800,
                    "permalink": "/r/Ghostty/comments/abc/t/c2/",
                },
            },
        ]
        session = reddit_session.Session("dummy-pr34-cookie-0123456789", browser="chrome")
        post = reddit_session.thread_post(data, session)
        assert post is not None
        assert not post.get("_drop")
        assert post["selftext"] == ""
        assert [c["author"] for c in post["top_comments"]] == ["real_user", "hidden_user", "weird_user"]
        assert [c["score"] for c in post["top_comments"]] == [88, 0, 0]
        assert post["top_comments"][0]["date"] == "2026-11-10"
        assert post["top_comments"][1]["date"] is None
        assert post["top_comments"][2]["date"] is None


# ---------------------------------------------------------------------------
# HIGH-135: Canary check_hydration detects Arctic Shift ingestion stalls
# ---------------------------------------------------------------------------


def _canary_fetcher(pages: dict[str, tuple[int | None, str]]):
    def fetch(url: str, accept: str = "text/html"):
        for prefix, answer in pages.items():
            if url.startswith(prefix):
                return answer
        raise AssertionError(f"unexpected fetch {url}")

    return fetch


def _sample_refs(n: int = 3) -> list[ThreadRef]:
    refs: list[ThreadRef] = []
    for i in range(n):
        ref = canonical_thread(f"https://www.reddit.com/r/ClaudeAI/comments/st{i:04d}/topic/")
        assert ref is not None
        ref.provenance.add("web")
        ref.title = f"Claude Code thread {i}"
        refs.append(ref)
    return refs


class TestHigh135CanaryHydrationOutageDetection:
    def test_ingestion_stall_through_real_hydrator_fails_canary(self, tmp_path):
        """Arctic Shift returning 200 {"data": []} for all recent refs while old
        KNOWN_THREAD_IDS still resolve must fail the canary as DOWN."""
        pages = {
            "https://search.brave.com/": (200, BRAVE_PAGE),
            "https://html.duckduckgo.com/": (202, ""),
            "https://www.startpage.com/": (429, ""),
            reddit_arctic.API: (200, ARCTIC_KNOWN_OK),
        }
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}):
            code, report = canary.run(
                fetch=_canary_fetcher(pages),
                hydrate=reddit_hydrate.hydrate,
                topics=("Claude Code",),
                state_file=tmp_path / "state.json",
                now=NOW,
                sleep=lambda _s: None,
            )

        states = {c["name"]: c["state"] for c in report["checks"]}
        hydrate_check = next(c for c in report["checks"] if c["name"] == "hydrate: Claude Code")
        assert states["arctic: known ids"] == canary.OK
        assert states["hydrate: Claude Code"] == canary.DOWN
        assert hydrate_check["results"] == 0
        assert "0 of " in hydrate_check["note"]
        assert code == 1
        assert any("hydrate: Claude Code" in reason and "Arctic hydration is down" in reason for reason in report["failures"])

    def test_check_hydration_zero_filled_is_down_for_ok_and_unused(self):
        refs = _sample_refs(3)

        def hydrate_unknown(_refs, **_kwargs):
            items = [{"engagement_source": UNKNOWN, "top_comments": []} for _ in _refs]
            return SimpleNamespace(
                items=items,
                arctic_state="ok",
                detail=f"web served {len(items)}",
                comment_fills=0,
                comment_failures=0,
                comment_throttles=0,
            )

        check_ok = canary.check_hydration("Claude Code", refs, hydrate_unknown)
        assert check_ok is not None
        assert check_ok.state == canary.DOWN
        assert check_ok.results == 0

        def hydrate_unused(_refs, **_kwargs):
            return SimpleNamespace(
                items=[],
                arctic_state="unused",
                detail="web served 0",
                comment_fills=0,
                comment_failures=0,
                comment_throttles=0,
            )

        check_unused = canary.check_hydration("Claude Code", refs, hydrate_unused)
        assert check_unused is not None
        assert check_unused.state == canary.DOWN

    def test_check_hydration_throttled_zero_filled_stays_challenged(self):
        refs = _sample_refs(2)

        def hydrate_throttled(_refs, **_kwargs):
            items = [{"engagement_source": UNKNOWN, "top_comments": []} for _ in _refs]
            return SimpleNamespace(
                items=items,
                arctic_state="throttled",
                detail="web served 2; arctic throttled",
                comment_fills=0,
                comment_failures=0,
                comment_throttles=0,
            )

        check = canary.check_hydration("Claude Code", refs, hydrate_throttled)
        assert check is not None
        assert check.state == canary.CHALLENGED

    @pytest.mark.parametrize("source", [ARCTIC, ARCTIC_PENDING])
    def test_check_hydration_hydrated_threads_with_empty_comments_still_pass(self, tmp_path, source):
        pages = {
            "https://search.brave.com/": (200, BRAVE_PAGE),
            "https://html.duckduckgo.com/": (202, ""),
            "https://www.startpage.com/": (429, ""),
            reddit_arctic.API: (200, ARCTIC_KNOWN_OK),
        }

        def hydrate_empty_comments(refs, **_kwargs):
            items = [{"engagement_source": source, "top_comments": []} for _ in refs]
            return SimpleNamespace(
                items=items,
                arctic_state="ok",
                detail=f"web+arctic served {len(items)}",
                comment_fills=0,
                comment_failures=0,
                comment_throttles=0,
            )

        code, report = canary.run(
            fetch=_canary_fetcher(pages),
            hydrate=hydrate_empty_comments,
            topics=("Claude Code",),
            state_file=tmp_path / "state.json",
            now=NOW,
            sleep=lambda _s: None,
        )
        states = {c["name"]: c["state"] for c in report["checks"]}
        assert code == 0, report["failures"]
        assert states["hydrate: Claude Code"] == canary.OK

    def test_check_hydration_returns_none_for_empty_refs(self):
        called = False

        def hydrate_should_not_run(_refs, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("should not be called")

        assert canary.check_hydration("Claude Code", [], hydrate_should_not_run) is None
        assert called is False
