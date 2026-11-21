"""Tests for scripts/lib/reddit_discover.py: the keyless web floor and topic bar."""

from pathlib import Path
from unittest import mock

import pytest

from lib import http, reddit_discover, web_search_keyless

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
        import time

        hits = [_hit("https://www.reddit.com/r/Kanye/comments/abc123/x/", "Kanye one")]
        stub, calls = _search_stub({"site:reddit.com Kanye West": hits})
        deadline = time.monotonic() + 0.05

        def slow_first(query, date_range, config, count=5, **kwargs):
            out = stub(query, date_range, config, count, **kwargs)
            time.sleep(0.1)  # the deadline passes during the first query
            return out

        partial = reddit_discover.WebFloor()
        floor = reddit_discover.web_refs(
            "Kanye West", subreddits=["hiphopheads"], search=slow_first,
            deadline=deadline, floor=partial,
        )
        assert floor is partial
        assert len(calls) == 1
        assert [r.post_id for r in partial.refs] == ["abc123"]
