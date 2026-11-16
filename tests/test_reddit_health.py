"""Reddit path health, the served-path detail, and doctor's Reddit rows (U3).

Every network seam is mocked: site search answers through a patched
``http.get_text``, the web floor and Arctic hydration through patched
``reddit_discover.web_refs`` / ``reddit_arctic`` calls, and doctor's probe
through a patched ``bounded_get.get``. The health cache lives in the test's
own config dir (conftest's ``_isolated_config``).
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

from lib import (
    doctor, env, health, http, pipeline, reddit_arctic, reddit_discover, reddit_health,
    reddit_keyless, reddit_refs, reddit_search, schema, web_search_keyless,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TODAY = datetime.now(timezone.utc).date()
FROM = (TODAY - timedelta(days=30)).isoformat()
TO = TODAY.isoformat()
OLD_UTC = int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp())


def _new_run():
    """What last30days.py does between commands in one process."""
    reddit_search.reset_run_state()
    reddit_discover.reset_run_state()
    reddit_health.reset_run_state()
    web_search_keyless.reset_rung_health()


class SearchAnswers:
    """Site search through http.get_text: each call records ``status``."""

    def __init__(self, status, message):
        self.status = status
        self.message = message
        self.requests: list[str] = []

    def __call__(self, url, **_kwargs):
        self.requests.append(url)
        http._record_failure(http.HTTPError(self.message, self.status))
        return None


@pytest.fixture
def search_403():
    fake = SearchAnswers(403, "HTTP 403: Blocked")
    with mock.patch.object(http, "get_text", side_effect=fake), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
         mock.patch.object(http.time, "sleep"):
        yield fake


@pytest.fixture
def search_429():
    fake = SearchAnswers(429, "HTTP 429: Too Many Requests")
    with mock.patch.object(http, "get_text", side_effect=fake), \
         mock.patch.object(http.REDDIT_KEYLESS_LIMITER, "acquire"), \
         mock.patch.object(http, "_sleep_reddit_429_retry"), \
         mock.patch.object(http.time, "sleep"):
        yield fake


def _ref(pid, title):
    ref = reddit_refs.canonical_thread(f"https://www.reddit.com/r/elonmusk/comments/{pid}/x/")
    ref.provenance.add("web")
    ref.title = title
    return ref


def _archived(pid, title, comments=9):
    return reddit_arctic._normalize_listing_row({
        "id": pid, "title": title, "score": 120, "num_comments": comments,
        "subreddit": "elonmusk", "created_utc": OLD_UTC,
        "permalink": f"/r/elonmusk/comments/{pid}/x/", "author": "someone",
    })


def _floor(pids, rungs=None):
    """Patch the web floor (Brave found ``pids``) and Arctic hydration."""
    rungs = rungs or {"brave": web_search_keyless.OK}
    refs = [_ref(pid, f"Elon Musk thread {pid}") for pid in pids]
    rows = {pid: _archived(pid, f"Elon Musk thread {pid}") for pid in pids}

    def web_refs(topic, **_kwargs):
        for rung, state in rungs.items():
            web_search_keyless.RUNG_HEALTH.record(rung, state)
        return reddit_discover.WebFloor(
            refs=[_ref(r.post_id, r.title) for r in refs],
            queries=[f"site:reddit.com {topic}"], rungs=dict(rungs),
        )

    return (
        mock.patch.object(reddit_keyless.reddit_discover, "web_refs", side_effect=web_refs),
        mock.patch.object(
            reddit_keyless.reddit_arctic, "fetch_posts_by_ids",
            side_effect=lambda ids, **_k: ({i: dict(rows[i]) for i in ids if i in rows}, "ok"),
        ),
        mock.patch.object(reddit_keyless.reddit_arctic, "fetch_comment_tree", return_value=[]),
        mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments", return_value={}),
    )


def _stream(config=None, limited=None, subreddits=None):
    return pipeline._retrieve_stream(
        topic="Elon Musk",
        subquery=schema.SubQuery(
            label="primary", search_query="Elon Musk", ranking_query="Elon Musk",
            sources=["reddit"],
        ),
        source="reddit", config=dict(config or {}), depth="quick",
        date_range=(FROM, TO), subreddits=subreddits,
        runtime=schema.ProviderRuntime(
            reasoning_provider="mock", planner_model="mock", rerank_model="mock",
        ),
        mock=False, rate_limited_sources=limited if limited is not None else set(),
    )


def _set_entry_age(path, hours):
    data = json.loads(reddit_health.cache_path().read_text())
    at = datetime.now(timezone.utc) - timedelta(hours=hours)
    data["paths"][path]["at"] = at.isoformat()
    reddit_health.cache_path().write_text(json.dumps(data))


# --- the persisted cache -----------------------------------------------------

class TestPersistedSkips:
    def test_search_page_403_writes_a_six_hour_skip_the_next_run_honors(self, search_403):
        assert reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None) == []
        assert len(search_403.requests) == 1

        entry = reddit_health.entries()["search"]
        assert entry["state"] == reddit_health.BLOCKED
        assert entry["cookie_fp"] is None  # schema carries it; nothing keyless writes it
        until = reddit_health.skip_until("search")
        assert timedelta(hours=5, minutes=59) < until - datetime.now(timezone.utc) <= timedelta(hours=6)

        # Next run: skipped without a request.
        _new_run()
        with reddit_keyless.lane_paths() as paths:
            assert reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None) == []
        assert len(search_403.requests) == 1
        assert paths["search"] == reddit_health.SKIPPED_CACHED

        # Seven hours later the skip has lapsed and search is asked again.
        _set_entry_age("search", 7)
        _new_run()
        reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None)
        assert len(search_403.requests) == 2

    def test_429_skips_the_path_for_this_run_only(self, search_429):
        with reddit_keyless.lane_paths() as paths:
            reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None)
        assert paths["search"] == reddit_health.THROTTLED
        asked = len(search_429.requests)
        assert asked >= 1
        assert "search" not in reddit_health.entries()  # never persisted

        # Same run: no further request.
        reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None)
        assert len(search_429.requests) == asked

        # Next run asks again.
        _new_run()
        reddit_keyless._site_search("Elon Musk", "quick", None, FROM, TO, None)
        assert len(search_429.requests) > asked

    def test_a_skip_under_another_cookie_fingerprint_is_ignored(self):
        reddit_health.record("search", reddit_health.BLOCKED, "logged out", cookie_fp="a" * 16)
        assert reddit_health.skip_until("search", cookie_fp="a" * 16) is not None
        assert reddit_health.skip_until("search", cookie_fp="b" * 16) is None
        assert reddit_health.skip_until("search") is None

    def test_an_ok_answer_clears_a_skip(self):
        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")
        _new_run()
        reddit_health.record("brave", reddit_health.OK)
        assert reddit_health.skip_until("brave") is None
        assert reddit_health.entries()["brave"]["state"] == reddit_health.OK

    def test_a_path_down_this_run_stays_down_for_it(self):
        reddit_health.record("ddg", reddit_health.THROTTLED)
        reddit_health.record("ddg", reddit_health.OK)
        assert reddit_health.skip_reason("ddg") == reddit_health.THROTTLED

    def test_unreadable_or_foreign_cache_reads_as_empty(self):
        reddit_health.cache_path().write_text("{not json")
        assert reddit_health.entries() == {}
        reddit_health.cache_path().write_text(json.dumps({"schema": 99, "paths": {"search": {}}}))
        assert reddit_health.entries() == {}

    def test_clean_mode_keeps_health_in_memory_only(self, monkeypatch):
        monkeypatch.setattr(env, "CONFIG_DIR", None)
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403")
        assert reddit_health.entries() == {}
        assert reddit_health.skip_reason("search") == reddit_health.BLOCKED


class TestWebRungSkips:
    def test_brave_403_skip_seeds_the_next_run_straight_to_ddg(self):
        reddit_health.record("brave", reddit_health.BLOCKED, "web search blocked")
        _new_run()
        assert reddit_health.seed_web_rungs() == {"brave": reddit_health.BLOCKED}
        asked = []

        def ddg(query, count, freshness=None):
            asked.append("ddg")
            return [], web_search_keyless.EMPTY

        def brave(query, count, freshness=None):
            asked.append("brave")
            return [], web_search_keyless.OK

        with mock.patch.object(web_search_keyless, "_brave_rung", side_effect=brave), \
             mock.patch.object(web_search_keyless, "_ddg_rung", side_effect=ddg), \
             mock.patch.object(web_search_keyless, "_startpage_rung",
                               return_value=([], web_search_keyless.EMPTY)):
            _items, artifact = web_search_keyless.keyless_search(
                "site:reddit.com Elon Musk", (FROM, TO), {},
            )
        assert asked == ["ddg"]
        assert artifact["keyless_rungs"]["brave"] == web_search_keyless.SKIPPED
        # Seeding happens once per run.
        assert reddit_health.seed_web_rungs() == {}

    def test_floor_rung_states_persist_blocked_and_ok_only(self):
        p_web, p_ids, p_tree, p_comments = _floor(
            ["aaa111"],
            rungs={"brave": web_search_keyless.BLOCKED, "ddg": web_search_keyless.THROTTLED,
                   "startpage": web_search_keyless.OK},
        )
        with p_web, p_ids, p_tree, p_comments, reddit_keyless.lane_paths() as paths:
            reddit_keyless._web_floor("Elon Musk", "quick", None, None, FROM, TO, None)
        entries = reddit_health.entries()
        assert entries["brave"]["state"] == reddit_health.BLOCKED
        assert "ddg" not in entries
        assert entries["startpage"]["state"] == reddit_health.OK
        assert entries["arctic"]["state"] == reddit_health.OK
        assert paths["brave"] == reddit_health.BLOCKED
        assert paths["ddg"] == reddit_health.THROTTLED
        assert paths["web"] == reddit_health.OK


# --- served-path detail through the stream ------------------------------------

class TestServedPathDetail:
    def test_floor_threads_with_search_blocked_are_ok_with_a_served_detail(self, search_403):
        pids = [f"t{i:05d}" for i in range(14)]
        p_web, p_ids, p_tree, p_comments = _floor(pids)
        limited: set = set()
        with p_web, p_ids, p_tree, p_comments:
            items, artifact = _stream(limited=limited)

        assert len(items) == 14
        assert "_source_outcome" not in artifact  # ok, not partial
        assert artifact["_source_outcome_detail"] == "web+arctic served 14; search blocked"
        assert artifact.get("_source_outcome_detail_state") is None
        assert "reddit" not in limited

    def test_site_search_429_keeps_the_floor_running_and_reddit_unlimited(self, search_429):
        p_web, p_ids, p_tree, p_comments = _floor(["aaa111", "bbb222"])
        limited: set = set()
        with p_web, p_ids, p_tree, p_comments:
            items, artifact = _stream(limited=limited)

        assert len(items) == 2
        assert "_source_outcome" not in artifact
        assert artifact["_source_outcome_detail"] == "web+arctic served 2; search throttled"
        assert artifact.get("_source_outcome_detail_state") != health.RATE_LIMITED
        assert "reddit" not in limited

    def test_every_path_failing_is_a_transport_failure_not_no_results(self, search_403):
        p_web, p_ids, p_tree, p_comments = _floor(
            [],
            rungs={"brave": web_search_keyless.BLOCKED, "ddg": web_search_keyless.THROTTLED,
                   "startpage": web_search_keyless.ERROR},
        )
        with p_web, p_ids, p_tree, p_comments:
            items, artifact = _stream()

        assert items == []
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == health.UNREACHABLE
        assert outcome["state"] != health.NO_RESULTS
        assert outcome["detail"] == (
            "no path served; search blocked; brave blocked; ddg throttled; startpage down"
        )

    def test_every_path_throttled_is_rate_limited(self, search_429):
        p_web, p_ids, p_tree, p_comments = _floor(
            [], rungs={"brave": web_search_keyless.THROTTLED},
        )
        with p_web, p_ids, p_tree, p_comments:
            items, artifact = _stream()

        assert items == []
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == health.RATE_LIMITED
        assert outcome["detail"] == (
            "no path served; search throttled; brave throttled; rate-limited (HTTP 429)"
        )

    def test_every_free_path_failing_and_an_empty_backfill_is_still_a_failure(self, search_403):
        p_web, p_ids, p_tree, p_comments = _floor(
            [], rungs={"brave": web_search_keyless.BLOCKED, "ddg": web_search_keyless.BLOCKED,
                       "startpage": web_search_keyless.BLOCKED},
        )
        with p_web, p_ids, p_tree, p_comments, \
             mock.patch("lib.reddit.search_and_enrich", return_value={}), \
             mock.patch("lib.reddit.parse_reddit_response", return_value=[]):
            items, artifact = _stream({"SCRAPECREATORS_API_KEY": "dummy-key"})

        assert items == []
        assert artifact["_source_outcome"]["state"] == health.UNREACHABLE

    def test_an_empty_answer_is_still_no_results(self):
        p_web, p_ids, p_tree, p_comments = _floor(
            [], rungs={"brave": web_search_keyless.EMPTY},
        )
        with p_web, p_ids, p_tree, p_comments, \
             mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]):
            items, artifact = _stream()

        assert items == []
        assert "_source_outcome" not in (artifact or {})

    def test_same_thread_from_floor_and_listing_is_one_item_with_comments(self):
        # #1083 shape: two subqueries, one thread found by both the web floor
        # (with archive comments) and a listing (with the live score).
        comment = {"id": "c1", "author": "a", "body": "Elon Musk again", "score": 5, "depth": 0}
        listing_copy = {
            **_archived("aaa111", "Elon Musk thread aaa111"),
            "score": 900, "engagement": {"score": 900, "num_comments": 40},
            "why_relevant": "Reddit listing",
        }
        p_web, p_ids, p_tree, p_comments = _floor(["aaa111"])
        with p_web, p_ids, p_comments, \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_comment_tree",
                               return_value=[dict(comment)]), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings",
                               side_effect=lambda *_a, **_k: [dict(listing_copy)]), \
             mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]):
            runs = [_stream(subreddits=["elonmusk"]) for _subquery in range(2)]

        for items, artifact in runs:
            assert len(items) == 1
            thread = items[0]
            assert thread["engagement"]["score"] == 900  # live beats the snapshot
            assert [c["id"] for c in thread["top_comments"]] == ["c1"]
            assert set(thread["provenance"]) == {"listing", "web"}
            assert artifact["_source_outcome_detail"].startswith("web+listing+arctic served 1")


class TestDetailVocabulary:
    def test_tokens_parse_back_for_doctor(self):
        paths = {"search": "blocked", "brave": "skipped-cached", "ddg": "throttled",
                 "arctic": "error"}
        items = [{"provenance": ["web"], "engagement_source": "arctic"}]
        detail = reddit_health.served_detail(items, paths)
        assert detail == (
            "web+arctic served 1; search blocked; brave skipped-cached; ddg throttled; arctic down"
        )
        parsed = reddit_health.parse_detail(detail)
        assert parsed == {"web": "served", "arctic": "served", "search": "blocked",
                          "brave": "skipped-cached", "ddg": "throttled"}

    def test_detail_stays_short_and_plain(self):
        paths = {p: "blocked" for p in reddit_health.PATHS}
        items = [{"provenance": list(reddit_health.SERVED_ORDER)}] * 3
        detail = reddit_health.served_detail(items, paths)
        assert len(detail) <= 140
        assert "\u2014" not in detail and "\u2013" not in detail

    def test_every_path_failed_needs_an_asked_failing_path(self):
        assert not reddit_health.every_path_failed({})
        assert not reddit_health.every_path_failed({"search": "blocked", "brave": "empty"})
        assert reddit_health.every_path_failed({"search": "blocked", "web": "empty",
                                                "brave": "blocked"})


# --- doctor -------------------------------------------------------------------

class TestDoctorRows:
    def _last_report(self, detail):
        path = env.CONFIG_DIR / doctor.REPORT_CACHE_FILENAME
        path.write_text(json.dumps({
            "schema": doctor.REPORT_CACHE_SCHEMA_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "topic": "Elon Musk",
            "reports": [{"report": {"source_status": {
                "reddit": {"state": "ok", "items_returned": 14, "detail": detail},
            }}}],
        }))

    def test_rows_show_last_run_skip_and_action(self):
        self._last_report("web+arctic served 14; search blocked; ddg throttled")
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403: Blocked")
        report = doctor.build_report({})
        rows = {row["path"]: row for row in report["sources"]["reddit"]["paths"]}

        assert list(rows) == ["search", "brave", "ddg", "startpage", "arctic"]
        assert rows["search"]["last_run"] == "blocked"
        assert rows["search"]["skip_until"].endswith("UTC")
        assert rows["brave"]["last_run"] == "served"
        assert rows["ddg"]["last_run"] == "throttled"
        assert "one run only" in rows["ddg"]["action"]
        assert rows["arctic"]["last_run"] == "served"
        text = doctor.render_text(report)
        assert "path: Reddit site search - last run blocked; skipped until" in text
        assert "path: Arctic Shift archive - last run served - action: none" in text
        for line in text.splitlines():
            if "path:" in line:
                assert "\u2014" not in line and "\u2013" not in line

    def test_rows_with_no_saved_run_say_so(self):
        report = doctor.build_report({})
        rows = report["sources"]["reddit"]["paths"]
        assert all(row["last_run"] is None for row in rows)
        assert "path: Brave web search - no saved run - action: none" in doctor.render_text(report)

    def test_sc_row_names_the_last_backfill(self):
        self._last_report("web+arctic+sc served 9; ScrapeCreators backfill ran (4 free items)")
        report = doctor.build_report({"SCRAPECREATORS_API_KEY": "dummy-key"})
        sc = next(b for b in report["sources"]["reddit"]["backups"]
                  if b["name"] == "ScrapeCreators backfill")
        assert sc["note"].endswith("; last run: backfill served")

    def test_doctor_clears_the_cache_reprobes_and_reads_no_cookie(self, capsys):
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403: Blocked")
        reddit_health.record("brave", reddit_health.BLOCKED, "HTTP 403")
        brave_page = (FIXTURES / "brave_reddit_serp.html").read_text()
        answers = [(200, brave_page.encode(), None), (200, b'{"data": []}', None)]
        with mock.patch.object(doctor, "_probeable_sources", return_value=("reddit",)), \
             mock.patch("lib.bounded_get.get", side_effect=answers) as get, \
             mock.patch("lib.env.extract_browser_credentials") as creds, \
             mock.patch("lib.cookie_extract.extract_cookies") as cookies, \
             mock.patch("lib.cookie_extract.extract_cookies_with_source") as sourced:
            assert doctor.run({}, probe=True) == 0

        assert get.call_count == 2
        urls = [call.args[0].full_url for call in get.call_args_list]
        assert urls[0].startswith("https://search.brave.com/search?")
        assert "/api/posts/ids?ids=" in urls[1]
        creds.assert_not_called()
        cookies.assert_not_called()
        sourced.assert_not_called()
        entries = reddit_health.entries()
        assert "search" not in entries  # cleared
        assert entries["brave"]["state"] == reddit_health.OK  # re-probed
        assert entries["arctic"]["state"] == reddit_health.OK
        assert reddit_health.skip_until("brave") is None
        out = capsys.readouterr().out
        assert "doctor cleared this skip; the next run asks this path again" in out

    def test_cached_doctor_keeps_the_skips(self):
        reddit_health.record("search", reddit_health.BLOCKED, "HTTP 403: Blocked")
        with mock.patch.object(doctor, "_probe_sources", return_value={}):
            doctor.run({}, cached=True)
        assert reddit_health.skip_until("search") is not None


def test_served_path_detail_never_reaches_the_footer():
    from lib import render

    from tests.test_render_footer import _reddit_item, _report

    detail = "web+arctic served 1; search blocked; brave throttled"
    report = _report(
        items_by_source={"reddit": [_reddit_item()]},
        source_status={"reddit": schema.SourceOutcome(
            source="reddit", state=health.OK, items_returned=1, detail=detail,
        )},
    )
    text = render.render_compact(report)
    footer = text.split("✅ All agents reported back!", 1)[1]
    assert "served" not in footer
    assert "search blocked" not in footer
    assert "throttled" not in footer
