"""Regression tests for PR 21: Reddit keyless/shreddit/arctic thread safety, OP filter & deadlines.

Covers:
- HIGH-111: OP (`is_submitter` / `is_uploader`) preservation across Arctic Shift,
  shreddit HTML, site search, normalization, and lane/post-ranking enrichment.
- HIGH-115: Synchronized `WebFloor` updates and snapshots under concurrent timeout.
- HIGH-116: Main-thread-only application of `_enrich` results so abandoned workers
  cannot mutate returned post dicts.
- HIGH-117: Thread-isolated `last_tree_throttled` / status tuple and locked,
  in-flight-deduplicated `_cache` in `reddit_arctic.fetch_scores`.
- HIGH-119: Shreddit comment fetch deadline clamping, `retries=1`, challenge HTML
  rejection (never memoized), and 0-comment Arctic Shift skip.
- HIGH-123: `reddit_search.search` waiter deadline bounds, no uncoordinated
  post-election fallthrough, and deadline-aware `_search_stream` paging.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from unittest import mock

import pytest

from lib import (
    http,
    normalize,
    reddit_arctic,
    reddit_discover,
    reddit_hydrate,
    reddit_keyless,
    reddit_refs,
    reddit_search,
    reddit_shreddit,
    render,
    schema,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CHALLENGE_HTML = (FIXTURES / "reddit_search_challenge.html").read_text(encoding="utf-8")
SEARCH_PAGE1_HTML = (FIXTURES / "reddit_search_page1.html").read_text(encoding="utf-8")
SHREDDIT_HTML = (FIXTURES / "reddit_shreddit_comments_sample.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_reddit_state():
    """Exercise real module logic with clean per-run memos and state."""
    http.reset_reddit_keyless_memo()
    reddit_arctic.reset_memo()
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()
    yield
    http.reset_reddit_keyless_memo()
    reddit_arctic.reset_memo()
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()


@pytest.fixture(autouse=True)
def _no_arctic_network():
    """Override conftest's stub so arctic tests exercise real functions."""
    yield


@pytest.fixture(autouse=True)
def _no_reddit_web_floor():
    """Override conftest's stub so web floor tests exercise real logic."""
    yield


@pytest.fixture(autouse=True)
def _no_reddit_kept_thread_comments():
    """Override conftest's stub so enrich_source_items tests run real logic."""
    yield


def _shreddit_comment_html(
    author: str,
    thing_id: str,
    score: int,
    body: str,
    extra_attrs: str = "",
) -> str:
    attrs = f" {extra_attrs.strip()}" if extra_attrs.strip() else ""
    return (
        f'<shreddit-comment created="2026-10-10T10:00:00+0000" author="{author}" '
        f'thingId="{thing_id}" depth="0" permalink="/r/test/comments/abc123/comment/{thing_id}/" '
        f'score="{score}"{attrs}>'
        f'<div id="{thing_id}-post-rtjson-content"><p>{body}</p></div>'
        f"</shreddit-comment>"
    )


def _search_page_html(post_id: str, author: str | None = None, cursor: str | None = None) -> str:
    ctx: dict = {
        "action_info": {"type": "post"},
        "post": {"id": f"t3_{post_id}", "title": f"Ghostty topic {post_id}"},
        "subreddit": {"name": "ghostty"},
    }
    if author is not None:
        ctx["profile"] = {"id": "t2_author1", "name": author}
    encoded = json.dumps(ctx).replace('"', "&quot;")
    parts = [
        f'<search-telemetry-tracker data-faceplate-tracking-context="{encoded}" '
        f'view-events="search/view/post">'
        f'<h2><a data-testid="post-title" href="/r/ghostty/comments/{post_id}/slug/">'
        f"Ghostty topic {post_id}</a></h2>"
        f"</search-telemetry-tracker>"
        f'<faceplate-timeago ts="2026-10-10T10:00:00.000000+0000"></faceplate-timeago>'
        f'<div data-testid="search-counter-row">'
        f'<span><faceplate-number number="42" pretty></faceplate-number> votes</span>'
        f'<span><faceplate-number number="9" pretty></faceplate-number> comments</span>'
        f"</div>",
    ]
    if cursor:
        parts.append(
            f'<faceplate-partial loading="lazy" '
            f'src="/svc/shreddit/search/?q=ghostty&amp;type=posts&amp;cursor={cursor}">'
            f"</faceplate-partial>"
        )
    return "<html><body>" + "".join(parts) + "</body></html>"


# ---------------------------------------------------------------------------
# HIGH-111: OP (`is_submitter` / `is_uploader`) preservation across all paths
# ---------------------------------------------------------------------------


