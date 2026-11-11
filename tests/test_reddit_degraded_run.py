"""Reddit on a network where site search is refused (ten live runs, 2026-11-10).

What those runs logged, and what each test pins:

    [ArcticShift] listing search failed r/waymo: HTTP 422: Unprocessable Entity   (x3 per URL)
    [ArcticShift] comments lookup failed t3_...: HTTP 422: Unprocessable Entity
        The archive's body was {"data":null,"error":"Timeout. Maybe slow down
        a bit"}: it answers all but one of a set of identical concurrent
        requests that way, and the lane sent each search once per subquery.
    [RedditSearch] r/X page 1 failed: HTTP 403: Blocked                            (every sub, every subquery)
    Reddit: 0 items (timeout: reddit timed out after 180s)
    Reddit absent from Partial Coverage with search blocked and no comments.
"""

import threading
import time
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from lib import health, http, pipeline, reddit_arctic, reddit_keyless, reddit_search, schema

SLOW_DOWN = "HTTP 422: Unprocessable Entity"


@pytest.fixture(autouse=True)
def _no_reddit_kept_thread_comments():
    # Overrides conftest's no-op: these tests run the real comment pass with
    # the comment fetch mocked.
    yield


@pytest.fixture(autouse=True)
def _no_arctic_network():
    # Overrides conftest's stub: these tests run the real archive adapter
    # with http.get mocked.
    reddit_arctic.reset_memo()
    yield
    reddit_arctic.reset_memo()


def _row(pid, sub, score=10):
    return {
        "id": pid, "title": f"Elon Musk thread {pid}", "score": score, "num_comments": 3,
        "subreddit": sub, "created_utc": 1794268800,  # 2026-11-09
        "permalink": f"/r/{sub}/comments/{pid}/x/", "author": "someone",
    }


class DuplicateIntolerantArchive:
    """Answers like the archive: a request identical to one in flight gets 422."""

    def __init__(self, hold=0.05):
        self.lock = threading.Lock()
        self.in_flight: set[str] = set()
        self.calls: list[str] = []
        self.hold = hold

    def __call__(self, url, **_kwargs):
        with self.lock:
            self.calls.append(url)
            duplicate = url in self.in_flight
            self.in_flight.add(url)
        if duplicate:
            http._raise(http.HTTPError(SLOW_DOWN, status_code=422))
        try:
            time.sleep(self.hold)
            sub = parse_qs(urlsplit(url).query).get("subreddit", ["x"])[0]
            return {"data": [_row(f"p{len(self.calls)}", sub)]}
        finally:
            with self.lock:
                self.in_flight.discard(url)


