"""The Reddit hydration chain: refs in, Reddit items out, through Arctic Shift
batched post lookups and comment trees, with fresh archive scores labeled
pending and a search-result fallback when nothing hydrates a ref."""

from urllib.parse import parse_qs, urlsplit

import pytest

from lib import http, normalize, reddit_arctic, reddit_hydrate, reddit_refs, render, schema

NOW = 1_800_000_000  # fixed clock for age math
HOUR = 3600
DAY = 24 * HOUR


class CountingLimiter:
    def __init__(self):
        self.calls = 0

    def acquire(self):
        self.calls += 1


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No real sleeps, a counting limiter, and a clean archive memo."""
    sleeps = []
    monkeypatch.setattr(reddit_arctic.time, "sleep", lambda s: sleeps.append(s))
    limiter = CountingLimiter()
    monkeypatch.setattr(http, "REDDIT_KEYLESS_LIMITER", limiter)
    reddit_arctic.reset_memo()
    yield {"sleeps": sleeps, "limiter": limiter}
    reddit_arctic.reset_memo()


def _post(pid, *, title="ghostty terminal release", score=50, num_comments=10,
          age=5 * DAY, sub="ghostty"):
    return {
        "id": pid, "title": title, "score": score, "num_comments": num_comments,
        "created_utc": NOW - age, "subreddit": sub,
        "permalink": f"/r/{sub}/comments/{pid}/slug/", "author": "op", "selftext": "body",
    }


def _comment(cid, pid, *, score=5, age=3 * DAY, depth=0, body="ghostty is fast now", replies=None):
    return {"kind": "t1", "data": {
        "id": cid, "score": score, "created_utc": NOW - age, "depth": depth,
        "author": f"user_{cid}", "body": body, "link_id": f"t3_{pid}",
        "permalink": f"/r/ghostty/comments/{pid}/slug/{cid}/",
        "replies": {"kind": "Listing", "data": {"children": replies or []}} if replies else "",
    }}


class FakeArctic:
    """Routes the archive's two endpoints to canned answers and records calls."""

    def __init__(self, posts=None, trees=None, ids_answers=None):
        self.posts = {p["id"]: p for p in (posts or [])}
        self.trees = trees or {}
        self.ids_answers = list(ids_answers or [])
        self.calls = []

    def __call__(self, url, headers=None, **_kwargs):
        self.calls.append(url)
        query = parse_qs(urlsplit(url).query)
        if "/posts/ids" in url:
            if self.ids_answers:
                answer = self.ids_answers.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
            ids = query["ids"][0].split(",")
            return {"data": [self.posts[i] for i in ids if i in self.posts]}
        if "/comments/tree" in url:
            pid = query["link_id"][0].removeprefix("t3_")
            return {"data": self.trees.get(pid, [])}
        raise AssertionError(f"unexpected URL {url}")

    def ids_calls(self):
        return [u for u in self.calls if "/posts/ids" in u]


def _ref(pid, **kwargs):
    ref = reddit_refs.canonical_thread(f"https://www.reddit.com/r/ghostty/comments/{pid}/slug/")
    ref.provenance.add("web")
    for key, value in kwargs.items():
        setattr(ref, key, value)
    return ref


def _hydrate(fake, refs, monkeypatch, **kwargs):
    monkeypatch.setattr(reddit_arctic.http, "get", fake)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("topic", "ghostty")
    return reddit_hydrate.hydrate(refs, **kwargs)


def _http_error(status):
    return http.HTTPError(f"HTTP {status}", status_code=status)


class TestBatching:
    def test_250_refs_make_three_serial_batches_of_at_most_100(self, monkeypatch, _quiet):
        pids = [f"a{i:04d}" for i in range(250)]
        fake = FakeArctic(posts=[_post(p) for p in pids])
        result = _hydrate(fake, [_ref(p) for p in pids], monkeypatch, comments=False)
        batches = [parse_qs(urlsplit(u).query)["ids"][0].split(",") for u in fake.ids_calls()]
        assert [len(b) for b in batches] == [100, 100, 50]
        assert sorted(sum(batches, [])) == sorted(pids)
        assert _quiet["limiter"].calls >= 3
        assert len(result.items) == 250


