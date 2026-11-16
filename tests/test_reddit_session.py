"""The opt-in logged-in Reddit session layer (lib/reddit_session.py).

Pages are scrubbed captures of logged-in reddit.com search fragments and a
thread JSON (fixtures/reddit_session_*). The session transport is replaced by
a fake that routes by URL, so no test reaches reddit.com and none sleeps.
"""

from __future__ import annotations

import contextlib
import io
import json
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from lib import (
    env, http, normalize, reddit_health, reddit_hydrate, reddit_keyless, reddit_refs,
    reddit_search, reddit_session, render, schema,
)
from lib.reddit_refs import ThreadRef, canonical_thread

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
COOKIE = "dummy-session-cookie-0123456789abcdef"
TOPIC = "Ghostty terminal"
FROM, TO = "2026-10-16", "2026-11-15"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


POSTS = _fixture("reddit_session_posts.html")
COMMENTS = _fixture("reddit_session_comments.html")
COMMUNITIES = _fixture("reddit_session_communities.html")
NO_COMMENTS = _fixture("reddit_session_comments_no_results.html")
BLOCKED = _fixture("reddit_session_blocked_403.html")
LOGGED_OUT = _fixture("reddit_session_logged_out_shell.html")
THREAD = json.loads(_fixture("reddit_session_thread.json"))


def thread_json(post_id: str, **fields) -> str:
    data = json.loads(json.dumps(THREAD))
    post = data[0]["data"]["children"][0]["data"]
    post.update({"id": post_id, "permalink": f"/r/{fields.get('subreddit', post['subreddit'])}/comments/{post_id}/t/"})
    post.update(fields)
    return json.dumps(data)


class FakeReddit:
    """Routes session GETs to canned pages; records each URL and cookie header."""

    def __init__(self, *, posts=POSTS, comments=COMMENTS, communities=COMMUNITIES, threads=None,
                 thread_status=None, page_status=200):
        self.pages = {"posts": posts, "comments": comments, "communities": communities}
        self.threads = threads or {}
        self.thread_status = thread_status or {}
        self.page_status = page_status
        self.calls: list[tuple[str, str]] = []

    def __call__(self, url, cookie_header, **_kwargs):
        self.calls.append((url, cookie_header))
        if ".json" in url:
            pid = url.split("/comments/", 1)[1].split(".json", 1)[0]
            status = self.thread_status.get(pid, 200)
            if status != 200:
                return status, BLOCKED, False
            return 200, self.threads.get(pid) or thread_json(pid), False
        for kind, body in self.pages.items():
            if f"type={kind}" in url:
                if self.page_status != 200:
                    return self.page_status, BLOCKED, False
                return 200, body, False
        return 404, "", False

    @property
    def urls(self):
        return [url for url, _ in self.calls]


@pytest.fixture(autouse=True)
def _no_free_chain_network(monkeypatch):
    """The free chain behind the session (Arctic, shreddit comments) finds nothing."""
    from lib import reddit_arctic, reddit_shreddit

    monkeypatch.setattr(reddit_arctic, "fetch_posts_by_ids", lambda ids, **_k: ({}, "down"))
    monkeypatch.setattr(reddit_arctic, "fetch_comment_tree", lambda *_a, **_k: None)
    monkeypatch.setattr(reddit_shreddit, "fetch_comments", lambda *_a, **_k: {})


@pytest.fixture
def fake(monkeypatch):
    transport = FakeReddit()
    monkeypatch.setattr(http, "reddit_session_get", transport)
    return transport


def _session(**kwargs) -> reddit_session.Session:
    return reddit_session.Session(COOKIE, browser="chrome", **kwargs)


def _lane(session, topic=TOPIC, **kwargs):
    """Run the keyless lane with ``session`` open; returns (posts, paths, notes)."""
    with reddit_keyless.lane_paths() as paths, reddit_keyless.lane_notes() as notes, \
            reddit_session.lane(session, topic):
        posts = reddit_keyless.search_and_enrich(topic, FROM, TO, depth="quick", **kwargs)
    return posts, dict(paths), list(notes)