class TestHigh111OpCommentFiltering:
    def test_arctic_comment_tree_and_post_comments_propagate_is_submitter(self):
        rows = [
            {
                "kind": "t1",
                "data": {
                    "id": "c_op",
                    "author": "ThreadStarter",
                    "body": "OP clarification comment",
                    "score": 120,
                    "created_utc": 1783000000,
                    "permalink": "/r/test/comments/abc123/x/c_op/",
                    "depth": 0,
                    "is_submitter": True,
                },
            },
            {
                "kind": "t1",
                "data": {
                    "id": "c_user",
                    "author": "CommunityReader",
                    "body": "Community feedback comment",
                    "score": 80,
                    "created_utc": 1783000000,
                    "permalink": "/r/test/comments/abc123/x/c_user/",
                    "depth": 0,
                    "is_submitter": False,
                },
            },
            {
                "kind": "t1",
                "data": {
                    "id": "c_nonbool",
                    "author": "OtherReader",
                    "body": "Comment without boolean is_submitter",
                    "score": 10,
                    "created_utc": 1783000000,
                    "permalink": "/r/test/comments/abc123/x/c_nonbool/",
                    "depth": 0,
                    "is_submitter": "true",
                },
            },
        ]
        with mock.patch.object(reddit_arctic.http, "get", return_value={"data": rows}):
            tree = reddit_arctic.fetch_comment_tree("abc123")
            fallback = reddit_arctic.fetch_post_comments("abc123")

        assert tree is not None
        by_author_tree = {c["author"]: c for c in tree}
        assert by_author_tree["ThreadStarter"]["is_uploader"] is True
        assert by_author_tree["CommunityReader"]["is_uploader"] is False
        assert "is_uploader" not in by_author_tree["OtherReader"]

        by_author_fb = {c["author"]: c for c in fallback}
        assert by_author_fb["ThreadStarter"]["is_uploader"] is True
        assert by_author_fb["CommunityReader"]["is_uploader"] is False
        assert "is_uploader" not in by_author_fb["OtherReader"]

    def test_shreddit_fetch_comments_preserves_arctic_is_uploader_flag(self):
        arctic_rows = [
            {
                "score": 95,
                "date": "2026-10-10",
                "author": "ThreadStarter",
                "body": "OP note from archive",
                "excerpt": "OP note from archive",
                "permalink": "/r/test/comments/abc123/x/c1/",
                "url": "https://reddit.com/r/test/comments/abc123/x/c1/",
                "depth": 0,
                "is_uploader": True,
            },
            {
                "score": 50,
                "date": "2026-10-10",
                "author": "CommunityUser",
                "body": "Real community reply",
                "excerpt": "Real community reply",
                "permalink": "/r/test/comments/abc123/x/c2/",
                "url": "https://reddit.com/r/test/comments/abc123/x/c2/",
                "depth": 0,
                "is_uploader": False,
            },
        ]
        url = "https://www.reddit.com/r/test/comments/abc123/slug/"
        with mock.patch.object(reddit_shreddit.http, "reddit_keyless_get_text", return_value=None), \
             mock.patch.object(reddit_shreddit.reddit_arctic, "fetch_post_comments", return_value=arctic_rows):
            out = reddit_shreddit.fetch_comments(url)

        marks = {c["author"]: c["is_uploader"] for c in out["top_comments"]}
        assert marks == {"ThreadStarter": True, "CommunityUser": False}

    def test_shreddit_parse_and_fetch_handle_html_op_attr_and_post_author(self):
        html_with_attr = (
            '<shreddit-comment-tree-stats total-comments="2"></shreddit-comment-tree-stats>'
            + _shreddit_comment_html("OpUser", "t1_op", 200, "OP reply", extra_attrs="is-op")
            + _shreddit_comment_html("Reader1", "t1_u1", 50, "Reader reply", extra_attrs='is-op="false"')
        )
        parsed = reddit_shreddit.parse_comments(html_with_attr)
        assert {c["author"]: c["is_uploader"] for c in parsed} == {
            "OpUser": True,
            "Reader1": False,
        }

        html_no_attr = (
            '<shreddit-comment-tree-stats total-comments="2"></shreddit-comment-tree-stats>'
            + _shreddit_comment_html("OpUser", "t1_op", 200, "OP reply without attr")
            + _shreddit_comment_html("Reader1", "t1_u1", 50, "Reader reply without attr")
        )
        url = "https://www.reddit.com/r/test/comments/abc123/slug/"
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=html_no_attr):
            out = reddit_shreddit.fetch_comments(url, post_author="opuser")
        assert {c["author"]: c["is_uploader"] for c in out["top_comments"]} == {
            "OpUser": True,
            "Reader1": False,
        }

        # Deleted/removed post author must not stamp false is_uploader values.
        http.reset_reddit_keyless_memo()
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=html_no_attr):
            out_deleted = reddit_shreddit.fetch_comments(url, post_author="[deleted]")
        assert all("is_uploader" not in c for c in out_deleted["top_comments"])

    def test_reddit_search_parse_page_extracts_profile_author(self):
        posts, _ = reddit_search.parse_page(SEARCH_PAGE1_HTML, "ButcherBox")
        assert posts[0]["author"] == "grassyglassy"
        assert posts[1]["author"] == "Altruistic_Echidna86"

        no_profile, _ = reddit_search.parse_page(_search_page_html("p999", author=None), "ghostty")
        assert no_profile[0]["author"] == ""

    def test_lane_and_post_ranking_enrichment_filter_op_comments_in_render(self):
        html_no_attr = (
            '<shreddit-comment-tree-stats total-comments="2"></shreddit-comment-tree-stats>'
            + _shreddit_comment_html("RealOP", "t1_op", 500, "My own OP follow-up on Ghostty")
            + _shreddit_comment_html("CommunityFan", "t1_fan", 75, "Great community take on Ghostty")
        )
        post = {
            "id": "R1",
            "title": "Ghostty benchmark thread",
            "url": "https://www.reddit.com/r/ghostty/comments/abc123/slug/",
            "score": 150,
            "num_comments": 2,
            "subreddit": "ghostty",
            "author": "RealOP",
            "selftext": "",
            "date": "2026-10-10",
            "engagement": {"score": 150, "num_comments": 2},
            "relevance": 0.9,
            "metadata": {"post_id": "abc123"},
        }

        # Path 1: lane-time _enrich stamps OP before normalization.
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=html_no_attr):
            enriched_posts = reddit_keyless._enrich([dict(post)], "quick")
        [norm_lane] = normalize.normalize_source_items("reddit", enriched_posts, "2026-10-01", "2026-10-31")
        assert norm_lane.author is None
        assert norm_lane.metadata["post_author"] == "RealOP"
        quotable_lane = [
            c for c in norm_lane.metadata["top_comments"]
            if render._is_community_comment(norm_lane, c, c.get("excerpt", ""))
        ]
        assert [c["author"] for c in quotable_lane] == ["CommunityFan"]

        # Path 2: post-normalization enrich_source_items preserves OP filtering via metadata["post_author"].
        http.reset_reddit_keyless_memo()
        [norm_empty] = normalize.normalize_source_items("reddit", [dict(post)], "2026-10-01", "2026-10-31")
        assert norm_empty.author is None
        assert norm_empty.metadata["post_author"] == "RealOP"
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=html_no_attr):
            reddit_keyless.enrich_source_items([norm_empty], depth="quick")
        quotable_final = [
            c for c in norm_empty.metadata["top_comments"]
            if render._is_community_comment(norm_empty, c, c.get("excerpt", ""))
        ]
        assert [c["author"] for c in quotable_final] == ["CommunityFan"]


# ---------------------------------------------------------------------------
# HIGH-115: Synchronized WebFloor updates and snapshots under _within timeout
# ---------------------------------------------------------------------------


class TestHigh115WebFloorThreadSafety:
    def test_web_floor_concurrent_mutations_and_snapshots_never_raise(self):
        floor = reddit_discover.WebFloor()
        stop = threading.Event()
        errors: list[BaseException] = []

        def writer():
            try:
                for i in range(400):
                    ref = reddit_refs.canonical_thread(
                        f"https://www.reddit.com/r/ghostty/comments/p{i:04d}/"
                    )
                    assert ref is not None
                    floor.record_query(
                        f"query-{i}",
                        {f"rung_{i % 25}": "ok"},
                        [*floor.snapshot().refs[-10:], ref],
                    )
                    floor.record_rung(f"extra_{i % 30}", "empty")
                    floor.add_refs([ref])
            except BaseException as exc:
                errors.append(exc)
            finally:
                stop.set()

        t = threading.Thread(target=writer)
        t.start()
        snapshots = 0
        try:
            while not stop.is_set() or snapshots < 20:
                snap = floor.snapshot()
                assert isinstance(snap.rungs, dict)
                assert isinstance(snap.refs, list)
                assert isinstance(snap.queries, list)
                snapshots += 1
        except BaseException as exc:
            errors.append(exc)
        t.join(timeout=5)
        assert errors == []

    def test_web_floor_timeout_salvages_partial_refs_while_worker_still_writes(self):
        first_ref = reddit_refs.canonical_thread("https://www.reddit.com/r/ghostty/comments/salv1/")
        assert first_ref is not None
        first_ref.provenance.add("web")
        first_ref.title = "Ghostty salvaged thread"

        started_mutating = threading.Event()
        stop_worker = threading.Event()

        def slow_mutating_web_refs(topic, *, floor=None, **_kwargs):
            assert floor is not None
            floor.record_query(f"site:reddit.com {topic}", {"brave": "ok"}, [first_ref])
            started_mutating.set()
            i = 0
            while not stop_worker.is_set() and i < 2000:
                floor.record_rung(f"dyn_{i}", "ok")
                i += 1
                time.sleep(0.0005)
            return floor

        archived = {
            "id": "",
            "title": "Ghostty salvaged thread",
            "url": "https://www.reddit.com/r/ghostty/comments/salv1/slug/",
            "score": 50,
            "num_comments": 5,
            "subreddit": "ghostty",
            "created_utc": 1700000000,
            "author": "op",
            "selftext": "",
            "date": "2026-10-10",
            "engagement": {"score": 50, "num_comments": 5},
            "relevance": 0.8,
            "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "salv1"},
        }

        def fake_within(wait_until, stage, default, call, *args, **kwargs):
            if stage == "web floor":
                t = threading.Thread(target=call, args=args, kwargs=kwargs)
                t.start()
                assert started_mutating.wait(timeout=3.0)
                return default
            return call(*args, **kwargs)

        try:
            with mock.patch.object(reddit_keyless, "_within", side_effect=fake_within), \
                 mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=slow_mutating_web_refs), \
                 mock.patch.object(
                     reddit_keyless.reddit_arctic, "fetch_posts_by_ids",
                     return_value=({"salv1": dict(archived)}, "ok"),
                 ), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_comment_tree", return_value=[]):
                kept = reddit_keyless._web_floor(
                    "Ghostty", "quick", None, None, "2026-10-01", "2026-10-31",
                    deadline=time.monotonic() + 30.0,
                )
        finally:
            stop_worker.set()

        assert [p["metadata"]["post_id"] for p in kept] == ["salv1"]