class TestPendingScores:
    def test_fresh_low_score_is_pending_and_older_scores_stay(self, monkeypatch):
        fake = FakeArctic(posts=[
            _post("fresh1", score=1, age=10 * HOUR),
            _post("old840", score=840, age=5 * DAY),
            _post("old1", score=1, age=4 * DAY),
        ])
        result = _hydrate(fake, [_ref("fresh1"), _ref("old840"), _ref("old1")], monkeypatch,
                          comments=False)
        by_id = {i["metadata"]["post_id"]: i for i in result.items}
        assert by_id["fresh1"]["score"] is None
        assert by_id["fresh1"]["engagement"]["score"] is None
        assert by_id["fresh1"]["engagement_source"] == reddit_refs.ARCTIC_PENDING
        assert by_id["old840"]["engagement"]["score"] == 840
        assert by_id["old840"]["engagement_source"] == reddit_refs.ARCTIC
        assert by_id["old1"]["engagement"]["score"] == 1
        assert by_id["old1"]["engagement_source"] == reddit_refs.ARCTIC

    def test_pending_score_renders_as_score_pending(self, monkeypatch):
        fake = FakeArctic(posts=[_post("fresh1", score=1, num_comments=4, age=10 * HOUR)])
        result = _hydrate(fake, [_ref("fresh1")], monkeypatch, comments=False)
        [item] = normalize.normalize_source_items(
            "reddit", result.items, "2027-01-01", "2027-02-01",
        )
        assert item.metadata["engagement_source"] == reddit_refs.ARCTIC_PENDING
        shown = render._format_engagement(item)
        assert "score pending" in shown
        assert "pts" not in shown
        assert "score pending" in render._format_item_engagement(item)


class TestComments:
    def test_every_stored_comment_carries_depth(self, monkeypatch):
        reply = _comment("c2", "p1", depth=1, score=3)
        tree = [_comment("c1", "p1", score=12, replies=[reply]), _comment("c3", "p1", score=7)]
        fake = FakeArctic(posts=[_post("p1")], trees={"p1": tree})
        result = _hydrate(fake, [_ref("p1")], monkeypatch)
        comments = result.items[0]["top_comments"]
        assert {c["id"] for c in comments} == {"c1", "c2", "c3"}
        assert all(isinstance(c["depth"], int) for c in comments)
        assert {c["id"]: c["depth"] for c in comments} == {"c1": 0, "c2": 1, "c3": 0}
        assert [c["id"] for c in comments] == ["c1", "c3", "c2"]  # by score
        assert result.items[0]["comments_source"] == reddit_refs.ARCTIC

    def test_original_posters_comments_are_marked_uploader(self, monkeypatch):
        own = _comment("c1", "p1", score=12)
        own["data"]["author"] = "op"
        tree = [own, _comment("c2", "p1", score=7)]
        fake = FakeArctic(posts=[_post("p1")], trees={"p1": tree})
        result = _hydrate(fake, [_ref("p1")], monkeypatch)
        marks = {c["id"]: c["is_uploader"] for c in result.items[0]["top_comments"]}
        assert marks == {"c1": True, "c2": False}
        [item] = normalize.normalize_source_items(
            "reddit", result.items, "2027-01-01", "2027-02-01",
        )
        quotable = [c for c in item.metadata["top_comments"]
                    if render._is_community_comment(item, c, c.get("excerpt", ""))]
        assert [c["id"] for c in quotable] == ["c2"]

    def test_fresh_arctic_comments_have_no_vote_count(self, monkeypatch):
        tree = [_comment("c1", "p1", score=1, age=5 * HOUR), _comment("c2", "p1", score=2, age=8 * HOUR)]
        fake = FakeArctic(posts=[_post("p1")], trees={"p1": tree})
        result = _hydrate(fake, [_ref("p1")], monkeypatch)
        comments = result.items[0]["top_comments"]
        assert len(comments) == 2
        assert all(c["score"] is None and c["score_pending"] for c in comments)

    def test_comments_only_for_top_threads_within_budget(self, monkeypatch):
        pids = [f"t{i}" for i in range(12)]
        fake = FakeArctic(
            posts=[_post(p, num_comments=100 - i) for i, p in enumerate(pids)],
            trees={p: [_comment(f"c{p}", p)] for p in pids},
        )
        _hydrate(fake, [_ref(p) for p in pids], monkeypatch, depth="quick")
        tree_calls = [u for u in fake.calls if "/comments/tree" in u]
        assert len(tree_calls) == 4  # the quick-depth comment budget
        assert all("link_id=t3_t" in u for u in tree_calls)
        assert "link_id=t3_t0" in tree_calls[0]  # most-discussed first