# --- parsers --------------------------------------------------------------------------

class TestParsers:
    def test_posts_page_parses_to_canonical_session_refs(self, fake):
        found = reddit_session.discover(_session(), TOPIC, depth="quick")
        assert found.state == reddit_health.OK
        assert found.refs
        for ref in found.refs:
            assert "session" in ref.provenance
            assert ref.url == canonical_thread(ref.url).url
            assert ref.url.startswith("https://www.reddit.com/r/")
        assert all(hit["engagement_source"] == reddit_refs.LIVE for hit in found.hits.values())

    def test_comments_page_yields_comment_text_date_and_parent_thread(self):
        hits = reddit_session.parse_comments(COMMENTS)
        assert hits
        hit = hits[0]
        assert hit["comment_id"] and hit["post_id"] and hit["body"]
        assert hit["comment_date"] == "2026-11-15"
        assert hit["permalink"].endswith(f"/{hit['comment_id']}/")
        assert f"/comments/{hit['post_id']}/" in hit["permalink"]
        # A comment's own votes, not its thread's counter.
        assert all(h["comment_score"] != h["post_score"] for h in hits if h["post_score"])

    def test_valid_comments_and_communities_pages_are_recognized(self):
        assert reddit_session.unrecognized_comments(COMMENTS) is None
        assert reddit_session.unrecognized_comments(NO_COMMENTS) is None
        assert reddit_session.unrecognized_communities(COMMUNITIES) is None
        assert [c["name"] for c in reddit_session.parse_communities(COMMUNITIES)][:2] == ["Ghostty", "commandline"]
        # A recognized page never reads as a block to the keyless validator's vocabulary.
        for text in (COMMENTS, COMMUNITIES):
            reason = reddit_session.unrecognized_comments(text) or reddit_session.unrecognized_communities(text) or ""
            assert not any(signal in reason for signal in reddit_search._BLOCK_SIGNALS)

    def test_valid_comments_page_never_sets_a_session_skip(self, fake):
        reddit_session.discover(_session(), TOPIC, depth="quick")
        assert reddit_health.skip_reason("session", cookie_fp=reddit_session.cookie_fp(COOKIE)) is None
        entry = reddit_health.entries().get("session") or {}
        assert entry.get("state") != reddit_health.BLOCKED

    def test_page_state_reads_the_logged_in_marker(self):
        assert reddit_session.page_state(200, POSTS) == reddit_session.OK
        assert reddit_session.page_state(200, LOGGED_OUT) == reddit_session.EXPIRED
        assert reddit_session.page_state(403, BLOCKED) == reddit_session.EXPIRED
        assert reddit_session.page_state(429, "") == reddit_session.THROTTLED
        assert reddit_session.page_state(None, None) == reddit_session.ERROR


# --- expiry, blocks and skips ----------------------------------------------------------