# ---------------------------------------------------------------------------
# HIGH-116: Abandoned _enrich workers never mutate returned post dicts
# ---------------------------------------------------------------------------


class TestHigh116EnrichMainThreadOnlyMutation:
    def test_enrich_one_does_not_mutate_post_in_place(self):
        post = {
            "url": "https://www.reddit.com/r/test/comments/abc123/slug/",
            "author": "op",
            "num_comments": 0,
            "engagement": {"score": 10, "num_comments": 0},
        }
        payload = {
            "top_comments": [{"score": 12, "author": "reader", "excerpt": "nice", "url": "", "depth": 0}],
            "comment_insights": ["nice"],
            "num_comments": 25,
        }
        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", return_value=payload):
            returned = reddit_keyless._enrich_one(post)
        assert returned == payload
        assert "top_comments" not in post
        assert post["num_comments"] == 0
        assert post["engagement"]["num_comments"] == 0

    def test_abandoned_worker_finishing_after_timeout_never_mutates_returned_post(self, monkeypatch):
        monkeypatch.setattr(reddit_keyless, "MIN_STAGE_SECONDS", 0.05)
        release = threading.Event()
        slow_finished = threading.Event()

        def fetch(url, deadline=None, post_author=None):
            if "slow02" in url:
                release.wait(timeout=5.0)
                slow_finished.set()
                return {
                    "top_comments": [{"score": 99, "author": "late", "excerpt": "too late", "url": "", "depth": 0}],
                    "comment_insights": ["late insight"],
                    "num_comments": 999,
                }
            return {
                "top_comments": [{"score": 15, "author": "fast_user", "excerpt": "fast comment", "url": "", "depth": 0}],
                "comment_insights": ["fast insight"],
                "num_comments": 15,
            }

        fast_post = {
            "url": "https://www.reddit.com/r/test/comments/fast01/slug/",
            "author": "op1",
            "num_comments": 1,
            "engagement": {"score": 10, "num_comments": 1},
        }
        slow_post = {
            "url": "https://www.reddit.com/r/test/comments/slow02/slug/",
            "author": "op2",
            "num_comments": 1,
            "engagement": {"score": 10, "num_comments": 1},
        }
        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", side_effect=fetch):
            result = reddit_keyless._enrich(
                [fast_post, slow_post], "quick", lane_deadline=time.monotonic() + 0.15,
            )

        # Fast post was enriched on the main thread; slow post was timed out.
        assert result[0]["num_comments"] == 15
        assert result[0]["top_comments"][0]["author"] == "fast_user"
        assert "top_comments" not in result[1]
        assert result[1]["num_comments"] == 1

        # Let the abandoned worker complete and prove it cannot mutate slow_post.
        release.set()
        assert slow_finished.wait(timeout=3.0)
        time.sleep(0.05)
        assert "top_comments" not in result[1]
        assert "comment_insights" not in result[1]
        assert result[1]["num_comments"] == 1
        assert result[1]["engagement"]["num_comments"] == 1


# ---------------------------------------------------------------------------
# HIGH-117: Thread-isolated last_tree_throttled & locked/deduped fetch_scores
# ---------------------------------------------------------------------------


