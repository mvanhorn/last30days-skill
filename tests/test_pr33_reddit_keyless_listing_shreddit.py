"""Regression tests for PR 33: Reddit keyless listings/scores & Shreddit/listing HTML parsing.

Covers:
- HIGH-112: Shreddit subreddit listings pass window-derived ``timeframe`` instead
  of hardcoding ``"month"``.
- HIGH-113: Listing HTTP failures stay isolated via ``http.capture_failures()`` and
  classify accurately as ``blocked`` / ``throttled`` / ``error`` / ``empty`` / ``ok``;
  deadline-skipped listings do not emit bogus ``"listing": "empty"`` that defeats
  ``reddit_health.every_path_failed``.
- HIGH-114: Live site-search posts carry ``engagement_source = LIVE`` when Reddit
  rendered a live vote counter (even ``0``), Arctic Shift score backfill never
  reduces existing ``num_comments`` and applies ``label_archive_post``, and
  0-score high-comment threads remain eligible for comment enrichment.
- HIGH-121: ``_attr`` in ``reddit_listing`` and ``reddit_shreddit`` matches exact
  attribute names (case-insensitively), never hyphenated suffixes like ``award-id``,
  ``parent-permalink``, ``hide-score``, or ``data-author``.
- HIGH-122: ``_PARA`` ignores ``<pre>``/``<picture>``/``<path>``/``<param>`` and
  self-closing ``<p/>``; ``_body_for`` matches both ``-post-rtjson-content`` and
  ``-comment-rtjson-content``, preserves nested ``<div>`` content, and stops at the
  matching closing ``</div>`` or ``<shreddit-comment>`` boundary without swallowing
  intervening deleted/UI ``<p>`` text.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import (
    dates,
    health,
    http,
    pipeline,
    reddit,
    reddit_arctic,
    reddit_discover,
    reddit_health,
    reddit_hydrate,
    reddit_keyless,
    reddit_listing,
    reddit_refs,
    reddit_search,
    reddit_shreddit,
    schema,
    web_search_keyless,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LISTING_CARDS_HTML = (FIXTURES / "reddit_listing_cards_sample.html").read_text(encoding="utf-8")
SEARCH_PAGE1_HTML = (FIXTURES / "reddit_search_page1.html").read_text(encoding="utf-8")
SHREDDIT_COMMENTS_HTML = (FIXTURES / "reddit_shreddit_comments_sample.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_state():
    http.reset_reddit_keyless_memo()
    reddit_arctic.reset_memo()
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()
    reddit_health.reset_run_state()
    web_search_keyless.reset_rung_health()
    yield
    http.reset_reddit_keyless_memo()
    reddit_arctic.reset_memo()
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()
    reddit_health.reset_run_state()
    web_search_keyless.reset_rung_health()


@pytest.fixture(autouse=True)
def _no_arctic_network():
    """Override conftest's stub so arctic interactions are controlled per test."""
    yield


@pytest.fixture(autouse=True)
def _no_reddit_web_floor():
    """Override conftest's stub so web floor is controlled per test."""
    yield


@pytest.fixture(autouse=True)
def _no_reddit_kept_thread_comments():
    """Override conftest's stub so enrich_source_items tests run real logic."""
    yield


def _post_dict(
    pid: str,
    *,
    title: str = "Topic discussion",
    sub: str = "python",
    score: int = 0,
    num_comments: int = 0,
    post_date: str = "2026-11-15",
    created_utc: float | None = None,
    engagement_source: str | None = None,
) -> dict:
    item: dict = {
        "id": "",
        "title": title,
        "url": f"https://www.reddit.com/r/{sub}/comments/{pid}/slug/",
        "score": score,
        "num_comments": num_comments,
        "subreddit": sub,
        "created_utc": created_utc,
        "author": "op_user",
        "selftext": "",
        "date": post_date,
        "engagement": {
            "score": score,
            "num_comments": num_comments,
            "upvote_ratio": None,
        },
        "relevance": 0.7,
        "why_relevant": "Reddit search",
        "metadata": {"post_id": pid},
    }
    if engagement_source is not None:
        item["engagement_source"] = engagement_source
    return item


def _empty_web_floor(*_args, **_kwargs):
    return reddit_discover.WebFloor()


# ---------------------------------------------------------------------------
# HIGH-112: Shreddit subreddit listings timeframe follows from_date / to_date
# ---------------------------------------------------------------------------


class TestHigh112ShredditListingTimeframe:
    @pytest.mark.parametrize(
        "offset_start,offset_end,expected_tf",
        [
            (0, 0, "day"),         # same-day window
            (2, 0, "week"),        # --days=2 short window
            (3, 0, "week"),        # --days=3 short window
            (30, 0, "month"),      # default 30-day window
            (90, 0, "year"),       # --days=90 long window
            (365, 0, "year"),      # --days=365 long window
            (400, 0, "all"),       # >366-day window
            (60, 50, "year"),      # --as-of historical window ending >31d ago
            (420, 400, "all"),     # --as-of historical window ending >366d ago
        ],
    )
    def test_scored_listings_passes_window_derived_timeframe(
        self, offset_start: int, offset_end: int, expected_tf: str
    ):
        today = datetime.now(timezone.utc).date()
        from_date = (today - timedelta(days=offset_start)).isoformat()
        to_date = (today - timedelta(days=offset_end)).isoformat()
        assert reddit._window_to_time_filter(from_date, to_date) == expected_tf

        with mock.patch.object(
            reddit_keyless.reddit_listing, "fetch_listings", return_value=[]
        ) as fetch_shreddit, mock.patch.object(
            reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]
        ):
            reddit_keyless._scored_listings(
                ["python"],
                depth="quick",
                query="python",
                from_date=from_date,
                to_date=to_date,
            )

        fetch_shreddit.assert_called_once()
        assert fetch_shreddit.call_args.kwargs["timeframe"] == expected_tf

    def test_missing_or_invalid_dates_fall_back_to_month(self):
        for bad_from, bad_to in ((None, None), ("", ""), ("not-a-date", "2026-11-01")):
            with mock.patch.object(
                reddit_keyless.reddit_listing, "fetch_listings", return_value=[]
            ) as fetch_shreddit, mock.patch.object(
                reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]
            ):
                reddit_keyless._scored_listings(
                    ["python"], depth="quick", from_date=bad_from, to_date=bad_to
                )
            assert fetch_shreddit.call_args.kwargs["timeframe"] == "month"

    def test_discover_and_listing_url_propagate_historical_as_of_timeframe(self):
        today = datetime.now(timezone.utc).date()
        from_date = (today - timedelta(days=90)).isoformat()
        to_date = (today - timedelta(days=60)).isoformat()
        in_window_ts = f"{(today - timedelta(days=75)).isoformat()}T12:00:00+00:00"
        card_html = (
            f'<shreddit-post permalink="/r/python/comments/old90d/python_release/" '
            f'post-title="Python release retrospective" score="320" comment-count="45" '
            f'author="alice" subreddit-name="python" created-timestamp="{in_window_ts}">'
            f"</shreddit-post>"
        )
        requested_urls: list[str] = []

        def fake_get_text(url, **_kwargs):
            requested_urls.append(url)
            return card_html

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores", return_value={}), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", return_value={}), \
             mock.patch.object(http, "get_text", side_effect=fake_get_text):
            out = reddit_keyless.search_and_enrich(
                "Python release",
                from_date,
                to_date,
                depth="quick",
                subreddits=["python"],
                dedicated_subreddits=["python"],
            )

        top_urls = [u for u in requested_urls if "/community-more-posts/top/" in u]
        assert top_urls, f"expected shreddit top listing request, got {requested_urls}"
        for u in top_urls:
            assert parse_qs(urlsplit(u).query).get("t") == ["year"], u
        assert [p["metadata"]["post_id"] for p in out] == ["old90d"]