class TestExpiry:
    def test_ae2_logged_out_shell_is_session_expired_and_the_floor_still_serves(self, fake):
        fake.pages["posts"] = LOGGED_OUT
        web_ref = canonical_thread("https://www.reddit.com/r/Ghostty/comments/1abcdef/x/")
        web_ref.provenance.add("web")
        web_ref.title, web_ref.date = "Ghostty terminal tips", "2026-11-10"
        floor = reddit_keyless.reddit_discover.WebFloor(refs=[web_ref], queries=["q"], rungs={"brave": "ok"})
        with mock.patch.object(reddit_keyless.reddit_discover, "web_refs", return_value=floor), \
                mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]):
            posts, paths, notes = _lane(_session())
        assert paths["session"] == reddit_health.SESSION_EXPIRED
        assert any("log in to reddit.com in chrome" in note for note in notes)
        assert posts and posts[0]["url"].endswith("/comments/1abcdef/")
        detail = reddit_health.served_detail(posts, paths)
        assert "session session-expired" in detail and "web served 1" in detail
        assert "no path served" not in detail

    def test_403_search_page_with_the_cookie_is_session_expired_and_persists_under_its_fp(self, fake):
        fake.page_status = 403
        session = _session()
        found = reddit_session.discover(session, TOPIC)
        assert found.state == reddit_session.EXPIRED
        assert reddit_health.skip_reason("session", cookie_fp=session.fp) == reddit_health.SKIPPED_CACHED or \
            reddit_health.entries()["session"]["state"] == reddit_health.BLOCKED
        assert reddit_health.entries()["session"]["cookie_fp"] == session.fp

    def test_429_is_retried_once_then_throttled_for_the_run_only(self, fake):
        fake.page_status = 429
        session = _session()
        found = reddit_session.discover(session, TOPIC)
        assert found.state == reddit_session.THROTTLED
        assert sum("type=posts" in url for url in fake.urls) == 2
        assert (reddit_health.entries().get("session") or {}).get("state") != reddit_health.BLOCKED

    def test_ae4_a_new_login_ignores_the_old_logins_skip(self, fake):
        old = reddit_session.cookie_fp("an-old-cookie-value")
        reddit_health.record("session", reddit_health.BLOCKED, "session-expired: logged-out page", cookie_fp=old)
        reddit_health.reset_run_state()
        config = {
            "_BROWSER_COOKIE_MODE": "read", env.REDDIT_CONSENT_VAR: "true", env.REDDIT_FROM_BROWSER_VAR: "chrome",
        }
        session = reddit_session.session_for(config, reader=lambda *_a: (COOKIE, "ok"))
        assert session is not None and session.alive
        reddit_session.reset_run_state()
        reddit_health.reset_run_state()
        same = reddit_session.session_for(config, reader=lambda *_a: ("an-old-cookie-value", "ok"))
        assert same is not None and same.state == reddit_session.EXPIRED

    def test_keyless_block_does_not_stop_session_search(self, fake, monkeypatch):
        monkeypatch.setattr(reddit_search, "_blocked_reason", "HTTP 403: Blocked")
        found = reddit_session.discover(_session(), TOPIC, depth="quick")
        assert found.refs and found.state == reddit_health.OK


# --- privacy -------------------------------------------------------------------------------