class TestHigh117ArcticThreadSafety:
    def test_concurrent_comment_tree_throttles_do_not_bleed_across_threads(self):
        a_done = threading.Event()
        b_done = threading.Event()

        def fake_get(url, **_kwargs):
            if "t3_throttled1" in url:
                raise http.HTTPError("HTTP 422: slow down", status_code=422)
            if "t3_error2" in url:
                raise http.HTTPError("HTTP 500: boom", status_code=500)
            return {"data": []}

        def thread_a():
            item = {
                "metadata": {"post_id": "throttled1"},
                "engagement": {"score": 20, "num_comments": 5},
                "engagement_source": reddit_refs.ARCTIC,
            }
            filled, failed, throttled = reddit_hydrate._attach_comments(
                [item], "quick", deadline=time.monotonic() + 10.0, now=1800000000.0,
            )
            a_done.set()
            assert b_done.wait(timeout=3.0)
            # Even after thread B failed with HTTP 500, thread A's thread-local
            # throttle flag must remain True.
            return filled, failed, throttled, reddit_arctic.last_tree_throttled

        def thread_b():
            assert a_done.wait(timeout=3.0)
            item = {
                "metadata": {"post_id": "error2"},
                "engagement": {"score": 20, "num_comments": 5},
                "engagement_source": reddit_refs.ARCTIC,
            }
            filled, failed, throttled = reddit_hydrate._attach_comments(
                [item], "quick", deadline=time.monotonic() + 10.0, now=1800000000.0,
            )
            b_done.set()
            return filled, failed, throttled, reddit_arctic.last_tree_throttled

        with mock.patch.object(reddit_arctic.http, "get", side_effect=fake_get), \
             mock.patch.object(reddit_arctic.time, "sleep"):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_throttled = pool.submit(thread_a)
                f_error = pool.submit(thread_b)
                res_throttled = f_throttled.result(timeout=5.0)
                res_error = f_error.result(timeout=5.0)

        assert res_throttled == (0, 1, 1, True)
        assert res_error == (0, 1, 0, False)

    def test_fetch_scores_coalesces_concurrent_requests_and_respects_waiter_deadline(self):
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        calls_lock = threading.Lock()

        def slow_get(url, **_kwargs):
            with calls_lock:
                calls.append(url)
            started.set()
            release.wait(timeout=5.0)
            return {
                "data": [
                    {"id": "p1", "score": 111, "num_comments": 12},
                    {"id": "p2", "score": 222, "num_comments": 24},
                ]
            }

        with mock.patch.object(reddit_arctic.http, "get", side_effect=slow_get):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                f_owner = pool.submit(reddit_arctic.fetch_scores, ["p1", "p2"])
                assert started.wait(timeout=3.0)
                # Waiter with generous deadline coalesces on owner's single request.
                f_waiter_ok = pool.submit(
                    reddit_arctic.fetch_scores, ["p1", "p2"], time.monotonic() + 5.0,
                )
                # Waiter with short deadline returns promptly without blocking.
                t0 = time.monotonic()
                timed_out = reddit_arctic.fetch_scores(["p1"], deadline=time.monotonic() + 0.08)
                elapsed = time.monotonic() - t0
                assert timed_out == {}
                assert elapsed < 1.0

                release.set()
                owner_res = f_owner.result(timeout=5.0)
                waiter_res = f_waiter_ok.result(timeout=5.0)

        assert len(calls) == 1
        assert owner_res == waiter_res == {
            "p1": {"score": 111, "num_comments": 12},
            "p2": {"score": 222, "num_comments": 24},
        }
        assert reddit_arctic._score_inflight == {}

    def test_fetch_scores_failure_does_not_poison_cache_or_leak_inflight(self):
        attempts = {"n": 0}

        def flaky_get(url, **_kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise http.HTTPError("HTTP 503", status_code=503)
            return {"data": [{"id": "p9", "score": 77, "num_comments": 4}]}

        with mock.patch.object(reddit_arctic.http, "get", side_effect=flaky_get):
            assert reddit_arctic.fetch_scores(["p9"]) == {}
            assert "p9" not in reddit_arctic._cache
            assert reddit_arctic._score_inflight == {}
            assert reddit_arctic.fetch_scores(["p9"]) == {"p9": {"score": 77, "num_comments": 4}}


# ---------------------------------------------------------------------------
# HIGH-119: Shreddit comment fetch deadline, retries=1, challenge & 0-comment
# ---------------------------------------------------------------------------


class TestHigh119ShredditFetchHardening:
    URL = "https://www.reddit.com/r/Rakuten/comments/1taeiw0/title/"

    def test_expired_deadline_skips_both_shreddit_and_arctic(self):
        for url in (self.URL, "https://www.reddit.com/comments/1taeiw0/"):
            with mock.patch.object(reddit_shreddit.http, "reddit_keyless_get_text") as shreddit_get, \
                 mock.patch.object(reddit_shreddit.reddit_arctic, "fetch_post_comments") as arctic_fetch:
                out = reddit_shreddit.fetch_comments(url, deadline=time.monotonic() - 1.0)
            assert out == {"top_comments": [], "comment_insights": [], "num_comments": None}
            shreddit_get.assert_not_called()
            arctic_fetch.assert_not_called()

    def test_uses_retries_1_and_clamps_timeout_to_deadline(self):
        seen_kwargs: dict = {}

        def fake_get_text(url, **kwargs):
            seen_kwargs.update(kwargs)
            return SHREDDIT_HTML

        with mock.patch.object(reddit_shreddit.http, "get_text", side_effect=fake_get_text), \
             mock.patch.object(reddit_shreddit.time, "monotonic", return_value=1000.0):
            out = reddit_shreddit.fetch_comments(self.URL, timeout=12, deadline=1003.5)

        assert out["top_comments"]
        assert seen_kwargs["retries"] == 1
        assert seen_kwargs["timeout"] == pytest.approx(3.5)
        assert seen_kwargs["deadline_monotonic"] == 1003.5

    def test_forwards_deadline_monotonic_when_transport_supports_it(self):
        seen: dict = {}

        def fake_keyless_get(url, timeout=12, retries=1, accept="*/*", headers=None,
                             validate=None, deadline_monotonic=None):
            seen.update(
                timeout=timeout,
                retries=retries,
                validate=validate,
                deadline_monotonic=deadline_monotonic,
            )
            return SHREDDIT_HTML

        with mock.patch.object(reddit_shreddit.http, "reddit_keyless_get_text", side_effect=fake_keyless_get), \
             mock.patch.object(reddit_shreddit.time, "monotonic", return_value=500.0):
            out = reddit_shreddit.fetch_comments(self.URL, deadline=504.0)

        assert out["top_comments"]
        assert seen["retries"] == 1
        assert seen["timeout"] == pytest.approx(4.0)
        assert seen["deadline_monotonic"] == 504.0
        assert callable(seen["validate"])

    def test_challenge_page_is_rejected_not_memoized_and_allows_fallback(self):
        bodies = [CHALLENGE_HTML, SHREDDIT_HTML]

        def fake_get_text(url, **_kwargs):
            return bodies.pop(0)

        with mock.patch.object(reddit_shreddit.http, "get_text", side_effect=fake_get_text) as gt, \
             mock.patch.object(reddit_shreddit.reddit_arctic, "fetch_post_comments", return_value=[]) as arctic, \
             http.capture_failures() as failures:
            first = reddit_shreddit.fetch_comments(self.URL)
            second = reddit_shreddit.fetch_comments(self.URL)

        assert first["top_comments"] == []
        assert arctic.call_count == 1  # fell back after challenge rejection
        assert len(failures) == 1
        assert "challenge" in str(failures[0]).lower()
        assert second["top_comments"]  # second call fetched fresh, not memoized challenge HTML
        assert gt.call_count == 2

    def test_zero_comment_shreddit_page_skips_arctic_fallback(self):
        zero_html = '<shreddit-comment-tree-stats total-comments="0"></shreddit-comment-tree-stats>'
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=zero_html), \
             mock.patch.object(reddit_shreddit.reddit_arctic, "fetch_post_comments") as arctic:
            out = reddit_shreddit.fetch_comments(self.URL)

        assert out == {"top_comments": [], "comment_insights": [], "num_comments": 0}
        arctic.assert_not_called()


# ---------------------------------------------------------------------------
# HIGH-123: reddit_search waiter deadline, no election fallthrough, stream cap
# ---------------------------------------------------------------------------


class TestHigh123SearchDeadlinesAndElection:
    def test_waiter_respects_deadline_and_never_falls_through_uncached(self):
        started = threading.Event()
        release = threading.Event()
        uncached_calls = {"n": 0}
        lock = threading.Lock()

        def slow_uncached(*args, **kwargs):
            with lock:
                uncached_calls["n"] += 1
            started.set()
            release.wait(timeout=5.0)
            return [{"id": "R1", "url": "https://www.reddit.com/r/ghostty/comments/p1/"}], True

        with mock.patch.object(reddit_search, "_search_uncached_with_status", side_effect=slow_uncached):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_owner = pool.submit(reddit_search.search, "Ghostty", depth="deep", subreddits=["a", "b", "c"])
                assert started.wait(timeout=3.0)

                t0 = time.monotonic()
                waiter_res = reddit_search.search(
                    "Ghostty", depth="deep", subreddits=["a", "b", "c"],
                    deadline=time.monotonic() + 0.1,
                )
                elapsed = time.monotonic() - t0
                assert waiter_res == []
                assert elapsed < 1.0
                # Waiter must NOT have fallen through to _search_uncached!
                assert uncached_calls["n"] == 1

                release.set()
                owner_res = f_owner.result(timeout=5.0)
                assert len(owner_res) == 1

    def test_election_loop_without_owner_returns_empty_instead_of_stampeding(self):
        # Pre-seed an inflight slot that never finishes across both election
        # rounds: waiter must return [] instead of running uncached without
        # holding _run_inflight[key].
        key = ("Ghostty", "quick", "month", ())
        stuck_slot = reddit_search._InflightSearch()
        with reddit_search._run_lock:
            reddit_search._run_inflight[key] = stuck_slot

        with mock.patch.object(reddit_search, "_result_timeout", return_value=0.02), \
             mock.patch.object(reddit_search, "_search_uncached_with_status") as uncached:
            out = reddit_search.search("Ghostty", depth="quick")

        assert out == []
        uncached.assert_not_called()

    def test_search_stream_stops_paging_at_deadline_and_clamps_timeout(self):
        pages_fetched: list[tuple[str, float]] = []
        clock = [100.0]

        def fake_retry_get(url, **kwargs):
            pages_fetched.append((url, kwargs["timeout"]))
            # First page takes 4 seconds, pushing clock past deadline 103.0.
            clock[0] += 4.0
            return _search_page_html(f"p{len(pages_fetched)}", author="alice", cursor=f"cur{len(pages_fetched)}"), None

        with mock.patch.object(reddit_search.http, "reddit_keyless_get_text_retry_429", side_effect=fake_retry_get), \
             mock.patch.object(reddit_search.time, "monotonic", side_effect=lambda: clock[0]):
            posts = reddit_search._search_stream(
                "ghostty", "month", max_pages=8, deadline=103.0,
            )

        assert len(pages_fetched) == 1
        assert pages_fetched[0][1] == pytest.approx(3.0)
        assert [p["metadata"]["post_id"] for p in posts] == ["p1"]
        assert posts[0]["author"] == "alice"


# ---------------------------------------------------------------------------
# Review fixes: real http.reddit_keyless_* deadline_monotonic, u/ prefix OP
# matching, strict start-tag attribute tokenization, expired fetch_scores gate
# ---------------------------------------------------------------------------