# ---------------------------------------------------------------------------
# HIGH-113: Listing health classification, isolation & deadline skip semantics
# ---------------------------------------------------------------------------


class TestHigh113ListingHealthAndFailureIsolation:
    @pytest.mark.parametrize(
        "status_code,reason,expected_state",
        [
            (403, "Forbidden", reddit_health.BLOCKED),
            (429, "Too Many Requests", reddit_health.THROTTLED),
            (500, "Internal Server Error", reddit_health.ERROR),
        ],
    )
    def test_scored_listings_classifies_http_errors_and_isolates_outer_sink(
        self, status_code: int, reason: str, expected_state: str
    ):
        def fake_get_text(_url, **_kwargs):
            http._record_failure(http.HTTPError(f"HTTP {status_code}: {reason}", status_code))
            return None

        with mock.patch.object(http, "get_text", side_effect=fake_get_text), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=http.HTTPError(f"HTTP {status_code}: {reason}", status_code)), \
             http.capture_failures() as outer_failures:
            posts, state = reddit_keyless._scored_listings_with_state(["python"], depth="quick")

        assert posts == []
        assert state == expected_state
        # Listing failures must stay isolated from the outer stream capture sink.
        assert outer_failures == []

    def test_scored_listings_classifies_interstitial_as_blocked(self):
        challenge = "<html><body><h1>Please verify you are human</h1></body></html>"

        with mock.patch.object(http, "get_text", return_value=challenge), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
             http.capture_failures() as outer_failures:
            posts, state = reddit_keyless._scored_listings_with_state(["python"], depth="quick")

        assert posts == []
        assert state == reddit_health.BLOCKED
        assert outer_failures == []

    def test_scored_listings_does_not_misclassify_429_on_subreddit_named_blocked(self):
        def fake_get_text(url, **_kwargs):
            http._record_failure(http.HTTPError(f"HTTP 429: Too Many Requests ({url})", 429))
            return None

        with mock.patch.object(http, "get_text", side_effect=fake_get_text), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]):
            posts, state = reddit_keyless._scored_listings_with_state(["adblocked"], depth="quick")

        assert posts == []
        assert state == reddit_health.THROTTLED

    def test_scored_listings_classifies_clean_empty_as_empty(self):
        with mock.patch.object(http, "get_text", return_value="<div></div>"), \
             mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}), \
             http.capture_failures() as outer_failures:
            posts, state = reddit_keyless._scored_listings_with_state(["python"], depth="quick")

        assert posts == []
        assert state == reddit_health.EMPTY
        assert outer_failures == []

    def test_scored_listings_classifies_timeout_as_error_not_empty(self):
        release = threading.Event()

        def hang_fetch(*_args, **_kwargs):
            release.wait(5)
            return []

        try:
            with mock.patch.object(reddit_listing, "_fetch_one", side_effect=hang_fetch), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
                 http.capture_failures() as outer_failures:
                posts, state = reddit_keyless._scored_listings_with_state(
                    ["python"], depth="quick", deadline=time.monotonic() + 0.15
                )
        finally:
            release.set()

        assert posts == []
        assert state == reddit_health.ERROR
        assert outer_failures == []

    def test_scored_listings_ok_when_arctic_recovers_shreddit_403_without_leaking_error(self):
        arctic_post = _post_dict("arc1", score=88, num_comments=12, created_utc=1_000_000)

        def shreddit_403(_url, **_kwargs):
            http._record_failure(http.HTTPError("HTTP 403: Blocked", 403))
            return None

        with mock.patch.object(http, "get_text", side_effect=shreddit_403), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[arctic_post]), \
             http.capture_failures() as outer_failures:
            posts, state = reddit_keyless._scored_listings_with_state(["python"], depth="quick")

        assert len(posts) == 1
        assert state == reddit_health.OK
        assert outer_failures == []

    def test_deadline_skipped_targeted_listing_does_not_mark_empty(self):
        # Search fails with 403, web rungs fail, then deadline expires before targeted listings.
        def search_403(*_args, **_kwargs):
            reddit_search._blocked_reason = "HTTP 403: Blocked"
            http._record_failure(http.HTTPError("HTTP 403: Blocked", 403))
            return []

        def web_blocked(*_args, **_kwargs):
            web_search_keyless.RUNG_HEALTH.record("brave", web_search_keyless.BLOCKED)
            return reddit_discover.WebFloor(
                refs=[], queries=["site:reddit.com topic"], rungs={"brave": web_search_keyless.BLOCKED}
            )

        # Sequence _out_of_time: False for host, session, search, web; True for targeted listings.
        out_of_time_seq = iter([False, False, False, False, True, True, True])

        with mock.patch.object(reddit_keyless.reddit_search, "search", side_effect=search_403), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_blocked), \
             mock.patch.object(reddit_keyless, "_out_of_time", side_effect=lambda _d: next(out_of_time_seq, True)), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings") as listing_fetch, \
             reddit_keyless.lane_paths() as paths:
            posts = reddit_keyless._discover("topic", "quick", ["python"], deadline=time.monotonic() + 10.0)

        assert posts == []
        listing_fetch.assert_not_called()
        assert paths.get("search") == reddit_health.BLOCKED
        assert paths.get("brave") == reddit_health.BLOCKED
        assert "listing" not in paths
        assert reddit_health.every_path_failed(paths) is True

    @pytest.mark.parametrize("mode", ["targeted", "dedicated", "both"])
    def test_total_outage_on_targeted_or_dedicated_run_triggers_every_path_failed(self, mode: str):
        subs = ["python"] if mode in ("targeted", "both") else None
        dedicated = ["django"] if mode in ("dedicated", "both") else None

        def fail_403(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                "https://www.reddit.com/svc/shreddit/search/", 403, "Blocked", {}, None
            )

        def web_blocked(*_args, **_kwargs):
            web_search_keyless.RUNG_HEALTH.record("brave", web_search_keyless.BLOCKED)
            return reddit_discover.WebFloor(
                refs=[], queries=["site:reddit.com django"], rungs={"brave": web_search_keyless.BLOCKED}
            )

        with mock.patch.object(http.urllib.request, "urlopen", side_effect=fail_403), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_blocked), \
             reddit_keyless.lane_paths() as paths:
            posts = reddit_keyless._discover(
                "django", "quick", subs, dedicated_subreddits=dedicated
            )

        assert posts == []
        assert paths.get("brave") == reddit_health.BLOCKED
        assert paths.get("listing") == reddit_health.BLOCKED
        assert reddit_health.every_path_failed(paths) is True
        state, detail = reddit_health.failure_outcome(paths)
        assert state == health.UNREACHABLE
        assert "listing blocked" in detail

    def test_total_429_outage_on_targeted_run_reports_rate_limited_in_pipeline(self):
        def fail_429(*_args, **_kwargs):
            raise urllib.error.HTTPError(
                "https://www.reddit.com/svc/shreddit/search/", 429, "Too Many Requests", {}, None
            )

        def web_throttled(*_args, **_kwargs):
            web_search_keyless.RUNG_HEALTH.record("brave", web_search_keyless.THROTTLED)
            return reddit_discover.WebFloor(
                refs=[], queries=["site:reddit.com python"], rungs={"brave": web_search_keyless.THROTTLED}
            )

        subquery = schema.SubQuery(
            label="primary",
            search_query="python",
            ranking_query="python",
            sources=["reddit"],
        )
        with mock.patch.object(http.urllib.request, "urlopen", side_effect=fail_429), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_throttled):
            items, artifact = pipeline._retrieve_stream(
                topic="python",
                subquery=subquery,
                source="reddit",
                config={},
                depth="quick",
                date_range=("2026-10-25", "2026-11-24"),
                subreddits=["python"],
                runtime=schema.ProviderRuntime("local", "mock", "mock"),
                mock=False,
            )

        assert items == []
        assert artifact["_source_outcome"]["state"] == health.RATE_LIMITED
        assert "listing throttled" in artifact["_source_outcome"]["detail"]

    def test_clean_empty_targeted_run_stays_no_results_in_pipeline(self):
        def web_empty(*_args, **_kwargs):
            web_search_keyless.RUNG_HEALTH.record("brave", web_search_keyless.EMPTY)
            return reddit_discover.WebFloor(
                refs=[], queries=["site:reddit.com quiettopic"], rungs={"brave": web_search_keyless.EMPTY}
            )

        subquery = schema.SubQuery(
            label="primary",
            search_query="quiettopic",
            ranking_query="quiettopic",
            sources=["reddit"],
        )
        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_empty), \
             mock.patch.object(http, "get_text", return_value="<div></div>"), \
             mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}):
            items, artifact = pipeline._retrieve_stream(
                topic="quiettopic",
                subquery=subquery,
                source="reddit",
                config={},
                depth="quick",
                date_range=("2026-10-25", "2026-11-24"),
                subreddits=["python"],
                runtime=schema.ProviderRuntime("local", "mock", "mock"),
                mock=False,
            )

        assert items == []
        assert "_source_outcome" not in (artifact or {})

    def test_mixed_shreddit_429_and_arctic_timeout_or_500_reports_error_not_throttled(self):
        def shreddit_429(_url, **_kwargs):
            http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", 429))
            return None

        # (a) Shreddit 429 + Arctic timeout -> ERROR (not THROTTLED)
        with mock.patch.object(http, "get_text", side_effect=shreddit_429), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=http.DeadlineExceeded()):
            posts_t, state_t = reddit_keyless._scored_listings_with_state(["python"], depth="quick")
        assert posts_t == []
        assert state_t == reddit_health.ERROR

        http.reset_reddit_keyless_memo()
        reddit_arctic.reset_memo()

        # (b) Shreddit 429 + Arctic 500 -> ERROR (not THROTTLED)
        with mock.patch.object(http, "get_text", side_effect=shreddit_429), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=http.HTTPError("HTTP 500", 500)):
            posts_500, state_500 = reddit_keyless._scored_listings_with_state(["python"], depth="quick")
        assert posts_500 == []
        assert state_500 == reddit_health.ERROR

        http.reset_reddit_keyless_memo()
        reddit_arctic.reset_memo()

        # (c) Shreddit 429 + Arctic clean empty -> THROTTLED
        with mock.patch.object(http, "get_text", side_effect=shreddit_429), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_arctic.http, "get", return_value={"data": []}):
            posts_clean, state_clean = reddit_keyless._scored_listings_with_state(["python"], depth="quick")
        assert posts_clean == []
        assert state_clean == reddit_health.THROTTLED

        http.reset_reddit_keyless_memo()
        reddit_arctic.reset_memo()

        # (d) Shreddit 429 + Arctic 429 -> THROTTLED
        with mock.patch.object(http, "get_text", side_effect=shreddit_429), \
             mock.patch.object(http.time, "sleep"), \
             mock.patch.object(http, "_sleep_reddit_429_retry"), \
             mock.patch.object(reddit_arctic.http, "get", side_effect=http.HTTPError("HTTP 429", 429)):
            posts_both, state_both = reddit_keyless._scored_listings_with_state(["python"], depth="quick")
        assert posts_both == []
        assert state_both == reddit_health.THROTTLED

    def test_dedicated_throttled_plus_targeted_error_combines_to_error_and_avoids_rate_limited(self):
        assert reddit_keyless._combine_listing_states(
            [reddit_health.THROTTLED, reddit_health.ERROR], False
        ) == reddit_health.ERROR
        assert reddit_keyless._combine_listing_states(
            [reddit_health.THROTTLED, reddit_health.THROTTLED], False
        ) == reddit_health.THROTTLED

        # Production contract: _discover calls _scored_listings_with_state directly.
        assert not hasattr(reddit_keyless, "_call_scored_listings")
        assert not hasattr(reddit_keyless, "_ORIG_SCORED_LISTINGS")

        def search_429(*_args, **_kwargs):
            http._record_failure(http.HTTPError("HTTP 429: Too Many Requests", 429))
            return []

        def web_throttled(*_args, **_kwargs):
            web_search_keyless.RUNG_HEALTH.record("brave", web_search_keyless.THROTTLED)
            return reddit_discover.WebFloor(
                refs=[], queries=["site:reddit.com django"], rungs={"brave": web_search_keyless.THROTTLED}
            )

        calls = iter([
            ([], reddit_health.THROTTLED),  # dedicated
            ([], reddit_health.ERROR),      # targeted
        ])
        with mock.patch.object(reddit_keyless.reddit_search, "search", side_effect=search_429), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_throttled), \
             mock.patch.object(reddit_keyless, "_scored_listings_with_state", side_effect=lambda *_a, **_k: next(calls)), \
             reddit_keyless.lane_paths() as paths:
            posts = reddit_keyless._discover(
                "django", "quick", ["python"], dedicated_subreddits=["django"]
            )

        assert posts == []
        assert paths["listing"] == reddit_health.ERROR
        assert reddit_health.every_path_failed(paths) is True
        outcome_state, _detail = reddit_health.failure_outcome(paths)
        assert outcome_state == health.UNREACHABLE
        assert outcome_state != health.RATE_LIMITED