class TestPrivacy:
    def _hydrate(self, refs, session, found=None):
        return reddit_hydrate.hydrate(
            refs, topic=TOPIC, from_date=FROM, to_date=TO, comments=False,
            session_hydrator=reddit_session.hydrator(session, found), now=1.9e9,
        )

    def _ref(self, pid, sub="Ghostty", provenance="session"):
        ref = canonical_thread(f"https://www.reddit.com/r/{sub}/comments/{pid}/")
        ref.provenance.add(provenance)
        ref.title, ref.date = "Ghostty terminal question", "2026-11-10"
        return ref

    def test_private_subreddit_dropped_unless_named(self, fake):
        fake.threads["1aaaaaa"] = thread_json("1aaaaaa", subreddit="secretclub", subreddit_type="private",
                                              created_utc=1794355200)
        out = self._hydrate([self._ref("1aaaaaa", "secretclub")], _session())
        assert out.items == []
        named = _session(named=["secretclub"])
        out = self._hydrate([self._ref("1aaaaaa", "secretclub")], named)
        assert [i["metadata"]["post_id"] for i in out.items] == ["1aaaaaa"]

    def test_planner_resolved_private_subreddit_does_not_admit(self, fake):
        fake.threads["1aaaaab"] = thread_json("1aaaaab", subreddit="secretclub", subreddit_type="private")
        config = {"_BROWSER_COOKIE_MODE": "read", env.REDDIT_CONSENT_VAR: "true",
                  env.REDDIT_FROM_BROWSER_VAR: "chrome"}
        session = reddit_session.session_for(config, reader=lambda *_a: (COOKIE, "ok"))
        # The lane's planner-resolved subreddits never reach session.named.
        assert "secretclub" not in session.named
        assert self._hydrate([self._ref("1aaaaab", "secretclub")], session).items == []

    def test_over_18_dropped_and_viewer_fields_absent(self, fake):
        fake.threads["1aaaaac"] = thread_json("1aaaaac", over_18=True)
        assert self._hydrate([self._ref("1aaaaac")], _session()).items == []
        fake.threads["1aaaaad"] = thread_json("1aaaaad", created_utc=1794355200)
        items = self._hydrate([self._ref("1aaaaad")], _session()).items
        assert items
        blob = json.dumps(items)
        for field in ("likes", "saved", "user_reports", "mod_reports", "approved_by", "dummy flair",
                      "dummy-moderator", "dummy report"):
            assert field not in blob

    def test_host_ref_to_a_private_thread_hydrated_through_the_session_is_dropped(self, fake):
        fake.threads["1aaaaae"] = thread_json("1aaaaae", subreddit="secretclub", subreddit_type="private")
        ref = self._ref("1aaaaae", "secretclub", provenance="host")
        assert self._hydrate([ref], _session()).items == []

    def test_quarantined_and_over_18_search_hits_are_dropped_before_hydration(self, fake):
        page = POSTS.replace("&quot;nsfw&quot;:false", "&quot;nsfw&quot;:true")
        fake.pages["posts"] = page
        fake.pages["comments"] = NO_COMMENTS
        found = reddit_session.discover(_session(), TOPIC, depth="quick")
        assert found.refs == [] and found.dropped_private > 0

    def test_ae3_a_403_on_one_private_thread_drops_only_it_and_writes_no_skip(self, fake):
        fake.thread_status["1bbbbbb"] = 403
        fake.threads["1cccccc"] = thread_json("1cccccc", created_utc=1794355200)
        session = _session()
        out = self._hydrate([self._ref("1cccccc"), self._ref("1bbbbbb")], session)
        assert [i["metadata"]["post_id"] for i in out.items] == ["1cccccc"]
        assert session.alive
        assert (reddit_health.entries().get("session") or {}).get("state") != reddit_health.BLOCKED

    def test_session_only_ref_with_no_public_copy_is_left_out(self, fake):
        session = _session()
        found = reddit_session.Discovery()
        ref = self._ref("1dddddd")
        found.hits["1dddddd"] = {"title": "x", "subreddit": "Ghostty", "url": ref.url}
        # The session's budget is spent: no thread JSON, no archive copy.
        session.cap = 0
        out = self._hydrate([ref], session, found)
        assert out.items == []


# --- budget --------------------------------------------------------------------------------

class TestBudget:
    def test_the_61st_request_is_refused(self, fake):
        session = _session()
        with reddit_session.lane(session, "a"):
            answers = [session.fetch(f"https://www.reddit.com/comments/x{i}.json") for i in range(61)]
        assert sum(1 for status, _ in answers if status is not None) == 60
        assert answers[-1] == (None, None)

    @pytest.mark.parametrize("entities,per_entity", [(3, 20), (7, 8)])
    def test_comparison_entities_share_the_cap(self, fake, entities, per_entity):
        session = _session(entity_count=entities)
        assert session.entity_budget() == per_entity
        for n in range(entities):
            with reddit_session.lane(session, f"entity-{n}"):
                for i in range(30):
                    session.fetch(f"https://www.reddit.com/comments/e{n}x{i}.json")
        assert session.requests <= 60
        assert session.requests == per_entity * entities

    def test_memo_hits_cost_nothing(self, monkeypatch):
        calls = []
        monkeypatch.setattr(http, "request", lambda *a, **k: calls.append(a) or POSTS)
        session = _session()
        with reddit_session.lane(session, "a"):
            session.fetch("https://www.reddit.com/svc/shreddit/search/?q=a&type=posts")
            session.fetch("https://www.reddit.com/svc/shreddit/search/?q=a&type=posts")
        assert len(calls) == 1 and session.requests == 1