class TestArchiveSharedAcrossSubqueries:
    def test_four_subqueries_send_each_listing_search_once(self):
        archive = DuplicateIntolerantArchive()
        results: list[list] = []
        failures_seen: list[int] = []

        def one_subquery():
            with http.capture_failures() as failures:
                results.append(reddit_arctic.fetch_listings(
                    ["elonmusk", "teslamotors"], from_date="2026-10-12", to_date="2026-11-11",
                ))
            failures_seen.append(len(failures))

        with mock.patch.object(reddit_arctic.http, "get", side_effect=archive), \
             mock.patch.object(reddit_arctic, "PACE_SECONDS", 0):
            threads = [threading.Thread(target=one_subquery) for _ in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]

        # 2 subreddits x 4 window slices, asked once each, never refused.
        assert len(archive.calls) == len(set(archive.calls)) == 8
        assert failures_seen == [0, 0, 0, 0]
        assert all(len(posts) == 8 for posts in results)

    def test_a_refused_slice_is_asked_for_again_after_the_others(self):
        # The archive sheds load with the same 422 on some lone requests too,
        # and a retry right after one tends to be refused again.
        asked: list[str] = []
        refuse_once = {"waymo"}

        def get(url, **_kwargs):
            sub = parse_qs(urlsplit(url).query)["subreddit"][0]
            asked.append(sub)
            if sub in refuse_once:
                refuse_once.discard(sub)
                http._raise(http.HTTPError(SLOW_DOWN, status_code=422))
            return {"data": [_row(f"{sub}1", sub)]}

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get), \
             mock.patch.object(reddit_arctic.time, "sleep"), \
             http.capture_failures() as failures:
            posts = reddit_arctic.fetch_listings(["waymo", "robotaxi"])

        assert asked == ["waymo", "robotaxi", "waymo"]
        assert sorted(post["subreddit"] for post in posts) == ["robotaxi", "waymo"]
        # The recovered refusal is not reported as a lost sub-request.
        assert failures == []

    def test_a_refused_slice_with_no_time_left_is_reported(self, capsys):
        clock = [0.0]

        def get(_url, **_kwargs):
            clock[0] += 50  # past the 45s budget
            http._raise(http.HTTPError(SLOW_DOWN, status_code=422))

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get), \
             mock.patch.object(reddit_arctic.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_arctic.time, "sleep"), \
             http.capture_failures() as failures:
            assert reddit_arctic.fetch_listings(["waymo"]) == []

        assert [failure.status_code for failure in failures] == [422]
        assert f"listing search failed r/waymo: {SLOW_DOWN}" in capsys.readouterr().err

    def test_a_422_twice_is_reported_and_not_memoized(self):
        def get(_url, **_kwargs):
            http._raise(http.HTTPError(SLOW_DOWN, status_code=422))

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get) as archive, \
             mock.patch.object(reddit_arctic.time, "sleep"), \
             http.capture_failures() as failures:
            assert reddit_arctic.fetch_listings(["waymo"]) == []
            assert reddit_arctic.fetch_listings(["waymo"]) == []

        assert archive.call_count == 4
        assert [failure.status_code for failure in failures] == [422, 422]

    def test_comment_lookup_is_shared_and_retried(self):
        comment = {"author": "reader", "body": "worth reading", "score": 7,
                   "permalink": "/r/elonmusk/comments/abc/x/c1/", "created_utc": 1794268800}
        answers = iter([http.HTTPError(SLOW_DOWN, status_code=422), {"data": [comment]}])

        def get(_url, **_kwargs):
            answer = next(answers)
            if isinstance(answer, Exception):
                http._raise(answer)
            return answer

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get) as archive, \
             mock.patch.object(reddit_arctic.time, "sleep"):
            first = reddit_arctic.fetch_post_comments("abc")
            second = reddit_arctic.fetch_post_comments("t3_abc")

        assert [c["author"] for c in first] == ["reader"]
        assert second == first
        assert archive.call_count == 2  # the refusal, the retry; the second lookup is free

    def test_deadline_drops_the_oldest_week_not_the_newest(self):
        asked: list[str] = []
        clock = [0.0]

        def get(url, **_kwargs):
            asked.append(parse_qs(urlsplit(url).query)["before"][0])
            clock[0] += 30  # two requests fit in the 45s budget
            return {"data": []}

        with mock.patch.object(reddit_arctic.http, "get", side_effect=get), \
             mock.patch.object(reddit_arctic.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_arctic.time, "sleep"):
            reddit_arctic.fetch_listings(["elonmusk"], from_date="2026-10-12", to_date="2026-11-11")

        assert asked == ["2026-11-11", "2026-11-05"]


class BlockedSearch:
    def __init__(self):
        self.requests: list[str] = []

    def __call__(self, url, **_kwargs):
        self.requests.append(url)
        http._record_failure(http.HTTPError("HTTP 403: Blocked", 403))
        return None


@pytest.fixture
def blocked_search():
    fake = BlockedSearch()
    with mock.patch.object(http, "get_text", side_effect=fake), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
         mock.patch.object(http.time, "sleep"):
        yield fake


SUBS = ["elonmusk", "EnoughMuskSpam", "teslamotors", "teslainvestorsclub", "SpaceX"]


class TestBlockedSiteSearchFailsFast:
    def test_first_block_stops_the_remaining_subreddits(self, blocked_search, capsys):
        with mock.patch.object(reddit_search, "MAX_WORKERS", 1):
            assert reddit_search.search("Elon Musk", subreddits=SUBS) == []

        # One refused request, not one per subreddit plus the global search.
        assert len(blocked_search.requests) == 1
        assert reddit_search.failed_reason() == "HTTP 403: Blocked"
        assert "site search is blocked for this network" in capsys.readouterr().err

    def test_later_subqueries_of_the_command_do_not_ask_again(self, blocked_search):
        with mock.patch.object(reddit_search, "MAX_WORKERS", 1):
            for _subquery in range(4):
                assert reddit_search.search("Elon Musk", subreddits=SUBS) == []

        assert len(blocked_search.requests) == 1

    def test_a_private_subreddit_403_does_not_stop_the_others(self):
        requests: list[str] = []

        def get_text(url, **_kwargs):
            requests.append(url)
            http._record_failure(http.HTTPError("HTTP 403: Forbidden", 403))
            return None

        with mock.patch.object(http, "get_text", side_effect=get_text), \
             mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
             mock.patch.object(http.time, "sleep"):
            reddit_search.search("Elon Musk", subreddits=SUBS, depth="quick")

        assert len(requests) == len(SUBS) + 1
        assert reddit_search.failed_reason() is None