class TestArcticRetries:
    def test_422_then_200_retries_once(self, monkeypatch, _quiet):
        fake = FakeArctic(ids_answers=[_http_error(422), {"data": [_post("p1")]}])
        result = _hydrate(fake, [_ref("p1")], monkeypatch, comments=False)
        assert len(fake.ids_calls()) == 2
        assert _quiet["sleeps"]  # paused before the retry, through the patchable clock
        assert result.items[0]["engagement"]["score"] == 50
        assert result.arctic_state == "ok"

    def test_two_422s_return_nothing_and_record_throttled(self, monkeypatch):
        fake = FakeArctic(ids_answers=[_http_error(422), _http_error(422)])
        monkeypatch.setattr(reddit_arctic.http, "get", fake)
        posts, state = reddit_arctic.fetch_posts_by_ids(["p1"])
        assert posts == {}
        assert state == "throttled"
        assert len(fake.ids_calls()) == 2


class TestLastRung:
    def test_arctic_down_keeps_search_result_data(self, monkeypatch):
        fake = FakeArctic(ids_answers=[_http_error(503)] * 10)
        refs = [
            _ref("p1", title="Ghostty 2.0 is out", date="2026-11-14"),
            _ref("p2", title="Ghostty vs kitty", date="2026-11-10"),
        ]
        result = _hydrate(fake, refs, monkeypatch, from_date="2026-10-16", to_date="2026-11-15")
        assert [i["title"] for i in result.items] == ["Ghostty 2.0 is out", "Ghostty vs kitty"]
        for item in result.items:
            assert item["engagement"]["score"] is None
            assert item["engagement_source"] == reddit_refs.UNKNOWN
            assert item["date"] in ("2026-11-14", "2026-11-10")
            assert item["url"].startswith("https://www.reddit.com/r/ghostty/comments/")
        assert result.arctic_state == "down"
        assert "arctic down" in result.detail
        assert "web served 2" in result.detail
        assert not [u for u in fake.calls if "/comments/tree" in u]


class TestWindow:
    def test_window_judged_on_post_created_utc(self, monkeypatch):
        fake = FakeArctic(posts=[_post("in1", age=2 * DAY), _post("old1", age=90 * DAY)])
        today = "2027-01-15"  # NOW is 2027-01-15 UTC
        result = _hydrate(fake, [_ref("in1"), _ref("old1")], monkeypatch, comments=False,
                          from_date="2026-12-16", to_date=today)
        assert [i["metadata"]["post_id"] for i in result.items] == ["in1"]


class TestScrapeCreatorsRung:
    def _sc_item(self, pid):
        return {
            "url": f"https://www.reddit.com/r/ghostty/comments/{pid}/x/",
            "title": f"ghostty sc {pid}", "date": "2027-01-14",
            "engagement": {"score": 77, "num_comments": 9},
            "top_comments": [{"score": 20, "excerpt": "ghostty from sc", "url": ""}],
        }

    def test_thin_free_layers_call_sc(self, monkeypatch):
        fake = FakeArctic(posts=[_post("p1"), _post("p2"), _post("p3", title="unrelated cats")])
        calls = []

        def sc():
            calls.append(1)
            return [self._sc_item("s1")]

        result = _hydrate(fake, [_ref("p1"), _ref("p2"), _ref("p3")], monkeypatch,
                          comments=False, sc_fetch=sc, sc_min_items=5)
        assert calls == [1]
        sc_item = next(i for i in result.items if i["metadata"]["post_id"] == "s1")
        assert sc_item["engagement_source"] == reddit_refs.LIVE
        assert all(isinstance(c["depth"], int) for c in sc_item["top_comments"])
        assert "sc" in result.detail

    def test_pending_on_topic_threads_count_toward_the_floor(self, monkeypatch):
        posts = [_post(f"f{i}", score=1, age=6 * HOUR) for i in range(4)] + [_post("s1", score=90)]
        fake = FakeArctic(posts=posts)
        calls = []
        result = _hydrate(fake, [_ref(p["id"]) for p in posts], monkeypatch, comments=False,
                          sc_fetch=lambda: calls.append(1) or [], sc_min_items=5)
        assert calls == []
        assert reddit_hydrate.on_topic_count(result.items, "ghostty") == 5