# ---------------------------------------------------------------------------
# HIGH-114: Arctic Shift score backfill preserves live num_comments & labels
# ---------------------------------------------------------------------------


class TestHigh114ScoreBackfillAndCommentIntegrity:
    def test_site_search_labels_live_counter_row_including_zero_score_post(self):
        posts, _cursor = reddit_search.parse_page(SEARCH_PAGE1_HTML, "ButcherBox")
        by_id = {p["metadata"]["post_id"]: p for p in posts}
        assert "1w9s403" in by_id
        zero_score_post = by_id["1w9s403"]
        assert zero_score_post["score"] == 0
        assert zero_score_post["num_comments"] == 62
        assert zero_score_post["engagement_source"] == reddit_refs.LIVE

    def test_site_search_without_vote_counter_does_not_claim_live_score(self):
        ctx = json.dumps({
            "action_info": {"type": "post"},
            "post": {"id": "t3_novote1", "title": "Unscored thread"},
            "subreddit": {"name": "python"},
        }).replace('"', "&quot;")
        html = (
            f'<search-telemetry-tracker data-faceplate-tracking-context="{ctx}" '
            f'view-events="search/view/post"><h2><a data-testid="post-title" '
            f'href="/r/python/comments/novote1/unscored_thread/">Unscored thread</a></h2>'
            f"</search-telemetry-tracker>"
            f'<faceplate-timeago ts="2026-11-20T10:00:00.000000+0000"></faceplate-timeago>'
            f'<div data-testid="search-counter-row">'
            f'<span><faceplate-number number="62" pretty></faceplate-number> comments</span>'
            f"</div>"
        )
        posts, _ = reddit_search.parse_page(html, "Unscored")
        assert len(posts) == 1
        assert posts[0]["score"] == 0
        assert posts[0]["num_comments"] == 62
        assert posts[0]["engagement_source"] == reddit_refs.UNKNOWN

    def test_discover_skips_arctic_backfill_for_live_zero_score_site_search_post(self):
        parsed, _ = reddit_search.parse_page(SEARCH_PAGE1_HTML, "ButcherBox")
        target = [p for p in parsed if p["metadata"]["post_id"] == "1w9s403"]
        assert len(target) == 1

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=target), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as fetch_scores:
            out = reddit_keyless._discover("ButcherBox", "quick", None)

        fetch_scores.assert_not_called()
        assert len(out) == 1
        assert out[0]["score"] == 0
        assert out[0]["num_comments"] == 62
        assert out[0]["engagement"]["num_comments"] == 62
        assert out[0]["engagement_source"] == reddit_refs.LIVE

    def test_apply_scores_never_reduces_existing_num_comments(self):
        post = _post_dict("1w9s403", score=0, num_comments=62)
        reddit_keyless._apply_scores(post, {"score": 1, "num_comments": 0})
        assert post["score"] == 1
        assert post["engagement"]["score"] == 1
        assert post["num_comments"] == 62
        assert post["engagement"]["num_comments"] == 62

        # Higher incoming count still upgrades.
        reddit_keyless._apply_scores(post, {"score": 5, "num_comments": 75})
        assert post["num_comments"] == 75
        assert post["engagement"]["num_comments"] == 75

        # None existing count safely adopts incoming count.
        missing = _post_dict("abc123", score=0, num_comments=0)
        missing["num_comments"] = None
        missing["engagement"]["num_comments"] = None
        reddit_keyless._apply_scores(missing, {"score": 9, "num_comments": 4})
        assert missing["num_comments"] == 4
        assert missing["engagement"]["num_comments"] == 4

    def test_arctic_backfill_preserves_comments_and_applies_label_archive_post(self):
        now = 1_760_000_000.0
        fresh_post = _post_dict(
            "fresh62",
            title="ButcherBox fresh discussion",
            score=0,
            num_comments=62,
            created_utc=now - 3600,  # 1h old -> ARCTIC_PENDING
        )
        old_post = _post_dict(
            "old62",
            title="ButcherBox older discussion",
            score=0,
            num_comments=62,
            created_utc=now - 5 * 86400,  # 5d old -> ARCTIC
        )

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[fresh_post, old_post]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(
                 reddit_keyless.reddit_arctic,
                 "fetch_scores",
                 return_value={
                     "fresh62": {"score": 1, "num_comments": 0},
                     "old62": {"score": 19, "num_comments": 0},
                 },
             ), \
             mock.patch.object(reddit_keyless.time, "time", return_value=now):
            out = reddit_keyless._discover("ButcherBox", "quick", None)

        by_id = {p["metadata"]["post_id"]: p for p in out}
        # Fresh post: score withheld as pending, num_comments preserved at 62.
        assert by_id["fresh62"]["engagement_source"] == reddit_refs.ARCTIC_PENDING
        assert by_id["fresh62"]["score"] is None
        assert by_id["fresh62"]["engagement"]["score"] is None
        assert by_id["fresh62"]["num_comments"] == 62
        assert by_id["fresh62"]["engagement"]["num_comments"] == 62
        # Settled post: archive score applied, num_comments preserved at 62.
        assert by_id["old62"]["engagement_source"] == reddit_refs.ARCTIC
        assert by_id["old62"]["score"] == 19
        assert by_id["old62"]["engagement"]["score"] == 19
        assert by_id["old62"]["num_comments"] == 62
        assert by_id["old62"]["engagement"]["num_comments"] == 62

        # Must remain eligible for enrich_source_items (not skipped as num_comments == 0).
        source_item = schema.SourceItem(
            item_id="R1",
            source="reddit",
            title=by_id["fresh62"]["title"],
            body="",
            url=by_id["fresh62"]["url"],
            author="op_user",
            published_at="2026-11-15",
            engagement=dict(by_id["fresh62"]["engagement"]),
            metadata={"post_id": "fresh62"},
        )
        enriched_payload = {
            "top_comments": [{"score": 14, "date": "2026-11-15", "author": "reader", "excerpt": "Great tip", "url": "https://reddit.com/c1", "depth": 0}],
            "comment_insights": ["Great tip"],
            "num_comments": 62,
        }
        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", return_value=enriched_payload) as fc:
            reddit_keyless.enrich_source_items([source_item], depth="quick")
        fc.assert_called_once()
        assert len(source_item.metadata["top_comments"]) == 1

    def test_zero_score_listing_card_labeled_live(self):
        html = (
            '<shreddit-post permalink="/r/python/comments/zcard1/zero_card/" '
            'post-title="Zero score active listing card" score="0" comment-count="19" '
            'author="alice" subreddit-name="python" '
            'created-timestamp="2026-11-20T10:00:00+00:00"></shreddit-post>'
        )
        cards = reddit_listing.parse_cards(html, query="Zero")
        assert len(cards) == 1
        assert cards[0]["score"] == 0
        assert cards[0]["num_comments"] == 19
        assert cards[0]["engagement_source"] == reddit_refs.LIVE

    def test_slugless_live_search_post_keeps_live_score_over_full_url_arctic_listing(self):
        # Search returns a slugless URL with live score=42; Arctic Shift returns
        # the full slug URL for the same post with stale score=1.
        live_search = _post_dict(
            "abc123",
            title="Python 3.14 free threading benchmark",
            sub="python",
            score=42,
            num_comments=15,
            engagement_source=reddit_refs.LIVE,
        )
        live_search["url"] = "https://www.reddit.com/r/python/comments/abc123/"

        arctic_listing = _post_dict(
            "abc123",
            title="Python 3.14 free threading benchmark",
            sub="python",
            score=1,
            num_comments=2,
            created_utc=1_700_000_000,  # >36h old -> labeled ARCTIC
        )
        arctic_listing["url"] = "https://www.reddit.com/r/python/comments/abc123/python_314_free_threading/"
        arctic_listing["why_relevant"] = "Reddit listing (arctic-shift)"

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[live_search]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[arctic_listing]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as fetch_scores:
            out = reddit_keyless._discover("Python 3.14", "quick", ["python"])

        fetch_scores.assert_not_called()
        assert len(out) == 1
        assert out[0]["score"] == 42
        assert out[0]["engagement"]["score"] == 42
        assert out[0]["num_comments"] == 15
        assert out[0]["engagement"]["num_comments"] == 15
        assert out[0]["engagement_source"] == reddit_refs.LIVE
        assert set(out[0]["provenance"]) == {"listing", "search"}

    def test_score_map_and_apply_scores_carry_source_and_never_mislable_archive_as_live(self):
        # Even when URLs have distinct thread_keys (matched only via score_map post_id),
        # an archive score must never overwrite a LIVE score, and must tag an unscored post ARCTIC.
        live_target = _post_dict("idlive1", score=42, num_comments=15, engagement_source=reddit_refs.LIVE)
        unscored_target = _post_dict("idarc1", score=0, num_comments=62)

        arctic_for_live = _post_dict("idlive1", score=1, num_comments=2, created_utc=1_700_000_000)
        arctic_for_live["url"] = "https://www.reddit.com/r/python/comments/other1/diff_url/"
        arctic_for_unscored = _post_dict("idarc1", score=17, num_comments=3, created_utc=1_700_000_000)
        arctic_for_unscored["url"] = "https://www.reddit.com/r/python/comments/other2/diff_url/"

        with mock.patch.object(
            reddit_keyless.reddit_search, "search", return_value=[live_target, unscored_target]
        ), mock.patch.object(
            reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor
        ), mock.patch.object(
            reddit_keyless.reddit_listing, "fetch_listings", return_value=[]
        ), mock.patch.object(
            reddit_keyless.reddit_arctic,
            "fetch_listings",
            return_value=[arctic_for_live, arctic_for_unscored],
        ), mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores", return_value={}):
            out = reddit_keyless._discover("topic", "quick", ["python"])

        by_url = {p["url"]: p for p in out}
        assert by_url[live_target["url"]]["score"] == 42
        assert by_url[live_target["url"]]["engagement_source"] == reddit_refs.LIVE
        assert by_url[unscored_target["url"]]["score"] == 17
        assert by_url[unscored_target["url"]]["num_comments"] == 62
        assert by_url[unscored_target["url"]]["engagement_source"] == reddit_refs.ARCTIC

        # Direct _apply_scores: live 0-score post refuses archive overwrite; archive post upgrades to LIVE.
        zero_live = _post_dict("z0", score=0, num_comments=10, engagement_source=reddit_refs.LIVE)
        reddit_keyless._apply_scores(
            zero_live, {"score": 1, "num_comments": 0, "engagement_source": reddit_refs.ARCTIC}
        )
        assert zero_live["score"] == 0
        assert zero_live["engagement_source"] == reddit_refs.LIVE

        archive_post = _post_dict("a1", score=1, num_comments=2, engagement_source=reddit_refs.ARCTIC)
        reddit_keyless._apply_scores(
            archive_post, {"score": 88, "num_comments": 20, "engagement_source": reddit_refs.LIVE}
        )
        assert archive_post["score"] == 88
        assert archive_post["num_comments"] == 20
        assert archive_post["engagement_source"] == reddit_refs.LIVE

    def test_missing_votes_never_erase_known_scores_and_measured_zero_beats_archive(self):
        # 1) Unscored search result (no vote counter -> UNKNOWN) merged with Arctic Shift (55, ARCTIC)
        #    must keep 55 + ARCTIC (both when parsed from HTML and when unlabeled placeholder 0).
        ctx = json.dumps({
            "action_info": {"type": "post"},
            "post": {"id": "t3_u111", "title": "Python unscored search"},
            "subreddit": {"name": "python"},
        }).replace('"', "&quot;")
        unscored_search_html = (
            f'<search-telemetry-tracker data-faceplate-tracking-context="{ctx}" '
            f'view-events="search/view/post"><h2><a data-testid="post-title" '
            f'href="/r/python/comments/u111/">Python unscored search</a></h2>'
            f"</search-telemetry-tracker>"
            f'<faceplate-timeago ts="2026-11-15T10:00:00+0000"></faceplate-timeago>'
            f'<div data-testid="search-counter-row">'
            f'<span><faceplate-number number="12" pretty></faceplate-number> comments</span></div>'
        )
        parsed_unscored_search, _ = reddit_search.parse_page(unscored_search_html, "Python")
        assert parsed_unscored_search[0]["engagement_source"] == reddit_refs.UNKNOWN

        arctic_copy = _post_dict("u111", title="Python unscored search", score=55, num_comments=10, created_utc=1_700_000_000)
        arctic_copy["url"] = "https://www.reddit.com/r/python/comments/u111/python_unscored_search/"

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=parsed_unscored_search), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[arctic_copy]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as fetch_scores:
            out1 = reddit_keyless._discover("Python", "quick", ["python"])
        fetch_scores.assert_not_called()
        assert len(out1) == 1
        assert out1[0]["score"] == 55
        assert out1[0]["engagement"]["score"] == 55
        assert out1[0]["num_comments"] == 12
        assert out1[0]["engagement_source"] == reddit_refs.ARCTIC

        # Also verify unlabeled placeholder zero search dict merged into ARCTIC keeps 55 + ARCTIC.
        unlabeled_zero = _post_dict("u111", score=0, num_comments=12)
        target_arctic = _post_dict("u111", score=55, num_comments=10, engagement_source=reddit_refs.ARCTIC)
        reddit_hydrate.merge_post(target_arctic, unlabeled_zero)
        assert target_arctic["score"] == 55
        assert target_arctic["engagement_source"] == reddit_refs.ARCTIC

        # 2) Unscored listing card (no score attr -> UNKNOWN) merged with measured search (19, LIVE)
        #    must adopt 19 + LIVE.
        unscored_card_html = (
            '<shreddit-post permalink="/r/python/comments/u222/python_card/" '
            'post-title="Python unscored listing" comment-count="8" '
            'author="alice" subreddit-name="python" '
            'created-timestamp="2026-11-15T10:00:00+00:00"></shreddit-post>'
        )
        parsed_unscored_card = reddit_listing.parse_cards(unscored_card_html, query="Python")
        assert parsed_unscored_card[0]["engagement_source"] == reddit_refs.UNKNOWN

        measured_search = _post_dict(
            "u222", title="Python unscored listing", score=19, num_comments=8, engagement_source=reddit_refs.LIVE
        )
        measured_search["url"] = "https://www.reddit.com/r/python/comments/u222/"

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[measured_search]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=parsed_unscored_card), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as fetch_scores2:
            out2 = reddit_keyless._discover("Python", "quick", ["python"])
        fetch_scores2.assert_not_called()
        assert len(out2) == 1
        assert out2[0]["score"] == 19
        assert out2[0]["engagement"]["score"] == 19
        assert out2[0]["engagement_source"] == reddit_refs.LIVE

        # 3) Measured zero-score search result (score=0, num_comments=62, LIVE) merged with
        #    Arctic Shift (score=1, num_comments=0, ARCTIC) must keep score=0, num_comments=62, LIVE.
        measured_zero = _post_dict(
            "u333", title="ButcherBox zero votes", score=0, num_comments=62, engagement_source=reddit_refs.LIVE
        )
        measured_zero["url"] = "https://www.reddit.com/r/Netherlands/comments/u333/"
        arctic_stub = _post_dict("u333", title="ButcherBox zero votes", score=1, num_comments=0, created_utc=1_700_000_000)
        arctic_stub["url"] = "https://www.reddit.com/r/Netherlands/comments/u333/butcherbox_zero_votes/"

        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[measured_zero]), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[arctic_stub]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as fetch_scores3:
            out3 = reddit_keyless._discover("ButcherBox", "quick", ["Netherlands"])
        fetch_scores3.assert_not_called()
        assert len(out3) == 1
        assert out3[0]["score"] == 0
        assert out3[0]["engagement"]["score"] == 0
        assert out3[0]["num_comments"] == 62
        assert out3[0]["engagement"]["num_comments"] == 62
        assert out3[0]["engagement_source"] == reddit_refs.LIVE

        # Unscored search post (UNKNOWN, no listing copy) remains eligible for Arctic backfill.
        solo_unscored, _ = reddit_search.parse_page(unscored_search_html, "Python")
        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=solo_unscored), \
             mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=_empty_web_floor), \
             mock.patch.object(
                 reddit_keyless.reddit_arctic, "fetch_scores", return_value={"u111": {"score": 27, "num_comments": 4}}
             ) as fetch_scores4:
            out4 = reddit_keyless._discover("Python", "quick", None)
        fetch_scores4.assert_called_once_with(["u111"])
        assert out4[0]["score"] == 27
        assert out4[0]["num_comments"] == 12
        assert out4[0]["engagement_source"] == reddit_refs.ARCTIC