# --- transport isolation and secrecy ----------------------------------------------------------

class TestTransport:
    def test_session_memo_and_keyless_memo_never_share_a_page(self, monkeypatch):
        url = "https://www.reddit.com/svc/shreddit/search/?q=a&type=posts&t=month&sort=relevance"
        bodies = iter([LOGGED_OUT, POSTS])
        monkeypatch.setattr(http, "request", lambda *a, **k: next(bodies))
        keyless = http.reddit_keyless_get_text(url)
        status, logged_in, from_memo = http.reddit_session_get(url, f"reddit_session={COOKIE}")
        assert keyless == LOGGED_OUT and logged_in == POSTS and not from_memo
        # Each side now answers from its own memo, never from the other's.
        assert http.reddit_keyless_get_text(url) == LOGGED_OUT
        assert http.reddit_session_get(url, f"reddit_session={COOKIE}")[1] == POSTS

    def test_cookie_only_in_the_request_header(self, monkeypatch):
        seen = []

        def fake_request(method, url, headers=None, **kwargs):
            seen.append((url, dict(headers or {}), kwargs))
            raise http.HTTPError("HTTP 403: Blocked", status_code=403, body=BLOCKED)

        monkeypatch.setattr(http, "request", fake_request)
        status, body, _ = http.reddit_session_get(
            "https://www.reddit.com/svc/shreddit/search/?q=a&type=posts", f"reddit_session={COOKIE}",
        )
        assert status == 403 and COOKIE not in (body or "")
        url, headers, kwargs = seen[0]
        assert COOKIE in headers["Cookie"] and COOKIE not in url
        assert kwargs["redirect_guard"] is http.reddit_only_redirect

    def test_redirect_guard_and_cross_origin_strip_keep_the_cookie_on_reddit(self):
        assert http.reddit_only_redirect("https://old.reddit.com/r/x/")
        assert not http.reddit_only_redirect("https://reddit.com.evil.tld/")
        assert not http.reddit_only_redirect("http://www.reddit.com/")
        assert http.reddit_session_get("https://example.com/", "reddit_session=x") == (None, None, False)
        handler = http._StripAuthOnCrossOriginRedirect()
        req = urllib.request.Request("https://www.reddit.com/a", headers={"Cookie": f"reddit_session={COOKIE}"})
        new = handler.redirect_request(req, None, 302, "Found", {}, "https://elsewhere.example/b")
        assert new is not None and "Cookie" not in new.headers and "cookie" not in {k.lower() for k in new.headers}
        guarded = urllib.request.Request("https://www.reddit.com/a", headers={"Cookie": "reddit_session=x"})
        setattr(guarded, http._REDIRECT_GUARD_ATTR, http.reddit_only_redirect)
        with pytest.raises(Exception):
            handler.redirect_request(guarded, None, 302, "Found", {}, "https://elsewhere.example/b")

    def test_cookie_never_reaches_stderr_items_or_errors(self, fake, capsys):
        with mock.patch.object(reddit_keyless.reddit_search, "search", return_value=[]):
            posts, paths, notes = _lane(_session())
        captured = capsys.readouterr()
        assert posts
        for blob in (captured.err, captured.out, json.dumps(posts), json.dumps(paths), json.dumps(notes),
                     repr(_session()), str(_session())):
            assert COOKIE not in blob
        assert all(COOKIE in cookie for _url, cookie in fake.calls)
        assert not any("/api/" in url for url in fake.urls)


# --- live scores, merge and the comment window ----------------------------------------------------

