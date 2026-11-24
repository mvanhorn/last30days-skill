"""Tests for scripts/lib/reddit_discover.py: the keyless web floor and topic bar."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from unittest import mock

import pytest

from lib import env, health, http, reddit_discover, web_search_keyless

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "brave_reddit_serp.html"
).read_text(encoding="utf-8")
WINDOW = ("2026-10-16", "2026-11-15")


@pytest.fixture(autouse=True)
def _no_reddit_web_floor():
    """This module tests the real floor; override the suite-wide stub."""
    reddit_discover.reset_run_state()
    web_search_keyless.reset_rung_health()
    yield
    reddit_discover.reset_run_state()
    web_search_keyless.reset_rung_health()


def _brave_only(url, *args, **kwargs):
    return FIXTURE if "search.brave.com" in url else ""


def _search_stub(results_by_query=None):
    """A keyless_search stand-in that records each query it is asked."""
    calls = []

    def search(query, date_range, config, count=5, **kwargs):
        calls.append((query, kwargs))
        items = (results_by_query or {}).get(query, [])
        return list(items), {"keyless_backend": "brave", "keyless_rungs": {"brave": "ok"}}
    return search, calls


def _hit(url, title="", date=None):
    return {"url": url, "title": title, "snippet": "", "date": date}


class TestWebRefs:
    def test_captured_serp_yields_canonical_thread_refs(self):
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=_brave_only):
            floor = reddit_discover.web_refs("Kanye West", from_date=WINDOW[0], to_date=WINDOW[1])
        urls = [ref.url for ref in floor.refs]
        assert urls
        assert all(u.startswith("https://www.reddit.com/r/") and "/comments/" in u for u in urls)
        # Profile pages and subreddit listing pages are not threads.
        assert not any("/user/" in u or u.endswith("/new/") or u.endswith("/best/") for u in urls)
        ref = next(r for r in floor.refs if r.post_id == "1xattdz")
        assert ref.url == "https://www.reddit.com/r/Kanye/comments/1xattdz/"
        assert ref.subreddit == "Kanye"
        assert ref.provenance == {"web"}
        # The SERP's "r/Kanye on Reddit: " prefix is not part of the title.
        assert ref.title.startswith("Debunked: North West is not pregnant")
        assert ref.date and len(ref.date) == 10  # relative SERP date resolved
        assert floor.rungs["brave"] == "ok"

    def test_wiki_user_and_share_links_are_ignored(self):
        hits = [
            _hit("https://www.reddit.com/r/Kanye/wiki/index/", "wiki"),
            _hit("https://www.reddit.com/user/someone/", "profile"),
            _hit("https://www.reddit.com/r/Kanye/s/AbCdEf", "share"),
            _hit("https://old.reddit.com/r/Kanye/comments/abc123/slug/def456/", "Kanye thread"),
            _hit("https://example.com/kanye", "not reddit"),
        ]
        search, _calls = _search_stub({"site:reddit.com Kanye West": hits})
        floor = reddit_discover.web_refs("Kanye West", search=search)
        assert [r.post_id for r in floor.refs] == ["abc123"]
        assert floor.refs[0].url == "https://www.reddit.com/r/Kanye/comments/abc123/"

    def test_duplicate_links_across_queries_merge_to_one_ref(self):
        hits = [_hit("https://www.reddit.com/r/Kanye/comments/abc123/x/", "Kanye one")]
        search, calls = _search_stub({
            "site:reddit.com Kanye West": hits,
            "site:reddit.com r/hiphopheads Kanye West": hits,
        })
        floor = reddit_discover.web_refs("Kanye West", subreddits=["hiphopheads"], search=search)
        assert len(calls) == 2
        assert [r.post_id for r in floor.refs] == ["abc123"]

    def test_two_queries_topic_then_top_subreddit_hint(self):
        search, calls = _search_stub()
        reddit_discover.web_refs(
            "Kanye West", subreddits=["hiphopheads", "Kanye"], search=search,
            from_date=WINDOW[0], to_date=WINDOW[1],
        )
        assert [q for q, _ in calls] == [
            "site:reddit.com Kanye West", "site:reddit.com r/hiphopheads Kanye West"]

    def test_current_month_window_is_month_filtered(self):
        search, calls = _search_stub()
        with mock.patch.object(reddit_discover, "_today", return_value="2026-11-15"):
            reddit_discover.web_refs("Kanye West", search=search,
                                     from_date=WINDOW[0], to_date=WINDOW[1])
        assert calls[0][1]["freshness"] == "month"

    def test_repeat_calls_for_one_entity_never_pass_two_queries(self):
        search, calls = _search_stub()
        for subs in (None, ["hiphopheads"], ["Kanye"], ["rap"]):
            reddit_discover.web_refs("Kanye West", subreddits=subs, search=search)
        assert len(calls) == 2

    def test_comparison_with_three_entities_stays_within_two_queries_each(self):
        search, calls = _search_stub()
        for entity in ("Claude", "ChatGPT", "Gemini"):
            for subs in (["LocalLLaMA"], ["singularity"], None):
                reddit_discover.web_refs(entity, subreddits=subs, search=search)
        per_entity = {}
        for query, _ in calls:
            entity = query.split()[-1]
            per_entity[entity] = per_entity.get(entity, 0) + 1
        assert per_entity == {"Claude": 2, "ChatGPT": 2, "Gemini": 2}

    def test_web_failures_stay_out_of_the_reddit_stream_sink(self):
        def brave_429(url, *args, **kwargs):
            http._record_failure(http.HTTPError("HTTP 429", 429))
            return None

        with http.capture_failures() as failures, \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=brave_429):
            floor = reddit_discover.web_refs("Kanye West")
        assert failures == []
        assert floor.refs == []
        assert floor.rungs["brave"] == "throttled"

    def test_search_failure_never_raises(self):
        def boom(*args, **kwargs):
            raise RuntimeError("down")
        floor = reddit_discover.web_refs("Kanye West", search=boom)
        assert floor.refs == []


def _item(title, subreddit="hiphopheads", selftext=""):
    return {"title": title, "subreddit": subreddit, "selftext": selftext,
            "url": f"https://www.reddit.com/r/{subreddit}/comments/abc/"}


class TestTopicBar:
    def test_title_naming_the_topic_is_kept(self):
        bar = reddit_discover.apply_bar("Kanye West", [_item("Kanye drops a new album")])
        assert len(bar.kept) == 1 and bar.dropped == []

    def test_title_not_naming_the_topic_is_dropped(self):
        bar = reddit_discover.apply_bar(
            "Kanye West", [_item("Kanye drops a new album"), _item("Best pizza in town")])
        assert [i["title"] for i in bar.dropped] == ["Best pizza in town"]
        assert bar.note("web search") == (
            "1 of 2 threads dropped for not naming the topic (web search)")

    def test_dedicated_subreddit_body_naming_the_topic_is_kept(self):
        thread = _item("New album leak?", subreddit="Kanye", selftext="Is this really Kanye on track 3?")
        bar = reddit_discover.apply_bar("Kanye West", [thread], dedicated=["kanye"])
        assert bar.kept == [thread]

    def test_same_thread_in_an_unrelated_subreddit_is_dropped(self):
        thread = _item("New album leak?", subreddit="music", selftext="Is this really Kanye on track 3?")
        bar = reddit_discover.apply_bar("Kanye West", [thread], dedicated=["Kanye"])
        assert bar.dropped == [thread]

    def test_resolved_subreddit_body_naming_the_topic_is_kept(self):
        thread = _item("Album of the year?", subreddit="hiphopheads", selftext="Kanye or nobody")
        bar = reddit_discover.apply_bar("Kanye West", [thread], subreddits=["HipHopHeads"])
        assert bar.kept == [thread]

    def test_resolved_subreddit_body_without_the_topic_is_dropped(self):
        thread = _item("Album of the year?", subreddit="hiphopheads", selftext="Drake or nobody")
        bar = reddit_discover.apply_bar("Kanye West", [thread], subreddits=["hiphopheads"])
        assert bar.dropped == [thread]

    def test_no_note_when_nothing_dropped(self):
        assert reddit_discover.apply_bar("Kanye West", []).note("web search") is None


def test_a_rung_taken_out_keeps_its_cause_in_the_floor_rungs():
    def brave_403_then_ddg(url, *args, **kwargs):
        if "search.brave.com" in url:
            http._record_failure(http.HTTPError("HTTP 403", 403))
            return None
        return ""

    with mock.patch.object(web_search_keyless.http, "get_text", side_effect=brave_403_then_ddg):
        floor = reddit_discover.web_refs("Kanye West", subreddits=["Kanye"])
    assert len(floor.queries) == 2
    assert floor.rungs["brave"] == "blocked"


class TestWebFloorDeadline:
    def test_threads_from_the_first_query_survive_a_spent_deadline(self):
        clock = [100.0]
        hits = [_hit("https://www.reddit.com/r/Kanye/comments/abc123/x/", "Kanye one")]
        stub, calls = _search_stub({"site:reddit.com Kanye West": hits})

        def slow_first(query, date_range, config, count=5, **kwargs):
            out = stub(query, date_range, config, count, **kwargs)
            clock[0] = 110.0
            return out

        partial = reddit_discover.WebFloor()
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            floor = reddit_discover.web_refs(
                "Kanye West", subreddits=["hiphopheads"], search=slow_first,
                deadline=110.0, floor=partial, config={},
            )
        assert floor is partial
        assert len(calls) == 1
        assert [r.post_id for r in partial.refs] == ["abc123"]
        assert partial.timed_out


class TestSearxngConfigHandoff:
    """Optional SearXNG fallback receives approved config without leaking secrets."""

    @staticmethod
    def _throttled_html(url, *args, **kwargs):
        http._record_failure(http.HTTPError("HTTP 429", 429))
        return None

    @staticmethod
    def _searxng_payload():
        return {
            "results": [
                {
                    "url": "https://www.reddit.com/r/LocalLLaMA/comments/1sx9abc/iq3_quantization_notes/",
                    "title": "IQ3 quantization notes : r/LocalLLaMA - Reddit",
                    "content": "IQ3_XS quality vs IQ3_M",
                },
                {
                    "url": "https://example.com/not-reddit",
                    "title": "Other site",
                    "content": "skip",
                },
            ]
        }

    def test_threaded_config_reaches_searxng_and_yields_thread_ref(self):
        cfg = {
            "LAST30DAYS_SEARXNG_URL": "https://searx.example",
            "OPENAI_API_KEY": "sk-secret",
            "SCRAPECREATORS_API_KEY": "sc-secret",
            "AUTH_TOKEN": "cookie-secret",
        }
        seen_urls = []

        def fake_get(url, **kwargs):
            seen_urls.append(url)
            return self._searxng_payload()

        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get", side_effect=fake_get), \
             mock.patch.object(env, "resolve_searxng_url", side_effect=AssertionError("should not reload config")) as load:
            floor = reddit_discover.web_refs("IQ3 quantization", config=cfg)

        load.assert_not_called()
        assert seen_urls and seen_urls[0].startswith("https://searx.example/search?")
        assert [r.post_id for r in floor.refs] == ["1sx9abc"]
        assert floor.refs[0].url == "https://www.reddit.com/r/LocalLLaMA/comments/1sx9abc/"
        assert floor.refs[0].title == "IQ3 quantization notes"
        assert floor.refs[0].provenance == {"web"}

    def test_config_passed_to_search_is_scoped_without_secrets(self):
        seen_configs = []

        def search(query, date_range, config, count=5, **kwargs):
            seen_configs.append(dict(config))
            return [
                _hit("https://www.reddit.com/r/LocalLLaMA/comments/1sx9abc/x/", "IQ3 thread"),
            ], {"keyless_backend": "searxng", "keyless_rungs": {"brave": "throttled"}}

        cfg = {
            "LAST30DAYS_SEARXNG_URL": "  https://searx.example  ",
            "OPENAI_API_KEY": "sk-secret",
            "SCRAPECREATORS_API_KEY": "sc-secret",
            "AUTH_TOKEN": "cookie-secret",
            "_reddit_envelope": object(),
        }
        floor = reddit_discover.web_refs("IQ3 quantization", search=search, config=cfg)
        assert seen_configs == [{"LAST30DAYS_SEARXNG_URL": "https://searx.example"}]
        assert [r.post_id for r in floor.refs] == ["1sx9abc"]

    @pytest.mark.parametrize("cfg", [
        {},
        {"LAST30DAYS_SEARXNG_URL": None},
        {"LAST30DAYS_SEARXNG_URL": ""},
        {"LAST30DAYS_SEARXNG_URL": "   "},
        {"LAST30DAYS_SEARXNG_URL": "${user_config.searxng_url}"},
    ])
    def test_disabled_or_empty_config_never_calls_searxng(self, cfg, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_SEARXNG_URL", "https://ambient.example")
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get, \
             mock.patch.object(env, "resolve_searxng_url", side_effect=AssertionError("explicit config must not reload")) as load:
            floor = reddit_discover.web_refs("IQ3 quantization", config=cfg)

        load.assert_not_called()
        sx_get.assert_not_called()
        assert floor.refs == []

    def test_omitted_config_loads_user_approved_env_once_per_run(self, monkeypatch):
        monkeypatch.setenv("LAST30DAYS_SEARXNG_URL", "https://searx.example")
        real_reader = env.resolve_searxng_url
        with mock.patch.object(env, "resolve_searxng_url", wraps=real_reader) as load, \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()):
            floor = reddit_discover.web_refs("IQ3 quantization", subreddits=["LocalLLaMA"])

        load.assert_called_once_with()
        assert [r.post_id for r in floor.refs] == ["1sx9abc"]

        # Resetting run state clears the memoized config for the next run.
        reddit_discover.reset_run_state()
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)
        with mock.patch.object(env, "resolve_searxng_url", wraps=real_reader) as load, \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            floor2 = reddit_discover.web_refs("IQ3 quantization")

        load.assert_called_once_with()
        sx_get.assert_not_called()
        assert floor2.refs == []

    def test_untrusted_project_config_searxng_is_ignored(self, tmp_path, monkeypatch):
        project_env = tmp_path / ".claude" / "last30days.env"
        project_env.parent.mkdir()
        project_env.write_text(
            "LAST30DAYS_SEARXNG_URL=https://untrusted-project.example\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LAST30DAYS_TRUST_PROJECT_CONFIG", raising=False)
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)

        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            floor = reddit_discover.web_refs("IQ3 quantization")

        sx_get.assert_not_called()
        assert floor.refs == []

    def test_searxng_stays_fallback_when_html_rung_succeeds(self):
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=_brave_only), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            floor = reddit_discover.web_refs("Kanye West", from_date=WINDOW[0], to_date=WINDOW[1], config=cfg)

        sx_get.assert_not_called()
        assert floor.refs and floor.rungs["brave"] == "ok"

    @pytest.mark.parametrize("has_thread", [False, True])
    def test_only_reddit_threads_can_stop_the_ladder(self, has_thread):
        html_hits = [
            _hit("https://www.reddit.com/r/LocalLLaMA/"),
            _hit("https://www.reddit.com/r/LocalLLaMA/wiki/index/"),
        ]
        if has_thread:
            html_hits.append(_hit("https://www.reddit.com/r/LocalLLaMA/comments/html123/"))
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        with mock.patch.object(web_search_keyless, "_brave_rung", return_value=(html_hits, "ok")), \
             mock.patch.object(web_search_keyless.http, "get_text", return_value=""), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()) as sx:
            floor = reddit_discover.web_refs("IQ3 quantization", config=cfg)

        assert [ref.post_id for ref in floor.refs] == (["html123"] if has_thread else ["1sx9abc"])
        assert floor.rungs["brave"] == ("ok" if has_thread else "empty")
        assert sx.call_count == (0 if has_thread else 1)

    def test_empty_web_timeout_can_retry_the_same_query_with_more_time(self):
        clock = [100.0]

        def expire_first(query, count, freshness, **kwargs):
            if clock[0] == 100.0:
                clock[0] = 110.0
            return [], "empty"

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(web_search_keyless, "_brave_rung", side_effect=expire_first), \
             mock.patch.object(web_search_keyless.http, "get_text", return_value=""), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()) as sx:
            first = reddit_discover.web_refs("IQ3 quantization", deadline=110.0, config=cfg)
            assert not first.refs and not sx.called
            second = reddit_discover.web_refs("IQ3 quantization", deadline=140.0, config=cfg)

        assert [ref.post_id for ref in second.refs] == ["1sx9abc"]
        assert first.timed_out and not second.timed_out
        sx.assert_called_once()

    def test_waiter_gets_its_attempt_timeout_before_the_slot_is_retried(self):
        from threading import Thread, current_thread

        entered, waiting, release = Event(), Event(), Event()
        calls, waiter_results = [], []
        real_wait = Event.wait

        def track_wait(event, timeout=None):
            if current_thread().name == "reddit-floor-waiter":
                waiting.set()
            return real_wait(event, timeout)

        def search(query, *args, **kwargs):
            calls.append(query)
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
                return [], {"_source_outcome": {"state": health.TIMEOUT}}
            return [_hit("https://www.reddit.com/r/LocalLLaMA/comments/new123/")], {}

        def run():
            return reddit_discover.web_refs("IQ3 quantization", search=search, config={})

        waiter = Thread(name="reddit-floor-waiter", target=lambda: waiter_results.append(run()))
        with mock.patch.object(Event, "wait", track_wait), ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(run)
            try:
                assert entered.wait(5)
                waiter.start()
                assert waiting.wait(5)
            finally:
                release.set()
            assert first.result(timeout=5).timed_out
            waiter.join(timeout=5)

        assert not waiter.is_alive()
        assert len(calls) == 1 and waiter_results[0].timed_out
        assert [ref.post_id for ref in run().refs] == ["new123"]
        assert len(calls) == 2

    def test_config_loader_exception_never_breaks_the_lane(self):
        with mock.patch.object(env, "resolve_searxng_url", side_effect=RuntimeError("boom")), \
             mock.patch.object(web_search_keyless.http, "get_text", return_value=None):
            floor = reddit_discover.web_refs("IQ3 quantization")
        assert floor.refs == []

    def test_config_loader_failure_runs_html_ladder_without_caching_empty(self):
        with mock.patch.object(env, "resolve_searxng_url", side_effect=RuntimeError("boom")), \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=_brave_only):
            floor = reddit_discover.web_refs(
                "Kanye West", from_date=WINDOW[0], to_date=WINDOW[1],
            )
        assert any(r.post_id == "1xattdz" for r in floor.refs)
        assert floor.rungs["brave"] == "ok"

        # A subsequent call in the same run must reuse the HTML result rather
        # than an empty answer cached because config loading failed.
        second = reddit_discover.web_refs(
            "Kanye West", from_date=WINDOW[0], to_date=WINDOW[1],
            search=lambda *a, **kw: ([], {}),
        )
        assert any(r.post_id == "1xattdz" for r in second.refs)

    @pytest.mark.parametrize("first_config", [OSError("config unavailable"), None],
                             ids=["transient-error", "stable-absence"])
    def test_same_query_retries_html_miss_only_after_config_read_error(self, first_config):
        with mock.patch.object(env, "resolve_searxng_url", side_effect=[first_config, "https://searx.example"]) as load, \
             mock.patch.object(web_search_keyless.http, "get_text", return_value=None) as html, \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()):
            first = reddit_discover.web_refs("IQ3 quantization")
            first_calls = html.call_count
            second = reddit_discover.web_refs("IQ3 quantization")

        assert first.refs == []
        if isinstance(first_config, OSError):
            assert [r.post_id for r in second.refs] == ["1sx9abc"]
            assert load.call_count == 2
        else:
            assert second.refs == []
            assert load.call_count == 1
            assert html.call_count == first_calls

    def test_retryable_misses_do_not_free_slots_for_new_subreddit_queries(self):
        search, calls = _search_stub()
        with mock.patch.object(env, "resolve_searxng_url", side_effect=OSError("config unavailable")):
            reddit_discover.web_refs("IQ3 quantization", subreddits=["LocalLLaMA"], search=search)
            reddit_discover.web_refs("IQ3 quantization", subreddits=["llama"], search=search)

        assert [q for q, _ in calls] == [
            "site:reddit.com IQ3 quantization",
            "site:reddit.com r/LocalLLaMA IQ3 quantization",
            "site:reddit.com IQ3 quantization",
        ]

    def test_spent_deadline_after_html_rungs_skips_late_searxng_and_retains_prior_results(self):
        clock = [100.0]
        deadline = 110.0

        def fake_get_text(url, *args, **kwargs):
            if "LocalLLaMA" not in url:
                return FIXTURE if "search.brave.com" in url else ""
            if "startpage.com" in url:
                clock[0] = 110.5
            else:
                clock[0] += 2.0
            http._record_failure(http.HTTPError("HTTP 429", 429))
            return None

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=fake_get_text), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            floor = reddit_discover.web_refs(
                "Kanye West",
                subreddits=["LocalLLaMA"],
                from_date=WINDOW[0],
                to_date=WINDOW[1],
                deadline=deadline,
                config=cfg,
            )

        sx_get.assert_not_called()
        assert any(r.post_id == "1xattdz" for r in floor.refs)

    def test_standalone_cache_stores_normalized_url_or_absence(self):
        with mock.patch.object(env, "resolve_searxng_url", return_value="  https://searx.example  "), \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()):
            floor = reddit_discover.web_refs("IQ3 quantization")

        assert [r.post_id for r in floor.refs] == ["1sx9abc"]
        assert reddit_discover._SEARXNG_URL == "https://searx.example"

        reddit_discover.reset_run_state()
        assert reddit_discover._SEARXNG_URL is None
        with mock.patch.object(env, "resolve_searxng_url", return_value="   "), \
             mock.patch.object(web_search_keyless.http, "get_text", return_value=None), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            reddit_discover.web_refs("IQ3 quantization")
        sx_get.assert_not_called()
        assert reddit_discover._SEARXNG_URL == ""

    def test_concurrent_first_load_reads_config_once(self):
        import threading

        load_count = 0
        count_lock = threading.Lock()
        barrier = threading.Barrier(4)
        seen_configs = []

        def slow_url_read():
            nonlocal load_count
            with count_lock:
                load_count += 1
            import time as _time
            _time.sleep(0.03)
            return "https://concurrent.example"

        def search(query, date_range, config, count=5, **kwargs):
            with count_lock:
                seen_configs.append(dict(config))
            return [], {"keyless_backend": "searxng", "keyless_rungs": {}}

        def worker(idx):
            barrier.wait(timeout=5)
            reddit_discover.web_refs(f"topic {idx}", search=search)

        with mock.patch.object(env, "resolve_searxng_url", side_effect=slow_url_read):
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

        assert load_count == 1
        assert seen_configs == [{"LAST30DAYS_SEARXNG_URL": "https://concurrent.example"}] * 4

    @pytest.mark.parametrize("blocked_at", ["load", "search"])
    def test_reset_fences_old_query_from_new_run_memo(self, blocked_at):
        entered = Event()
        release = Event()
        topic = "IQ3 quantization"
        query = "site:reddit.com IQ3 quantization"
        new_hint = "site:reddit.com r/NewSub IQ3 quantization"

        def block():
            entered.set()
            assert release.wait(timeout=5)

        def old_url():
            if blocked_at == "load":
                block()
            return "https://stale.example"

        def old_search(*args, **kwargs):
            if blocked_at == "search":
                block()
            return [_hit("https://www.reddit.com/r/LocalLLaMA/comments/old123/")], {}

        new_search, calls = _search_stub({
            query: [_hit("https://www.reddit.com/r/LocalLLaMA/comments/new123/")],
            new_hint: [_hit("https://www.reddit.com/r/NewSub/comments/new456/")],
        })
        new_config = {"LAST30DAYS_SEARXNG_URL": "https://fresh.example"}
        with mock.patch.object(env, "resolve_searxng_url", side_effect=old_url), \
             ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(reddit_discover.web_refs, topic, subreddits=["OldSub"], search=old_search)
            try:
                assert entered.wait(timeout=5)
                reddit_discover.reset_run_state()
                # Explicit config lets the new run finish even if the old run's loader is blocked.
                fresh = reddit_discover.web_refs(topic, search=new_search, config=new_config)
                assert [r.post_id for r in fresh.refs] == ["new123"]
            finally:
                release.set()
            old = pending.result(timeout=5)
            repeated = reddit_discover.web_refs(
                topic, subreddits=["NewSub"], search=new_search, config=new_config,
            )

        assert [r.post_id for r in repeated.refs] == ["new123", "new456"]
        assert [q for q, _ in calls] == [query, new_hint]
        assert old.queries == ([] if blocked_at == "load" else [query])
        assert reddit_discover._SEARXNG_URL is None

    def test_trusted_project_config_searxng_is_loaded(self, tmp_path, monkeypatch):
        project_env = tmp_path / ".claude" / "last30days.env"
        project_env.parent.mkdir()
        project_env.write_text(
            "LAST30DAYS_SEARXNG_URL=https://trusted-project.example\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)

        seen_urls = []

        def fake_get(url, **kwargs):
            seen_urls.append(url)
            return self._searxng_payload()

        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get", side_effect=fake_get):
            floor = reddit_discover.web_refs("IQ3 quantization")

        assert seen_urls and seen_urls[0].startswith("https://trusted-project.example/search?")
        assert [r.post_id for r in floor.refs] == ["1sx9abc"]

    def test_standalone_discovery_reads_trusted_url_without_exporting_settings(self, tmp_path, monkeypatch):
        import os

        project_env = tmp_path / ".claude" / "last30days.env"
        project_env.parent.mkdir()
        project_env.write_text(
            "LAST30DAYS_SEARXNG_URL=https://trusted-project.example\n"
            "SCRAPECREATORS_BASE_URL=https://local-provider.example\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")
        monkeypatch.setenv("LAST30DAYS_SEARXNG_URL", "${user_config.searxng_url}")
        monkeypatch.delenv("SCRAPECREATORS_BASE_URL", raising=False)
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=""), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()) as sx:
            floor = reddit_discover.web_refs("IQ3 quantization")

        assert [ref.post_id for ref in floor.refs] == ["1sx9abc"]
        assert sx.call_args.args[0].startswith("https://trusted-project.example/search?")
        assert "SCRAPECREATORS_BASE_URL" not in os.environ
        assert os.environ.get("LAST30DAYS_SEARXNG_URL") == "${user_config.searxng_url}"

    def test_standalone_url_uses_global_value_when_trusted_project_has_a_placeholder(self, tmp_path, monkeypatch):
        env.CONFIG_FILE.write_text("LAST30DAYS_SEARXNG_URL=https://global.example\n", encoding="utf-8")
        project_env = tmp_path / ".claude" / "last30days.env"
        project_env.parent.mkdir()
        project_env.write_text("LAST30DAYS_SEARXNG_URL=${user_config.searxng_url}\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LAST30DAYS_TRUST_PROJECT_CONFIG", "1")
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)
        with mock.patch.object(web_search_keyless.http, "get_text", return_value=""), \
             mock.patch.object(web_search_keyless.http, "get", return_value=self._searxng_payload()) as sx:
            floor = reddit_discover.web_refs("IQ3 quantization")

        assert [ref.post_id for ref in floor.refs] == ["1sx9abc"]
        assert sx.call_args.args[0].startswith("https://global.example/search?")

    def test_memoized_answers_are_reused_across_calls_with_config(self):
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        calls = []

        def search(query, date_range, config, count=5, **kwargs):
            calls.append((query, dict(config)))
            return [
                _hit("https://www.reddit.com/r/LocalLLaMA/comments/1sx9abc/x/", "IQ3 thread"),
            ], {"keyless_backend": "searxng", "keyless_rungs": {}}

        first = reddit_discover.web_refs("IQ3 quantization", search=search, config=cfg)
        second = reddit_discover.web_refs("IQ3 quantization", search=search, config=cfg)
        assert len(calls) == 1
        assert [r.post_id for r in first.refs] == ["1sx9abc"]
        assert [r.post_id for r in second.refs] == ["1sx9abc"]

    def _run_pipeline_reddit_with_searxng(
        self, cfg, *, sc_search=None, html_fetch=None, site_hits=(), searxng_get=None,
        full_report=False, labels=("primary",), report_depth="quick", report_sources=("reddit",),
    ):
        from lib import pipeline, reddit, reddit_keyless, schema

        seen_urls = []
        archived = {
            "id": "",
            "title": "IQ3 quantization notes",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/1sx9abc/iq3_quantization_notes/",
            "score": 40,
            "num_comments": 7,
            "subreddit": "LocalLLaMA",
            "created_utc": 1_700_000_000,
            "author": "u",
            "selftext": "IQ3 notes",
            "date": "2026-05-20",
            "engagement": {"score": 40, "num_comments": 7, "upvote_ratio": None},
            "relevance": 0.0,
            "why_relevant": "Reddit listing (arctic-shift)",
            "metadata": {"post_id": "1sx9abc"},
        }

        def fake_get(url, **kwargs):
            seen_urls.append(url)
            return self._searxng_payload()

        sc_ctx = (
            mock.patch.object(reddit, "search_and_enrich_memo", side_effect=sc_search)
            if sc_search is not None else nullcontext()
        )
        with sc_ctx, \
             mock.patch.object(reddit_keyless.reddit_search, "search", return_value=list(site_hits)), \
             mock.patch.object(reddit_keyless.reddit_listing, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_listings", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_scores", return_value={}), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_posts_by_ids",
                               return_value=({"1sx9abc": dict(archived)}, "ok")), \
             mock.patch.object(reddit_keyless.reddit_arctic, "fetch_comment_tree", return_value=[]), \
             mock.patch.object(reddit_keyless.reddit_shreddit, "fetch_comments",
                               return_value={"top_comments": [], "comment_insights": [], "num_comments": None}), \
             mock.patch.object(web_search_keyless.http, "get_text", side_effect=html_fetch or self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get", side_effect=searxng_get or fake_get), \
             mock.patch.object(env, "resolve_searxng_url", side_effect=AssertionError("already loaded config should be threaded")):
            if full_report:
                # The whole run, so the result is the serialized per-source outcome.
                report = pipeline.run(
                    topic="IQ3 quantization",
                    config=cfg,
                    depth=report_depth,
                    requested_sources=list(report_sources),
                    web_backend="none",
                    as_of_date="2026-05-31",
                    external_plan={
                        "intent": "research",
                        "freshness_mode": "balanced_recent",
                        "cluster_mode": "topic",
                        "subqueries": [{
                            "label": label,
                            # Identical subqueries are planned once, and quick depth plans only one.
                            "search_query": "IQ3 quantization" if index == 0 else f"IQ3 quantization {label}",
                            "ranking_query": "IQ3 quantization" if index == 0 else f"IQ3 quantization {label}",
                            "sources": list(report_sources),
                        } for index, label in enumerate(labels)],
                    },
                )
                return report, None, seen_urls
            items, artifact = pipeline._retrieve_stream(
                topic="IQ3 quantization",
                subquery=schema.SubQuery(
                    label="primary", search_query="IQ3 quantization",
                    ranking_query="IQ3 quantization", sources=["reddit"],
                ),
                source="reddit",
                config=cfg,
                depth="quick",
                date_range=("2026-05-01", "2026-05-31"),
                runtime=schema.ProviderRuntime(
                    reasoning_provider="mock", planner_model="mock", rerank_model="mock",
                ),
                mock=False,
            )
        return items, artifact, seen_urls

    def test_pipeline_threads_config_end_to_end_to_searxng_web_floor(self):
        cfg = {
            "LAST30DAYS_SEARXNG_URL": "https://searx.example",
            "OPENAI_API_KEY": "sk-secret",
        }
        items, _, seen_urls = self._run_pipeline_reddit_with_searxng(cfg)
        assert seen_urls and seen_urls[0].startswith("https://searx.example/search?")
        assert [i["metadata"]["post_id"] for i in items] == ["1sx9abc"]

    def test_sc_primary_fallback_also_threads_config_to_web_floor(self):
        cfg = {
            "SCRAPECREATORS_API_KEY": "sc-dummy-key",
            env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
            "LAST30DAYS_SEARXNG_URL": "https://searx.example",
        }
        items, _, seen_urls = self._run_pipeline_reddit_with_searxng(cfg, sc_search=lambda *a, **kw: {})
        assert seen_urls and seen_urls[0].startswith("https://searx.example/search?")
        assert [i["metadata"]["post_id"] for i in items] == ["1sx9abc"]

    @pytest.mark.parametrize("finished_at, http_status, expected_state", [
        (200.0, None, health.TIMEOUT),
        (198.0, None, health.TIMEOUT),
        (170.0, None, None),
        (198.0, 429, health.RATE_LIMITED),
    ], ids=["run-budget-spent", "public-budget-too-short", "in-budget-miss", "primary-rate-limit"])
    def test_sc_primary_empty_fallback_reports_its_outcome(self, finished_at, http_status, expected_state):
        clock = [100.0]

        def sc_search(*args, **kwargs):
            clock[0] = finished_at
            if http_status is not None:
                http._record_failure(http.HTTPError("HTTP 429", http_status))
            return {}

        cfg = {
            "SCRAPECREATORS_API_KEY": "sc-dummy-key",
            env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
            "LAST30DAYS_SEARXNG_URL": "https://searx.example" if expected_state is not None else "",
            "_research_deadline": 200.0,
        }
        html_fetch = mock.Mock(return_value=None)
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=html_fetch,
            )

        assert seen_urls == []
        assert items == []
        assert bool(html_fetch.call_count) is (expected_state is None)
        assert artifact.get("_source_outcome", {}).get("state") == expected_state

    def test_sc_primary_raised_auth_failure_is_not_masked_by_public_timeout(self):
        from lib import pipeline

        clock = [100.0]

        def sc_search(*args, **kwargs):
            clock[0] = 179.0
            raise http.HTTPError("HTTP 401", 401)

        cfg = {
            "SCRAPECREATORS_API_KEY": "sc-dummy-key",
            env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
            "_research_deadline": 200.0,
        }
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             pytest.raises(pipeline.SourceRunError) as exc:
            self._run_pipeline_reddit_with_searxng(cfg, sc_search=sc_search)
        assert exc.value.outcome_state == health.AUTH_FAILED

    @pytest.mark.parametrize("mode", ["public-first", "public-first-backfill", "sc-first", "sc-first-429"])
    @pytest.mark.parametrize("wait_expired", [False, True], ids=["ladder", "wait"])
    def test_nested_web_cutoff_is_visible_before_the_public_lane_expires(self, mode, wait_expired):
        from lib import reddit_keyless

        clock = [100.0]
        # Public-first without a key and the SC-first fallback both start with 100s left, so
        # each ends at 200 - 15 = 185 (web cutoff 177).
        exhausted_at = 128.0 if mode == "public-first-backfill" else 178.0
        # The public-first lane with a paid key ends at 170 - 10.5 - 24.5 = 135 (cutoff 127).
        budget = 170.0 if mode == "public-first-backfill" else 200.0
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": budget}
        if mode != "public-first":
            cfg["SCRAPECREATORS_API_KEY"] = "sc-dummy-key"
        if mode.startswith("sc-first"):
            cfg[env.REDDIT_BACKEND_PIN_VAR] = "scrapecreators"

        def sc_search(*args, **kwargs):
            if mode == "sc-first-429":
                http._record_failure(http.HTTPError("HTTP 429", 429))
            return {}

        def html_fetch(*args, **kwargs):
            clock[0] = exhausted_at  # Past discovery, before the public lane's deadline.
            return ""

        def run_inline(_wait_until, stage, default, call, /, *args, **kwargs):
            if wait_expired and stage == "web floor":
                clock[0] = exhausted_at
                return default
            return call(*args, **kwargs)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search if mode != "public-first" else None, html_fetch=html_fetch,
            )

        assert not items and not seen_urls
        expected = health.RATE_LIMITED if mode == "sc-first-429" else health.TIMEOUT
        assert artifact.get("_source_outcome", {}).get("state") == expected

    def test_public_first_backfill_clean_miss_is_not_a_timeout(self):
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 200.0}
        with mock.patch("time.monotonic", return_value=100.0):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=lambda *args, **kwargs: {}, html_fetch=lambda *args, **kwargs: "",
            )

        assert not items and not seen_urls
        assert "_source_outcome" not in artifact

    @pytest.mark.parametrize("backfill, state", [
        ("empty", health.TIMEOUT),
        ("rate-limited", health.RATE_LIMITED),
        ("result", None),
    ])
    def test_public_cutoff_precedes_failed_paths_after_backfill(self, backfill, state):
        from lib import reddit_keyless

        clock = [100.0]
        lanes = []
        thread = "https://www.reddit.com/r/LocalLLaMA/comments/paid123/"
        # 170s budget: the free lane ends at 170 - 10.5 - 24.5 = 135 (see the lanes assertion).
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 170.0,
               "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        def blocked_html(*args, **kwargs):
            http._record_failure(http.HTTPError("HTTP 403: HTML rung blocked", 403))
            clock[0] = 128.0  # Past the discovery cutoff (127), before the lane deadline (135).
            return None

        def sc_search(*args, **kwargs):
            if backfill == "rate-limited":
                http._record_failure(http.HTTPError("HTTP 429: paid backfill throttled", 429))
            return {"items": [_hit(thread)] if backfill == "result" else []}

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline), \
             mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane), \
             mock.patch.object(reddit_keyless.reddit_search, "_blocked_reason", "offline 403"):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=blocked_html,
            )

        assert lanes == [135.0]
        assert clock[0] == 128.0 and not seen_urls
        assert [item["url"] for item in items] == ([thread] if backfill == "result" else [])
        assert artifact.get("_source_outcome", {}).get("state") == state
        if backfill == "rate-limited":
            assert artifact["_source_outcome"]["detail"] == "HTTP 429: paid backfill throttled"

    @pytest.mark.parametrize("backfill, state, detail", [
        ("incomplete", health.TIMEOUT, "paid backfill stopped at its deadline before completing"),
        ("incomplete-429", health.RATE_LIMITED, "HTTP 429: paid backfill throttled"),
        ("complete-empty", health.TIMEOUT, "Reddit ran out of search time before it found threads"),
    ])
    def test_actual_cutoff_keeps_the_incomplete_backfill_note(self, backfill, state, detail):
        from lib import reddit_keyless

        clock = [100.0]
        lanes = []
        note = "paid backfill stopped at its deadline before completing"
        # 170s budget: the free lane ends at 135 and the web stage's own cutoff is 127.
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 170.0,
               "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        def clean_empty_html(*args, **kwargs):
            clock[0] = 128.0  # Past the discovery cutoff (127), before the lane deadline (135).
            return ""

        def sc_search(*args, **kwargs):
            if backfill == "complete-empty":
                return {}
            if backfill == "incomplete-429":
                http._record_failure(http.HTTPError("HTTP 429: paid backfill throttled", 429))
            return {"items": [], "incomplete": True, "incomplete_note": note}

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]),              mock.patch.object(reddit_keyless, "_within", side_effect=run_inline),              mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=clean_empty_html,
            )

        # The site search answered cleanly and empty, so no path failed; the web stage hit its
        # real cutoff and the paid backfill ended empty. A captured paid failure keeps precedence.
        assert lanes == [135.0] and clock[0] == 128.0 and not seen_urls and not items
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == state and outcome["detail"] == detail

    def test_completed_failures_precede_the_incomplete_backfill_note(self):
        from lib import reddit_keyless

        note = "paid backfill stopped at its deadline before completing"

        def web_floor(*args, **kwargs):
            return []

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 170.0}
        with mock.patch("time.monotonic", return_value=100.0), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline), \
             mock.patch.object(reddit_keyless, "_web_floor", side_effect=web_floor), \
             mock.patch.object(reddit_keyless.reddit_search, "_blocked_reason", "offline 403"):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=lambda *a, **k: {"items": [], "incomplete": True, "incomplete_note": note},
            )

        # Every asked path failed and nothing was cut off: the failure, not the paid note, is reported.
        assert not items and not seen_urls
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == health.UNREACHABLE and "search blocked" in outcome["detail"]

    @pytest.mark.parametrize("backfill", ["empty", "rate-limited", "result"])
    def test_return_margin_alone_keeps_completed_failures_after_backfill(self, backfill):
        from lib import reddit_keyless

        clock = [100.0]
        marks = []
        lanes = []
        thread = "https://www.reddit.com/r/LocalLLaMA/comments/paid123/"
        # 170s budget: the free lane ends at 170 - 10.5 - 24.5 = 135 (see the lanes assertion).
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 170.0}
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        def web_floor_finishes_inside_margin(*args, **kwargs):
            # It starts with 4s left, so its own cutoff (133.4) is never reached;
            # it ends 1.8s before the 135 lane deadline: inside the 2s return margin.
            clock[0] = 133.2
            return []

        def sc_search(*args, **kwargs):
            if backfill == "rate-limited":
                http._record_failure(http.HTTPError("HTTP 429: paid backfill throttled", 429))
            return {"items": [_hit(thread)] if backfill == "result" else []}

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        real_mark = reddit_keyless._mark

        def record_mark(path, state):
            marks.append((path, state))
            return real_mark(path, state)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]),              mock.patch.object(reddit_keyless, "_within", side_effect=run_inline),              mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane),              mock.patch.object(reddit_keyless, "_web_floor", side_effect=web_floor_finishes_inside_margin),              mock.patch.object(reddit_keyless, "_mark", side_effect=record_mark),              mock.patch.object(reddit_keyless.reddit_search, "_blocked_reason", "offline 403"):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(cfg, sc_search=sc_search)

        # Every asked path finished and failed. Only the return margin was crossed.
        assert lanes == [135.0]
        assert clock[0] == 133.2 and not seen_urls
        assert ("web", health.TIMEOUT) not in marks
        assert [item["url"] for item in items] == ([thread] if backfill == "result" else [])
        outcome = artifact.get("_source_outcome")
        if backfill == "result":
            assert outcome is None
        else:
            assert outcome["state"] == health.UNREACHABLE and "search blocked" in outcome["detail"]

    @pytest.mark.parametrize("margin_crossed", [False, True], ids=["in-margin", "past-margin"])
    def test_incomplete_empty_backfill_keeps_its_note_ahead_of_the_margin_timeout(self, margin_crossed):
        from lib import reddit_keyless

        clock = [100.0]
        note = "Reddit ScrapeCreators backfill stopped at its deadline before completing"
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "_research_deadline": 170.0}

        def web_floor(*args, **kwargs):
            clock[0] = 133.2 if margin_crossed else 100.0  # 133.2 is inside the 135 lane's 2s margin
            return []

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline), \
             mock.patch.object(reddit_keyless, "_web_floor", side_effect=web_floor):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=lambda *a, **k: {"items": [], "incomplete": True, "incomplete_note": note},
            )

        assert not items and not seen_urls
        outcome = artifact["_source_outcome"]
        assert outcome["state"] == health.TIMEOUT and outcome["detail"] == note

    @pytest.mark.parametrize("failure, note", [
        (http.HTTPError("HTTP 403: refused", 403), "SearXNG fallback refused (403)"),
        (http.HTTPError("HTTP 429: slow down", 429), "SearXNG fallback throttled (429)"),
    ])
    def test_searxng_failure_is_noted_without_becoming_a_reddit_failure(self, failure, note):
        def refuse(*args, **kwargs):
            http._record_failure(failure)
            raise failure

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, html_fetch=lambda *args, **kwargs: "", searxng_get=refuse,
        )

        assert not items
        assert note in artifact.get("_source_outcome_detail", "")
        assert artifact.get("_source_outcome", {}).get("state") not in (health.UNREACHABLE, health.RATE_LIMITED)
        assert "searx.example" not in str(artifact)

    def test_searxng_deadline_is_a_real_timeout_not_a_reddit_failure(self):
        def run_out(*args, **kwargs):
            failure = http.DeadlineExceeded()
            http._record_failure(failure)
            raise failure

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, html_fetch=lambda *args, **kwargs: "", searxng_get=run_out,
        )

        assert not items
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT

    @pytest.mark.parametrize("failure, note", [
        (http.HTTPError("HTTP 403: refused", 403), "SearXNG fallback refused (403)"),
        (http.HTTPError("HTTP 429: slow down", 429), "SearXNG fallback throttled (429)"),
    ])
    def test_sc_first_public_dispatch_keeps_the_searxng_note(self, failure, note):
        def refuse(*args, **kwargs):
            http._record_failure(failure)
            raise failure

        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
               "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, sc_search=lambda *a, **k: {}, html_fetch=lambda *a, **k: "", searxng_get=refuse,
        )

        assert not items
        assert note in artifact.get("_source_outcome_detail", "")
        assert "_source_outcome_detail_state" not in artifact
        assert artifact.get("_source_outcome", {}).get("state") not in (health.UNREACHABLE, health.RATE_LIMITED)

    def test_sc_first_searxng_note_leaves_captured_http_priority_alone(self):
        def run(searxng_get):
            reddit_discover.reset_run_state()
            web_search_keyless.reset_rung_health()

            def sc_search(*args, **kwargs):
                http._record_failure(http.HTTPError("HTTP 429: paid primary throttled", 429))
                return {}

            cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
                   "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
            return self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=lambda *a, **k: "", searxng_get=searxng_get,
            )[1]

        def refuse(*args, **kwargs):
            failure = http.HTTPError("HTTP 403: refused", 403)
            http._record_failure(failure)
            raise failure

        baseline = run(lambda *a, **k: {"results": []})
        noted = run(refuse)

        assert noted["_source_outcome"]["state"] == baseline["_source_outcome"]["state"]
        # The reason stays first and intact. The note only follows it.
        assert noted["_source_outcome"]["detail"] == f"{baseline['_source_outcome']['detail']}; SearXNG fallback refused (403)"
        assert "SearXNG" not in baseline.get("_source_outcome_detail", "")
        assert "SearXNG fallback refused (403)" in noted["_source_outcome_detail"]

    def test_public_first_early_timeout_keeps_the_observed_searxng_note(self):
        clock = [100.0]

        def refuse(*args, **kwargs):
            failure = http.HTTPError("HTTP 403: refused", 403)
            http._record_failure(failure)
            clock[0] = 158.0  # Past the web stage's own cutoff and inside the lane's 2s return margin.
            raise failure

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": 170.0}
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            items, artifact, _ = self._run_pipeline_reddit_with_searxng(
                cfg, html_fetch=lambda *a, **k: "", searxng_get=refuse,
            )

        assert not items
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert "SearXNG fallback refused (403)" in artifact["_source_outcome_detail"]
        assert "_source_outcome_detail_state" not in artifact

    def test_public_first_early_failure_keeps_the_observed_searxng_note(self):
        def refuse(*args, **kwargs):
            failure = http.HTTPError("HTTP 403: refused", 403)
            http._record_failure(failure)
            raise failure

        def blocked_html(*args, **kwargs):
            http._record_failure(http.HTTPError("HTTP 403: HTML rung blocked", 403))
            return None

        from lib import reddit_keyless

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        with mock.patch.object(reddit_keyless.reddit_search, "_blocked_reason", "offline 403"):
            items, artifact, _ = self._run_pipeline_reddit_with_searxng(
                cfg, html_fetch=blocked_html, searxng_get=refuse,
            )

        assert not items
        assert artifact["_source_outcome"]["state"] == health.UNREACHABLE
        assert "SearXNG fallback refused (403)" in artifact["_source_outcome_detail"]

    def test_public_first_backfill_timeout_keeps_its_detail_and_gains_the_searxng_note(self):
        note = "paid backfill stopped at its deadline before completing"

        def refuse(*args, **kwargs):
            failure = http.HTTPError("HTTP 429: slow down", 429)
            http._record_failure(failure)
            raise failure

        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, sc_search=lambda *a, **k: {"items": [], "incomplete": True, "incomplete_note": note},
            html_fetch=lambda *a, **k: "", searxng_get=refuse,
        )

        assert not items
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert artifact["_source_outcome"]["detail"] == f"{note}; SearXNG fallback throttled (429)"
        assert artifact["_source_outcome_detail"] == "SearXNG fallback throttled (429)"

    @staticmethod
    def _searxng_refusal(status, after=None):
        def refuse(*args, **kwargs):
            failure = http.HTTPError(f"HTTP {status}: refused", status)
            http._record_failure(failure)
            if after is not None:
                after()
            raise failure

        return refuse

    def test_report_keeps_the_note_with_a_real_cutoff_timeout(self):
        from lib import reddit_keyless

        clock = [100.0]
        lanes = []
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": 170.0}
        refuse = self._searxng_refusal(403, after=lambda: clock.__setitem__(0, 158.0))
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane):
            report, _, _ = self._run_pipeline_reddit_with_searxng(
                cfg, html_fetch=lambda *a, **k: "", searxng_get=refuse, full_report=True,
            )

        outcome = report.source_status["reddit"]
        assert lanes == [159.5]
        assert outcome.state == health.TIMEOUT
        assert outcome.detail == "Reddit ran out of search time before it found threads; SearXNG fallback refused (403)"

    def test_report_keeps_a_captured_paid_429_and_its_reason_with_the_note(self):
        def sc_search(*args, **kwargs):
            http._record_failure(http.HTTPError("HTTP 429: paid primary throttled", 429))
            return {}

        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
               "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        report, _, _ = self._run_pipeline_reddit_with_searxng(
            cfg, sc_search=sc_search, html_fetch=lambda *a, **k: "",
            searxng_get=self._searxng_refusal(403), full_report=True,
        )

        outcome = report.source_status["reddit"]
        assert outcome.state == health.RATE_LIMITED
        assert outcome.detail == "HTTP 429: paid primary throttled; SearXNG fallback refused (403)"

    def test_report_keeps_a_paid_backfill_timeout_and_its_note(self):
        note = "paid backfill stopped at its deadline before completing"
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        report, _, _ = self._run_pipeline_reddit_with_searxng(
            cfg, sc_search=lambda *a, **k: {"items": [], "incomplete": True, "incomplete_note": note},
            html_fetch=lambda *a, **k: "", searxng_get=self._searxng_refusal(429), full_report=True,
        )

        outcome = report.source_status["reddit"]
        assert outcome.state == health.TIMEOUT
        assert outcome.detail == f"{note}; SearXNG fallback throttled (429)"

    def test_report_without_a_fallback_failure_has_no_note(self):
        clock = [100.0]
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": 170.0}

        def html_past_cutoff(*args, **kwargs):
            clock[0] = 158.0
            return ""

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            cutoff, _, _ = self._run_pipeline_reddit_with_searxng(
                cfg, html_fetch=html_past_cutoff, full_report=True,
            )
        clean, _, _ = self._run_pipeline_reddit_with_searxng(
            {"LAST30DAYS_SEARXNG_URL": "https://searx.example"},
            html_fetch=lambda *a, **k: "", searxng_get=lambda *a, **k: {"results": []}, full_report=True,
        )

        assert cutoff.source_status["reddit"].detail == "Reddit ran out of search time before it found threads"
        assert "SearXNG" not in str(clean.source_status["reddit"].detail)

    def test_report_for_a_served_run_keeps_the_note_and_the_ok_state(self):
        site_hit = {
            "title": "IQ3 quantization discussion",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/site123/",
            "date": "2026-05-20",
            "engagement": {"score": 25, "num_comments": 3},
            "relevance": 0.9,
            "metadata": {"post_id": "site123"},
        }
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        report, _, _ = self._run_pipeline_reddit_with_searxng(
            cfg, html_fetch=lambda *a, **k: "", site_hits=[site_hit],
            searxng_get=self._searxng_refusal(403), full_report=True,
        )

        outcome = report.source_status["reddit"]
        assert outcome.state == health.OK and outcome.items_returned >= 1
        assert "SearXNG fallback refused (403)" in outcome.detail

    @pytest.mark.parametrize("note_first", [True, False], ids=["note-first", "note-second"])
    def test_report_keeps_the_note_from_either_stream_of_the_same_source(self, note_first):
        import time

        from lib import pipeline

        reason = "Reddit ran out of search time before it found threads"
        plain = {"_source_outcome": {"state": health.TIMEOUT, "detail": reason, "attempted": True}}
        noted = {**plain, "_source_outcome_detail": "SearXNG fallback refused (403)"}

        def stream(*args, **kwargs):
            is_noted = kwargs["subquery"].label == "noted"
            if is_noted != note_first:
                time.sleep(0.4)  # Streams are recorded as they finish, so this one finishes second.
            return [], dict(noted if is_noted else plain)

        labels = ("noted", "plain") if note_first else ("plain", "noted")
        with mock.patch.object(pipeline, "_retrieve_stream_impl", side_effect=stream):
            report, _, _ = self._run_pipeline_reddit_with_searxng(
                {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}, full_report=True, labels=labels,
                report_depth="default",
            )

        outcome = report.source_status["reddit"]
        assert outcome.state == health.TIMEOUT
        assert outcome.detail == f"{reason}; SearXNG fallback refused (403)"
        assert report.errors_by_source["reddit"] == outcome.detail

    def test_keep_lane_notes_helper_edge_cases(self):
        from lib import pipeline, schema

        def bundle_with(detail, failed=True):
            bundle = schema.RetrievalBundle()
            if failed:
                bundle.record_failure("reddit", health.TIMEOUT, detail)
            else:
                bundle.add_items("primary", "reddit", [])
            return bundle

        nothing = schema.RetrievalBundle()
        pipeline._keep_reddit_lane_notes(nothing, "a note")
        assert "reddit" not in nothing.source_status

        held = bundle_with("the reason")
        pipeline._keep_reddit_lane_notes(held, "a note")
        assert held.source_status["reddit"].detail == "the reason; a note"
        assert held.source_status["reddit"].state == health.TIMEOUT
        assert held.errors_by_source["reddit"] == "the reason; a note"

        again = bundle_with("the reason; a note")
        pipeline._keep_reddit_lane_notes(again, "a note")
        assert again.source_status["reddit"].detail == "the reason; a note"

        partly = bundle_with("the reason; one")
        pipeline._keep_reddit_lane_notes(partly, "one; two")
        assert partly.source_status["reddit"].detail == "the reason; one; two"

        served = bundle_with(None, failed=False)
        pipeline._keep_reddit_lane_notes(served, "a note")
        assert served.source_status["reddit"].detail is None

        # A delivered source that only carries a detail is not a failure and is left alone.
        detailed = bundle_with(None, failed=False)
        detailed.record_detail("reddit", "search served 1")
        pipeline._keep_reddit_lane_notes(detailed, "a note")
        assert detailed.source_status["reddit"].detail == "search served 1"
        assert "reddit" not in detailed.errors_by_source

        # The error text is only extended when it lacks the notes, and a missing entry is fine.
        known = bundle_with("the reason")
        known.errors_by_source["reddit"] = "the reason; a note"
        pipeline._keep_reddit_lane_notes(known, "a note")
        assert known.errors_by_source["reddit"] == "the reason; a note"
        assert known.source_status["reddit"].detail == "the reason; a note"

        unlisted = bundle_with("the reason")
        unlisted.errors_by_source.pop("reddit")
        pipeline._keep_reddit_lane_notes(unlisted, "a note")
        assert "reddit" not in unlisted.errors_by_source
        assert unlisted.source_status["reddit"].detail == "the reason; a note"

    def test_another_sources_note_never_lands_on_reddit(self):
        import time

        from lib import pipeline

        reason = "Reddit ran out of search time before it found threads"

        def stream(*args, **kwargs):
            if kwargs["source"] == "polymarket":
                time.sleep(0.4)  # Recorded after Reddit's failure, so a leaked note would have a target.
                return [], {
                    "_source_outcome": {"state": health.ERROR, "detail": "market feed broke", "attempted": True},
                    "_source_outcome_detail": "a market note",
                }
            return [], {"_source_outcome": {"state": health.TIMEOUT, "detail": reason, "attempted": True}}

        with mock.patch.object(pipeline, "_retrieve_stream_impl", side_effect=stream):
            report, _, _ = self._run_pipeline_reddit_with_searxng(
                {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}, full_report=True,
                report_sources=("reddit", "polymarket"),
            )

        assert report.source_status["reddit"].detail == reason
        assert "a market note" not in str(report.source_status["reddit"].detail)
        assert report.source_status["polymarket"].state == health.ERROR
        assert report.source_status["polymarket"].detail == "market feed broke"

    def test_report_without_any_note_stays_unchanged_across_two_streams(self):
        from lib import pipeline

        reason = "Reddit ran out of search time before it found threads"
        plain = {"_source_outcome": {"state": health.TIMEOUT, "detail": reason, "attempted": True}}
        with mock.patch.object(pipeline, "_retrieve_stream_impl", side_effect=lambda *a, **k: ([], dict(plain))):
            report, _, _ = self._run_pipeline_reddit_with_searxng(
                {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}, full_report=True, labels=("one", "two"),
                report_depth="default",
            )

        assert report.source_status["reddit"].detail == reason

    def test_fold_helper_edge_cases(self):
        from lib import pipeline

        typed = {"state": health.TIMEOUT, "detail": "ran out of time", "attempted": True}
        served = {"_source_outcome_detail": "search served 1"}
        no_note = {"_source_outcome": typed}
        already = {"_source_outcome": {**typed, "detail": "ran out of time; a note"}, "_source_outcome_detail": "a note"}
        original = {"_source_outcome": typed, "_source_outcome_detail": "a note"}

        assert pipeline._fold_reddit_lane_notes(None) is None
        assert pipeline._fold_reddit_lane_notes(served) is served
        assert pipeline._fold_reddit_lane_notes(no_note) is no_note
        assert pipeline._fold_reddit_lane_notes(already) is already
        folded = pipeline._fold_reddit_lane_notes(original)
        assert folded["_source_outcome"]["detail"] == "ran out of time; a note"
        assert folded["_source_outcome"]["state"] == health.TIMEOUT
        # The input is never changed, so a replayed artifact cannot pick up the note twice.
        assert typed["detail"] == "ran out of time" and original["_source_outcome"] is typed
        assert pipeline._fold_reddit_lane_notes(folded) is folded

    @pytest.mark.parametrize("source, folded", [("reddit", True), ("hackernews", False)])
    def test_only_reddit_outcomes_get_their_lane_notes_folded(self, source, folded):
        from lib import pipeline

        artifact = {
            "_source_outcome": {"state": health.ERROR, "detail": "boom", "attempted": True},
            "_source_outcome_detail": "a side note",
        }
        with mock.patch.object(pipeline, "_retrieve_stream_impl", return_value=([], dict(artifact))):
            _, result = pipeline._retrieve_stream(source=source, config={})

        assert result["_source_outcome"]["state"] == health.ERROR
        assert result["_source_outcome"]["detail"] == ("boom; a side note" if folded else "boom")
        assert result["_source_outcome_detail"] == "a side note"

    @pytest.mark.parametrize("detail", [
        "Reddit ran out of search time before it found threads",
        "HTTP 429: paid primary throttled",
        "paid backfill stopped at its deadline before completing",
        "Reddit backup failed after 1 public items: HTTP 500; search blocked",
        "Reddit primary failed; public fallback returned 1 items: connection error",
    ])
    @pytest.mark.parametrize("state", ["blocked", "throttled", "timeout", "error"])
    def test_a_searxng_note_can_never_change_how_a_detail_is_classified(self, state, detail):
        from lib import reddit_keyless

        note = f"SearXNG fallback {reddit_keyless._SEARXNG_NOTES[state]}"

        assert http.classify_failure(message=f"{detail}; {note}") == http.classify_failure(message=detail)
        assert http.classify_failure(message=note) == health.ERROR

    @pytest.mark.parametrize("with_partial", [True, False], ids=["partial", "empty"])
    def test_paid_backfill_exception_keeps_the_searxng_note(self, with_partial):
        site_hit = {
            "title": "IQ3 quantization discussion",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/site123/",
            "date": "2026-05-20",
            "engagement": {"score": 25, "num_comments": 3},
            "relevance": 0.9,
            "metadata": {"post_id": "site123"},
        }

        def failing_backfill(*args, **kwargs):
            raise RuntimeError("paid lookup broke")

        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, sc_search=failing_backfill, html_fetch=lambda *a, **k: "",
            site_hits=[site_hit] if with_partial else [], searxng_get=self._searxng_refusal(403),
        )

        if with_partial:
            # The retained partial, the chosen detail state and the paid failure all stay as they were.
            assert [i["metadata"]["post_id"] for i in items] == ["site123"]
            assert artifact["_source_outcome_detail_state"] == health.ERROR
            assert artifact["_source_outcome_detail"] == (
                "search served 1; ScrapeCreators backfill failed after 1 free items: paid lookup broke; "
                "SearXNG fallback refused (403)"
            )
            assert "ScrapeCreators backfill failed" in artifact["_source_outcome_detail"]
            assert "_source_outcome" not in artifact
        else:
            assert artifact["_source_outcome"]["detail"].startswith("Reddit backup failed after")
        assert "SearXNG fallback refused (403)" in artifact["_source_outcome_detail"]

    def test_paid_backfill_exception_without_a_searxng_failure_has_no_note(self):
        def failing_backfill(*args, **kwargs):
            raise RuntimeError("paid lookup broke")

        for cfg in ({"SCRAPECREATORS_API_KEY": "sc-dummy-key"},
                    {"SCRAPECREATORS_API_KEY": "sc-dummy-key", "LAST30DAYS_SEARXNG_URL": "https://searx.example"}):
            reddit_discover.reset_run_state()
            items, artifact, _ = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=failing_backfill, html_fetch=lambda *a, **k: "",
                searxng_get=lambda *a, **k: {"results": []},
            )
            assert "SearXNG" not in str(artifact)

    def test_cutoff_before_searxng_runs_fabricates_no_note(self):
        clock = [100.0]

        def html_past_cutoff(*args, **kwargs):
            clock[0] = 158.0
            return ""

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": 170.0}
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, html_fetch=html_past_cutoff,
            )

        assert not items and seen_urls == []
        assert artifact["_source_outcome"]["state"] == health.TIMEOUT
        assert "SearXNG" not in str(artifact)

    def test_observed_searxng_failure_survives_a_later_clean_query(self):
        from lib import reddit_keyless

        outcomes = iter([http.HTTPError("HTTP 429: slow down", 429), {"results": []}])

        def searxng_get(*args, **kwargs):
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                http._record_failure(outcome)
                raise outcome
            return outcome

        with mock.patch.object(web_search_keyless.http, "get_text", return_value=""), \
             mock.patch.object(web_search_keyless.http, "get", side_effect=searxng_get) as sx, \
             reddit_keyless.lane_paths(), reddit_keyless.lane_notes() as notes:
            reddit_keyless._web_floor(
                "IQ3 quantization", "quick", ["LocalLLaMA"], None, "2026-05-01", "2026-05-31", None,
                {"LAST30DAYS_SEARXNG_URL": "https://searx.example"},
            )

        assert sx.call_count == 2
        assert notes == ["SearXNG fallback throttled (429)"]

    def test_searxng_failure_is_kept_apart_from_the_latest_rung_and_deduplicated(self):
        floor = reddit_discover.WebFloor()
        for state in ["blocked", "throttled", "blocked", "empty"]:
            floor.record_query("q", {"brave": "empty", "searxng": state}, [], searxng=state)

        # The rung map still holds the latest state. The failures are kept, in order, once each.
        assert floor.rungs == {"brave": "empty", "searxng": "empty"}
        assert floor.searxng_failures == ["blocked", "throttled"]
        assert floor.snapshot().searxng_failures == ["blocked", "throttled"]

    def test_only_known_failure_states_are_kept(self):
        floor = reddit_discover.WebFloor()
        for state in ["ok", "empty", "skipped", "https://searx.example/secret", None]:
            floor.record_query("q", {}, [], searxng=state)

        assert floor.searxng_failures == []

    def test_searxng_clean_miss_adds_no_note(self):
        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example"}
        items, artifact, _ = self._run_pipeline_reddit_with_searxng(
            cfg, html_fetch=lambda *args, **kwargs: "", searxng_get=lambda *a, **k: {"results": []},
        )

        assert not items and "SearXNG" not in artifact.get("_source_outcome_detail", "")

    @pytest.mark.parametrize("site", ["open", "read"])
    def test_transient_config_read_error_is_retried_not_cached_as_missing(self, site, tmp_path, monkeypatch):
        import errno
        import os

        env.CONFIG_FILE.write_text("LAST30DAYS_SEARXNG_URL=https://global.example\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)
        real_open, real_fdopen, tripped = os.open, os.fdopen, []

        def flaky_open(path, *args, **kwargs):
            if site == "open" and not tripped and os.fspath(path) == os.fspath(env.CONFIG_FILE):
                tripped.append(1)
                raise PermissionError(errno.EACCES, "file is in use")
            return real_open(path, *args, **kwargs)

        def flaky_fdopen(fd, *args, **kwargs):
            if site == "read" and not tripped:
                tripped.append(1)
                raise OSError(errno.EIO, "input/output error")
            return real_fdopen(fd, *args, **kwargs)

        monkeypatch.setattr(os, "open", flaky_open)
        monkeypatch.setattr(os, "fdopen", flaky_fdopen)
        generation = reddit_discover._RUN_GENERATION

        assert reddit_discover._standalone_searxng_url(generation) is None
        assert reddit_discover._standalone_searxng_url(generation) == "https://global.example"
        assert tripped == [1]

    @pytest.mark.parametrize("how", ["absent", "unsafe"])
    def test_absent_or_unsafe_config_file_stays_a_cached_miss(self, how, tmp_path, monkeypatch):
        import errno
        import os

        env.CONFIG_FILE.write_text("LAST30DAYS_SEARXNG_URL=https://global.example\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("LAST30DAYS_SEARXNG_URL", raising=False)
        real_open, opens = os.open, []

        def refuse_open(path, *args, **kwargs):
            if os.fspath(path) == os.fspath(env.CONFIG_FILE):
                opens.append(1)
                raise OSError(errno.ELOOP, "symlink refused") if how == "unsafe" else FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", refuse_open)
        generation = reddit_discover._RUN_GENERATION

        assert reddit_discover._standalone_searxng_url(generation) == ""
        assert reddit_discover._standalone_searxng_url(generation) == ""
        assert opens == [1]

    def test_sc_first_public_fallback_gets_a_proportionate_margin_at_handoff(self):
        from lib import reddit_keyless

        clock = [100.0]
        lanes = []
        site_hit = {
            "title": "IQ3 quantization discussion",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/site123/",
            "date": "2026-05-20",
            "engagement": {"score": 25, "num_comments": 3},
            "relevance": 0.9,
            "metadata": {"post_id": "site123"},
        }
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
               "_research_deadline": 200.0}
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        def sc_search(*args, **kwargs):
            clock[0] = 185.0  # The paid primary came back empty with 15s left.
            return {}

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            return call(*args, **kwargs)

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline), \
             mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane):
            items, artifact, _ = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=lambda *a, **k: "", site_hits=[site_hit],
            )

        # With 15s left the margin is min(20, 15 * 0.15) = 2.25s, so the fallback ends at 197.75 and never past 200.
        assert lanes == [pytest.approx(197.75)]
        assert [i["metadata"]["post_id"] for i in items] == ["site123"]

    def test_sc_first_public_fallback_gets_no_extra_allowance_when_time_is_spent(self):
        from lib import reddit_keyless

        clock = [100.0]
        lanes = []
        cfg = {"SCRAPECREATORS_API_KEY": "sc-dummy-key", env.REDDIT_BACKEND_PIN_VAR: "scrapecreators",
               "_research_deadline": 200.0}
        real_lane = reddit_keyless.lane_deadline

        def record_lane(deadline):
            lanes.append(deadline)
            return real_lane(deadline)

        def sc_search(*args, **kwargs):
            clock[0] = 201.0  # Already past the run deadline when the primary returns.
            return {}

        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "lane_deadline", side_effect=record_lane):
            items, artifact, _ = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=sc_search, html_fetch=lambda *a, **k: "",
            )

        assert lanes == [200.0] and not items

    @pytest.mark.parametrize("sc_primary", [False, True], ids=["public-primary", "sc-primary"])
    def test_pipeline_keeps_site_hit_without_late_searxng_on_either_primary_path(self, sc_primary):
        from lib import reddit_keyless

        clock = [100.0]
        html_urls = []

        def html_fetch(url, *args, **kwargs):
            html_urls.append(url)
            # Too little time for a new lane stage, but the site hit is ready.
            clock[0] = 179.0
            return None

        def run_inline(_wait_until, _stage, _default, call, /, *args, **kwargs):
            # Only replace the executor wait so the mocked clock cannot race it.
            return call(*args, **kwargs)

        cfg = {"LAST30DAYS_SEARXNG_URL": "https://searx.example", "_research_deadline": 200.0}
        if sc_primary:
            cfg.update({"SCRAPECREATORS_API_KEY": "sc-dummy-key",
                        env.REDDIT_BACKEND_PIN_VAR: "scrapecreators"})
        site_hit = {
            "title": "IQ3 quantization discussion",
            "url": "https://www.reddit.com/r/LocalLLaMA/comments/site123/",
            "date": "2026-05-20",
            "engagement": {"score": 25, "num_comments": 3},
            "relevance": 0.9,
            "metadata": {"post_id": "site123"},
        }
        with mock.patch("time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(reddit_keyless, "_within", side_effect=run_inline):
            items, artifact, seen_urls = self._run_pipeline_reddit_with_searxng(
                cfg, sc_search=(lambda *a, **kw: {}) if sc_primary else None,
                html_fetch=html_fetch, site_hits=[site_hit],
            )

        assert seen_urls == []
        assert len(html_urls) == 1 and "search.brave.com" in html_urls[0]
        assert [i["metadata"]["post_id"] for i in items] == ["site123"]
        assert "_source_outcome" not in artifact

    def test_blocked_searxng_url_is_rejected_by_downstream_guard_without_get(self):
        cfg = {"LAST30DAYS_SEARXNG_URL": "http://169.254.169.254"}
        with mock.patch.object(web_search_keyless.http, "get_text", side_effect=self._throttled_html), \
             mock.patch.object(web_search_keyless.http, "get") as sx_get:
            floor = reddit_discover.web_refs("IQ3 quantization", config=cfg)

        sx_get.assert_not_called()
        assert floor.refs == []