# ---------------------------------------------------------------------------
# HIGH-121: Hyphenated attribute suffixes must never hijack _attr extraction
# ---------------------------------------------------------------------------


class TestHigh121HyphenatedAttrParsing:
    def test_live_listing_fixture_extracts_real_id_not_award_id(self):
        first_card = reddit_listing._POST_CARD.search(LISTING_CARDS_HTML)
        assert first_card is not None
        tag = first_card.group(0)
        assert reddit_listing._attr(tag, "award-id") == "award_obsessed_2"
        assert reddit_listing._attr(tag, "id") == "t3_1tq0zk7"
        assert reddit_listing._attr(tag, "author-id") == "t2_cc0n0rs5"
        assert reddit_listing._attr(tag, "author") == "AdSpecialist6598"

    def test_listing_attr_and_parse_cards_ignore_hyphenated_prefixes_and_respect_case(self):
        tag = (
            '<shreddit-post '
            'award-id="award_decoy" '
            'parent-permalink="/r/wrong/comments/bad999/wrong/" '
            'hide-score="true" '
            'sub-comment-count="0" '
            'data-author="decoy_bot" '
            'old-post-title="Wrong Title" '
            'data-note=\'score="999999" author="quoted_decoy"\' '
            'id="t3_real42" '
            'PERMALINK="/r/python/comments/real42/real_post/" '
            'SCORE="420" '
            'Comment-Count="88" '
            'AUTHOR=\'RealAuthor\' '
            'POST-TITLE="Real &amp; Clean Title" '
            'subreddit-name="python" '
            'created-timestamp="2026-11-18T12:00:00+00:00">'
            "</shreddit-post>"
        )
        assert reddit_listing._attr(tag, "id") == "t3_real42"
        assert reddit_listing._attr(tag, "permalink") == "/r/python/comments/real42/real_post/"
        assert reddit_listing._attr(tag, "score") == "420"
        assert reddit_listing._attr(tag, "comment-count") == "88"
        assert reddit_listing._attr(tag, "author") == "RealAuthor"
        assert reddit_listing._attr(tag, "post-title") == "Real & Clean Title"

        cards = reddit_listing.parse_cards(tag, query="Clean")
        assert len(cards) == 1
        card = cards[0]
        assert card["metadata"]["post_id"] == "real42"
        assert card["url"] == "https://www.reddit.com/r/python/comments/real42/real_post/"
        assert card["score"] == 420
        assert card["num_comments"] == 88
        assert card["author"] == "RealAuthor"
        assert card["title"] == "Real & Clean Title"

    def test_shreddit_attr_and_parse_comments_ignore_hyphenated_prefixes(self):
        html = (
            '<shreddit-comment-tree-stats sub-total-comments="999" total-comments="7"></shreddit-comment-tree-stats>'
            '<shreddit-comment '
            'data-created="2020-01-01T00:00:00+0000" '
            'created="2026-11-19T10:00:00+0000" '
            'data-author="AutoModerator" '
            'author="RealCommunityMember" '
            'parent-thingId="t1_parent_decoy" '
            'thingId="t1_realc1" '
            'nested-depth="9" '
            'depth="1" '
            'parent-permalink="/r/python/comments/abc/comment/parent_decoy/" '
            'permalink="/r/python/comments/abc/comment/realc1/" '
            'hide-score="true" '
            'data-meta=\'score="9999" author="fake"\' '
            'score="77">'
            '<div id="t1_realc1-comment-rtjson-content"><p>Genuine insight here.</p></div>'
            "</shreddit-comment>"
        )
        assert reddit_shreddit._total_comments(html) == 7
        comments = reddit_shreddit.parse_comments(html)
        assert len(comments) == 1
        c = comments[0]
        assert c["author"] == "RealCommunityMember"
        assert c["score"] == 77
        assert c["depth"] == 1
        assert c["date"] == "2026-11-19"
        assert c["permalink"] == "/r/python/comments/abc/comment/realc1/"
        assert c["body"] == "Genuine insight here."

    def test_attr_skips_boolean_flags_and_handles_missing_and_regex_metachars(self):
        tag = '<shreddit-post is-slim-card score="15" award-id="x">'
        assert reddit_listing._attr(tag, "is-slim-card") is None
        assert reddit_listing._attr(tag, "id") is None
        assert reddit_listing._attr(tag, "a.b") is None
        assert reddit_shreddit._attr(tag, "is-slim-card") == ""
        assert reddit_shreddit._attr(tag, "id") == ""
        assert reddit_shreddit._attr(tag, "a.b") == ""