class TestReviewFixes:
    def test_bool_attr_tokenizes_attributes_without_value_false_positives(self):
        html = (
            '<shreddit-comment-tree-stats total-comments="4"></shreddit-comment-tree-stats>'
            + _shreddit_comment_html(
                "NoteUser", "t1_note", 100, "Quoted text mentioning OP",
                extra_attrs='aria-label="a is-op note" data-note=\'is-submitter\'',
            )
            + _shreddit_comment_html(
                "SingleFalse", "t1_sf", 90, "Single-quoted false",
                extra_attrs="is-op='false'",
            )
            + _shreddit_comment_html(
                "SingleTrue", "t1_st", 80, "Single-quoted true",
                extra_attrs="is-op='true'",
            )
            + _shreddit_comment_html(
                "PrefixedAttr", "t1_pref", 70, "Different attribute name",
                extra_attrs='author-is-op="true"',
            )
        )
        parsed = {c["author"]: c for c in reddit_shreddit.parse_comments(html)}
        assert "is_uploader" not in parsed["NoteUser"]
        assert parsed["SingleFalse"]["is_uploader"] is False
        assert parsed["SingleTrue"]["is_uploader"] is True
        assert "is_uploader" not in parsed["PrefixedAttr"]

    def test_u_prefix_stripped_when_matching_op_author(self):
        html = (
            '<shreddit-comment-tree-stats total-comments="3"></shreddit-comment-tree-stats>'
            + _shreddit_comment_html("u/Alice", "t1_a1", 50, "OP with u/ prefix")
            + _shreddit_comment_html("/u/Alice", "t1_a2", 40, "OP with /u/ prefix")
            + _shreddit_comment_html("u/Bob", "t1_b1", 30, "Other user with u/ prefix")
        )
        url = "https://www.reddit.com/r/test/comments/abc123/slug/"
        with mock.patch.object(reddit_shreddit.http, "get_text", return_value=html):
            out = reddit_shreddit.fetch_comments(url, post_author="Alice")
        marks = {c["author"]: c["is_uploader"] for c in out["top_comments"]}
        assert marks == {"u/Alice": True, "/u/Alice": True, "u/Bob": False}

        # Also verify post_author with u/ prefix matches bare comment author,
        # and reddit_hydrate._stamp_uploader strips u/ and /u/ consistently.
        post = {
            "author": "/u/Alice",
            "top_comments": [
                {"author": "Alice", "excerpt": "bare op"},
                {"author": "u/alice", "excerpt": "prefixed op"},
                {"author": "u/other", "excerpt": "community"},
                {"author": "u/[deleted]", "excerpt": "deleted"},
            ],
        }
        reddit_hydrate._stamp_uploader(post)
        assert [c.get("is_uploader") for c in post["top_comments"]] == [True, True, False, None]

    def test_expired_fetch_scores_returns_cached_hits_without_registering_inflight(self):
        with reddit_arctic._memo_lock:
            reddit_arctic._cache["cached1"] = {"score": 42, "num_comments": 7}

        seen_inserts: list[str] = []

        class SpyDict(dict):
            def __setitem__(self, key, value):
                seen_inserts.append(key)
                super().__setitem__(key, value)

        spy_inflight = SpyDict()
        with mock.patch.object(reddit_arctic, "_score_inflight", spy_inflight), \
             mock.patch.object(reddit_arctic.http, "get") as http_get:
            out = reddit_arctic.fetch_scores(
                ["cached1", "uncached2"], deadline=time.monotonic() - 1.0,
            )

        assert out == {"cached1": {"score": 42, "num_comments": 7}}
        http_get.assert_not_called()
        assert seen_inserts == []
        assert spy_inflight == {}

    def test_http_reddit_keyless_get_text_enforces_deadline_on_limiter_waiter_and_get_text(self, monkeypatch):
        url_limiter = "https://www.reddit.com/svc/shreddit/comments/r/test/t3_lim1?sort=top"
        # 1. Drained limiter fails fast inside acquire(deadline_monotonic=...) without calling get_text.
        slow_limiter = http.RateLimiter(rate_per_sec=0.05, burst=1)
        slow_limiter.acquire()  # drain the single token (next refill in 20s)
        monkeypatch.setattr(http, "REDDIT_KEYLESS_LIMITER", slow_limiter)

        with mock.patch.object(http, "get_text") as gt, \
             http.capture_failures() as failures:
            t0 = time.monotonic()
            body = http.reddit_keyless_get_text(
                url_limiter, timeout=12, retries=1, deadline_monotonic=time.monotonic() + 0.1,
            )
            elapsed = time.monotonic() - t0
        assert body is None
        assert elapsed < 1.0
        gt.assert_not_called()
        assert any(isinstance(f, http.DeadlineExceeded) for f in failures)

        # 2. Coalesced waiter respects deadline_monotonic and does not block on wait allowance.
        monkeypatch.setattr(http, "REDDIT_KEYLESS_LIMITER", http.RateLimiter(rate_per_sec=100.0, burst=100))
        url_waiter = "https://www.reddit.com/svc/shreddit/comments/r/test/t3_wait1?sort=top"
        owner_started = threading.Event()
        release_owner = threading.Event()
        seen_get_text_kwargs: dict = {}

        def slow_owner_get(u, **kwargs):
            seen_get_text_kwargs.update(kwargs)
            owner_started.set()
            release_owner.wait(timeout=5.0)
            return SHREDDIT_HTML

        with mock.patch.object(http, "get_text", side_effect=slow_owner_get):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                owner_deadline = time.monotonic() + 5.0
                f_owner = pool.submit(
                    http.reddit_keyless_get_text,
                    url_waiter,
                    timeout=12,
                    retries=1,
                    deadline_monotonic=owner_deadline,
                )
                assert owner_started.wait(timeout=3.0)

                t0 = time.monotonic()
                waiter_res = http.reddit_keyless_get_text(
                    url_waiter, timeout=12, retries=1, deadline_monotonic=time.monotonic() + 0.08,
                )
                elapsed = time.monotonic() - t0
                assert waiter_res is None
                assert elapsed < 1.0

                release_owner.set()
                assert f_owner.result(timeout=5.0) == SHREDDIT_HTML

        assert seen_get_text_kwargs["deadline_monotonic"] == owner_deadline
        assert seen_get_text_kwargs["timeout"] <= 5.0

    def test_http_reddit_keyless_retry_429_respects_deadline_monotonic(self):
        url = "https://www.reddit.com/svc/shreddit/search/?q=ghostty&type=posts&t=month&sort=relevance"

        # Case A: insufficient remaining budget after 429 skips sleep & retry.
        def always_429(u, **kwargs):
            http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", 429))
            return None

        with mock.patch.object(http, "get_text", side_effect=always_429) as gt, \
             mock.patch.object(http.time, "sleep") as slept, \
             http.capture_failures() as failures:
            body, err = http.reddit_keyless_get_text_retry_429(
                url,
                timeout=15,
                accept="text/html",
                deadline_monotonic=time.monotonic() + 0.3,
            )

        assert body is None
        assert err is not None and "429" in err
        assert gt.call_count == 1
        slept.assert_not_called()
        assert any(f.status_code == 429 for f in failures)

        # Case B: sufficient budget sleeps once, forwards deadline_monotonic to both attempts, and recovers.
        seen_deadlines: list[float | None] = []
        responses = [None, _search_page_html("p429", author="alice")]

        def recover_429(u, **kwargs):
            seen_deadlines.append(kwargs.get("deadline_monotonic"))
            val = responses.pop(0)
            if val is None:
                http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", 429))
            return val

        future_deadline = time.monotonic() + 10.0
        with mock.patch.object(http, "get_text", side_effect=recover_429), \
             mock.patch.object(http.time, "sleep") as slept, \
             http.capture_failures() as failures:
            body, err = reddit_search._fetch_page(url, deadline=future_deadline)

        assert body is not None and err is None
        assert seen_deadlines == [future_deadline, future_deadline]
        slept.assert_called_once()
        assert failures == []


