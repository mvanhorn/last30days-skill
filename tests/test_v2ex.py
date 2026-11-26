"""V2EX source: SOV2EX search, relevance gating, reply enrichment."""

import datetime
import urllib.parse

from lib import normalize, pipeline, v2ex


def _hit(topic_id, title, *, content="", created="2026-11-20T08:00:00", member=None, replies=0, node=12):
    return {
        "_id": str(topic_id),
        "_source": {
            "id": topic_id,
            "title": title,
            "content": content or f"About {title}",
            "created": created,
            "member": member or f"user{topic_id}",
            "node": node,
            "replies": replies,
        },
    }


def _reply(username, content, created=1795667778):
    return {"member": {"username": username}, "content": content, "created": created}


class FakeApi:
    def __init__(self, hits=None, replies=None, fail=(), search_body=None):
        self.hits = hits or []
        self.replies = replies or {}
        self.fail = set(fail)
        self.search_body = search_body
        self.calls = []

    def __call__(self, method, url, headers=None, **kwargs):
        self.calls.append(url)
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.netloc == "www.sov2ex.com" and parsed.path == "/api/search":
            if "search" in self.fail:
                raise OSError("search down")
            if self.search_body is not None:
                return self.search_body
            return {"took": 1, "total": len(self.hits), "hits": self.hits}
        if parsed.netloc == "www.v2ex.com" and parsed.path == "/api/replies/show.json":
            if "replies" in self.fail:
                raise OSError("replies down")
            return self.replies.get(query["topic_id"], [])
        raise AssertionError(f"unexpected URL {url}")


def _install(monkeypatch, api):
    monkeypatch.setattr(v2ex.http, "request", api)
    return api


def _search_params(api):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(api.calls[0]).query))


def _source_items(hits):
    parsed = v2ex.parse_v2ex_response({"topics": [h["_source"] for h in hits]}, query="claude code")
    return normalize.normalize_source_items("v2ex", parsed, "2026-10-27", "2026-11-26")


def _diagnose_sources(argv, config=None):
    import json

    from tests.test_diagnose_compat import FAKE_KEYLESS_CONFIG, _run_cli

    rc, out = _run_cli(["--diagnose", *argv], {**FAKE_KEYLESS_CONFIG, **(config or {})})
    assert rc == 0
    return json.loads(out)["available_sources"]


def test_v2ex_is_off_by_default():
    assert "v2ex" not in pipeline.available_sources({}, None, x_pending=False)


def test_include_sources_and_search_each_activate_it():
    assert "v2ex" in pipeline.available_sources({"INCLUDE_SOURCES": "v2ex"}, None, x_pending=False)
    assert "v2ex" in pipeline.available_sources({}, ["v2ex"], x_pending=False)


def test_cli_activation_and_exclusion_through_diagnose():
    assert "v2ex" not in _diagnose_sources([])
    assert "v2ex" in _diagnose_sources(["--search", "reddit,v2ex"])
    assert "v2ex" in _diagnose_sources([], {"INCLUDE_SOURCES": "v2ex"})
    assert "v2ex" not in _diagnose_sources([], {"INCLUDE_SOURCES": "v2ex", "EXCLUDE_SOURCES": "v2ex"})


def test_search_sends_window_bounds_and_requires_every_term(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    v2ex.search_v2ex("claude code", "2026-10-27", "2026-11-26", depth="deep", search_query='"claude code"')
    params = _search_params(api)
    assert params["q"] == "claude code"
    assert params["size"] == "40"
    assert params["sort"] == "sumup"
    assert params["operator"] == "and"
    utc = datetime.timezone.utc
    assert int(params["gte"]) == int(datetime.datetime(2026, 10, 27, tzinfo=utc).timestamp())
    assert int(params["lte"]) == int(datetime.datetime(2026, 11, 26, 23, 59, 59, tzinfo=utc).timestamp())


def test_search_size_stays_under_the_sov2ex_cap():
    assert max(v2ex.SEARCH_SIZE.values()) <= 50


def test_offtopic_and_duplicate_hits_are_dropped(monkeypatch):
    _install(monkeypatch, FakeApi(hits=[
        _hit(1, "Claude Code 和 Codex 怎么选", content="claude code 用了一个月"),
        _hit(1, "Claude Code 和 Codex 怎么选", content="claude code 用了一个月"),
        _hit(2, "招聘后端工程师", content="五险一金，弹性工作"),
    ]))
    result = v2ex.search_v2ex("claude code", "2026-10-27", "2026-11-26")
    assert [t["id"] for t in result["topics"]] == [1]
    assert "error" not in result


def test_chinese_topics_pass_relevance(monkeypatch):
    _install(monkeypatch, FakeApi(hits=[_hit(1, "国产大模型评测对比", content="最近测了几个大模型")]))
    result = v2ex.search_v2ex("大模型评测", "2026-10-27", "2026-11-26")
    assert [t["id"] for t in result["topics"]] == [1]


def test_failed_search_reports_an_error(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"search"}))
    result = v2ex.search_v2ex("claude code", "2026-10-27", "2026-11-26")
    assert result["topics"] == []
    assert "search down" in result["error"]