def _post(pid):
    return {
        "id": "", "title": f"Elon Musk thread {pid}",
        "url": f"https://www.reddit.com/r/elonmusk/comments/{pid}/x/",
        "score": 50, "num_comments": 9, "subreddit": "elonmusk", "date": "2026-11-09",
        "engagement": {"score": 50, "num_comments": 9}, "relevance": 0.9,
        "metadata": {"post_id": pid},
    }


class TestLaneReturnsWhatItHasInsideItsBudget:
    def test_spent_budget_skips_enrichment_and_keeps_the_threads(self, capsys):
        posts = [_post("a"), _post("b"), _post("c")]
        with mock.patch.object(reddit_keyless, "_discover", return_value=posts), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments") as comments, \
             reddit_keyless.lane_deadline(time.monotonic() + 0.5):
            out = reddit_keyless.search_and_enrich(
                "Elon Musk", "2026-10-12", "2026-11-11", subreddits=["elonmusk"],
            )

        assert len(out) == 3
        comments.assert_not_called()
        assert "comment enrichment skipped: lane budget spent" in capsys.readouterr().err

    def test_spent_budget_skips_the_later_discovery_stages(self):
        listing = [_post("a"), _post("b")]
        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=listing), \
             mock.patch.object(reddit_keyless.reddit_search, "search") as search, \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings") as archive, \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores") as scores:
            out = reddit_keyless._discover(
                "Elon Musk", "default", ["teslamotors"], dedicated_subreddits=["elonmusk"],
                from_date="2026-10-12", to_date="2026-11-11", deadline=time.monotonic() + 0.5,
            )

        # The dedicated subreddit's listing came back; nothing slower is started.
        assert [post["metadata"]["post_id"] for post in out] == ["a", "b"]
        search.assert_not_called()
        archive.assert_not_called()
        scores.assert_not_called()

    def test_enrichment_wait_is_capped_by_the_lane_deadline(self):
        release = threading.Event()

        def slow_comments(_url, deadline=None):
            release.wait(5)
            return {"top_comments": [], "comment_insights": [], "num_comments": None}

        started = time.monotonic()
        try:
            with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", side_effect=slow_comments):
                out = reddit_keyless._enrich(
                    [_post("a"), _post("b")], "default", lane_deadline=time.monotonic() + 2.3,
                )
        finally:
            release.set()

        assert len(out) == 2
        assert time.monotonic() - started < 4

    def test_pipeline_gives_the_lane_a_deadline_before_the_stream_wait_ends(self):
        seen: list[float | None] = []

        def public(*_args, **_kwargs):
            seen.append(reddit_keyless._lane_deadline.get())
            return [_post("a")]

        research_deadline = time.monotonic() + 180
        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=public):
            _stream({"_research_deadline": research_deadline})

        assert seen == [research_deadline - pipeline.REDDIT_LANE_RETURN_MARGIN_SECONDS]


def test_lane_that_starts_with_no_budget_is_a_timeout_not_no_results():
    with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
        items, artifact = _stream({"_research_deadline": time.monotonic() + 5})

    assert items == []
    assert artifact["_source_outcome"]["state"] == health.TIMEOUT


def test_lane_that_finds_nothing_in_time_is_still_no_results():
    with mock.patch("lib.reddit_public.search_reddit_public", return_value=[]):
        items, artifact = _stream({"_research_deadline": time.monotonic() + 180})

    assert items == []
    assert "_source_outcome" not in (artifact or {})