class TestSessionHook:
    def test_session_hydrated_refs_skip_arctic(self, monkeypatch):
        fake = FakeArctic(posts=[_post("p2")])

        def session(refs):
            return {"p1": reddit_arctic._normalize_listing_row(_post("p1", score=300))}

        result = _hydrate(fake, [_ref("p1"), _ref("p2")], monkeypatch, comments=False,
                          session_hydrator=session)
        ids_requested = parse_qs(urlsplit(fake.ids_calls()[0]).query)["ids"][0]
        assert ids_requested == "p2"
        by_id = {i["metadata"]["post_id"]: i for i in result.items}
        assert by_id["p1"]["engagement_source"] == reddit_refs.LIVE


def _reddit_item(url, *, score, source_label, comments, item_id="R1"):
    return schema.SourceItem(
        item_id=item_id, source="reddit", title="ghostty 2.0", body="ghostty 2.0",
        url=url, engagement={"score": score, "num_comments": 12},
        metadata={"top_comments": comments, "engagement_source": source_label,
                  "local_relevance": 0.8, "freshness": 80, "source_quality": 0.7},
    )


class TestMergeKeepsMatchedComment:
    def test_listing_copy_gains_the_session_copys_matched_comment(self):
        listing = {"url": "u", "engagement": {"score": 5}, "engagement_source": reddit_refs.LIVE}
        session_copy = {"url": "u", "engagement": {"score": 5},
                        "matched_comment": {"date": "2026-11-10", "url": "c"}}
        reddit_hydrate.merge_post(listing, session_copy)
        assert listing["matched_comment"] == {"date": "2026-11-10", "url": "c"}


class TestMergeAcrossForms:
    def test_three_forms_of_one_thread_merge_with_live_score_and_all_comments(self):
        from lib import fusion

        plan = schema.QueryPlan(
            intent="breaking_news", freshness_mode="strict_recent", cluster_mode="story",
            raw_topic="ghostty",
            subqueries=[
                schema.SubQuery(label=label, search_query="ghostty", ranking_query="ghostty",
                                sources=["reddit"], weight=1.0)
                for label in ("a", "b", "c")
            ],
            source_weights={"reddit": 1.0},
        )
        session = _reddit_item(
            "https://www.reddit.com/r/ghostty/comments/1abc2de/ghostty_20/", score=412,
            source_label=reddit_refs.LIVE,
            comments=[{"id": "c1", "score": 50, "excerpt": "session comment", "depth": 0}],
        )
        brave = _reddit_item(
            "https://old.reddit.com/r/ghostty/comments/1abc2de/ghostty_20/k9z8y7x/", score=None,
            source_label=reddit_refs.ARCTIC_PENDING,
            comments=[{"id": "c1", "score": None, "score_pending": True, "excerpt": "session comment", "depth": 0},
                      {"id": "c2", "score": None, "score_pending": True, "excerpt": "arctic comment", "depth": 1}],
        )
        host = _reddit_item(
            "https://redd.it/1abc2de", score=900, source_label=reddit_refs.ARCTIC,
            comments=[{"id": "c3", "score": 8, "excerpt": "host comment", "depth": 0}],
        )
        streams = {("a", "reddit"): [brave], ("b", "reddit"): [session], ("c", "reddit"): [host]}
        candidates = fusion.weighted_rrf(streams, plan, pool_limit=10)
        assert len(candidates) == 1
        [item] = candidates[0].source_items
        assert item.engagement["score"] == 412
        assert item.metadata["engagement_source"] == reddit_refs.LIVE
        ids = [c["id"] for c in item.metadata["top_comments"]]
        assert sorted(ids) == ["c1", "c2", "c3"]
        assert ids[-1] == "c2"  # pending after scored


class TestRankingSkipsPendingScores:
    def _candidate(self, item, engagement):
        return schema.Candidate(
            candidate_id=item.url, item_id=item.item_id, source="reddit", title=item.title,
            url=item.url, snippet="", subquery_labels=["a"], native_ranks={"a:reddit": 1},
            local_relevance=0.8, freshness=80, engagement=engagement, source_quality=0.7,
            rrf_score=0.02, sources=["reddit"], source_items=[item], rerank_score=60.0,
        )

    def test_pending_engagement_is_unknown_not_zero(self):
        from lib import rerank

        pending = _reddit_item("https://www.reddit.com/r/g/comments/aaa/", score=None,
                               source_label=reddit_refs.ARCTIC_PENDING, comments=[])
        zero = _reddit_item("https://www.reddit.com/r/g/comments/bbb/", score=0,
                            source_label=reddit_refs.ARCTIC, comments=[])
        assert rerank._final_score(self._candidate(pending, None)) > rerank._final_score(
            self._candidate(zero, None)
        )

    def test_engagement_reservation_skips_pending_scores(self):
        from lib import fusion

        pending = _reddit_item("https://www.reddit.com/r/g/comments/aaa/", score=None,
                               source_label=reddit_refs.ARCTIC_PENDING, comments=[])
        pending.engagement["num_comments"] = 500
        pending.local_relevance = 0.8
        scored = _reddit_item("https://www.reddit.com/r/g/comments/bbb/", score=40,
                              source_label=reddit_refs.LIVE, comments=[])
        scored.local_relevance = 0.8
        fused = [self._candidate(pending, None), self._candidate(scored, None)]
        reserved = fusion._reddit_engagement_reservation(fused, 2, "ghostty")
        assert [c.url for c in reserved] == [scored.url]