# ---------------------------------------------------------------------------
# Greptile review fixes: partial page salvage, web-floor hydration reserve,
# incremental/full-cache fetch_scores handoff, direct kwarg forwarding
# ---------------------------------------------------------------------------


class TestGreptileFixes:
    def test_search_salvages_page_one_when_page_two_stalls_past_deadline(self):
        page2_started = threading.Event()
        release_page2 = threading.Event()

        def fake_retry_get(url, **kwargs):
            if "cursor=" not in url:
                return _search_page_html("p1keep", author="alice", cursor="nextcur1"), None
            page2_started.set()
            release_page2.wait(timeout=5.0)
            return _search_page_html("p2late", author="bob"), None

        t0 = time.monotonic()
        try:
            with mock.patch.object(
                reddit_search.http, "reddit_keyless_get_text_retry_429", side_effect=fake_retry_get,
            ):
                posts = reddit_search.search(
                    "Ghostty", depth="quick", deadline=time.monotonic() + 0.15,
                )
        finally:
            release_page2.set()

        assert page2_started.is_set()
        assert time.monotonic() - t0 < 1.5
        assert [p["metadata"]["post_id"] for p in posts] == ["p1keep"]
        assert posts[0]["author"] == "alice"

    def test_web_floor_hydrates_query_one_threads_when_query_two_blocks(self):
        query2_started = threading.Event()
        release_query2 = threading.Event()

        def fake_keyless_search(query, date_range, config, count=20, freshness=None, *, deadline_monotonic):
            assert deadline_monotonic is not None
            if "r/ghostty" not in query:
                return (
                    [{
                        "url": "https://www.reddit.com/r/ghostty/comments/q1hit/ghostty_fast/",
                        "title": "r/ghostty on Reddit: Ghostty fast terminal",
                        "snippet": "",
                        "date": "2026-10-10",
                    }],
                    {"keyless_backend": "brave", "keyless_rungs": {"brave": "ok"}},
                )
            query2_started.set()
            release_query2.wait(timeout=5.0)
            return ([], {"keyless_backend": "brave", "keyless_rungs": {"brave": "ok"}})

        archived = {
            "id": "",
            "title": "Ghostty fast terminal",
            "url": "https://www.reddit.com/r/ghostty/comments/q1hit/ghostty_fast/",
            "score": 88,
            "num_comments": 12,
            "subreddit": "ghostty",
            "created_utc": 1700000000,
            "author": "op_user",
            "selftext": "",
            "date": "2026-10-10",
            "engagement": {"score": 88, "num_comments": 12},
            "relevance": 0.9,
            "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "q1hit"},
        }

        t0 = time.monotonic()
        try:
            with mock.patch.object(
                reddit_discover.web_search_keyless, "keyless_search", side_effect=fake_keyless_search,
            ), mock.patch.object(
                reddit_keyless.reddit_arctic, "fetch_posts_by_ids",
                return_value=({"q1hit": dict(archived)}, "ok"),
            ), mock.patch.object(
                reddit_keyless.reddit_arctic, "fetch_comment_tree", return_value=[],
            ):
                # 1.8s lane budget gives discovery 60% (~1.08s) and reserves 40% (~0.72s)
                # for hydration, so blocked query 2 cuts off early while query 1's ref is hydrated.
                kept = reddit_keyless._web_floor(
                    "Ghostty", "quick", ["ghostty"], None, "2026-10-01", "2026-10-31",
                    deadline=time.monotonic() + 1.8,
                )
        finally:
            release_query2.set()

        assert query2_started.is_set()
        assert time.monotonic() - t0 < 2.5
        assert [p["metadata"]["post_id"] for p in kept] == ["q1hit"]

    def test_web_discover_deadline_keeps_meaningful_share_at_tight_budget_and_hydrates(self):
        # At 2.9s remaining, web discovery must get 60% (1.74s, >= 1.5s) and
        # reserve 40% (1.16s, >= 1.0s) for hydration rather than starving query 1.
        now = 1000.0
        cutoff_tight = reddit_keyless._web_discover_deadline(now + 2.9, now=now)
        assert cutoff_tight is not None
        assert cutoff_tight - now == pytest.approx(1.74)
        assert (now + 2.9) - cutoff_tight == pytest.approx(1.16)

        # At 20s remaining, reserve caps at _WEB_HYDRATION_RESERVE_SECONDS (8s).
        cutoff_wide = reddit_keyless._web_discover_deadline(now + 20.0, now=now)
        assert cutoff_wide == pytest.approx(now + 12.0)

        # With a 2.9s budget where the single query takes 0.35s (> old 100ms cutoff),
        # _web_floor must still find and hydrate query 1's thread.
        def moderate_query(query, date_range, config, count=20, freshness=None, *, deadline_monotonic):
            assert deadline_monotonic is not None
            time.sleep(0.35)
            return (
                [{
                    "url": "https://www.reddit.com/r/ghostty/comments/tight1/ghostty_tight/",
                    "title": "r/ghostty on Reddit: Ghostty tight budget",
                    "snippet": "",
                    "date": "2026-10-10",
                }],
                {"keyless_backend": "brave", "keyless_rungs": {"brave": "ok"}},
            )

        archived = {
            "id": "",
            "title": "Ghostty tight budget",
            "url": "https://www.reddit.com/r/ghostty/comments/tight1/ghostty_tight/",
            "score": 55,
            "num_comments": 4,
            "subreddit": "ghostty",
            "created_utc": 1700000000,
            "author": "op_user",
            "selftext": "",
            "date": "2026-10-10",
            "engagement": {"score": 55, "num_comments": 4},
            "relevance": 0.9,
            "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "tight1"},
        }

        with mock.patch.object(
            reddit_discover.web_search_keyless, "keyless_search", side_effect=moderate_query,
        ), mock.patch.object(
            reddit_keyless.reddit_arctic, "fetch_posts_by_ids",
            return_value=({"tight1": dict(archived)}, "ok"),
        ), mock.patch.object(
            reddit_keyless.reddit_arctic, "fetch_comment_tree", return_value=[],
        ):
            kept = reddit_keyless._web_floor(
                "Ghostty", "quick", None, None, "2026-10-01", "2026-10-31",
                deadline=time.monotonic() + 2.9,
            )

        assert [p["metadata"]["post_id"] for p in kept] == ["tight1"]

    def test_partial_search_is_not_cached_and_later_call_fetches_all_pages(self):
        page2_attempts = {"n": 0}
        release_first_page2 = threading.Event()
        first_page2_started = threading.Event()

        def fake_retry_get(url, **kwargs):
            if "cursor=" not in url:
                return _search_page_html("p1full", author="alice", cursor="cur2"), None
            page2_attempts["n"] += 1
            if page2_attempts["n"] == 1:
                first_page2_started.set()
                release_first_page2.wait(timeout=5.0)
                return None, "deadline exceeded"
            return _search_page_html("p2full", author="bob"), None

        try:
            with mock.patch.object(
                reddit_search.http, "reddit_keyless_get_text_retry_429", side_effect=fake_retry_get,
            ):
                # Call 1 times out on page 2 and returns salvaged page-1 posts,
                # without caching the truncated result in _run_results.
                partial = reddit_search.search(
                    "Ghostty", depth="quick", deadline=time.monotonic() + 0.15,
                )
                assert [p["metadata"]["post_id"] for p in partial] == ["p1full"]
                assert reddit_search._run_results == {}

                # Call 2 has ample time and fetches both page 1 and page 2, then caches.
                full = reddit_search.search(
                    "Ghostty", depth="quick", deadline=time.monotonic() + 5.0,
                )
                assert [p["metadata"]["post_id"] for p in full] == ["p1full", "p2full"]
                assert len(reddit_search._run_results) == 1
        finally:
            release_first_page2.set()
        assert first_page2_started.is_set()

    def test_partial_search_hands_partial_fallback_to_concurrent_waiter(self):
        owner_started = threading.Event()
        release_owner = threading.Event()
        calls = {"n": 0}
        lock = threading.Lock()

        def fake_uncached_status(*_args, **_kwargs):
            with lock:
                calls["n"] += 1
                n = calls["n"]
            if n == 1:
                owner_started.set()
                release_owner.wait(timeout=5.0)
                return [{"id": "R1", "url": "https://www.reddit.com/r/ghostty/comments/part1/"}], False
            # Waiter's retry yields nothing; it must fall back to the owner's partial list.
            return [], False

        with mock.patch.object(
            reddit_search, "_search_uncached_with_status", side_effect=fake_uncached_status,
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_owner = pool.submit(
                    reddit_search.search, "Ghostty", depth="quick", deadline=time.monotonic() + 5.0,
                )
                assert owner_started.wait(timeout=3.0)
                f_waiter = pool.submit(
                    reddit_search.search, "Ghostty", depth="quick", deadline=time.monotonic() + 5.0,
                )
                time.sleep(0.05)
                release_owner.set()
                owner_res = f_owner.result(timeout=5.0)
                waiter_res = f_waiter.result(timeout=5.0)

        assert calls["n"] == 2
        assert len(owner_res) == 1
        assert len(waiter_res) == 1
        assert waiter_res[0]["url"] == owner_res[0]["url"]
        assert waiter_res[0] is not owner_res[0]
        assert reddit_search._run_results == {}

    def test_shared_deadline_waiter_receives_live_page_one_while_page_two_stalls(self):
        page2_started = threading.Event()
        release_page2 = threading.Event()

        def fake_retry_get(url, **kwargs):
            if "cursor=" not in url:
                return _search_page_html("p1live", author="alice", cursor="cur_live"), None
            page2_started.set()
            release_page2.wait(timeout=5.0)
            return _search_page_html("p2slow", author="bob"), None

        try:
            with mock.patch.object(
                reddit_search.http, "reddit_keyless_get_text_retry_429", side_effect=fake_retry_get,
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    # Owner stays paused on page 2 until we explicitly release it,
                    # guaranteeing slot.partial is still None when the waiter times out.
                    f_owner = pool.submit(
                        reddit_search.search, "Ghostty", depth="quick", deadline=time.monotonic() + 5.0,
                    )
                    assert page2_started.wait(timeout=3.0)
                    key = ("Ghostty", "quick", "month", ())
                    with reddit_search._run_lock:
                        inflight_slot = reddit_search._run_inflight[key]
                    assert inflight_slot.partial is None

                    f_waiter = pool.submit(
                        reddit_search.search, "Ghostty", depth="quick", deadline=time.monotonic() + 0.12,
                    )
                    waiter_res = f_waiter.result(timeout=2.0)
                    # Check waiter result BEFORE releasing the owner so live_snapshot() is proven.
                    assert not f_owner.done()
                    assert inflight_slot.partial is None
                    assert [p["metadata"]["post_id"] for p in waiter_res] == ["p1live"]

                    release_page2.set()
                    owner_res = f_owner.result(timeout=3.0)
        finally:
            release_page2.set()

        assert [p["metadata"]["post_id"] for p in owner_res] == ["p1live", "p2slow"]
        assert owner_res[0] is not waiter_res[0]

    def test_live_snapshot_interleaves_subreddit_and_global_streams_like_owner(self):
        sub_done = threading.Barrier(2)
        global_page2_started = threading.Event()
        release_global_page2 = threading.Event()

        def multi_post_page(prefix: str, count: int, sub: str, cursor: str | None = None) -> str:
            units = []
            for i in range(count):
                pid = f"{prefix}{i}"
                ctx = json.dumps({
                    "action_info": {"type": "post"},
                    "post": {"id": f"t3_{pid}", "title": f"Ghostty {pid}"},
                    "subreddit": {"name": sub},
                    "profile": {"id": f"t2_{pid}", "name": f"user_{pid}"},
                }).replace('"', "&quot;")
                units.append(
                    f'<search-telemetry-tracker data-faceplate-tracking-context="{ctx}" '
                    f'view-events="search/view/post">'
                    f'<h2><a data-testid="post-title" href="/r/{sub}/comments/{pid}/slug/">'
                    f"Ghostty {pid}</a></h2></search-telemetry-tracker>"
                    f'<faceplate-timeago ts="2026-10-10T10:00:00.000000+0000"></faceplate-timeago>'
                    f'<div data-testid="search-counter-row">'
                    f'<span><faceplate-number number="10" pretty></faceplate-number> votes</span>'
                    f'<span><faceplate-number number="2" pretty></faceplate-number> comments</span>'
                    f"</div>"
                )
            if cursor:
                units.append(
                    f'<faceplate-partial loading="lazy" '
                    f'src="/svc/shreddit/search/?q=Ghostty&amp;type=posts&amp;cursor={cursor}">'
                    f"</faceplate-partial>"
                )
            return "<html><body>" + "".join(units) + "</body></html>"

        def fake_retry_get(url, **kwargs):
            if "/r/SubA/" in url:
                html = multi_post_page("a", 7, "SubA")
                sub_done.wait(timeout=3.0)
                return html, None
            if "/r/SubB/" in url:
                html = multi_post_page("b", 7, "SubB")
                sub_done.wait(timeout=3.0)
                return html, None
            if "cursor=" not in url:
                # Ensure both subreddit streams complete (14 sub posts > quick cap of 10)
                # BEFORE global page 1 arrives.
                time.sleep(0.05)
                return multi_post_page("g", 7, "allnews", cursor="gcur2"), None
            global_page2_started.set()
            release_global_page2.wait(timeout=5.0)
            return multi_post_page("g2", 3, "allnews"), None

        try:
            with mock.patch.object(
                reddit_search.http, "reddit_keyless_get_text_retry_429", side_effect=fake_retry_get,
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    f_owner = pool.submit(
                        reddit_search.search,
                        "Ghostty", depth="quick", subreddits=["SubA", "SubB"],
                        deadline=time.monotonic() + 5.0,
                    )
                    assert global_page2_started.wait(timeout=3.0)
                    f_waiter = pool.submit(
                        reddit_search.search,
                        "Ghostty", depth="quick", subreddits=["SubA", "SubB"],
                        deadline=time.monotonic() + 0.12,
                    )
                    waiter_res = f_waiter.result(timeout=2.0)
                    assert not f_owner.done()
                    waiter_ids = [p["metadata"]["post_id"] for p in waiter_res]
                    assert len(waiter_ids) == 10
                    # Round-robin across [SubA, SubB, global] keeps global posts even
                    # though SubA + SubB alone finished earlier with 14 posts.
                    assert waiter_ids == ["a0", "b0", "g0", "a1", "b1", "g1", "a2", "b2", "g2", "a3"]

                    release_global_page2.set()
                    owner_res = f_owner.result(timeout=3.0)
        finally:
            release_global_page2.set()

        assert [p["metadata"]["post_id"] for p in owner_res] == [
            "a0", "b0", "g0", "a1", "b1", "g1", "a2", "b2", "g2", "a3",
        ]

    def test_web_floor_passes_earlier_hydration_cutoff_to_session_and_archive(self):
        ref = reddit_refs.canonical_thread("https://www.reddit.com/r/ghostty/comments/sess1/")
        assert ref is not None
        ref.provenance.add("web")
        ref.title = "Ghostty session hydration cutoff"

        seen_deadlines: dict[str, float | None] = {}

        class FakeSession:
            alive = True

        def fake_session_hydrator(session, *, depth="default", deadline=None):
            seen_deadlines["session_hydrator"] = deadline
            return lambda _refs: {}

        def fake_hydrate(refs, *, topic="", depth="default", from_date=None, to_date=None,
                         session_hydrator=None, deadline=None, **_kwargs):
            seen_deadlines["hydrate"] = deadline
            return reddit_hydrate.Hydration(
                items=[{
                    "title": "Ghostty session hydration cutoff",
                    "url": ref.url,
                    "subreddit": "ghostty",
                    "metadata": {"post_id": "sess1"},
                }],
                detail="web+session served 1",
                arctic_state="ok",
            )

        outer_deadline = time.monotonic() + 10.0
        with mock.patch.object(
            reddit_keyless.reddit_discover, "web_refs",
            return_value=reddit_discover.WebFloor(
                refs=[ref], queries=["site:reddit.com Ghostty"], rungs={"brave": "ok"},
            ),
        ), mock.patch.object(
            reddit_keyless.reddit_session, "current", return_value=FakeSession(),
        ), mock.patch.object(
            reddit_keyless.reddit_session, "hydrator", side_effect=fake_session_hydrator,
        ), mock.patch.object(
            reddit_keyless.reddit_hydrate, "hydrate", side_effect=fake_hydrate,
        ):
            kept = reddit_keyless._web_floor(
                "Ghostty", "quick", None, None, "2026-10-01", "2026-10-31",
                deadline=outer_deadline,
            )

        assert [p["metadata"]["post_id"] for p in kept] == ["sess1"]
        assert seen_deadlines["session_hydrator"] is not None
        assert seen_deadlines["hydrate"] is not None
        assert seen_deadlines["session_hydrator"] == seen_deadlines["hydrate"]
        assert seen_deadlines["session_hydrator"] < outer_deadline

    def test_fetch_scores_waiter_fetches_batch_two_when_owner_expires_after_batch_one(self):
        batch1_started = threading.Event()
        release_batch1 = threading.Event()
        calls: list[tuple[str, int]] = []
        calls_lock = threading.Lock()
        owner_tid: list[int] = []
        waiter_tid: list[int] = []

        def fake_get(url, **_kwargs):
            tid = threading.get_ident()
            with calls_lock:
                calls.append((url, tid))
            if "ids=b1" in url:
                batch1_started.set()
                release_batch1.wait(timeout=5.0)
                return {"data": [{"id": "b1", "score": 11, "num_comments": 1}]}
            if "ids=b2" in url:
                return {"data": [{"id": "b2", "score": 22, "num_comments": 2}]}
            return {"data": []}

        def run_owner():
            owner_tid.append(threading.get_ident())
            return reddit_arctic.fetch_scores(["b1", "b2"], time.monotonic() + 0.12)

        def run_waiter():
            waiter_tid.append(threading.get_ident())
            return reddit_arctic.fetch_scores(["b1", "b2"], time.monotonic() + 5.0)

        with mock.patch.object(reddit_arctic, "BATCH", 1), \
             mock.patch.object(reddit_arctic, "PACE_SECONDS", 0.0), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=fake_get):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                # Owner has a tight deadline that expires while batch 1 is in flight,
                # so owner must NOT block waiter from fetching batch 2 ("b2").
                f_owner = pool.submit(run_owner)
                assert batch1_started.wait(timeout=3.0)
                f_waiter = pool.submit(run_waiter)
                time.sleep(0.15)  # let owner's deadline expire before releasing batch 1
                release_batch1.set()
                owner_res = f_owner.result(timeout=5.0)
                waiter_res = f_waiter.result(timeout=5.0)

        assert owner_res["b1"] == {"score": 11, "num_comments": 1}
        assert waiter_res == {
            "b1": {"score": 11, "num_comments": 1},
            "b2": {"score": 22, "num_comments": 2},
        }
        assert len(calls) == 2
        by_batch = {("b1" if "ids=b1" in url else "b2"): tid for url, tid in calls}
        assert by_batch == {"b1": owner_tid[0], "b2": waiter_tid[0]}

    def test_fetch_scores_waiter_retries_slot_when_owner_fails_mid_batch(self):
        started = threading.Event()
        release = threading.Event()
        attempt = {"n": 0}
        lock = threading.Lock()

        def flaky_get(url, **_kwargs):
            with lock:
                attempt["n"] += 1
                n = attempt["n"]
            if n == 1:
                started.set()
                release.wait(timeout=5.0)
                raise http.DeadlineExceeded()
            return {"data": [{"id": "retry1", "score": 99, "num_comments": 8}]}

        with mock.patch.object(reddit_arctic.http, "get", side_effect=flaky_get):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_owner = pool.submit(
                    reddit_arctic.fetch_scores, ["retry1"], time.monotonic() + 0.1,
                )
                assert started.wait(timeout=3.0)
                f_waiter = pool.submit(
                    reddit_arctic.fetch_scores, ["retry1"], time.monotonic() + 5.0,
                )
                time.sleep(0.03)
                release.set()
                owner_res = f_owner.result(timeout=5.0)
                waiter_res = f_waiter.result(timeout=5.0)

        assert owner_res == {}
        assert waiter_res == {"retry1": {"score": 99, "num_comments": 8}}
        assert attempt["n"] == 2

    def test_fetch_scores_waiter_receives_scores_even_when_cache_is_full(self):
        with reddit_arctic._memo_lock:
            reddit_arctic._cache["existing"] = {"score": 1, "num_comments": 0}

        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def slow_get(url, **_kwargs):
            calls.append(url)
            started.set()
            release.wait(timeout=5.0)
            return {"data": [{"id": "fresh1", "score": 333, "num_comments": 44}]}

        with mock.patch.object(reddit_arctic, "CACHE_MAX", 1), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=slow_get):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                f_owner = pool.submit(reddit_arctic.fetch_scores, ["fresh1"])
                assert started.wait(timeout=3.0)
                f_waiter = pool.submit(reddit_arctic.fetch_scores, ["fresh1"])
                time.sleep(0.03)
                release.set()
                owner_res = f_owner.result(timeout=5.0)
                waiter_res = f_waiter.result(timeout=5.0)

        assert len(calls) == 1
        assert owner_res == {"fresh1": {"score": 333, "num_comments": 44}}
        assert waiter_res == {"fresh1": {"score": 333, "num_comments": 44}}
        # Cache stayed strictly bounded at CACHE_MAX == 1.
        assert len(reddit_arctic._cache) == 1
        assert "fresh1" not in reddit_arctic._cache

    def test_no_reflection_helpers_and_direct_kwarg_forwarding(self):
        assert not hasattr(reddit_search, "_accepts_kwarg")
        assert not hasattr(reddit_shreddit, "_accepts_kwarg")
        assert not hasattr(reddit_shreddit, "_accepts_named_kwarg")
        assert not hasattr(reddit_keyless, "_accepts_kwarg")

        # Direct post_author forwarding from reddit_keyless._fetch_enrichment.
        with mock.patch.object(
            reddit_keyless.reddit_shreddit, "fetch_comments",
            return_value={"top_comments": [], "comment_insights": [], "num_comments": None},
        ) as fc:
            reddit_keyless._fetch_enrichment(
                "https://www.reddit.com/r/test/comments/abc123/slug/",
                deadline=1234.5,
                post_author="Alice",
            )
        fc.assert_called_once_with(
            "https://www.reddit.com/r/test/comments/abc123/slug/",
            deadline=1234.5,
            post_author="Alice",
        )