def _stream(config):
    return pipeline._retrieve_stream(
        topic="Elon Musk",
        subquery=schema.SubQuery(
            label="primary", search_query="Elon Musk", ranking_query="Elon Musk", sources=["reddit"],
        ),
        source="reddit", config=config, depth="quick",
        date_range=("2026-10-12", "2026-11-11"),
        runtime=schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
        ),
        mock=False,
    )


class TestBlockedSearchIsReportedAsPartial:
    def test_listing_only_threads_mark_reddit_partial(self):
        def public(*_args, **_kwargs):
            reddit_search._failed_reason = "HTTP 403: Blocked"
            return [_post("a"), {**_post("b"), "top_comments": [{"author": "x"}]}]

        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=public):
            items, artifact = _stream({})

        assert len(items) == 2
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == health.PARTIAL
        assert outcome["detail"] == (
            "Reddit site search was blocked (HTTP 403: Blocked); threads come from "
            "subreddit listings only"
        )

    def test_working_search_leaves_reddit_ok(self):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=[_post("a")]):
            items, artifact = _stream({})

        assert len(items) == 1
        assert "_source_outcome" not in (artifact or {})

    def test_partial_outcome_reaches_the_bundle_as_degraded(self):
        bundle = schema.RetrievalBundle()
        bundle.add_items("primary", "reddit", [
            schema.SourceItem(item_id="R1", source="reddit", title="t", body="", url="https://reddit.com/x"),
        ])

        bundle.record_failure("reddit", health.PARTIAL, "Reddit site search was blocked")

        assert bundle.source_status["reddit"].state == health.PARTIAL
        assert bundle.source_status["reddit"].items_returned == 1


# --- ScrapeCreators backfill (the owner's runs had a key and no backfill) ---
#
# "Fable 5.2": the free lane returned dozens of listing threads from the
# targeted subreddits, so the raw count never fell under the 5-item floor;
# the stream's relevance prune then kept one. "Elon Musk": the free lane was
# still running when the 180s wait ended, so the backfill, which comes after
# it in the same stream, never got a turn.

KEY = {"SCRAPECREATORS_API_KEY": "dummy-key"}


def _off_topic(pid):
    return {**_post(pid), "title": f"Weekly megathread {pid}", "relevance": 0.0}


def _sc_item(pid):
    return {"url": f"https://www.reddit.com/r/elonmusk/comments/{pid}/x/", "title": f"Elon Musk {pid}"}