# ---------------------------------------------------------------------------
# HIGH-122: _PARA tag boundaries, -comment-rtjson-content, and bounded _body_for
# ---------------------------------------------------------------------------


class TestHigh122ShredditParaAndBodyBounding:
    def test_para_ignores_pre_picture_path_param_and_self_closing_p(self):
        html = (
            '<div id="t1_tags1-post-rtjson-content">'
            "<pre><code>x = 1\ny = 2</code></pre>"
            '<picture><source srcset="a.webp"><img src="a.png" alt="pic"></picture>'
            '<svg><path d="M0 0 L10 10"></path></svg>'
            '<object><param name="movie" value="demo.swf"></object>'
            "<p/><p class='empty' />"
            '<p dir="auto">First real paragraph.</p>'
            '<P CLASS="second">Second real paragraph.</P>'
            "</div>"
        )
        body = reddit_shreddit._body_for(html, "t1_tags1")
        assert body == "First real paragraph. Second real paragraph."
        assert "x = 1" not in body
        assert "demo.swf" not in body

    def test_body_for_matches_comment_rtjson_content_when_post_variant_absent(self):
        html = (
            '<shreddit-comment author="alice" thingId="t1_onlyc" score="15" depth="0" '
            'permalink="/r/python/comments/1/comment/onlyc/">'
            '<DIV ID="t1_onlyc-comment-rtjson-content" slot="comment">'
            "<p>Body inside comment-rtjson-content only.</p>"
            "</DIV>"
            "</shreddit-comment>"
        )
        comments = reddit_shreddit.parse_comments(html)
        assert len(comments) == 1
        assert comments[0]["body"] == "Body inside comment-rtjson-content only."

    def test_body_for_preserves_nested_divs_and_stops_at_matching_close_div(self):
        html = (
            '<shreddit-comment author="parent" thingId="t1_nested" score="40" depth="0" '
            'permalink="/r/python/comments/1/comment/nested/">'
            '<div id="t1_nested-comment-rtjson-content" slot="comment">'
            '<div id="t1_nested-post-rtjson-content" dir="auto">'
            "<p>Opening thought.</p>"
            '<div class="quote-box"><div class="inner"><p>Quoted detail.</p></div><div class="spacer" /></div>'
            "<p>Closing thought after inner div.</p>"
            "</div>"
            "</div>"
            '<div class="ui-footer"><p>Reply Share Award Report</p></div>'
            '<shreddit-comment author="[deleted]" thingId="t1_delchild" score="1" depth="1" '
            'permalink="/r/python/comments/1/comment/delchild/">'
            "<p>Comment deleted by user</p>"
            "</shreddit-comment>"
            '<div class="collapsed-stub"><p>3 more replies</p></div>'
            "</shreddit-comment>"
        )
        body = reddit_shreddit._body_for(html, "t1_nested")
        assert body == "Opening thought. Quoted detail. Closing thought after inner div."
        assert "Reply Share" not in body
        assert "Comment deleted" not in body
        assert "3 more replies" not in body

    def test_body_for_handles_malformed_unclosed_div_before_child_or_sibling_comment(self):
        # Missing closing </div> before deleted child <shreddit-comment>
        unclosed_before_child = (
            '<shreddit-comment author="alice" thingId="t1_mal1" score="20" depth="0" '
            'permalink="/r/python/comments/1/comment/mal1/">'
            '<div id="t1_mal1-comment-rtjson-content">'
            "<p>Parent text before missing close.</p>"
            '<shreddit-comment author="[deleted]" thingId="t1_del1" score="1" depth="1" '
            'permalink="/r/python/comments/1/comment/del1/">'
            "<p>Swallowed deleted text</p>"
            "</shreddit-comment>"
            "</shreddit-comment>"
        )
        body1 = reddit_shreddit._body_for(unclosed_before_child, "t1_mal1")
        assert body1 == "Parent text before missing close."
        assert "Swallowed" not in body1

        # Missing closing </div> directly followed by another comment's rtjson div (no tag boundary)
        unclosed_before_next_rtjson = (
            '<div id="t1_mal2-comment-rtjson-content">'
            "<p>First comment text.</p>"
            '<div id="t1_mal3-comment-rtjson-content">'
            "<p>Second comment text.</p>"
            "</div>"
        )
        body2 = reddit_shreddit._body_for(unclosed_before_next_rtjson, "t1_mal2")
        assert body2 == "First comment text."
        assert "Second comment" not in body2

    def test_live_shreddit_fixture_still_parses_all_real_comments_cleanly(self):
        comments = reddit_shreddit.parse_comments(SHREDDIT_COMMENTS_HTML)
        assert len(comments) == 7
        by_id = {c["url"].rstrip("/").rsplit("/", 1)[-1]: c for c in comments}
        assert "$750" in by_id["ol8tp8n"]["body"]
        assert "pending ($712.49)" in by_id["olcy1iv"]["body"]
        assert "Family plan" == by_id["ol8undb"]["body"]

    def test_quoted_gt_in_div_comment_post_and_para_attributes(self):
        comment_html = (
            '<shreddit-comment data-hint="a > b" author="alice" thingId="t1_gt1" score="25" '
            'depth="0" permalink="/r/python/comments/1/comment/gt1/">'
            '<div data-note="x > y" id="t1_gt1-comment-rtjson-content">'
            '<p title="1 > 0">Hello <a title="2 > 1" href="/x">world</a></p>'
            '<div data-expr="m > n"><p>Nested after gt attr.</p></div>'
            "</div>"
            '<div class="ui-footer"><p>Must not leak</p></div>'
            "</shreddit-comment>"
        )
        comments = reddit_shreddit.parse_comments(comment_html)
        assert len(comments) == 1
        assert comments[0]["author"] == "alice"
        assert comments[0]["score"] == 25
        assert comments[0]["body"] == "Hello world Nested after gt attr."
        assert "Must not leak" not in comments[0]["body"]

        post_html = (
            '<shreddit-post post-title="Python > Ruby" score="100" comment-count="12" '
            'author="bob" subreddit-name="python" '
            'permalink="/r/python/comments/gtp1/python_gt_ruby/" '
            'created-timestamp="2026-11-20T00:00:00+00:00"></shreddit-post>'
        )
        cards = reddit_listing.parse_cards(post_html, query="Python")
        assert len(cards) == 1
        assert cards[0]["title"] == "Python > Ruby"
        assert cards[0]["score"] == 100
        assert cards[0]["num_comments"] == 12
        assert cards[0]["author"] == "bob"

    def test_self_closing_unquoted_attrs_and_unclosed_quote_linear_time(self):
        assert reddit_listing._attr("<shreddit-post score=42/>", "score") == "42"
        assert reddit_shreddit._attr("<shreddit-comment score=19/>", "score") == "19"
        assert (
            reddit_listing._attr('<shreddit-post permalink="/r/python/comments/1/x/"/>', "permalink")
            == "/r/python/comments/1/x/"
        )

        # Malformed unclosed quote must not catastrophically backtrack.
        malformed = '<div title="' + ("a" * 50_000)
        started = time.monotonic()
        assert reddit_shreddit._body_for(malformed, "t1_missing") == ""
        assert time.monotonic() - started < 1.0

    def test_parse_comments_indexes_body_divs_once_per_page_and_prefers_post_variant(self):
        parts = []
        for i in range(30):
            tid = f"t1_c{i:02d}"
            if i == 0:
                # Comment 0 has BOTH -comment-rtjson-content and nested -post-rtjson-content
                # plus a sibling footer inside -comment-rtjson-content to confirm -post- wins.
                body_html = (
                    f'<div id="{tid}-comment-rtjson-content">'
                    f'<div id="{tid}-post-rtjson-content"><p>Preferred post body 0.</p></div>'
                    f'<div class="extra-ui"><p>Outer wrapper noise</p></div>'
                    f"</div>"
                )
            else:
                body_html = f'<div id="{tid}-comment-rtjson-content"><p>Comment body {i}.</p></div>'
            parts.append(
                f'<shreddit-comment author="user{i}" thingId="{tid}" score="{100 - i}" '
                f'depth="0" permalink="/r/python/comments/1/comment/{tid}/">'
                f"{body_html}"
                f"</shreddit-comment>"
            )
        html = "".join(parts)

        real_div_open = reddit_shreddit._DIV_OPEN
        scan_count = 0

        class CountingPattern:
            def finditer(self, text):
                nonlocal scan_count
                scan_count += 1
                return real_div_open.finditer(text)

        with mock.patch.object(reddit_shreddit, "_DIV_OPEN", CountingPattern()):
            comments = reddit_shreddit.parse_comments(html, limit=30)

        assert scan_count == 1, f"expected single page scan, got {scan_count}"
        assert len(comments) == 30
        assert comments[0]["body"] == "Preferred post body 0."
        assert "Outer wrapper noise" not in comments[0]["body"]
        assert comments[15]["body"] == "Comment body 15."
        assert comments[29]["body"] == "Comment body 29."