def test_rejected_query_is_an_error_not_an_empty_result(monkeypatch):
    _install(monkeypatch, FakeApi(search_body={"message": "too large size"}))
    result = v2ex.search_v2ex("claude code", "2026-10-27", "2026-11-26")
    assert result["topics"] == []
    assert "too large size" in result["error"]


def test_empty_query_makes_no_request(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    assert v2ex.search_v2ex("", "2026-10-27", "2026-11-26") == {"topics": []}
    assert api.calls == []


def test_parse_maps_topic_fields():
    long_content = "claude code " * 100
    [item] = v2ex.parse_v2ex_response(
        {"topics": [_hit(1259973, "Claude Code &amp; Codex", content=long_content, replies=12, member="alice")["_source"]]},
        query="claude code",
    )
    assert item["id"] == "1259973"
    assert item["url"] == "https://www.v2ex.com/t/1259973"
    assert item["title"] == "Claude Code & Codex"
    assert item["author"] == "alice"
    assert item["date"] == "2026-11-20"
    assert item["engagement"] == {"replies": 12}
    assert item["text"].endswith("...") and len(item["text"]) == v2ex.TEXT_LIMIT + 3
    assert item["metadata"] == {"node_id": 12}
    assert 0 < item["relevance"] <= 1


def test_normalized_item_uses_v2ex_container():
    [item] = _source_items([_hit(1, "Claude Code 体验", content="claude code 很好用")])
    assert item.source == "v2ex"
    assert item.container == "V2EX"
    assert item.published_at == "2026-11-20"
    assert item.date_confidence == "high"
    assert item.snippet == "claude code 很好用"


def test_replies_put_topic_answers_first_and_skip_the_author(monkeypatch):
    _install(monkeypatch, FakeApi(replies={"1": [
        _reply("op", "补充一下背景"),
        _reply("bob", "@carol 同意"),
        _reply("carol", "命令行就够了"),
        _reply("dave", "@bob @carol 半天就适应了"),
        _reply("erin", "   "),
    ]}))
    count, comments = v2ex._fetch_replies("1", "op")
    assert count == 5
    assert [(c["author"], c["text"]) for c in comments] == [
        ("carol", "命令行就够了"),
        ("bob", "同意"),
        ("dave", "半天就适应了"),
    ]
    assert comments[0]["date"] == "2026-11-26"


def test_enrichment_targets_top_items_and_refreshes_reply_counts(monkeypatch):
    api = _install(monkeypatch, FakeApi(replies={
        "1": [_reply("bob", "claude code 好用"), _reply("carol", "确实")],
        "2": [],
        "3": [_reply("dave", "不错")],
    }))
    items = _source_items([
        _hit(1, "Claude Code one", replies=0),
        _hit(2, "Claude Code two", replies=0),
        _hit(3, "Claude Code three", replies=0),
        _hit(4, "Claude Code four", replies=9),
    ])
    v2ex.enrich_source_items(items)
    fetched = sorted(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query))["topic_id"] for u in api.calls)
    assert fetched == ["1", "2", "3"]
    assert items[0].engagement["replies"] == 2
    assert [c["excerpt"] for c in items[0].metadata["top_comments"]] == ["claude code 好用", "确实"]
    # Replies carry no votes: the score stays absent, not a measured zero.
    assert items[0].metadata["top_comments"][0]["score"] is None
    assert not items[1].metadata.get("top_comments")
    assert items[3].engagement["replies"] == 9