class TestBackfillRunsWhenFreeResultIsThin:
    def _run(self, config, public, sc_side_effect=None, sc_items=()):
        with mock.patch("lib.reddit_public.search_reddit_public", return_value=public), \
             mock.patch("lib.reddit.search_and_enrich", return_value={"raw": 1},
                        side_effect=sc_side_effect) as sc, \
             mock.patch("lib.reddit.parse_reddit_response", return_value=list(sc_items)):
            items, artifact = _stream(config)
        return items, artifact, sc

    def test_many_off_topic_listing_threads_do_not_hide_a_thin_result(self, capsys):
        free = [_post("a")] + [_off_topic(f"o{i}") for i in range(40)]

        items, artifact, sc = self._run(KEY, free, sc_items=[_sc_item("z")])

        sc.assert_called_once()
        assert len(items) == 42
        assert "41 free items" in artifact["_source_outcome_detail"]
        assert "(1 on topic, below the 5-item floor)" in capsys.readouterr().err

    def test_five_on_topic_threads_spend_nothing(self):
        free = [_post(c) for c in "abcde"] + [_off_topic("o1")]

        _items, _artifact, sc = self._run(KEY, free, sc_items=[_sc_item("z")])

        sc.assert_not_called()

    def test_no_key_never_backfills(self):
        _items, _artifact, sc = self._run({}, [_off_topic("o1")], sc_items=[_sc_item("z")])

        sc.assert_not_called()

    def test_free_lane_stops_early_enough_to_leave_the_backfill_a_turn(self):
        seen: list[float | None] = []

        def public(*_args, **_kwargs):
            seen.append(reddit_keyless._lane_deadline.get())
            return [_post(c) for c in "abcde"]

        research_deadline = time.monotonic() + 180
        with mock.patch("lib.reddit_public.search_reddit_public", side_effect=public):
            _stream({**KEY, "_research_deadline": research_deadline})

        assert seen == [
            research_deadline - pipeline.REDDIT_LANE_RETURN_MARGIN_SECONDS
            - pipeline.REDDIT_SC_BACKFILL_RESERVE_SECONDS
        ]

    def test_free_path_that_timed_out_with_nothing_is_backfilled(self, capsys):
        # 60s left: past the free lane's deadline (65s before the end), with
        # room for the backfill.
        config = {**KEY, "_research_deadline": time.monotonic() + 60}

        items, _artifact, sc = self._run(config, [], sc_items=[_sc_item("z")])

        sc.assert_called_once()
        assert [item["title"] for item in items] == ["Elon Musk z"]
        assert "Free path ran out of time with no threads; backfilling" in capsys.readouterr().err

    def test_slow_backfill_is_abandoned_and_the_free_threads_are_kept(self, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_MIN_SECONDS", 0.1)
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_RETURN_MARGIN_SECONDS", 0.0)
        monkeypatch.setattr(pipeline, "REDDIT_SC_BACKFILL_RESERVE_SECONDS", 0.0)
        monkeypatch.setattr(pipeline, "REDDIT_LANE_RETURN_MARGIN_SECONDS", 0.0)

        def slow(*_args, **_kwargs):
            release.wait(5)
            return {"raw": 1}

        started = time.monotonic()
        try:
            items, artifact, _sc = self._run(
                {**KEY, "_research_deadline": time.monotonic() + 0.5}, [_post("a")],
                sc_side_effect=slow,
            )
        finally:
            release.set()

        assert time.monotonic() - started < 3
        assert [item["metadata"]["post_id"] for item in items] == ["a"]
        assert "ScrapeCreators backfill failed after 1 free items" in artifact["_source_outcome_detail"]
        assert "did not finish" in artifact["_source_outcome_detail"]


# --- Every kept thread carries its top comments ---
#
# Lane-time enrichment picks its 8 slots before the pipeline's relevance
# prune and ranking; in the 2026-11-10 runs 3 to 5 of the final 12 threads
# had comments.


def _kept(pid, num_comments=9, top_comments=None):
    return schema.SourceItem(
        item_id=pid, source="reddit", title=f"Elon Musk thread {pid}", body="",
        url=f"https://www.reddit.com/r/elonmusk/comments/{pid}/x/", container="elonmusk",
        engagement={"score": 50, "num_comments": num_comments},
        metadata={"top_comments": top_comments} if top_comments else {},
    )


def _comments_for(url, deadline=None):
    pid = url.rstrip("/").split("/")[-2]
    return {
        "top_comments": [{
            "score": 321, "date": "2026-11-09", "author": f"reader_{pid}",
            "excerpt": f"Top comment on {pid}",
            "url": f"https://reddit.com/r/elonmusk/comments/{pid}/x/c_{pid}/",
        }],
        "comment_insights": [f"Insight on {pid}"],
        "num_comments": 44,
    }


class TestKeptThreadsGetTheirTopComments:
    def test_every_kept_thread_without_comments_is_fetched_in_rank_order(self):
        already = [{"score": 5, "author": "early", "excerpt": "kept", "url": "https://reddit.com/c"}]
        items = [_kept("a", top_comments=already)] + [_kept(f"t{i}") for i in range(11)]

        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_comments_for) as fetch:
            reddit_keyless.enrich_source_items(items)

        assert fetch.call_count == 11  # the thread the lane enriched costs nothing
        assert all(item.metadata.get("top_comments") for item in items)
        assert items[0].metadata["top_comments"] == already
        top = items[3].metadata["top_comments"][0]
        assert (top["author"], top["score"], top["url"]) == (
            "reader_t2", 321, "https://reddit.com/r/elonmusk/comments/t2/x/c_t2/",
        )
        assert items[3].metadata["comment_insights"] == ["Insight on t2"]
        assert items[3].engagement["num_comments"] == 44

    def test_cap_goes_to_the_highest_ranked_threads(self):
        items = [_kept(f"t{i}") for i in range(20)]

        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_comments_for):
            reddit_keyless.enrich_source_items(items, depth="default")

        with_comments = [bool(item.metadata.get("top_comments")) for item in items]
        assert with_comments == [True] * 12 + [False] * 8

    def test_thread_with_no_comments_is_not_fetched(self):
        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments") as fetch:
            reddit_keyless.enrich_source_items([_kept("a", num_comments=0)])

        fetch.assert_not_called()

    def test_no_time_left_fetches_nothing(self):
        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments") as fetch:
            reddit_keyless.enrich_source_items([_kept("a")], deadline=time.monotonic() + 0.5)

        fetch.assert_not_called()

    def test_slow_fetch_does_not_hold_the_report(self, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(reddit_keyless, "FINAL_ENRICH_BUDGET", 2.2)

        def fetch(url, deadline=None):
            if url.endswith("/slow/x/"):
                release.wait(5)
            return _comments_for(url)

        items = [_kept("fast"), _kept("slow")]
        started = time.monotonic()
        try:
            with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", side_effect=fetch):
                reddit_keyless.enrich_source_items(items)
        finally:
            release.set()

        assert time.monotonic() - started < 4
        assert items[0].metadata.get("top_comments") and not items[1].metadata.get("top_comments")

    def test_finalize_enriches_the_surviving_reddit_threads(self):
        items = [_kept(f"t{i}") for i in range(3)]
        titles = ["Starship flight 15 catch", "Tesla pay package vote passes", "Neuralink trial update"]
        for rank, (item, title) in enumerate(zip(items, titles)):
            item.title = title  # distinct, so the text dedupe keeps all three
            item.local_rank_score = 1.0 - rank / 10

        with mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               side_effect=_comments_for):
            finalized = pipeline._finalize_items_by_source({"reddit": items}, topic="Elon Musk", config={})

        assert [bool(item.metadata.get("top_comments")) for item in finalized["reddit"]] == [True] * 3