class TestMergeAndWindow:
    def test_live_session_score_replaces_an_arctic_pending_score(self):
        pending = {"engagement": {"score": None, "num_comments": 3}, "engagement_source": reddit_refs.ARCTIC_PENDING,
                   "provenance": ["web"]}
        live = {"engagement": {"score": 240, "num_comments": 12}, "engagement_source": reddit_refs.LIVE,
                "provenance": ["session"]}
        reddit_hydrate.merge_post(pending, live)
        assert pending["engagement"]["score"] == 240
        assert pending["engagement_source"] == reddit_refs.LIVE
        assert pending["provenance"] == ["session", "web"]

    def test_ae9_a_fresh_comment_on_a_60_day_old_thread_is_kept_and_labeled(self, fake):
        old_thread = 1789516800  # 2026-09-16, 60 days before the window end
        fake.threads["1eeeeee"] = thread_json("1eeeeee", created_utc=old_thread)
        ref = canonical_thread("https://www.reddit.com/r/Ghostty/comments/1eeeeee/")
        ref.provenance.add("session")
        ref.matched_comment = reddit_refs.MatchedComment("zz9cmt", created_utc=1794700800,
                                                         permalink=f"{ref.url}x/zz9cmt/")
        found = reddit_session.Discovery()
        found.comments["1eeeeee"] = {"id": "zz9cmt", "score": 4, "date": "2026-11-15", "author": "",
                                     "excerpt": "Ghostty terminal is great now", "url": f"{ref.url}x/zz9cmt/",
                                     "depth": 0, "matched": True}
        out = reddit_hydrate.hydrate([ref], topic=TOPIC, from_date=FROM, to_date=TO, comments=False,
                                     session_hydrator=reddit_session.hydrator(_session(), found), now=1.9e9)
        assert len(out.items) == 1
        item = out.items[0]
        assert item["date"] < FROM
        assert item["matched_comment"]["date"] == "2026-11-15"
        assert any(c.get("matched") for c in item["top_comments"])
        source_items = normalize.normalize_source_items("reddit", out.items, FROM, TO)
        assert len(source_items) == 1
        source = source_items[0]
        assert source.published_at == item["date"]
        assert source.metadata["matched_comment_date"] == "2026-11-15"
        candidate = schema.Candidate(
            candidate_id="c1", item_id=source.item_id, source="reddit", title=source.title, url=source.url,
            snippet="", subquery_labels=["primary"], native_ranks={"primary:reddit": 1}, local_relevance=0.5,
            freshness=50, engagement=None, source_quality=0.7, rrf_score=0.01, sources=["reddit"],
            source_items=[source], metadata={"range_from": FROM, "range_to": TO},
        )
        assert not schema.candidate_out_of_window(candidate)
        assert "comment 2026-11-15" in (render._format_engagement(source) or "")

    def test_lane_merges_session_threads_and_skips_the_web_floor_when_enough(self, fake):
        with mock.patch.object(reddit_keyless.reddit_discover, "web_refs") as web, \
                mock.patch.object(reddit_keyless.reddit_search, "search") as keyless_search:
            posts, paths, _notes = _lane(_session(sc_floor=1))
        assert posts and paths["session"] == reddit_health.OK
        web.assert_not_called()
        keyless_search.assert_not_called()
        assert all("session" in p["provenance"] for p in posts)
        assert any(p.get("engagement_source") == reddit_refs.LIVE for p in posts)
        assert "session" in reddit_health.served_detail(posts, paths).split(" served")[0]


def test_reddit_search_url_carries_the_result_kind_and_sort():
    assert "type=comments" in reddit_search.search_url("a", kind="comments", sort="new")
    assert "sort=new" in reddit_search.search_url("a", kind="comments", sort="new")
    communities = reddit_search.search_url("a", kind="communities")
    assert "type=communities" in communities and "t=" not in communities
    assert reddit_search.search_url("a") == reddit_search.search_url("a", kind="posts")


def test_limiter_sleep_is_injectable():
    slept = []
    limiter = http.RateLimiter(1000.0, burst=1, sleep=slept.append)
    limiter.acquire()
    limiter.acquire()
    assert slept and all(s > 0 for s in slept)