class TestPendingCommentsInQuotes:
    def _report(self, candidates):
        return schema.Report(
            topic="ghostty", range_from="2027-01-01", range_to="2027-01-15",
            generated_at="2027-01-15T00:00:00Z", provider_runtime=schema.ProviderRuntime(
                reasoning_provider="mock", planner_model="mock", rerank_model="mock"),
            query_plan=schema.QueryPlan(intent="concept", freshness_mode="balanced_recent",
                                        cluster_mode="none", raw_topic="ghostty",
                                        subqueries=[], source_weights={}),
            clusters=[], ranked_candidates=candidates, items_by_source={}, errors_by_source={},
        )

    def _cand(self, item):
        return schema.Candidate(
            candidate_id=item.url, item_id=item.item_id, source=item.source, title=item.title,
            url=item.url, snippet="", subquery_labels=["a"], native_ranks={"a": 1},
            local_relevance=0.9, freshness=80, engagement=10, source_quality=0.7,
            rrf_score=0.02, sources=[item.source], source_items=[item], final_score=50.0,
        )

    def test_pending_quotes_have_no_count_and_follow_scored_ones(self):
        reddit = _reddit_item(
            "https://www.reddit.com/r/ghostty/comments/aaa/", score=None,
            source_label=reddit_refs.ARCTIC_PENDING,
            comments=[{"id": "c1", "score": None, "score_pending": True, "depth": 0,
                       "author": "fresh_user", "excerpt": "ghostty pending comment text"}],
        )
        youtube = schema.SourceItem(
            item_id="YT1", source="youtube", title="ghostty video", body="ghostty video",
            url="https://www.youtube.com/watch?v=abc", author="channel",
            engagement={"views": 1000},
            metadata={"top_comments": [{"score": 30, "author": "yt_user",
                                        "excerpt": "ghostty scored youtube comment"}]},
        )
        lines = render._render_top_comments(
            self._report([self._cand(reddit), self._cand(youtube)]), limit=10,
        )
        pending = next(line for line in lines if "pending comment" in line)
        scored = next(line for line in lines if "scored youtube" in line)
        assert "upvotes" not in pending
        assert lines.index(scored) < lines.index(pending)

    def test_cluster_comment_lines_show_pending_without_votes(self):
        reddit = _reddit_item(
            "https://www.reddit.com/r/ghostty/comments/aaa/", score=None,
            source_label=reddit_refs.ARCTIC_PENDING,
            comments=[{"id": "c1", "score": None, "score_pending": True, "depth": 0,
                       "author": "fresh_user", "excerpt": "ghostty pending comment text"}],
        )
        shown = render._top_comments_list(reddit)
        assert [c["id"] for c in shown] == ["c1"]


class TestArcticRunHealth:
    def test_arctic_throttled_earlier_this_run_is_not_asked_again(self, monkeypatch, _quiet):
        from lib import reddit_health

        reddit_health.record("arctic", reddit_health.THROTTLED, "posts/ids 429")
        fake = FakeArctic(posts=[_post("a0001")])
        result = _hydrate(fake, [_ref("a0001")], monkeypatch, comments=False)
        assert fake.ids_calls() == []
        assert result.arctic_state == "throttled"
        assert len(result.items) == 1

    def test_skipped_archive_fetches_no_comment_trees(self, monkeypatch, _quiet):
        from lib import reddit_health

        reddit_health.record("arctic", reddit_health.THROTTLED, "posts/ids 429")
        fake = FakeArctic(posts=[_post("a0002")])
        _hydrate(fake, [_ref("a0002")], monkeypatch, comments=True)
        assert fake.calls == []