# --- Review findings on the lane deadline (PR #1408) ---


class TestLaneDeadlineIsHard:
    def test_listing_that_spends_the_budget_still_returns_its_threads(self):
        # The shreddit listing returns with the budget gone: the archive
        # supplement is skipped and the threads already fetched come back.
        deadline = time.monotonic() + 3

        def slow_listing(*_args, **_kwargs):
            time.sleep(1.5)  # under MIN_STAGE_SECONDS left afterwards
            return [_post("a"), _post("b")]

        with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", side_effect=slow_listing), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings") as archive:
            posts = reddit_keyless._scored_listings(["elonmusk"], deadline=deadline)

        archive.assert_not_called()
        assert [post["metadata"]["post_id"] for post in posts] == ["a", "b"]

    def test_running_site_search_is_abandoned_at_the_deadline(self):
        release = threading.Event()

        def slow_search(*_args, **_kwargs):
            release.wait(10)
            return [_post("late")]

        started = time.monotonic()
        try:
            with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                                   return_value=[_post("a"), _post("b")]), \
                 mock.patch.object(reddit_keyless.reddit_search, "search", side_effect=slow_search), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores", return_value={}):
                out = reddit_keyless._discover(
                    "Elon Musk", "default", None, dedicated_subreddits=["elonmusk"],
                    from_date="2026-10-12", to_date="2026-11-11", deadline=time.monotonic() + 3,
                )
        finally:
            release.set()

        # The search started with budget left and never came back: the lane
        # stops waiting at its deadline and keeps the dedicated threads.
        assert time.monotonic() - started < 5
        assert [post["metadata"]["post_id"] for post in out] == ["a", "b"]

    @pytest.mark.parametrize("slow", ["listing", "scores"])
    def test_running_listing_and_score_calls_are_abandoned_too(self, slow):
        release = threading.Event()

        def hang(*_args, **_kwargs):
            release.wait(10)
            return [] if slow == "listing" else {}

        unscored = {**_post("s"), "score": 0, "engagement": {"score": 0, "num_comments": 0}}
        listing = hang if slow == "listing" else (lambda *_a, **_k: [])
        scores = hang if slow == "scores" else (lambda *_a, **_k: {})
        started = time.monotonic()
        try:
            with mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", side_effect=listing), \
                 mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[unscored]), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
                 mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores", side_effect=scores):
                out = reddit_keyless._discover(
                    "Elon Musk", "default", ["elonmusk"],
                    from_date="2026-10-12", to_date="2026-11-11", deadline=time.monotonic() + 3,
                )
        finally:
            release.set()

        assert time.monotonic() - started < 5
        assert [post["metadata"]["post_id"] for post in out] == ["s"]