def test_refreshed_reply_count_reaches_the_engagement_score(monkeypatch):
    from lib import signals

    _install(monkeypatch, FakeApi(replies={"1": [_reply(f"user{n}", f"回复 {n}") for n in range(60)]}))
    [item] = _source_items([_hit(1, "Claude Code one", replies=0)])
    signals.annotate_stream([item], "claude code", "balanced_recent", reference_date="2026-11-26")
    stale_score = item.engagement_score
    v2ex.enrich_source_items([item])
    assert item.engagement["replies"] == 60
    assert item.engagement_score == signals.engagement_score(item)
    # Zero replies measure no engagement at all (None), not a zero score.
    assert stale_score is None
    assert item.engagement_score > 0


def test_failed_reply_fetch_keeps_the_item(monkeypatch):
    _install(monkeypatch, FakeApi(fail={"replies"}))
    items = _source_items([_hit(1, "Claude Code one", replies=4)])
    v2ex.enrich_source_items(items)
    assert items[0].engagement["replies"] == 4
    assert not items[0].metadata.get("top_comments")


def test_enrichment_skips_when_the_deadline_has_passed(monkeypatch):
    api = _install(monkeypatch, FakeApi())
    items = _source_items([_hit(1, "Claude Code one")])
    v2ex.enrich_source_items(items, deadline=0.0)
    assert api.calls == []


def test_enrichment_stops_waiting_at_its_time_limit(monkeypatch):
    import threading
    import time

    release = threading.Event()

    def stuck(topic_id, op):
        release.wait(5)
        return 1, [{"author": "a", "text": "late"}]

    monkeypatch.setattr(v2ex, "_fetch_replies", stuck)
    monkeypatch.setattr(v2ex, "ENRICH_WAIT_SECONDS", 0.2)
    items = _source_items([_hit(1, "Claude Code one")])
    started = time.monotonic()
    v2ex.enrich_source_items(items)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 2
    assert not items[0].metadata.get("top_comments")


def test_requests_carry_a_total_deadline(monkeypatch):
    seen = {}

    def capture(method, url, headers=None, **kwargs):
        seen.update(kwargs)
        return {"hits": []}

    monkeypatch.setattr(v2ex.http, "request", capture)
    v2ex.search_v2ex("claude code", "2026-10-27", "2026-11-26")
    assert seen["owned_get"] is True
    assert seen["deadline_monotonic"] is not None


def test_doctor_reports_v2ex_as_opt_in_until_enabled():
    from lib import doctor

    assert doctor._v2ex_record({})["status"] == "opt-in"
    assert doctor._v2ex_record({"INCLUDE_SOURCES": "v2ex"})["status"] == "ok"
    assert "v2ex" not in doctor._probeable_sources()


def _plan(sources):
    from lib import schema

    return schema.QueryPlan(
        intent="concept",
        freshness_mode="balanced_recent",
        cluster_mode="none",
        raw_topic="claude code",
        subqueries=[schema.SubQuery(label="primary", search_query="claude code",
                                    ranking_query="q", sources=list(sources), weight=1.0)],
        source_weights={source: 1.0 for source in sources},
    )


def test_activated_v2ex_joins_a_plan_that_omits_it():
    plan = _plan(["hackernews"])
    pipeline._ensure_opt_in_source_in_plan(plan, ["hackernews", "v2ex"], "v2ex")
    assert "v2ex" in plan.subqueries[0].sources
    assert "v2ex" in plan.source_weights


def test_inactive_v2ex_is_not_added_to_a_plan():
    plan = _plan(["hackernews"])
    pipeline._ensure_opt_in_source_in_plan(plan, ["hackernews"], "v2ex")
    assert plan.subqueries[0].sources == ["hackernews"]


def test_retrieve_stream_runs_search_and_parse(monkeypatch):
    from lib import schema

    _install(monkeypatch, FakeApi(hits=[_hit(1, "Claude Code 体验", content="claude code 很好用")]))
    subquery = schema.SubQuery(label="primary", search_query="claude code",
                               ranking_query="q", sources=["v2ex"], weight=1.0)
    items, _artifact = pipeline._retrieve_stream_impl(
        topic="claude code", subquery=subquery, source="v2ex", config={},
        depth="default", date_range=("2026-10-27", "2026-11-26"),
        runtime=None, mock=False,
    )
    assert [item["url"] for item in items] == ["https://www.v2ex.com/t/1"]
